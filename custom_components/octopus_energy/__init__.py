"""The Octopus Energy integration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import logging

from aiooctopusenergy import (
    OctopusEnergyClient,
    OctopusEnergyConnectionError,
    OctopusEnergyError,
    OctopusEnergyGraphQLClient,
)

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .comparison_coordinator import TariffComparisonCoordinator
from .const import CONF_ACCOUNT_NUMBER, CONF_API_KEY, CONF_POSTCODE, DOMAIN
from .home_mini_coordinator import HomeMiniCoordinator
from .solar_coordinator import SolarEstimateCoordinator
from .coordinator import (
    OctopusEnergyConfigEntry,
    OctopusEnergyCoordinator,
    OctopusEnergyRuntimeData,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

URL_BASE = "/octopus_energy"
CARD_URL = f"{URL_BASE}/octopus-tariff-comparison-card.js"


async def async_setup_entry(
    hass: HomeAssistant, entry: OctopusEnergyConfigEntry
) -> bool:
    """Set up Octopus Energy from a config entry."""
    session = async_get_clientsession(hass)
    api_key = entry.data[CONF_API_KEY]
    client = OctopusEnergyClient(api_key=api_key, session=session)

    # Validate connectivity and fetch account data
    try:
        account = await client.get_account(entry.data[CONF_ACCOUNT_NUMBER])
    except (OctopusEnergyConnectionError, OctopusEnergyError) as err:
        raise ConfigEntryNotReady from err

    coordinator = OctopusEnergyCoordinator(hass, entry, client, account)
    await coordinator.async_load_cache()
    await coordinator.async_config_entry_first_refresh()

    # Always create GraphQL client (used by comparison, solar and Home Mini)
    graphql_client = OctopusEnergyGraphQLClient(
        api_key=api_key, session=session
    )

    # Stagger secondary coordinators to avoid triggering API rate limits
    # on restart.  The main coordinator fires first (above), then we wait
    # before starting comparison, solar and Home Mini fetches.
    comparison = TariffComparisonCoordinator(
        hass, entry, client, coordinator, graphql_client
    )
    await comparison.async_load_cache()
    await asyncio.sleep(10)
    await comparison.async_refresh()

    solar: SolarEstimateCoordinator | None = None
    postcode = entry.options.get(CONF_POSTCODE)
    if postcode:
        solar = SolarEstimateCoordinator(hass, graphql_client, postcode)
        await asyncio.sleep(10)
        await solar.async_refresh()

    home_mini = await _async_setup_home_mini(hass, entry, graphql_client)

    entry.runtime_data = OctopusEnergyRuntimeData(
        coordinator=coordinator,
        comparison=comparison,
        solar=solar,
        home_mini=home_mini,
    )

    # Register custom card
    await _async_register_card(hass)

    # Reload when options change (update interval or API key)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_home_mini(
    hass: HomeAssistant,
    entry: OctopusEnergyConfigEntry,
    graphql_client: OctopusEnergyGraphQLClient,
) -> HomeMiniCoordinator | None:
    """Start Home Mini polling if the account has a smart import meter.

    Optional: a failed lookup leaves the rest of the integration running
    without live readings rather than failing setup.
    """
    try:
        devices = await graphql_client.get_smart_meter_devices(
            entry.data[CONF_ACCOUNT_NUMBER]
        )
    except OctopusEnergyError as err:
        _LOGGER.warning("Smart meter lookup failed, no Home Mini sensors: %s", err)
        return None
    if not devices:
        return None
    home_mini = HomeMiniCoordinator(hass, entry, graphql_client, devices)
    await asyncio.sleep(10)
    await home_mini.async_refresh()
    return home_mini


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the custom Lovelace card."""
    hass.data.setdefault(DOMAIN + "_frontend", False)
    if hass.data[DOMAIN + "_frontend"]:
        return
    hass.data[DOMAIN + "_frontend"] = True

    frontend_path = str(Path(__file__).parent / "frontend")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, frontend_path, False)]
    )
    add_extra_js_url(hass, CARD_URL)


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
