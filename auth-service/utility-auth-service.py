#!/usr/bin/env python3
"""
Utility Auth & Data Service
Runs on the Pi, provides a local HTTP API for Home Assistant integrations
that need real browser-based authentication to bypass JavaScript-rendered
login pages, Cloudflare WAF, or Incapsula bot-detection challenges.

Endpoints:
  POST /sc/auth      → Southern Company (Alabama Power) — Incapsula WAF bypass
  POST /bwwb/data    → BWWB — full data fetch (login + OData) via Playwright
  GET  /health       → Health check

Usage: python3 southern_company_auth_service.py
Listens on: http://<your-pi-ip>:18792

Why a sidecar?
--------------
Both Southern Company and BWWB use JavaScript-rendered login pages and
Cloudflare/Incapsula WAF on their API endpoints:

  - Southern Company: webauth.southernco.com is behind Incapsula/Imperva WAF.
    Plain aiohttp requests return a bot-detection challenge page. Playwright
    bypasses the challenge and extracts the data-aft CSRF token.

  - BWWB: Uses SAP SAPUI5 (Utegration Mobius). Login is JS-rendered only.
    Additionally, ALL OData API endpoints are behind Cloudflare WAF — direct
    aiohttp calls return "Just a moment..." challenge pages. Playwright session
    cookies carry the Cloudflare clearance, so all data fetches must also go
    through the browser session.

Architecture:
  HA → POST /bwwb/data or /sc/auth (local LAN)
     → Playwright headless Chromium (this Pi)
     → Returns data as JSON
"""

import asyncio
import datetime
import json
import logging
import re
from aiohttp import web

import jwt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("utility-auth-service")

SC_CACHE: dict = {}
BWWB_CACHE: dict = {}
BWWB_DATA_CACHE_MINUTES = 60  # Cache full data fetch for 1 hour


# ─── SOUTHERN COMPANY ────────────────────────────────────────────────────────

