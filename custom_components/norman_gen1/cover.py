"""Cover platform for the Norman Gen 1 integration."""

from __future__ import annotations

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
)
from .const import DOMAIN
from .coordinator import NormanConfigEntry, NormanDataUpdateCoordinator
from .entity import NormanBaseCover
from .helpers import clean_label, group_name
from .profiles import configured_group_levels, resolve_configured_profile

PARALLEL_UPDATES = 1


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
        can_fan_out = self._can_fan_out()
        if can_fan_out or self._can_broadcast("open_position"):
            features |= CoverEntityFeature.OPEN
        if can_fan_out or self._can_broadcast("close_position"):
            features |= CoverEntityFeature.CLOSE
        if can_fan_out:
            features |= CoverEntityFeature.SET_POSITION
        return features

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
        return self.coordinator.data.levels_by_room.get(self.room.id, [])

    def _command_levels(self) -> list[int]:
        return sorted(
            set(self._levels())
            | configured_group_levels(self.entry.options, self.room.id)
        )

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

    def _can_broadcast(self, attribute: str) -> bool:
        native_target = int(
            getattr(resolve_position_profile(self._current_room.raw), attribute)
        )
        windows = self.coordinator.data.windows_by_room.get(self.room.id, [])
        profiles = list(self._profiles_by_level().values())
        profiles.extend(
            resolve_configured_profile(
                self.entry.options,
                self.room.id,
            )
            for window in windows
            if window.level < 0
        )
        if profiles:
            return all(
                int(getattr(profile, attribute)) == native_target
                for profile in profiles
            )
        return int(getattr(self._position_profile, attribute)) == native_target

    def _can_fan_out(self) -> bool:
        windows = self.coordinator.data.windows_by_room.get(self.room.id, [])
        return bool(self._command_levels()) and all(
            window.level >= 0 for window in windows
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the room to its visual-open target."""
        if self._can_broadcast("open_position"):
            await self._run_control_command(
                lambda: self.api.full_open_room(self.room.id),
                100,
            )
            return
        if not self._can_fan_out():
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
        if self._can_broadcast("close_position"):
            await self._run_control_command(
                lambda: self.api.full_close_room(self.room.id),
                0,
            )
            return
        if not self._can_fan_out():
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
