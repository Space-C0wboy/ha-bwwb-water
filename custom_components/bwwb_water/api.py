"""Birmingham Water Works Board API client.

Authentication & Data note
--------------------------
BWWB uses Utegration Mobius — a SAP IS-U frontend built on SAPUI5. Two layers
prevent direct HTTP access from Home Assistant:

1. **JS-rendered login**: The login page is pure SAPUI5 JavaScript. Plain HTTP
   POST to the SAP ITS endpoint returns 403. A real browser is required.

2. **Cloudflare WAF on OData endpoints**: Even with valid session cookies,
   direct aiohttp calls to /sap/opu/odata/... return Cloudflare challenge pages
   ("Just a moment..."). The Cloudflare clearance is bound to the browser
   session's TLS fingerprint and cannot be transferred to aiohttp.

Solution
--------
A local Playwright sidecar service handles BOTH login AND all OData data
fetching inside the browser session. HA calls POST /bwwb/data and receives
pre-parsed meter data as JSON — no direct calls to web.bwwb.org needed.

This is similar to the Southern Company sidecar (Incapsula WAF bypass) but
more comprehensive since even the API layer is WAF-protected.

See: https://github.com/Space-C0wboy/ha-bwwb-water#architecture
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import (
    AUTH_SERVICE_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
)

_LOGGER = logging.getLogger(__name__)


class BWWBAuthError(Exception):
    """Authentication failed."""


class BWWBConnectionError(Exception):
    """Connection error."""


class BWWBAPI:
    """Client for the Birmingham Water Works Board.

    All data fetching is delegated to the local Playwright sidecar service.
    The sidecar performs login and OData queries inside a real browser session
    that carries Cloudflare clearance. HA receives pre-parsed JSON data.
    """

    def __init__(self, auth_service_url: str | None = None) -> None:
        self._username: str = ""
        self._password: str = ""
        self._last_data: dict[str, Any] = {}
        self._auth_service_url: str = auth_service_url or AUTH_SERVICE_URL

    async def login(self, username: str, password: str) -> bool:
        """Validate credentials by fetching data from the sidecar service."""
        self._username = username
        self._password = password
        # login() doubles as the initial data fetch — if it succeeds, auth works
        data = await self.fetch_data()
        return bool(data)

    async def fetch_data(self) -> dict[str, Any]:
        """Fetch all BWWB data via the Pi sidecar service."""
        _LOGGER.debug("BWWB: requesting data from sidecar at %s", self._auth_service_url)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._auth_service_url,
                    json={"username": self._username, "password": self._password},
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    result = await resp.json(content_type=None)
        except aiohttp.ClientConnectorError as exc:
            raise BWWBConnectionError(
                f"Cannot reach BWWB sidecar at {self._auth_service_url}. "
                f"Is the utility-auth-service running? Error: {exc}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise BWWBConnectionError(f"Sidecar HTTP error: {exc}") from exc

        if not result.get("success"):
            error = result.get("error", "unknown error")
            if any(w in error.lower() for w in ["invalid", "failed", "login", "401"]):
                raise BWWBAuthError(f"BWWB login failed: {error}")
            raise BWWBConnectionError(f"Sidecar error: {error}")

        self._last_data = result
        return result

    @property
    def meter_reading_ft3(self) -> float | None:
        """Latest cumulative meter reading in ft³ (CCF × 100)."""
        return self._last_data.get("meter_reading_ft3")

    @property
    def last_read_date(self) -> str | None:
        """Date string of the most recent meter read."""
        return self._last_data.get("last_read_date")

    @property
    def device_id(self) -> str | None:
        return self._last_data.get("device_id")

    @property
    def contract_id(self) -> str | None:
        return self._last_data.get("contract_id")
