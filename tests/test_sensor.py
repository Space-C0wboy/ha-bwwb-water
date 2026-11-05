"""Tests for the sensor platform."""

from datetime import date
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bwwb_water.coordinator import CAWData
from custom_components.bwwb_water.parsers import AccountSummary, Payments
from custom_components.bwwb_water.sensor import days_until_due

from .common import BILLS, FakeClient, fake_parse_bill_pdf


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    with (
        patch("custom_components.bwwb_water.CAWClient", FakeClient),
        patch(
            "custom_components.bwwb_water.coordinator.parse_bill_pdf",
            side_effect=fake_parse_bill_pdf,
        ),
        patch("custom_components.bwwb_water.coordinator.async_import_bill_statistics"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def state(hass, entry, key):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "bwwb_water", f"{entry.entry_id}_{key}"
    )
    assert entity_id, key
    return hass.states.get(entity_id)


async def test_sensor_values(hass, entry):
    numeric = {
        "water_meter": 93900,
        "water_meter_ccf": 939,
        "current_period_ccf": 4,
        "prev_period_ccf": 5,
        "current_balance": -99.99,
        "last_bill_amount": 99.99,
        "water_charges": 43.97,
        "sewer_charges": 56.02,
        "days_until_due": 0,
    }
    text = {
        "last_read_date": "2026-09-02",
        "last_bill_due_date": "2026-09-16",
        "scheduled_payment": "Waiting for bill",
    }
    for key, value in numeric.items():
        assert float(state(hass, entry, key).state) == value, key
    for key, value in text.items():
        assert state(hass, entry, key).state == value, key


async def test_sensor_attributes(hass, entry):
    assert state(hass, entry, "water_meter").attributes["meter_number"] == "000000001"
    assert state(hass, entry, "scheduled_payment").attributes["last_payment_amount"] == 102.99
    assert state(hass, entry, "scheduled_payment").attributes["last_payment_date"] == "2026-09-22"
    assert state(hass, entry, "days_until_due").attributes["paid"] is True
    assert state(hass, entry, "water_charges").attributes["line_items"] == [
        {"description": "Base Service Charge", "amount": 28.49}
    ]
    assert (
        state(hass, entry, "current_balance").attributes["payment_status"]
        == "Auto Draft Compl 09/22/2026"
    )


def data_with(balance: float, bills=None) -> CAWData:
    bills = [BILLS[d] for d in sorted(BILLS)] if bills is None else bills
    return CAWData(AccountSummary("999900001111", balance, None), Payments((), ()), bills)


@pytest.mark.parametrize(
    ("balance", "today", "expected"),
    [
        (99.99, date(2026, 9, 10), 6),  # due 2026-09-16
        (99.99, date(2026, 9, 16), 0),
        (99.99, date(2026, 9, 20), -4),
        (0.0, date(2026, 9, 10), 0),
        (-5.0, date(2026, 9, 10), 0),
    ],
)
def test_days_until_due(balance, today, expected):
    assert days_until_due(data_with(balance), today) == expected


def test_days_until_due_without_bills():
    assert days_until_due(data_with(10.0, bills=[]), date(2026, 9, 10)) is None
