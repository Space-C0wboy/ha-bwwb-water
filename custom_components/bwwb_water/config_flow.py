"""Config flow for Central Alabama Water."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import CAWAuthError, CAWClient, CAWConnectionError, CAWPortalChangedError
from .const import DOMAIN, NAME
from .parsers import PortalParseError, parse_summary

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
PASSWORD_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def validate_credentials(hass: HomeAssistant, username: str, password: str) -> str:
    """Log in for real and return the portal account number."""
    # Not created during entry setup, so HA would only release it at shutdown.
    session = async_create_clientsession(hass, auto_cleanup=False)
    try:
        client = CAWClient(session, username, password)
        return parse_summary(await client.get_summary_html()).account_number
    finally:
        session.detach()


class BWWBConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Central Alabama Water."""

    VERSION = 2

    async def _validate(self, username: str, password: str) -> tuple[str | None, dict[str, str]]:
        try:
            return await validate_credentials(self.hass, username, password), {}
        except CAWAuthError:
            return None, {"base": "invalid_auth"}
        except CAWConnectionError:
            return None, {"base": "cannot_connect"}
        except (CAWPortalChangedError, PortalParseError):
            return None, {"base": "portal_changed"}
        except Exception:
            _LOGGER.exception("Unexpected error validating Central Alabama Water credentials")
            return None, {"base": "unknown"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if account:
                await self.async_set_unique_id(account)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"{NAME} ({account})", data=user_input)
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if account:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if account:
                # A migrated v1 entry is still keyed by username until a setup succeeds;
                # let reconfigure adopt the account number instead of aborting.
                if entry.unique_id == entry.data[CONF_USERNAME].lower():
                    return self.async_update_reload_and_abort(entry, unique_id=account, data_updates=user_input)
                await self.async_set_unique_id(account)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, {CONF_USERNAME: entry.data[CONF_USERNAME]}),
            errors=errors,
        )
