"""Hub-independent panel and room control through an adopted ESPHome bridge.

The ESP32 alone owns RF bytes, persisted counters and autonomous relay policy.
Successful bursts are command intents, never physical shutter acknowledgments.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import json
import logging
import re
from time import monotonic
from typing import Any, cast

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

RF = "esphome_rf"
CONF_BRIDGE = "bridge"
CONF_TARGETS = "targets"
RF_REQUEST_TIMEOUT_SECONDS = 12.0
# Protocol 4 status lets a lost action reply be checked against the bridge's
# own record of the burst instead of abandoning the rest of the room.
LOST_REPLY_RECOVERY_SECONDS = 20.0
STATUS_POLL_SECONDS = 2.0
_LOGGER = logging.getLogger(__name__)
_PREFIX = re.compile(r"[a-z0-9_]+\Z")
_FINGERPRINT = re.compile(r"[a-f0-9]{16}\Z")


@dataclass(frozen=True)
class RFTarget:
    """Explicit commissioned section identity and its learned endpoint policy."""

    slot: int
    profile_id: str
    name: str
    room: str
    open_position: int
    close_position: int
    close_down_learned: bool
    close_up_learned: bool


@dataclass(frozen=True)
class RFStatus:
    """Strict firmware inventory; deliberately excludes rolling codes."""

    ready: bool
    targets: tuple[RFTarget, ...]
    protocol: int = 3
    command_active: bool = False
    command_sequence: int = 0
    completed_sequence: int = 0
    command_result: str = ""


def parse_targets(value: Any) -> tuple[RFTarget, ...]:
    """Validate both persisted HA bindings and untrusted firmware responses."""
    if not isinstance(value, list) or len(value) > 32:
        raise HomeAssistantError("Malformed RF target inventory")
    targets = []
    slots: set[int] = set()
    identities: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            raise HomeAssistantError("Malformed RF target")
        slot, identity = row.get("slot"), row.get("profile_id")
        name, room = row.get("name"), row.get("room")
        opened, closed = row.get("open_position"), row.get("close_position")
        down, up = row.get("close_down_learned"), row.get("close_up_learned")
        if (
            type(slot) is not int
            or not 0 <= slot < 32
            or slot in slots
            or not isinstance(identity, str)
            or not _FINGERPRINT.fullmatch(identity)
            or identity in identities
            or not isinstance(name, str)
            or not 0 < len(name) < 48
            or not isinstance(room, str)
            or not 0 < len(room) < 48
            or type(opened) is not int
            or not 0 < opened < 100
            or type(closed) is not int
            or closed not in (0, 100)
            or type(down) is not bool
            or type(up) is not bool
            or not (down if closed == 0 else up)
        ):
            raise HomeAssistantError("Invalid, duplicate or unlearned RF target")
        slots.add(slot)
        identities.add(identity)
        targets.append(RFTarget(slot, identity, name, room, opened, closed, down, up))
    return tuple(sorted(targets, key=lambda target: target.slot))


def bridge_choices(hass: HomeAssistant) -> dict[str, str]:
    """Require inventory and identity-checked batch commands."""
    services = hass.services.async_services().get("esphome", {})
    suffix = "_rf_targets_status"
    return {
        prefix: prefix.replace("_", " ")
        for name in services
        if name.endswith(suffix)
        and _PREFIX.fullmatch(prefix := name.removesuffix(suffix))
        and f"{prefix}_rf_targets_command" in services
    }


async def read_status(
    hass: HomeAssistant, prefix: str, *, operation: str = "status"
) -> RFStatus:
    """Read inventory with a named deadline; never transmit or retry RF."""
    started = monotonic()
    _LOGGER.debug("RF %s started for bridge %s", operation, prefix)
    if not _PREFIX.fullmatch(prefix) or prefix not in bridge_choices(hass):
        raise HomeAssistantError("The commissioned ESPHome RF bridge is unavailable")
    try:
        async with asyncio.timeout(RF_REQUEST_TIMEOUT_SECONDS):
            result = await hass.services.async_call(
                "esphome",
                f"{prefix}_rf_targets_status",
                blocking=True,
                return_response=True,
            )
    except asyncio.CancelledError:
        _LOGGER.debug(
            "RF %s cancelled after %.2fs for bridge %s",
            operation,
            monotonic() - started,
            prefix,
        )
        raise
    except (HomeAssistantError, TimeoutError) as err:
        detail = str(err).strip() or "No response before the request deadline"
        raise HomeAssistantError(
            f"RF {operation} failed after {monotonic() - started:.2f}s "
            f"({type(err).__name__}): {detail}"
        ) from err
    if (
        not isinstance(result, dict)
        or type(result.get("protocol_version")) is not int
        or result["protocol_version"] not in (3, 4)
        or type(result.get("batch_capacity")) is not int
        or result["batch_capacity"] != 8
        or type(result.get("ready")) is not bool
    ):
        raise HomeAssistantError("Unsupported or malformed ESPHome RF status")
    protocol = cast(int, result["protocol_version"])
    active = result.get("command_active", False)
    sequence = result.get("command_sequence", 0)
    completed = result.get("completed_sequence", 0)
    outcome = result.get("command_result", "")
    if (
        type(active) is not bool
        or type(sequence) is not int
        or type(completed) is not int
        or not isinstance(outcome, str)
        or (
            protocol == 4
            and (
                result.get("sequential_rooms") is not True
                or "command_active" not in result
                or "command_sequence" not in result
                or "completed_sequence" not in result
                or "command_result" not in result
                or not 0 <= completed <= sequence
            )
        )
    ):
        raise HomeAssistantError("Unsupported or malformed ESPHome RF status")
    status = RFStatus(
        cast(bool, result["ready"]),
        parse_targets(result.get("targets")),
        protocol,
        active,
        sequence,
        completed,
        outcome,
    )
    _LOGGER.debug(
        "RF %s completed after %.2fs for bridge %s",
        operation,
        monotonic() - started,
        prefix,
    )
    return status


def binding_data(status: RFStatus, rooms: list[str]) -> list[dict[str, Any]]:
    """Bind complete selected rooms, never overlapping or oversized batches."""
    available = {target.room for target in status.targets}
    if (
        not rooms
        or any(not isinstance(room, str) for room in rooms)
        or len(set(rooms)) != len(rooms)
        or not set(rooms) <= available
        or any(
            sum(target.room == room for target in status.targets) > 8 for room in rooms
        )
    ):
        raise HomeAssistantError(
            "Select complete learned rooms of at most eight sections"
        )
    return [asdict(target) for target in status.targets if target.room in rooms]


def claimed_profiles(hass: HomeAssistant, exclude_entry_id: str = "") -> set[str]:
    """Keep one direct-command owner per learned identity across RF entries."""
    return {
        target.profile_id
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != exclude_entry_id and entry.data.get("generation") == RF
        for target in parse_targets(entry.data.get(CONF_TARGETS))
    }


class RFCoordinator(DataUpdateCoordinator[RFStatus]):
    """One shared serializer for every room and section on this bridge."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
        """Pin commissioning identities and start with unknown physical state."""
        super().__init__(
            hass, _LOGGER, name=entry.title, update_interval=timedelta(seconds=30)
        )
        self.bridge: str = entry.data[CONF_BRIDGE]
        self.targets = parse_targets(entry.data.get(CONF_TARGETS))
        self._command_lock = asyncio.Lock()
        self.intents: dict[int, bool | None] = {
            target.slot: None for target in self.targets
        }

    async def _async_update_data(self) -> RFStatus:
        try:
            return await read_status(self.hass, self.bridge)
        except (HomeAssistantError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err

    def usable(self, status: RFStatus, target: RFTarget) -> bool:
        """Do not let changed templates, room membership or direction retarget HA."""
        current = next(
            (item for item in status.targets if item.slot == target.slot), None
        )
        return (
            status.ready
            and current is not None
            and current.profile_id == target.profile_id
            and current.room == target.room
            and current.open_position == target.open_position
            and current.close_position == target.close_position
            and (
                current.close_down_learned
                if target.close_position == 0
                else current.close_up_learned
            )
        )

    async def send(self, targets: tuple[RFTarget, ...], closed: bool) -> None:
        """Send each section as its own command; never retry movement.

        Several sections' frames on air together (firmware batches, interleaved
        or bridge-paced) intermittently left panels unmoved, as the hub's room
        broadcast did. Separate single-section commands moved every panel in
        camera trials, matching the hub's paced room path. With protocol 4, a
        lost action reply is checked against the bridge's command sequence and
        result, and the remaining sections are still sent.
        """
        if (
            not targets
            or len(targets) > 8
            or len({target.slot for target in targets}) != len(targets)
            or any(target not in self.targets for target in targets)
        ):
            raise HomeAssistantError(
                "Select one to eight unique commissioned RF targets"
            )
        async with self._command_lock:
            status = await read_status(
                self.hass, self.bridge, operation="pre-command status"
            )
            if any(not self.usable(status, target) for target in targets):
                raise HomeAssistantError(
                    "RF target unavailable or commissioning changed"
                )
            if status.protocol < 4:
                for index, target in enumerate(targets):
                    await self._send_one(target, closed, len(targets) - index - 1)
                return
            expected = status.command_sequence + 1
            problems: list[str] = []
            for index, target in enumerate(targets):
                outcome, last = await self._send_confirmed(target, closed, expected)
                if last is None:
                    unsent = len(targets) - index - 1
                    later = (
                        f"; {unsent} later section(s) were not sent" if unsent else ""
                    )
                    raise HomeAssistantError(
                        f"RF bridge unreachable after a lost reply; section "
                        f"{target.name} is uncertain{later}. No automatic retry."
                    )
                if outcome is False:
                    problems.append(f"{target.name} was not sent")
                elif outcome is None:
                    problems.append(f"{target.name} is uncertain")
                expected = last + 1
            if problems:
                raise HomeAssistantError(
                    f"RF command finished with {len(problems)} of {len(targets)} "
                    f"section(s) unconfirmed: {'; '.join(problems)}. "
                    "No automatic retry."
                )

    async def _call_target(self, target: RFTarget, closed: bool) -> None:
        """Run one single-target action; firmware reserves the code before RF."""
        async with asyncio.timeout(RF_REQUEST_TIMEOUT_SECONDS):
            await self.hass.services.async_call(
                "esphome",
                f"{self.bridge}_rf_targets_command",
                {
                    "targets_json": json.dumps(
                        {
                            "slots": [target.slot],
                            "profile_ids": [target.profile_id],
                            "positions": [
                                target.close_position
                                if closed
                                else target.open_position
                            ],
                        },
                        separators=(",", ":"),
                    ),
                },
                blocking=True,
            )

    async def _send_confirmed(
        self, target: RFTarget, closed: bool, expected: int
    ) -> tuple[bool | None, int | None]:
        """Protocol 4 section: True sent, False not sent, None uncertain.

        Also returns the bridge's latest command sequence, or None when the
        bridge could not be reached after a lost reply.
        """
        started = monotonic()
        try:
            await self._call_target(target, closed)
        except asyncio.CancelledError:
            self.intents[target.slot] = None
            self.async_update_listeners()
            raise
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.debug(
                "RF reply for bridge %s slot %s failed after %.2fs (%s: %s); "
                "checking the bridge's record",
                self.bridge,
                target.slot,
                monotonic() - started,
                type(err).__name__,
                str(err).strip() or "no response",
            )
            outcome, last = await self._recover(expected)
            if outcome is True:
                self.intents[target.slot] = closed
            elif outcome is None:
                self.intents[target.slot] = None
            self.async_update_listeners()
            await asyncio.sleep(0.3)
            return outcome, last
        self.intents[target.slot] = closed
        self.async_update_listeners()
        # Hold the shared lock through firmware's250ms cooldown.
        await asyncio.sleep(0.3)
        return True, expected

    async def _recover(self, expected: int) -> tuple[bool | None, int | None]:
        """Read status until the bridge shows whether sequence `expected` ran."""
        deadline = monotonic() + LOST_REPLY_RECOVERY_SECONDS
        seen: int | None = None
        while True:
            try:
                status = await read_status(
                    self.hass, self.bridge, operation="lost-reply status"
                )
            except HomeAssistantError as err:
                _LOGGER.debug("%s; still checking the lost reply", err)
            else:
                seen = status.command_sequence
                if not status.command_active:
                    if status.command_sequence < expected:
                        return False, seen  # Never accepted, so nothing was sent.
                    if status.completed_sequence >= expected:
                        done = (
                            status.completed_sequence == expected
                            and status.command_result == "complete_not_acknowledged"
                        )
                        return (True if done else None), seen
            if monotonic() >= deadline:
                return None, seen
            await asyncio.sleep(STATUS_POLL_SECONDS)

    async def _send_one(self, target: RFTarget, closed: bool, unsent: int) -> None:
        """Protocol 3 section; a failure leaves later sections unsent."""
        started = monotonic()
        _LOGGER.debug(
            "RF command started for bridge %s slot %s", self.bridge, target.slot
        )
        try:
            await self._call_target(target, closed)
        except asyncio.CancelledError:
            _LOGGER.debug(
                "RF command cancelled after %.2fs for bridge %s; "
                "section %s is uncertain and %s later section(s) were not sent",
                monotonic() - started,
                self.bridge,
                target.slot,
                unsent,
            )
            self.intents[target.slot] = None
            self.async_update_listeners()
            raise
        except (HomeAssistantError, TimeoutError) as err:
            self.intents[target.slot] = None
            self.async_update_listeners()
            detail = str(err).strip() or "No response before the request deadline"
            later = f"; {unsent} later section(s) were not sent" if unsent else ""
            raise HomeAssistantError(
                f"RF command failed after {monotonic() - started:.2f}s "
                f"({type(err).__name__}): {detail}; "
                f"section {target.name} is uncertain{later}. "
                "No automatic retry."
            ) from err
        _LOGGER.debug(
            "RF command action completed after %.2fs for bridge %s; "
            "physical shutter state remains assumed",
            monotonic() - started,
            self.bridge,
        )
        self.intents[target.slot] = closed
        self.async_update_listeners()
        # Hold the shared lock through firmware's250ms cooldown.
        await asyncio.sleep(0.3)


def rf_coordinator(entry: ConfigEntry[Any]) -> RFCoordinator:
    """Narrow the separate RF runtime without changing either hub protocol."""
    return cast(RFCoordinator, entry.runtime_data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Set up with no Norman hub configured or powered."""
    coordinator = RFCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, (Platform.COVER,))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Leave ESPHome adoption and standalone relay untouched."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, (Platform.COVER,)
    )
    if unloaded:
        await rf_coordinator(entry).async_shutdown()
    return unloaded


def async_setup_covers(
    entry: ConfigEntry[Any], async_add_entities: AddEntitiesCallback
) -> None:
    """Create all bound sections plus exact room fan-outs."""
    targets = rf_coordinator(entry).targets
    entities = [RFCover(entry, (target,)) for target in targets]
    entities.extend(
        RFCover(entry, tuple(target for target in targets if target.room == room), room)
        for room in sorted({target.room for target in targets})
    )
    async_add_entities(entities)


class RFCover(CoordinatorEntity[RFCoordinator], CoverEntity):
    """Panel or room with shared, explicitly assumed command state."""

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(
        self,
        entry: ConfigEntry[Any],
        targets: tuple[RFTarget, ...],
        room: str | None = None,
    ) -> None:
        """Room IDs derive from membership labels, section IDs from persistent slots."""
        coordinator = rf_coordinator(entry)
        super().__init__(coordinator)
        self.targets = targets
        self.room = room
        suffix = (
            f"room_{hashlib.sha256(room.encode()).hexdigest()[:12]}"
            if room is not None
            else f"panel_{targets[0].slot}"
        )
        self._attr_unique_id = f"rf_{coordinator.bridge}_{suffix}"
        self._attr_name = room if room is not None else targets[0].name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"rf_{coordinator.bridge}")},
            name=entry.title,
            manufacturer="Community",
            model="ESPHome nRF24 multipanel prototype",
        )

    @property
    def available(self) -> bool:
        """A room is available only when every bound section is available."""
        return super().available and all(
            self.coordinator.usable(self.coordinator.data, target)
            for target in self.targets
        )

    @property
    def is_closed(self) -> bool | None:
        """No restored position, inferred RF acknowledgment or hub-state mirroring."""
        if not self.available:
            return None
        values = [self.coordinator.intents[target.slot] for target in self.targets]
        return None if any(value is None for value in values) else all(values)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose direction and grouping without private RF command data."""
        return {
            "transport": RF,
            "physical_feedback": False,
            "command_scope": "room" if self.room is not None else "panel",
            "room_transmission": "sequential",
            "target_slots": [target.slot for target in self.targets],
            "open_positions": [target.open_position for target in self.targets],
            "close_positions": [target.close_position for target in self.targets],
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Send exact learned open intents through the bridge serializer."""
        await self.coordinator.send(self.targets, False)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Use each section's own commissioned closing direction."""
        await self.coordinator.send(self.targets, True)
