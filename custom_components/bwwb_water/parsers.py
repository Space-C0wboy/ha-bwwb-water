"""Pure parsers for the Central Alabama Water portal (HTML pages + statement PDFs).

No I/O here: every function takes the page body and returns dataclasses, so
each one can be tested against fixture files.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from io import BytesIO

from bs4 import BeautifulSoup, Tag
from pypdf import PdfReader

_LOGGER = logging.getLogger(__name__)
# pypdf warns per embedded font that fontTools is missing; extraction works without it.
logging.getLogger("pypdf").setLevel(logging.ERROR)


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
    try:
        return float(cleaned)
    except ValueError:
        raise PortalParseError(f"not a money value: {value!r}") from None


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
        refs.append(
            StatementRef(
                issue_date=_date(cells[0]), amount=_money(cells[1]), due_date=_date(cells[2])
            )
        )
    return refs


@dataclass(frozen=True)
class ScheduledPayment:
    type: str
    scheduled: str | None
    amount: float | None
    funding_source: str
    status: str


@dataclass(frozen=True)
class CompletedPayment:
    type: str
    payment_date: date
    amount: float
    funding_source: str
    status: str
    confirmation: str | None


@dataclass(frozen=True)
class Payments:
    scheduled: tuple[ScheduledPayment, ...]
    completed: tuple[CompletedPayment, ...]  # newest first


def _optional(value: str) -> str | None:
    return None if value in ("", "--") else value


def parse_payments(html: str) -> Payments:
    scheduled_table = _table(html, "table_data", "scheduled payments")
    completed_table = _table(html, "table_data2", "payment history")

    scheduled = []
    for row in scheduled_table.select("tbody tr"):
        cells = _cells(row)
        if len(cells) < 7:
            continue
        amount = _optional(cells[2])
        scheduled.append(
            ScheduledPayment(
                type=cells[0],
                scheduled=_optional(cells[1]),
                amount=_money(amount) if amount else None,
                funding_source=cells[4],
                status=cells[5],
            )
        )

    completed = []
    for row in completed_table.select("tbody tr"):
        cells = _cells(row)
        if len(cells) < 7:
            continue
        completed.append(
            CompletedPayment(
                type=cells[0],
                payment_date=_date(cells[1]),
                amount=_money(cells[2]),
                funding_source=cells[4],
                status=cells[5],
                confirmation=_optional(cells[6]),
            )
        )
    completed.sort(key=lambda p: p.payment_date, reverse=True)
    return Payments(scheduled=tuple(scheduled), completed=tuple(completed))


@dataclass(frozen=True)
class LineItem:
    description: str
    amount: float


@dataclass(frozen=True)
class Bill:
    invoice_date: date
    due_date: date
    total_due: float
    period_start: date
    period_end: date
    meter_number: str
    previous_read: float  # CCF, cumulative
    previous_read_date: date
    present_read: float  # CCF, cumulative
    present_read_date: date
    consumption: float  # CCF, the bill's CONS column
    water_charges: float
    sewer_charges: float
    water_line_items: tuple[LineItem, ...]
    sewer_line_items: tuple[LineItem, ...]


_LONG_DATE = r"\d{2}/\d{2}/\d{4}"
_SHORT_DATE = r"\d{2}/\d{2}/\d{2}"
_MONEY = r"-?\$\s*-?[\d,]+\.\d{2}"
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_LINE_ITEM = re.compile(r"^(?P<description>.*?\S)\s+(?P<amount>-?[\d,]+\.\d{2})$")
_BILL_DATE_FIELDS = (
    "invoice_date",
    "due_date",
    "period_start",
    "period_end",
    "previous_read_date",
    "present_read_date",
)


def _search(pattern: str, text: str, what: str) -> re.Match[str]:
    match = re.search(pattern, text)
    if match is None:
        raise PortalParseError(f"statement: {what} not found")
    return match


def _number(value: str) -> float:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        raise PortalParseError(f"not a number: {value!r}") from None


def _line_items(text: str, heading: str, total: str) -> tuple[LineItem, ...]:
    section = re.search(rf"{heading}(.*?){re.escape(total)}", text, re.S)
    if section is None:
        raise PortalParseError(f"statement: section {heading!r} not found")
    items = []
    for line in section.group(1).splitlines():
        line = line.strip()
        if not line or re.fullmatch(rf"{_SHORT_DATE}\s*-\s*{_SHORT_DATE}", line):
            continue
        match = _LINE_ITEM.match(line)
        if match:
            items.append(LineItem(match["description"], _money(match["amount"])))
    return tuple(items)


def parse_bill_text(text: str) -> Bill:
    invoice = _search(rf"INVOICE DATE:\s*({_LONG_DATE})", text, "invoice date")
    period = _search(
        rf"SERVICE PERIOD:\s*({_LONG_DATE})\s*-\s*({_LONG_DATE})", text, "service period"
    )
    due = _search(rf"TOTAL AMOUNT DUE by\s*({_LONG_DATE})\s*({_MONEY})", text, "amount due")
    meter = _search(
        rf"CONS\s+(\S+)\s+\S+\s+({_NUMBER})\s+({_SHORT_DATE})\s+({_NUMBER})\s+({_SHORT_DATE})\s+({_NUMBER})",
        text,
        "meter readings",
    )
    water = _search(rf"TOTAL Water Charges\s*({_MONEY})", text, "water charges")
    sewer = _search(rf"TOTAL Sewer Charges\s*({_MONEY})", text, "sewer charges")
    return Bill(
        invoice_date=_date(invoice[1]),
        due_date=_date(due[1]),
        total_due=_money(due[2]),
        period_start=_date(period[1]),
        period_end=_date(period[2]),
        meter_number=meter[1],
        previous_read=_number(meter[2]),
        previous_read_date=_date(meter[3]),
        present_read=_number(meter[4]),
        present_read_date=_date(meter[5]),
        consumption=_number(meter[6]),
        water_charges=_money(water[1]),
        sewer_charges=_money(sewer[1]),
        # Headings are upper case ("CAW WATER CHARGES" / "BWWB WATER CHARGES");
        # the TOTAL lines are mixed case, so the case-sensitive match can't confuse them.
        water_line_items=_line_items(text, r"[A-Z]+ WATER CHARGES", "TOTAL Water Charges"),
        sewer_line_items=_line_items(text, r"[A-Z ]+ SEWER CHARGES", "TOTAL Sewer Charges"),
    )


def parse_bill_pdf(data: bytes) -> Bill:
    try:
        reader = PdfReader(BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as err:  # pypdf raises many non-PyPdfError types on corrupt input
        raise PortalParseError(f"statement is not a readable PDF: {err}") from err
    try:
        return parse_bill_text(text)
    except PortalParseError:
        raise
    except Exception as err:  # one odd statement must not fail the whole poll
        raise PortalParseError(f"statement could not be parsed: {err!r}") from err


def bill_to_dict(bill: Bill) -> dict:
    data = asdict(bill)
    for name in _BILL_DATE_FIELDS:
        data[name] = data[name].isoformat()
    return data


def bill_from_dict(data: dict) -> Bill:
    values = dict(data)
    for name in _BILL_DATE_FIELDS:
        values[name] = date.fromisoformat(values[name])
    values["water_line_items"] = tuple(LineItem(**item) for item in data["water_line_items"])
    values["sewer_line_items"] = tuple(LineItem(**item) for item in data["sewer_line_items"])
    return Bill(**values)
