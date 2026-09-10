"""Support for Norman window coverings."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from __future__ import annotations

from functools import cached_property
import logging
from typing import Any, cast

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import voluptuous as vol

from custom_components.norman_gen1.const import DOMAIN

from .api import NormanApiError, NormanConnectionError
from .const import (
    ATTR_TARGET_POSITION,
    ATTR_TARGET_TILT,
    COVER_TYPE_SMARTDRAPE,
    SERVICE_NUDGE_POSITION,
    SERVICE_NUDGE_TILT,
)
from .coordinator import NormanCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Norman cover devices."""
    coordinator = cast(NormanCoordinator, entry.runtime_data)

    entities: list[NormanCoverBase] = []
    for device_id, device_data in coordinator.data.items():
        cover_type = device_data.type

        if cover_type == COVER_TYPE_SMARTDRAPE:
            entities.append(NormanBlind(coordinator, device_id, entry))
        else:
            # TODO: Support other kinds of blinds
            raise HomeAssistantError(
                f"Unsupported cover type {cover_type} for device {device_id}"
            )

    # Register a service to nudge the position of the blinds
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_NUDGE_POSITION,
        {vol.Required("step"): vol.All(vol.Coerce(int), vol.Range(min=-100, max=100))},
        "async_nudge_position",
        required_features=[CoverEntityFeature.SET_TILT_POSITION],
    )
    platform.async_register_entity_service(
        SERVICE_NUDGE_TILT,
        {vol.Required("step"): vol.All(vol.Coerce(int), vol.Range(min=-100, max=100))},
        "async_nudge_tilt",
        required_features=[CoverEntityFeature.SET_TILT_POSITION],
    )

    async_add_entities(entities)


class NormanCoverBase(CoordinatorEntity[NormanCoordinator], CoverEntity):
    """Base class for Norman covers."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        coordinator: NormanCoordinator,
        device_id: int,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self._device_id = device_id

        device_data = self.coordinator.data[device_id]

        self._attr_unique_id = f"{entry.unique_id}_{device_id}"
        self._attr_name = device_data.name or f"Norman Cover {device_id}"

        # Create device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self._attr_name,
            manufacturer="Norman",
            model=f"Window Covering {device_data.module_type}",
            suggested_area=device_data.room_name,
            sw_version=device_data.firmware_version,
        )

        self._attr_extra_state_attributes = {}

    @cached_property
    def device_class(self) -> CoverDeviceClass | None:
        """Return the device class."""
        return CoverDeviceClass.BLIND

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        # TODO: handle case where individual devices can go offline
        return self._device_id in self.coordinator.data

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed.

        Returns:
            True if the cover is closed, False otherwise, None if unknown

        """
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    @property
    def current_cover_position(self) -> int | None:
        """Return current position of the cover.

        Norman API returns 0 as closed and 100 as open, which aligns with HA's convention.

        Returns:
            Position from 0 (closed) to 100 (open), None if unknown

        """
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return None

        device_data = self.coordinator.data[self._device_id]
        return device_data.bottom_rail_position

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        # Close bottom rail to 0, preserve middle rail
        await self._async_set_position(bottom=0, middle=None, action="close cover")

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        # Open bottom rail to 100, preserve middle rail
        await self._async_set_position(bottom=100, middle=None, action="open cover")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs[ATTR_POSITION]
        # Move bottom rail (cover) to position, preserve middle rail
        await self._async_set_position(
            bottom=position, middle=None, action="set position", value=position
        )

    async def _async_set_position(
        self,
        bottom: int | None,
        middle: int | None,
        action: str,
        value: int | None = None,
    ) -> None:
        """Set bottom and middle rail positions, preserving the other if None, with error handling."""

        data = self.coordinator.data.get(self._device_id)
        if data is None:
            raise HomeAssistantError("ShadeAuto device is unavailable")
        bottom_val = (
            bottom
            if bottom is not None
            else (
                data.target_bottom_rail_position
                if data.target_bottom_rail_position is not None
                else data.bottom_rail_position
            )
        )
        middle_val = (
            middle
            if middle is not None
            else (
                data.target_middle_rail_position
                if data.target_middle_rail_position is not None
                else data.middle_rail_position
            )
        )
        if bottom_val is None or middle_val is None:
            raise HomeAssistantError(
                "Cannot preserve an unknown rail position; refresh the hub first"
            )

        try:
            await self.coordinator.api.async_set_position(
                self._device_id, bottom_val, middle_val
            )
            data.target_bottom_rail_position = bottom_val
            data.target_middle_rail_position = middle_val
            await self.coordinator.async_request_refresh()
        except (NormanApiError, NormanConnectionError) as err:
            raise HomeAssistantError(
                f"Failed to {action} (value: {value}) for {self._attr_name}: {err}"
            ) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data.get(self._device_id)
        self._attr_extra_state_attributes[ATTR_TARGET_POSITION] = getattr(
            data, "target_bottom_rail_position", None
        )
        return self._attr_extra_state_attributes

    def _nudge_start(self, *, tilt: bool) -> int:
        """Use the latest target or current position, never an invented position."""
        data = self.coordinator.data.get(self._device_id)
        if data is None:
            raise HomeAssistantError("ShadeAuto device is unavailable")
        target = (
            data.target_middle_rail_position
            if tilt
            else data.target_bottom_rail_position
        )
        current = data.middle_rail_position if tilt else data.bottom_rail_position
        value = target if target is not None else current
        if value is None:
            raise HomeAssistantError("ShadeAuto rail position is unknown")
        return value

    async def async_nudge_position(self, step: int) -> None:
        """Nudge the cover position by a specified step."""
        # Positive step = more open, negative = more closed
        new_pos = max(
            0,
            min(
                100,
                self._nudge_start(tilt=False) + step,
            ),
        )
        await self.async_set_cover_position(position=new_pos)


class NormanBlind(NormanCoverBase):
    """Representation of a Norman blind with tilt capabilities."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.SET_TILT_POSITION
    )

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position of the cover.

        Norman API uses middle_rail_position for tilt (0-100).

        """
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return None

        device_data = self.coordinator.data[self._device_id]
        return device_data.middle_rail_position

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the cover tilt."""
        await self._async_set_position(bottom=None, middle=100, action="open tilt")

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the cover tilt."""
        await self._async_set_position(bottom=None, middle=0, action="close tilt")

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Move the cover tilt to a specific position."""
        tilt_position = kwargs[ATTR_TILT_POSITION]
        await self._async_set_position(
            bottom=None,
            middle=tilt_position,
            action="set tilt position",
            value=tilt_position,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        _ = super().extra_state_attributes
        data = self.coordinator.data.get(self._device_id)
        self._attr_extra_state_attributes[ATTR_TARGET_TILT] = getattr(
            data, "target_middle_rail_position", None
        )
        return self._attr_extra_state_attributes

    async def async_nudge_tilt(self, step: int) -> None:
        """Nudge the cover tilt by a specified step."""
        new_tilt = max(
            0,
            min(
                100,
                self._nudge_start(tilt=True) + step,
            ),
        )
        await self.async_set_cover_tilt_position(tilt_position=new_tilt)
