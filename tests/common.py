"""Shared test data."""

from datetime import date, timedelta

from custom_components.bwwb_water.client import CAWConnectionError
from custom_components.bwwb_water.parsers import Bill, LineItem

from .conftest import load_fixture


def make_bill(
    issue: date, present: float, cons: float, water: float = 43.97, sewer: float = 56.02
) -> Bill:
    return Bill(
        invoice_date=issue,
        due_date=issue + timedelta(days=14),
        total_due=round(water + sewer, 2),
        period_start=issue - timedelta(days=28),
        period_end=issue,
        meter_number="000000001",
        previous_read=present - cons,
        previous_read_date=issue - timedelta(days=29),
        present_read=present,
        present_read_date=issue,
        consumption=cons,
        water_charges=water,
        sewer_charges=sewer,
        water_line_items=(LineItem("Base Service Charge", 28.49),),
        sewer_line_items=(LineItem("Base Service Charge", 26.80),),
    )


BILLS = {
    date(2026, 7, 2): make_bill(date(2026, 7, 2), 930, 5),
    date(2026, 8, 4): make_bill(date(2026, 8, 4), 935, 5),
    date(2026, 9, 2): make_bill(date(2026, 9, 2), 939, 4),
}


class FakeClient:
    """Stands in for CAWClient: serves fixture HTML and marker 'PDFs'."""

    def __init__(
        self, *args, fail_pdfs: set[date] | None = None, summary_error: Exception | None = None
    ):
        self.fail_pdfs = fail_pdfs or set()
        self.summary_error = summary_error
        self.pdf_calls: list[date] = []

    async def get_summary_html(self) -> str:
        if self.summary_error:
            raise self.summary_error
        return load_fixture("account_summary.html")

    async def get_statement_history_html(self) -> str:
        return load_fixture("statement_history.html")

    async def get_payments_html(self) -> str:
        return load_fixture("payment_summary.html")

    async def get_statement_pdf(self, account_number: str, statement_date: date) -> bytes:
        self.pdf_calls.append(statement_date)
        if statement_date in self.fail_pdfs:
            raise CAWConnectionError("boom")
        return b"%PDF-" + statement_date.isoformat().encode()


def fake_parse_bill_pdf(data: bytes) -> Bill:
    return BILLS[date.fromisoformat(data[5:].decode())]
