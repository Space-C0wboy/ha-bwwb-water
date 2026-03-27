"""DataUpdateCoordinator for Birmingham Water Works Board."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BWWBAPI, BWWBAuthError, BWWBConnectionError
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class BWWBDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches BWWB data via the Pi Playwright sidecar."""

    def __init__(self, hass: HomeAssistant, api: BWWBAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest water meter data from sidecar service."""
        try:
            data = await self.api.fetch_data()
            return {
                "water_meter_ft3": data.get("meter_reading_ft3"),
                "last_read_date": data.get("last_read_date"),
                "device_id": data.get("device_id"),
                "contract_id": data.get("contract_id"),
            }
        except BWWBAuthError as exc:
            raise UpdateFailed(f"Authentication error: {exc}") from exc
        except BWWBConnectionError as exc:
            raise UpdateFailed(f"Connection error: {exc}") from exc
