"""Tests for tariff comparison coordinator."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import time

import pytest

from aiooctopusenergy import (
    ApplicableRate,
    Consumption,
    Rate,
    StandingCharge,
    TariffCostComparison,
)

from custom_components.octopus_energy.comparison_coordinator import (
    MonthlyTariffCost,
    SlotCost,
    TariffComparisonData,
    _compute_monthly_costs,
    _find_missing_ranges,
)
from custom_components.octopus_energy.coordinator import (
    _build_tariff_code,
    _extract_gsp_suffix,
)


class TestExtractGspSuffix:
    def test_agile_tariff(self):
        assert _extract_gsp_suffix("E-1R-AGILE-24-10-01-C") == "C"

    def test_variable_tariff(self):
        assert _extract_gsp_suffix("G-1R-VAR-22-11-01-A") == "A"

    def test_go_tariff(self):
        assert _extract_gsp_suffix("E-1R-GO-VAR-22-10-14-P") == "P"

    def test_no_hyphen(self):
        assert _extract_gsp_suffix("NOTARIFF") == "NOTARIFF"


class TestBuildTariffCode:
    def test_agile(self):
        assert _build_tariff_code("AGILE-24-10-01", "C") == "E-1R-AGILE-24-10-01-C"

    def test_variable(self):
        assert _build_tariff_code("VAR-22-11-01", "A") == "E-1R-VAR-22-11-01-A"

    def test_go(self):
        assert _build_tariff_code("GO-VAR-22-10-14", "P") == "E-1R-GO-VAR-22-10-14-P"


class TestComputeMonthlyCostFlatRate:
    def test_single_month_flat_rate(self):
        """Flat rate: 10p/kWh, 1kWh consumption = 10p = £0.10 unit cost."""
        month_start = datetime(2025, 10, 1, tzinfo=UTC)

        consumption_by_month = {
            "2025-10": [
                (
                    month_start,
                    month_start + timedelta(minutes=30),
                    1.0,
                ),
            ],
        }

        rates = [
            Rate(
                value_exc_vat=9.52,
                value_inc_vat=10.0,
                valid_from=datetime(2025, 10, 1, tzinfo=UTC),
                valid_to=None,
            ),
        ]

        standing_charges = [
            StandingCharge(
                value_exc_vat=28.57,
                value_inc_vat=30.0,
                valid_from=datetime(2025, 10, 1, tzinfo=UTC),
                valid_to=None,
            ),
        ]

        result = _compute_monthly_costs(
            consumption_by_month, rates, standing_charges, ["2025-10"]
        )

        assert len(result) == 1
        m = result[0]
        assert m.month == "2025-10"
        assert m.days_with_data == 1
        assert m.days_in_month == 31
        assert m.unit_cost == 0.10  # 1kWh * 10p = 10p = £0.10
        assert m.standing_cost == 0.30  # 30p * 1 day = 30p = £0.30
        assert m.total_cost == 0.40
        assert m.consumption_kwh == 1.0
        assert m.slots == []

    def test_single_month_with_slots(self):
        """When include_slots=True, per-slot data is retained."""
        month_start = datetime(2025, 10, 1, tzinfo=UTC)

        consumption_by_month = {
            "2025-10": [
                (month_start, month_start + timedelta(minutes=30), 1.0),
            ],
        }

        rates = [
            Rate(
                value_exc_vat=9.52,
                value_inc_vat=10.0,
                valid_from=datetime(2025, 10, 1, tzinfo=UTC),
                valid_to=None,
            ),
        ]

        result = _compute_monthly_costs(
            consumption_by_month, rates, [], ["2025-10"], include_slots=True
        )

        m = result[0]
        assert len(m.slots) == 1
        s = m.slots[0]
        assert s.start == month_start.isoformat()
        assert s.consumption_kwh == 1.0
        assert s.rate == 10.0
        assert s.cost == 0.1  # 1.0 kWh * 10p / 100 = £0.10


class TestSlotDataVariableRates:
    def test_slots_capture_per_interval_rate(self):
        """Slots record the correct rate for each half-hour."""
        t0 = datetime(2025, 10, 1, 0, 0, tzinfo=UTC)
        t1 = datetime(2025, 10, 1, 0, 30, tzinfo=UTC)
        t2 = datetime(2025, 10, 1, 1, 0, tzinfo=UTC)

        consumption_by_month = {
            "2025-10": [
                (t0, t1, 0.5),
                (t1, t2, 0.3),
            ],
        }

        rates = [
            Rate(value_exc_vat=0, value_inc_vat=20.0, valid_from=t0, valid_to=t1),
            Rate(value_exc_vat=0, value_inc_vat=10.0, valid_from=t1, valid_to=t2),
        ]

        result = _compute_monthly_costs(
            consumption_by_month, rates, [], ["2025-10"], include_slots=True
        )

        m = result[0]
        assert len(m.slots) == 2
        assert m.slots[0].rate == 20.0
        assert m.slots[0].consumption_kwh == 0.5
        assert m.slots[0].cost == 0.1  # 0.5 * 20 / 100 = £0.10
        assert m.slots[1].rate == 10.0
        assert m.slots[1].consumption_kwh == 0.3

    def test_slots_not_included_by_default(self):
        """Without include_slots, slots list is empty."""
        t0 = datetime(2025, 10, 1, 0, 0, tzinfo=UTC)
        t1 = datetime(2025, 10, 1, 0, 30, tzinfo=UTC)

        consumption_by_month = {
            "2025-10": [(t0, t1, 1.0)],
        }

        rates = [
            Rate(value_exc_vat=0, value_inc_vat=10.0, valid_from=t0, valid_to=t1),
        ]

        result = _compute_monthly_costs(
            consumption_by_month, rates, [], ["2025-10"]
        )

        assert result[0].slots == []


class TestComputeMonthlyCostVariableRate:
    def test_multiple_rates(self):
        """Variable rates: 2 readings at different rates."""
        month_start = datetime(2025, 11, 1, tzinfo=UTC)
        mid = month_start + timedelta(minutes=30)

        consumption_by_month = {
            "2025-11": [
                (month_start, mid, 0.5),  # 0.5 kWh at 20p/kWh
                (mid, mid + timedelta(minutes=30), 0.3),  # 0.3 kWh at 10p/kWh
            ],
        }

        rates = [
            Rate(
                value_exc_vat=19.05,
                value_inc_vat=20.0,
                valid_from=month_start,
                valid_to=mid,
            ),
            Rate(
                value_exc_vat=9.52,
                value_inc_vat=10.0,
                valid_from=mid,
                valid_to=mid + timedelta(minutes=30),
            ),
        ]

        standing_charges = [
            StandingCharge(
                value_exc_vat=28.57,
                value_inc_vat=30.0,
                valid_from=month_start,
                valid_to=None,
            ),
        ]

        result = _compute_monthly_costs(
            consumption_by_month, rates, standing_charges, ["2025-11"]
        )

        m = result[0]
        # Unit cost: 0.5*20 + 0.3*10 = 10 + 3 = 13p = £0.13
        assert m.unit_cost == 0.13
        assert m.consumption_kwh == 0.8


class TestMissingConsumption:
    def test_empty_month(self):
        """Month with no consumption data returns zero costs."""
        result = _compute_monthly_costs({}, [], [], ["2025-12"])

        assert len(result) == 1
        m = result[0]
        assert m.days_with_data == 0
        assert m.unit_cost == 0.0
        assert m.standing_cost == 0.0
        assert m.total_cost == 0.0
        assert m.consumption_kwh == 0.0

    def test_no_standing_charges(self):
        """Consumption without standing charges still computes unit cost."""
        month_start = datetime(2025, 10, 1, tzinfo=UTC)
        consumption_by_month = {
            "2025-10": [
                (month_start, month_start + timedelta(minutes=30), 1.0),
            ],
        }
        rates = [
            Rate(
                value_exc_vat=9.52,
                value_inc_vat=10.0,
                valid_from=month_start,
                valid_to=None,
            ),
        ]

        result = _compute_monthly_costs(
            consumption_by_month, rates, [], ["2025-10"]
        )

        m = result[0]
        assert m.unit_cost == 0.10
        assert m.standing_cost == 0.0
        assert m.total_cost == 0.10


class TestApplicableRateConversion:
    """Test that GraphQL ApplicableRate objects convert to Rate for cost computation."""

    def test_applicable_rates_as_rate_objects(self):
        """ApplicableRate converted to Rate with value_exc_vat=0 works in cost calc."""
        month_start = datetime(2025, 10, 1, tzinfo=UTC)

        applicable_rates = [
            ApplicableRate(
                value_inc_vat=15.0,
                valid_from=month_start,
                valid_to=month_start + timedelta(minutes=30),
            ),
        ]

        # Convert ApplicableRate to Rate (as the coordinator does)
        rates = [
            Rate(
                value_exc_vat=0.0,
                value_inc_vat=ar.value_inc_vat,
                valid_from=ar.valid_from,
                valid_to=ar.valid_to,
            )
            for ar in applicable_rates
        ]

        consumption_by_month = {
            "2025-10": [
                (month_start, month_start + timedelta(minutes=30), 2.0),
            ],
        }

        result = _compute_monthly_costs(
            consumption_by_month, rates, [], ["2025-10"]
        )

        m = result[0]
        # 2.0 kWh * 15p/kWh = 30p = £0.30
        assert m.unit_cost == 0.30
        assert m.consumption_kwh == 2.0

    def test_multiple_applicable_rates(self):
        """Multiple half-hourly applicable rates match correctly."""
        t0 = datetime(2025, 10, 1, 0, 0, tzinfo=UTC)
        t1 = datetime(2025, 10, 1, 0, 30, tzinfo=UTC)
        t2 = datetime(2025, 10, 1, 1, 0, tzinfo=UTC)

        applicable_rates = [
            ApplicableRate(value_inc_vat=5.0, valid_from=t0, valid_to=t1),
            ApplicableRate(value_inc_vat=25.0, valid_from=t1, valid_to=t2),
        ]

        rates = [
            Rate(
                value_exc_vat=0.0,
                value_inc_vat=ar.value_inc_vat,
                valid_from=ar.valid_from,
                valid_to=ar.valid_to,
            )
            for ar in applicable_rates
        ]

        consumption_by_month = {
            "2025-10": [
                (t0, t1, 1.0),  # 1 kWh at 5p
                (t1, t2, 1.0),  # 1 kWh at 25p
            ],
        }

        result = _compute_monthly_costs(
            consumption_by_month, rates, [], ["2025-10"]
        )

        m = result[0]
        # 1*5 + 1*25 = 30p = £0.30
        assert m.unit_cost == 0.30


class TestTariffComparisonDataSmartComparison:
    """Test TariffComparisonData stores smart comparison fields."""

    def test_default_smart_comparison_fields(self):
        """Smart comparison fields default to None."""
        data = TariffComparisonData()
        assert data.octopus_current_cost is None
        assert data.octopus_comparisons is None

    def test_stores_smart_comparison(self):
        """Smart comparison data is stored correctly."""
        comparisons = [
            TariffCostComparison(
                tariff_code="E-1R-AGILE-24-10-01-C",
                product_code="AGILE-24-10-01",
                cost_inc_vat=150.50,
            ),
            TariffCostComparison(
                tariff_code="E-1R-VAR-22-11-01-C",
                product_code="VAR-22-11-01",
                cost_inc_vat=180.25,
            ),
        ]

        data = TariffComparisonData(
            octopus_current_cost=165.00,
            octopus_comparisons=comparisons,
        )

        assert data.octopus_current_cost == 165.00
        assert len(data.octopus_comparisons) == 2
        assert data.octopus_comparisons[0].product_code == "AGILE-24-10-01"
        assert data.octopus_comparisons[0].cost_inc_vat == 150.50
        assert data.octopus_comparisons[1].product_code == "VAR-22-11-01"


class TestFindMissingRanges:
    def test_all_missing(self):
        dates = [date(2025, 3, 1), date(2025, 3, 2), date(2025, 3, 3)]
        result = _find_missing_ranges(dates, set())
        assert result == [(date(2025, 3, 1), date(2025, 3, 4))]

    def test_none_missing(self):
        dates = [date(2025, 3, 1), date(2025, 3, 2), date(2025, 3, 3)]
        cached = {"2025-03-01", "2025-03-02", "2025-03-03"}
        assert _find_missing_ranges(dates, cached) == []

    def test_gap_in_middle(self):
        dates = [date(2025, 3, 1), date(2025, 3, 2), date(2025, 3, 3),
                 date(2025, 3, 4), date(2025, 3, 5)]
        cached = {"2025-03-01", "2025-03-05"}
        assert _find_missing_ranges(dates, cached) == [(date(2025, 3, 2), date(2025, 3, 5))]

    def test_multiple_gaps(self):
        dates = [date(2025, 3, 1), date(2025, 3, 2), date(2025, 3, 3),
                 date(2025, 3, 4), date(2025, 3, 5)]
        cached = {"2025-03-01", "2025-03-03"}
        assert _find_missing_ranges(dates, cached) == [
            (date(2025, 3, 2), date(2025, 3, 3)),
            (date(2025, 3, 4), date(2025, 3, 6)),
        ]

    def test_single_missing_day(self):
        dates = [date(2025, 3, 1), date(2025, 3, 2), date(2025, 3, 3)]
        cached = {"2025-03-01", "2025-03-03"}
        assert _find_missing_ranges(dates, cached) == [(date(2025, 3, 2), date(2025, 3, 3))]

    def test_empty_input(self):
        assert _find_missing_ranges([], set()) == []


def _linear_scan_rate(sorted_rates, interval_start, interval_end):
    """Reference matcher: the original front-to-back scan."""
    for rate in sorted_rates:
        if rate.valid_from <= interval_start and (
            rate.valid_to is None or rate.valid_to >= interval_end
        ):
            return rate.value_inc_vat
    for rate in reversed(sorted_rates):
        if rate.valid_from <= interval_start:
            return rate.value_inc_vat
    return None


def _reference_slot_rates(consumption_by_month, rates):
    sorted_rates = sorted(rates, key=lambda r: r.valid_from)
    return [
        _linear_scan_rate(sorted_rates, start, end) or 0.0
        for readings in consumption_by_month.values()
        for start, end, _ in readings
    ]


BST = timezone(timedelta(hours=1))


def _half_hours(start, count):
    return [start + timedelta(minutes=30 * i) for i in range(count)]


def _bst_readings(slots):
    """Readings as the REST API returns them in summer: +01:00 offsets."""
    by_month = {}
    for i, s in enumerate(slots):
        local = s.astimezone(BST)
        by_month.setdefault(f"{local.year}-{local.month:02d}", []).append(
            (local, (s + timedelta(minutes=30)).astimezone(BST), 0.1 + (i % 5) / 10)
        )
    return by_month


class TestRateLookupMatchesLinearScan:
    """The bisect lookup must price every slot as the original scan did."""

    def _assert_matches(self, consumption_by_month, rates):
        months = sorted(consumption_by_month)
        result = _compute_monthly_costs(
            consumption_by_month, rates, [], months, include_slots=True
        )
        got = [s.rate for m in result for s in m.slots]
        expected = [round(r, 2) for r in _reference_slot_rates(consumption_by_month, rates)]
        assert got == expected

    def test_half_hourly_rates_with_gap_and_trailing_readings(self):
        slots = _half_hours(datetime(2026, 7, 30, tzinfo=UTC), 48 * 4)
        rates = [
            Rate(value_exc_vat=0, value_inc_vat=5.0 + i % 37,
                 valid_from=s, valid_to=s + timedelta(minutes=30))
            for i, s in enumerate(slots[: 48 * 3])
            if not 60 <= i < 70  # gap: falls back to the closest earlier rate
        ]
        # Readings run a day past the last rate, as when rates lag consumption
        self._assert_matches(_bst_readings(slots), rates)

    def test_flat_tariff_open_ended_latest_rate(self):
        slots = _half_hours(datetime(2026, 8, 30, tzinfo=UTC), 48 * 4)
        change = datetime(2026, 9, 1, tzinfo=UTC)
        rates = [
            Rate(value_exc_vat=0, value_inc_vat=24.5,
                 valid_from=datetime(2026, 4, 1, tzinfo=UTC), valid_to=change),
            Rate(value_exc_vat=0, value_inc_vat=22.1, valid_from=change, valid_to=None),
        ]
        self._assert_matches(_bst_readings(slots), rates)

    def test_rates_sharing_valid_from_keep_first(self):
        slots = _half_hours(datetime(2026, 9, 1, tzinfo=UTC), 48)
        day = datetime(2026, 9, 1, tzinfo=UTC)
        rates = [
            Rate(value_exc_vat=0, value_inc_vat=23.0, valid_from=day,
                 valid_to=day + timedelta(days=1)),
            Rate(value_exc_vat=0, value_inc_vat=25.0, valid_from=day,
                 valid_to=day + timedelta(days=1)),
        ]
        self._assert_matches(_bst_readings(slots), rates)

    def test_readings_before_first_rate_are_unpriced(self):
        slots = _half_hours(datetime(2026, 9, 1, tzinfo=UTC), 4)
        rates = [
            Rate(value_exc_vat=0, value_inc_vat=30.0, valid_from=slots[2], valid_to=None),
        ]
        result = _compute_monthly_costs(
            _bst_readings(slots), rates, [], ["2026-09"], include_slots=True
        )
        assert [s.rate for s in result[0].slots] == [0.0, 0.0, 30.0, 30.0]

    def test_six_months_of_agile_is_fast(self):
        """Regression guard: the linear scan took ~24s here (minutes on a Pi)."""
        slots = _half_hours(datetime(2026, 5, 1, tzinfo=UTC), 48 * 184)
        rates = [
            Rate(value_exc_vat=0, value_inc_vat=10.0 + i % 48,
                 valid_from=s, valid_to=s + timedelta(minutes=30))
            for i, s in enumerate(slots)
        ]
        consumption_by_month = _bst_readings(slots)
        started = time.perf_counter()
        _compute_monthly_costs(
            consumption_by_month, rates, [], sorted(consumption_by_month),
            include_slots=True,
        )
        assert time.perf_counter() - started < 2.0
