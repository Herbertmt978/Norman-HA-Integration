"""Cover platform for the Norman Gen 1 integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    NormanGen1Api,
    NormanRoom,
    NormanWindow,
    PositionProfile,
    ha_position_to_hub,
    resolve_position_profile,
    room_target_id,
)
from .const import CONF_SIMULTANEOUS_ROOMS, DOMAIN
from .coordinator import NormanConfigEntry, NormanDataUpdateCoordinator
from .entity import NormanBaseCover
from .helpers import clean_label, group_name
from .profiles import configured_group_levels, resolve_configured_profile

PARALLEL_UPDATES = 1

LEVEL_FANOUT = "level_fanout"
ROOM_BROADCAST = "room_broadcast"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RoomCommandRouting:
    """Describe the effective room command path for the current snapshot."""

    command_levels: tuple[int, ...]
    simultaneous_selected: bool
    open_mode: str
    close_mode: str
    position_mode: str


def resolve_room_command_routing(
    options: Mapping[str, Any],
    room: NormanRoom,
    windows: Iterable[NormanWindow],
    discovered_levels: Iterable[int],
) -> RoomCommandRouting:
    """Choose safe room command paths from correlated discovery and options."""
    window_list = list(windows)
    command_levels = tuple(
        sorted(
            {level for level in discovered_levels if level >= 0}
            | configured_group_levels(options, room.id)
        )
    )
    can_fan_out = bool(command_levels) and all(
        window.level >= 0 for window in window_list
    )
    raw_simultaneous_rooms = options.get(CONF_SIMULTANEOUS_ROOMS)
    simultaneous_selected = (
        isinstance(raw_simultaneous_rooms, list)
        and room_target_id(room.id) in raw_simultaneous_rooms
    )

    profiles = [
        resolve_configured_profile(options, room.id, level) for level in command_levels
    ]
    if any(window.level < 0 for window in window_list):
        profiles.append(resolve_configured_profile(options, room.id))
    if not profiles:
        profiles.append(resolve_configured_profile(options, room.id))
    native_profile = resolve_position_profile(room.raw)

    def endpoint_mode(attribute: str) -> str:
        native_target = int(getattr(native_profile, attribute))
        broadcast_is_safe = all(
            int(getattr(profile, attribute)) == native_target for profile in profiles
        )
        if broadcast_is_safe and (simultaneous_selected or not can_fan_out):
            return ROOM_BROADCAST
        return LEVEL_FANOUT if can_fan_out else UNSUPPORTED

    return RoomCommandRouting(
        command_levels=command_levels,
        simultaneous_selected=simultaneous_selected,
        open_mode=endpoint_mode("open_position"),
        close_mode=endpoint_mode("close_position"),
        position_mode=LEVEL_FANOUT if can_fan_out else UNSUPPORTED,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Norman room and group covers."""
    coordinator = entry.runtime_data
    api = coordinator.api
    known_entities: set[tuple[str, int, int | None]] = set()

    @callback
    def add_discovered_entities() -> None:
        entities: list[CoverEntity] = []
        for room in coordinator.data.rooms:
            room_key = ("room", room.id, None)
            if room_key not in known_entities:
                known_entities.add(room_key)
                entities.append(NormanRoomCover(entry, api, coordinator, room))
            levels = coordinator.data.levels_by_room.get(room.id, [])
            for level in levels:
                group_key = ("group", room.id, level)
                if group_key in known_entities:
                    continue
                known_entities.add(group_key)
                entities.append(
                    NormanGroupCover(
                        entry,
                        api,
                        coordinator,
                        room,
                        level,
                        group_name(room.group_names, level, levels),
                    )
                )
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_discovered_entities))
    add_discovered_entities()


