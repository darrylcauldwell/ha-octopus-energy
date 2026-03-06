"""Tests for tariff comparison coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aiooctopusenergy import Consumption, Rate, StandingCharge

from custom_components.octopus_energy.comparison_coordinator import (
    MonthlyTariffCost,
    _compute_monthly_costs,
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
