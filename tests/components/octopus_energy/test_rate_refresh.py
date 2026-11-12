"""Tests for rate sensors redrawing at half-hourly slot boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.octopus_energy.coordinator import MeterData
from custom_components.octopus_energy.sensor import (
    ELECTRICITY_SENSOR_DESCRIPTIONS,
    GAS_SENSOR_DESCRIPTIONS,
    OctopusEnergySensor,
    async_setup_entry,
)

RATE_KEYS = {"current_rate", "next_rate", "gas_current_rate"}


def _meter(meter_id: str, *, is_export: bool = False) -> MeterData:
    return MeterData(
        meter_id=meter_id,
        serial_number="22L4344979",
        tariff_code="E-1R-GO-VAR-22-10-14-B",
        product_code="GO-VAR-22-10-14",
        is_export=is_export,
        is_gas=False,
    )


def _sensor(key: str) -> OctopusEnergySensor:
    description = next(
        d for d in ELECTRICITY_SENSOR_DESCRIPTIONS + GAS_SENSOR_DESCRIPTIONS if d.key == key
    )
    coordinator = MagicMock()
    coordinator.config_entry.entry_id = "entry1"
    return OctopusEnergySensor(coordinator, description, "1100009640372_22L4344979")


class TestDescriptions:
    def test_only_rate_sensors_refresh_at_slot_boundaries(self):
        for description in ELECTRICITY_SENSOR_DESCRIPTIONS + GAS_SENSOR_DESCRIPTIONS:
            assert description.refresh_at_slot_boundaries == (description.key in RATE_KEYS)

    @pytest.mark.asyncio
    async def test_export_copies_keep_every_field(self):
        coordinator = MagicMock()
        coordinator.config_entry.entry_id = "entry1"
        coordinator.data.meters = {
            "1170001806920_22L4344979": _meter("1170001806920_22L4344979", is_export=True)
        }
        entry = MagicMock()
        entry.runtime_data.coordinator = coordinator
        entry.runtime_data.solar = None
        entry.runtime_data.home_mini = None
        entry.runtime_data.home_mini_gas = None
        add_entities = MagicMock()

        await async_setup_entry(MagicMock(), entry, add_entities)

        entities = add_entities.call_args.args[0]
        export_rate = next(
            e for e in entities
            if getattr(e, "entity_description", None) is not None
            and e.entity_description.key == "export_current_rate"
        )
        assert export_rate.entity_description.translation_key == "export_current_rate"
        assert export_rate.entity_description.refresh_at_slot_boundaries is True
        assert export_rate.entity_description.attrs_fn is not None


class TestSlotBoundaryRedraw:
    @pytest.mark.asyncio
    async def test_rate_sensor_redraws_on_the_half_hour(self):
        sensor = _sensor("current_rate")
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            new=AsyncMock(),
        ), patch(
            "custom_components.octopus_energy.sensor.async_track_time_change"
        ) as track:
            await sensor.async_added_to_hass()

        assert track.call_args.kwargs == {"minute": (0, 30), "second": 0}
        listener = track.call_args.args[1]
        listener(datetime(2026, 11, 10, 0, 30, tzinfo=UTC))
        sensor.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_sensors_do_not_track_time(self):
        sensor = _sensor("previous_consumption")
        sensor.hass = MagicMock()

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            new=AsyncMock(),
        ), patch(
            "custom_components.octopus_energy.sensor.async_track_time_change"
        ) as track:
            await sensor.async_added_to_hass()

        track.assert_not_called()
