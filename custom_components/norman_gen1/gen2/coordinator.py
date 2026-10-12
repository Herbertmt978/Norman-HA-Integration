"""Data update coordinator for Norman devices."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import re
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.norman_gen1.const import DOMAIN

from .api import (
    NormanApiClient,
    NormanApiError,
    NormanConnectionError,
    NormanPeriodicReconnectError,
)
from .const import COVER_TYPE_SMARTDRAPE, RECONNECT_INTERVAL
from .models import NormanDevices, NormanPeripheralData, validate_position

_LOGGER = logging.getLogger(__name__)
_MISSING_ROOM_ID = object()
_ROOM_ID_STRING = re.compile(r"[+-]?[0-9]+", re.ASCII)


def _room_id_type(value: Any) -> str:
    """Return a safe, stable label for the original protocol value type."""
    if value is _MISSING_ROOM_ID:
        return "missing"
    if value is None:
        return "NoneType"
    if type(value) is int:
        return "int"
    if type(value) is str:
        return "str"
    if type(value) is bool:
        return "bool"
    if type(value) is float:
        return "float"
    if type(value) is list:
        return "list"
    if type(value) is dict:
        return "dict"
    return "other"


def _normalise_room_id(value: Any) -> tuple[int | None, str | None]:
    """Accept integer IDs and decimal integer strings, rejecting coercions."""
    if type(value) is int:
        return value, None
    if type(value) is str:
        if not _ROOM_ID_STRING.fullmatch(value):
            return None, "malformed"
        try:
            return int(value), None
        except ValueError:
            return None, "malformed"
    if value is _MISSING_ROOM_ID:
        return None, "missing"
    if value is None:
        return None, "null"
    if type(value) is bool:
        return None, "boolean"
    if type(value) is float:
        return None, "float"
    return None, "unsupported_type"


# Bound optimistic targets if the hub never acknowledges an accepted command.
PENDING_TARGET_TIMEOUT = 30
COMMAND_REFRESH_DELAY = 2
NOTIFICATION_REFRESH_MAX_WAIT = 5
MOTION_REPORT_TIMEOUT = 120
COMMAND_FOLLOWUP_REFRESH_DELAY = 35
COMMAND_QUIET_PERIOD = 2
IDLE_UPDATE_INTERVAL = timedelta(seconds=60)


def _resolve_rail_position(
    current: int | None,
    target: int | None,
    requested: int | None,
    *,
    nudge: bool,
) -> int:
    """Resolve one rail without changing state or sending a command."""
    position = target if target is not None else current
    if requested is not None:
        if nudge:
            if position is None:
                raise HomeAssistantError("ShadeAuto rail position is unknown")
            position = max(0, min(100, position + requested))
        else:
            position = requested
    if position is None:
        raise HomeAssistantError(
            "Cannot preserve an unknown rail position; refresh the hub first"
        )
    try:
        validate_position(position)
    except ValueError as err:
        raise HomeAssistantError(str(err)) from err
    return position


class NormanCoordinator(DataUpdateCoordinator[NormanDevices]):
    """Norman data update coordinator."""

    def __init__(
        self, hass: HomeAssistant, api: NormanApiClient, entry: ConfigEntry
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=IDLE_UPDATE_INTERVAL,
            always_update=False,
            # The trailing timers own debounce; an additional cooldown delays reads.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=0, immediate=True
            ),
        )
        self.api = api
        self._device_info: dict[str, Any] = {}
        self.room_diagnostics: dict[str, Any] = {
            "raw_room_count": 0,
            "unique_room_id_count": 0,
            "usable_room_count": 0,
            "rejected_room_count": 0,
            "room_id_type_counts": {},
            "rejection_reason_counts": {},
        }
        self._room_identity_warning_signature: tuple[Any, ...] | None = None
        self._command_lock = asyncio.Lock()
        self._command_calls_pending = 0
        self._command_calls_idle = asyncio.Event()
        self._command_calls_idle.set()
        self._last_command_end: float | None = None
        self._near_refresh_task: asyncio.Task[None] | None = None
        self._followup_refresh_task: asyncio.Task[None] | None = None
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        self._near_refresh_started: float | None = None
        self._motion_expiry_task: asyncio.Task[None] | None = None
        self._motion_deadlines: dict[int, float] = {}
        self._shutting_down = False
        self._middle_rail_devices: set[int] = set()
        self._pending_targets: dict[tuple[int, str], tuple[int, float]] = {}

    async def listen_notifications(self) -> None:
        """Continuously listen for hub notifications and refresh data on change."""
        while True:
            try:
                async for _notification in self.api.async_listen_notifications():
                    _LOGGER.debug("Received ShadeAuto state notification")
                    self._schedule_near_refresh()
            except asyncio.CancelledError:
                _LOGGER.debug("Notification listener task cancelled")
                return
            except (NormanConnectionError, NormanApiError) as err:
                _LOGGER.error("Notification listener disconnected: %s", err)
            except NormanPeriodicReconnectError:
                _LOGGER.debug("Periodic reconnection time arrived")
                continue

            _LOGGER.info(
                "Reconnecting notification listener in %s seconds",
                RECONNECT_INTERVAL,
            )
            try:
                await asyncio.sleep(RECONNECT_INTERVAL)
                # Refresh device states when reconnecting to ensure we have the latest state
                # This handles cases where blind states changed while the hub was offline
                _LOGGER.debug(
                    "Refreshing device states after notification reconnection"
                )
                self._schedule_near_refresh()
            except asyncio.CancelledError:
                _LOGGER.debug("Notification listener sleep cancelled")
                return

    async def _async_update_data(self) -> NormanDevices:
        """Fetch data from API.

        Returns:
            Dictionary with peripheral data keyed by device ID

        Raises:
            UpdateFailed: If the update operation fails

        """
        try:
            while True:
                await self._wait_for_command_quiet()
                async with self._command_lock:
                    if not self._commands_are_quiet():
                        continue
                    if not self._device_info:
                        self._device_info = await self.api.async_get_devices()
                    status_data = await self.api.async_get_status()
                    devices = self._process_data(self._device_info, status_data)
                    pending_changed = self._record_motion(devices)
                    for key, (target, expires) in list(self._pending_targets.items()):
                        device_id, rail = key
                        data = devices.get(device_id)
                        if (
                            monotonic() >= expires
                            or data is None
                            or getattr(data, f"{rail}_rail_position") == target
                            or getattr(data, f"target_{rail}_rail_position") == target
                        ):
                            del self._pending_targets[key]
                            pending_changed = True
                    self._schedule_motion_expiry()
                    if (
                        pending_changed
                        and devices == self.data
                        and self.last_update_success
                    ):
                        # Equal hub data can still expire a locally accepted target.
                        self.async_update_listeners()
                    return devices
        except NormanConnectionError as err:
            raise UpdateFailed(f"Error communicating with Norman hub: {err}") from err
        except (NormanApiError, TypeError, ValueError, AttributeError) as err:
            raise UpdateFailed(f"Invalid response from Norman hub: {err}") from err

    def target_position(self, device_id: int, rail: str) -> int | None:
        """Return an unacknowledged command target or the hub's reported target."""
        pending = self._pending_targets.get((device_id, rail))
        if pending is not None and monotonic() < pending[1]:
            return pending[0]
        return getattr(self.data.get(device_id), f"target_{rail}_rail_position", None)

    def motion_direction(self, device_id: int) -> int | None:
        """Return a bounded bottom-rail direction without changing rail targets."""
        data = self.data.get(device_id)
        if data is None or data.bottom_rail_position is None:
            return None
        now = monotonic()
        pending = self._pending_targets.get((device_id, "bottom"))
        local_target_live = pending is not None and now < pending[1]
        target = (
            pending[0]
            if pending is not None and local_target_live
            else data.target_bottom_rail_position
        )
        if target is None:
            return None
        current = data.bottom_rail_position
        if target == current:
            return 0
        if not local_target_live and now >= self._motion_deadlines.get(device_id, 0):
            return None
        return 1 if target > current else -1

    def _record_motion(self, devices: NormanDevices) -> bool:
        """Start a bounded reporting window only after an observed position change."""
        now = monotonic()
        expired = False
        for device_id, deadline in list(self._motion_deadlines.items()):
            if device_id not in devices or now >= deadline:
                del self._motion_deadlines[device_id]
                expired = True
        previous = self.data or {}
        for device_id, data in devices.items():
            old = previous.get(device_id)
            if (
                old is not None
                and old.bottom_rail_position is not None
                and data.bottom_rail_position is not None
                and old.bottom_rail_position != data.bottom_rail_position
            ):
                self._motion_deadlines[device_id] = now + MOTION_REPORT_TIMEOUT
        return expired

    def _schedule_motion_expiry(self) -> None:
        """Publish expiry even if the hub data is unchanged or reads fail."""
        self._cancel_refresh_task("_motion_expiry_task")
        if self._shutting_down:
            return
        deadlines = list(self._motion_deadlines.values())
        deadlines.extend(expires for _, expires in self._pending_targets.values())
        if deadlines:
            self._motion_expiry_task = self.hass.async_create_background_task(
                self._expire_motion_after(max(0, min(deadlines) - monotonic())),
                "ShadeAuto motion expiry",
            )

    async def _expire_motion_after(self, delay: float) -> None:
        """Expire local motion evidence and update existing HA entity states."""
        try:
            await asyncio.sleep(delay)
            self._motion_expiry_task = None
            if self._shutting_down:
                return
            now = monotonic()
            self._pending_targets = {
                key: value
                for key, value in self._pending_targets.items()
                if now < value[1]
            }
            self._motion_deadlines = {
                key: value
                for key, value in self._motion_deadlines.items()
                if now < value
            }
            self.async_update_listeners()
            self._schedule_motion_expiry()
        except asyncio.CancelledError:
            return

    async def async_set_position(
        self,
        device_id: int,
        *,
        bottom: int | None,
        middle: int | None,
        nudge: bool = False,
    ) -> None:
        """Resolve both rails atomically and retain accepted targets until acknowledged."""
        self._begin_command_call()
        attempted = False
        try:
            async with self._command_lock:
                data = self.data.get(device_id)
                if not self.last_update_success or data is None:
                    raise HomeAssistantError("ShadeAuto device is unavailable")
                positions = self._resolve_positions(data, bottom, middle, nudge=nudge)
                attempted = True
                await self.api.async_set_position(device_id, *positions)
                self._retain_targets(device_id, bottom, middle, positions)
                self.async_update_listeners()
        finally:
            self._end_command_call()
            if attempted:
                self._schedule_command_refreshes()

    async def async_set_room_position(
        self,
        room_id: int,
        *,
        bottom: int | None,
        middle: int | None,
        nudge: bool = False,
    ) -> None:
        """Preflight all room members, then send serial peripheral commands."""
        self._begin_command_call()
        attempted = False
        try:
            async with self._command_lock:
                if not self.last_update_success:
                    raise HomeAssistantError("ShadeAuto room is unavailable")
                if type(room_id) is not int:
                    raise HomeAssistantError("ShadeAuto room ID must be an integer")
                members = {
                    device_id: data
                    for device_id, data in self.data.items()
                    if type(data.room_id) is int and data.room_id == room_id
                }
                if not members:
                    raise HomeAssistantError("ShadeAuto room has no available members")
                # Resolve every child before moving any of them. Preserved rails and
                # relative nudges use each child's own current or pending target.
                positions_by_device = {
                    device_id: self._resolve_positions(
                        data, bottom, middle, nudge=nudge
                    )
                    for device_id, data in members.items()
                }
                for device_id, positions in positions_by_device.items():
                    attempted = True
                    try:
                        await self.api.async_set_position(device_id, *positions)
                    except (NormanConnectionError, NormanApiError) as err:
                        raise HomeAssistantError(
                            "ShadeAuto room command failed; some shades may have moved "
                            "and the failed shade's movement is uncertain. "
                            "Remaining shades were not commanded"
                        ) from err
                    self._retain_targets(device_id, bottom, middle, positions)
        finally:
            self._end_command_call()
            if attempted:
                self.async_update_listeners()
                self._schedule_command_refreshes()

    def _resolve_positions(
        self,
        data: NormanPeripheralData,
        bottom: int | None,
        middle: int | None,
        *,
        nudge: bool,
    ) -> tuple[int, int | None]:
        """Resolve each rail using the same rules for individual and room commands."""
        bottom_position = _resolve_rail_position(
            data.bottom_rail_position,
            self.target_position(data.id, "bottom"),
            bottom,
            nudge=nudge,
        )
        if not data.supports_middle_rail:
            if middle is not None:
                raise HomeAssistantError(
                    "ShadeAuto device does not support a middle rail"
                )
            return bottom_position, None
        middle_position = _resolve_rail_position(
            data.middle_rail_position,
            self.target_position(data.id, "middle"),
            middle,
            nudge=nudge,
        )
        return bottom_position, middle_position

    def _retain_targets(
        self,
        device_id: int,
        bottom: int | None,
        middle: int | None,
        positions: tuple[int, int | None],
    ) -> None:
        """Retain only requested rails after the hub accepts a command."""
        for rail, requested, position in zip(
            ("bottom", "middle"), (bottom, middle), positions, strict=True
        ):
            if requested is not None:
                assert position is not None
                self._pending_targets[device_id, rail] = (
                    position,
                    monotonic() + PENDING_TARGET_TIMEOUT,
                )

        self._schedule_motion_expiry()

    def _process_data(
        self, device_info: dict[str, Any], status_data: dict[str, Any]
    ) -> NormanDevices:
        """Process and combine data from GetAllPeripheral and status endpoints.

        Args:
            device_info: Data from GetAllPeripheral endpoint
            status_data: Data from status endpoint

        Returns:
            Combined data keyed by device ID

        """
        if not isinstance(device_info.get("results", {}), dict) or not isinstance(
            status_data.get("Peripherals", []), list
        ):
            raise NormanApiError("Malformed ShadeAuto discovery or status")
        devices: NormanDevices = {}

        # Process device information (names, room, group).
        room_type_counts: dict[str, int] = {}
        rejection_counts: dict[str, int] = {}
        valid_room_ids: set[int] = set()
        raw_room_count = 0
        room_list = device_info.get("results", {}).get("RoomList", [])
        if not isinstance(room_list, list):
            raise NormanApiError("Malformed ShadeAuto room list")
        raw_room_count = len(room_list)
        for room in room_list:
            room_id_raw = (
                room.get("RoomID", _MISSING_ROOM_ID)
                if isinstance(room, dict)
                else _MISSING_ROOM_ID
            )
            type_label = _room_id_type(room_id_raw)
            room_type_counts[type_label] = room_type_counts.get(type_label, 0) + 1
            room_id, rejection = _normalise_room_id(room_id_raw)
            if rejection is not None:
                rejection_counts[rejection] = rejection_counts.get(rejection, 0) + 1
            else:
                assert room_id is not None
                valid_room_ids.add(room_id)

            if not isinstance(room, dict):
                continue
            room_name = room.get("RoomName", "")

            # Process groups
            for group in room.get("GroupList", []):
                group_id = group.get("GroupID")
                group_name = group.get("GroupName", "")

                # Process peripherals
                for peripheral in group.get("PeripheralList", []):
                    peripheral_uid_raw = peripheral.get("PeripheralUID")
                    if peripheral_uid_raw is None:
                        continue
                    try:
                        peripheral_uid = int(peripheral_uid_raw)
                    except (TypeError, ValueError):
                        continue

                    # Create device entry using dataclass
                    device_type = COVER_TYPE_SMARTDRAPE  # Default to SmartDrape
                    # TODO: Support other blind types
                    devices[peripheral_uid] = NormanPeripheralData(
                        id=peripheral_uid,
                        name=peripheral.get(
                            "PeripheralName", f"Norman {peripheral_uid}"
                        ),
                        type=device_type,
                        room_id=room_id,
                        room_name=room_name,
                        group_id=group_id,
                        group_name=group_name,
                        module_type=peripheral.get("ModuleType"),
                        module_detail=peripheral.get("ModuleDetail"),
                        supports_middle_rail=peripheral_uid
                        in self._middle_rail_devices,
                    )

        usable_room_ids = {
            device.room_id for device in devices.values() if device.room_id is not None
        }
        self.room_diagnostics = {
            "raw_room_count": raw_room_count,
            "unique_room_id_count": len(valid_room_ids),
            "usable_room_count": len(usable_room_ids),
            "rejected_room_count": sum(rejection_counts.values()),
            "room_id_type_counts": dict(sorted(room_type_counts.items())),
            "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        }
        if rejection_counts:
            warning_signature = (
                tuple(sorted(room_type_counts.items())),
                tuple(sorted(rejection_counts.items())),
            )
            if warning_signature != self._room_identity_warning_signature:
                _LOGGER.warning(
                    "Skipped %d ShadeAuto room(s) with invalid identities "
                    "(original types: %s; rejection reasons: %s)",
                    sum(rejection_counts.values()),
                    dict(sorted(room_type_counts.items())),
                    dict(sorted(rejection_counts.items())),
                )
                self._room_identity_warning_signature = warning_signature

        # Add status information
        if "Peripherals" in status_data:
            for peripheral in status_data["Peripherals"]:
                peripheral_uid_raw = peripheral.get("PeripheralUID")
                if peripheral_uid_raw is None:
                    continue
                try:
                    peripheral_uid = int(peripheral_uid_raw)
                except (TypeError, ValueError):
                    continue

                if peripheral_uid not in devices:
                    # Create minimal device if not found in device_info
                    devices[peripheral_uid] = NormanPeripheralData(
                        id=peripheral_uid,
                        name=f"Norman {peripheral_uid}",
                        type=COVER_TYPE_SMARTDRAPE,
                    )

                # Update with status information on dataclass
                device = devices[peripheral_uid]
                device.bottom_rail_position = validate_position(
                    peripheral.get("BottomRailPosition")
                )
                device.middle_rail_position = validate_position(
                    peripheral.get("MiddleRailPosition")
                )
                device.target_bottom_rail_position = validate_position(
                    peripheral.get("TargetBottomRailPosition")
                )
                device.target_middle_rail_position = validate_position(
                    peripheral.get("TargetMiddleRailPosition")
                )
                if (
                    device.middle_rail_position is not None
                    or device.target_middle_rail_position is not None
                ):
                    self._middle_rail_devices.add(peripheral_uid)
                device.supports_middle_rail = (
                    peripheral_uid in self._middle_rail_devices
                )
                device.battery_level = peripheral.get("BatteryVoltage")
                device.firmware_version = peripheral.get("FirmwareVersion")
                device.last_update = peripheral.get("Timestamp")

        return devices

    def _begin_command_call(self) -> None:
        """Mark a command as queued before it waits for the shared command lock."""
        if self._command_calls_pending == 0:
            self._command_calls_idle.clear()
        self._command_calls_pending += 1

    def _end_command_call(self) -> None:
        """Mark command completion and release refreshes after a quiet window."""
        self._command_calls_pending -= 1
        if self._command_calls_pending == 0:
            self._last_command_end = monotonic()
            self._command_calls_idle.set()

    def _commands_are_quiet(self) -> bool:
        """Return whether no command is queued and the trailing quiet period passed."""
        if self._command_calls_pending:
            return False
        return self._last_command_end is None or (
            monotonic() - self._last_command_end >= COMMAND_QUIET_PERIOD
        )

    async def _wait_for_command_quiet(self) -> None:
        """Wait for queued commands and their quiet period before reading status."""
        while True:
            if self._command_calls_pending:
                await self._command_calls_idle.wait()
                continue
            if self._last_command_end is None:
                return
            remaining = COMMAND_QUIET_PERIOD - (monotonic() - self._last_command_end)
            if remaining > 0:
                await asyncio.sleep(remaining)
            if self._commands_are_quiet():
                return

    def _schedule_command_refreshes(self) -> None:
        """Schedule a quiet-window refresh and final post-command verification."""
        if self._shutting_down:
            return
        self._schedule_near_refresh()
        self._replace_refresh_task(
            "_followup_refresh_task", COMMAND_FOLLOWUP_REFRESH_DELAY
        )

    def _schedule_near_refresh(self) -> None:
        """Debounce notifications with a maximum wait before requesting a read."""
        if self._shutting_down:
            return
        now = monotonic()
        if self._near_refresh_started is None:
            self._near_refresh_started = now
        delay = min(
            COMMAND_REFRESH_DELAY,
            max(0, NOTIFICATION_REFRESH_MAX_WAIT - (now - self._near_refresh_started)),
        )
        self._replace_refresh_task("_near_refresh_task", delay)

    def _replace_refresh_task(self, attribute: str, delay: float) -> None:
        """Replace only the sleeping timer; active requests remain owned until done."""
        self._cancel_refresh_task(attribute)
        task = self.hass.async_create_background_task(
            self._refresh_after(attribute, delay),
            f"ShadeAuto deferred refresh ({attribute})",
        )
        setattr(self, attribute, task)
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    def _cancel_refresh_task(self, attribute: str) -> None:
        task = getattr(self, attribute)
        if task is not None and not task.done():
            task.cancel()
        setattr(self, attribute, None)

    async def _refresh_after(self, attribute: str, delay: float) -> None:
        """Detach a timer before starting a refresh so replacement cannot abort IO."""
        try:
            await asyncio.sleep(delay)
            if getattr(self, attribute) is asyncio.current_task():
                setattr(self, attribute, None)
                if attribute == "_near_refresh_task":
                    self._near_refresh_started = None
            if not self._shutting_down:
                await self.async_request_refresh()
        except asyncio.CancelledError:
            return

    async def async_shutdown(self) -> None:
        """Cancel deferred refreshes before shutting down the coordinator."""
        if self._shutting_down:
            return
        self._shutting_down = True
        tasks = [task for task in self._refresh_tasks if not task.done()]
        if self._motion_expiry_task is not None and not self._motion_expiry_task.done():
            tasks.append(self._motion_expiry_task)
        self._motion_expiry_task = None
        self._near_refresh_started = None
        self._near_refresh_task = None
        self._followup_refresh_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await super().async_shutdown()
