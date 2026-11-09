"""Tests for the Home Mini telemetry coordinator and sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import OctopusEnergyError, SmartMeterDevice, SmartMeterTelemetry
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.octopus_energy.home_mini_coordinator import (
    TELEMETRY_WINDOW,
    HomeMiniCoordinator,
    HomeMiniReading,
    latest_complete_reading,
)
from custom_components.octopus_energy.sensor import (
    HOME_MINI_SENSOR_DESCRIPTIONS,
    HomeMiniSensor,
)

DEVICE = SmartMeterDevice(
    mpan="1100009640372", serial_number="22L4344979", device_id="00-11-22"
)
METER_ID = "1100009640372_22L4344979"


def _reading(second: int, demand=500.0, consumption=11326.0) -> SmartMeterTelemetry:
    return SmartMeterTelemetry(
        read_at=datetime(2026, 11, 9, 8, 9, second, tzinfo=UTC),
        consumption_kwh=consumption,
        demand_w=demand,
        consumption_delta_kwh=0.0,
    )


def _make_coordinator(graphql_client, devices=(DEVICE,)):
    """Create a HomeMiniCoordinator with mocked base class init."""
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
    ):
        coordinator = HomeMiniCoordinator(
            MagicMock(), MagicMock(), graphql_client, list(devices)
        )
    return coordinator


class TestLatestCompleteReading:
    def test_picks_newest(self):
        result = latest_complete_reading([_reading(10, 100.0), _reading(30, 300.0), _reading(20, 200.0)])
        assert result == HomeMiniReading(
            read_at=datetime(2026, 11, 9, 8, 9, 30, tzinfo=UTC),
            demand_w=300.0,
            consumption_kwh=11326.0,
        )

    def test_skips_readings_missing_a_value(self):
        result = latest_complete_reading(
            [_reading(10, 100.0), _reading(20, None), _reading(30, 300.0, None)]
        )
        assert result is not None
        assert result.demand_w == 100.0

    def test_none_when_nothing_complete(self):
        assert latest_complete_reading([_reading(10, None)]) is None
        assert latest_complete_reading([]) is None


class TestHomeMiniCoordinator:
    @pytest.mark.asyncio
    async def test_update_keys_readings_by_meter_id(self):
        client = MagicMock()
        client.get_smart_meter_telemetry = AsyncMock(return_value=[_reading(10), _reading(40, 1843.0)])
        coordinator = _make_coordinator(client)

        data = await coordinator._async_update_data()

        assert list(data) == [METER_ID]
        assert data[METER_ID].demand_w == 1843.0
        call = client.get_smart_meter_telemetry.call_args
        assert call.args == ("00-11-22",)
        assert call.kwargs["end"] - call.kwargs["start"] == TELEMETRY_WINDOW

    @pytest.mark.asyncio
    async def test_meter_without_readings_is_left_out(self):
        client = MagicMock()
        client.get_smart_meter_telemetry = AsyncMock(return_value=[])
        coordinator = _make_coordinator(client)

        assert await coordinator._async_update_data() == {}
        assert coordinator.meter_ids == [METER_ID]

    @pytest.mark.asyncio
    async def test_api_error_raises_update_failed(self):
        client = MagicMock()
        client.get_smart_meter_telemetry = AsyncMock(side_effect=OctopusEnergyError("429"))
        coordinator = _make_coordinator(client)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


class TestHomeMiniSensor:
    def _sensor(self, data, key):
        coordinator = MagicMock()
        coordinator.data = data
        entry = MagicMock()
        entry.entry_id = "entry1"
        description = next(d for d in HOME_MINI_SENSOR_DESCRIPTIONS if d.key == key)
        return HomeMiniSensor(coordinator, description, entry, METER_ID)

    def test_demand_and_register(self):
        reading = HomeMiniReading(
            read_at=datetime(2026, 11, 9, 8, 9, 40, tzinfo=UTC),
            demand_w=1843.0,
            consumption_kwh=11326.045,
        )
        demand = self._sensor({METER_ID: reading}, "home_mini_demand")
        register = self._sensor({METER_ID: reading}, "home_mini_consumption")

        assert demand.native_value == 1843.0
        assert register.native_value == 11326.045
        assert demand.extra_state_attributes == {"read_at": "2026-11-09T08:09:40+00:00"}
        assert demand.unique_id == f"entry1_{METER_ID}_home_mini_demand"

    def test_no_reading_is_unknown(self):
        sensor = self._sensor({}, "home_mini_demand")
        assert sensor.native_value is None
        assert sensor.extra_state_attributes is None
