"""Unconverted ShadeAuto battery readings for protocol troubleshooting."""

from __future__ import annotations

import math
from typing import cast

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.norman_gen1.const import DOMAIN

from .coordinator import NormanCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add optional diagnostic readings without assuming volts or percentages."""
    coordinator = cast(NormanCoordinator, entry.runtime_data)
    async_add_entities(
        NormanBatteryReading(coordinator, entry, device_id)
        for device_id in coordinator.data
    )


class NormanBatteryReading(CoordinatorEntity[NormanCoordinator], SensorEntity):
    """Expose the hub's BatteryVoltage value without a fabricated unit."""

    _attr_has_entity_name = True
    _attr_name = "Battery reading"
    _attr_icon = "mdi:battery"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: NormanCoordinator, entry: ConfigEntry, device_id: int
    ) -> None:
        """Attach the reading to the existing shade device."""
        super().__init__(coordinator)
        self._device_id = device_id
        device = coordinator.data[device_id]
        self._attr_unique_id = f"{entry.unique_id}_{device_id}_battery_reading"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.unique_id}_{device_id}")},
            name=device.name or f"Norman Cover {device_id}",
            manufacturer="Norman",
            model=f"Window Covering {device.module_type}",
            suggested_area=device.room_name,
        )

    @property
    def available(self) -> bool:
        """A removed shade or unavailable hub cannot provide a reading."""
        return (
            self.coordinator.last_update_success
            and self._device_id in self.coordinator.data
        )

    @property
    def native_value(self) -> float | int | None:
        """Keep finite numeric readings as reported, including legitimate zero."""
        device = self.coordinator.data.get(self._device_id)
        value = device.battery_level if device is not None else None
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            return None
        return value
