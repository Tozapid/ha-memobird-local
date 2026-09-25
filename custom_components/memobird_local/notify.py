"""Notify entity and print actions for the Memobird printer."""

from __future__ import annotations

from pathlib import Path

import aiohttp
import voluptuous as vol

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MemobirdConfigEntry
from .api import (
    LINE_DASH,
    LINE_THIN,
    Document,
    MemobirdClient,
    MemobirdError,
    prepare_image,
)
from .const import (
    ATTR_BIG,
    ATTR_BOLD,
    ATTR_CAMERA,
    ATTR_CAPTION,
    ATTR_DITHER,
    ATTR_FILE,
    ATTR_MESSAGE,
    ATTR_SEPARATOR,
    ATTR_TIMESTAMP,
    ATTR_TITLE,
    ATTR_UNDERLINE,
    ATTR_URL,
    SERVICE_PRINT,
    SERVICE_PRINT_IMAGE,
)
from .entity import MemobirdEntity

# Characters per line at the normal font size.
LINE_WIDTH = 32
# Refuse to download anything bigger than this.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_SOURCES = "image_source"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MemobirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MemobirdNotify(entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PRINT,
        {
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_TITLE): cv.string,
            vol.Optional(ATTR_BIG, default=False): cv.boolean,
            vol.Optional(ATTR_BOLD, default=False): cv.boolean,
            vol.Optional(ATTR_UNDERLINE, default=False): cv.boolean,
            vol.Optional(ATTR_TIMESTAMP, default=True): cv.boolean,
            vol.Optional(ATTR_SEPARATOR, default=True): cv.boolean,
        },
        "async_print",
    )
    platform.async_register_entity_service(
        SERVICE_PRINT_IMAGE,
        {
            vol.Exclusive(ATTR_FILE, IMAGE_SOURCES): cv.string,
            vol.Exclusive(ATTR_URL, IMAGE_SOURCES): cv.url,
            vol.Exclusive(ATTR_CAMERA, IMAGE_SOURCES): cv.entity_id,
            vol.Optional(ATTR_TITLE): cv.string,
            vol.Optional(ATTR_CAPTION): cv.string,
            vol.Optional(ATTR_DITHER, default=True): cv.boolean,
            vol.Optional(ATTR_TIMESTAMP, default=False): cv.boolean,
            vol.Optional(ATTR_SEPARATOR, default=True): cv.boolean,
        },
        "async_print_image",
    )


class MemobirdNotify(MemobirdEntity, NotifyEntity):
    _attr_name = None
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, entry: MemobirdConfigEntry) -> None:
        super().__init__(entry, "notify")
        self._client: MemobirdClient = entry.runtime_data.client

    @property
    def available(self) -> bool:
        # Always try to print; a failed job raises a clear error instead.
        return True

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        await self.async_print(message=message, title=title)

    async def async_print(
        self,
        message: str,
        title: str | None = None,
        big: bool = False,
        bold: bool = False,
        underline: bool = False,
        timestamp: bool = True,
        separator: bool = True,
    ) -> None:
        doc = Document()
        self._add_header(doc, title, timestamp, separator)
        doc.add_text(message.strip(), big=big, bold=bold, underline=underline)
        if separator:
            doc.add_line(LINE_DASH)
        await self._send(doc)

    async def async_print_image(
        self,
        file: str | None = None,
        url: str | None = None,
        camera: str | None = None,
        title: str | None = None,
        caption: str | None = None,
        dither: bool = True,
        timestamp: bool = False,
        separator: bool = True,
    ) -> None:
        if file:
            data = await self._read_file(file)
        elif url:
            data = await self._download(url)
        elif camera:
            data = await self._snapshot(camera)
        else:
            raise ServiceValidationError("Provide one of: file, url or camera")

        try:
            img = await self.hass.async_add_executor_job(
                lambda: prepare_image(data, dither=dither)
            )
        except MemobirdError as err:
            raise HomeAssistantError(str(err)) from err

        doc = Document()
        self._add_header(doc, title, timestamp, separator)
        doc.add_image(img)
        if caption:
            doc.add_text(caption.strip())
        if separator:
            doc.add_line(LINE_DASH)
        await self._send(doc)

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
            await self._client.print_document(doc)
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
                data = await resp.content.read(MAX_IMAGE_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Can't download {url}: {err}") from err
        if len(data) > MAX_IMAGE_BYTES:
            raise HomeAssistantError(f"Image at {url} is too large")
        return data

    async def _snapshot(self, camera: str) -> bytes:
        # Imported lazily: camera pulls in heavy dependencies.
        from homeassistant.components.camera import async_get_image

        try:
            image = await async_get_image(self.hass, camera)
        except HomeAssistantError as err:
            raise HomeAssistantError(f"Can't get a snapshot from {camera}: {err}") from err
        return image.content
