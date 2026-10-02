"""Tests for the portal HTTP client."""
from datetime import date

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from custom_components.bwwb_water.client import (
    CAWAuthError,
    CAWClient,
    CAWConnectionError,
    CAWPortalChangedError,
)

from .conftest import load_fixture

BASE = "https://schedulepayment.com/alabama"
LOGIN = load_fixture("login_page.html")
SUMMARY = load_fixture("account_summary.html")
PDF_URL = f"{BASE}/Account/ViewStatement?AccountNumber=999900001111&StatementDate=09%2F02%2F2026"


def html(mock, method, url, body, status=200):
    getattr(mock, method)(url, status=status, body=body, content_type="text/html")


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def test_login_posts_token_and_credentials(session):
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        await CAWClient(session, "TESTUSER", "pw").login()
        sent = m.requests[("POST", URL(f"{BASE}/Home/Login"))][0].kwargs["data"]
    assert sent == {"__RequestVerificationToken": "TOKEN123", "UserName": "TESTUSER", "Password": "pw"}


async def test_login_rejected(session):
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", LOGIN)
        with pytest.raises(CAWAuthError):
            await CAWClient(session, "TESTUSER", "bad").login()


async def test_login_page_without_token(session):
    with aioresponses() as m:
        html(m, "get", BASE, "<html>Just a moment...</html>")
        with pytest.raises(CAWPortalChangedError):
            await CAWClient(session, "TESTUSER", "pw").login()


async def test_http_error_is_connection_error(session):
    with aioresponses() as m:
        html(m, "get", BASE, "oops", status=503)
        with pytest.raises(CAWConnectionError):
            await CAWClient(session, "TESTUSER", "pw").login()


async def test_network_failure_is_connection_error(session):
    with aioresponses() as m:
        m.get(BASE, exception=aiohttp.ClientConnectionError("down"))
        with pytest.raises(CAWConnectionError):
            await CAWClient(session, "TESTUSER", "pw").login()


async def test_get_summary_logs_in_first(session):
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        html(m, "get", f"{BASE}/Account/AccountSummary", SUMMARY)
        assert await CAWClient(session, "TESTUSER", "pw").get_summary_html() == SUMMARY


async def test_expired_session_relogs_once(session):
    client = CAWClient(session, "TESTUSER", "pw")
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        await client.login()
        html(m, "get", f"{BASE}/Billing/SingleAccountStatementHistory", LOGIN)  # expired
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        html(m, "get", f"{BASE}/Billing/SingleAccountStatementHistory", "<a href='/alabama/Home/Logout'>x</a>")
        assert "Logout" in await client.get_statement_history_html()


async def test_expired_session_twice_is_auth_error(session):
    client = CAWClient(session, "TESTUSER", "pw")
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        await client.login()
        html(m, "get", f"{BASE}/Payment/SingleAccountPaymentSummary", LOGIN)
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        html(m, "get", f"{BASE}/Payment/SingleAccountPaymentSummary", LOGIN)
        with pytest.raises(CAWAuthError):
            await client.get_payments_html()


async def test_get_statement_pdf(session):
    client = CAWClient(session, "TESTUSER", "pw")
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        m.get(PDF_URL, body=b"%PDF-1.7 data", content_type="application/pdf")
        assert await client.get_statement_pdf("999900001111", date(2026, 9, 2)) == b"%PDF-1.7 data"


async def test_statement_that_is_not_a_pdf(session):
    client = CAWClient(session, "TESTUSER", "pw")
    with aioresponses() as m:
        html(m, "get", BASE, LOGIN)
        html(m, "post", f"{BASE}/Home/Login", SUMMARY)
        m.get(PDF_URL, body=b"{}", content_type="application/json")
        with pytest.raises(CAWPortalChangedError):
            await client.get_statement_pdf("999900001111", date(2026, 9, 2))
