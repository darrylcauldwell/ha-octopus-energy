"""The Octopus Energy integration."""

from __future__ import annotations

from aiooctopusenergy import OctopusEnergyClient, OctopusEnergyConnectionError

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ACCOUNT_NUMBER, CONF_API_KEY, DOMAIN
from .coordinator import OctopusEnergyConfigEntry, OctopusEnergyCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: OctopusEnergyConfigEntry
) -> bool:
    """Set up Octopus Energy from a config entry."""
    session = async_get_clientsession(hass)
    client = OctopusEnergyClient(
        api_key=entry.data[CONF_API_KEY], session=session
    )

    # Validate connectivity and fetch account data
    try:
        account = await client.get_account(entry.data[CONF_ACCOUNT_NUMBER])
    except OctopusEnergyConnectionError as err:
        raise ConfigEntryNotReady from err

    coordinator = OctopusEnergyCoordinator(hass, entry, client, account)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Reload when options change (update interval or API key)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: OctopusEnergyConfigEntry
) -> None:
    """Reload integration when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: OctopusEnergyConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
