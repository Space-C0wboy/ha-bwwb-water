"""Tests for the pure portal parsers."""
from datetime import date

import pytest

from custom_components.bwwb_water.parsers import (
    AccountSummary,
    PortalParseError,
    StatementRef,
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
