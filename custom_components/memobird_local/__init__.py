"""Memobird thermal printer over the local network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components.notify.legacy import NOTIFY_SERVICES
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey

from .api import MemobirdClient, MemobirdError, PrinterStatus
from .const import DOMAIN
from .printer import MemobirdPrinter

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NOTIFY]
SCAN_INTERVAL = timedelta(minutes=1)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Home Assistant's YAML config, needed to load the legacy notify platform.
DATA_HASS_CONFIG: HassKey[ConfigType] = HassKey(f"{DOMAIN}_hass_config")
# notify.<name> services by config entry id, so they can be removed on unload.
DATA_NOTIFY_SERVICES: HassKey[dict] = HassKey(f"{DOMAIN}_notify_services")


@dataclass
class MemobirdData:
    client: MemobirdClient
    coordinator: DataUpdateCoordinator[PrinterStatus]
    printer: MemobirdPrinter


type MemobirdConfigEntry = ConfigEntry[MemobirdData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data[DATA_HASS_CONFIG] = config
    hass.data.setdefault(DATA_NOTIFY_SERVICES, {})
    return True


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

    entry.runtime_data = MemobirdData(client, coordinator, MemobirdPrinter(hass, entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The classic notify.<name> service (notify groups, blueprints, `data:`).
    # Legacy notify platforms can't be forwarded, only discovered.
    hass.async_create_task(
        discovery.async_load_platform(
            hass,
            Platform.NOTIFY,
            DOMAIN,
            {CONF_NAME: entry.title, "entry_id": entry.entry_id},
            hass.data[DATA_HASS_CONFIG],
        ),
        eager_start=True,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MemobirdConfigEntry) -> bool:
    if service := hass.data[DATA_NOTIFY_SERVICES].pop(entry.entry_id, None):
        await service.async_unregister_services()
        # Keep notify's own bookkeeping in sync, or a reload would re-add it.
        registered = hass.data.get(NOTIFY_SERVICES, {}).get(DOMAIN, [])
        if service in registered:
            registered.remove(service)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
