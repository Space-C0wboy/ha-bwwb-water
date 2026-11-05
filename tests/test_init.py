"""Tests for setup, unload and the v1 -> v2 migration."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import FakeClient, fake_parse_bill_pdf


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


@pytest.fixture
def patched():
    with (
        patch("custom_components.bwwb_water.CAWClient", FakeClient),
        patch(
            "custom_components.bwwb_water.coordinator.parse_bill_pdf",
            side_effect=fake_parse_bill_pdf,
        ),
        patch("custom_components.bwwb_water.coordinator.async_import_bill_statistics"),
    ):
        yield


async def test_v1_entry_migrates_and_adopts_account_number(hass, patched):
    entry = MockConfigEntry(
        domain="bwwb_water",
        version=1,
        unique_id="testuser",
        data={
            "username": "TESTUSER",
            "password": "pw",
            "auth_service_url": "http://192.168.1.5:18792/bwwb/data",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.data == {"username": "TESTUSER", "password": "pw"}
    assert entry.unique_id == "999900001111"


async def test_unload(hass, patched):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_unload_releases_session(hass, patched):
    sessions = []

    def client(session, *args):
        sessions.append(session)
        return FakeClient()

    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    with patch("custom_components.bwwb_water.CAWClient", side_effect=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert not sessions[0].closed
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert sessions[0].closed


async def test_remove_entry_deletes_bill_cache(hass, patched, hass_storage):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert f"bwwb_water.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"bwwb_water.{entry.entry_id}" not in hass_storage
