"""Connectivity sensor for the Memobird printer."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MemobirdConfigEntry
from .entity import MemobirdEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MemobirdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MemobirdConnectivity(entry)])


class MemobirdConnectivity(MemobirdEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: MemobirdConfigEntry) -> None:
        super().__init__(entry, "connectivity")

    @property
    def available(self) -> bool:
        # Being unreachable is exactly what this sensor reports.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict | None:
        status = self.coordinator.data
        if not self.coordinator.last_update_success or status is None:
            return None
        return {"printer_state": status.printer_state, "busy": status.busy}
