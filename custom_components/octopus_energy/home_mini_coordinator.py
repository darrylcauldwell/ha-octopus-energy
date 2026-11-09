"""Home Mini live telemetry coordinator for Octopus Energy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from aiooctopusenergy import (
    OctopusEnergyError,
    OctopusEnergyGraphQLClient,
    SmartMeterDevice,
    SmartMeterTelemetry,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Octopus rate-limits GraphQL per account and this is the most frequently
# polled query, so once a minute: demand stays current without crowding out
# the rate and comparison fetches.
HOME_MINI_UPDATE_INTERVAL = timedelta(minutes=1)
# Several readings wide, so one late upload from the Home Mini still leaves
# a recent reading in the window.
TELEMETRY_WINDOW = timedelta(minutes=3)


@dataclass(frozen=True)
class HomeMiniReading:
    """The latest complete Home Mini reading for one meter."""

    read_at: datetime
    demand_w: float
    consumption_kwh: float


def latest_complete_reading(
    telemetry: list[SmartMeterTelemetry],
) -> HomeMiniReading | None:
    """Newest reading carrying both demand and the register.

    A reading missing either is skipped rather than reported, so a gap never
    shows as zero demand or a falling register.
    """
    for item in sorted(telemetry, key=lambda t: t.read_at, reverse=True):
        if item.demand_w is not None and item.consumption_kwh is not None:
            return HomeMiniReading(item.read_at, item.demand_w, item.consumption_kwh)
    return None


class HomeMiniCoordinator(DataUpdateCoordinator[dict[str, HomeMiniReading]]):
    """Polls live readings for each meter paired with a Home Mini.

    Data is keyed by meter id (``{mpan}_{serial}``), matching the meter
    devices the main coordinator creates.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        graphql_client: OctopusEnergyGraphQLClient,
        devices: list[SmartMeterDevice],
    ) -> None:
        """Initialize the Home Mini coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_home_mini",
            update_interval=HOME_MINI_UPDATE_INTERVAL,
        )
        self._graphql_client = graphql_client
        self._devices = {f"{d.mpan}_{d.serial_number}": d for d in devices}

    @property
    def meter_ids(self) -> list[str]:
        """Meter ids of every smart import meter being polled."""
        return list(self._devices)

    async def _async_update_data(self) -> dict[str, HomeMiniReading]:
        """Fetch the last few minutes of telemetry for each device."""
        end = datetime.now(UTC)
        readings: dict[str, HomeMiniReading] = {}
        for meter_id, device in self._devices.items():
            try:
                telemetry = await self._graphql_client.get_smart_meter_telemetry(
                    device.device_id, start=end - TELEMETRY_WINDOW, end=end
                )
            except OctopusEnergyError as err:
                raise UpdateFailed(
                    f"Failed to fetch Home Mini telemetry: {err}"
                ) from err
            reading = latest_complete_reading(telemetry)
            if reading is not None:
                readings[meter_id] = reading
        return readings
