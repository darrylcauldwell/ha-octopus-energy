"""Tariff comparison coordinator for Octopus Energy."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging

from aiooctopusenergy import (
    OctopusEnergyClient,
    OctopusEnergyGraphQLClient,
    OctopusEnergyNotFoundError,
    Rate,
    TariffCostComparison,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ACCOUNT_NUMBER,
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
    octopus_current_cost: float | None = None
    octopus_comparisons: list[TariffCostComparison] | None = None


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


def _find_missing_ranges(
    expected_dates: list[date], cached_keys: set[str]
) -> list[tuple[date, date]]:
    """Find contiguous date ranges not present in cache.

    Returns list of (start_date, end_date_exclusive) tuples.
    """
    missing = sorted(d for d in expected_dates if d.isoformat() not in cached_keys)
    if not missing:
        return []

    ranges: list[tuple[date, date]] = []
    range_start = missing[0]
    prev = missing[0]

    for d in missing[1:]:
        if (d - prev).days == 1:
            prev = d
        else:
            ranges.append((range_start, prev + timedelta(days=1)))
            range_start = d
            prev = d

    ranges.append((range_start, prev + timedelta(days=1)))
    return ranges


class TariffComparisonCoordinator(DataUpdateCoordinator[TariffComparisonData]):
    """Coordinator that computes tariff cost comparisons."""

    config_entry: OctopusEnergyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: OctopusEnergyConfigEntry,
        client: OctopusEnergyClient,
        main_coordinator: OctopusEnergyCoordinator,
        graphql_client: OctopusEnergyGraphQLClient,
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
        self._graphql_client = graphql_client
        # Incremental cache
        self._cached_consumption: dict[str, list[tuple[datetime, datetime, float]]] = {}
        self._cached_rates: dict[str, list] = {}
        self._cached_standing: dict[str, list] = {}

    async def _get_cached_rates(
        self, product_code: str, tariff_code: str,
        period_start: datetime, period_end: datetime,
    ) -> list:
        """Return cached rates or fetch and cache them."""
        if product_code in self._cached_rates:
            return self._cached_rates[product_code]
        rates = await self.client.get_electricity_rates(
            product_code, tariff_code,
            period_from=period_start, period_to=period_end, page_size=25000,
        )
        self._cached_rates[product_code] = rates
        return rates

    async def _get_cached_standing(
        self, product_code: str, tariff_code: str,
        period_start: datetime, period_end: datetime,
    ) -> list:
        """Return cached standing charges or fetch and cache them."""
        if product_code in self._cached_standing:
            return self._cached_standing[product_code]
        charges = await self.client.get_electricity_standing_charges(
            product_code, tariff_code,
            period_from=period_start, period_to=period_end, page_size=25000,
        )
        self._cached_standing[product_code] = charges
        return charges

    async def _async_update_data(self) -> TariffComparisonData:
        """Fetch consumption and re-price against comparison tariffs."""
        main_data = self._main.data
        if not main_data or not main_data.meters:
            return TariffComparisonData()

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
        account_number = self.config_entry.data[CONF_ACCOUNT_NUMBER]

        num_months = self.config_entry.options.get(
            CONF_COMPARISON_MONTHS, DEFAULT_COMPARISON_MONTHS
        )
        now = datetime.now(UTC)
        yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=1
        )

        start_month = now.month - num_months
        start_year = now.year
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        period_start = datetime(start_year, start_month, 1, tzinfo=UTC)

        months: list[str] = []
        cursor = period_start
        while cursor <= yesterday:
            months.append(f"{cursor.year}-{cursor.month:02d}")
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)

        # Incremental consumption fetch
        expected_dates: list[date] = []
        d = period_start.date()
        while d <= yesterday.date():
            expected_dates.append(d)
            d += timedelta(days=1)

        valid_keys = {d.isoformat() for d in expected_dates}
        stale = set(self._cached_consumption.keys()) - valid_keys
        for key in stale:
            del self._cached_consumption[key]

        missing_ranges = _find_missing_ranges(
            expected_dates, set(self._cached_consumption.keys())
        )

        _LOGGER.debug(
            "Consumption cache: %d cached days, %d missing ranges",
            len(self._cached_consumption),
            len(missing_ranges),
        )

        for range_start, range_end in missing_ranges:
            try:
                consumption = await self.client.get_electricity_consumption(
                    mpan, serial,
                    period_from=datetime(
                        range_start.year, range_start.month, range_start.day,
                        tzinfo=UTC,
                    ),
                    period_to=datetime(
                        range_end.year, range_end.month, range_end.day,
                        tzinfo=UTC,
                    ),
                    page_size=25000,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to fetch consumption %s to %s: %s",
                    range_start, range_end, err,
                )
                continue

            for reading in consumption:
                day_key = reading.interval_start.date().isoformat()
                self._cached_consumption.setdefault(day_key, []).append(
                    (reading.interval_start, reading.interval_end, reading.consumption)
                )

        consumption_by_month: dict[str, list[tuple[datetime, datetime, float]]] = {}
        total_kwh = 0.0
        for day_key, readings in self._cached_consumption.items():
            for interval_start, interval_end, kwh in readings:
                month_key = f"{interval_start.year}-{interval_start.month:02d}"
                consumption_by_month.setdefault(month_key, []).append(
                    (interval_start, interval_end, kwh)
                )
                total_kwh += kwh

        if not consumption_by_month:
            return TariffComparisonData(
                months=months, gsp_region=gsp_suffix, updated_at=now.isoformat()
            )

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

            period_end = yesterday + timedelta(days=1)

            # For the current tariff, try GraphQL applicable rates first
            rates = None
            if is_current:
                try:
                    applicable = await self._graphql_client.get_applicable_rates(
                        account_number,
                        mpan,
                        start_at=period_start,
                        end_at=period_end,
                    )
                    rates = [
                        Rate(
                            value_exc_vat=0.0,
                            value_inc_vat=ar.value_inc_vat,
                            valid_from=ar.valid_from,
                            valid_to=ar.valid_to,
                        )
                        for ar in applicable
                    ]
                    _LOGGER.debug(
                        "Using GraphQL applicable rates for current tariff (%d rates)",
                        len(rates),
                    )
                except Exception:
                    _LOGGER.warning(
                        "GraphQL applicable rates failed, falling back to REST",
                        exc_info=True,
                    )

            # Fall back to cached REST rates
            if rates is None:
                try:
                    rates = await self._get_cached_rates(
                        product_code, tariff_code, period_start, period_end
                    )
                except OctopusEnergyNotFoundError:
                    comparison.error = (
                        f"Product {product_code} not found for region {gsp_suffix}"
                    )
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

            try:
                standing_charges = await self._get_cached_standing(
                    product_code, tariff_code, period_start, period_end
                )
            except Exception:
                standing_charges = []
                _LOGGER.warning(
                    "Failed to fetch standing charges for %s",
                    product_code,
                    exc_info=True,
                )

            monthly_costs = _compute_monthly_costs(
                consumption_by_month, rates, standing_charges, months
            )
            comparison.months = monthly_costs
            comparison.total_cost = round(
                sum(m.total_cost for m in monthly_costs), 2
            )
            tariffs.append(comparison)

        # Fetch Octopus smart tariff comparison
        octopus_current_cost: float | None = None
        octopus_comparisons: list[TariffCostComparison] | None = None
        try:
            smart = await self._graphql_client.get_smart_tariff_comparison(
                account_number=account_number, mpan=mpan
            )
            octopus_current_cost = smart.get("current_cost")
            octopus_comparisons = smart.get("comparisons")
        except Exception:
            _LOGGER.warning(
                "Smart tariff comparison unavailable", exc_info=True
            )

        return TariffComparisonData(
            tariffs=tariffs,
            months=months,
            total_consumption_kwh=round(total_kwh, 2),
            gsp_region=gsp_suffix,
            updated_at=now.isoformat(),
            octopus_current_cost=octopus_current_cost,
            octopus_comparisons=octopus_comparisons,
        )
