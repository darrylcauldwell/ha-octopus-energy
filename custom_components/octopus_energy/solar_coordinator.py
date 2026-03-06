"""Solar estimate coordinator for Octopus Energy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging

from aiooctopusenergy import OctopusEnergyGraphQLClient, SolarEstimate

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SOLAR_UPDATE_INTERVAL = timedelta(hours=6)


@dataclass
class SolarEstimateData:
    """Solar generation estimate data."""

    today_total_kwh: float = 0.0
    hourly_estimates: list[SolarEstimate] = field(default_factory=list)
    updated_at: str = ""


class SolarEstimateCoordinator(DataUpdateCoordinator[SolarEstimateData]):
    """Coordinator that fetches solar generation estimates."""

    def __init__(
        self,
        hass: HomeAssistant,
        graphql_client: OctopusEnergyGraphQLClient,
        postcode: str,
    ) -> None:
        """Initialize the solar estimate coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_solar",
            update_interval=SOLAR_UPDATE_INTERVAL,
        )
        self._graphql_client = graphql_client
        self._postcode = postcode

    async def _async_update_data(self) -> SolarEstimateData:
        """Fetch solar generation estimates from GraphQL API."""
        now = datetime.now(UTC)
        today_str = now.strftime("%Y-%m-%d")

        try:
            estimates = await self._graphql_client.get_solar_generation_estimate(
                self._postcode, from_date=now
            )
        except Exception as err:
            raise UpdateFailed(
                f"Failed to fetch solar estimates: {err}"
            ) from err

        # Filter to today's estimates and compute total
        today_estimates = [e for e in estimates if e.date == today_str]
        today_total = sum(e.value for e in today_estimates)

        return SolarEstimateData(
            today_total_kwh=round(today_total, 2),
            hourly_estimates=estimates,
            updated_at=now.isoformat(),
        )
