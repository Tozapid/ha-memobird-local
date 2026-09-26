"""Printing logic shared by the notify entity, actions and notify service."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import LINE_DASH, LINE_THIN, Document, MemobirdError, prepare_image, to_bitmap
from .const import (
    ATTR_BIG,
    ATTR_BOLD,
    ATTR_CAMERA,
    ATTR_CAPTION,
    ATTR_DITHER,
    ATTR_FILE,
    ATTR_FONT_SIZE,
    ATTR_FORMAT,
    ATTR_MESSAGE,
    ATTR_SEPARATOR,
    ATTR_SVG,
    ATTR_TIMESTAMP,
    ATTR_TITLE,
    ATTR_UNDERLINE,
    ATTR_URL,
    CONF_DEFAULT_FORMAT,
    CONF_FONT_SIZE,
)
from .render import (
    DEFAULT_FONT_SIZE,
    FORMAT_PLAIN,
    FORMATS,
    MAX_FONT_SIZE,
    MIN_FONT_SIZE,
    render,
)
from .svg import SvgError, render_svg

if TYPE_CHECKING:
    from . import MemobirdConfigEntry

# Characters per line at the normal font size.
LINE_WIDTH = 32
# Refuse to download anything bigger than this.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_SOURCES = "image_source"

FONT_SIZE_SCHEMA = vol.All(vol.Coerce(int), vol.Range(MIN_FONT_SIZE, MAX_FONT_SIZE))

TEXT_OPTIONS = {
    vol.Optional(ATTR_FORMAT): vol.In(FORMATS),
    vol.Optional(ATTR_FONT_SIZE): FONT_SIZE_SCHEMA,
    vol.Optional(ATTR_TIMESTAMP): cv.boolean,
    vol.Optional(ATTR_SEPARATOR): cv.boolean,
}
PLAIN_STYLE = {
    vol.Optional(ATTR_BIG): cv.boolean,
    vol.Optional(ATTR_BOLD): cv.boolean,
    vol.Optional(ATTR_UNDERLINE): cv.boolean,
}
IMAGE_SOURCE = {
    vol.Exclusive(ATTR_FILE, IMAGE_SOURCES): cv.string,
    vol.Exclusive(ATTR_URL, IMAGE_SOURCES): cv.url,
    vol.Exclusive(ATTR_CAMERA, IMAGE_SOURCES): cv.entity_id,
    vol.Exclusive(ATTR_SVG, IMAGE_SOURCES): cv.string,
    vol.Optional(ATTR_DITHER): cv.boolean,
}

PRINT_SCHEMA = {
    vol.Required(ATTR_MESSAGE): cv.string,
    vol.Optional(ATTR_TITLE): cv.string,
    **TEXT_OPTIONS,
    **PLAIN_STYLE,
}
PRINT_IMAGE_SCHEMA = {
    vol.Optional(ATTR_TITLE): cv.string,
    vol.Optional(ATTR_CAPTION): cv.string,
    **IMAGE_SOURCE,
    **TEXT_OPTIONS,
}
# `data:` of the notify.<name> service: text options, plain-text styles and,
# to print an image with the message as its caption, an image source.
NOTIFY_DATA_SCHEMA = vol.Schema({**TEXT_OPTIONS, **PLAIN_STYLE, **IMAGE_SOURCE})


class MemobirdPrinter:
    """Builds print jobs for one configured printer."""

    def __init__(self, hass: HomeAssistant, entry: MemobirdConfigEntry) -> None:
        self.hass = hass
        self._entry = entry

    async def print_text(
        self,
        message: str,
        title: str | None = None,
        format: str | None = None,  # noqa: A002 - service field name
        font_size: int | None = None,
        big: bool = False,
        bold: bool = False,
        underline: bool = False,
        timestamp: bool = True,
        separator: bool = True,
    ) -> None:
        doc = Document()
        self._add_header(doc, title, timestamp, separator)
        fmt = format or self._default_format
        if fmt == FORMAT_PLAIN:
            doc.add_text(message.strip(), big=big, bold=bold, underline=underline)
        else:
            await self._add_formatted(doc, message, fmt, font_size)
        if separator:
            doc.add_line(LINE_DASH)
        await self._send(doc)

    async def print_image(
        self,
        file: str | None = None,
        url: str | None = None,
        camera: str | None = None,
        svg: str | None = None,
        title: str | None = None,
        caption: str | None = None,
        format: str | None = None,  # noqa: A002 - service field name
        font_size: int | None = None,
        dither: bool | None = None,
        timestamp: bool = False,
        separator: bool = True,
    ) -> None:
        if svg:
            # Line art: crisp threshold unless dithering is asked for.
            try:
                img = await self.hass.async_add_executor_job(
                    lambda: to_bitmap(render_svg(svg), dither=bool(dither))
                )
            except SvgError as err:
                raise ServiceValidationError(str(err)) from err
        else:
            if file:
                data = await self._read_file(file)
            elif url:
                data = await self._download(url)
            elif camera:
                data = await self._snapshot(camera)
            else:
                raise ServiceValidationError("Provide one of: file, url, camera or svg")
            try:
                img = await self.hass.async_add_executor_job(
                    lambda: prepare_image(data, dither=dither is not False)
                )
            except MemobirdError as err:
                raise HomeAssistantError(str(err)) from err

        doc = Document()
        self._add_header(doc, title, timestamp, separator)
        doc.add_image(img)
        if caption:
            fmt = format or self._default_format
            if fmt == FORMAT_PLAIN:
                doc.add_text(caption.strip())
            else:
                await self._add_formatted(doc, caption, fmt, font_size)
        if separator:
            doc.add_line(LINE_DASH)
        await self._send(doc)

    @property
    def _default_format(self) -> str:
        return self._entry.options.get(CONF_DEFAULT_FORMAT, FORMAT_PLAIN)

    async def _add_formatted(
        self, doc: Document, text: str, fmt: str, font_size: int | None
    ) -> None:
        size = font_size or self._entry.options.get(CONF_FONT_SIZE, DEFAULT_FONT_SIZE)
        img = await self.hass.async_add_executor_job(render, text.strip(), fmt, size)
        if img is not None:
            doc.add_image(img)

    @staticmethod
    def _add_header(
        doc: Document, title: str | None, timestamp: bool, separator: bool
    ) -> None:
        if title:
            doc.add_text(title.strip(), big=True, bold=True)
        if timestamp:
            now = dt_util.now().strftime("%d.%m.%Y %H:%M")
            doc.add_text(now.center(LINE_WIDTH).rstrip())
        if separator and (title or timestamp):
            doc.add_line(LINE_THIN)

    async def _send(self, doc: Document) -> None:
        try:
            await self._entry.runtime_data.client.print_document(doc)
        except MemobirdError as err:
            raise HomeAssistantError(str(err)) from err

    async def _read_file(self, file: str) -> bytes:
        if not self.hass.config.is_allowed_path(file):
            raise ServiceValidationError(
                f"{file} is not in allowlist_external_dirs "
                "(files under /config/www and /media are allowed by default)"
            )
        path = Path(file)
        try:
            return await self.hass.async_add_executor_job(path.read_bytes)
        except OSError as err:
            raise HomeAssistantError(f"Can't read {file}: {err}") from err

    async def _download(self, url: str) -> bytes:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                resp.raise_for_status()
                if (resp.content_length or 0) > MAX_IMAGE_BYTES:
                    raise HomeAssistantError(f"Image at {url} is too large")
                # read(n) returns whatever has arrived so far, so collect
                # chunks until the body ends, enforcing the size limit.
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > MAX_IMAGE_BYTES:
                        raise HomeAssistantError(f"Image at {url} is too large")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Can't download {url}: {err}") from err
        return bytes(data)

    async def _snapshot(self, camera: str) -> bytes:
        # Imported lazily: camera pulls in heavy dependencies.
        from homeassistant.components.camera import async_get_image

        try:
            image = await async_get_image(self.hass, camera)
        except HomeAssistantError as err:
            raise HomeAssistantError(f"Can't get a snapshot from {camera}: {err}") from err
        return image.content
