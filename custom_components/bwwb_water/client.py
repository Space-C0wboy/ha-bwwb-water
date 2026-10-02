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
