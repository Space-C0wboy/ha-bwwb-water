"""Sensor platform for Birmingham Water Works Board."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import BWWBAPI
from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)

UNIT_CCF = "CCF"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    api: BWWBAPI = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BWWBWaterMeterSensor(api, entry),
        BWWBWaterMeterCCFSensor(api, entry),
        BWWBLastReadDateSensor(api, entry),
        BWWBCurrentPeriodUsageSensor(api, entry),
        BWWBPrevPeriodUsageSensor(api, entry),
        BWWBCurrentBalanceSensor(api, entry),
        BWWBLastBillAmountSensor(api, entry),
        BWWBLastBillDueDateSensor(api, entry),
    ], update_before_add=True)


class BWWBBaseSensor(SensorEntity):
    _attr_should_poll = True
    def __init__(self, api: BWWBAPI, entry: ConfigEntry) -> None:
        self._api = api
        self._entry = entry
        self._data: dict[str, Any] = {}
        self._last_good: dict[str, Any] = {}
    async def async_update(self) -> None:
        try:
            data = await self._api.fetch_data()
            if data and data.get("success"):
                self._data = data
                self._last_good = data
            else:
                self._data = self._last_good
        except Exception as exc:
            _LOGGER.warning("BWWB update error (using last good data): %s", exc)
            self._data = self._last_good
    @property
    def device_info(self) -> dict[str, Any]:
        return {"identifiers": {(DOMAIN, self._entry.entry_id)}, "name": NAME, "manufacturer": "Birmingham Water Works Board", "model": "SAP IS-U Water Meter"}


class BWWBWaterMeterSensor(BWWBBaseSensor):
    _attr_name = "BWWB Water Meter"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_FEET
    _attr_icon = "mdi:water-pump"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_water_meter"
    @property
    def native_value(self): return self._data.get("meter_reading_ft3")


class BWWBWaterMeterCCFSensor(BWWBBaseSensor):
    _attr_name = "BWWB Water Meter (CCF)"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UNIT_CCF
    _attr_icon = "mdi:water-pump"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_water_meter_ccf"
    @property
    def native_value(self): return self._data.get("meter_reading_ccf")


class BWWBLastReadDateSensor(BWWBBaseSensor):
    _attr_name = "BWWB Last Read Date"
    _attr_icon = "mdi:calendar-check"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_read_date"
    @property
    def native_value(self): return self._data.get("last_read_date")
    @property
    def extra_state_attributes(self):
        return {"device_id": self._data.get("device_id"), "contract_id": self._data.get("contract_id")}


class BWWBCurrentPeriodUsageSensor(BWWBBaseSensor):
    _attr_name = "BWWB Current Period Usage"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UNIT_CCF
    _attr_icon = "mdi:water"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_period_ccf"
    @property
    def native_value(self): return self._data.get("current_period_ccf")
    @property
    def extra_state_attributes(self):
        return {
            "period_start": self._data.get("current_period_start"),
            "period_end": self._data.get("current_period_end"),
        }


class BWWBPrevPeriodUsageSensor(BWWBBaseSensor):
    _attr_name = "BWWB Previous Period Usage"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UNIT_CCF
    _attr_icon = "mdi:water-outline"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_prev_period_ccf"
    @property
    def native_value(self): return self._data.get("prev_period_ccf")


class BWWBCurrentBalanceSensor(BWWBBaseSensor):
    _attr_name = "BWWB Current Balance"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "USD"
    _attr_icon = "mdi:currency-usd"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_balance"
    @property
    def native_value(self): return self._data.get("current_balance")
    @property
    def extra_state_attributes(self):
        return {"past_due": self._data.get("past_due")}


class BWWBLastBillAmountSensor(BWWBBaseSensor):
    _attr_name = "BWWB Last Bill Amount"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "USD"
    _attr_icon = "mdi:receipt-text"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_bill_amount"
    @property
    def native_value(self): return self._data.get("last_bill_amount")
    @property
    def extra_state_attributes(self):
        return {
            "bill_date": self._data.get("last_bill_date"),
            "due_date": self._data.get("last_bill_due_date"),
        }


class BWWBLastBillDueDateSensor(BWWBBaseSensor):
    _attr_name = "BWWB Last Bill Due Date"
    _attr_icon = "mdi:calendar-clock"
    def __init__(self, api, entry):
        super().__init__(api, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_bill_due_date"
    @property
    def native_value(self): return self._data.get("last_bill_due_date")
