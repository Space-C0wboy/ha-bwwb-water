"""Pure parsers for the Central Alabama Water portal (HTML pages + statement PDFs).

No I/O here: every function takes the page body and returns dataclasses, so
each one can be tested against fixture files.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
import re

from bs4 import BeautifulSoup, Tag

_LOGGER = logging.getLogger(__name__)


class PortalParseError(Exception):
    """A portal page or statement did not have the expected structure."""


@dataclass(frozen=True)
class AccountSummary:
    account_number: str
    balance: float  # negative = credit
    payment_status: str | None


@dataclass(frozen=True)
class StatementRef:
    issue_date: date
    amount: float
    due_date: date


def _money(value: str) -> float:
    cleaned = re.sub(r"[^\d.\-]", "", value)
    if cleaned in ("", "-", "."):
        raise PortalParseError(f"not a money value: {value!r}")
    return float(cleaned)


def _date(value: str) -> date:
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise PortalParseError(f"not a date: {value!r}")


def _cells(row: Tag) -> list[str]:
    return [" ".join(td.get_text(" ", strip=True).split()) for td in row.find_all("td")]


def _table(html: str, table_id: str, what: str) -> Tag:
    table = BeautifulSoup(html, "html.parser").find("table", id=table_id)
    if table is None:
        raise PortalParseError(f"{what}: table #{table_id} not found")
    return table


def parse_summary(html: str) -> AccountSummary:
    rows = _table(html, "table_data", "account summary").select("tbody tr")
    if not rows:
        raise PortalParseError("account summary: no account rows")
    if len(rows) > 1:
        _LOGGER.info("Portal lists %d linked accounts; using the first", len(rows))
    tds = rows[0].find_all("td")
    if len(tds) < 3 or tds[0].find("h1") is None:
        raise PortalParseError("account summary: unexpected row layout")
    status = " ".join(tds[1].get_text(" ", strip=True).split())
    return AccountSummary(
        account_number=tds[0].find("h1").get_text(strip=True),
        balance=_money(tds[2].get_text(" ", strip=True)),
        payment_status=status or None,
    )


def parse_statement_list(html: str) -> list[StatementRef]:
    refs: list[StatementRef] = []
    for row in _table(html, "table_data", "statement history").select("tbody tr"):
        cells = _cells(row)
        if len(cells) < 3:
            continue  # DataTables "No data available" placeholder row
        refs.append(StatementRef(issue_date=_date(cells[0]), amount=_money(cells[1]), due_date=_date(cells[2])))
    return refs
