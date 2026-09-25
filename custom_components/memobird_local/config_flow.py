"""Config flow for Memobird (local)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MemobirdClient, MemobirdError
from .const import DOMAIN

DEFAULT_NAME = "Memobird"


class MemobirdConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(host.lower())
            self._abort_if_unique_id_configured()

            client = MemobirdClient(async_get_clientsession(self.hass), host)
            try:
                await client.status()
            except MemobirdError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                    data={CONF_HOST: host},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                    }
                ),
                user_input,
            ),
            errors=errors,
        )
