# Central Alabama Water portal redesign (v2.0.0)

**Date:** 2026-10-01
**Status:** Approved design, pending implementation plan

## Background

Birmingham Water Works Board rebranded as **Central Alabama Water (CAW)**
(`bwwb.org` → `caw-al.gov`) and replaced its SAP/Utegration Mobius customer
portal with a Doxim-hosted portal at `https://schedulepayment.com/alabama`.
The old SAPUI5 login and OData service are gone, so v1 of this integration
(Playwright sidecar → OData) no longer works; all sensors went `unavailable`
on 2026-10-01.

### What the new portal is

- Server-rendered ASP.NET MVC + jQuery. No SAPUI5, no CAPTCHA.
- Login: `POST /alabama/Home/Login` with form fields
  `__RequestVerificationToken`, `UserName`, `Password` (token + cookie come
  from `GET /alabama`).
- Cloudflare proxies the site, but plain non-browser clients (curl, Python
  urllib) receive the login page with HTTP 200 and no challenge.
  **The login POST itself is unverified** (see "Login probe").

### What data is available after login

| Page | Format | Contents |
|---|---|---|
| `/alabama/Account/AccountSummary` | HTML | Account number, service address, payment status, account balance (negative = credit), statement amount, due date, link to current statement |
| `/alabama/Billing/SingleAccountStatementHistory` | HTML table | ~12 statements: issue date, amount, due date, `ViewStatement` link |
| `/alabama/Account/ViewStatement?AccountNumber=<acct>&StatementDate=MM%2FDD%2FYYYY` | PDF (3 pages, text-extractable) | See below |
| `/alabama/Payment/SingleAccountPaymentSummary` | HTML tables | Scheduled payments (type, amount, funding source, status) and completed payments (date, amount, status, confirmation #) |
| "Usage Data" menu item | — | Hidden (`display:none`), `href="javascript:void(0)"`; disabled for this biller. Not used. |

Statement PDF contents (page 1 + page 3), identical layout on both CAW- and
BWWB-branded statements back to at least Oct 2025:

```
INVOICE DATE:   MM/DD/YYYY
ACCOUNT NUMBER:   <acct>
SERVICE PERIOD:   MM/DD/YYYY - MM/DD/YYYY
TOTAL AMOUNT DUE by MM/DD/YYYY   $NN.NN
CAW WATER SERVICE   $NN.NN
JEFFERSON COUNTY SEWER SERVICE   $NN.NN
METER READINGS
METER NO   SZ   PREVIOUS READ   PRESENT READ   CONS
<meter>   5/8"   <prev> MM/DD/YY   <present> MM/DD/YY   <cons>
...
CAW WATER CHARGES
Base Service Charge   NN.NN
Tier 1 (1 - 3) 3.00 CCF @ $2.96   NN.NN
Tier 2 (4 - 12) 1.00 CCF @ $4.91   NN.NN
Alabama 4 % Utility Tax   NN.NN
TOTAL Water Charges   $NN.NN
JEFFERSON COUNTY SEWER CHARGES
Base Service Charge   NN.NN
...
TOTAL Sewer Charges   $NN.NN
```

Older BWWB-branded bills label the line `BWWB WATER SERVICE` (or similar); the
parser must match the water/sewer **TOTAL ... Charges** lines on page 3 and
not depend on the brand name.

Meter reads are in CCF and cumulative, and continue the v1 series (same meter),
so the existing ft³ sensor continues without a discontinuity.

## Decisions

1. **Rework this repo in place** as v2.0.0. Keep the `bwwb_water` domain
   and all existing sensor `unique_id`s so entity IDs, dashboards and
   automations survive. Display names and README move to "Central Alabama
   Water".
2. **Talk to the portal directly from Home Assistant** (aiohttp + `pypdf`).
   No Playwright, no sidecar for water.
3. **Import external statistics** dated to the actual meter-read date, with a
   12-month backfill and real billed cost, for the Energy dashboard.

Rejected: keeping the sidecar with a new scraper (keeps the always-on-desktop
dependency for no current benefit); direct + sidecar fallback (two code paths
for a hypothetical Cloudflare challenge); new `caw_water` domain (breaks
long-term statistics continuity).

## Architecture

```
custom_components/bwwb_water/
  __init__.py      setup/unload, async_migrate_entry (v1 → v2)
  client.py        CAWClient: owns an aiohttp session (HA's cookie-jar-enabled
                   client session); login(), get_summary_html(),
                   get_statement_history_html(), get_payments_html(),
                   get_statement_pdf(date) -> bytes. HTTP only, no parsing.
  parsers.py       Pure functions, no I/O:
                     parse_summary(html) -> AccountSummary
                     parse_statement_list(html) -> list[StatementRef]
                     parse_payments(html) -> Payments
                     parse_bill_pdf(data: bytes) -> Bill
                   plus the dataclasses above.
  coordinator.py   DataUpdateCoordinator[CAWData], update_interval 6h.
  statistics.py    Builds and imports external statistics from a list of Bills.
  sensor.py        12 CoordinatorEntity sensors.
  config_flow.py   user, reauth, reconfigure steps.
  const.py, manifest.json (requirements: pypdf), strings.json, translations/
```

Each unit can be tested in isolation: `parsers.py` against fixture files,
`statistics.py` against a list of `Bill`s, `client.py` against mocked HTTP.

The `auth-service/` directory stays in the repo **only** because the Southern
Company (Alabama Power) integration still uses it; its `/bwwb/data` endpoint
and BWWB code are removed and the README states water no longer needs it.

## Data flow (each poll, every 6 hours)

1. **Session.** If no authenticated session: `GET /alabama`, extract
   `__RequestVerificationToken`; `POST /alabama/Home/Login`.
   - Final URL `/alabama/Account/AccountSummary` → success.
   - Login page returned with a validation message → `CAWAuthError`.
   - Login page returned **without** the expected form fields →
     `CAWPortalChangedError`.
   - Any later request redirected to the login page → re-login once, retry
     the request; a second redirect → `CAWAuthError`.
2. **Fetch + parse** AccountSummary, StatementHistory, PaymentSummary.
3. **New PDFs only.** Parsed `Bill`s are persisted in an HA `Store`
   (`.storage/bwwb_water.<entry_id>`) keyed by statement date. Only statement
   dates absent from the store are downloaded and parsed. The first run
   therefore backfills every listed statement (~12); later runs fetch ~1/month.
4. **Per-bill failure isolation.** If a PDF fails to download or parse, log a
   warning, skip it, and retry it next poll. It does not fail the update.
5. **Import statistics** (see below) whenever the set of stored bills changed.
6. **Return `CAWData`** (summary, payments, bills sorted by read date) to
   sensors.

Error mapping in the coordinator: `CAWAuthError` → `ConfigEntryAuthFailed`
(starts reauth); connection errors, HTTP 5xx, `CAWPortalChangedError` →
`UpdateFailed`. Sensors use standard coordinator availability (a failed poll
makes them `unavailable`); v1's "hold last good value forever" behaviour is
dropped.

## External statistics

Imported with `homeassistant.components.recorder.statistics.async_add_external_statistics`,
`source = "bwwb_water"`, hourly-period rows (`start` = local midnight of the
date, as an aware datetime truncated to the hour).

| statistic_id | name | unit | has_sum | state | sum |
|---|---|---|---|---|---|
| `bwwb_water:water_consumption` | Central Alabama Water consumption | ft³ | yes | cumulative meter (present read × 100) | running total of `CONS × 100` |
| `bwwb_water:water_cost` | Central Alabama Water cost | USD | yes | — | running total of (water charges + sewer charges) |

Rules:
- **Baseline anchor:** one row at the oldest bill's *previous-read* date with
  `sum = 0` (consumption `state` = previous read × 100), so the first month
  registers as usage.
- One row per bill at its **present-read** date.
- `sum` accumulates the bill's **CONS** column, not read differences, so
  meter replacement or rollover cannot produce spikes.
- Cost per bill = `TOTAL Water Charges + TOTAL Sewer Charges`. Previous
  balance, payments and adjustments are excluded.
- Statistics are always rebuilt from the full stored bill list in date order
  and re-imported. Same `start` overwrites, so re-imports are idempotent.
- Bills older than the store (dropped from the portal's 12-statement window)
  remain in the store and keep their rows; the store is never pruned.

After deployment, Energy dashboard water source is switched from
`sensor.<…>_bwwb_water_meter` (+ fixed `number_energy_price`) to
`bwwb_water:water_consumption` with `stat_cost: bwwb_water:water_cost`.
History before the switch remains under the old sensor.

## Sensors

All are `CoordinatorEntity` sensors on one device ("Central Alabama Water",
manufacturer "Central Alabama Water"). Existing unique_id suffixes are kept.

| # | Name | unique_id suffix | Value | Unit / class | Attributes |
|---|---|---|---|---|---|
| 1 | Water Meter | `_water_meter` (kept) | latest present read × 100 | ft³, water, total_increasing | meter_number |
| 2 | Water Meter (CCF) | `_water_meter_ccf` (kept) | latest present read | CCF, total_increasing | — |
| 3 | Last Read Date | `_last_read_date` (kept) | latest present-read date | date | previous_read_date |
| 4 | Current Period Usage | `_current_period_ccf` (kept) | latest bill CONS | CCF, total | period_start, period_end |
| 5 | Previous Period Usage | `_prev_period_ccf` (kept) | second-latest bill CONS | CCF, total | period_start, period_end |
| 6 | Current Balance | `_current_balance` (kept) | account balance (negative = credit) | USD, monetary | payment_status |
| 7 | Last Bill Amount | `_last_bill_amount` (kept) | latest bill total amount due | USD, monetary | bill_date, due_date |
| 8 | Last Bill Due Date | `_last_bill_due_date` (kept) | latest bill due date | date | — |
| 9 | Water Charges | `_water_charges` (new) | latest bill TOTAL Water Charges | USD, monetary | line_items (list of {description, amount}), service_period |
| 10 | Sewer Charges | `_sewer_charges` (new) | latest bill TOTAL Sewer Charges | USD, monetary | line_items, service_period |
| 11 | Scheduled Payment | `_scheduled_payment` (new) | status text of the first scheduled payment, e.g. `Waiting for bill`, or `None` when none scheduled | — | type (Auto Draft / One-Time), amount, funding_source (portal's masked nickname), last_payment_date, last_payment_amount, last_payment_confirmation |
| 12 | Days Until Due | `_days_until_due` (new) | `(due_date − today).days`; **0 when balance ≤ 0** | days | due_date, paid (balance ≤ 0) |

Monetary sensors use no `state_class` (v1's `total`/`measurement` on money
values is dropped; cost history lives in the external statistic).
`_attr_has_entity_name = True`; names above are the entity names.

Days Until Due is evaluated on each coordinator update (every 6h), not live.
Past due shows as negative.

## Config flow

- **user:** username + password. Validates with a real login, then reads the
  account number from AccountSummary and uses it as the entry `unique_id`
  (duplicate accounts are rejected). Errors: `invalid_auth`, `cannot_connect`,
  `portal_changed`.
- **reauth:** re-prompt for password (username prefilled).
- **reconfigure:** change username and/or password in place.
- **Migration:** config entry `VERSION` 1 → 2. `async_migrate_entry` drops
  `auth_service_url` and keeps credentials. No delete/re-add is required. If
  the stored credentials fail, the normal reauth flow starts.
- If the portal lists linked accounts, the first account is used and the
  others are logged at INFO. Multi-account support is out of scope.

## Testing

- **Login probe (gate):** `scripts/probe_login.py` (stdlib or aiohttp only),
  run once by the owner with real credentials. Prints whether the login
  POST lands on AccountSummary. If Cloudflare blocks it, stop and re-plan
  around the sidecar approach.
- **Unit tests** (`pytest`, `pytest-homeassistant-custom-component`):
  - `parsers.py` against fixtures in `tests/fixtures/` (AccountSummary,
    StatementHistory, PaymentSummary HTML; text of one CAW-branded and one
    BWWB-branded statement). **All fixtures are scrubbed**: account number,
    name, address, meter number, funding source, confirmation numbers
    replaced with fakes. PDF parsing is tested on extracted text (the
    `pypdf` call is a thin wrapper); one tiny synthetic PDF tests the wrapper.
  - `statistics.py`: baseline row, running sums, cost excludes
    balances/payments, idempotent rebuild, unsorted input.
  - Days Until Due: future, today, past due, paid, credit.
  - Config flow: success, invalid_auth, cannot_connect, duplicate account,
    reauth, reconfigure, v1 → v2 migration.
- **Live verification after deploy:** 12 sensors load with plausible values;
  both statistics visible with ~12 months in Developer Tools → Statistics;
  Energy dashboard shows monthly water volume and real cost.

## Cutover

1. Owner runs the login probe → confirm direct login works.
2. Implement on branch `caw-portal-v2`, tests green.
3. Release v2.0.0 (manifest version, README rewrite, `hacs.json`), update via
   HACS, restart HA.
4. Verify the entry migrated and sensors populated.
5. Switch Energy prefs water source + cost to the external statistics.
6. Sidecar cleanup: remove `/bwwb/data` and BWWB code from
   `auth-service/utility-auth-service.py`, rebuild the desktop container from
   `main`.

## Out of scope

- Any payment or account-changing action.
- Multiple / linked accounts.
- The hidden "Usage Data" page.
- Daily or hourly usage (not offered by the portal).
