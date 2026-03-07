"""Tests for carbon intensity correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.octopus_energy.coordinator import CarbonIntensityPeriod
from custom_components.octopus_energy.sensor import (
    _compute_optimal_windows,
    _enrich_charges_with_carbon,
)


YESTERDAY = datetime(2026, 3, 6, tzinfo=UTC)


def _make_carbon_periods(
    start: datetime, count: int, intensities: list[int], indices: list[str]
) -> list[CarbonIntensityPeriod]:
    """Create carbon periods with given intensities."""
    periods = []
    for i in range(count):
        from_dt = start + timedelta(minutes=30 * i)
        to_dt = from_dt + timedelta(minutes=30)
        periods.append(
            CarbonIntensityPeriod(
                from_dt=from_dt,
                to_dt=to_dt,
                forecast=intensities[i],
                actual=intensities[i],
                index=indices[i],
            )
        )
    return periods


def _make_charges(
    start: datetime, count: int, consumptions: list[float], rates: list[float]
) -> list[dict]:
    """Create charge dicts matching the cost attrs format."""
    charges = []
    for i in range(count):
        from_dt = start + timedelta(minutes=30 * i)
        to_dt = from_dt + timedelta(minutes=30)
        charges.append(
            {
                "start": from_dt.isoformat(),
                "end": to_dt.isoformat(),
                "consumption": consumptions[i],
                "rate": rates[i],
            }
        )
    return charges


class TestEnrichChargesWithCarbon:
    def test_matches_by_timestamp(self):
        charges = _make_charges(YESTERDAY, 2, [0.5, 0.3], [20.0, 25.0])
        carbon = _make_carbon_periods(
            YESTERDAY, 2, [150, 200], ["moderate", "high"]
        )
        result = _enrich_charges_with_carbon(charges, carbon)

        assert "carbon_summary" in result
        assert "optimization" in result
        assert charges[0]["carbon_intensity"] == 150
        assert charges[1]["carbon_intensity"] == 200
        assert charges[0]["carbon_index"] == "moderate"
        assert charges[0]["carbon_grams"] == pytest.approx(75.0, abs=0.1)
        assert charges[1]["carbon_grams"] == pytest.approx(60.0, abs=0.1)

    def test_summary_calculations(self):
        charges = _make_charges(YESTERDAY, 4, [1.0, 1.0, 1.0, 1.0], [10.0] * 4)
        carbon = _make_carbon_periods(
            YESTERDAY, 4, [100, 200, 50, 300], ["low", "high", "very low", "very high"]
        )
        result = _enrich_charges_with_carbon(charges, carbon)

        summary = result["carbon_summary"]
        assert summary["total_grams_co2"] == pytest.approx(650.0, abs=0.1)
        assert summary["weighted_avg_intensity"] == pytest.approx(162.5, abs=0.1)
        assert summary["high_carbon_kwh"] == pytest.approx(2.0)
        assert summary["low_carbon_kwh"] == pytest.approx(2.0)
        assert summary["high_carbon_pct"] == pytest.approx(50.0)
        assert summary["low_carbon_pct"] == pytest.approx(50.0)

    def test_empty_charges(self):
        carbon = _make_carbon_periods(YESTERDAY, 2, [150, 200], ["moderate", "high"])
        assert _enrich_charges_with_carbon([], carbon) == {}

    def test_empty_carbon(self):
        charges = _make_charges(YESTERDAY, 2, [0.5, 0.3], [20.0, 25.0])
        assert _enrich_charges_with_carbon(charges, []) == {}

    def test_uses_forecast_when_actual_missing(self):
        charges = _make_charges(YESTERDAY, 1, [1.0], [20.0])
        carbon = [
            CarbonIntensityPeriod(
                from_dt=YESTERDAY,
                to_dt=YESTERDAY + timedelta(minutes=30),
                forecast=180,
                actual=None,
                index="moderate",
            )
        ]
        result = _enrich_charges_with_carbon(charges, carbon)
        assert charges[0]["carbon_intensity"] == 180
        assert charges[0]["carbon_grams"] == pytest.approx(180.0, abs=0.1)

    def test_zero_consumption_returns_empty(self):
        charges = _make_charges(YESTERDAY, 2, [0.0, 0.0], [20.0, 25.0])
        carbon = _make_carbon_periods(
            YESTERDAY, 2, [150, 200], ["moderate", "high"]
        )
        assert _enrich_charges_with_carbon(charges, carbon) == {}


class TestComputeOptimalWindows:
    def test_finds_cheapest_window(self):
        charges = _make_charges(
            YESTERDAY, 6, [0.5] * 6, [30.0, 25.0, 10.0, 5.0, 8.0, 12.0]
        )
        carbon = _make_carbon_periods(
            YESTERDAY, 6, [150] * 6, ["moderate"] * 6
        )
        result = _compute_optimal_windows(charges, carbon)

        assert "cheapest_2h_window" in result
        expected_start = (YESTERDAY + timedelta(minutes=60)).isoformat()
        assert result["cheapest_2h_window"]["start"] == expected_start
        assert result["cheapest_2h_window"]["avg_rate"] == pytest.approx(
            (10.0 + 5.0 + 8.0 + 12.0) / 4, abs=0.01
        )

    def test_finds_greenest_window(self):
        charges = _make_charges(YESTERDAY, 6, [0.5] * 6, [20.0] * 6)
        carbon = _make_carbon_periods(
            YESTERDAY, 6, [200, 180, 50, 40, 60, 150], ["high"] * 6
        )
        result = _compute_optimal_windows(charges, carbon)

        assert "greenest_2h_window" in result
        expected_start = (YESTERDAY + timedelta(minutes=60)).isoformat()
        assert result["greenest_2h_window"]["start"] == expected_start
        assert result["greenest_2h_window"]["avg_intensity"] == pytest.approx(
            (50 + 40 + 60 + 150) / 4, abs=0.1
        )

    def test_too_few_periods_returns_empty(self):
        charges = _make_charges(YESTERDAY, 3, [0.5] * 3, [20.0] * 3)
        carbon = _make_carbon_periods(YESTERDAY, 3, [150] * 3, ["moderate"] * 3)
        assert _compute_optimal_windows(charges, carbon) == {}

    def test_skips_periods_without_rate(self):
        charges = _make_charges(YESTERDAY, 5, [0.5] * 5, [20.0, None, 20.0, 20.0, 20.0])
        carbon = _make_carbon_periods(YESTERDAY, 5, [150] * 5, ["moderate"] * 5)
        result = _compute_optimal_windows(charges, carbon)
        # Only 4 periods have rates (index 0, 2, 3, 4) but they're not consecutive
        # in the filtered list, so the window should still work
        assert "cheapest_2h_window" in result