class NormanRoomCover(NormanBaseCover):
    """Room-wide cover using discovered group commands."""

    def __init__(
        self,
        entry: NormanConfigEntry,
        api: NormanGen1Api,
        coordinator: NormanDataUpdateCoordinator,
        room: NormanRoom,
    ) -> None:
        """Initialize a room cover."""
        super().__init__(entry, api, coordinator, room)
        self._attr_unique_id = f"{api.hub_id}_room_{room.id}"
        self._attr_name = None

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Expose only commands that can be represented by discovered levels."""
        features = CoverEntityFeature(0)
        routing = self._routing
        if routing.open_mode != UNSUPPORTED:
            features |= CoverEntityFeature.OPEN
        if routing.close_mode != UNSUPPORTED:
            features |= CoverEntityFeature.CLOSE
        if routing.position_mode != UNSUPPORTED:
            features |= CoverEntityFeature.SET_POSITION
        return features

    @property
    def _routing(self) -> RoomCommandRouting:
        return resolve_room_command_routing(
            self.entry.options,
            self._current_room,
            self.coordinator.data.windows_by_room.get(self.room.id, []),
            self.coordinator.data.levels_by_room.get(self.room.id, []),
        )

    def _hub_positions(self) -> list[int | None]:
        return [
            window.position
            for window in self.coordinator.data.windows_by_room.get(self.room.id, [])
        ]

    def _hub_position_samples(self) -> list[tuple[int | None, PositionProfile]]:
        """Normalize every physical window with its effective panel profile."""
        return [
            (
                window.position,
                resolve_configured_profile(
                    self.entry.options,
                    self.room.id,
                    window.level if window.level >= 0 else None,
                ),
            )
            for window in self.coordinator.data.windows_by_room.get(self.room.id, [])
        ]

    def _levels(self) -> list[int]:
        return [
            level
            for level in self.coordinator.data.levels_by_room.get(self.room.id, [])
            if level >= 0
        ]

    def _command_levels(self) -> list[int]:
        return list(self._routing.command_levels)

    def _models_by_level(self) -> dict[int, int]:
        models: dict[int, int] = {}
        for level in self._command_levels():
            for window in self.coordinator.data.windows_by_group.get(
                (self.room.id, level), []
            ):
                models[level] = window.model
                break
        return models

    def _profiles_by_level(self) -> dict[int, PositionProfile]:
        return {
            level: resolve_configured_profile(
                self.entry.options,
                self.room.id,
                level,
            )
            for level in self._command_levels()
        }

    def _endpoint_positions(self, attribute: str) -> dict[int, int]:
        return {
            level: int(getattr(profile, attribute))
            for level, profile in self._profiles_by_level().items()
        }

    def _mapped_positions(self, position: int) -> dict[int, int]:
        return {
            level: ha_position_to_hub(position, profile)
            for level, profile in self._profiles_by_level().items()
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose safe command-planning details for troubleshooting."""
        routing = self._routing
        open_positions = self._endpoint_positions("open_position")
        close_positions = self._endpoint_positions("close_position")
        models = self._models_by_level()
        return {
            "room_id": self.room.id,
            "window_ids": self._window_ids(),
            "levels": self._levels(),
            "simultaneous_room_selected": routing.simultaneous_selected,
            "open_command": routing.open_mode,
            "close_command": routing.close_mode,
            "position_command": routing.position_mode,
            "level_command_plan": [
                {
                    "level": level,
                    "model": models.get(level, 1),
                    "group_ids": self._group_ids_for_level(level),
                    "window_ids": self._window_ids_for_level(level),
                    "open_position": open_positions.get(level),
                    "close_position": close_positions.get(level),
                }
                for level in self._command_levels()
            ],
        }

    def _window_ids(self) -> list[int]:
        return sorted(
            window.id
            for window in self.coordinator.data.windows_by_room.get(self.room.id, [])
        )

    def _window_ids_for_level(self, level: int) -> list[int]:
        return sorted(
            window.id
            for window in self.coordinator.data.windows_by_group.get(
                (self.room.id, level), []
            )
        )

    def _group_ids_for_level(self, level: int) -> list[int]:
        return sorted(
            {
                group_id
                for window in self.coordinator.data.windows_by_group.get(
                    (self.room.id, level), []
                )
                if (group_id := window.group_id) is not None
            }
        )

    def _can_fan_out(self) -> bool:
        return self._routing.position_mode == LEVEL_FANOUT

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the room to its visual-open target."""
        mode = self._routing.open_mode
        if mode == ROOM_BROADCAST:
            await self._run_control_command(
                lambda: self.api.full_open_room(self.room.id),
                100,
            )
            return
        if mode != LEVEL_FANOUT:
            self._raise_unsupported_position()
        positions = self._endpoint_positions("open_position")
        if not positions:
            self._raise_unsupported_position()
        models = self._models_by_level()
        await self._run_control_command(
            lambda: self.api.set_room_positions(
                self.room.id,
                positions,
                models,
            ),
            100,
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the room on its configured movement branch."""
        mode = self._routing.close_mode
        if mode == ROOM_BROADCAST:
            await self._run_control_command(
                lambda: self.api.full_close_room(self.room.id),
                0,
            )
            return
        if mode != LEVEL_FANOUT:
            self._raise_unsupported_position()
        positions = self._endpoint_positions("close_position")
        if not positions:
            self._raise_unsupported_position()
        models = self._models_by_level()
        await self._run_control_command(
            lambda: self.api.set_room_positions(
                self.room.id,
                positions,
                models,
            ),
            0,
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set a Home Assistant position on the configured hub branch."""
        if not self._can_fan_out():
            self._raise_unsupported_position()
        position = max(0, min(100, int(kwargs[ATTR_POSITION])))
        positions = self._mapped_positions(position)
        models = self._models_by_level()
        await self._run_control_command(
            lambda: self.api.set_room_positions(
                self.room.id,
                positions,
                models,
            ),
            position,
        )

    def _raise_unsupported_position(self) -> None:
        """Raise a translated error for rooms without usable group levels."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unsupported_position",
        )


