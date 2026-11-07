"""Tests for Octopus Energy diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.octopus_energy.coordinator import (
    MeterData,
    OctopusEnergyCoordinator,
    OctopusEnergyData,
)
from custom_components.octopus_energy.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import MOCK_ACCOUNT


def _coordinator(last_success_time):
    coord = object.__new__(OctopusEnergyCoordinator)
    coord.last_update_success = True
    coord.last_update_success_time = last_success_time
    coord.data = OctopusEnergyData(
        account=MOCK_ACCOUNT,
        meters={
            "m1": MeterData(
                meter_id="m1", serial_number="S1",
                tariff_code="E-1R-GO-VAR-22-10-14-B", product_code="GO-VAR-22-10-14",
                is_export=False, is_gas=False,
            )
        },
    )
    return coord


def _entry(coordinator):
    entry = MagicMock()
    entry.as_dict.return_value = {"data": {"api_key": "sk_live_secret"}}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(
        coordinator=coordinator, comparison=SimpleNamespace(data=None), solar=None
    )
    return entry


async def test_diagnostics_report_last_successful_update():
    updated = datetime(2026, 11, 7, 17, 0, tzinfo=UTC)

    result = await async_get_config_entry_diagnostics(MagicMock(), _entry(_coordinator(updated)))

    assert result["coordinator_info"]["last_updated"] == updated.isoformat()
    assert result["meters"]["m1"]["product_code"] == "GO-VAR-22-10-14"
    assert result["config_entry"]["data"]["api_key"] == "**REDACTED**"


async def test_diagnostics_before_first_successful_update():
    coord = object.__new__(OctopusEnergyCoordinator)
    coord.last_update_success = False
    coord.data = _coordinator(None).data

    result = await async_get_config_entry_diagnostics(MagicMock(), _entry(coord))

    assert result["coordinator_info"]["last_updated"] is None
