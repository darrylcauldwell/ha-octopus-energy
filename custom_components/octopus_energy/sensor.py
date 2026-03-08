"""Sensor platform for Octopus Energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .comparison_coordinator import (
    TariffComparisonCoordinator,
    TariffComparisonData,
)
from .const import DOMAIN
from .solar_coordinator import SolarEstimateCoordinator, SolarEstimateData
from .coordinator import (
    CarbonIntensityPeriod,
    MeterData,
    OctopusEnergyConfigEntry,
    OctopusEnergyCoordinator,
    OctopusEnergyData,
)
from .entity import OctopusEnergyEntity

PARALLEL_UPDATES = 0

CURRENCY_PENCE = "p"
CURRENCY_PENCE_PER_KWH = "p/kWh"
CURRENCY_POUNDS = "GBP"


def _get_current_rate(meter: MeterData) -> float | None:
    """Get the current half-hourly rate."""
    now = datetime.now(UTC)
    for rate in meter.rates:
        if rate.valid_from <= now and (
            rate.valid_to is None or rate.valid_to > now
        ):
            return rate.value_inc_vat
    return None


def _get_next_rate(meter: MeterData) -> float | None:
    """Get the next half-hourly rate."""
    now = datetime.now(UTC)
    future_rates = [
        r for r in meter.rates if r.valid_from > now
    ]
    if future_rates:
        future_rates.sort(key=lambda r: r.valid_from)
        return future_rates[0].value_inc_vat
    return None


def _get_previous_consumption(meter: MeterData) -> float | None:
    """Get total consumption for yesterday."""
    if not meter.consumption:
        return None
    return sum(c.consumption for c in meter.consumption)


def _get_previous_cost(meter: MeterData) -> float | None:
    """Get total cost for yesterday in pounds."""
    if not meter.consumption or not meter.rates:
        return None

    total_cost = 0.0
    for reading in meter.consumption:
        # Find matching rate for this interval
        rate_value = None
        for rate in meter.rates:
            if rate.valid_from <= reading.interval_start and (
                rate.valid_to is None or rate.valid_to >= reading.interval_end
            ):
                rate_value = rate.value_inc_vat
                break
        if rate_value is not None:
            total_cost += reading.consumption * rate_value

    return round(total_cost / 100.0, 2)  # Convert pence to pounds


def _get_standing_charge(meter: MeterData) -> float | None:
    """Get the current standing charge."""
    now = datetime.now(UTC)
    for charge in meter.standing_charges:
        if charge.valid_from <= now and (
            charge.valid_to is None or charge.valid_to > now
        ):
            return charge.value_inc_vat
    return meter.standing_charges[0].value_inc_vat if meter.standing_charges else None


def _get_consumption_attrs(meter: MeterData) -> dict[str, Any]:
    """Get consumption details as attributes."""
    if not meter.consumption:
        return {}
    return {
        "charges": [
            {
                "start": c.interval_start.isoformat(),
                "end": c.interval_end.isoformat(),
                "consumption": c.consumption,
            }
            for c in sorted(meter.consumption, key=lambda c: c.interval_start)
        ]
    }


def _get_cost_attrs(meter: MeterData) -> dict[str, Any]:
    """Get cost details per half-hour as attributes."""
    if not meter.consumption or not meter.rates:
        return {}

    charges = []
    for reading in sorted(meter.consumption, key=lambda c: c.interval_start):
        rate_value = None
        for rate in meter.rates:
            if rate.valid_from <= reading.interval_start and (
                rate.valid_to is None or rate.valid_to >= reading.interval_end
            ):
                rate_value = rate.value_inc_vat
                break
        charges.append(
            {
                "start": reading.interval_start.isoformat(),
                "end": reading.interval_end.isoformat(),
                "consumption": reading.consumption,
                "rate": rate_value,
                "cost": round(reading.consumption * rate_value / 100.0, 4)
                if rate_value
                else None,
            }
        )
    return {"charges": charges}


def _get_rate_attrs(meter: MeterData) -> dict[str, Any]:
    """Get rate details as attributes."""
    now = datetime.now(UTC)
    for rate in meter.rates:
        if rate.valid_from <= now and (
            rate.valid_to is None or rate.valid_to > now
        ):
            attrs: dict[str, Any] = {
                "start": rate.valid_from.isoformat(),
                "tariff_code": meter.tariff_code,
            }
            if rate.valid_to:
                attrs["end"] = rate.valid_to.isoformat()
            return attrs
    return {"tariff_code": meter.tariff_code}


def _get_next_rate_attrs(meter: MeterData) -> dict[str, Any]:
    """Get next rate details as attributes."""
    now = datetime.now(UTC)
    future_rates = [r for r in meter.rates if r.valid_from > now]
    if future_rates:
        future_rates.sort(key=lambda r: r.valid_from)
        rate = future_rates[0]
        attrs: dict[str, Any] = {"start": rate.valid_from.isoformat()}
        if rate.valid_to:
            attrs["end"] = rate.valid_to.isoformat()
        return attrs
    return {}


UNIT_GRAMS_CO2_PER_KWH = "gCO2/kWh"


def _enrich_charges_with_carbon(
    charges: list[dict[str, Any]],
    carbon_data: list[CarbonIntensityPeriod],
) -> dict[str, Any]:
    """Match charges to carbon periods by timestamp and add carbon data."""
    if not charges or not carbon_data:
        return {}

    carbon_by_start = {p.from_dt.isoformat(): p for p in carbon_data}

    total_carbon_grams = 0.0
    total_kwh = 0.0
    high_carbon_kwh = 0.0
    low_carbon_kwh = 0.0

    for charge in charges:
        start_iso = charge["start"]
        period = carbon_by_start.get(start_iso)
        if period:
            intensity = period.actual if period.actual is not None else period.forecast
            charge["carbon_intensity"] = intensity
            charge["carbon_index"] = period.index
            kwh = charge.get("consumption", 0)
            charge["carbon_grams"] = round(kwh * intensity, 1)
            total_carbon_grams += kwh * intensity
            total_kwh += kwh
            if period.index in ("high", "very high"):
                high_carbon_kwh += kwh
            elif period.index in ("low", "very low"):
                low_carbon_kwh += kwh

    if total_kwh == 0:
        return {}

    weighted_avg = round(total_carbon_grams / total_kwh, 1) if total_kwh else 0

    return {
        "carbon_summary": {
            "total_grams_co2": round(total_carbon_grams, 1),
            "weighted_avg_intensity": weighted_avg,
            "high_carbon_kwh": round(high_carbon_kwh, 2),
            "low_carbon_kwh": round(low_carbon_kwh, 2),
            "high_carbon_pct": round(high_carbon_kwh / total_kwh * 100, 1),
            "low_carbon_pct": round(low_carbon_kwh / total_kwh * 100, 1),
        },
        "optimization": _compute_optimal_windows(charges, carbon_data),
    }


def _compute_optimal_windows(
    charges: list[dict[str, Any]],
    carbon_data: list[CarbonIntensityPeriod],
) -> dict[str, Any]:
    """Find cheapest and greenest 2h windows from yesterday's data."""
    # Build sorted list of periods with both rate and carbon data
    carbon_by_start = {p.from_dt.isoformat(): p for p in carbon_data}
    periods = []
    for charge in charges:
        period = carbon_by_start.get(charge["start"])
        rate = charge.get("rate")
        if period and rate is not None:
            intensity = period.actual if period.actual is not None else period.forecast
            periods.append({
                "start": charge["start"],
                "end": charge["end"],
                "rate": rate,
                "carbon_intensity": intensity,
            })

    if len(periods) < 4:
        return {}

    # Find best 2h (4 consecutive half-hours) windows
    best_cost_sum = float("inf")
    best_cost_window = None
    best_carbon_sum = float("inf")
    best_carbon_window = None

    for i in range(len(periods) - 3):
        window = periods[i : i + 4]
        cost_sum = sum(p["rate"] for p in window)
        carbon_sum = sum(p["carbon_intensity"] for p in window)
        if cost_sum < best_cost_sum:
            best_cost_sum = cost_sum
            best_cost_window = window
        if carbon_sum < best_carbon_sum:
            best_carbon_sum = carbon_sum
            best_carbon_window = window

    result: dict[str, Any] = {}
    if best_cost_window:
        result["cheapest_2h_window"] = {
            "start": best_cost_window[0]["start"],
            "end": best_cost_window[-1]["end"],
            "avg_rate": round(best_cost_sum / 4, 2),
        }
    if best_carbon_window:
        result["greenest_2h_window"] = {
            "start": best_carbon_window[0]["start"],
            "end": best_carbon_window[-1]["end"],
            "avg_intensity": round(best_carbon_sum / 4, 1),
        }
    return result