class NormanGroupCover(NormanBaseCover):
    """Cover for one Norman room group or panel level."""

    def __init__(
        self,
        entry: NormanConfigEntry,
        api: NormanGen1Api,
        coordinator: NormanDataUpdateCoordinator,
        room: NormanRoom,
        level: int,
        display_name: str,
    ) -> None:
        """Initialize a group cover."""
        super().__init__(entry, api, coordinator, room)
        self.level = level
        self.group_name = clean_label(display_name)
        self._attr_unique_id = f"{api.hub_id}_room_{room.id}_level_{level}"
        self._attr_name = self.group_name

    @property
    def _option_level(self) -> int:
        return self.level

    @property
    def available(self) -> bool:
        """Return whether the group is present in the latest snapshot."""
        return (
            super().available
            and (self.room.id, self.level) in self.coordinator.data.windows_by_group
        )

    @property
    def _windows(self) -> list[NormanWindow]:
        return self.coordinator.data.windows_by_group.get(
            (self.room.id, self.level), []
        )

    def _hub_positions(self) -> list[int | None]:
        return [window.position for window in self._windows]

    def _model(self) -> int:
        return self._windows[0].model if self._windows else 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose safe command-planning details for troubleshooting."""
        profile = self._position_profile
        return {
            "room_id": self.room.id,
            "level": self.level,
            "command_mode": "level_command",
            "model": self._model(),
            "group_ids": sorted(
                {
                    group_id
                    for window in self._windows
                    if (group_id := window.group_id) is not None
                }
            ),
            "window_ids": sorted(window.id for window in self._windows),
            "open_position": profile.open_position,
            "close_position": profile.close_position,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the group to its visual-open target."""
        profile = self._position_profile
        await self._run_control_command(
            lambda: self.api.set_group_position(
                self.room.id, self.level, profile.open_position, self._model()
            ),
            100,
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the group on its configured movement branch."""
        profile = self._position_profile
        await self._run_control_command(
            lambda: self.api.set_group_position(
                self.room.id, self.level, profile.close_position, self._model()
            ),
            0,
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set a Home Assistant position on the configured hub branch."""
        position = max(0, min(100, int(kwargs[ATTR_POSITION])))
        hub_position = ha_position_to_hub(position, self._position_profile)
        await self._run_control_command(
            lambda: self.api.set_group_position(
                self.room.id, self.level, hub_position, self._model()
            ),
            position,
        )
