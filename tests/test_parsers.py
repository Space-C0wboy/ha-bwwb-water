"""Tests for the pure portal parsers."""
from datetime import date

import pytest

from custom_components.bwwb_water.parsers import (
    AccountSummary,
    Bill,
    CompletedPayment,
    LineItem,
    Payments,
    PortalParseError,
    ScheduledPayment,
    StatementRef,
    _money,
    bill_from_dict,
    bill_to_dict,
    parse_bill_pdf,
    parse_bill_text,
    parse_payments,
    parse_statement_list,
    parse_summary,
)

from .conftest import load_fixture


def test_parse_summary():
    assert parse_summary(load_fixture("account_summary.html")) == AccountSummary(
        account_number="999900001111",
        balance=-99.99,
        payment_status="Auto Draft Compl 09/22/2026",
    )


def test_parse_summary_missing_table():
    with pytest.raises(PortalParseError):
        parse_summary("<html><body>nothing</body></html>")


def test_parse_statement_list():
    assert parse_statement_list(load_fixture("statement_history.html")) == [
        StatementRef(issue_date=date(2026, 9, 2), amount=99.99, due_date=date(2026, 9, 16)),
        StatementRef(issue_date=date(2026, 8, 4), amount=0.0, due_date=date(2026, 8, 24)),
        StatementRef(issue_date=date(2026, 7, 2), amount=0.0, due_date=date(2026, 7, 22)),
    ]


def test_parse_statement_list_skips_placeholder_row():
    html = (
        '<table id="table_data"><tbody>'
        '<tr><td colspan="4">No data available in table</td></tr>'
        "</tbody></table>"
    )
    assert parse_statement_list(html) == []


def test_parse_payments():
    assert parse_payments(load_fixture("payment_summary.html")) == Payments(
        scheduled=(
            ScheduledPayment(
                type="Auto Draft",
                scheduled=None,
                amount=None,
                funding_source="CARD_4242",
                status="Waiting for bill",
            ),
        ),
        completed=(
            CompletedPayment(
                type="One-Time",
                payment_date=date(2026, 9, 22),
                amount=102.99,
                funding_source="CARD_4242",
                status="Complete",
                confirmation="CW00TEST2",
            ),
            CompletedPayment(
                type="One-Time",
                payment_date=date(2026, 9, 16),
                amount=102.99,
                funding_source="CARD_4242",
                status="Complete",
                confirmation="CW00TEST1",
            ),
        ),
    )


def test_parse_payments_empty_tables():
    html = '<table id="table_data"><tbody></tbody></table><table id="table_data2"><tbody></tbody></table>'
    assert parse_payments(html) == Payments(scheduled=(), completed=())


def test_parse_payments_missing_tables():
    with pytest.raises(PortalParseError):
        parse_payments("<html></html>")


def test_money_rejects_malformed():
    with pytest.raises(PortalParseError):
        _money("1.2.3")


def test_parse_bill_caw():
    assert parse_bill_text(load_fixture("bill_caw.txt")) == Bill(
        invoice_date=date(2026, 9, 2),
        due_date=date(2026, 9, 16),
        total_due=99.99,
        period_start=date(2026, 8, 5),
        period_end=date(2026, 9, 2),
        meter_number="000000001",
        previous_read=935.0,
        previous_read_date=date(2026, 8, 4),
        present_read=939.0,
        present_read_date=date(2026, 9, 2),
        consumption=4.0,
        water_charges=43.97,
        sewer_charges=56.02,
        water_line_items=(
            LineItem("Base Service Charge", 28.49),
            LineItem("Tier 1 (1 - 3) 3.00 CCF @ $2.96", 8.88),
            LineItem("Tier 2 (4 - 12) 1.00 CCF @ $4.91", 4.91),
            LineItem("Alabama 4 % Utility Tax", 1.69),
        ),
        sewer_line_items=(
            LineItem("Base Service Charge", 26.80),
            LineItem("Billed Consumption 4.00 * 0.85 = 3.40", 0.0),
            LineItem("Tier 1 (1 - 3) 3.00 CCF @ $8.07", 24.21),
            LineItem("Tier 2 (4 - 6) 0.40 CCF @ $12.52", 5.01),
        ),
    )


def test_parse_bill_bwwb_branded():
    bill = parse_bill_text(load_fixture("bill_bwwb.txt"))
    assert (bill.invoice_date, bill.due_date, bill.total_due) == (date(2025, 10, 6), date(2025, 10, 20), 26.20)
    assert (bill.previous_read, bill.previous_read_date) == (878.0, date(2025, 9, 26))
    assert (bill.present_read, bill.present_read_date, bill.consumption) == (879.0, date(2025, 10, 2), 1.0)
    assert (bill.water_charges, bill.sewer_charges) == (9.90, 16.30)
    assert bill.water_line_items == (
        LineItem("Base Service Charge", 6.56),
        LineItem("Tier 1 (1 - 1) 1.00 CCF @ $2.96", 2.96),
        LineItem("Alabama 4 % Utility Tax", 0.38),
    )


def test_parse_bill_missing_meter_section():
    with pytest.raises(PortalParseError):
        parse_bill_text("INVOICE DATE: 09/02/2026\nnothing else")


def test_parse_bill_pdf_joins_pages(monkeypatch):
    pages = load_fixture("bill_caw.txt").split("Page 3 of 3")

    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class FakeReader:
        def __init__(self, stream):
            self.pages = [FakePage(pages[0]), FakePage("Page 3 of 3" + pages[1])]

    monkeypatch.setattr("custom_components.bwwb_water.parsers.PdfReader", FakeReader)
    assert parse_bill_pdf(b"%PDF-fake").present_read == 939.0


def test_parse_bill_pdf_rejects_garbage():
    with pytest.raises(PortalParseError):
        parse_bill_pdf(b"not a pdf")


def test_parse_bill_pdf_wraps_non_pypdf_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise KeyError("boom")

    monkeypatch.setattr("custom_components.bwwb_water.parsers.PdfReader", boom)
    with pytest.raises(PortalParseError):
        parse_bill_pdf(b"%PDF")


def test_bill_dict_round_trip():
    bill = parse_bill_text(load_fixture("bill_caw.txt"))
    assert bill_from_dict(bill_to_dict(bill)) == bill


def test_real_pages_parse():
    """Smoke test: the scrubbed live pages from scripts/probe_login.py still parse."""
    summary = parse_summary(load_fixture("real_account_summary.html"))
    assert summary.account_number == "999900001111"
    assert parse_statement_list(load_fixture("real_statement_history.html"))
    parse_payments(load_fixture("real_payment_summary.html"))
