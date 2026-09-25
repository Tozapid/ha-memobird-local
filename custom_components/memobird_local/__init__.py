"""Memobird thermal printer over the local network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MemobirdClient, MemobirdError, PrinterStatus
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NOTIFY]
SCAN_INTERVAL = timedelta(minutes=1)


@dataclass
class MemobirdData:
    client: MemobirdClient
    coordinator: DataUpdateCoordinator[PrinterStatus]


type MemobirdConfigEntry = ConfigEntry[MemobirdData]


async def async_setup_entry(hass: HomeAssistant, entry: MemobirdConfigEntry) -> bool:
    client = MemobirdClient(async_get_clientsession(hass), entry.data[CONF_HOST])

    async def _update() -> PrinterStatus:
        try:
            return await client.status()
        except MemobirdError as err:
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_method=_update,
        update_interval=SCAN_INTERVAL,
    )
    # A sleeping or rebooting printer shouldn't block setup: the
    # connectivity sensor reports it and printing retries on demand.
    await coordinator.async_refresh()

    entry.runtime_data = MemobirdData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MemobirdConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
