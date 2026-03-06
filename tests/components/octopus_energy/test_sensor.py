"""Tests for Octopus Energy sensor functions."""

from __future__ import annotations

import pytest

from custom_components.octopus_energy.coordinator import MeterData
from custom_components.octopus_energy.sensor import (
    _get_current_rate,
    _get_next_rate,
    _get_previous_consumption,
    _get_previous_cost,
    _get_standing_charge,
    _get_consumption_attrs,
    _get_cost_attrs,
)

from .conftest import MOCK_CONSUMPTION, MOCK_RATES, MOCK_STANDING_CHARGES


@pytest.fixture
def mock_meter() -> MeterData:
    """Create a mock meter with test data."""
    return MeterData(
        meter_id="1100009640372_22L4344979",
        serial_number="22L4344979",
        tariff_code="E-1R-AGILE-24-10-01-C",
        product_code="AGILE-24-10-01",
        is_export=False,
        is_gas=False,
        rates=MOCK_RATES,
        consumption=MOCK_CONSUMPTION,
        standing_charges=MOCK_STANDING_CHARGES,
    )


class TestGetCurrentRate:
    def test_returns_current_rate(self, mock_meter):
        rate = _get_current_rate(mock_meter)
        assert rate == 20.517

    def test_empty_rates(self, mock_meter):
        mock_meter = MeterData(
            meter_id="test",
            serial_number="test",
            tariff_code="test",
            product_code="test",
            is_export=False,
            is_gas=False,
        )
        assert _get_current_rate(mock_meter) is None


class TestGetNextRate:
    def test_returns_next_rate(self, mock_meter):
        rate = _get_next_rate(mock_meter)
        assert rate == 22.05


class TestGetPreviousConsumption:
    def test_total_consumption(self, mock_meter):
        total = _get_previous_consumption(mock_meter)
        assert total == pytest.approx(0.8)

    def test_empty_consumption(self):
        meter = MeterData(
            meter_id="test",
            serial_number="test",
            tariff_code="test",
            product_code="test",
            is_export=False,
            is_gas=False,
        )
        assert _get_previous_consumption(meter) is None


class TestGetPreviousCost:
    def test_empty_consumption(self):
        meter = MeterData(
            meter_id="test",
            serial_number="test",
            tariff_code="test",
            product_code="test",
            is_export=False,
            is_gas=False,
        )
        assert _get_previous_cost(meter) is None


class TestGetStandingCharge:
    def test_returns_current_charge(self, mock_meter):
        charge = _get_standing_charge(mock_meter)
        assert charge == 39.53

    def test_empty_charges(self):
        meter = MeterData(
            meter_id="test",
            serial_number="test",
            tariff_code="test",
            product_code="test",
            is_export=False,
            is_gas=False,
        )
        assert _get_standing_charge(meter) is None


class TestConsumptionAttrs:
    def test_returns_charges_list(self, mock_meter):
        attrs = _get_consumption_attrs(mock_meter)
        assert "charges" in attrs
        assert len(attrs["charges"]) == 2
        assert attrs["charges"][0]["consumption"] == 0.5

    def test_empty_consumption(self):
        meter = MeterData(
            meter_id="test",
            serial_number="test",
            tariff_code="test",
            product_code="test",
            is_export=False,
            is_gas=False,
        )
        assert _get_consumption_attrs(meter) == {}


class TestCostAttrs:
    def test_empty_consumption(self):
        meter = MeterData(
            meter_id="test",
            serial_number="test",
            tariff_code="test",
            product_code="test",
            is_export=False,
            is_gas=False,
        )
        assert _get_cost_attrs(meter) == {}
