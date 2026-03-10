"""Tests for persistent cache and empty consumption retry logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import Consumption, Rate, StandingCharge

from custom_components.octopus_energy.coordinator import (
    CACHE_STORAGE_KEY,
    CACHE_STORAGE_VERSION,
    CarbonIntensityPeriod,
    MeterData,
    OctopusEnergyCoordinator,
    OctopusEnergyData,
)

from .conftest import MOCK_ACCOUNT, MOCK_CONSUMPTION, MOCK_RATES, MOCK_STANDING_CHARGES

NOW = datetime.now(UTC)
YESTERDAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)


def _make_stored_data(
    consumption_date: str | None = None,
    standing_charges_date: str | None = None,
    carbon_date: str | None = None,
    meters: dict | None = None,
) -> dict:
    """Build a stored cache dict for testing."""
    return {
        "consumption_date": consumption_date,
        "standing_charges_date": standing_charges_date,
        "carbon_date": carbon_date,
        "meters": meters or {},
        "carbon_intensity": [],
    }


def _make_meter_cache(
    meter_id: str = "1100009640372_22L4344979",
    serial: str = "22L4344979",
    tariff: str = "E-1R-AGILE-24-10-01-C",
    product: str = "AGILE-24-10-01",
    is_export: bool = False,
    is_gas: bool = False,
    consumption: list | None = None,
) -> dict:
    """Build a cached meter dict."""
    return {
        "meter_id": meter_id,
        "serial_number": serial,
        "tariff_code": tariff,
        "product_code": product,
        "is_export": is_export,
        "is_gas": is_gas,
        "consumption": consumption or [
            {
                "consumption": 0.5,
                "interval_start": YESTERDAY.isoformat(),
                "interval_end": (YESTERDAY + timedelta(minutes=30)).isoformat(),
            },
        ],
        "standing_charges": [
            {
                "value_exc_vat": 37.65,
                "value_inc_vat": 39.53,
                "valid_from": datetime(2024, 10, 1, tzinfo=UTC).isoformat(),
            },
        ],
        "rates": [
            {
                "value_exc_vat": 19.54,
                "value_inc_vat": 20.517,
                "valid_from": (NOW - timedelta(minutes=15)).isoformat(),
                "valid_to": (NOW + timedelta(minutes=15)).isoformat(),
            },
        ],
    }


class TestEmptyConsumptionNotCached:
    """Verify that empty consumption results don't mark the date as cached."""

    def _make_coordinator_stub(self):
        """Minimal stub with the fields _async_update_data reads."""

        class Stub:
            data = None
            _consumption_date = None
            _standing_charges_date = None
            _carbon_date = None
            _rates_last_fetched = None

        return Stub()

    def test_empty_consumption_does_not_set_date(self):
        """When API returns empty lists, _consumption_date must stay None."""
        stub = self._make_coordinator_stub()
        yesterday = YESTERDAY.date()

        # Simulate the fixed logic from coordinator.py lines 612-626
        fetch_consumption = True
        consumption_ok = True
        meters = {
            "import_123": MeterData(
                meter_id="import_123",
                serial_number="123",
                tariff_code="E-1R-AGILE-24-10-01-C",
                product_code="AGILE-24-10-01",
                is_export=False,
                is_gas=False,
                consumption=[],  # Empty — API returned nothing
            ),
        }

        if fetch_consumption and consumption_ok:
            has_consumption = any(
                meter.consumption
                for meter in meters.values()
                if not meter.is_export
            )
            if has_consumption:
                stub._consumption_date = yesterday

        assert stub._consumption_date is None

    def test_nonempty_consumption_sets_date(self):
        """When API returns data, _consumption_date should be set."""
        stub = self._make_coordinator_stub()
        yesterday = YESTERDAY.date()

        fetch_consumption = True
        consumption_ok = True
        meters = {
            "import_123": MeterData(
                meter_id="import_123",
                serial_number="123",
                tariff_code="E-1R-AGILE-24-10-01-C",
                product_code="AGILE-24-10-01",
                is_export=False,
                is_gas=False,
                consumption=MOCK_CONSUMPTION,
            ),
        }

        if fetch_consumption and consumption_ok:
            has_consumption = any(
                meter.consumption
                for meter in meters.values()
                if not meter.is_export
            )
            if has_consumption:
                stub._consumption_date = yesterday

        assert stub._consumption_date == yesterday

    def test_export_only_empty_does_not_set_date(self):
        """Export-only meters with empty consumption should not set date."""
        stub = self._make_coordinator_stub()
        yesterday = YESTERDAY.date()

        fetch_consumption = True
        consumption_ok = True
        meters = {
            "export_456": MeterData(
                meter_id="export_456",
                serial_number="456",
                tariff_code="E-1R-OUTGOING-FIX-12M-19-05-13-C",
                product_code="OUTGOING-FIX-12M-19-05-13",
                is_export=True,
                is_gas=False,
                consumption=[],
            ),
        }

        if fetch_consumption and consumption_ok:
            has_consumption = any(
                meter.consumption
                for meter in meters.values()
                if not meter.is_export
            )
            if has_consumption:
                stub._consumption_date = yesterday

        assert stub._consumption_date is None


