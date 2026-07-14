"""Diagnostics support for the Norman Gen 1 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import NormanConfigEntry
from .cover import resolve_room_command_routing
from .profiles import resolve_default_profile, stored_position_profiles

TO_REDACT = {"host", "password"}
SAFE_HUB_FIELDS = ("swVer", "firmwareVersion", "version", "status", "errorCode")
SAFE_VALUE_TYPES = (str, int, float, bool)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
) -> dict[str, Any]:
    """Return privacy-safe diagnostics for a Norman Gen 1 hub."""
    coordinator = entry.runtime_data
    api = coordinator.api
    data = coordinator.data
    default_profile = resolve_default_profile(entry.options)
    routings = {
        room.id: resolve_room_command_routing(
            entry.options,
            room,
            data.windows_by_room.get(room.id, []),
            data.levels_by_room.get(room.id, []),
        )
        for room in data.rooms
    }
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": {
            "default_open_position": default_profile.open_position,
            "default_close_position": default_profile.close_position,
            "profile_override_count": len(stored_position_profiles(entry.options)),
            "simultaneous_room_ids": sorted(
                room_id
                for room_id, routing in routings.items()
                if routing.simultaneous_selected
            ),
        },
        "hub": {
            key: value
            for key in SAFE_HUB_FIELDS
            if isinstance(value := api.hub_info.get(key), SAFE_VALUE_TYPES)
        },
        "snapshot": {
            "room_count": len(data.rooms),
            "window_count": len(data.windows),
            "group_count": sum(len(levels) for levels in data.levels_by_room.values()),
            "rooms": [
                {
                    "room_id": room.id,
                    "style": value
                    if isinstance(value := room.raw.get("Style"), SAFE_VALUE_TYPES)
                    else None,
                    "group_name_count": len(room.group_names),
                    "level_count": len(data.levels_by_room.get(room.id, [])),
                    "levels": data.levels_by_room.get(room.id, []),
                    "command_levels": list(routings[room.id].command_levels),
                    "open_command": routings[room.id].open_mode,
                    "close_command": routings[room.id].close_mode,
                    "position_command": routings[room.id].position_mode,
                }
                for room in data.rooms
            ],
            "windows": [
                {
                    "window_id": window.id,
                    "room_id": window.room_id,
                    "level": window.level,
                    "group_id": window.group_id,
                    "position": window.position,
                    "model": window.model,
                    "battery": window.battery,
                }
                for window in data.windows
            ],
        },
    }
