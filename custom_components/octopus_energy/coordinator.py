"""Data update coordinator for Octopus Energy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
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

    async def _async_update_data(self) -> OctopusEnergyData:
        """Fetch data from the Octopus Energy API."""
        # Build meter list from account
        meters: dict[str, MeterData] = {}

        for prop in self._account.properties:
            for ep in prop.electricity_meter_points:
                agreement = _get_active_agreement(ep.agreements)
                if not agreement or not ep.meters:
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
                if not agreement or not gp.meters:
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

        # Fetch rates, consumption, and standing charges concurrently
        tasks = []
        meter_keys = list(meters.keys())

        for meter_id, meter in meters.items():
            if meter.is_gas:
                tasks.append(
                    self.client.get_gas_rates(
                        meter.product_code, meter.tariff_code
                    )
                )
            else:
                tasks.append(
                    self.client.get_electricity_rates(
                        meter.product_code, meter.tariff_code
                    )
                )

        for meter_id, meter in meters.items():
            # Consumption for yesterday
            yesterday_start = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=1)
            yesterday_end = yesterday_start + timedelta(days=1)

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

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
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

        previous = self.data
        n = len(meter_keys)

        # Parse results: first n = rates, next n = consumption, next n = standing charges
        for i, meter_id in enumerate(meter_keys):
            meter = meters[meter_id]
            prev_meter = previous.meters.get(meter_id) if previous else None

            rates_result = results[i]
            if isinstance(rates_result, BaseException):
                _LOGGER.warning("Failed to fetch rates for %s: %s", meter_id, rates_result)
                meter.rates = prev_meter.rates if prev_meter else []
            else:
                meter.rates = rates_result

            consumption_result = results[n + i]
            if isinstance(consumption_result, BaseException):
                _LOGGER.warning(
                    "Failed to fetch consumption for %s: %s",
                    meter_id,
                    consumption_result,
                )
                meter.consumption = prev_meter.consumption if prev_meter else []
            else:
                meter.consumption = consumption_result

            standing_result = results[2 * n + i]
            if isinstance(standing_result, BaseException):
                _LOGGER.warning(
                    "Failed to fetch standing charges for %s: %s",
                    meter_id,
                    standing_result,
                )
                meter.standing_charges = (
                    prev_meter.standing_charges if prev_meter else []
                )
            else:
                meter.standing_charges = standing_result

        return OctopusEnergyData(account=self._account, meters=meters)
