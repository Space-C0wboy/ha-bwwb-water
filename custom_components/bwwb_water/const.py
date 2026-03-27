"""Constants for the Birmingham Water Works Board integration."""

DOMAIN = "bwwb_water"
NAME = "Birmingham Water Works Board"

# Pi sidecar service — handles Playwright login + OData fetching
# Both the SAP SAPUI5 login AND the OData API are behind WAF/Cloudflare,
# so all data access goes through this service.
AUTH_SERVICE_URL = "http://localhost:18792/bwwb/data"  # Override during setup

# Update interval (minutes) — BWWB only updates on billing cycles (~monthly)
# but we poll hourly in case of manual reads or system updates
UPDATE_INTERVAL = 360  # 6 hours

# Config keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Sensor keys
SENSOR_WATER_METER = "water_meter"
SENSOR_LAST_READ_DATE = "last_read_date"
