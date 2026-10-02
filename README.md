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
