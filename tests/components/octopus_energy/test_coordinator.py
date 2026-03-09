"""Tests for Octopus Energy coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aiooctopusenergy import Rate

from custom_components.octopus_energy.coordinator import (
    MeterData,
    OctopusEnergyCoordinator,
    OctopusEnergyData,
    _extract_product_code,
    _get_active_agreement,
)

from .conftest import MOCK_ACCOUNT


class TestExtractProductCode:
    def test_agile_tariff(self):
        assert _extract_product_code("E-1R-AGILE-24-10-01-C") == "AGILE-24-10-01"

    def test_variable_tariff(self):
        assert _extract_product_code("G-1R-VAR-22-11-01-C") == "VAR-22-11-01"

    def test_export_tariff(self):
        result = _extract_product_code("E-1R-OUTGOING-FIX-12M-19-05-13-C")
        assert result == "OUTGOING-FIX-12M-19-05-13"

    def test_short_code_passthrough(self):
        assert _extract_product_code("ABC") == "ABC"


class TestGetActiveAgreement:
    def test_active_agreement(self):
        agreements = MOCK_ACCOUNT.properties[0].electricity_meter_points[0].agreements
        agreement = _get_active_agreement(agreements)
        assert agreement is not None
        assert agreement.tariff_code == "E-1R-AGILE-24-10-01-C"

    def test_empty_agreements(self):
        assert _get_active_agreement([]) is None


class TestShouldFetchRates:
    """Test the _should_fetch_rates smart caching logic."""

    def _make_coordinator_stub(self):
        class Stub:
            data = None
            _rates_last_fetched = None

            def _should_fetch_rates(self, meter, now):
                return OctopusEnergyCoordinator._should_fetch_rates(self, meter, now)

        return Stub()

    def _make_meter(self, is_gas=False):
        return MeterData(
            meter_id="test_123",
            serial_number="123",
            tariff_code="E-1R-AGILE-24-10-01-C",
            product_code="AGILE-24-10-01",
            is_export=False,
            is_gas=is_gas,
        )

    def test_first_fetch_always_true(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter()
        now = datetime(2025, 3, 8, 12, 0, tzinfo=UTC)
        assert stub._should_fetch_rates(meter, now) is True

    def test_skip_when_rates_cover_tomorrow(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter()
        now = datetime(2025, 3, 8, 12, 0, tzinfo=UTC)
        tomorrow = datetime(2025, 3, 9, 0, 0, tzinfo=UTC)

        prev_meter = self._make_meter()
        prev_meter.rates = [
            Rate(value_exc_vat=10.0, value_inc_vat=10.5,
                 valid_from=tomorrow, valid_to=tomorrow + timedelta(minutes=30)),
        ]
        stub.data = OctopusEnergyData(account=MOCK_ACCOUNT, meters={"test_123": prev_meter})
        assert stub._should_fetch_rates(meter, now) is False

    def test_fetch_after_4pm_no_tomorrow_rates(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter()
        now = datetime(2025, 3, 8, 16, 30, tzinfo=UTC)

        prev_meter = self._make_meter()
        prev_meter.rates = [
            Rate(value_exc_vat=10.0, value_inc_vat=10.5,
                 valid_from=datetime(2025, 3, 8, 12, 0, tzinfo=UTC),
                 valid_to=datetime(2025, 3, 8, 23, 30, tzinfo=UTC)),
        ]
        stub.data = OctopusEnergyData(account=MOCK_ACCOUNT, meters={"test_123": prev_meter})
        assert stub._should_fetch_rates(meter, now) is True

    def test_skip_before_4pm_recently_fetched(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter()
        now = datetime(2025, 3, 8, 10, 0, tzinfo=UTC)
        stub._rates_last_fetched = now - timedelta(hours=1)

        prev_meter = self._make_meter()
        prev_meter.rates = [
            Rate(value_exc_vat=10.0, value_inc_vat=10.5,
                 valid_from=datetime(2025, 3, 8, 0, 0, tzinfo=UTC),
                 valid_to=datetime(2025, 3, 8, 23, 30, tzinfo=UTC)),
        ]
        stub.data = OctopusEnergyData(account=MOCK_ACCOUNT, meters={"test_123": prev_meter})
        assert stub._should_fetch_rates(meter, now) is False

    def test_fetch_before_4pm_stale(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter()
        now = datetime(2025, 3, 8, 10, 0, tzinfo=UTC)
        stub._rates_last_fetched = now - timedelta(hours=5)

        prev_meter = self._make_meter()
        prev_meter.rates = [
            Rate(value_exc_vat=10.0, value_inc_vat=10.5,
                 valid_from=datetime(2025, 3, 8, 0, 0, tzinfo=UTC),
                 valid_to=datetime(2025, 3, 8, 23, 30, tzinfo=UTC)),
        ]
        stub.data = OctopusEnergyData(account=MOCK_ACCOUNT, meters={"test_123": prev_meter})
        assert stub._should_fetch_rates(meter, now) is True

    def test_gas_fetched_today_skips(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter(is_gas=True)
        now = datetime(2025, 3, 8, 14, 0, tzinfo=UTC)
        stub._rates_last_fetched = datetime(2025, 3, 8, 6, 0, tzinfo=UTC)

        prev_meter = self._make_meter(is_gas=True)
        prev_meter.rates = [
            Rate(value_exc_vat=5.0, value_inc_vat=5.25,
                 valid_from=datetime(2025, 3, 1, 0, 0, tzinfo=UTC), valid_to=None),
        ]
        stub.data = OctopusEnergyData(account=MOCK_ACCOUNT, meters={"test_123": prev_meter})
        assert stub._should_fetch_rates(meter, now) is False

    def test_gas_new_day_fetches(self):
        stub = self._make_coordinator_stub()
        meter = self._make_meter(is_gas=True)
        now = datetime(2025, 3, 9, 6, 0, tzinfo=UTC)
        stub._rates_last_fetched = datetime(2025, 3, 8, 6, 0, tzinfo=UTC)

        prev_meter = self._make_meter(is_gas=True)
        prev_meter.rates = [
            Rate(value_exc_vat=5.0, value_inc_vat=5.25,
                 valid_from=datetime(2025, 3, 1, 0, 0, tzinfo=UTC), valid_to=None),
        ]
        stub.data = OctopusEnergyData(account=MOCK_ACCOUNT, meters={"test_123": prev_meter})
        assert stub._should_fetch_rates(meter, now) is True
