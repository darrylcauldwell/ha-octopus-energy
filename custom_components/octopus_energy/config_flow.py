"""Config flow for Octopus Energy."""

from __future__ import annotations

import logging
from typing import Any

from aiooctopusenergy import (
    OctopusEnergyAuthenticationError,
    OctopusEnergyClient,
    OctopusEnergyConnectionError,
    OctopusEnergyNotFoundError,
)
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_API_KEY,
    CONF_COMPARISON_MONTHS,
    CONF_COMPARISON_PRODUCTS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_COMPARISON_MONTHS,
    DEFAULT_COMPARISON_PRODUCTS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_ACCOUNT_NUMBER): str,
    }
)


async def _validate_credentials(
    hass: HomeAssistant, api_key: str, account_number: str
) -> dict[str, str]:
    """Validate credentials against the API. Returns errors dict."""
    errors: dict[str, str] = {}

    session = async_get_clientsession(hass)
    client = OctopusEnergyClient(api_key=api_key, session=session)
    try:
        await client.get_account(account_number)
    except OctopusEnergyAuthenticationError:
        errors["base"] = "invalid_auth"
    except OctopusEnergyNotFoundError:
        errors["base"] = "account_not_found"
    except OctopusEnergyConnectionError:
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception during validation")
        errors["base"] = "unknown"

    return errors


class OctopusEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Octopus Energy."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry,
    ) -> OctopusEnergyOptionsFlow:
        """Create the options flow."""
        return OctopusEnergyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            account_number = user_input[CONF_ACCOUNT_NUMBER].strip().upper()

            errors = await _validate_credentials(
                self.hass, api_key, account_number
            )

            if not errors:
                await self.async_set_unique_id(account_number)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Octopus Energy ({account_number})",
                    data={
                        CONF_API_KEY: api_key,
                        CONF_ACCOUNT_NUMBER: account_number,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class OctopusEnergyOptionsFlow(OptionsFlow):
    """Handle options flow for Octopus Energy."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            new_api_key = user_input[CONF_API_KEY].strip()
            current_api_key = self.config_entry.data.get(CONF_API_KEY, "")
            account_number = self.config_entry.data[CONF_ACCOUNT_NUMBER]

            if new_api_key != current_api_key:
                errors = await _validate_credentials(
                    self.hass, new_api_key, account_number
                )

                if not errors:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_API_KEY: new_api_key,
                        },
                    )

            if not errors:
                # Parse comparison products from comma-separated string
                products_str = user_input.get(
                    CONF_COMPARISON_PRODUCTS, ""
                )
                if isinstance(products_str, str) and products_str.strip():
                    products = [
                        p.strip() for p in products_str.split(",") if p.strip()
                    ]
                else:
                    products = DEFAULT_COMPARISON_PRODUCTS

                return self.async_create_entry(
                    data={
                        CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
                        CONF_COMPARISON_MONTHS: user_input.get(
                            CONF_COMPARISON_MONTHS, DEFAULT_COMPARISON_MONTHS
                        ),
                        CONF_COMPARISON_PRODUCTS: products,
                    },
                )

        current_products = self.config_entry.options.get(
            CONF_COMPARISON_PRODUCTS, DEFAULT_COMPARISON_PRODUCTS
        )
        products_default = (
            ", ".join(current_products)
            if isinstance(current_products, list)
            else current_products
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_KEY,
                    default=self.config_entry.data.get(CONF_API_KEY, ""),
                ): str,
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=10, max=120)),
                vol.Required(
                    CONF_COMPARISON_MONTHS,
                    default=self.config_entry.options.get(
                        CONF_COMPARISON_MONTHS, DEFAULT_COMPARISON_MONTHS
                    ),
                ): vol.All(int, vol.Range(min=1, max=12)),
                vol.Optional(
                    CONF_COMPARISON_PRODUCTS,
                    default=products_default,
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
