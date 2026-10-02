#!/usr/bin/env python3
"""
Utility Auth Service
Runs on the Pi, provides a local HTTP API for Home Assistant integrations
that need real browser-based authentication to bypass JavaScript-rendered
login pages or Incapsula bot-detection challenges.

Endpoints:
  POST /sc/auth      -> Southern Company (Alabama Power) - Incapsula WAF bypass
  GET  /health       -> Health check

Usage: python3 southern_company_auth_service.py
Listens on: http://<your-pi-ip>:18792

Why a sidecar?
--------------
Southern Company's webauth.southernco.com is behind Incapsula/Imperva WAF.
Plain aiohttp requests return a bot-detection challenge page. Playwright
bypasses the challenge and extracts the data-aft CSRF token.

Architecture:
  HA -> POST /sc/auth (local LAN)
     -> Playwright headless Chromium (this Pi)
     -> Returns the Southern Company token as JSON
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


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "utility-auth-service",
        "endpoints": ["/sc/auth", "/auth", "/health"],
    })

# Legacy endpoint
async def handle_auth_legacy(request: web.Request) -> web.Response:
    return await handle_sc_auth(request)


app = web.Application()
app.router.add_post("/sc/auth", handle_sc_auth)
app.router.add_post("/auth", handle_auth_legacy)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    # Bind to all interfaces so HA Docker containers can reach us via 172.17.0.1:18792
    # (HA Docker network uses 172.17.0.0/16; Pi is the gateway at 172.17.0.1)
    log.info("Starting Utility Auth Service on 0.0.0.0:18792")
    log.info("Endpoints: /sc/auth, /auth, /health")
    web.run_app(app, host="0.0.0.0", port=18792)
