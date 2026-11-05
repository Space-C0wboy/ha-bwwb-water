"""Tests for the data update coordinator."""

from datetime import date
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bwwb_water.client import CAWAuthError, CAWConnectionError
from custom_components.bwwb_water.coordinator import CAWCoordinator
from custom_components.bwwb_water.parsers import PortalParseError

from .common import BILLS, FakeClient, fake_parse_bill_pdf

PARSE = "custom_components.bwwb_water.coordinator.parse_bill_pdf"
IMPORT = "custom_components.bwwb_water.coordinator.async_import_bill_statistics"


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    return entry


async def test_first_update_backfills_all_statements(hass, entry, hass_storage):
    client = FakeClient()
    coordinator = CAWCoordinator(hass, entry, client)
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT) as imp:
        data = await coordinator._async_update_data()
    assert sorted(client.pdf_calls) == sorted(BILLS)
    assert data.bills == [BILLS[d] for d in sorted(BILLS)]
    assert data.latest == BILLS[date(2026, 9, 2)]
    assert data.previous == BILLS[date(2026, 8, 4)]
    assert data.summary.balance == -99.99
    imp.assert_called_once()
    await hass.async_block_till_done()
    assert set(hass_storage[f"bwwb_water.{entry.entry_id}"]["data"]["bills"]) == {
        "2026-07-02",
        "2026-08-04",
        "2026-09-02",
    }


async def test_second_update_downloads_nothing_new(hass, entry):
    client = FakeClient()
    coordinator = CAWCoordinator(hass, entry, client)
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT) as imp:
        await coordinator._async_update_data()
        await coordinator._async_update_data()
    assert len(client.pdf_calls) == 3
    imp.assert_called_once()


async def test_bills_reload_from_store(hass, entry):
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT):
        await CAWCoordinator(hass, entry, FakeClient())._async_update_data()
    await hass.async_block_till_done()
    client = FakeClient()
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT) as imp:
        data = await CAWCoordinator(hass, entry, client)._async_update_data()
    assert client.pdf_calls == []
    assert len(data.bills) == 3
    imp.assert_called_once()  # statistics re-imported once per HA start


async def test_corrupt_store_is_discarded_and_redownloaded(hass, entry, hass_storage):
    key = f"bwwb_water.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"bills": {"2026-09-02": {"bogus": 1}}},
    }
    client = FakeClient()
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT):
        data = await CAWCoordinator(hass, entry, client)._async_update_data()
    assert len(client.pdf_calls) == 3
    assert len(data.bills) == 3


async def test_store_with_wrong_shape_is_discarded(hass, entry, hass_storage):
    key = f"bwwb_water.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"bills": []}}
    client = FakeClient()
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT):
        data = await CAWCoordinator(hass, entry, client)._async_update_data()
    assert len(data.bills) == 3


async def test_failed_pdf_is_skipped_and_retried(hass, entry):
    client = FakeClient(fail_pdfs={date(2026, 8, 4)})
    coordinator = CAWCoordinator(hass, entry, client)
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT):
        data = await coordinator._async_update_data()
        assert len(data.bills) == 2
        client.fail_pdfs.clear()
        data = await coordinator._async_update_data()
    assert len(data.bills) == 3
    assert client.pdf_calls.count(date(2026, 8, 4)) == 2


async def test_auth_error_triggers_reauth(hass, entry):
    coordinator = CAWCoordinator(hass, entry, FakeClient(summary_error=CAWAuthError("no")))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_connection_error_is_update_failed(hass, entry):
    coordinator = CAWCoordinator(hass, entry, FakeClient(summary_error=CAWConnectionError("down")))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.parametrize("error", [PortalParseError("bad statement"), ValueError("unexpected")])
async def test_unparseable_statement_is_skipped(hass, entry, error):
    def parse(data: bytes):
        if data == b"%PDF-2026-08-04":
            raise error
        return fake_parse_bill_pdf(data)

    coordinator = CAWCoordinator(hass, entry, FakeClient())
    with patch(PARSE, side_effect=parse), patch(IMPORT):
        data = await coordinator._async_update_data()
    assert data.bills == [BILLS[date(2026, 7, 2)], BILLS[date(2026, 9, 2)]]


async def test_statement_auth_error_still_triggers_reauth(hass, entry):
    coordinator = CAWCoordinator(hass, entry, FakeClient())
    with (
        patch(PARSE, side_effect=CAWAuthError("no")),
        patch(IMPORT),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await coordinator._async_update_data()
