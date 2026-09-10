"""Data models for Norman integration."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from __future__ import annotations

from dataclasses import dataclass


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
