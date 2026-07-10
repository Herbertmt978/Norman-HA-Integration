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
from .helpers import BatteryMotorLabel, battery_motor_labels

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
    known_window_labels: dict[int, BatteryMotorLabel] = {}
    reload_scheduled = False

    @callback
    def add_discovered_entities() -> None:
        nonlocal known_window_labels, reload_scheduled
        labels_by_window_id: dict[int, BatteryMotorLabel] = {}
        for room_id, windows in coordinator.data.windows_by_room.items():
            room = coordinator.data.rooms_by_id.get(room_id)
            if room is None:
                continue
            labels_by_window_id.update(
                battery_motor_labels(
                    room.group_names,
                    windows,
                    coordinator.data.levels_by_room.get(room_id, []),
                )
            )
        if known_window_labels and any(
            window_id in coordinator.data.windows_by_id
            and labels_by_window_id.get(window_id) != label
            for window_id, label in known_window_labels.items()
        ):
            if not reload_scheduled:
                reload_scheduled = True
                hass.async_create_task(
                    hass.config_entries.async_reload(entry.entry_id),
                    f"reload {entry.title} after Norman motor labels change",
                )
            return

        entities: list[SensorEntity] = []
        for window in coordinator.data.windows:
            if window.id in known_windows:
                continue
            room = coordinator.data.rooms_by_id.get(window.room_id)
            if room is None:
                continue
            known_windows.add(window.id)
            entities.append(
                NormanWindowBatterySensor(
                    entry,
                    api,
                    coordinator,
                    room,
                    window,
                    labels_by_window_id.get(window.id),
                )
            )
        known_window_labels.update(
            {
                window_id: labels_by_window_id[window_id]
                for window_id in known_windows
                if window_id in labels_by_window_id
            }
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
        label: BatteryMotorLabel | None,
    ) -> None:
        """Initialize a physical-window battery sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._api = api
        self._room_id = room.id
        self._window_id = window.id
        self._attr_unique_id = f"{api.hub_id}_window_{window.id}_battery"
        translation_key, placeholders = self._motor_label_translation(window, label)
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = placeholders
        self._attr_device_info = room_device_info(api, room)

    @staticmethod
    def _motor_label_translation(
        window: NormanWindow,
        label: BatteryMotorLabel | None,
    ) -> tuple[str, dict[str, str]]:
        """Return translated entity-name metadata for one correlated motor."""
        if label is None or (label.name is None and label.group_number is None):
            return "unassigned_motor_battery", {"window_id": str(window.id)}
        if label.group_number is not None:
            if label.number is None:
                return "group_battery", {
                    "group_number": str(label.group_number),
                }
            return "group_numbered_motor_battery", {
                "group_number": str(label.group_number),
                "motor_number": str(label.number),
            }
        if label.name is None:
            return "unassigned_motor_battery", {"window_id": str(window.id)}
        if label.number is None:
            return "window_battery", {"window_name": label.name}
        return "group_motor_battery", {
            "group_name": label.name,
            "motor_number": str(label.number),
        }

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
