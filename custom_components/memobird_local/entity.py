"""Shared base entity for Memobird."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MemobirdConfigEntry
from .const import DOMAIN


class MemobirdEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: MemobirdConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="Memobird",
            model="Thermal printer",
            configuration_url=f"http://{entry.data[CONF_HOST]}/sys/printer",
        )
