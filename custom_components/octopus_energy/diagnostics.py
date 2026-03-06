"""Diagnostics support for Octopus Energy."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
from .coordinator import OctopusEnergyConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: OctopusEnergyConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    comparison = runtime_data.comparison
    data = coordinator.data

    meters_info: dict[str, Any] = {}
    for meter_id, meter in data.meters.items():
        meters_info[meter_id] = {
            "serial_number": meter.serial_number,
            "tariff_code": meter.tariff_code,
            "product_code": meter.product_code,
            "is_export": meter.is_export,
            "is_gas": meter.is_gas,
            "rates_count": len(meter.rates),
            "consumption_count": len(meter.consumption),
            "standing_charges_count": len(meter.standing_charges),
        }

    comparison_data = comparison.data
    comparison_info: dict[str, Any] = {
        "tariff_count": len(comparison_data.tariffs) if comparison_data else 0,
        "months": comparison_data.months if comparison_data else [],
        "total_consumption_kwh": comparison_data.total_consumption_kwh
        if comparison_data
        else 0,
        "gsp_region": comparison_data.gsp_region if comparison_data else "",
    }

    return {
        "config_entry": async_redact_data(config_entry.as_dict(), TO_REDACT),
        "coordinator_info": {
            "last_updated": coordinator.last_update_success_time.isoformat()
            if coordinator.last_update_success_time
            else None,
            "update_interval_minutes": config_entry.options.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
            "last_update_success": coordinator.last_update_success,
        },
        "account": {
            "number": data.account.number,
            "property_count": len(data.account.properties),
        },
        "meters": meters_info,
        "comparison": comparison_info,
    }
