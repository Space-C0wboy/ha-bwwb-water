"""Tests for external statistics building/import."""

from dataclasses import replace
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from custom_components.bwwb_water.statistics import (
    async_import_bill_statistics,
    build_statistics,
)

from .common import BILLS, make_bill

TZ = ZoneInfo("America/Chicago")


def midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ)


def test_consumption_rows_baseline_and_running_sum():
    consumption, _ = build_statistics(list(BILLS.values())[::-1], TZ)  # unsorted input
    assert [(r["start"], r["state"], r["sum"]) for r in consumption] == [
        (midnight(date(2026, 6, 3)), 92500, 0.0),  # baseline: oldest bill's previous read
        (midnight(date(2026, 7, 2)), 93000, 500.0),
        (midnight(date(2026, 8, 4)), 93500, 1000.0),
        (midnight(date(2026, 9, 2)), 93900, 1400.0),
    ]


def test_cost_rows_sum_water_plus_sewer():
    _, cost = build_statistics(list(BILLS.values()), TZ)
    assert [(r["start"], r["sum"]) for r in cost] == [
        (midnight(date(2026, 6, 3)), 0.0),
        (midnight(date(2026, 7, 2)), 99.99),
        (midnight(date(2026, 8, 4)), 199.98),
        (midnight(date(2026, 9, 2)), 299.97),
    ]


def test_sum_uses_cons_not_read_difference():
    bills = [
        make_bill(date(2026, 7, 2), 930, 5),
        make_bill(date(2026, 8, 4), 3, 3),
    ]  # meter swapped
    consumption, _ = build_statistics(bills, TZ)
    assert [r["sum"] for r in consumption] == [0.0, 500.0, 800.0]


def test_build_is_deterministic():
    assert build_statistics(list(BILLS.values()), TZ) == build_statistics(list(BILLS.values()), TZ)


async def test_import_calls_recorder(hass):
    with patch("custom_components.bwwb_water.statistics.async_add_external_statistics") as add:
        async_import_bill_statistics(hass, list(BILLS.values()))
    assert [c.args[1]["statistic_id"] for c in add.call_args_list] == [
        "bwwb_water:water_consumption",
        "bwwb_water:water_cost",
    ]
    assert len(add.call_args_list[0].args[2]) == 4


def test_build_empty():
    assert build_statistics([], TZ) == ([], [])


async def test_import_empty_does_nothing(hass):
    with patch("custom_components.bwwb_water.statistics.async_add_external_statistics") as add:
        async_import_bill_statistics(hass, [])
    assert add.call_count == 0


def test_reissued_bill_keeps_latest_invoice():
    original = make_bill(date(2026, 9, 2), 939, 4)
    reissued = replace(original, invoice_date=date(2026, 9, 10), water_charges=40.00)
    for bills in ([original, reissued], [reissued, original]):
        consumption, cost = build_statistics([BILLS[date(2026, 8, 4)], *bills], TZ)
        assert [r["sum"] for r in consumption] == [0.0, 500.0, 900.0]
        assert cost[-1]["state"] == round(40.00 + 56.02, 2)
