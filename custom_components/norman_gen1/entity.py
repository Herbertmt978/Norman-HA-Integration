"""Shared entities for the Norman Gen 1 integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import logging

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    CannotConnect,
    CannotControl,
    InvalidAuth,
    InvalidSession,
    NormanGen1Api,
    NormanRoom,
    PositionProfile,
    UnexpectedHub,
    group_target_id,
    hub_position_to_ha,
    position_is_closed,
    resolve_position_profile,
    room_target_id,
    target_override_enabled,
)
from .const import (
    COMMAND_SETTLE_SECONDS,
    CONF_KNOWN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import NormanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class NormanBaseCover(CoordinatorEntity[NormanDataUpdateCoordinator], CoverEntity):
    """Base class for coordinator-backed Norman covers."""

    _attr_assumed_state = True
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_has_entity_name = True
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        entry: ConfigEntry,
        api: NormanGen1Api,
        coordinator: NormanDataUpdateCoordinator,
        room: NormanRoom,
    ) -> None:
        """Initialize a Norman cover."""
        super().__init__(coordinator)
        self.entry = entry
        self.api = api
        self.room = room
        self._optimistic_position: int | None = None
        self._refresh_generation = 0
        self._refresh_task: asyncio.Task[None] | None = None
        hub_name = self.api.hub_info.get("hubName")
        if not isinstance(hub_name, str) or not hub_name.strip():
            hub_name = "Norman Gen 1 Hub"
        sw_version = self.api.hub_info.get("swVer")
        if not isinstance(sw_version, str):
            sw_version = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.api.hub_id)},
            name=f"Norman Hub {hub_name}",
            manufacturer=MANUFACTURER,
            model="Gen 1 Hub",
            sw_version=sw_version,
            configuration_url=f"http://{self.api.host}/",
        )

    @property
    def available(self) -> bool:
        """Return whether the room is present in the latest snapshot."""
        return super().available and self.room.id in self.coordinator.data.rooms_by_id

    @property
    def _current_room(self) -> NormanRoom:
        return self.coordinator.data.rooms_by_id.get(self.room.id, self.room)

    @property
    def _option_level(self) -> int | None:
        return None

    @property
    def _position_profile(self) -> PositionProfile:
        return resolve_position_profile(
            self._current_room.raw,
            use_tilt_open=self._target_option_enabled(CONF_TILT_OPEN_TARGETS),
            use_reversed_close=self._target_option_enabled(CONF_REVERSED_CLOSE_TARGETS),
        )

    @property
    def current_cover_position(self) -> int | None:
        """Return a Home Assistant position normalized from raw hub positions."""
        if self._optimistic_position is not None:
            return self._optimistic_position
        hub_positions = self._hub_positions()
        if not hub_positions or any(position is None for position in hub_positions):
            return None
        positions = [
            hub_position_to_ha(position, self._position_profile)
            for position in hub_positions
            if position is not None
        ]
        if any(position is None for position in positions):
            return None
        return round(
            sum(position for position in positions if position is not None)
            / len(positions)
        )

    @property
    def is_closed(self) -> bool | None:
        """Return whether all known shutters are physically closed."""
        if self._optimistic_position is not None:
            return self._optimistic_position <= 0
        positions = self._hub_positions()
        if not positions or any(position is None for position in positions):
            return None
        profile = self._position_profile
        return all(
            position_is_closed(
                position,
                profile.close_position,
                closes_at_both_ends=profile.closes_at_both_ends,
            )
            for position in positions
            if position is not None
        )

    async def _refresh_after_command(self, optimistic_position: int) -> None:
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._optimistic_position = optimistic_position
        self.async_write_ha_state()
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = self.hass.async_create_task(
            self._delayed_refresh(generation)
        )

    async def _run_control_command(
        self, command: Callable[[], Awaitable[None]], optimistic_position: int
    ) -> None:
        try:
            for attempt in range(2):
                try:
                    async with self.api.authenticated_session():
                        await command()
                    break
                except InvalidSession:
                    if attempt == 1:
                        raise
        except CannotConnect as err:
            message = f"Unable to reach Norman Gen 1 hub at {self.api.host}"
            _LOGGER.warning("%s while controlling %s: %s", message, self.entity_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
        except InvalidAuth as err:
            message = (
                "Norman Gen 1 hub rejected the control request; check the hub password"
            )
            _LOGGER.warning("%s while controlling %s: %s", message, self.entity_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except InvalidSession as err:
            message = "Norman Gen 1 hub rejected the control session after retrying"
            _LOGGER.warning("%s for %s: %s", message, self.entity_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="session_rejected",
            ) from err
        except UnexpectedHub as err:
            _LOGGER.error(
                "Refusing to control %s because the configured hub identity changed",
                self.entity_id,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wrong_hub",
            ) from err
        except CannotControl as err:
            message = "Norman Gen 1 hub did not confirm the shutter command"
            _LOGGER.warning("%s for %s: %s", message, self.entity_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_not_confirmed",
            ) from err
        await self._refresh_after_command(optimistic_position)

    async def _delayed_refresh(self, generation: int) -> None:
        try:
            await asyncio.sleep(COMMAND_SETTLE_SECONDS)
            if generation != self._refresh_generation:
                return
            await self.coordinator.async_request_refresh()
        finally:
            if generation == self._refresh_generation:
                self._optimistic_position = None
                self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a delayed refresh when the entity is removed."""
        self._refresh_generation += 1
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresh_task
        self._refresh_task = None
        await super().async_will_remove_from_hass()

    def _hub_positions(self) -> list[int | None]:
        raise NotImplementedError

    def _target_option_enabled(self, option_name: str) -> bool | None:
        if option_name not in self.entry.options:
            return None
        if target_override_enabled(
            self.entry.options.get(option_name, []),
            self.room.id,
            self._option_level,
        ):
            return True
        known_targets = self.entry.options.get(CONF_KNOWN_TARGETS)
        if known_targets is None:
            return False
        target_id = (
            room_target_id(self.room.id)
            if self._option_level is None
            else group_target_id(self.room.id, self._option_level)
        )
        room_id = room_target_id(self.room.id)
        return False if target_id in known_targets or room_id in known_targets else None