async def get_sc_token_playwright(username: str, password: str) -> dict:
    """Playwright auth for Southern Company (Incapsula WAF bypass)."""
    from playwright.async_api import async_playwright

    cached = SC_CACHE.get(username)
    if cached and datetime.datetime.now() < cached["sc_expiry"]:
        log.info(f"SC cache hit for {username}")
        return {"sc_token": cached["sc_token"], "sc_expiry": cached["sc_expiry"].isoformat()}

    log.info(f"SC: launching Playwright for {username}...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled", "--disable-extensions",
        ])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = {runtime: {}};
        """)
        await page.goto("https://webauth.southernco.com/account/login", timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.wait_for_load_state("networkidle", timeout=20000)

        aft_token = await page.evaluate(
            "() => { const el = document.querySelector('[data-aft]'); return el ? el.getAttribute('data-aft') : null; }"
        )
        if not aft_token:
            await browser.close()
            raise ValueError("Could not find data-aft token")

        escaped_user = username.replace("'", "\\'").replace('"', '\\"')
        escaped_pass = password.replace("'", "\\'").replace('"', '\\"')
        escaped_aft = aft_token.replace("'", "\\'")

        result = await page.evaluate(f"""async () => {{
            const r = await fetch('/api/login', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json; charset=utf-8', 'RequestVerificationToken': '{escaped_aft}'}},
                body: JSON.stringify({{username: '{escaped_user}', password: '{escaped_pass}', targetPage: 1, params: {{ReturnUrl: 'null'}}}})
            }});
            return {{status: r.status, body: await r.text()}};
        }}""")
        await browser.close()

    if result["status"] != 200:
        raise ValueError(f"SC login returned {result['status']}")
    connection = json.loads(result["body"])
    if connection.get("statusCode") == 500:
        raise ValueError("Invalid SC credentials")

    sc_regex = re.compile(r"NAME='ScWebToken' value='(\S+\.\S+\.\S+)'", re.IGNORECASE)
    sc_data = sc_regex.search(connection["data"]["html"])
    if not sc_data:
        raise ValueError("ScWebToken not found in response")

    sc_token = sc_data.group(1).split("'>")[0] if "'>" in sc_data.group(1) else sc_data.group(1)
    sc_decoded = jwt.decode(sc_token, options={"verify_signature": False})
    sc_expiry = datetime.datetime.fromtimestamp(sc_decoded["exp"])

    SC_CACHE[username] = {"sc_token": sc_token, "sc_expiry": sc_expiry}
    log.info(f"SC auth successful for {username}, expires {sc_expiry}")
    return {"sc_token": sc_token, "sc_expiry": sc_expiry.isoformat()}


def _parse_sap_date(date_str: str) -> str | None:
    """Convert SAP /Date(ms)/ format to YYYY-MM-DD string."""
    if not date_str:
        return None
    m = re.search(r"/Date\((\d+)", date_str)
    if m:
        return datetime.datetime.fromtimestamp(int(m.group(1)) / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    return date_str[:10] if len(date_str) >= 10 else None


# ─── BWWB ────────────────────────────────────────────────────────────────────

async def _bwwb_fetch(page, url: str) -> dict:
    """Fetch a URL via the authenticated Playwright page session."""
    return await page.evaluate(
        """async (url) => {
            const r = await fetch(url, {headers: {'Accept': 'application/json'}});
            return {status: r.status, body: await r.text()};
        }""",
        url
    )


async def get_bwwb_data_playwright(username: str, password: str) -> dict:
    """
    Full BWWB data fetch via Playwright.

    Why everything goes through Playwright:
    1. Login is SAP SAPUI5 JS-rendered — can't be done with plain HTTP
    2. ALL OData endpoints are behind Cloudflare WAF — direct aiohttp returns
       "Just a moment..." challenge pages even with valid session cookies.
       The Cloudflare clearance is tied to the browser session's TLS fingerprint
       and cannot be transferred to aiohttp.

    Solution: login + all OData calls happen inside the Playwright browser
    session. We return the processed data (not raw cookies) so HA never needs
    to make direct calls to web.bwwb.org.
    """
    from playwright.async_api import async_playwright

    cached = BWWB_CACHE.get(username)
    if cached:
        age = (datetime.datetime.now() - cached["cached_at"]).total_seconds() / 60
        if age < BWWB_DATA_CACHE_MINUTES:
            log.info(f"BWWB data cache hit for {username}, age {age:.1f}m")
            return cached["data"]
        else:
            log.info(f"BWWB cache expired for {username} (age {age:.1f}m), re-fetching...")

    log.info(f"BWWB: launching Playwright for {username}...")
    BASE = "https://web.bwwb.org"
    ODATA = "/sap/opu/odata/sap/ZUTE_ERP_UT_UMC_SRV"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        await page.goto(f"{BASE}/myaccount/?sap-client=300&sap-language=EN", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)

        # Fill login form
        for sel in ["#__xmlview0--nameInput-inner", "input[type='text']"]:
            try: await page.fill(sel, username, timeout=3000); break
            except: continue
        for sel in ["#__xmlview0--passwordInput-inner", "input[type='password']"]:
            try: await page.fill(sel, password, timeout=3000); break
            except: continue

        await asyncio.sleep(1)
        for sel in ["#__xmlview0--loginButton", "button:has-text('Log On')", "button:has-text('Login')"]:
            try: await page.click(sel, timeout=3000); break
            except: continue

        # Wait for #/Home to confirm successful login (up to 30s)
        login_ok = False
        for _ in range(15):
            await asyncio.sleep(2)
            if "#/Home" in page.url:
                login_ok = True
                break
        if not login_ok:
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        final_url = page.url
        log.info(f"BWWB: post-login URL: {final_url}")

        if "#/Home" not in final_url and "myaccount" not in final_url:
            await browser.close()
            raise ValueError(f"BWWB login failed. Final URL: {final_url}")

        # ── Known IDs (fallback) ──
        device_id = None  # Discovered dynamically from OData
        contract_id = None  # Discovered dynamically from OData
        account_id = None  # Discovered dynamically from OData

        # Try to discover device/contract from Devices endpoint
        devices_result = await _bwwb_fetch(page, f"{ODATA}/Devices?$format=json")
        log.info(f"BWWB devices fetch: {devices_result['status']}")
        if devices_result["status"] == 200:
            try:
                results = json.loads(devices_result["body"]).get("d", {}).get("results", [])
                if results:
                    device_id = results[0].get("DeviceID") or device_id
                    log.info(f"BWWB: discovered device_id={device_id}")
            except Exception as e:
                log.warning(f"BWWB: failed to parse devices: {e}")

        # ── Fetch meter reading results (actual cumulative reads) ──
        readings_result = await _bwwb_fetch(
            page, f"{ODATA}/Devices('{device_id}')/MeterReadingResults?$format=json&$top=10"
        )
        log.info(f"BWWB meter readings: {readings_result['status']}")

        # ── Fetch consumption values ──
        consumption_result = await _bwwb_fetch(
            page, f"{ODATA}/Contracts('{contract_id}')/ContractConsumptionValues?$format=json&$top=12"
        )
        log.info(f"BWWB consumption: {consumption_result['status']}")

        # ── Fetch invoices (bill history) ──
        invoices_result = await _bwwb_fetch(
            page, f"{ODATA}/Accounts('{account_id}')/Invoices?$format=json&$top=10"
        )
        log.info(f"BWWB invoices: {invoices_result['status']}")

        # ── Fetch account balance ──
        balance_result = await _bwwb_fetch(
            page, f"{ODATA}/Accounts('{account_id}')/AccountBalance?$format=json"
        )
        log.info(f"BWWB balance: {balance_result['status']}")

        await browser.close()

    # ── Parse meter readings ──
    meter_readings = []
    if readings_result["status"] == 200:
        try:
            meter_readings = json.loads(readings_result["body"]).get("d", {}).get("results", [])
        except Exception as e:
            log.warning(f"BWWB: failed to parse meter readings: {e}")

    # Sort meter readings by ReadingDateTime descending (newest first)
    def _reading_ts(r):
        m = re.search(r"/Date\((\d+)", r.get("ReadingDateTime", ""))
        return int(m.group(1)) if m else 0
    meter_readings.sort(key=_reading_ts, reverse=True)

    # ── Parse consumption values ──
    # These come in ascending order from the API (oldest first)
    consumption_values = []
    if consumption_result["status"] == 200:
        try:
            consumption_values = json.loads(consumption_result["body"]).get("d", {}).get("results", [])
        except Exception as e:
            log.warning(f"BWWB: failed to parse consumption: {e}")

    # Sort consumption by StartDate ascending to be safe
    def _cons_ts(c):
        m = re.search(r"/Date\((\d+)", c.get("StartDate", ""))
        return int(m.group(1)) if m else 0
    consumption_values.sort(key=_cons_ts)

    # ── Parse invoices ──
    invoices = []
    if invoices_result["status"] == 200:
        try:
            invoices = json.loads(invoices_result["body"]).get("d", {}).get("results", [])
        except Exception as e:
            log.warning(f"BWWB: failed to parse invoices: {e}")

    # Sort invoices by DueDate descending (newest first)
    def _inv_ts(inv):
        m = re.search(r"/Date\((\d+)", inv.get("DueDate", ""))
        return int(m.group(1)) if m else 0
    invoices.sort(key=_inv_ts, reverse=True)

    # ── Parse account balance ──
    balance_data = {}
    if balance_result["status"] == 200:
        try:
            balance_data = json.loads(balance_result["body"]).get("d", {})
        except Exception as e:
            log.warning(f"BWWB: failed to parse balance: {e}")

    # ── Extract latest meter reading (cumulative CCF and ft³) ──
    latest_reading_ccf = None
    latest_reading_ft3 = None
    latest_date = None
    if meter_readings:
        r = meter_readings[0]  # newest (sorted desc)
        try:
            latest_reading_ccf = float(r.get("ReadingResult", 0) or 0)
            latest_reading_ft3 = latest_reading_ccf * 100  # CCF → ft³
            date_raw = r.get("ReadingDateTime", "")
            match = re.search(r"/Date\((\d+)", date_raw)
            if match:
                ts = int(match.group(1)) / 1000
                latest_date = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        except Exception as e:
            log.warning(f"BWWB: failed to extract meter reading: {e}")

    # ── Extract current/previous period consumption ──
    # consumption_values is sorted ascending: [-1] = newest, [-2] = previous
    current_ccf = None
    prev_ccf = None
    current_start = None
    current_end = None
    if consumption_values:
        current_ccf = float(consumption_values[-1].get("ConsumptionValue", 0) or 0)
        current_start = _parse_sap_date(consumption_values[-1].get("StartDate", ""))
        current_end = _parse_sap_date(consumption_values[-1].get("EndDate", ""))
        if len(consumption_values) > 1:
            prev_ccf = float(consumption_values[-2].get("ConsumptionValue", 0) or 0)

    # If meter readings failed, derive last_read_date from consumption end date
    if latest_date is None and current_end:
        latest_date = current_end

    # ── Extract billing summary ──
    current_balance = None
    past_due = None
    if balance_data:
        try:
            current_balance = float(balance_data.get("CurrentBalance", 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            past_due = float(balance_data.get("OpenCollectable", 0) or 0)
        except (ValueError, TypeError):
            pass

    # Latest invoice
    last_bill_amount = None
    last_bill_date = None
    last_bill_due_date = None
    if invoices:
        inv = invoices[0]
        try:
            last_bill_amount = float(inv.get("AmountDue", 0) or 0)
        except (ValueError, TypeError):
            pass
        last_bill_date = _parse_sap_date(inv.get("InvoiceDate", ""))
        last_bill_due_date = _parse_sap_date(inv.get("DueDate", ""))

    data = {
        "device_id": device_id,
        "contract_id": contract_id,
        "account_id": account_id,
        "meter_reading_ccf": latest_reading_ccf,
        "meter_reading_ft3": latest_reading_ft3,
        "current_period_ccf": current_ccf,
        "prev_period_ccf": prev_ccf,
        "current_period_start": current_start,
        "current_period_end": current_end,
        "last_read_date": latest_date,
        "current_balance": current_balance,
        "past_due": past_due,
        "last_bill_amount": last_bill_amount,
        "last_bill_date": last_bill_date,
        "last_bill_due_date": last_bill_due_date,
        "meter_readings_raw": meter_readings[:5],
        "consumption_raw": consumption_values[:12],
        "invoices_raw": [
            {
                "invoice_id": inv.get("InvoiceID"),
                "amount_due": float(inv.get("AmountDue", 0) or 0),
                "amount_remaining": float(inv.get("AmountRemaining", 0) or 0),
                "invoice_date": _parse_sap_date(inv.get("InvoiceDate", "")),
                "due_date": _parse_sap_date(inv.get("DueDate", "")),
            }
            for inv in invoices[:10]
        ],
        "fetched_at": datetime.datetime.now().isoformat(),
    }

    # Only cache if we got real data — don't poison cache with empty results
    if latest_reading_ft3 is not None:
        BWWB_CACHE[username] = {"data": data, "cached_at": datetime.datetime.now()}
        log.info(f"BWWB data fetch complete: {latest_reading_ccf} CCF ({latest_reading_ft3} ft³) on {latest_date}, balance=${current_balance}")
    else:
        # Auth succeeded but OData returned nothing — return stale cache if available
        stale = BWWB_CACHE.get(username)
        if stale:
            log.warning(f"BWWB fetch returned empty data for {username}, returning stale cache")
            return stale["data"]
        log.warning(f"BWWB fetch returned empty data for {username} and no cache available")
    return data


# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def handle_sc_auth(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        username = body.get("username")
        password = body.get("password")
        if not username or not password:
            return web.json_response({"error": "username and password required"}, status=400)
        result = await get_sc_token_playwright(username, password)
        return web.json_response({"success": True, **result})
    except ValueError as e:
        log.error(f"SC auth error: {e}")
        return web.json_response({"error": str(e)}, status=401)
    except Exception as e:
        log.error(f"SC unexpected error: {e}", exc_info=True)
        return web.json_response({"error": f"Internal error: {str(e)}"}, status=500)


async def handle_bwwb_data(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        username = body.get("username")
        password = body.get("password")
        if not username or not password:
            return web.json_response({"error": "username and password required"}, status=400)
        data = await get_bwwb_data_playwright(username, password)
        return web.json_response({"success": True, **data})
    except ValueError as e:
        log.error(f"BWWB error: {e}")
        return web.json_response({"error": str(e)}, status=401)
    except Exception as e:
        log.error(f"BWWB unexpected error: {e}", exc_info=True)
        return web.json_response({"error": f"Internal error: {str(e)}"}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "utility-auth-service",
        "endpoints": ["/sc/auth", "/bwwb/data", "/health"],
    })

# Legacy endpoint
async def handle_auth_legacy(request: web.Request) -> web.Response:
    return await handle_sc_auth(request)


app = web.Application()
app.router.add_post("/sc/auth", handle_sc_auth)
app.router.add_post("/bwwb/data", handle_bwwb_data)
app.router.add_post("/bwwb/auth", handle_bwwb_data)  # backwards compat alias
app.router.add_post("/auth", handle_auth_legacy)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    # Bind to all interfaces so HA Docker containers can reach us via 172.17.0.1:18792
    # (HA Docker network uses 172.17.0.0/16; Pi is the gateway at 172.17.0.1)
    log.info("Starting Utility Auth & Data Service on 0.0.0.0:18792")
    log.info("Endpoints: /sc/auth, /bwwb/data, /health")
    web.run_app(app, host="0.0.0.0", port=18792)
