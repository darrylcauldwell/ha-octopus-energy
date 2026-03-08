"""Data update coordinator for Octopus Energy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .comparison_coordinator import TariffComparisonCoordinator
    from .solar_coordinator import SolarEstimateCoordinator

from aiooctopusenergy import (
    Account,
    Agreement,
    Consumption,
    OctopusEnergyClient,
    OctopusEnergyConnectionError,
    OctopusEnergyError,
    OctopusEnergyTimeoutError,
    Rate,
    StandingCharge,
)

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CARBON_INTENSITY_API_URL,
    CONF_ACCOUNT_NUMBER,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

type OctopusEnergyConfigEntry = ConfigEntry["OctopusEnergyRuntimeData"]


def _extract_product_code(tariff_code: str) -> str:
    """Extract product code from a tariff code.

    E.g. E-1R-AGILE-24-10-01-C -> AGILE-24-10-01
    or G-1R-VAR-22-11-01-C -> VAR-22-11-01
    """
    parts = tariff_code.split("-")
    # Remove prefix (E-1R- or G-1R-) and suffix (-C etc)
    if len(parts) >= 4:
        return "-".join(parts[2:-1])
    return tariff_code


def _extract_gsp_suffix(tariff_code: str) -> str:
    """Extract GSP region suffix from a tariff code.

    E.g. E-1R-AGILE-24-10-01-C -> C
    """
    return tariff_code.rsplit("-", 1)[-1] if "-" in tariff_code else tariff_code


def _build_tariff_code(product_code: str, gsp_suffix: str) -> str:
    """Build a tariff code from product code and GSP suffix.

    E.g. ("VAR-22-11-01", "C") -> E-1R-VAR-22-11-01-C
    """
    return f"E-1R-{product_code}-{gsp_suffix}"


def _get_active_agreement(
    agreements: list[Agreement],
) -> Agreement | None:
    """Get the currently active agreement."""
    now = datetime.now(UTC)
    for agreement in agreements:
        if agreement.valid_from <= now and (
            agreement.valid_to is None or agreement.valid_to > now
        ):
            return agreement
    return agreements[0] if agreements else None


@dataclass
class CarbonIntensityPeriod:
    """Carbon intensity data for a single half-hour period."""

    from_dt: datetime
    to_dt: datetime
    forecast: int
    actual: int | None
    index: str


@dataclass
class MeterData:
    """Data for a single meter."""

    meter_id: str
    serial_number: str
    tariff_code: str
    product_code: str
    is_export: bool
    is_gas: bool
    rates: list[Rate] = field(default_factory=list)
    consumption: list[Consumption] = field(default_factory=list)
    standing_charges: list[StandingCharge] = field(default_factory=list)


@dataclass
class OctopusEnergyData:
    """Data class for Octopus Energy coordinator."""

    account: Account
    meters: dict[str, MeterData] = field(default_factory=dict)
    carbon_intensity: list[CarbonIntensityPeriod] = field(default_factory=list)


@dataclass
class OctopusEnergyRuntimeData:
    """Runtime data for the Octopus Energy config entry."""

    coordinator: OctopusEnergyCoordinator
    comparison: TariffComparisonCoordinator
    solar: SolarEstimateCoordinator | None = None


class OctopusEnergyCoordinator(DataUpdateCoordinator[OctopusEnergyData]):
    """Coordinator for fetching Octopus Energy data."""

    config_entry: OctopusEnergyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: OctopusEnergyConfigEntry,
        client: OctopusEnergyClient,
        account: Account,
    ) -> None:
        """Initialize the coordinator."""
        interval = config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.client = client
        self._account = account
        self._session = async_get_clientsession(hass)
        # Smart cache tracking
        self._consumption_date: date | None = None
        self._standing_charges_date: date | None = None
        self._carbon_date: date | None = None
        self._rates_last_fetched: datetime | None = None

    def _should_fetch_rates(self, meter: MeterData, now: datetime) -> bool:
        """Determine whether rates need fetching for a meter."""
        previous = self.data
        if not previous:
            return True
        prev_meter = previous.meters.get(meter.meter_id)
        if not prev_meter or not prev_meter.rates:
            return True

        if meter.is_gas:
            if self._rates_last_fetched and self._rates_last_fetched.date() == now.date():
                return False
            return True

        tomorrow_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        has_tomorrow = any(r.valid_from >= tomorrow_midnight for r in prev_meter.rates)
        if has_tomorrow:
            return False
        if now.hour >= 16:
            return True
        if self._rates_last_fetched is None:
            return True
        return (now - self._rates_last_fetched) > timedelta(hours=4)

    async def _fetch_carbon_intensity(
        self, date: str
    ) -> list[CarbonIntensityPeriod]:
        """Fetch carbon intensity data for a date from the National Grid ESO API.

        Non-fatal: returns empty list on any failure.
        """
        url = CARBON_INTENSITY_API_URL.format(date=date)
        try:
            async with asyncio.timeout(10):
                resp = await self._session.get(url)
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Failed to fetch carbon intensity for %s: %s", date, err)
            return []

        periods: list[CarbonIntensityPeriod] = []
        for entry in data.get("data", []):
            try:
                intensity = entry["intensity"]
                periods.append(
                    CarbonIntensityPeriod(
                        from_dt=datetime.fromisoformat(
                            entry["from"].replace("Z", "+00:00")
                        ),
                        to_dt=datetime.fromisoformat(
                            entry["to"].replace("Z", "+00:00")
                        ),
                        forecast=intensity["forecast"],
                        actual=intensity.get("actual"),
                        index=intensity.get("index", "unknown"),
                    )
                )
            except (KeyError, ValueError) as err:
                _LOGGER.debug("Skipping malformed carbon entry: %s", err)
        return periods

    async def _async_update_data(self) -> OctopusEnergyData:
        """Fetch data from the Octopus Energy API with smart caching."""
        now = datetime.now(UTC)
        yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=1
        )
        today = now.date()
        previous = self.data

        fetch_consumption = self._consumption_date != yesterday.date()
        fetch_standing = self._standing_charges_date != today
        fetch_carbon = self._carbon_date != yesterday.date()

        # Build meter list from account
        meters: dict[str, MeterData] = {}

        for prop in self._account.properties:
            for ep in prop.electricity_meter_points:
                agreement = _get_active_agreement(ep.agreements)
                if not agreement or not ep.meters or not agreement.tariff_code:
                    continue
                tariff_code = agreement.tariff_code
                product_code = _extract_product_code(tariff_code)
                meter_id = f"{ep.mpan}_{ep.meters[0].serial_number}"
                meters[meter_id] = MeterData(
                    meter_id=meter_id,
                    serial_number=ep.meters[0].serial_number,
                    tariff_code=tariff_code,
                    product_code=product_code,
                    is_export=ep.is_export,
                    is_gas=False,
                )

            for gp in prop.gas_meter_points:
                agreement = _get_active_agreement(gp.agreements)
                if not agreement or not gp.meters or not agreement.tariff_code:
                    continue
                tariff_code = agreement.tariff_code
                product_code = _extract_product_code(tariff_code)
                meter_id = f"{gp.mprn}_{gp.meters[0].serial_number}"
                meters[meter_id] = MeterData(
                    meter_id=meter_id,
                    serial_number=gp.meters[0].serial_number,
                    tariff_code=tariff_code,
                    product_code=product_code,
                    is_export=False,
                    is_gas=True,
                )

        # Build task lists with tracking
        tasks: list = []
        task_map: list[tuple[str, str]] = []  # (meter_id, category)

        for meter_id, meter in meters.items():
            if not self._should_fetch_rates(meter, now):
                _LOGGER.debug("Cache hit: rates for %s", meter_id)
                continue
            if meter.is_gas:
                tasks.append(
                    self.client.get_gas_rates(meter.product_code, meter.tariff_code)
                )
            else:
                tasks.append(
                    self.client.get_electricity_rates(
                        meter.product_code, meter.tariff_code
                    )
                )
            task_map.append((meter_id, "rates"))

        if fetch_consumption:
            yesterday_start = yesterday
            yesterday_end = yesterday_start + timedelta(days=1)
            for meter_id, meter in meters.items():
                if meter.is_gas:
                    tasks.append(
                        self.client.get_gas_consumption(
                            meter_id.split("_")[0],
                            meter.serial_number,
                            period_from=yesterday_start,
                            period_to=yesterday_end,
                        )
                    )
                else:
                    tasks.append(
                        self.client.get_electricity_consumption(
                            meter_id.split("_")[0],
                            meter.serial_number,
                            period_from=yesterday_start,
                            period_to=yesterday_end,
                        )
                    )
                task_map.append((meter_id, "consumption"))
        else:
            _LOGGER.debug("Cache hit: consumption")

        if fetch_standing:
            for meter_id, meter in meters.items():
                if meter.is_gas:
                    tasks.append(
                        self.client.get_gas_standing_charges(
                            meter.product_code, meter.tariff_code
                        )
                    )
                else:
                    tasks.append(
                        self.client.get_electricity_standing_charges(
                            meter.product_code, meter.tariff_code
                        )
                    )
                task_map.append((meter_id, "standing"))
        else:
            _LOGGER.debug("Cache hit: standing charges")

        categories = {cat for _, cat in task_map}
        skipped = {"rates", "consumption", "standing"} - categories
        _LOGGER.debug(
            "API calls this cycle: %d (fetching=%s, skipped=%s)",
            len(tasks),
            sorted(categories) if categories else "none",
            sorted(skipped) if skipped else "none",
        )

        # Pre-populate from previous data for skipped categories
        fetching_rates = {mid for mid, cat in task_map if cat == "rates"}
        for meter_id, meter in meters.items():
            prev_meter = previous.meters.get(meter_id) if previous else None
            if prev_meter:
                if not fetch_consumption:
                    meter.consumption = prev_meter.consumption
                if not fetch_standing:
                    meter.standing_charges = prev_meter.standing_charges
                if meter_id not in fetching_rates:
                    meter.rates = prev_meter.rates

        # Carbon intensity — once per calendar day
        carbon: list[CarbonIntensityPeriod] = []
        if fetch_carbon:
            yesterday_str = yesterday.strftime("%Y-%m-%d")
            carbon = await self._fetch_carbon_intensity(yesterday_str)
            if carbon:
                self._carbon_date = yesterday.date()
            elif previous:
                carbon = previous.carbon_intensity
        else:
            _LOGGER.debug("Cache hit: carbon intensity")
            carbon = previous.carbon_intensity if previous else []

        if not tasks:
            return OctopusEnergyData(
                account=self._account, meters=meters, carbon_intensity=carbon
            )

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cancelled_error",
            ) from err
        except OctopusEnergyTimeoutError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="timeout_error",
            ) from err
        except OctopusEnergyConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="connection_error",
            ) from err
        except OctopusEnergyError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_error",
                translation_placeholders={"error": str(err)},
            ) from err

        consumption_ok = True
        standing_ok = True
        for i, (meter_id, category) in enumerate(task_map):
            meter = meters[meter_id]
            prev_meter = previous.meters.get(meter_id) if previous else None
            result = results[i]

            if isinstance(result, BaseException):
                _LOGGER.warning(
                    "Failed to fetch %s for %s: %s", category, meter_id, result
                )
                if category == "rates":
                    meter.rates = prev_meter.rates if prev_meter else []
                elif category == "consumption":
                    meter.consumption = prev_meter.consumption if prev_meter else []
                    consumption_ok = False
                elif category == "standing":
                    meter.standing_charges = (
                        prev_meter.standing_charges if prev_meter else []
                    )
                    standing_ok = False
            else:
                if category == "rates":
                    meter.rates = result
                elif category == "consumption":
                    meter.consumption = result
                elif category == "standing":
                    meter.standing_charges = result

        if fetch_consumption and consumption_ok:
            self._consumption_date = yesterday.date()
        if fetch_standing and standing_ok:
            self._standing_charges_date = today
        if categories & {"rates"}:
            self._rates_last_fetched = now

        return OctopusEnergyData(
            account=self._account, meters=meters, carbon_intensity=carbon
        )
