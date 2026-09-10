"""Data models for Norman integration."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NormanPeripheralData:
    """Local model for storing Norman peripheral data."""

    id: int
    name: str
    type: str
    room_id: int | None = None
    room_name: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    module_type: int | None = None
    module_detail: int | None = None
    bottom_rail_position: int | None = None
    middle_rail_position: int | None = None
    target_bottom_rail_position: int | None = None
    target_middle_rail_position: int | None = None
    battery_level: float | None = None
    firmware_version: str | None = None
    last_update: str | None = None


# Represents all peripherals keyed by their ID
NormanDevices = dict[int, NormanPeripheralData]


def validate_position(value: Any) -> int | None:
    """Accept only known protocol percentages; missing rails remain unknown."""
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError("ShadeAuto rail position must be an integer from 0 to 100")
    return value
