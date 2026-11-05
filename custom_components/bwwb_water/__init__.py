"""Central Alabama Water (formerly Birmingham Water Works Board) integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .client import CAWClient
from .const import CONF_AUTH_SERVICE_URL, DOMAIN, STORAGE_VERSION
from .coordinator import CAWConfigEntry, CAWCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: CAWConfigEntry) -> bool:
    client = CAWClient(
        # Own cookie jar for the portal session; HA detaches it when the entry unloads.
        async_create_clientsession(hass),
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version > 2:
        return False
    if entry.version == 1:
        # v1 talked to a Playwright sidecar; v2 talks to the portal directly.
        data = {k: v for k, v in entry.data.items() if k != CONF_AUTH_SERVICE_URL}
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True
