# Central Alabama Water Portal Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead Playwright/OData data path with a direct aiohttp scraper of the new Central Alabama Water portal (`schedulepayment.com/alabama`), 12 coordinator-backed sensors, and Energy-dashboard external statistics with real billed cost — shipped as `bwwb_water` v2.0.0.

**Architecture:** `client.py` does HTTP only (login with anti-forgery token, re-login on session expiry). `parsers.py` holds pure functions turning HTML/PDF into frozen dataclasses. `coordinator.py` polls every 6h, caches parsed bills in an HA `Store`, and hands bills to `statistics.py`, which imports external statistics dated to each meter read. `sensor.py` renders `CoordinatorEntity` sensors from the coordinator's `CAWData`.

**Tech Stack:** Python 3.14, Home Assistant ≥ 2025.11, aiohttp (from HA), `beautifulsoup4` (html.parser backend), `pypdf`; tests with `pytest-homeassistant-custom-component` + `aioresponses`, run in a `python:3.14` Docker container.

**Spec:** `docs/superpowers/specs/2026-10-01-caw-portal-redesign-design.md`

## Global Constraints

- Domain stays `bwwb_water`. Keep the existing unique_id suffixes: `_water_meter`, `_water_meter_ccf`, `_last_read_date`, `_current_period_ccf`, `_prev_period_ccf`, `_current_balance`, `_last_bill_amount`, `_last_bill_due_date`. Device identifier stays `(DOMAIN, entry.entry_id)`.
- Display name: `Central Alabama Water`.
- Portal base: `https://schedulepayment.com`, portal path `/alabama`.
- Poll interval: 6 hours.
- Statistic IDs: `bwwb_water:water_consumption` (ft³), `bwwb_water:water_cost`.
- `manifest.json` requirements: `beautifulsoup4>=4.12`, `pypdf>=5.0` (ranges, not pins — HA's `scrape` pins bs4 4.13.3). `dependencies: ["recorder"]`. Version `2.0.0`.
- `hacs.json` minimum HA: `2025.11.0` (`StatisticMetaData.unit_class` is required).
- Config entry `VERSION = 2`; v1 → v2 migration drops `auth_service_url`.
- **This repo is public.** Never commit real account numbers, names, addresses, meter numbers, card digits, confirmation numbers or tokens. All fixtures are scrubbed; Task 1 Step 4 is the gate.
- No payment or account-changing requests, ever. The client only issues GET requests plus the login POST.
- Run tests with `scripts/test.sh` (Docker). Local Python is 3.13 and cannot run the HA test harness.

**Spec deviations (decided while planning):**
- The cost statistic uses `unit_of_measurement=None`, matching HA core's Opower integration; the Energy dashboard takes the currency from the HA config. The spec said "USD".
- `parse_bill_pdf` is tested by monkeypatching `pypdf.PdfReader` instead of with a hand-built synthetic PDF. Real-text parsing is covered by fixtures captured with pypdf in Task 1.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/probe_login.py` (new) | One-off: proves the plain-HTTP login works; captures scrubbed real fixtures |
| `scripts/test.sh`, `Dockerfile.test`, `requirements_test.txt`, `pytest.ini` (new) | Test harness in Docker |
| `custom_components/__init__.py` (new, empty) | Lets pytest import `custom_components.bwwb_water` |
| `custom_components/bwwb_water/const.py` (rewrite) | Constants |
| `custom_components/bwwb_water/parsers.py` (new) | Dataclasses + pure HTML/PDF parsers + Store (de)serialization |
| `custom_components/bwwb_water/client.py` (new) | `CAWClient` HTTP + login/session handling |
| `custom_components/bwwb_water/statistics.py` (new) | Build + import external statistics |
| `custom_components/bwwb_water/coordinator.py` (rewrite) | `CAWData`, `CAWCoordinator` |
| `custom_components/bwwb_water/sensor.py` (rewrite) | 12 sensors |
| `custom_components/bwwb_water/__init__.py` (rewrite) | Setup, unload, migration |
| `custom_components/bwwb_water/config_flow.py` (rewrite) | user / reauth / reconfigure |
| `custom_components/bwwb_water/strings.json`, `translations/en.json` (rewrite) | UI strings |
| `custom_components/bwwb_water/manifest.json`, `hacs.json`, `README.md` (modify) | Release metadata + docs |
| `custom_components/bwwb_water/api.py` (delete) | Old sidecar client |
| `auth-service/utility-auth-service.py` (modify) | Remove BWWB endpoint; Southern Company stays |
| `tests/conftest.py`, `tests/common.py`, `tests/fixtures/*` (new) | Shared test setup, bill factory, fixtures |
| `tests/test_parsers.py`, `test_client.py`, `test_statistics.py`, `test_coordinator.py`, `test_sensor.py`, `test_init.py`, `test_config_flow.py` (new) | Tests |

---

### Task 1: Login probe + real fixture capture (OWNER GATE)

**Files:**
- Create: `scripts/probe_login.py`
- Create (by running the script): `tests/fixtures/real_login_page.html`, `real_account_summary.html`, `real_statement_history.html`, `real_payment_summary.html`, `bill_caw.txt`, `bill_bwwb.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: the fixture files above. `bill_caw.txt` is the pypdf text of the 09/02/2026 statement and `bill_bwwb.txt` the pypdf text of the 10/06/2025 statement. Task 4's expected values come from those two statements.

This task needs the repo owner. Only they have the password, and the script prompts for it interactively. **If the login check fails, STOP the plan** and re-plan around the sidecar (spec "Rejected" approach 1).

- [ ] **Step 1: Write the probe script**

```python
"""One-off probe: can a plain HTTP client log in to the Central Alabama Water portal?

Usage (Windows, local Python 3.13 is fine):
    py -3.13 -m pip install --user aiohttp pypdf
    py -3.13 scripts/probe_login.py USERNAME
    py -3.13 scripts/probe_login.py USERNAME --capture --redact "FULL NAME" --redact "STREET ADDRESS" --redact "ZIP+4"

Prompts for the password; it is never stored or printed. Without --capture it
only reports whether login works. With --capture it also writes scrubbed
fixtures to tests/fixtures/ for the parser tests. Review them before committing.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
from io import BytesIO
from pathlib import Path
import re
import sys

import aiohttp
from pypdf import PdfReader

BASE = "https://schedulepayment.com/alabama"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
STATEMENTS = {"bill_caw.txt": "09%2F02%2F2026", "bill_bwwb.txt": "10%2F06%2F2025"}
TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
ACCOUNT_RE = re.compile(r"<h1>\s*(\d{6,})\s*</h1>")


def scrub(text: str, account: str, username: str, redact: list[str]) -> str:
    text = text.replace(account, "999900001111").replace(account[-4:], "1111")
    text = re.sub(re.escape(username), "TESTUSER", text, flags=re.I)
    for value in redact:
        text = re.sub(re.escape(value), "REDACTED", text, flags=re.I)
    text = re.sub(r"\b(VISA|MC|MASTERCARD|AMEX|DISCOVER|CHECKING|SAVINGS)_\d{4}\b", "CARD_4242", text, flags=re.I)
    text = re.sub(r"CW00[A-Z0-9]+", "CW00TEST1", text)
    text = re.sub(r"'\d{9,}'", "'000000000000'", text)
    text = re.sub(r"\b\d{9}\b(?=\s+\d/\d\")", "000000001", text)  # meter number, before size e.g. 5/8"
    text = re.sub(r'(name="__RequestVerificationToken"[^>]*value=")[^"]+', r"\1TOKEN", text)
    return text


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--redact", action="append", default=[])
    args = parser.parse_args()
    password = getpass.getpass("Portal password: ")

    async with aiohttp.ClientSession() as session:
        async with session.get(BASE) as resp:
            login_html = await resp.text()
            print(f"GET login page: HTTP {resp.status}")
        token = TOKEN_RE.search(login_html)
        if token is None:
            print("FAIL: no __RequestVerificationToken on the login page (Cloudflare challenge?)")
            print(login_html[:500])
            return 1
        async with session.post(
            f"{BASE}/Home/Login",
            data={"__RequestVerificationToken": token.group(1), "UserName": args.username, "Password": password},
        ) as resp:
            after = await resp.text()
            print(f"POST login: HTTP {resp.status}, final URL {resp.url}")
        if "/alabama/Home/Logout" not in after:
            print("FAIL: not logged in after POST. Page title / errors:")
            for m in re.finditer(r"<title>(.*?)</title>|validation-summary-errors[^>]*>(.*?)</div>", after, re.S):
                print("  ", " ".join((m.group(1) or m.group(2) or "").split())[:300])
            return 1
        print("OK: plain-HTTP login works.")

        if not args.capture:
            return 0
        account_match = ACCOUNT_RE.search(after)
        if account_match is None:
            print("FAIL: could not find the account number on the summary page")
            return 1
        account = account_match.group(1)
        FIXTURES.mkdir(parents=True, exist_ok=True)

        def write(name: str, text: str) -> None:
            (FIXTURES / name).write_text(scrub(text, account, args.username, args.redact), encoding="utf-8")
            print(f"wrote tests/fixtures/{name}")

        write("real_login_page.html", login_html)
        for name, path in {
            "real_account_summary.html": "/Account/AccountSummary",
            "real_statement_history.html": "/Billing/SingleAccountStatementHistory",
            "real_payment_summary.html": "/Payment/SingleAccountPaymentSummary",
        }.items():
            async with session.get(BASE + path) as resp:
                write(name, await resp.text())
        for name, stamp in STATEMENTS.items():
            url = f"{BASE}/Account/ViewStatement?AccountNumber={account}&StatementDate={stamp}"
            async with session.get(url) as resp:
                data = await resp.read()
                print(f"statement {stamp}: HTTP {resp.status}, {resp.content_type}, {len(data)} bytes")
            reader = PdfReader(BytesIO(data))
            write(name, "\n".join(page.extract_text() or "" for page in reader.pages))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Owner runs the login check**

Run (owner, in their own terminal): `py -3.13 -m pip install --user aiohttp pypdf` then `py -3.13 scripts/probe_login.py <USERNAME>`
Expected: `OK: plain-HTTP login works.` If it prints `FAIL`, stop the plan and report the output.

- [ ] **Step 3: Owner captures fixtures**

Run: `py -3.13 scripts/probe_login.py <USERNAME> --capture --redact "<FULL NAME>" --redact "<STREET ADDRESS>" --redact "<ZIP+4>"`
Expected: six `wrote tests/fixtures/...` lines and two `statement ...: HTTP 200, application/pdf` lines.

- [ ] **Step 4: PII gate — verify scrubbing**

Run grep with the owner's real values: account number, its last 4 digits, surname, street number + street name, card last 4, meter number (all known to the owner, and visible on the bill). Do not write these values into any file.
`grep -rniE "<acct>|<last4>|<surname>|<street>|<card4>|<meter>" tests/fixtures/`
Expected: no output. Also open each `bill_*.txt` and confirm that the `METER READINGS` row, `TOTAL Water Charges` and `TOTAL Sewer Charges` lines survived scrubbing.

- [ ] **Step 5: Commit**

```bash
git add scripts/probe_login.py tests/fixtures/
git commit -m "test: login probe + scrubbed real portal fixtures"
```

---

### Task 2: Test harness + account summary and statement list parsers

**Files:**
- Create: `Dockerfile.test`, `requirements_test.txt`, `pytest.ini`, `scripts/test.sh`, `custom_components/__init__.py`, `tests/__init__.py`, `tests/conftest.py`
- Create: `tests/fixtures/account_summary.html`, `tests/fixtures/statement_history.html`
- Create: `custom_components/bwwb_water/parsers.py`
- Rewrite: `custom_components/bwwb_water/const.py`
- Test: `tests/test_parsers.py`

**Interfaces:**
- Produces (in `parsers.py`): `PortalParseError(Exception)`; `AccountSummary(account_number: str, balance: float, payment_status: str | None)`; `StatementRef(issue_date: date, amount: float, due_date: date)`; `parse_summary(html: str) -> AccountSummary`; `parse_statement_list(html: str) -> list[StatementRef]`; private helpers `_money(s: str) -> float`, `_date(s: str) -> date`, `_cells(row) -> list[str]`.
- Produces (in `const.py`): `DOMAIN`, `NAME`, `BASE_URL`, `PORTAL_PATH`, `UPDATE_INTERVAL`, `CCF_TO_FT3`, `STAT_CONSUMPTION`, `STAT_COST`, `STORAGE_VERSION`, `CONF_AUTH_SERVICE_URL`.

- [ ] **Step 1: Create the harness files**

`requirements_test.txt`:
```
pytest-homeassistant-custom-component==0.13.368
beautifulsoup4==4.13.3
pypdf==6.19.0
aioresponses==0.7.9
```

`Dockerfile.test`:
```dockerfile
FROM python:3.14
COPY requirements_test.txt /tmp/requirements_test.txt
RUN pip install --no-cache-dir -r /tmp/requirements_test.txt
WORKDIR /src
```

`pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
pythonpath = .
```

`scripts/test.sh`:
```bash
#!/usr/bin/env bash
# Run the test suite in Docker (HA's test harness needs Python 3.14).
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -f Dockerfile.test -t bwwb-water-test . >/dev/null
SRC="$(pwd -W 2>/dev/null || pwd)"
MSYS_NO_PATHCONV=1 docker run --rm -v "$SRC:/src" bwwb-water-test pytest -q "$@"
```

`custom_components/__init__.py` and `tests/__init__.py`: empty files.

`tests/conftest.py`:
```python
"""Shared test fixtures."""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/bwwb_water in every test."""
    yield
```

- [ ] **Step 2: Rewrite `const.py`**

```python
"""Constants for the Central Alabama Water (formerly BWWB) integration."""
from datetime import timedelta

DOMAIN = "bwwb_water"
NAME = "Central Alabama Water"

BASE_URL = "https://schedulepayment.com"
PORTAL_PATH = "/alabama"

# Bills arrive monthly; 6h keeps the balance / payment sensors reasonably fresh.
UPDATE_INTERVAL = timedelta(hours=6)

CCF_TO_FT3 = 100

STAT_CONSUMPTION = f"{DOMAIN}:water_consumption"
STAT_COST = f"{DOMAIN}:water_cost"

STORAGE_VERSION = 1

# v1 config entries stored the sidecar URL; only used by the v1 -> v2 migration.
CONF_AUTH_SERVICE_URL = "auth_service_url"
```

- [ ] **Step 3: Create the hand-written fixtures**

`tests/fixtures/account_summary.html`:
```html
<!DOCTYPE html>
<html><body>
<nav><a href="/alabama/Account/AccountSummary">My Account</a>
<a href="/alabama/Home/Logout">Log Out</a></nav>
<table id="table_data" class="display nowrap custom-table">
<thead><tr><th>Account Number</th><th><span>Payment Status</span></th><th><span>Account Balance</span></th><th>Statement Amount</th><th>Current Bill</th></tr></thead>
<tbody>
<tr>
<td><h1>999900001111</h1><span>123 MAIN ST</span></td>
<td><span><span>Auto Draft </span><br>
   Compl 09/22/2026</span></td>
<td><h1>
   $-99.99
</h1></td>
<td><h1>$99.99</h1><span>Due 09/16/2026</span></td>
<td class="tabid"><a href="/alabama/Account/ViewStatement?AccountNumber=999900001111&amp;StatementDate=09%2F02%2F2026">View</a></td>
</tr>
</tbody></table>
</body></html>
```

`tests/fixtures/statement_history.html`:
```html
<!DOCTYPE html>
<html><body>
<a href="/alabama/Home/Logout">Log Out</a>
<table id="table_data" class="display nowrap custom-table">
<thead><tr><th>Issue Date</th><th>Amount</th><th>Due Date</th><th>Bill Image</th></tr></thead>
<tbody>
<tr><td>9/2/2026</td><td>$ 99.99</td><td>9/16/2026</td>
<td><a class="tabid" href="/alabama/Billing/ViewStatement?AccountNumber=999900001111&amp;StatementDate=09%2F02%2F2026&amp;class=tabid">View</a></td></tr>
<tr><td>8/4/2026</td><td>$ 0.00</td><td>8/24/2026</td>
<td><a class="tabid" href="/alabama/Billing/ViewStatement?AccountNumber=999900001111&amp;StatementDate=08%2F04%2F2026&amp;class=tabid">View</a></td></tr>
<tr><td>7/2/2026</td><td>$ 0.00</td><td>7/22/2026</td>
<td><a class="tabid" href="/alabama/Billing/ViewStatement?AccountNumber=999900001111&amp;StatementDate=07%2F02%2F2026&amp;class=tabid">View</a></td></tr>
</tbody></table>
</body></html>
```

- [ ] **Step 4: Write the failing tests**

`tests/test_parsers.py`:
```python
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
```

- [ ] **Step 5: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_parsers.py`
Expected: FAIL / collection error, `ModuleNotFoundError: No module named 'custom_components.bwwb_water.parsers'`. The first run also builds the Docker image, which takes a few minutes.

- [ ] **Step 6: Implement `parsers.py`**

```python
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
```

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_parsers.py`
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add Dockerfile.test requirements_test.txt pytest.ini scripts/test.sh custom_components/__init__.py tests/ custom_components/bwwb_water/const.py custom_components/bwwb_water/parsers.py
git commit -m "feat: test harness + account summary / statement list parsers"
```

---

### Task 3: Payments parser

**Files:**
- Create: `tests/fixtures/payment_summary.html`
- Modify: `custom_components/bwwb_water/parsers.py` (append)
- Test: `tests/test_parsers.py` (append)

**Interfaces:**
- Consumes: `_cells`, `_money`, `_date`, `_table`, `PortalParseError` from Task 2.
- Produces: `ScheduledPayment(type: str, scheduled: str | None, amount: float | None, funding_source: str, status: str)`; `CompletedPayment(type: str, payment_date: date, amount: float, funding_source: str, status: str, confirmation: str | None)`; `Payments(scheduled: tuple[ScheduledPayment, ...], completed: tuple[CompletedPayment, ...])`, with `completed` sorted newest first; `parse_payments(html: str) -> Payments`.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/payment_summary.html`:
```html
<!DOCTYPE html>
<html><body>
<a href="/alabama/Home/Logout">Log Out</a>
<table id="table_data" class="display nowrap custom-table">
<thead><tr><th>Payment Type</th><th>Scheduled</th><th>Amount</th><th>Nickname</th><th>Funding Source</th><th>Status</th><th>Confirmation #</th><th>Edit</th></tr></thead>
<tbody>
<tr>
<td><span class="auto_draft">Auto Draft </span></td>
<td><span>--</span></td>
<td><span>--</span></td>
<td>Test User</td>
<td>CARD_4242</td>
<td><span>Waiting for bill</span></td>
<td><span>--</span></td>
<td class="cancel"><span><a href="javascript:CancelAutoDraft()" class="cancel">Cancel</a></span></td>
</tr>
</tbody></table>
<table id="table_data2" class="display nowrap custom-table">
<thead><tr><th>Payment Type</th><th>Payment Date</th><th>Amount</th><th>Nickname</th><th>Funding Source</th><th>Status</th><th>Confirmation #</th></tr></thead>
<tbody>
<tr><td><span>One-Time</span></td><td>
09/16/2026            </td><td>$102.99</td><td>Test User</td><td>CARD_4242</td><td>Complete</td>
<td class="blue_text"><a href="javascript:onPaymentDetailsClick('CW00TEST1')">CW00TEST1</a></td></tr>
<tr><td><span>One-Time</span></td><td>
09/22/2026            </td><td>$102.99</td><td>Test User</td><td>CARD_4242</td><td>Complete</td>
<td class="blue_text"><a href="javascript:onPaymentDetailsClick('CW00TEST2')">CW00TEST2</a></td></tr>
</tbody></table>
</body></html>
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_parsers.py`; add `CompletedPayment, Payments, ScheduledPayment, parse_payments` to the import list)

```python
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
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_parsers.py`
Expected: ImportError for `parse_payments`.

- [ ] **Step 4: Implement** (append to `parsers.py`)

```python
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
```

The nickname column (index 3) holds the account holder's name. It is deliberately never read.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_parsers.py`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/payment_summary.html tests/test_parsers.py custom_components/bwwb_water/parsers.py
git commit -m "feat: payments parser"
```

---

### Task 4: Statement (bill) parser + Store serialization + real-fixture smoke tests

**Files:**
- Modify: `custom_components/bwwb_water/parsers.py` (append)
- Test: `tests/test_parsers.py` (append)

**Interfaces:**
- Consumes: Task 1 fixtures `bill_caw.txt`, `bill_bwwb.txt`, `real_*.html`; Task 2/3 helpers.
- Produces: `LineItem(description: str, amount: float)`; `Bill(invoice_date: date, due_date: date, total_due: float, period_start: date, period_end: date, meter_number: str, previous_read: float, previous_read_date: date, present_read: float, present_read_date: date, consumption: float, water_charges: float, sewer_charges: float, water_line_items: tuple[LineItem, ...], sewer_line_items: tuple[LineItem, ...])`; `parse_bill_text(text: str) -> Bill`; `parse_bill_pdf(data: bytes) -> Bill`; `bill_to_dict(bill: Bill) -> dict`; `bill_from_dict(data: dict) -> Bill`.

Expected values below come from the two pinned statements. The pdf.js extraction of them looked like this; pypdf spacing may differ, and the regexes allow for that:
```
INVOICE DATE:   09/02/2026
SERVICE PERIOD:   08/05/2026 - 09/02/2026
TOTAL AMOUNT DUE by 09/16/2026   $99.99
METER NO   SZ   PREVIOUS READ   PRESENT READ   CONS
000000001   5/8"   935 08/04/26   939 09/02/26   4
CAW WATER CHARGES
08/05/26 - 09/02/26
Base Service Charge   28.49
Tier 1 (1 - 3) 3.00 CCF @ $2.96   8.88
Tier 2 (4 - 12) 1.00 CCF @ $4.91   4.91
Alabama 4 % Utility Tax   1.69
TOTAL Water Charges   $43.97
JEFFERSON COUNTY SEWER CHARGES
08/05/26 - 09/02/26
Base Service Charge   26.80
Billed Consumption 4.00 * 0.85 = 3.40   0.00
Tier 1 (1 - 3) 3.00 CCF @ $8.07   24.21
Tier 2 (4 - 6) 0.40 CCF @ $12.52   5.01
TOTAL Sewer Charges   $56.02
```
The 10/06/2025 BWWB bill uses `BWWB WATER CHARGES` as its water heading. Page 3 also lists payment and deposit lines **before** the water heading, and those must not leak into the line items.

- [ ] **Step 1: Write the failing tests** (append; extend the imports with `Bill, LineItem, bill_from_dict, bill_to_dict, parse_bill_pdf, parse_bill_text`)

```python
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


def test_bill_dict_round_trip():
    bill = parse_bill_text(load_fixture("bill_caw.txt"))
    assert bill_from_dict(bill_to_dict(bill)) == bill


def test_real_pages_parse():
    """Smoke test: the scrubbed live pages from scripts/probe_login.py still parse."""
    summary = parse_summary(load_fixture("real_account_summary.html"))
    assert summary.account_number == "999900001111"
    assert parse_statement_list(load_fixture("real_statement_history.html"))
    parse_payments(load_fixture("real_payment_summary.html"))
```

Note for the implementer: `test_parse_bill_pdf_joins_pages` splits on `Page 3 of 3`. If the captured `bill_caw.txt` doesn't contain that marker, split on any substring that appears once in the file instead.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_parsers.py`
Expected: ImportError for `Bill`.

- [ ] **Step 3: Implement** (append to `parsers.py`; add `from dataclasses import asdict` and `from io import BytesIO`, `from pypdf import PdfReader`, `from pypdf.errors import PyPdfError` to the imports at the top)

```python
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
_NUMBER = r"[\d,]+(?:\.\d+)?"
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
    return float(value.replace(",", ""))


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
    period = _search(rf"SERVICE PERIOD:\s*({_LONG_DATE})\s*-\s*({_LONG_DATE})", text, "service period")
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
    except PyPdfError as err:
        raise PortalParseError(f"statement is not a readable PDF: {err}") from err
    return parse_bill_text(text)


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
```

- [ ] **Step 4: Run the tests and make them pass**

Run: `scripts/test.sh tests/test_parsers.py`
Expected: 14 passed. If a bill test fails only because pypdf's layout differs from the pdf.js sample (for example, line items run together on one line), adjust the regexes in `parse_bill_text` / `_line_items`. Never change the expected values: they are what's printed on the bills. If `PdfReader(b"not a pdf")` raises something other than `PyPdfError`, catch that exception class too.

- [ ] **Step 5: Commit**

```bash
git add tests/test_parsers.py custom_components/bwwb_water/parsers.py
git commit -m "feat: statement PDF parser with line items + store serialization"
```

---

### Task 5: Portal HTTP client

**Files:**
- Create: `custom_components/bwwb_water/client.py`
- Create: `tests/fixtures/login_page.html`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `BASE_URL`, `PORTAL_PATH` from `const.py`.
- Produces: `CAWError(Exception)`, with subclasses `CAWAuthError`, `CAWConnectionError` and `CAWPortalChangedError`; `CAWClient(session: aiohttp.ClientSession, username: str, password: str, base_url: str = BASE_URL)` with `async login() -> None`, `async get_summary_html() -> str`, `async get_statement_history_html() -> str`, `async get_payments_html() -> str`, `async get_statement_pdf(account_number: str, statement_date: date) -> bytes`.

Session detection rule: any `text/html` response **without** the `/alabama/Home/Logout` link counts as logged out.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/login_page.html`:
```html
<!DOCTYPE html>
<html><body>
<form action="/alabama/Home/Login" method="post">
<input name="__RequestVerificationToken" type="hidden" value="TOKEN123">
<input id="UserName" name="UserName" type="text">
<input id="Password" name="Password" type="password">
<button type="submit">Sign In</button>
</form>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_client.py`:
```python
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
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_client.py`
Expected: ImportError for `custom_components.bwwb_water.client`.

- [ ] **Step 4: Implement `client.py`**

```python
"""HTTP client for the Central Alabama Water customer portal (schedulepayment.com).

HTTP only, no parsing beyond finding the login form token. The portal is a
server-rendered ASP.NET MVC site: GET the login page for the anti-forgery
token + cookie, POST the form, then GET pages with the session cookie.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import aiohttp
from bs4 import BeautifulSoup
from yarl import URL

from .const import BASE_URL, PORTAL_PATH

TIMEOUT = aiohttp.ClientTimeout(total=60)
LOGGED_IN_MARKER = f"{PORTAL_PATH}/Home/Logout"


class CAWError(Exception):
    """Base error for the portal client."""


class CAWAuthError(CAWError):
    """The portal rejected the credentials."""


class CAWConnectionError(CAWError):
    """The portal could not be reached or returned an HTTP error."""


class CAWPortalChangedError(CAWError):
    """The portal responded in a shape this client does not understand."""


@dataclass(frozen=True)
class _Response:
    body: bytes
    content_type: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def is_logged_out_page(self) -> bool:
        return self.content_type == "text/html" and LOGGED_IN_MARKER not in self.text


class CAWClient:
    """Logs in to the portal and fetches pages/statements with a cookie session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = BASE_URL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url
        self._logged_in = False

    async def _request(self, method: str, path: str, **kwargs) -> _Response:
        url = URL(self._base_url + path, encoded=True)
        try:
            async with self._session.request(method, url, timeout=TIMEOUT, **kwargs) as resp:
                body = await resp.read()
                if resp.status >= 400:
                    raise CAWConnectionError(f"{method} {path} returned HTTP {resp.status}")
                return _Response(body=body, content_type=resp.content_type)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CAWConnectionError(f"{method} {path} failed: {err}") from err

    async def login(self) -> None:
        page = await self._request("GET", PORTAL_PATH)
        field = BeautifulSoup(page.text, "html.parser").find(
            "input", attrs={"name": "__RequestVerificationToken"}
        )
        if field is None or not field.get("value"):
            raise CAWPortalChangedError("login form not found on the portal home page")
        result = await self._request(
            "POST",
            f"{PORTAL_PATH}/Home/Login",
            data={
                "__RequestVerificationToken": field["value"],
                "UserName": self._username,
                "Password": self._password,
            },
        )
        if LOGGED_IN_MARKER in result.text:
            self._logged_in = True
            return
        if 'name="UserName"' in result.text:
            raise CAWAuthError("the portal rejected the username or password")
        raise CAWPortalChangedError("unexpected page after login")

    async def _get(self, path: str) -> _Response:
        if not self._logged_in:
            await self.login()
        resp = await self._request("GET", path)
        if resp.is_logged_out_page:
            self._logged_in = False
            await self.login()
            resp = await self._request("GET", path)
            if resp.is_logged_out_page:
                raise CAWAuthError("the portal ended the session right after logging in")
        return resp

    async def get_summary_html(self) -> str:
        return (await self._get(f"{PORTAL_PATH}/Account/AccountSummary")).text

    async def get_statement_history_html(self) -> str:
        return (await self._get(f"{PORTAL_PATH}/Billing/SingleAccountStatementHistory")).text

    async def get_payments_html(self) -> str:
        return (await self._get(f"{PORTAL_PATH}/Payment/SingleAccountPaymentSummary")).text

    async def get_statement_pdf(self, account_number: str, statement_date: date) -> bytes:
        stamp = f"{statement_date:%m}%2F{statement_date:%d}%2F{statement_date:%Y}"
        resp = await self._get(
            f"{PORTAL_PATH}/Account/ViewStatement?AccountNumber={account_number}&StatementDate={stamp}"
        )
        if resp.content_type != "application/pdf":
            raise CAWPortalChangedError(f"statement {statement_date} came back as {resp.content_type}")
        return resp.body
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_client.py`
Expected: 10 passed. If aioresponses fails to match `PDF_URL` because of `%2F` normalization, register the mock with `URL(PDF_URL, encoded=True)` instead of the string.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bwwb_water/client.py tests/test_client.py tests/fixtures/login_page.html
git commit -m "feat: direct portal client with session re-login"
```

---

### Task 6: External statistics

**Files:**
- Create: `custom_components/bwwb_water/statistics.py`
- Create: `tests/common.py`
- Test: `tests/test_statistics.py`

**Interfaces:**
- Consumes: `Bill`, `LineItem` (Task 4); `STAT_CONSUMPTION`, `STAT_COST`, `CCF_TO_FT3`, `DOMAIN`, `NAME` (Task 2).
- Produces: `CONSUMPTION_METADATA: StatisticMetaData`, `COST_METADATA: StatisticMetaData`, `build_statistics(bills: list[Bill], tz: tzinfo) -> tuple[list[StatisticData], list[StatisticData]]`, `@callback async_import_bill_statistics(hass: HomeAssistant, bills: list[Bill]) -> None`.
- Produces (`tests/common.py`): `make_bill(issue: date, present: float, cons: float, water: float = 43.97, sewer: float = 56.02) -> Bill`, `BILLS: dict[date, Bill]` with keys 2026-07-02, 2026-08-04 and 2026-09-02.

- [ ] **Step 1: Write the test helpers**

`tests/common.py`:
```python
"""Shared test data."""
from datetime import date, timedelta

from custom_components.bwwb_water.parsers import Bill, LineItem


def make_bill(issue: date, present: float, cons: float, water: float = 43.97, sewer: float = 56.02) -> Bill:
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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_statistics.py`:
```python
"""Tests for external statistics building/import."""
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from custom_components.bwwb_water.statistics import (
    async_import_bill_statistics,
    build_statistics,
)

from .common import BILLS, make_bill

TZ = ZoneInfo("America/Chicago")


def midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ)


def test_consumption_rows_baseline_and_running_sum():
    consumption, _ = build_statistics(list(BILLS.values())[::-1], TZ)  # unsorted input
    assert [(r["start"], r["state"], r["sum"]) for r in consumption] == [
        (midnight(date(2026, 6, 3)), 92500, 0.0),  # baseline: oldest bill's previous read
        (midnight(date(2026, 7, 2)), 93000, 500.0),
        (midnight(date(2026, 8, 4)), 93500, 1000.0),
        (midnight(date(2026, 9, 2)), 93900, 1400.0),
    ]


def test_cost_rows_sum_water_plus_sewer():
    _, cost = build_statistics(list(BILLS.values()), TZ)
    assert [(r["start"], r["sum"]) for r in cost] == [
        (midnight(date(2026, 6, 3)), 0.0),
        (midnight(date(2026, 7, 2)), 99.99),
        (midnight(date(2026, 8, 4)), 199.98),
        (midnight(date(2026, 9, 2)), 299.97),
    ]


def test_sum_uses_cons_not_read_difference():
    bills = [make_bill(date(2026, 7, 2), 930, 5), make_bill(date(2026, 8, 4), 3, 3)]  # meter swapped
    consumption, _ = build_statistics(bills, TZ)
    assert [r["sum"] for r in consumption] == [0.0, 500.0, 800.0]


def test_build_is_deterministic():
    assert build_statistics(list(BILLS.values()), TZ) == build_statistics(list(BILLS.values()), TZ)


async def test_import_calls_recorder(hass):
    with patch(
        "custom_components.bwwb_water.statistics.async_add_external_statistics"
    ) as add:
        async_import_bill_statistics(hass, list(BILLS.values()))
    assert [c.args[1]["statistic_id"] for c in add.call_args_list] == [
        "bwwb_water:water_consumption",
        "bwwb_water:water_cost",
    ]
    assert len(add.call_args_list[0].args[2]) == 4
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_statistics.py`
Expected: ImportError for `custom_components.bwwb_water.statistics`.

- [ ] **Step 4: Implement `statistics.py`**

```python
"""External statistics for the Energy dashboard, one point per bill at its meter-read date."""
from __future__ import annotations

from datetime import date, datetime, time, tzinfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .const import CCF_TO_FT3, DOMAIN, NAME, STAT_CONSUMPTION, STAT_COST
from .parsers import Bill

CONSUMPTION_METADATA = StatisticMetaData(
    mean_type=StatisticMeanType.NONE,
    has_sum=True,
    name=f"{NAME} consumption",
    source=DOMAIN,
    statistic_id=STAT_CONSUMPTION,
    unit_class=VolumeConverter.UNIT_CLASS,
    unit_of_measurement=UnitOfVolume.CUBIC_FEET,
)
COST_METADATA = StatisticMetaData(
    mean_type=StatisticMeanType.NONE,
    has_sum=True,
    name=f"{NAME} cost",
    source=DOMAIN,
    statistic_id=STAT_COST,
    unit_class=None,
    unit_of_measurement=None,
)


def build_statistics(
    bills: list[Bill], tz: tzinfo
) -> tuple[list[StatisticData], list[StatisticData]]:
    """Rebuild both series from scratch. Same start times every run, so re-imports overwrite."""
    ordered = sorted(bills, key=lambda b: b.present_read_date)

    def start(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=tz)

    first = ordered[0]
    consumption = [
        StatisticData(start=start(first.previous_read_date), state=first.previous_read * CCF_TO_FT3, sum=0.0)
    ]
    cost = [StatisticData(start=start(first.previous_read_date), state=0.0, sum=0.0)]
    volume_total = 0.0
    money_total = 0.0
    for bill in ordered:
        # Sum the bill's CONS column, not read differences, so a meter swap can't spike.
        volume_total += bill.consumption * CCF_TO_FT3
        bill_cost = bill.water_charges + bill.sewer_charges
        money_total = round(money_total + bill_cost, 2)
        consumption.append(
            StatisticData(start=start(bill.present_read_date), state=bill.present_read * CCF_TO_FT3, sum=volume_total)
        )
        cost.append(StatisticData(start=start(bill.present_read_date), state=round(bill_cost, 2), sum=money_total))
    return consumption, cost


@callback
def async_import_bill_statistics(hass: HomeAssistant, bills: list[Bill]) -> None:
    consumption, cost = build_statistics(bills, dt_util.get_default_time_zone())
    async_add_external_statistics(hass, CONSUMPTION_METADATA, consumption)
    async_add_external_statistics(hass, COST_METADATA, cost)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_statistics.py`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bwwb_water/statistics.py tests/common.py tests/test_statistics.py
git commit -m "feat: external consumption + cost statistics dated to meter reads"
```

---

### Task 7: Coordinator

**Files:**
- Rewrite: `custom_components/bwwb_water/coordinator.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `CAWClient` + errors (Task 5); parsers + `bill_to_dict`/`bill_from_dict` (Tasks 2–4); `async_import_bill_statistics` (Task 6); `UPDATE_INTERVAL`, `STORAGE_VERSION`, `DOMAIN`.
- Produces: `CAWData(summary: AccountSummary, payments: Payments, bills: list[Bill])` (bills sorted oldest first), with properties `latest -> Bill | None` and `previous -> Bill | None`; `CAWCoordinator(hass, entry: ConfigEntry, client: CAWClient)` with attribute `client`; `type CAWConfigEntry = ConfigEntry[CAWCoordinator]`.
- Produces (`tests/test_coordinator.py`, reused by Tasks 8–9): `FakeClient` and `fake_parse_bill_pdf`. Put both in `tests/common.py` so later tests can import them.

- [ ] **Step 1: Add the fake client to `tests/common.py`** (append; add `from custom_components.bwwb_water.client import CAWConnectionError` and `from .conftest import load_fixture` to its imports)

```python
class FakeClient:
    """Stands in for CAWClient: serves fixture HTML and marker 'PDFs'."""

    def __init__(self, *args, fail_pdfs: set[date] | None = None, summary_error: Exception | None = None):
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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_coordinator.py`:
```python
"""Tests for the data update coordinator."""
from datetime import date
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bwwb_water.client import CAWAuthError, CAWConnectionError
from custom_components.bwwb_water.coordinator import CAWCoordinator

from .common import BILLS, FakeClient, fake_parse_bill_pdf

PARSE = "custom_components.bwwb_water.coordinator.parse_bill_pdf"
IMPORT = "custom_components.bwwb_water.coordinator.async_import_bill_statistics"


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    return entry


async def test_first_update_backfills_all_statements(hass, entry, hass_storage):
    client = FakeClient()
    coordinator = CAWCoordinator(hass, entry, client)
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT) as imp:
        data = await coordinator._async_update_data()
    assert sorted(client.pdf_calls) == sorted(BILLS)
    assert data.bills == [BILLS[d] for d in sorted(BILLS)]
    assert data.latest == BILLS[date(2026, 9, 2)]
    assert data.previous == BILLS[date(2026, 8, 4)]
    assert data.summary.balance == -99.99
    imp.assert_called_once()
    await hass.async_block_till_done()
    assert set(hass_storage[f"bwwb_water.{entry.entry_id}"]["data"]["bills"]) == {
        "2026-07-02", "2026-08-04", "2026-09-02",
    }


async def test_second_update_downloads_nothing_new(hass, entry):
    client = FakeClient()
    coordinator = CAWCoordinator(hass, entry, client)
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT) as imp:
        await coordinator._async_update_data()
        await coordinator._async_update_data()
    assert len(client.pdf_calls) == 3
    imp.assert_called_once()


async def test_bills_reload_from_store(hass, entry):
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT):
        await CAWCoordinator(hass, entry, FakeClient())._async_update_data()
    await hass.async_block_till_done()
    client = FakeClient()
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT) as imp:
        data = await CAWCoordinator(hass, entry, client)._async_update_data()
    assert client.pdf_calls == []
    assert len(data.bills) == 3
    imp.assert_called_once()  # statistics re-imported once per HA start


async def test_failed_pdf_is_skipped_and_retried(hass, entry):
    client = FakeClient(fail_pdfs={date(2026, 8, 4)})
    coordinator = CAWCoordinator(hass, entry, client)
    with patch(PARSE, side_effect=fake_parse_bill_pdf), patch(IMPORT):
        data = await coordinator._async_update_data()
        assert len(data.bills) == 2
        client.fail_pdfs.clear()
        data = await coordinator._async_update_data()
    assert len(data.bills) == 3
    assert client.pdf_calls.count(date(2026, 8, 4)) == 2


async def test_auth_error_triggers_reauth(hass, entry):
    coordinator = CAWCoordinator(hass, entry, FakeClient(summary_error=CAWAuthError("no")))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_connection_error_is_update_failed(hass, entry):
    coordinator = CAWCoordinator(hass, entry, FakeClient(summary_error=CAWConnectionError("down")))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_coordinator.py`
Expected: ImportError. The old `coordinator.py` has no `CAWCoordinator` and imports the deleted-to-be `api` module's names.

- [ ] **Step 4: Rewrite `coordinator.py`**

```python
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

    async def _async_update_data(self) -> CAWData:
        if self._bills is None:
            stored = await self._store.async_load() or {}
            self._bills = {key: bill_from_dict(value) for key, value in stored.get("bills", {}).items()}

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
            self._bills[key] = bill
            added = True

        bills = sorted(self._bills.values(), key=lambda b: b.present_read_date)
        if added:
            await self._store.async_save({"bills": {k: bill_to_dict(b) for k, b in self._bills.items()}})
        if bills and (added or not self._statistics_imported):
            async_import_bill_statistics(self.hass, bills)
            self._statistics_imported = True
        return CAWData(summary=summary, payments=payments, bills=bills)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_coordinator.py`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bwwb_water/coordinator.py tests/common.py tests/test_coordinator.py
git commit -m "feat: coordinator with statement cache and statistics import"
```

---

### Task 8: Integration setup, migration and the 12 sensors

**Files:**
- Rewrite: `custom_components/bwwb_water/__init__.py`, `custom_components/bwwb_water/sensor.py`
- Modify: `custom_components/bwwb_water/manifest.json`
- Delete: `custom_components/bwwb_water/api.py`
- Test: `tests/test_init.py`, `tests/test_sensor.py`

**Interfaces:**
- Consumes: `CAWCoordinator`, `CAWData`, `CAWConfigEntry` (Task 7); `CAWClient` (Task 5); `FakeClient`, `fake_parse_bill_pdf` (tests/common.py).
- Produces: `async_setup_entry`, `async_unload_entry`, `async_migrate_entry` in `__init__.py`; `days_until_due(data: CAWData, today: date) -> int | None` and `SENSORS: tuple[CAWSensorDescription, ...]` in `sensor.py`.

Both test files need the recorder, because the manifest declares `dependencies: ["recorder"]`. `recorder_mock` must be requested before `hass`, so each file overrides the autouse fixture from `conftest.py`.

- [ ] **Step 1: Update `manifest.json`**

```json
{
  "domain": "bwwb_water",
  "name": "Central Alabama Water",
  "version": "2.0.0",
  "documentation": "https://github.com/Space-C0wboy/ha-bwwb-water",
  "issue_tracker": "https://github.com/Space-C0wboy/ha-bwwb-water/issues",
  "requirements": ["beautifulsoup4>=4.12", "pypdf>=5.0"],
  "dependencies": ["recorder"],
  "codeowners": ["@Space-C0wboy"],
  "iot_class": "cloud_polling",
  "config_flow": true,
  "integration_type": "service",
  "icon": "mdi:water-pump"
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_init.py`:
```python
"""Tests for setup, unload and the v1 -> v2 migration."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import FakeClient, fake_parse_bill_pdf


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


@pytest.fixture
def patched():
    with (
        patch("custom_components.bwwb_water.CAWClient", FakeClient),
        patch("custom_components.bwwb_water.coordinator.parse_bill_pdf", side_effect=fake_parse_bill_pdf),
        patch("custom_components.bwwb_water.coordinator.async_import_bill_statistics"),
    ):
        yield


async def test_v1_entry_migrates_and_adopts_account_number(hass, patched):
    entry = MockConfigEntry(
        domain="bwwb_water",
        version=1,
        unique_id="testuser",
        data={"username": "TESTUSER", "password": "pw", "auth_service_url": "http://192.168.1.5:18792/bwwb/data"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.data == {"username": "TESTUSER", "password": "pw"}
    assert entry.unique_id == "999900001111"


async def test_unload(hass, patched):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
```

`tests/test_sensor.py`:
```python
"""Tests for the sensor platform."""
from dataclasses import replace
from datetime import date
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bwwb_water.coordinator import CAWData
from custom_components.bwwb_water.parsers import AccountSummary, Payments
from custom_components.bwwb_water.sensor import days_until_due

from .common import BILLS, FakeClient, fake_parse_bill_pdf


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    with (
        patch("custom_components.bwwb_water.CAWClient", FakeClient),
        patch("custom_components.bwwb_water.coordinator.parse_bill_pdf", side_effect=fake_parse_bill_pdf),
        patch("custom_components.bwwb_water.coordinator.async_import_bill_statistics"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def state(hass, entry, key):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "bwwb_water", f"{entry.entry_id}_{key}")
    assert entity_id, key
    return hass.states.get(entity_id)


async def test_sensor_values(hass, entry):
    numeric = {
        "water_meter": 93900,
        "water_meter_ccf": 939,
        "current_period_ccf": 4,
        "prev_period_ccf": 5,
        "current_balance": -99.99,
        "last_bill_amount": 99.99,
        "water_charges": 43.97,
        "sewer_charges": 56.02,
        "days_until_due": 0,
    }
    text = {
        "last_read_date": "2026-09-02",
        "last_bill_due_date": "2026-09-16",
        "scheduled_payment": "Waiting for bill",
    }
    for key, value in numeric.items():
        assert float(state(hass, entry, key).state) == value, key
    for key, value in text.items():
        assert state(hass, entry, key).state == value, key


async def test_sensor_attributes(hass, entry):
    assert state(hass, entry, "water_meter").attributes["meter_number"] == "000000001"
    assert state(hass, entry, "scheduled_payment").attributes["last_payment_amount"] == 102.99
    assert state(hass, entry, "scheduled_payment").attributes["last_payment_date"] == "2026-09-22"
    assert state(hass, entry, "days_until_due").attributes["paid"] is True
    assert state(hass, entry, "water_charges").attributes["line_items"] == [
        {"description": "Base Service Charge", "amount": 28.49}
    ]
    assert state(hass, entry, "current_balance").attributes["payment_status"] == "Auto Draft Compl 09/22/2026"


def data_with(balance: float, bills=None) -> CAWData:
    bills = [BILLS[d] for d in sorted(BILLS)] if bills is None else bills
    return CAWData(AccountSummary("999900001111", balance, None), Payments((), ()), bills)


@pytest.mark.parametrize(
    ("balance", "today", "expected"),
    [
        (99.99, date(2026, 9, 10), 6),  # due 2026-09-16
        (99.99, date(2026, 9, 16), 0),
        (99.99, date(2026, 9, 20), -4),
        (0.0, date(2026, 9, 10), 0),
        (-5.0, date(2026, 9, 10), 0),
    ],
)
def test_days_until_due(balance, today, expected):
    assert days_until_due(data_with(balance), today) == expected


def test_days_until_due_without_bills():
    assert days_until_due(data_with(10.0, bills=[]), date(2026, 9, 10)) is None
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_init.py tests/test_sensor.py`
Expected: failures. The old `__init__.py` imports `api.BWWBAPI` and has no `async_migrate_entry`.

- [ ] **Step 4: Rewrite `__init__.py`**

```python
"""Central Alabama Water (formerly Birmingham Water Works Board) integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import CAWClient
from .const import CONF_AUTH_SERVICE_URL
from .coordinator import CAWConfigEntry, CAWCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: CAWConfigEntry) -> bool:
    client = CAWClient(
        async_create_clientsession(hass),  # own cookie jar for the portal session
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = CAWCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    # v1 entries were keyed by username; v2 keys by the portal account number.
    account_number = coordinator.data.summary.account_number
    if entry.unique_id != account_number:
        hass.config_entries.async_update_entry(entry, unique_id=account_number)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CAWConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version > 2:
        return False
    if entry.version == 1:
        # v1 talked to a Playwright sidecar; v2 talks to the portal directly.
        data = {k: v for k, v in entry.data.items() if k != CONF_AUTH_SERVICE_URL}
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True
```

- [ ] **Step 5: Rewrite `sensor.py`**

```python
"""Sensors for Central Alabama Water."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CCF_TO_FT3, DOMAIN, NAME
from .coordinator import CAWConfigEntry, CAWCoordinator, CAWData
from .parsers import Bill


def days_until_due(data: CAWData, today: date) -> int | None:
    """Days until the latest bill is due; 0 once nothing is owed."""
    if data.latest is None:
        return None
    if data.summary.balance <= 0:
        return 0
    return (data.latest.due_date - today).days


def _latest(fn: Callable[[Bill], Any]) -> Callable[[CAWData], Any]:
    return lambda data: fn(data.latest) if data.latest else None


def _previous(fn: Callable[[Bill], Any]) -> Callable[[CAWData], Any]:
    return lambda data: fn(data.previous) if data.previous else None


def _period(bill: Bill | None) -> dict[str, Any]:
    if bill is None:
        return {}
    return {"period_start": bill.period_start.isoformat(), "period_end": bill.period_end.isoformat()}


def _charges_attrs(items_of: Callable[[Bill], tuple]) -> Callable[[CAWData], dict[str, Any]]:
    def attrs(data: CAWData) -> dict[str, Any]:
        bill = data.latest
        if bill is None:
            return {}
        return {
            "line_items": [{"description": i.description, "amount": i.amount} for i in items_of(bill)],
            "service_period": f"{bill.period_start.isoformat()} - {bill.period_end.isoformat()}",
        }

    return attrs


def _scheduled_state(data: CAWData) -> str:
    return data.payments.scheduled[0].status if data.payments.scheduled else "None"


def _scheduled_attrs(data: CAWData) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if data.payments.scheduled:
        nxt = data.payments.scheduled[0]
        attrs.update(type=nxt.type, scheduled=nxt.scheduled, amount=nxt.amount, funding_source=nxt.funding_source)
    if data.payments.completed:
        last = data.payments.completed[0]
        attrs.update(
            last_payment_date=last.payment_date.isoformat(),
            last_payment_amount=last.amount,
            last_payment_confirmation=last.confirmation,
        )
    return attrs


@dataclass(frozen=True, kw_only=True)
class CAWSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CAWData], Any]
    attrs_fn: Callable[[CAWData], dict[str, Any]] = lambda data: {}


SENSORS: tuple[CAWSensorDescription, ...] = (
    CAWSensorDescription(
        key="water_meter",
        name="Water Meter",
        icon="mdi:water-pump",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_FEET,
        value_fn=_latest(lambda b: b.present_read * CCF_TO_FT3),
        attrs_fn=lambda d: {"meter_number": d.latest.meter_number} if d.latest else {},
    ),
    CAWSensorDescription(
        key="water_meter_ccf",
        name="Water Meter (CCF)",
        icon="mdi:water-pump",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        value_fn=_latest(lambda b: b.present_read),
    ),
    CAWSensorDescription(
        key="last_read_date",
        name="Last Read Date",
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.DATE,
        value_fn=_latest(lambda b: b.present_read_date),
        attrs_fn=lambda d: {"previous_read_date": d.latest.previous_read_date.isoformat()} if d.latest else {},
    ),
    CAWSensorDescription(
        key="current_period_ccf",
        name="Current Period Usage",
        icon="mdi:water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        value_fn=_latest(lambda b: b.consumption),
        attrs_fn=lambda d: _period(d.latest),
    ),
    CAWSensorDescription(
        key="prev_period_ccf",
        name="Previous Period Usage",
        icon="mdi:water-outline",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CENTUM_CUBIC_FEET,
        value_fn=_previous(lambda b: b.consumption),
        attrs_fn=lambda d: _period(d.previous),
    ),
    CAWSensorDescription(
        key="current_balance",
        name="Current Balance",
        icon="mdi:currency-usd",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=lambda d: d.summary.balance,
        attrs_fn=lambda d: {"payment_status": d.summary.payment_status},
    ),
    CAWSensorDescription(
        key="last_bill_amount",
        name="Last Bill Amount",
        icon="mdi:receipt-text",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=_latest(lambda b: b.total_due),
        attrs_fn=lambda d: (
            {"bill_date": d.latest.invoice_date.isoformat(), "due_date": d.latest.due_date.isoformat()}
            if d.latest
            else {}
        ),
    ),
    CAWSensorDescription(
        key="last_bill_due_date",
        name="Last Bill Due Date",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
        value_fn=_latest(lambda b: b.due_date),
    ),
    CAWSensorDescription(
        key="water_charges",
        name="Water Charges",
        icon="mdi:water-check",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=_latest(lambda b: b.water_charges),
        attrs_fn=_charges_attrs(lambda b: b.water_line_items),
    ),
    CAWSensorDescription(
        key="sewer_charges",
        name="Sewer Charges",
        icon="mdi:pipe",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        value_fn=_latest(lambda b: b.sewer_charges),
        attrs_fn=_charges_attrs(lambda b: b.sewer_line_items),
    ),
    CAWSensorDescription(
        key="scheduled_payment",
        name="Scheduled Payment",
        icon="mdi:calendar-sync",
        value_fn=_scheduled_state,
        attrs_fn=_scheduled_attrs,
    ),
    CAWSensorDescription(
        key="days_until_due",
        name="Days Until Due",
        icon="mdi:calendar-alert",
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda d: days_until_due(d, dt_util.now().date()),
        attrs_fn=lambda d: (
            {"due_date": d.latest.due_date.isoformat(), "paid": d.summary.balance <= 0} if d.latest else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: CAWConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(CAWSensor(coordinator, description) for description in SENSORS)


class CAWSensor(CoordinatorEntity[CAWCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: CAWSensorDescription

    def __init__(self, coordinator: CAWCoordinator, description: CAWSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=NAME,
            manufacturer=NAME,
            model="Water meter",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs_fn(self.coordinator.data) or None
```

- [ ] **Step 6: Delete the old sidecar client**

Run: `git rm custom_components/bwwb_water/api.py`

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_init.py tests/test_sensor.py`
Expected: all pass (2 + 8). If HA rejects `device_class=WATER` with unit `CCF`, check `SensorDeviceClass.WATER` units in the installed HA version. `UnitOfVolume.CENTUM_CUBIC_FEET` has been valid for water since 2023.x.

- [ ] **Step 8: Run the full suite**

Run: `scripts/test.sh`
Expected: all pass. `config_flow.py` still imports `api`, but no test loads it yet. If collection fails on it, move straight on to Task 9.

- [ ] **Step 9: Commit**

```bash
git add -A custom_components/bwwb_water tests/test_init.py tests/test_sensor.py
git commit -m "feat: v2 setup/migration and 12 coordinator sensors"
```

---

### Task 9: Config flow (user, reauth, reconfigure) + strings

**Files:**
- Rewrite: `custom_components/bwwb_water/config_flow.py`, `custom_components/bwwb_water/strings.json`, `custom_components/bwwb_water/translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `CAWClient` + errors (Task 5), `parse_summary`, `PortalParseError` (Task 2), `NAME`, `DOMAIN`.
- Produces: `validate_credentials(hass, username: str, password: str) -> str` (returns the account number); `BWWBConfigFlow` with `VERSION = 2`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config_flow.py`:
```python
"""Tests for the config flow."""
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bwwb_water.client import CAWAuthError, CAWConnectionError, CAWPortalChangedError

VALIDATE = "custom_components.bwwb_water.config_flow.validate_credentials"
SETUP = "custom_components.bwwb_water.async_setup_entry"
CREDS = {"username": "TESTUSER", "password": "pw"}


@pytest.fixture(autouse=True)
def no_setup():
    with patch(SETUP, return_value=True):
        yield


async def test_user_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init("bwwb_water", context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Central Alabama Water (999900001111)"
    assert result["data"] == CREDS
    assert result["result"].unique_id == "999900001111"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CAWAuthError(), "invalid_auth"),
        (CAWConnectionError(), "cannot_connect"),
        (CAWPortalChangedError(), "portal_changed"),
        (RuntimeError(), "unknown"),
    ],
)
async def test_user_flow_errors(hass, error, code):
    result = await hass.config_entries.flow.async_init("bwwb_water", context={"source": config_entries.SOURCE_USER})
    with patch(VALIDATE, side_effect=error):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": code}


async def test_duplicate_account_aborts(hass):
    MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init("bwwb_water", context={"source": config_entries.SOURCE_USER})
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


async def test_reconfigure_updates_credentials(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(VALIDATE, return_value="999900001111"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "NEWUSER", "password": "new"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"username": "NEWUSER", "password": "new"}


async def test_reconfigure_rejects_other_account(hass):
    entry = MockConfigEntry(domain="bwwb_water", version=2, unique_id="999900001111", data=CREDS)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(VALIDATE, return_value="888800002222"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["reason"] == "unique_id_mismatch"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `scripts/test.sh tests/test_config_flow.py`
Expected: failures. The old flow imports `api` and has no reauth/reconfigure steps.

- [ ] **Step 3: Rewrite `config_flow.py`**

```python
"""Config flow for Central Alabama Water."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import CAWAuthError, CAWClient, CAWConnectionError, CAWPortalChangedError
from .const import DOMAIN, NAME
from .parsers import PortalParseError, parse_summary

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
PASSWORD_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def validate_credentials(hass: HomeAssistant, username: str, password: str) -> str:
    """Log in for real and return the portal account number."""
    client = CAWClient(async_create_clientsession(hass), username, password)
    return parse_summary(await client.get_summary_html()).account_number


class BWWBConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Central Alabama Water."""

    VERSION = 2

    async def _validate(self, username: str, password: str) -> tuple[str | None, dict[str, str]]:
        try:
            return await validate_credentials(self.hass, username, password), {}
        except CAWAuthError:
            return None, {"base": "invalid_auth"}
        except CAWConnectionError:
            return None, {"base": "cannot_connect"}
        except (CAWPortalChangedError, PortalParseError):
            return None, {"base": "portal_changed"}
        except Exception:
            _LOGGER.exception("Unexpected error validating Central Alabama Water credentials")
            return None, {"base": "unknown"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if account:
                await self.async_set_unique_id(account)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"{NAME} ({account})", data=user_input)
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if account:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if account:
                await self.async_set_unique_id(account)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, {CONF_USERNAME: entry.data[CONF_USERNAME]}),
            errors=errors,
        )
```

- [ ] **Step 4: Rewrite `strings.json`, and copy it verbatim to `translations/en.json`**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Central Alabama Water",
        "description": "Sign in with your Central Alabama Water online account (schedulepayment.com/alabama).",
        "data": {
          "username": "Username",
          "password": "Password"
        }
      },
      "reauth_confirm": {
        "title": "Re-authenticate",
        "description": "The portal rejected the stored password for {username}. Enter the current password.",
        "data": {
          "password": "Password"
        }
      },
      "reconfigure": {
        "title": "Update credentials",
        "data": {
          "username": "Username",
          "password": "Password"
        }
      }
    },
    "error": {
      "cannot_connect": "Could not reach schedulepayment.com. Check the internet connection and try again.",
      "invalid_auth": "The portal rejected the username or password.",
      "portal_changed": "The Central Alabama Water portal changed in a way this integration does not understand. Check for an integration update.",
      "unknown": "Unexpected error. Check the Home Assistant logs."
    },
    "abort": {
      "already_configured": "This account is already configured.",
      "reauth_successful": "Re-authentication was successful.",
      "reconfigure_successful": "Credentials updated.",
      "unique_id_mismatch": "These credentials belong to a different account."
    }
  }
}
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `scripts/test.sh tests/test_config_flow.py`
Expected: 9 passed.

- [ ] **Step 6: Full suite + no leftovers**

Run: `scripts/test.sh` and then `grep -rn "api\|auth_service\|sidecar" custom_components/bwwb_water/`
Expected: every test passes. The grep only matches the `CONF_AUTH_SERVICE_URL` constant and its migration use in `__init__.py`/`const.py`.

- [ ] **Step 7: Commit**

```bash
git add custom_components/bwwb_water/config_flow.py custom_components/bwwb_water/strings.json custom_components/bwwb_water/translations/en.json tests/test_config_flow.py
git commit -m "feat: config flow with reauth and reconfigure"
```

---

### Task 10: Sidecar cleanup, README and release metadata

**Files:**
- Modify: `auth-service/utility-auth-service.py`
- Modify: `README.md`, `hacs.json`

**Interfaces:**
- Consumes: nothing new.
- Produces: the v2.0.0 release contents.

- [ ] **Step 1: Remove the BWWB code from the sidecar**

In `auth-service/utility-auth-service.py`, delete:
- the `BWWB_CACHE`, `BWWB_DATA_CACHE_MINUTES` and `BWWB_LOCK` globals (around lines 50–52);
- the whole `# ─── BWWB ───` section: `_bwwb_fetch`, `get_bwwb_data_playwright`, `_get_bwwb_data_impl`, through to the next `# ───` section header;
- the `/bwwb/data` request handler function and its `app.router.add_post("/bwwb/data", ...)` route;
- the BWWB lines in the module docstring (lines 10, 18, 25, 32). The docstring should now describe a Southern Company–only service.

Keep `/sc/auth`, `/auth` and `/health`, and keep the imports they still use. Remove imports used only by BWWB code.

- [ ] **Step 2: Verify the sidecar still parses and BWWB is gone**

Run: `python -c "import ast,sys; ast.parse(open('auth-service/utility-auth-service.py', encoding='utf-8').read()); print('ok')"` and `grep -ni "bwwb" auth-service/utility-auth-service.py`
Expected: `ok`, and no grep output.

- [ ] **Step 3: Update `hacs.json`**

```json
{
  "name": "Central Alabama Water (formerly BWWB)",
  "render_readme": true,
  "homeassistant": "2025.11.0"
}
```

(`filename` is dropped. It only applies to single-file plugin repos and is wrong for an integration.)

- [ ] **Step 4: Rewrite `README.md`**

````markdown
> ⚠️ **Personal project — not a professional developer.** Built for personal use with AI assistance (Claude). Works for my setup; use at your own risk, PRs welcome.

# Central Alabama Water (formerly Birmingham Water Works Board) — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Monthly meter reads, usage, bills, balance and autopay status from the Central Alabama Water customer portal, plus Energy-dashboard statistics with real billed water + sewer cost.

Birmingham Water Works Board became **Central Alabama Water** in 2026 and moved its customer portal to `schedulepayment.com/alabama`. v2.0.0 of this integration talks to that portal directly. **The Playwright sidecar is no longer needed for water.**

## Installation

1. HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/Space-C0wboy/ha-bwwb-water` (Integration).
2. Install **Central Alabama Water**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Central Alabama Water** → enter your portal username and password.

Upgrading from v1: just update and restart. The existing entry migrates automatically, and your sensors keep their entity IDs.

## Sensors

| Sensor | Unit | Notes |
|---|---|---|
| Water Meter | ft³ | Cumulative meter read (CCF × 100), `total_increasing` |
| Water Meter (CCF) | CCF | Same read in CCF |
| Last Read Date | date | Attribute: `previous_read_date` |
| Current Period Usage | CCF | Latest bill's consumption |
| Previous Period Usage | CCF | Prior bill's consumption |
| Current Balance | USD | Negative = credit |
| Last Bill Amount | USD | Attributes: `bill_date`, `due_date` |
| Last Bill Due Date | date | |
| Water Charges | USD | Latest bill's water total; attribute `line_items` |
| Sewer Charges | USD | Latest bill's Jefferson County sewer total; attribute `line_items` |
| Scheduled Payment | — | e.g. `Waiting for bill`; last payment in attributes |
| Days Until Due | d | `0` when nothing is owed (attribute `paid: true`); negative when past due |

## Energy dashboard

The integration imports two long-term statistics, each with one data point per bill on the actual meter-read date (12 months backfilled on first setup):

- `bwwb_water:water_consumption`: water volume (ft³)
- `bwwb_water:water_cost`: billed water + sewer charges

Settings → Dashboards → Energy → Water consumption → add **Central Alabama Water consumption**, and choose "Use an entity tracking the total costs" → **Central Alabama Water cost**.

## Data notes

- Reads are monthly; the portal offers no daily or hourly data. Each month shows as one bar on its read date.
- Data comes from the HTML account pages and the text of each statement PDF. Each PDF is downloaded once and cached in `.storage/`.
- The integration polls every 6 hours and only ever reads. It never makes payments or changes account settings.

## Troubleshooting

- **invalid_auth**: check that you can log in at https://schedulepayment.com/alabama.
- **portal_changed**: the portal's pages changed; open an issue with debug logs (`custom_components.bwwb_water: debug`).

## The `auth-service/` directory

It holds a Playwright sidecar still used by the separate Southern Company / Alabama Power integration. This integration no longer uses it.

Not affiliated with Central Alabama Water.
````

- [ ] **Step 5: Run the full suite**

Run: `scripts/test.sh`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add auth-service/utility-auth-service.py README.md hacs.json
git commit -m "docs: v2.0.0 README; drop BWWB from the auth sidecar"
```

---

### Task 11: Release, deploy and live verification (OWNER-CONFIRMED STEPS)

**Files:** none in this repo. This task acts on GitHub, HA and the desktop Docker host.

**Interfaces:**
- Consumes: the finished `caw-portal-v2` branch.
- Produces: v2.0.0 running in HA, the Energy dashboard switched over, and the sidecar rebuilt.

Every step that pushes, publishes or changes HA configuration needs an explicit "yes" from the owner in chat before it runs.

- [ ] **Step 1: Push and open a PR** (needs owner approval)

```bash
git push -u origin caw-portal-v2
gh pr create --repo Space-C0wboy/ha-bwwb-water --base main --title "v2.0.0: Central Alabama Water portal" --body "Direct schedulepayment.com integration replacing the dead SAP/OData sidecar path. See docs/superpowers/specs/2026-10-01-caw-portal-redesign-design.md."
```

- [ ] **Step 2: Merge, tag and release v2.0.0** (needs owner approval)

```bash
gh pr merge --repo Space-C0wboy/ha-bwwb-water --squash
git checkout main && git pull
git tag v2.0.0 && git push origin v2.0.0
gh release create v2.0.0 --repo Space-C0wboy/ha-bwwb-water --title "v2.0.0 — Central Alabama Water" --notes "BWWB is now Central Alabama Water. Talks to schedulepayment.com directly (no sidecar), adds water/sewer charges, scheduled payment, days-until-due, and Energy-dashboard statistics with real billed cost. Existing entries migrate automatically."
```

- [ ] **Step 3: Update in HA** (needs owner approval)

In HACS, update **Central Alabama Water** to v2.0.0 (ha-mcp `ha_manage_hacs`, or the HACS UI), then restart HA (`ha_restart`).

- [ ] **Step 4: Verify live**

- `ha_get_state` on the 12 sensors. Expect `water_meter` = latest read × 100 (≥ 93900), balance matching the portal, nothing `unavailable`.
- `ha_get_logs source=system`, search `bwwb_water`: no errors.
- Developer Tools → Statistics (or ha-mcp): `bwwb_water:water_consumption` and `bwwb_water:water_cost` exist with about 12 monthly points.

- [ ] **Step 5: Switch the Energy dashboard** (needs owner approval)

With `ha_manage_energy_prefs`, change the water source from `sensor.birmingham_water_works_board_bwwb_water_meter` (with `number_energy_price` 0.0296) to `stat_energy_from: bwwb_water:water_consumption`, `stat_cost: bwwb_water:water_cost`. The 2026-07-29 memory notes that `mode='set'` can time out during validation; retry.

- [ ] **Step 6: Rebuild the sidecar from `main`** (needs owner approval)

```bash
cd C:/Users/ksgra/utility-auth-sidecar
docker build --no-cache -t utility-auth-sidecar:latest .
docker rm -f utility-auth-sidecar
docker run -d --name utility-auth-sidecar --restart unless-stopped -p 18792:18792 utility-auth-sidecar:latest
curl -s http://localhost:18792/health
```
Expected: health JSON. Then confirm in HA that the Southern Company entry recovers.

- [ ] **Step 7: Update the memory and backlog**

Update `utility-auth-sidecar.md` in the Claude memory directory: BWWB no longer uses the sidecar; v2.0.0 is direct to schedulepayment.com. Add or close the relevant `BACKLOG.md` entry in the Home-Assistant repo.
