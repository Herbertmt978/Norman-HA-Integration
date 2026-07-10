"""Sensor platform for the Norman Gen 1 integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NormanGen1Api, NormanRoom, NormanWindow
from .coordinator import NormanConfigEntry, NormanDataUpdateCoordinator
from .device import room_device_info
from .helpers import clean_label

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up battery sensors for physical Norman window motors."""
    coordinator = entry.runtime_data
    api = coordinator.api
    known_windows: set[int] = set()

    @callback
    def add_discovered_entities() -> None:
        entities: list[SensorEntity] = []
        for window in coordinator.data.windows:
            if window.id in known_windows:
                continue
            room = coordinator.data.rooms_by_id.get(window.room_id)
            if room is None:
                continue
            known_windows.add(window.id)
            entities.append(
                NormanWindowBatterySensor(entry, api, coordinator, room, window)
            )
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_discovered_entities))
    add_discovered_entities()


class NormanWindowBatterySensor(
    CoordinatorEntity[NormanDataUpdateCoordinator], SensorEntity
):
    """Battery percentage reported by one physical Norman window motor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "window_battery"

    def __init__(
        self,
        entry: NormanConfigEntry,
        api: NormanGen1Api,
        coordinator: NormanDataUpdateCoordinator,
        room: NormanRoom,
        window: NormanWindow,
    ) -> None:
        """Initialize a physical-window battery sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._api = api
        self._room_id = room.id
        self._window_id = window.id
        self._attr_unique_id = f"{api.hub_id}_window_{window.id}_battery"
        self._attr_translation_placeholders = {"window_name": clean_label(window.name)}
        self._attr_device_info = room_device_info(api, room)

    @property
    def _current_window(self) -> NormanWindow | None:
        """Return this motor from the latest coordinator snapshot."""
        return self.coordinator.data.windows_by_id.get(self._window_id)

    @property
    def available(self) -> bool:
        """Return whether the physical motor remains in the latest snapshot."""
        return super().available and self._current_window is not None

    @property
    def native_value(self) -> int | None:
        """Return a validated battery percentage without inventing a reading."""
        window = self._current_window
        if window is None:
            return None
        battery = window.battery
        if isinstance(battery, bool) or not isinstance(battery, int):
            return None
        return battery if 0 <= battery <= 100 else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Move the stable sensor if the hub assigns its motor to another room."""
        window = self._current_window
        if window is not None and window.room_id != self._room_id:
            room = self.coordinator.data.rooms_by_id.get(window.room_id)
            if room is not None:
                device = dr.async_get(self.hass).async_get_or_create(
                    config_entry_id=self._entry.entry_id,
                    **room_device_info(self._api, room),
                )
                if self.entity_id is not None:
                    registry = er.async_get(self.hass)
                    registry_entry = registry.async_get(self.entity_id)
                    if (
                        registry_entry is not None
                        and registry_entry.device_id != device.id
                    ):
                        registry.async_update_entity(
                            self.entity_id,
                            device_id=device.id,
                        )
                self._attr_device_info = room_device_info(self._api, room)
                self._room_id = room.id
        super()._handle_coordinator_update()
