"""Tariff comparison coordinator for Octopus Energy."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging

from aiooctopusenergy import (
    OctopusEnergyClient,
    OctopusEnergyNotFoundError,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_COMPARISON_MONTHS,
    CONF_COMPARISON_PRODUCTS,
    DEFAULT_COMPARISON_MONTHS,
    DEFAULT_COMPARISON_PRODUCTS,
    DOMAIN,
)
from .coordinator import (
    OctopusEnergyConfigEntry,
    OctopusEnergyCoordinator,
    _build_tariff_code,
    _extract_gsp_suffix,
    _extract_product_code,
)

_LOGGER = logging.getLogger(__name__)

COMPARISON_UPDATE_INTERVAL = timedelta(hours=24)

# Display names for well-known products
PRODUCT_DISPLAY_NAMES: dict[str, str] = {
    "AGILE-24-10-01": "Agile Octopus",
    "VAR-22-11-01": "Flexible Octopus",
    "GO-VAR-22-10-14": "Octopus Go",
    "COSY-22-12-08": "Cosy Octopus",
    "SILVER-24-04-03": "12M Fixed",
}


@dataclass
class MonthlyTariffCost:
    """Cost breakdown for a single month on a single tariff."""

    month: str  # YYYY-MM format
    days_with_data: int
    days_in_month: int
    unit_cost: float  # GBP
    standing_cost: float  # GBP
    total_cost: float  # GBP
    consumption_kwh: float


@dataclass
class TariffComparison:
    """Comparison data for a single tariff."""

    product_code: str
    display_name: str
    tariff_code: str
    is_current: bool
    months: list[MonthlyTariffCost] = field(default_factory=list)
    total_cost: float = 0.0
    error: str | None = None


@dataclass
class TariffComparisonData:
    """Full comparison dataset."""

    tariffs: list[TariffComparison] = field(default_factory=list)
    months: list[str] = field(default_factory=list)  # YYYY-MM labels
    total_consumption_kwh: float = 0.0
    gsp_region: str = ""
    updated_at: str = ""


def _compute_monthly_costs(
    consumption_by_month: dict[str, list[tuple[datetime, datetime, float]]],
    rates: list,
    standing_charges: list,
    months: list[str],
) -> list[MonthlyTariffCost]:
    """Compute monthly costs by matching consumption to rates."""
    # Build sorted rate lookup (ascending by valid_from)
    sorted_rates = sorted(rates, key=lambda r: r.valid_from)
    monthly_costs: list[MonthlyTariffCost] = []

    for month_key in months:
        year, mon = map(int, month_key.split("-"))
        days_in_month = calendar.monthrange(year, mon)[1]
        readings = consumption_by_month.get(month_key, [])

        unit_cost_pence = 0.0
        total_kwh = 0.0
        days_seen: set[int] = set()

        for interval_start, interval_end, kwh in readings:
            days_seen.add(interval_start.day)
            total_kwh += kwh

            # Find matching rate for this interval
            matched_rate = None
            for rate in sorted_rates:
                if rate.valid_from <= interval_start and (
                    rate.valid_to is None or rate.valid_to >= interval_end
                ):
                    matched_rate = rate.value_inc_vat
                    break
            if matched_rate is None:
                # Try closest rate before interval
                for rate in reversed(sorted_rates):
                    if rate.valid_from <= interval_start:
                        matched_rate = rate.value_inc_vat
                        break
            if matched_rate is not None:
                unit_cost_pence += kwh * matched_rate

        # Standing charge: pence/day * days with data
        days_with_data = len(days_seen)
        standing_pence = 0.0
        if standing_charges:
            sc = standing_charges[0]  # Use most recent
            standing_pence = sc.value_inc_vat * days_with_data

        monthly_costs.append(
            MonthlyTariffCost(
                month=month_key,
                days_with_data=days_with_data,
                days_in_month=days_in_month,
                unit_cost=round(unit_cost_pence / 100.0, 2),
                standing_cost=round(standing_pence / 100.0, 2),
                total_cost=round((unit_cost_pence + standing_pence) / 100.0, 2),
                consumption_kwh=round(total_kwh, 2),
            )
        )

    return monthly_costs


class TariffComparisonCoordinator(DataUpdateCoordinator[TariffComparisonData]):
    """Coordinator that computes tariff cost comparisons."""

    config_entry: OctopusEnergyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: OctopusEnergyConfigEntry,
        client: OctopusEnergyClient,
        main_coordinator: OctopusEnergyCoordinator,
    ) -> None:
        """Initialize the comparison coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_comparison",
            update_interval=COMPARISON_UPDATE_INTERVAL,
        )
        self.client = client
        self._main = main_coordinator

    async def _async_update_data(self) -> TariffComparisonData:
        """Fetch consumption and re-price against comparison tariffs."""
        main_data = self._main.data
        if not main_data or not main_data.meters:
            return TariffComparisonData()

        # Find first import electricity meter
        import_meter = None
        for meter in main_data.meters.values():
            if not meter.is_gas and not meter.is_export:
                import_meter = meter
                break

        if import_meter is None:
            return TariffComparisonData()

        mpan = import_meter.meter_id.split("_")[0]
        serial = import_meter.serial_number
        gsp_suffix = _extract_gsp_suffix(import_meter.tariff_code)
        current_product = import_meter.product_code

        # Determine comparison period
        num_months = self.config_entry.options.get(
            CONF_COMPARISON_MONTHS, DEFAULT_COMPARISON_MONTHS
        )
        now = datetime.now(UTC)
        yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=1
        )

        # Start of period: first day of (now - num_months)
        start_month = now.month - num_months
        start_year = now.year
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        period_start = datetime(start_year, start_month, 1, tzinfo=UTC)

        # Generate month labels
        months: list[str] = []
        cursor = period_start
        while cursor <= yesterday:
            months.append(f"{cursor.year}-{cursor.month:02d}")
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)

        # Fetch all consumption for the period
        try:
            consumption = await self.client.get_electricity_consumption(
                mpan,
                serial,
                period_from=period_start,
                period_to=yesterday + timedelta(days=1),
                page_size=25000,
            )
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch consumption: {err}") from err

        if not consumption:
            return TariffComparisonData(
                months=months, gsp_region=gsp_suffix, updated_at=now.isoformat()
            )

        # Bucket consumption by month
        consumption_by_month: dict[str, list[tuple[datetime, datetime, float]]] = {}
        total_kwh = 0.0
        for reading in consumption:
            key = f"{reading.interval_start.year}-{reading.interval_start.month:02d}"
            consumption_by_month.setdefault(key, []).append(
                (reading.interval_start, reading.interval_end, reading.consumption)
            )
            total_kwh += reading.consumption

        # Build tariff list: current + configured comparison products
        comparison_products = self.config_entry.options.get(
            CONF_COMPARISON_PRODUCTS, DEFAULT_COMPARISON_PRODUCTS
        )
        product_codes: list[str] = [current_product]
        for code in comparison_products:
            if code != current_product:
                product_codes.append(code)

        tariffs: list[TariffComparison] = []

        for product_code in product_codes:
            is_current = product_code == current_product
            tariff_code = _build_tariff_code(product_code, gsp_suffix)
            display_name = PRODUCT_DISPLAY_NAMES.get(product_code, product_code)
            if is_current:
                display_name = f"{display_name} (current)"

            comparison = TariffComparison(
                product_code=product_code,
                display_name=display_name,
                tariff_code=tariff_code,
                is_current=is_current,
            )

            try:
                rates = await self.client.get_electricity_rates(
                    product_code,
                    tariff_code,
                    period_from=period_start,
                    period_to=yesterday + timedelta(days=1),
                    page_size=25000,
                )
                standing_charges = await self.client.get_electricity_standing_charges(
                    product_code,
                    tariff_code,
                    period_from=period_start,
                    period_to=yesterday + timedelta(days=1),
                    page_size=25000,
                )
            except OctopusEnergyNotFoundError:
                comparison.error = f"Product {product_code} not found for region {gsp_suffix}"
                _LOGGER.warning(
                    "Tariff %s not found for GSP %s, skipping",
                    product_code,
                    gsp_suffix,
                )
                tariffs.append(comparison)
                continue
            except Exception:
                comparison.error = f"Failed to fetch rates for {product_code}"
                _LOGGER.warning(
                    "Failed to fetch rates for %s, skipping",
                    product_code,
                    exc_info=True,
                )
                tariffs.append(comparison)
                continue

            monthly_costs = _compute_monthly_costs(
                consumption_by_month, rates, standing_charges, months
            )
            comparison.months = monthly_costs
            comparison.total_cost = round(
                sum(m.total_cost for m in monthly_costs), 2
            )
            tariffs.append(comparison)

        return TariffComparisonData(
            tariffs=tariffs,
            months=months,
            total_consumption_kwh=round(total_kwh, 2),
            gsp_region=gsp_suffix,
            updated_at=now.isoformat(),
        )
