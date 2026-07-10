"""Data coordinator for the Norman Gen 1 integration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CannotConnect,
    InvalidAuth,
    InvalidSession,
    NoDevicesFound,
    NormanGen1Api,
    NormanRoom,
    NormanWindow,
    UnexpectedHub,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormanData:
    """Normalized room, group, and shutter state from one hub refresh."""

    rooms: list[NormanRoom]
    windows: list[NormanWindow]
    rooms_by_id: dict[int, NormanRoom]
    windows_by_id: dict[int, NormanWindow]
    windows_by_room: dict[int, list[NormanWindow]]
    windows_by_group: dict[tuple[int, int], list[NormanWindow]]
    levels_by_room: dict[int, list[int]]


class NormanDataUpdateCoordinator(DataUpdateCoordinator[NormanData]):
    """Coordinate room, group, and shutter state from a Norman Gen 1 hub."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: NormanGen1Api,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
            always_update=False,
        )
        self.api = api
        self._known_rooms_by_id: dict[int, NormanRoom] = {}

    async def _async_update_data(self) -> NormanData:
        """Fetch fresh hub data and translate failures for Home Assistant."""
        for attempt in range(2):
            try:
                return await self._fetch_data()
            except InvalidSession as err:
                if attempt == 0:
                    continue
                raise UpdateFailed(
                    "Hub repeatedly rejected the authenticated session"
                ) from err
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="invalid_auth",
                ) from err
            except UnexpectedHub as err:
                raise ConfigEntryError(
                    translation_domain=DOMAIN,
                    translation_key="wrong_hub",
                ) from err
            except (CannotConnect, NoDevicesFound) as err:
                raise UpdateFailed(str(err)) from err
        raise AssertionError("Norman refresh retry loop did not return or raise")

    async def _fetch_data(self) -> NormanData:
        """Fetch and normalize one complete hub snapshot."""
        async with self.api.authenticated_session():
            rooms = await self.api.get_rooms()
            windows = await self.api.get_windows()

        rooms_by_id = {room.id: room for room in rooms}
        self._known_rooms_by_id.update(rooms_by_id)
        windows_by_room: dict[int, list[NormanWindow]] = defaultdict(list)
        windows_by_group: dict[tuple[int, int], list[NormanWindow]] = defaultdict(list)
        levels_by_room: dict[int, set[int]] = defaultdict(set)
        valid_windows: list[NormanWindow] = []

        for window in windows:
            if window.room_id < 0:
                _LOGGER.warning("Ignoring Norman window without a valid room id")
                continue
            valid_windows.append(window)
            windows_by_room[window.room_id].append(window)
            if window.level >= 0:
                windows_by_group[(window.room_id, window.level)].append(window)
                levels_by_room[window.room_id].add(window.level)

        for room_id in sorted(windows_by_room):
            if room_id in rooms_by_id:
                continue
            room = self._known_rooms_by_id.get(room_id)
            if room is None:
                room = NormanRoom(
                    id=room_id,
                    name=f"Room {room_id}",
                    group_names=[],
                    raw={"generated_from_window_scan": True},
                )
            rooms_by_id[room_id] = room

        if not rooms_by_id and not valid_windows:
            raise NoDevicesFound(
                "Hub responded, but no Norman rooms or shutter devices were discovered"
            )

        self._known_rooms_by_id.update(rooms_by_id)
        return NormanData(
            rooms=list(rooms_by_id.values()),
            windows=valid_windows,
            rooms_by_id=rooms_by_id,
            windows_by_id={window.id: window for window in valid_windows},
            windows_by_room=dict(windows_by_room),
            windows_by_group=dict(windows_by_group),
            levels_by_room={
                room_id: sorted(levels) for room_id, levels in levels_by_room.items()
            },
        )


type NormanConfigEntry = ConfigEntry[NormanDataUpdateCoordinator]
