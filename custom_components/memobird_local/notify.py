"""Notify entity and print action for the Memobird printer."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MemobirdConfigEntry
from .api import LINE_DASH, LINE_THIN, Document, MemobirdClient, MemobirdError
from .const import (
    ATTR_BIG,
    ATTR_BOLD,
    ATTR_MESSAGE,
    ATTR_SEPARATOR,
    ATTR_TIMESTAMP,
    ATTR_TITLE,
    ATTR_UNDERLINE,
    SERVICE_PRINT,
)
from .entity import MemobirdEntity

# Characters per line at the normal font size.
LINE_WIDTH = 32


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
        if title:
            doc.add_text(title.strip(), big=True, bold=True)
        if timestamp:
            now = dt_util.now().strftime("%d.%m.%Y %H:%M")
            doc.add_text(now.center(LINE_WIDTH).rstrip())
        if separator and (title or timestamp):
            doc.add_line(LINE_THIN)
        doc.add_text(message.strip(), big=big, bold=bold, underline=underline)
        if separator:
            doc.add_line(LINE_DASH)

        try:
            await self._client.print_document(doc)
        except MemobirdError as err:
            raise HomeAssistantError(str(err)) from err