@dataclass(frozen=True, kw_only=True)
class OctopusEnergySensorDescription(SensorEntityDescription):
    """Describes an Octopus Energy sensor entity."""

    value_fn: Callable[[MeterData], StateType]
    attrs_fn: Callable[[MeterData], dict[str, Any]] | None = None


ELECTRICITY_SENSOR_DESCRIPTIONS: tuple[OctopusEnergySensorDescription, ...] = (
    OctopusEnergySensorDescription(
        key="current_rate",
        translation_key="current_rate",
        native_unit_of_measurement=CURRENCY_PENCE_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_get_current_rate,
        attrs_fn=_get_rate_attrs,
    ),
    OctopusEnergySensorDescription(
        key="next_rate",
        translation_key="next_rate",
        native_unit_of_measurement=CURRENCY_PENCE_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_get_next_rate,
        attrs_fn=_get_next_rate_attrs,
    ),
    OctopusEnergySensorDescription(
        key="previous_consumption",
        translation_key="previous_consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_get_previous_consumption,
        attrs_fn=_get_consumption_attrs,
    ),
    OctopusEnergySensorDescription(
        key="previous_cost",
        translation_key="previous_cost",
        native_unit_of_measurement=CURRENCY_POUNDS,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_get_previous_cost,
        attrs_fn=_get_cost_attrs,
    ),
    OctopusEnergySensorDescription(
        key="standing_charge",
        translation_key="standing_charge",
        native_unit_of_measurement=CURRENCY_PENCE,
        suggested_display_precision=2,
        value_fn=_get_standing_charge,
    ),
)

