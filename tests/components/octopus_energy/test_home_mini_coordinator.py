"""Tests for the Home Mini telemetry coordinator and sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import (
    OctopusEnergyError,
    SmartGasMeterDevice,
    SmartMeterDevice,
    SmartMeterTelemetry,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.octopus_energy.home_mini_coordinator import (
    GAS_TELEMETRY_WINDOW,
    TELEMETRY_WINDOW,
    GasHalfHour,
    HomeMiniCoordinator,
    HomeMiniGasCoordinator,
    HomeMiniReading,
    latest_complete_reading,
    latest_gas_half_hour,
)
from custom_components.octopus_energy.sensor import (
    HOME_MINI_GAS_SENSOR_DESCRIPTIONS,
    HOME_MINI_SENSOR_DESCRIPTIONS,
    HomeMiniGasSensor,
    HomeMiniSensor,
    _meters_with_telemetry,
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


GAS_DEVICE = SmartGasMeterDevice(
    mprn="2112316000", serial_number="E6E07422582221", device_id="44-11-02"
)
GAS_METER_ID = "2112316000_E6E07422582221"


def _gas_upload(
    hour: int, minute: int, second: int, delta=7.36, register=20936.28
) -> SmartMeterTelemetry:
    return SmartMeterTelemetry(
        read_at=datetime(2026, 11, 10, hour, minute, second, tzinfo=UTC),
        consumption_kwh=register,
        demand_w=None,
        consumption_delta_kwh=delta,
    )


def _make_gas_coordinator(graphql_client, devices=(GAS_DEVICE,)):
    """Create a HomeMiniGasCoordinator with mocked base class init."""
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
    ):
        coordinator = HomeMiniGasCoordinator(
            MagicMock(), MagicMock(), graphql_client, list(devices)
        )
    return coordinator


class TestLatestGasHalfHour:
    def test_upload_covers_half_hour_ending_at_last_boundary(self):
        # Real uploads: the published half-hours put 3.44 kWh in 07:00-07:30
        # and 7.36 kWh in 07:30-08:00.
        result = latest_gas_half_hour(
            [_gas_upload(8, 16, 50, 7.36), _gas_upload(7, 46, 40, 3.44)]
        )
        assert result == GasHalfHour(
            read_at=datetime(2026, 11, 10, 8, 16, 50, tzinfo=UTC),
            period_start=datetime(2026, 11, 10, 7, 30, tzinfo=UTC),
            period_end=datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
            consumption_kwh=20936.28,
            half_hour_kwh=7.36,
        )

    def test_upload_just_before_a_boundary_is_the_previous_half_hour(self):
        result = latest_gas_half_hour([_gas_upload(16, 59, 40)])
        assert result is not None
        assert (result.period_start, result.period_end) == (
            datetime(2026, 11, 10, 16, 0, tzinfo=UTC),
            datetime(2026, 11, 10, 16, 30, tzinfo=UTC),
        )

    def test_skips_uploads_missing_a_value(self):
        result = latest_gas_half_hour(
            [_gas_upload(7, 46, 40, 3.44), _gas_upload(8, 16, 50, None)]
        )
        assert result is not None
        assert result.half_hour_kwh == 3.44

    def test_none_when_nothing_complete(self):
        assert latest_gas_half_hour([_gas_upload(8, 16, 50, register=None)]) is None
        assert latest_gas_half_hour([]) is None


class TestHomeMiniGasCoordinator:
    @pytest.mark.asyncio
    async def test_update_keys_half_hours_by_meter_id(self):
        client = MagicMock()
        client.get_smart_meter_telemetry = AsyncMock(
            return_value=[_gas_upload(7, 46, 40, 3.44), _gas_upload(8, 16, 50, 7.36)]
        )
        coordinator = _make_gas_coordinator(client)

        data = await coordinator._async_update_data()

        assert list(data) == [GAS_METER_ID]
        assert data[GAS_METER_ID].half_hour_kwh == 7.36
        call = client.get_smart_meter_telemetry.call_args
        assert call.args == ("44-11-02",)
        assert call.kwargs["end"] - call.kwargs["start"] == GAS_TELEMETRY_WINDOW

    @pytest.mark.asyncio
    async def test_meter_without_uploads_is_left_out(self):
        client = MagicMock()
        client.get_smart_meter_telemetry = AsyncMock(return_value=[])
        coordinator = _make_gas_coordinator(client)

        assert await coordinator._async_update_data() == {}
        assert coordinator.meter_ids == [GAS_METER_ID]

    @pytest.mark.asyncio
    async def test_api_error_raises_update_failed(self):
        client = MagicMock()
        client.get_smart_meter_telemetry = AsyncMock(side_effect=OctopusEnergyError("429"))
        coordinator = _make_gas_coordinator(client)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


class TestHomeMiniGasSensor:
    HALF_HOUR = GasHalfHour(
        read_at=datetime(2026, 11, 10, 8, 16, 50, tzinfo=UTC),
        period_start=datetime(2026, 11, 10, 7, 30, tzinfo=UTC),
        period_end=datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
        consumption_kwh=20936.28,
        half_hour_kwh=7.36,
    )

    def _sensor(self, data, key):
        coordinator = MagicMock()
        coordinator.data = data
        entry = MagicMock()
        entry.entry_id = "entry1"
        description = next(d for d in HOME_MINI_GAS_SENSOR_DESCRIPTIONS if d.key == key)
        return HomeMiniGasSensor(coordinator, description, entry, GAS_METER_ID)

    def test_register_and_half_hour(self):
        data = {GAS_METER_ID: self.HALF_HOUR}
        register = self._sensor(data, "home_mini_consumption")
        half_hour = self._sensor(data, "home_mini_last_half_hour")

        assert register.native_value == 20936.28
        assert half_hour.native_value == 7.36
        assert half_hour.extra_state_attributes == {
            "period_start": "2026-11-10T07:30:00+00:00",
            "period_end": "2026-11-10T08:00:00+00:00",
            "read_at": "2026-11-10T08:16:50+00:00",
        }
        assert half_hour.unique_id == f"entry1_{GAS_METER_ID}_home_mini_last_half_hour"

    def test_only_the_half_hour_total_has_a_last_reset(self):
        data = {GAS_METER_ID: self.HALF_HOUR}
        assert self._sensor(data, "home_mini_last_half_hour").last_reset == datetime(
            2026, 11, 10, 7, 30, tzinfo=UTC
        )
        assert self._sensor(data, "home_mini_consumption").last_reset is None

    def test_no_half_hour_is_unknown(self):
        sensor = self._sensor({}, "home_mini_last_half_hour")
        assert sensor.native_value is None
        assert sensor.last_reset is None
        assert sensor.extra_state_attributes is None


class TestMetersWithTelemetry:
    def test_only_meters_that_returned_readings(self):
        home_mini = MagicMock(last_update_success=True, data={GAS_METER_ID: object()})
        home_mini.meter_ids = [GAS_METER_ID, "9999999999_NOMINI"]
        assert _meters_with_telemetry(home_mini) == [GAS_METER_ID]

    def test_every_meter_when_the_first_poll_failed(self):
        home_mini = MagicMock(last_update_success=False, data=None)
        home_mini.meter_ids = [GAS_METER_ID]
        assert _meters_with_telemetry(home_mini) == [GAS_METER_ID]
