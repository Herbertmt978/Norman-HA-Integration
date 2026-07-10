"""Diagnostics support for the Norman Gen 1 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_KNOWN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
)
from .coordinator import NormanConfigEntry

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
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": {
            "tilt_open_target_count": _target_count(
                entry.options.get(CONF_TILT_OPEN_TARGETS)
            ),
            "reversed_close_target_count": _target_count(
                entry.options.get(CONF_REVERSED_CLOSE_TARGETS)
            ),
            "known_target_count": _target_count(entry.options.get(CONF_KNOWN_TARGETS)),
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
                    "style": value
                    if isinstance(value := room.raw.get("Style"), SAFE_VALUE_TYPES)
                    else None,
                    "group_name_count": len(room.group_names),
                    "level_count": len(data.levels_by_room.get(room.id, [])),
                }
                for room in data.rooms
            ],
            "windows": [
                {
                    "level": window.level,
                    "position": window.position,
                    "model": window.model,
                    "battery": window.battery,
                }
                for window in data.windows
            ],
        },
    }


def _target_count(value: Any) -> int:
    """Return a count without exposing internal room or group target IDs."""
    return len(value) if isinstance(value, list) else 0