GAS_SENSOR_DESCRIPTIONS: tuple[OctopusEnergySensorDescription, ...] = (
    OctopusEnergySensorDescription(
        key="gas_current_rate",
        translation_key="gas_current_rate",
        native_unit_of_measurement=CURRENCY_PENCE_PER_KWH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_get_current_rate,
        attrs_fn=_get_rate_attrs,
    ),
    OctopusEnergySensorDescription(
        key="gas_previous_consumption",
        translation_key="gas_previous_consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_get_previous_consumption,
        attrs_fn=_get_consumption_attrs,
    ),
    OctopusEnergySensorDescription(
        key="gas_previous_cost",
        translation_key="gas_previous_cost",
        native_unit_of_measurement=CURRENCY_POUNDS,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_get_previous_cost,
        attrs_fn=_get_cost_attrs,
    ),
    OctopusEnergySensorDescription(
        key="gas_standing_charge",
        translation_key="gas_standing_charge",
        native_unit_of_measurement=CURRENCY_PENCE,
        suggested_display_precision=2,
        value_fn=_get_standing_charge,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctopusEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Octopus Energy sensor entities."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    comparison = runtime_data.comparison
    entities: list[SensorEntity] = []

    for meter_id, meter in coordinator.data.meters.items():
        if meter.is_gas:
            for description in GAS_SENSOR_DESCRIPTIONS:
                entities.append(
                    OctopusEnergySensor(coordinator, description, meter_id)
                )
        else:
            prefix = "export_" if meter.is_export else ""
            for description in ELECTRICITY_SENSOR_DESCRIPTIONS:
                desc = OctopusEnergySensorDescription(
                    key=f"{prefix}{description.key}",
                    translation_key=f"{prefix}{description.translation_key}"
                    if prefix
                    else description.translation_key,
                    native_unit_of_measurement=description.native_unit_of_measurement,
                    device_class=description.device_class,
                    state_class=description.state_class,
                    suggested_display_precision=description.suggested_display_precision,
                    value_fn=description.value_fn,
                    attrs_fn=description.attrs_fn,
                )
                entities.append(
                    OctopusEnergySensor(coordinator, desc, meter_id)
                )

    # Add tariff comparison sensor
    entities.append(TariffComparisonSensor(comparison, entry))

    # Add carbon correlation sensor
    entities.append(CarbonCorrelationSensor(coordinator, entry))

    # Add solar estimate sensor if configured
    if runtime_data.solar is not None:
        entities.append(SolarEstimateSensor(runtime_data.solar, entry))

    async_add_entities(entities)


class OctopusEnergySensor(OctopusEnergyEntity, SensorEntity):
    """Sensor entity for Octopus Energy."""

    entity_description: OctopusEnergySensorDescription

    def __init__(
        self,
        coordinator: OctopusEnergyCoordinator,
        description: OctopusEnergySensorDescription,
        meter_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description, meter_id)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        meter = self.coordinator.data.meters.get(self._meter_id)
        if meter is None:
            return None
        return self.entity_description.value_fn(meter)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes, enriched with carbon data for cost sensors."""
        if self.entity_description.attrs_fn is None:
            return None
        meter = self.coordinator.data.meters.get(self._meter_id)
        if meter is None:
            return None
        attrs = self.entity_description.attrs_fn(meter)
        # Enrich cost sensors with carbon intensity correlation
        if (
            self.entity_description.key.endswith("previous_cost")
            and "charges" in attrs
            and self.coordinator.data.carbon_intensity
        ):
            carbon_attrs = _enrich_charges_with_carbon(
                attrs["charges"], self.coordinator.data.carbon_intensity
            )
            attrs.update(carbon_attrs)
        return attrs


class TariffComparisonSensor(
    CoordinatorEntity[TariffComparisonCoordinator], SensorEntity
):
    """Sensor showing cheapest tariff cost from comparison analysis."""

    _attr_has_entity_name = True
    _attr_translation_key = "tariff_comparison"
    _attr_native_unit_of_measurement = CURRENCY_POUNDS
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-bar"

    def __init__(
        self,
        coordinator: TariffComparisonCoordinator,
        entry: OctopusEnergyConfigEntry,
    ) -> None:
        """Initialize the comparison sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tariff_comparison"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_comparison")},
            name="Octopus Energy Tariff Comparison",
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Octopus Energy",
        )

    @property
    def native_value(self) -> StateType:
        """Return the cheapest tariff's total cost."""
        data = self.coordinator.data
        if not data or not data.tariffs:
            return None
        valid = [t for t in data.tariffs if t.error is None and t.total_cost > 0]
        if not valid:
            return None
        return min(t.total_cost for t in valid)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return tariff comparison data as attributes."""
        data = self.coordinator.data
        if not data or not data.tariffs:
            return None

        attrs: dict[str, Any] = {
            "tariffs": [
                {
                    "product_code": t.product_code,
                    "display_name": t.display_name,
                    "tariff_code": t.tariff_code,
                    "is_current": t.is_current,
                    "total_cost": t.total_cost,
                    "error": t.error,
                    "months": [
                        {
                            "month": m.month,
                            "days_with_data": m.days_with_data,
                            "days_in_month": m.days_in_month,
                            "unit_cost": m.unit_cost,
                            "standing_cost": m.standing_cost,
                            "total_cost": m.total_cost,
                            "consumption_kwh": m.consumption_kwh,
                        }
                        for m in t.months
                    ],
                }
                for t in data.tariffs
            ],
            "months": data.months,
            "total_consumption_kwh": data.total_consumption_kwh,
            "gsp_region": data.gsp_region,
            "updated_at": data.updated_at,
        }

        if data.octopus_comparisons:
            attrs["octopus_comparison"] = {
                "current_cost": data.octopus_current_cost,
                "alternatives": [
                    {
                        "product_code": c.product_code,
                        "tariff_code": c.tariff_code,
                        "cost": c.cost_inc_vat,
                    }
                    for c in data.octopus_comparisons
                ],
            }

        return attrs


class CarbonCorrelationSensor(
    CoordinatorEntity[OctopusEnergyCoordinator], SensorEntity
):
    """Sensor showing weighted average carbon intensity of yesterday's consumption."""

    _attr_has_entity_name = True
    _attr_translation_key = "carbon_correlation"
    _attr_native_unit_of_measurement = UNIT_GRAMS_CO2_PER_KWH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:molecule-co2"

    def __init__(
        self,
        coordinator: OctopusEnergyCoordinator,
        entry: OctopusEnergyConfigEntry,
    ) -> None:
        """Initialize the carbon correlation sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_carbon_correlation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_carbon")},
            name="Octopus Energy Carbon Correlation",
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Octopus Energy",
        )

    def _get_import_meter(self) -> MeterData | None:
        """Get the non-export electricity meter."""
        for meter in self.coordinator.data.meters.values():
            if not meter.is_gas and not meter.is_export:
                return meter
        return None

    @property
    def native_value(self) -> StateType:
        """Return weighted average carbon intensity of yesterday's consumption."""
        meter = self._get_import_meter()
        carbon = self.coordinator.data.carbon_intensity
        if not meter or not meter.consumption or not carbon:
            return None

        carbon_by_start = {p.from_dt.isoformat(): p for p in carbon}
        total_grams = 0.0
        total_kwh = 0.0

        for reading in meter.consumption:
            period = carbon_by_start.get(reading.interval_start.isoformat())
            if period:
                intensity = (
                    period.actual if period.actual is not None else period.forecast
                )
                total_grams += reading.consumption * intensity
                total_kwh += reading.consumption

        if total_kwh == 0:
            return None
        return round(total_grams / total_kwh, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return carbon summary and optimization insights."""
        meter = self._get_import_meter()
        carbon = self.coordinator.data.carbon_intensity
        if not meter or not meter.consumption or not carbon:
            return None

        charges = []
        for reading in sorted(meter.consumption, key=lambda c: c.interval_start):
            rate_value = None
            for rate in meter.rates:
                if rate.valid_from <= reading.interval_start and (
                    rate.valid_to is None or rate.valid_to >= reading.interval_end
                ):
                    rate_value = rate.value_inc_vat
                    break
            charges.append({
                "start": reading.interval_start.isoformat(),
                "end": reading.interval_end.isoformat(),
                "consumption": reading.consumption,
                "rate": rate_value,
            })

        return _enrich_charges_with_carbon(charges, carbon)


class SolarEstimateSensor(
    CoordinatorEntity[SolarEstimateCoordinator], SensorEntity
):
    """Sensor showing estimated solar generation for today."""

    _attr_has_entity_name = True
    _attr_translation_key = "solar_estimate"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: SolarEstimateCoordinator,
        entry: OctopusEnergyConfigEntry,
    ) -> None:
        """Initialize the solar estimate sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_solar_estimate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_solar")},
            name="Octopus Energy Solar Estimate",
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Octopus Energy",
        )

    @property
    def native_value(self) -> StateType:
        """Return today's estimated solar generation in kWh."""
        data = self.coordinator.data
        if not data:
            return None
        return data.today_total_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return hourly estimates as attributes."""
        data = self.coordinator.data
        if not data or not data.hourly_estimates:
            return None

        return {
            "hourly_estimates": [
                {
                    "date": e.date,
                    "hour": e.hour,
                    "value": e.value,
                }
                for e in data.hourly_estimates
            ],
            "updated_at": data.updated_at,
        }
