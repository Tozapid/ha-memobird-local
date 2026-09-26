"""Notify entity, notify.<name> service and print actions for Memobird."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TITLE,
    BaseNotificationService,
    NotifyEntity,
    NotifyEntityFeature,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DATA_NOTIFY_SERVICES, MemobirdConfigEntry
from .const import (
    ATTR_CAMERA,
    ATTR_FILE,
    ATTR_SVG,
    ATTR_URL,
    DOMAIN,
    SERVICE_PRINT,
    SERVICE_PRINT_IMAGE,
)
from .entity import MemobirdEntity
from .printer import NOTIFY_DATA_SCHEMA, PRINT_IMAGE_SCHEMA, PRINT_SCHEMA

CONF_ENTRY_ID = "entry_id"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MemobirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MemobirdNotify(entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_PRINT, PRINT_SCHEMA, "async_print")
    platform.async_register_entity_service(
        SERVICE_PRINT_IMAGE, PRINT_IMAGE_SCHEMA, "async_print_image"
    )


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> BaseNotificationService | None:
    """Set up the notify.<name> service for a config entry."""
    if discovery_info is None:
        return None
    entry_id = discovery_info[CONF_ENTRY_ID]
    service = MemobirdNotificationService(entry_id)
    hass.data[DATA_NOTIFY_SERVICES][entry_id] = service
    return service


class MemobirdNotify(MemobirdEntity, NotifyEntity):
    """notify.memobird entity, for notify.send_message."""

    _attr_name = None
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, entry: MemobirdConfigEntry) -> None:
        super().__init__(entry, "notify")
        self._printer = entry.runtime_data.printer

    @property
    def available(self) -> bool:
        # Always try to print; a failed job raises a clear error instead.
        return True

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        await self._printer.print_text(message, title=title)

    async def async_print(self, **kwargs: Any) -> None:
        await self._printer.print_text(**kwargs)

    async def async_print_image(self, **kwargs: Any) -> None:
        await self._printer.print_image(**kwargs)


class MemobirdNotificationService(BaseNotificationService):
    """The classic notify.<name> service, usable in notify groups and blueprints.

    Looks the config entry up on every call, so it keeps working after the
    entry is reloaded.
    """

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise HomeAssistantError("The Memobird printer is not set up")
        printer = entry.runtime_data.printer

        try:
            data = NOTIFY_DATA_SCHEMA(kwargs.get(ATTR_DATA) or {})
        except vol.Invalid as err:
            raise ServiceValidationError(f"Invalid data for {DOMAIN}: {err}") from err
        title = kwargs.get(ATTR_TITLE)

        if any(key in data for key in (ATTR_FILE, ATTR_URL, ATTR_CAMERA, ATTR_SVG)):
            # An image, with the message as its caption.
            for key in ("big", "bold", "underline"):
                data.pop(key, None)
            await printer.print_image(title=title, caption=message or None, **data)
        else:
            await printer.print_text(message, title=title, **data)
