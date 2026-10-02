"""DataUpdateCoordinator for Central Alabama Water."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import CAWAuthError, CAWClient, CAWConnectionError, CAWPortalChangedError
from .const import DOMAIN, STORAGE_VERSION, UPDATE_INTERVAL
from .parsers import (
    AccountSummary,
    Bill,
    Payments,
    PortalParseError,
    bill_from_dict,
    bill_to_dict,
    parse_bill_pdf,
    parse_payments,
    parse_statement_list,
    parse_summary,
)
from .statistics import async_import_bill_statistics

_LOGGER = logging.getLogger(__name__)

type CAWConfigEntry = ConfigEntry[CAWCoordinator]


@dataclass
class CAWData:
    summary: AccountSummary
    payments: Payments
    bills: list[Bill]  # oldest first

    @property
    def latest(self) -> Bill | None:
        return self.bills[-1] if self.bills else None

    @property
    def previous(self) -> Bill | None:
        return self.bills[-2] if len(self.bills) > 1 else None


class CAWCoordinator(DataUpdateCoordinator[CAWData]):
    """Polls the portal; downloads each statement PDF once and caches the parsed bill."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: CAWClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._bills: dict[str, Bill] | None = None
        self._statistics_imported = False

    async def _async_load_bills(self) -> dict[str, Bill]:
        stored = await self._store.async_load() or {}
        try:
            return {key: bill_from_dict(value) for key, value in stored.get("bills", {}).items()}
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Discarding unreadable bill cache, statements will be re-downloaded: %s", err)
            return {}

    async def _async_update_data(self) -> CAWData:
        if self._bills is None:
            self._bills = await self._async_load_bills()

        try:
            summary = parse_summary(await self.client.get_summary_html())
            refs = parse_statement_list(await self.client.get_statement_history_html())
            payments = parse_payments(await self.client.get_payments_html())
        except CAWAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CAWConnectionError, CAWPortalChangedError, PortalParseError) as err:
            raise UpdateFailed(str(err)) from err

        added = False
        for ref in refs:
            key = ref.issue_date.isoformat()
            if key in self._bills:
                continue
            try:
                pdf = await self.client.get_statement_pdf(summary.account_number, ref.issue_date)
                bill = await self.hass.async_add_executor_job(parse_bill_pdf, pdf)
            except CAWAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (CAWConnectionError, CAWPortalChangedError, PortalParseError) as err:
                _LOGGER.warning("Skipping statement %s until the next update: %s", key, err)
                continue
            except Exception:  # one odd statement must not fail the whole poll
                _LOGGER.exception("Skipping statement %s until the next update", key)
                continue
            self._bills[key] = bill
            added = True

        bills = sorted(self._bills.values(), key=lambda b: b.present_read_date)
        if added:
            await self._store.async_save({"bills": {k: bill_to_dict(b) for k, b in self._bills.items()}})
        if bills and (added or not self._statistics_imported):
            async_import_bill_statistics(self.hass, bills)
            self._statistics_imported = True
        return CAWData(summary=summary, payments=payments, bills=bills)
