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
import re
import sys
from io import BytesIO
from pathlib import Path

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
    text = re.sub(
        r"\b(VISA|MC|MASTERCARD|AMEX|DISCOVER|CHECKING|SAVINGS)_\d{4}\b",
        "CARD_4242",
        text,
        flags=re.I,
    )
    text = re.sub(r"CW00[A-Z0-9]+", "CW00TEST1", text)
    text = re.sub(r"'\d{9,}'", "'000000000000'", text)
    text = re.sub(
        r"\b\d{9}\b(?=\s+\d/\d\")", "000000001", text
    )  # meter number, before size e.g. 5/8"
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
            data={
                "__RequestVerificationToken": token.group(1),
                "UserName": args.username,
                "Password": password,
            },
        ) as resp:
            after = await resp.text()
            print(f"POST login: HTTP {resp.status}, final URL {resp.url}")
        if "/alabama/Home/Logout" not in after:
            print("FAIL: not logged in after POST. Page title / errors:")
            for m in re.finditer(
                r"<title>(.*?)</title>|validation-summary-errors[^>]*>(.*?)</div>", after, re.S
            ):
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
            (FIXTURES / name).write_text(
                scrub(text, account, args.username, args.redact), encoding="utf-8"
            )
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
                print(
                    f"statement {stamp}: HTTP {resp.status}, {resp.content_type}, {len(data)} bytes"
                )
            reader = PdfReader(BytesIO(data))
            write(name, "\n".join(page.extract_text() or "" for page in reader.pages))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
