"""Room covers composed from ShadeAuto's discovered peripherals."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.norman_gen1.const import DOMAIN

from .const import ATTR_TARGET_POSITION, ATTR_TARGET_TILT
from .coordinator import NormanCoordinator
from .models import NormanPeripheralData


class NormanRoomCover(CoordinatorEntity[NormanCoordinator], CoverEntity):
    """Control a discovered room without assuming an undocumented broadcast API."""

    _attr_device_class = CoverDeviceClass.BLIND
    _attr_has_entity_name = True
    _attr_name = "Shades"
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.SET_TILT_POSITION
    )

    def __init__(
        self,
        coordinator: NormanCoordinator,
        room_id: int,
        room_name: str,
        entry: ConfigEntry,
    ) -> None:
        """Keep room identity separate from individual peripheral identities."""
        super().__init__(coordinator)
        self._room_id = room_id
        self._attr_unique_id = f"{entry.unique_id}_room_{room_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=room_name,
            manufacturer="Norman",
            model="ShadeAuto room",
            suggested_area=room_name,
        )

    @property
    def _members(self) -> list[NormanPeripheralData]:
        return [
            device
            for device in self.coordinator.data.values()
            if type(device.room_id) is int and device.room_id == self._room_id
        ]

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Offer second-rail actions only when every member supports them."""
        members = self._members
        if members and all(device.supports_middle_rail for device in members):
            return self._attr_supported_features
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
        )

    def _direction(self, *, opening: bool) -> bool | None:
        """Aggregate known directions without guessing missing targets."""
        comparisons: list[bool | None] = []
        for member in self._members:
            current = member.bottom_rail_position
            target = self.coordinator.target_position(member.id, "bottom")
            if current is None or target is None:
                comparisons.append(None)
            else:
                comparisons.append(target > current if opening else target < current)
        if any(value is True for value in comparisons):
            return True
        if not comparisons or any(value is None for value in comparisons):
            return None
        return False

    @property
    def is_opening(self) -> bool | None:
        """Report whether any member has a higher bottom-rail target."""
        return self._direction(opening=True)

    @property
    def is_closing(self) -> bool | None:
        """Report whether any member has a lower bottom-rail target."""
        return self._direction(opening=False)

    @property
    def available(self) -> bool:
        """A disconnected hub or empty room cannot be controlled."""
        return self.coordinator.last_update_success and bool(self._members)

    def _position(self, rail: str, *, target: bool = False) -> int | None:
        members = self._members
        positions = [
            self.coordinator.target_position(device.id, rail)
            if target
            else getattr(device, f"{rail}_rail_position")
            for device in members
        ]
        if not positions or any(value is None for value in positions):
            return None
        return round(
            sum(value for value in positions if value is not None) / len(positions)
        )

    @property
    def current_cover_position(self) -> int | None:
        """Show the mean only when every member reports its bottom rail."""
        return self._position("bottom")

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Show the mean only when every member reports its middle rail."""
        return self._position("middle")

    @property
    def is_closed(self) -> bool | None:
        """Require every member to be closed, not merely a rounded mean of zero."""
        positions = [device.bottom_rail_position for device in self._members]
        if not positions or any(value is None for value in positions):
            return None
        return all(value == 0 for value in positions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose membership count and aggregated targets without raw hub data."""
        return {
            "member_count": len(self._members),
            ATTR_TARGET_POSITION: self._position("bottom", target=True),
            ATTR_TARGET_TILT: self._position("middle", target=True),
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open each bottom rail, preserving its own middle rail."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=100, middle=None
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close each bottom rail, preserving its own middle rail."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=0, middle=None
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set every bottom rail to the requested position."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=kwargs[ATTR_POSITION], middle=None
        )

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open each middle rail, preserving its own bottom rail."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=None, middle=100
        )

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close each middle rail, preserving its own bottom rail."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=None, middle=0
        )

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set every middle rail to the requested position."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=None, middle=kwargs[ATTR_TILT_POSITION]
        )

    async def async_nudge_position(self, step: int) -> None:
        """Move each bottom rail relative to its own latest target."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=step, middle=None, nudge=True
        )

    async def async_nudge_tilt(self, step: int) -> None:
        """Move each middle rail relative to its own latest target."""
        await self.coordinator.async_set_room_position(
            self._room_id, bottom=None, middle=step, nudge=True
        )
