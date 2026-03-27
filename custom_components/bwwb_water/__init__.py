"""Birmingham Water Works Board Home Assistant integration."""
from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from .api import BWWBAPI, BWWBAuthError, BWWBConnectionError
from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = BWWBAPI()
    try:
        success = await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        if not success:
            raise ConfigEntryNotReady("BWWB: initial auth failed")
    except (BWWBAuthError, BWWBConnectionError) as exc:
        raise ConfigEntryNotReady(f"BWWB init error: {exc}") from exc
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
