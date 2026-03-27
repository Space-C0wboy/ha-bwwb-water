"""Config flow for Birmingham Water Works Board integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError

from .api import BWWBAPI, BWWBAuthError, BWWBConnectionError
from .const import DOMAIN, NAME, CONF_USERNAME, CONF_PASSWORD, CONF_AUTH_SERVICE_URL, AUTH_SERVICE_URL

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_AUTH_SERVICE_URL, default=AUTH_SERVICE_URL): str,
    }
)


class BWWBConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Birmingham Water Works Board."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                api = BWWBAPI(auth_service_url=user_input[CONF_AUTH_SERVICE_URL])
                success = await api.login(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                if not success:
                    errors["base"] = "invalid_auth"
                else:
                    await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"{NAME} ({user_input[CONF_USERNAME]})",
                        data=user_input,
                    )
            except BWWBAuthError:
                errors["base"] = "invalid_auth"
            except BWWBConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during BWWB config flow")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
