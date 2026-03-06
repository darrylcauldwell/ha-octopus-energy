"""Base entity for Octopus Energy."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OctopusEnergyCoordinator


class OctopusEnergyEntity(CoordinatorEntity[OctopusEnergyCoordinator]):
    """Base entity for Octopus Energy."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OctopusEnergyCoordinator,
        description: EntityDescription,
        meter_id: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{meter_id}_{description.key}"
        )
        self._meter_id = meter_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{meter_id}")},
            name=f"Octopus Energy ({meter_id})",
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Octopus Energy",
        )