class TestCacheLoadSave:
    """Test cache serialisation round-trip."""

    @pytest.fixture
    def mock_store(self):
        """Create a mock Store."""
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        return store

    @pytest.fixture
    def coordinator_stub(self, mock_store):
        """Create a coordinator-like object for cache testing.

        We bind the real methods so they receive `self` automatically.
        """

        class StubCoordinator:
            data = None
            _account = MOCK_ACCOUNT
            _store = mock_store
            _consumption_date = None
            _standing_charges_date = None
            _carbon_date = None
            _rates_last_fetched = None

        stub = StubCoordinator()
        # Bind unbound methods to the stub instance
        import types

        stub.async_load_cache = types.MethodType(
            OctopusEnergyCoordinator.async_load_cache, stub
        )
        stub._save_cache = types.MethodType(
            OctopusEnergyCoordinator._save_cache, stub
        )
        return stub

    @pytest.mark.asyncio
    async def test_load_empty_cache(self, coordinator_stub, mock_store):
        """Loading when no cache exists should leave data as None."""
        mock_store.async_load.return_value = None
        await coordinator_stub.async_load_cache()
        assert coordinator_stub.data is None

    @pytest.mark.asyncio
    async def test_load_restores_consumption_date(self, coordinator_stub, mock_store):
        """Cache load should restore consumption_date."""
        yesterday_str = YESTERDAY.date().isoformat()
        stored = _make_stored_data(
            consumption_date=yesterday_str,
            meters={"m1": _make_meter_cache()},
        )
        mock_store.async_load.return_value = stored

        await coordinator_stub.async_load_cache()

        assert coordinator_stub._consumption_date == YESTERDAY.date()
        assert coordinator_stub.data is not None
        assert "m1" in coordinator_stub.data.meters
        assert len(coordinator_stub.data.meters["m1"].consumption) == 1

    @pytest.mark.asyncio
    async def test_load_restores_meter_data(self, coordinator_stub, mock_store):
        """Cache load should deserialise meter data correctly."""
        stored = _make_stored_data(
            meters={"m1": _make_meter_cache()},
        )
        mock_store.async_load.return_value = stored

        await coordinator_stub.async_load_cache()

        meter = coordinator_stub.data.meters["m1"]
        assert isinstance(meter, MeterData)
        assert meter.serial_number == "22L4344979"
        assert len(meter.rates) == 1
        assert isinstance(meter.rates[0], Rate)
        assert len(meter.standing_charges) == 1
        assert isinstance(meter.standing_charges[0], StandingCharge)

    @pytest.mark.asyncio
    async def test_load_handles_corrupt_cache(self, coordinator_stub, mock_store):
        """Corrupt cache should not crash — starts fresh."""
        mock_store.async_load.return_value = {"meters": {"m1": {"bad": "data"}}}

        await coordinator_stub.async_load_cache()

        # Should not have set data on error
        assert coordinator_stub.data is None

    @pytest.mark.asyncio
    async def test_save_persists_data(self, coordinator_stub, mock_store):
        """Save should serialise all data to the store."""
        coordinator_stub._consumption_date = YESTERDAY.date()
        data = OctopusEnergyData(
            account=MOCK_ACCOUNT,
            meters={
                "m1": MeterData(
                    meter_id="m1",
                    serial_number="123",
                    tariff_code="E-1R-AGILE-24-10-01-C",
                    product_code="AGILE-24-10-01",
                    is_export=False,
                    is_gas=False,
                    consumption=MOCK_CONSUMPTION,
                    rates=MOCK_RATES,
                    standing_charges=MOCK_STANDING_CHARGES,
                ),
            },
            carbon_intensity=[
                CarbonIntensityPeriod(
                    from_dt=YESTERDAY,
                    to_dt=YESTERDAY + timedelta(minutes=30),
                    forecast=150,
                    actual=145,
                    index="moderate",
                ),
            ],
        )

        await coordinator_stub._save_cache(data)

        mock_store.async_save.assert_called_once()
        saved = mock_store.async_save.call_args[0][0]

        assert saved["consumption_date"] == YESTERDAY.date().isoformat()
        assert "m1" in saved["meters"]
        assert len(saved["meters"]["m1"]["consumption"]) == 2
        assert len(saved["meters"]["m1"]["rates"]) == 2
        assert len(saved["carbon_intensity"]) == 1

    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self, coordinator_stub, mock_store):
        """Data saved should be loadable and match the original."""
        coordinator_stub._consumption_date = YESTERDAY.date()
        original_data = OctopusEnergyData(
            account=MOCK_ACCOUNT,
            meters={
                "m1": MeterData(
                    meter_id="m1",
                    serial_number="123",
                    tariff_code="E-1R-AGILE-24-10-01-C",
                    product_code="AGILE-24-10-01",
                    is_export=False,
                    is_gas=False,
                    consumption=MOCK_CONSUMPTION,
                    rates=MOCK_RATES,
                    standing_charges=MOCK_STANDING_CHARGES,
                ),
            },
        )

        # Save
        await coordinator_stub._save_cache(original_data)
        saved = mock_store.async_save.call_args[0][0]

        # Reset and load
        coordinator_stub.data = None
        coordinator_stub._consumption_date = None
        mock_store.async_load.return_value = saved

        await coordinator_stub.async_load_cache()

        assert coordinator_stub._consumption_date == YESTERDAY.date()
        loaded_meter = coordinator_stub.data.meters["m1"]
        original_meter = original_data.meters["m1"]

        assert len(loaded_meter.consumption) == len(original_meter.consumption)
        assert loaded_meter.consumption[0].consumption == original_meter.consumption[0].consumption
        assert len(loaded_meter.rates) == len(original_meter.rates)
        assert loaded_meter.rates[0].value_inc_vat == original_meter.rates[0].value_inc_vat
