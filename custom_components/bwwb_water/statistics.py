"""External statistics for the Energy dashboard, one point per bill at its meter-read date."""
from __future__ import annotations

from datetime import date, datetime, time, tzinfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .const import CCF_TO_FT3, DOMAIN, NAME, STAT_CONSUMPTION, STAT_COST
from .parsers import Bill

CONSUMPTION_METADATA = StatisticMetaData(
    mean_type=StatisticMeanType.NONE,
    has_sum=True,
    name=f"{NAME} consumption",
    source=DOMAIN,
    statistic_id=STAT_CONSUMPTION,
    unit_class=VolumeConverter.UNIT_CLASS,
    unit_of_measurement=UnitOfVolume.CUBIC_FEET,
)
COST_METADATA = StatisticMetaData(
    mean_type=StatisticMeanType.NONE,
    has_sum=True,
    name=f"{NAME} cost",
    source=DOMAIN,
    statistic_id=STAT_COST,
    unit_class=None,
    unit_of_measurement=None,
)


def build_statistics(
    bills: list[Bill], tz: tzinfo
) -> tuple[list[StatisticData], list[StatisticData]]:
    """Rebuild both series from scratch. Same start times every run, so re-imports overwrite."""
    if not bills:
        return [], []
    ordered = sorted(bills, key=lambda b: b.present_read_date)

    def start(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=tz)

    first = ordered[0]
    consumption = [
        StatisticData(start=start(first.previous_read_date), state=first.previous_read * CCF_TO_FT3, sum=0.0)
    ]
    cost = [StatisticData(start=start(first.previous_read_date), state=0.0, sum=0.0)]
    volume_total = 0.0
    money_total = 0.0
    for bill in ordered:
        # Sum the bill's CONS column, not read differences, so a meter swap can't spike.
        volume_total += bill.consumption * CCF_TO_FT3
        bill_cost = bill.water_charges + bill.sewer_charges
        money_total = round(money_total + bill_cost, 2)
        consumption.append(
            StatisticData(start=start(bill.present_read_date), state=bill.present_read * CCF_TO_FT3, sum=volume_total)
        )
        cost.append(StatisticData(start=start(bill.present_read_date), state=round(bill_cost, 2), sum=money_total))
    return consumption, cost


@callback
def async_import_bill_statistics(hass: HomeAssistant, bills: list[Bill]) -> None:
    if not bills:
        return
    consumption, cost = build_statistics(bills, dt_util.get_default_time_zone())
    async_add_external_statistics(hass, CONSUMPTION_METADATA, consumption)
    async_add_external_statistics(hass, COST_METADATA, cost)
