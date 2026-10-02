"""Tests for the config flow."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bwwb_water.client import CAWAuthError, CAWConnectionError, CAWPortalChangedError
from custom_components.bwwb_water.config_flow import validate_credentials

VALIDATE = "custom_components.bwwb_water.config_flow.validate_credentials"
SETUP = "custom_components.bwwb_water.async_setup_entry"
CREDS = {"username": "TESTUSER", "password": "pw"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_setup():
    with patch(SETUP, return_value=True, create=True):
        yield


async def test_user_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init("bwwb_water", context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Central Alabama Water (999900001111)"
    assert result["data"] == CREDS
    assert result["result"].unique_id == "999900001111"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CAWAuthError(), "invalid_auth"),
        (CAWConnectionError(), "cannot_connect"),
        (CAWPortalChangedError(), "portal_changed"),
        (RuntimeError(), "unknown"),
    ],
)
async def test_user_flow_errors(hass, error, code):
    result = await hass.config_entries.flow.async_init("bwwb_water", context={"source": config_entries.SOURCE_USER})
    with patch(VALIDATE, side_effect=error):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": code}


async def test_duplicate_account_aborts(hass):
    MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init("bwwb_water", context={"source": config_entries.SOURCE_USER})
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


async def test_reconfigure_updates_credentials(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "NEWUSER", "password": "new"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"username": "NEWUSER", "password": "new"}


async def test_reconfigure_rejects_other_account(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(VALIDATE, return_value="888800002222"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["reason"] == "unique_id_mismatch"


async def test_reconfigure_legacy_entry_adopts_account_number(hass):
    # A migrated v1 entry keeps its username unique_id until a setup succeeds.
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="testuser", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "NEWUSER", "password": "new"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"username": "NEWUSER", "password": "new"}
    assert entry.unique_id == "999900001111"


async def test_validate_credentials_releases_session(hass):
    sessions = []

    def client(session, *args):
        sessions.append(session)
        return MagicMock(get_summary_html=AsyncMock(side_effect=CAWAuthError()))

    with patch("custom_components.bwwb_water.config_flow.CAWClient", side_effect=client), pytest.raises(CAWAuthError):
        await validate_credentials(hass, "TESTUSER", "pw")
    assert sessions[0].closed
