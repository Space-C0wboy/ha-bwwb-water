"""Sensors for Central Alabama Water."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CCF_TO_FT3, DOMAIN, NAME
from .coordinator import CAWConfigEntry, CAWCoordinator, CAWData
from .parsers import Bill


def days_until_due(data: CAWData, today: date) -> int | None:
    """Days until the latest bill is due; 0 once nothing is owed."""
    if data.latest is None:
        return None
    if data.summary.balance <= 0:
        return 0
    return (data.latest.due_date - today).days


def _latest(fn: Callable[[Bill], Any]) -> Callable[[CAWData], Any]:
    return lambda data: fn(data.latest) if data.latest else None


def _previous(fn: Callable[[Bill], Any]) -> Callable[[CAWData], Any]:
    return lambda data: fn(data.previous) if data.previous else None


def _period(bill: Bill | None) -> dict[str, Any]:
    if bill is None:
        return {}
    return {"period_start": bill.period_start.isoformat(), "period_end": bill.period_end.isoformat()}


def _charges_attrs(items_of: Callable[[Bill], tuple]) -> Callable[[CAWData], dict[str, Any]]:
    def attrs(data: CAWData) -> dict[str, Any]:
        bill = data.latest
        if bill is None:
            return {}
        return {
            "line_items": [{"description": i.description, "amount": i.amount} for i in items_of(bill)],
            "service_period": f"{bill.period_start.isoformat()} - {bill.period_end.isoformat()}",
        }

    return attrs


def _scheduled_state(data: CAWData) -> str:
    return data.payments.scheduled[0].status if data.payments.scheduled else "None"


def _scheduled_attrs(data: CAWData) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if data.payments.scheduled:
        nxt = data.payments.scheduled[0]
        attrs.update(type=nxt.type, scheduled=nxt.scheduled, amount=nxt.amount, funding_source=nxt.funding_source)
    if data.payments.completed:
        last = data.payments.completed[0]
        attrs.update(
            last_payment_date=last.payment_date.isoformat(),
            last_payment_amount=last.amount,
            last_payment_confirmation=last.confirmation,
        )
    return attrs


@dataclass(frozen=True, kw_only=True)
class CAWSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CAWData], Any]
    attrs_fn: Callable[[CAWData], dict[str, Any]] = lambda data: {}


SENSORS: tuple[CAWSensorDescription, ...] = (
    CAWSensorDescription(
        key="water_meter",
        name="Water Meter",
        icon="mdi:water-pump",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_FEET,
        suggested_unit_of_measurement=UnitOfVolume.CUBIC_FEET,
        value_fn=_latest(lambda b: b.present_read * CCF_TO_FT3),
        attrs_fn=lambda d: {"meter_number": d.latest.meter_number} if d.latest else {},
    ),
    CAWSensorDescription(
        key="water_meter_ccf",
        name="Water Meter (CCF)",
        icon="mdi:water-pump",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        value_fn=_latest(lambda b: b.present_read),
    ),
    CAWSensorDescription(
        key="last_read_date",
        name="Last Read Date",
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.DATE,
        value_fn=_latest(lambda b: b.present_read_date),
        attrs_fn=lambda d: {"previous_read_date": d.latest.previous_read_date.isoformat()} if d.latest else {},
    ),
    CAWSensorDescription(
        key="current_period_ccf",
        name="Current Period Usage",
        icon="mdi:water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        suggested_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        value_fn=_latest(lambda b: b.consumption),
        attrs_fn=lambda d: _period(d.latest),
    ),
    CAWSensorDescription(
        key="prev_period_ccf",
        name="Previous Period Usage",
        icon="mdi:water-outline",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        suggested_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        value_fn=_previous(lambda b: b.consumption),
        attrs_fn=lambda d: _period(d.previous),
    ),
    CAWSensorDescription(
        key="current_balance",
        name="Current Balance",
        icon="mdi:currency-usd",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda d: d.summary.balance,
        attrs_fn=lambda d: {"payment_status": d.summary.payment_status},
    ),
    CAWSensorDescription(
        key="last_bill_amount",
        name="Last Bill Amount",
        icon="mdi:receipt-text",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=_latest(lambda b: b.total_due),
        attrs_fn=lambda d: (
            {"bill_date": d.latest.invoice_date.isoformat(), "due_date": d.latest.due_date.isoformat()}
            if d.latest
            else {}
        ),
    ),
    CAWSensorDescription(
        key="last_bill_due_date",
        name="Last Bill Due Date",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
        value_fn=_latest(lambda b: b.due_date),
    ),
    CAWSensorDescription(
        key="water_charges",
        name="Water Charges",
        icon="mdi:water-check",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=_latest(lambda b: b.water_charges),
        attrs_fn=_charges_attrs(lambda b: b.water_line_items),
    ),
    CAWSensorDescription(
        key="sewer_charges",
        name="Sewer Charges",
        icon="mdi:pipe",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=_latest(lambda b: b.sewer_charges),
        attrs_fn=_charges_attrs(lambda b: b.sewer_line_items),
    ),
    CAWSensorDescription(
        key="scheduled_payment",
        name="Scheduled Payment",
        icon="mdi:calendar-sync",
        value_fn=_scheduled_state,
        attrs_fn=_scheduled_attrs,
    ),
    CAWSensorDescription(
        key="days_until_due",
        name="Days Until Due",
        icon="mdi:calendar-alert",
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda d: days_until_due(d, dt_util.now().date()),
        attrs_fn=lambda d: (
            {"due_date": d.latest.due_date.isoformat(), "paid": d.summary.balance <= 0} if d.latest else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: CAWConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(CAWSensor(coordinator, description) for description in SENSORS)


class CAWSensor(CoordinatorEntity[CAWCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: CAWSensorDescription

    def __init__(self, coordinator: CAWCoordinator, description: CAWSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=NAME,
            manufacturer=NAME,
            model="Water meter",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs_fn(self.coordinator.data) or None
