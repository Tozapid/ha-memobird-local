"""Config flow for Memobird (local)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import MemobirdClient, MemobirdError
from .const import CONF_DEFAULT_FORMAT, CONF_FONT_SIZE, DOMAIN
from .render import (
    DEFAULT_FONT_SIZE,
    FORMAT_PLAIN,
    FORMATS,
    MAX_FONT_SIZE,
    MIN_FONT_SIZE,
)

DEFAULT_NAME = "Memobird"


class MemobirdConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MemobirdOptionsFlow()

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


class MemobirdOptionsFlow(OptionsFlow):
    """Default text format and font size for formatted printing."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEFAULT_FORMAT,
                        default=options.get(CONF_DEFAULT_FORMAT, FORMAT_PLAIN),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=FORMATS,
                            translation_key="format",
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_FONT_SIZE,
                        default=options.get(CONF_FONT_SIZE, DEFAULT_FONT_SIZE),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(
                                min=MIN_FONT_SIZE,
                                max=MAX_FONT_SIZE,
                                step=1,
                                mode=NumberSelectorMode.SLIDER,
                                unit_of_measurement="px",
                            )
                        ),
                        vol.Coerce(int),
                    ),
                }
            ),
        )
