"""Tests for the tariff comparison coordinator's consumption cache."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import Consumption, Rate, StandingCharge

from custom_components.octopus_energy.comparison_coordinator import (
    COMPARISON_UPDATE_INTERVAL,
    TariffComparisonCoordinator,
    _consumption_from_storage,
    _consumption_to_storage,
)
from custom_components.octopus_energy.const import (
    CONF_ACCOUNT_NUMBER,
    CONF_COMPARISON_MONTHS,
    CONF_COMPARISON_PRODUCTS,
)
from custom_components.octopus_energy.coordinator import MeterData

MODULE = "custom_components.octopus_energy.comparison_coordinator"
MPAN = "1100009640372"
SERIAL = "22L4344979"
BST = timezone(timedelta(hours=1))
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
# comparison_months=2 at NOW: 2026-07-01 .. 2026-09-19
FIRST_DAY = date(2026, 7, 1)
LAST_DAY = date(2026, 9, 19)


class _FrozenDatetime(datetime):
    now_value = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.now_value


class FakeStore:
    """In-memory Store that round-trips through JSON like the real one."""

    def __init__(self, data=None):
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = json.loads(json.dumps(data))


def _bst_readings(period_from, period_to):
    """Half-hourly readings with the +01:00 offsets the API uses in summer."""
    readings = []
    t = period_from
    while t < period_to:
        readings.append(
            Consumption(
                consumption=0.25,
                interval_start=t.astimezone(BST),
                interval_end=(t + timedelta(minutes=30)).astimezone(BST),
            )
        )
        t += timedelta(minutes=30)
    return readings


def _make_client():
    client = MagicMock()
    client.get_electricity_consumption = AsyncMock(
        side_effect=lambda mpan, serial, *, period_from, period_to, **kw: _bst_readings(
            period_from, period_to
        )
    )
    client.get_electricity_rates = AsyncMock(
        return_value=[
            Rate(value_exc_vat=0, value_inc_vat=20.0,
                 valid_from=datetime(2026, 1, 1, tzinfo=UTC), valid_to=None),
        ]
    )
    client.get_electricity_standing_charges = AsyncMock(
        return_value=[
            StandingCharge(value_exc_vat=0, value_inc_vat=50.0,
                           valid_from=datetime(2026, 1, 1, tzinfo=UTC), valid_to=None),
        ]
    )
    return client


def _make_coordinator(store, client, mpan=MPAN):
    """Real coordinator methods on a hand-built instance (no hass needed)."""
    coord = object.__new__(TariffComparisonCoordinator)
    coord.hass = MagicMock()
    coord.hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *a: func(*a))
    coord.config_entry = MagicMock(
        data={CONF_ACCOUNT_NUMBER: "A-AAAA1111"},
        options={CONF_COMPARISON_MONTHS: 2, CONF_COMPARISON_PRODUCTS: ["VAR-22-11-01"]},
    )
    coord.client = client
    coord._main = MagicMock()
    coord._main.data.meters = {
        f"{mpan}_{SERIAL}": MeterData(
            meter_id=f"{mpan}_{SERIAL}",
            serial_number=SERIAL,
            tariff_code="E-1R-AGILE-24-10-01-C",
            product_code="AGILE-24-10-01",
            is_export=False,
            is_gas=False,
        )
    }
    coord._graphql_client = MagicMock(
        get_applicable_rates=AsyncMock(side_effect=Exception("down")),
        get_smart_tariff_comparison=AsyncMock(side_effect=Exception("down")),
    )
    coord._store = store
    coord._cached_consumption = {}
    coord._cached_mpan = None
    coord._cached_rates = {}
    coord._cached_standing = {}
    coord.update_interval = COMPARISON_UPDATE_INTERVAL
    return coord


@pytest.fixture(autouse=True)
def _frozen_clock_no_delays():
    _FrozenDatetime.now_value = NOW
    with (
        patch(f"{MODULE}.datetime", _FrozenDatetime),
        patch(f"{MODULE}.asyncio.sleep", AsyncMock()),
    ):
        yield


async def _run_until_settled(coord):
    for _ in range(10):
        data = await coord._async_update_data()
        if coord.update_interval == COMPARISON_UPDATE_INTERVAL:
            return data
    raise AssertionError("backfill never completed")


def _fetched_windows(client):
    return [
        (c.kwargs["period_from"].date(), c.kwargs["period_to"].date())
        for c in client.get_electricity_consumption.call_args_list
    ]


class TestStorageRoundTrip:
    def test_round_trip_preserves_readings_and_offsets(self):
        start = datetime(2026, 7, 1, 0, 0, tzinfo=BST)
        cache = {"2026-06-30": [(start, start + timedelta(minutes=30), 0.25)]}
        restored = _consumption_from_storage(
            json.loads(json.dumps(_consumption_to_storage(cache)))
        )
        assert restored == cache
        assert restored["2026-06-30"][0][0].utcoffset() == timedelta(hours=1)


class TestConsumptionCache:
    async def test_backfill_keys_full_utc_days(self):
        store = FakeStore()
        coord = _make_coordinator(store, _make_client())

        data = await _run_until_settled(coord)

        expected = {
            (FIRST_DAY + timedelta(days=n)).isoformat()
            for n in range((LAST_DAY - FIRST_DAY).days + 1)
        }
        assert set(coord._cached_consumption) == expected
        assert all(len(r) == 48 for r in coord._cached_consumption.values())
        assert data.total_consumption_kwh == len(expected) * 48 * 0.25

    async def test_restart_reuses_cache_and_refetches_only_recent_days(self):
        store = FakeStore()
        first = _make_coordinator(store, _make_client())
        before = await _run_until_settled(first)

        client = _make_client()
        restarted = _make_coordinator(FakeStore(store.data), client)
        await restarted.async_load_cache()
        after = await restarted._async_update_data()

        assert _fetched_windows(client) == [(date(2026, 9, 17), date(2026, 9, 20))]
        assert restarted.update_interval == COMPARISON_UPDATE_INTERVAL
        assert after.total_consumption_kwh == before.total_consumption_kwh

    async def test_refetched_recent_days_replace_not_duplicate(self):
        coord = _make_coordinator(FakeStore(), _make_client())
        before = await _run_until_settled(coord)

        after = await coord._async_update_data()
        after = await coord._async_update_data()

        assert all(len(r) == 48 for r in coord._cached_consumption.values())
        assert after.total_consumption_kwh == before.total_consumption_kwh

    async def test_cache_for_another_meter_is_discarded(self):
        store = FakeStore()
        await _run_until_settled(_make_coordinator(store, _make_client(), mpan="999"))

        client = _make_client()
        coord = _make_coordinator(FakeStore(store.data), client)
        await coord.async_load_cache()
        await coord._async_update_data()

        assert _fetched_windows(client)[0][0] == FIRST_DAY
        assert coord._cached_mpan == MPAN

    async def test_corrupt_cache_starts_fresh(self):
        store = FakeStore({"mpan": MPAN, "consumption": {"2026-07-01": [["bad", "x", 1]]}})
        coord = _make_coordinator(store, _make_client())

        await coord.async_load_cache()

        assert coord._cached_consumption == {}
        assert coord._cached_mpan is None
