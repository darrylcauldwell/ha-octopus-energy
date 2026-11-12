"""Home Mini live telemetry coordinator for Octopus Energy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from aiooctopusenergy import (
    OctopusEnergyError,
    OctopusEnergyGraphQLClient,
    SmartGasMeterDevice,
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
# A gas meter uploads only once every 30 minutes, so polling every 5 shows
# each half-hour soon after it lands at little GraphQL cost.
GAS_UPDATE_INTERVAL = timedelta(minutes=5)
# Several uploads wide, so one missed upload still leaves a reading.
GAS_TELEMETRY_WINDOW = timedelta(hours=2)
HALF_HOUR = timedelta(minutes=30)


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


@dataclass(frozen=True)
class GasHalfHour:
    """The latest half-hour of gas reported by the Home Mini for one meter."""

    read_at: datetime
    period_start: datetime
    period_end: datetime
    consumption_kwh: float
    """Cumulative meter register."""
    half_hour_kwh: float


def latest_gas_half_hour(
    telemetry: list[SmartMeterTelemetry],
) -> GasHalfHour | None:
    """Newest gas reading carrying both the register and its half-hour.

    An upload's delta is the half-hour that ended at the last :00 or :30
    before read_at, so the period is placed there, not in the 30 minutes up
    to read_at.
    """
    for item in sorted(telemetry, key=lambda t: t.read_at, reverse=True):
        if item.consumption_kwh is None or item.consumption_delta_kwh is None:
            continue
        period_end = item.read_at.replace(
            minute=item.read_at.minute // 30 * 30, second=0, microsecond=0
        )
        return GasHalfHour(
            read_at=item.read_at,
            period_start=period_end - HALF_HOUR,
            period_end=period_end,
            consumption_kwh=item.consumption_kwh,
            half_hour_kwh=item.consumption_delta_kwh,
        )
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


class HomeMiniGasCoordinator(DataUpdateCoordinator[dict[str, GasHalfHour]]):
    """Polls the latest half-hour for each gas meter paired with a Home Mini.

    Data is keyed by meter id (``{mprn}_{serial}``), matching the gas meter
    devices the main coordinator creates.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        graphql_client: OctopusEnergyGraphQLClient,
        devices: list[SmartGasMeterDevice],
    ) -> None:
        """Initialize the Home Mini gas coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_home_mini_gas",
            update_interval=GAS_UPDATE_INTERVAL,
        )
        self._graphql_client = graphql_client
        self._devices = {f"{d.mprn}_{d.serial_number}": d for d in devices}

    @property
    def meter_ids(self) -> list[str]:
        """Meter ids of every smart gas meter being polled."""
        return list(self._devices)

    async def _async_update_data(self) -> dict[str, GasHalfHour]:
        """Fetch the last couple of hours of uploads for each gas meter."""
        end = datetime.now(UTC)
        readings: dict[str, GasHalfHour] = {}
        for meter_id, device in self._devices.items():
            try:
                telemetry = await self._graphql_client.get_smart_meter_telemetry(
                    device.device_id, start=end - GAS_TELEMETRY_WINDOW, end=end
                )
            except OctopusEnergyError as err:
                raise UpdateFailed(
                    f"Failed to fetch Home Mini gas telemetry: {err}"
                ) from err
            half_hour = latest_gas_half_hour(telemetry)
            if half_hour is not None:
                readings[meter_id] = half_hour
        return readings
