"""Canonical numeric movement profiles for Norman shutters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from .api import (
    NormanRoom,
    PositionProfile,
    group_target_id,
    resolve_position_profile,
    room_target_id,
    target_override_enabled,
)
from .const import (
    CONF_CLOSE_POSITION,
    CONF_DEFAULT_CLOSE_POSITION,
    CONF_DEFAULT_OPEN_POSITION,
    CONF_KNOWN_TARGETS,
    CONF_OPEN_POSITION,
    CONF_POSITION_PROFILES,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
    DEFAULT_PROFILE_CLOSE_POSITION,
    DEFAULT_PROFILE_OPEN_POSITION,
)

LEGACY_OPTION_KEYS = (
    CONF_TILT_OPEN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_KNOWN_TARGETS,
)
PROFILE_OPTION_KEYS = (
    CONF_DEFAULT_OPEN_POSITION,
    CONF_DEFAULT_CLOSE_POSITION,
    CONF_POSITION_PROFILES,
)


def make_position_profile(
    open_position: object,
    close_position: object,
) -> PositionProfile:
    """Validate raw endpoints and build a movement profile."""
    if not _is_position(open_position):
        raise ValueError("Open position must be an integer from 0 to 100")
    if not _is_close_position(close_position):
        raise ValueError("Closed position must be either 0 or 100")
    if open_position == close_position:
        raise ValueError("Open and closed positions must be different")
    validated_open = cast(int, open_position)
    validated_close = cast(int, close_position)
    return PositionProfile(
        open_position=validated_open,
        close_position=validated_close,
        closes_at_both_ends=0 < validated_open < 100,
    )


def resolve_configured_profile(
    options: Mapping[str, Any],
    room_id: int,
    level: int | None = None,
) -> PositionProfile:
    """Resolve panel, room, then global numeric profile options."""
    profiles = options.get(CONF_POSITION_PROFILES)
    if isinstance(profiles, Mapping):
        targets = (
            (group_target_id(room_id, level), room_target_id(room_id))
            if level is not None
            else (room_target_id(room_id),)
        )
        for target in targets:
            if (profile := _profile_from_mapping(profiles.get(target))) is not None:
                return profile
    return _default_profile(options)


def resolve_default_profile(options: Mapping[str, Any]) -> PositionProfile:
    """Return the validated global profile from config-entry options."""
    return _default_profile(options)


def stored_position_profiles(
    options: Mapping[str, Any],
) -> dict[str, dict[str, int]]:
    """Return only valid sparse target overrides from stored options."""
    result: dict[str, dict[str, int]] = {}
    profiles = options.get(CONF_POSITION_PROFILES)
    if not isinstance(profiles, Mapping):
        return result
    for target, raw_profile in profiles.items():
        if not isinstance(target, str):
            continue
        if (profile := _profile_from_mapping(raw_profile)) is not None:
            result[target] = profile_as_options(profile)
    return result


def configured_group_levels(options: Mapping[str, Any], room_id: int) -> set[int]:
    """Return valid group levels retained as sparse overrides for one room."""
    levels: set[int] = set()
    for target in stored_position_profiles(options):
        coordinates = _target_coordinates(target)
        if coordinates is None:
            continue
        target_room_id, level = coordinates
        if target_room_id == room_id and level is not None and level >= 0:
            levels.add(level)
    return levels


def profile_as_options(profile: PositionProfile) -> dict[str, int]:
    """Serialize one validated profile for config-entry options."""
    return {
        CONF_OPEN_POSITION: profile.open_position,
        CONF_CLOSE_POSITION: profile.close_position,
    }


def migrate_legacy_profile_options(
    options: Mapping[str, Any],
    rooms: Iterable[NormanRoom],
    levels_by_room: Mapping[int, Iterable[int]],
) -> dict[str, Any]:
    """Convert v0.2 boolean choices to equivalent sparse numeric profiles."""
    room_list = list(rooms)
    had_numeric_options = any(key in options for key in PROFILE_OPTION_KEYS)
    migrated = {
        key: value for key, value in options.items() if key not in LEGACY_OPTION_KEYS
    }

    if had_numeric_options:
        default_profile = _default_profile(options)
        migrated[CONF_DEFAULT_OPEN_POSITION] = default_profile.open_position
        migrated[CONF_DEFAULT_CLOSE_POSITION] = default_profile.close_position
        migrated[CONF_POSITION_PROFILES] = stored_position_profiles(options)
        return migrated

    default_profile = make_position_profile(
        DEFAULT_PROFILE_OPEN_POSITION,
        DEFAULT_PROFILE_CLOSE_POSITION,
    )
    profiles: dict[str, dict[str, int]] = {}
    current_targets: set[str] = set()
    rooms_by_id = {room.id: room for room in room_list}
    for room in room_list:
        current_targets.add(room_target_id(room.id))
        room_profile = _legacy_profile(options, room, None)
        if room_profile != default_profile:
            profiles[room_target_id(room.id)] = profile_as_options(room_profile)

        for level in sorted(set(levels_by_room.get(room.id, []))):
            current_targets.add(group_target_id(room.id, level))
            group_profile = _legacy_profile(options, room, level)
            if group_profile != room_profile:
                profiles[group_target_id(room.id, level)] = profile_as_options(
                    group_profile
                )

    legacy_targets = _legacy_target_ids(options)
    for target in sorted(legacy_targets - current_targets):
        coordinates = _target_coordinates(target)
        if coordinates is None:
            continue
        missing_room_id, missing_level = coordinates
        room = rooms_by_id.get(
            missing_room_id,
            NormanRoom(
                id=missing_room_id,
                name=str(missing_room_id),
                group_names=[],
                raw={},
            ),
        )
        profile = _legacy_profile(options, room, missing_level)
        parent_target = room_target_id(missing_room_id)
        inherited_profile = default_profile
        if (
            missing_level is not None
            and parent_target in current_targets | legacy_targets
        ):
            inherited_profile = _legacy_profile(options, room, None)
        if profile == inherited_profile:
            continue
        profiles[target] = profile_as_options(profile)

    migrated[CONF_DEFAULT_OPEN_POSITION] = default_profile.open_position
    migrated[CONF_DEFAULT_CLOSE_POSITION] = default_profile.close_position
    migrated[CONF_POSITION_PROFILES] = profiles
    return migrated


def _legacy_profile(
    options: Mapping[str, Any],
    room: NormanRoom,
    level: int | None,
) -> PositionProfile:
    return resolve_position_profile(
        room.raw,
        use_tilt_open=_legacy_override(
            options,
            CONF_TILT_OPEN_TARGETS,
            room.id,
            level,
        ),
        use_reversed_close=_legacy_override(
            options,
            CONF_REVERSED_CLOSE_TARGETS,
            room.id,
            level,
        ),
    )


def _legacy_override(
    options: Mapping[str, Any],
    option_name: str,
    room_id: int,
    level: int | None,
) -> bool | None:
    if option_name not in options:
        return None
    raw_targets = options.get(option_name)
    targets = raw_targets if isinstance(raw_targets, list) else []
    if target_override_enabled(targets, room_id, level):
        return True

    raw_known_targets = options.get(CONF_KNOWN_TARGETS)
    if raw_known_targets is None:
        return False
    known_targets = raw_known_targets if isinstance(raw_known_targets, list) else []
    target = (
        room_target_id(room_id) if level is None else group_target_id(room_id, level)
    )
    return (
        False
        if target in known_targets or room_target_id(room_id) in known_targets
        else None
    )


def _legacy_target_ids(options: Mapping[str, Any]) -> set[str]:
    targets: set[str] = set()
    for key in LEGACY_OPTION_KEYS:
        values = options.get(key)
        if isinstance(values, list):
            targets.update(value for value in values if isinstance(value, str))
    return targets


def _target_coordinates(target: str) -> tuple[int, int | None] | None:
    parts = target.split(":")
    try:
        if len(parts) == 2 and parts[0] == "room":
            return int(parts[1]), None
        if len(parts) == 3 and parts[0] == "group":
            return int(parts[1]), int(parts[2])
    except ValueError:
        return None
    return None


def _default_profile(options: Mapping[str, Any]) -> PositionProfile:
    try:
        return make_position_profile(
            options.get(
                CONF_DEFAULT_OPEN_POSITION,
                DEFAULT_PROFILE_OPEN_POSITION,
            ),
            options.get(
                CONF_DEFAULT_CLOSE_POSITION,
                DEFAULT_PROFILE_CLOSE_POSITION,
            ),
        )
    except ValueError:
        return make_position_profile(
            DEFAULT_PROFILE_OPEN_POSITION,
            DEFAULT_PROFILE_CLOSE_POSITION,
        )


def _profile_from_mapping(value: object) -> PositionProfile | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return make_position_profile(
            value.get(CONF_OPEN_POSITION),
            value.get(CONF_CLOSE_POSITION),
        )
    except ValueError:
        return None


def _is_position(value: object) -> bool:
    return type(value) is int and 0 <= value <= 100


def _is_close_position(value: object) -> bool:
    return type(value) is int and value in (0, 100)
