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
