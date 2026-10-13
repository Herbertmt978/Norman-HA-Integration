"""Protocol 4 sections: separate commands with lost-reply recovery from bridge status."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.rf import RFCoordinator, read_status

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
PREFIX = "test_rf_bridge"
TARGETS = [
    {
        "slot": i,
        "profile_id": f"{i + 1:016x}",
        "name": f"Panel {i}",
        "room": "Bedroom" if i < 2 else "Study",
        "open_position": 37,
        "close_position": 0 if i == 0 else 100,
        "close_down_learned": i == 0,
        "close_up_learned": i != 0,
    }
    for i in range(3)
]
FAST = patch("custom_components.norman_gen1.rf.STATUS_POLL_SECONDS", 0)


class Bridge:
    """Mutable protocol 4 bridge with scriptable per-slot outcomes."""

    def __init__(self) -> None:
        """Start idle with four commands since boot."""
        self.sequence = 4
        self.completed = 4
        self.result = "complete_not_acknowledged"
        self.calls: list[list[int]] = []
        self.lose: set[int] = set()  # Burst runs, reply is lost.
        self.reject: set[int] = set()  # Never accepted.
        self.fail: set[int] = set()  # Accepted, radio reports failure.
        self.status_down = False

    def status(self) -> dict:
        """Report protocol 4 status, or fail while the API is stalled."""
        if self.status_down:
            raise HomeAssistantError("Transient API stall")
        return {
            "protocol_version": 4,
            "batch_capacity": 8,
            "ready": True,
            "targets": deepcopy(TARGETS),
            "sequential_rooms": True,
            "command_active": False,
            "command_sequence": self.sequence,
            "completed_sequence": self.completed,
            "command_result": self.result,
        }

    def command(self, slots: list[int]) -> None:
        """Run one single-target command with the scripted outcome."""
        self.calls.append(slots)
        slot = slots[0]
        if slot in self.reject:
            raise HomeAssistantError("Target unavailable or busy")
        self.sequence += 1
        self.completed = self.sequence
        failed = slot in self.fail
        self.result = "radio_tx_failed" if failed else "complete_not_acknowledged"
        if slot in self.lose:
            raise TimeoutError
        if failed:
            raise HomeAssistantError("Radio burst failed; shutter state unknown")


@pytest.fixture
def bridge(hass):
    """Register status and single-target actions; no radio is touched."""
    fake = Bridge()

    async def read(call):
        return fake.status()

    async def command(call):
        fake.command(json.loads(call.data["targets_json"])["slots"])

    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_targets_status",
        read,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("esphome", f"{PREFIX}_rf_targets_command", command)
    return fake


def coordinator(hass) -> RFCoordinator:
    """Create a coordinator bound to the three synthetic targets."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"rf_{PREFIX}",
        version=2,
        title="Test RF",
        data={
            "generation": "esphome_rf",
            "bridge": PREFIX,
            "targets": deepcopy(TARGETS),
        },
    )
    entry.add_to_hass(hass)
    return RFCoordinator(hass, entry)


async def test_room_sends_each_section_separately(hass, bridge):
    """A healthy room is separate single-target commands, all confirmed."""
    rf = coordinator(hass)
    with FAST:
        await rf.send(rf.targets[:2], True)
    assert bridge.calls == [[0], [1]]
    assert rf.intents == {0: True, 1: True, 2: None}
    await rf.async_shutdown()


async def test_lost_reply_confirmed_by_bridge_continues(hass, bridge):
    """A lost reply whose burst completed counts as sent; later sections follow."""
    rf = coordinator(hass)
    bridge.lose = {0}
    with FAST:
        await rf.send(rf.targets[:2], True)
    assert bridge.calls == [[0], [1]]
    assert rf.intents == {0: True, 1: True, 2: None}
    await rf.async_shutdown()


async def test_single_section_lost_reply_is_recovered(hass, bridge):
    """Recovery also applies to an individual section cover."""
    rf = coordinator(hass)
    bridge.lose = {2}
    with FAST:
        await rf.send(rf.targets[2:], False)
    assert rf.intents[2] is False
    await rf.async_shutdown()


async def test_rejected_section_is_reported_and_room_continues(hass, bridge):
    """A command the bridge never accepted keeps its state; others still run."""
    rf = coordinator(hass)
    rf.intents.update({0: False, 1: False, 2: True})
    bridge.reject = {0}
    with FAST, pytest.raises(HomeAssistantError) as caught:
        await rf.send(rf.targets[:2], True)
    assert "1 of 2 section(s) unconfirmed: Panel 0 was not sent" in str(caught.value)
    assert "No automatic retry" in str(caught.value)
    assert bridge.calls == [[0], [1]]
    assert rf.intents == {0: False, 1: True, 2: True}
    await rf.async_shutdown()


async def test_radio_failure_is_uncertain_and_room_continues(hass, bridge):
    """A burst the bridge reports as failed is uncertain, never resent."""
    rf = coordinator(hass)
    bridge.fail = {0}
    with FAST, pytest.raises(HomeAssistantError, match="Panel 0 is uncertain"):
        await rf.send(rf.targets[:2], True)
    assert bridge.calls == [[0], [1]]
    assert rf.intents == {0: None, 1: True, 2: None}
    await rf.async_shutdown()


async def test_superseded_sequence_is_uncertain(hass, bridge):
    """Another command completing in between makes the section uncertain."""
    rf = coordinator(hass)
    bridge.lose = {0}
    original = bridge.command

    def interleaved(slots):
        try:
            original(slots)
        finally:
            if slots == [0]:
                bridge.sequence += 1  # Someone else's command ran meanwhile.
                bridge.completed = bridge.sequence

    bridge.command = interleaved
    with FAST, pytest.raises(HomeAssistantError, match="Panel 0 is uncertain"):
        await rf.send(rf.targets[:2], True)
    assert bridge.calls == [[0], [1]]
    assert rf.intents == {0: None, 1: True, 2: None}
    await rf.async_shutdown()


async def test_unreachable_bridge_stops_remaining_sections(hass, bridge):
    """If no status can be read after a lost reply, later sections are not sent."""
    rf = coordinator(hass)
    rf.intents.update({0: False, 1: False, 2: True})
    bridge.lose = {0}
    original = bridge.command

    def stall(slots):
        try:
            original(slots)
        finally:
            bridge.status_down = True

    bridge.command = stall
    with (
        FAST,
        patch("custom_components.norman_gen1.rf.LOST_REPLY_RECOVERY_SECONDS", 0),
        pytest.raises(HomeAssistantError) as caught,
    ):
        await rf.send(rf.targets[:2], True)
    assert "unreachable" in str(caught.value)
    assert "1 later section(s) were not sent" in str(caught.value)
    assert bridge.calls == [[0]]
    assert rf.intents == {0: None, 1: False, 2: True}
    await rf.async_shutdown()


async def test_recovery_waits_through_status_stalls(hass, bridge):
    """Transient status failures during recovery are read retries only."""
    rf = coordinator(hass)
    bridge.lose = {0}
    original_command = bridge.command
    original_status = bridge.status
    stalls = {"left": 0}

    def stall_then_recover(slots):
        try:
            original_command(slots)
        finally:
            if slots == [0]:
                stalls["left"] = 2

    def status():
        if stalls["left"]:
            stalls["left"] -= 1
            raise HomeAssistantError("Transient API stall")
        return original_status()

    bridge.command = stall_then_recover
    bridge.status = status
    with FAST:
        await rf.send(rf.targets[:2], True)
    assert stalls["left"] == 0
    assert bridge.calls == [[0], [1]]
    assert rf.intents == {0: True, 1: True, 2: None}
    await rf.async_shutdown()


async def test_active_command_is_awaited_before_judging(hass, bridge):
    """Recovery waits while the bridge still reports its burst as active."""
    rf = coordinator(hass)
    bridge.lose = {0}
    original_status = bridge.status
    active = {"left": 2}

    def status():
        result = original_status()
        if bridge.calls and active["left"]:
            active["left"] -= 1
            result["command_active"] = True
        return result

    bridge.status = status
    with FAST:
        await rf.send(rf.targets[:1], True)
    assert active["left"] == 0
    assert rf.intents[0] is True
    await rf.async_shutdown()


async def test_recovery_deadline_without_completion_is_uncertain(hass, bridge):
    """A burst still reported active when the window closes stays uncertain."""
    rf = coordinator(hass)
    bridge.lose = {0}
    original_status = bridge.status

    def status():
        result = original_status()
        if bridge.calls:
            result["command_active"] = True
        return result

    bridge.status = status
    with (
        FAST,
        patch("custom_components.norman_gen1.rf.LOST_REPLY_RECOVERY_SECONDS", 0),
        pytest.raises(HomeAssistantError, match="Panel 0 is uncertain"),
    ):
        await rf.send(rf.targets[:1], True)
    assert rf.intents[0] is None
    await rf.async_shutdown()


async def test_cancellation_marks_current_section_uncertain(hass, bridge):
    """Caller cancellation during a section leaves only that section uncertain."""
    rf = coordinator(hass)
    rf.intents.update({0: False, 1: False, 2: True})
    entered, release = asyncio.Event(), asyncio.Event()

    async def hanging(call):
        entered.set()
        await release.wait()

    hass.services.async_register("esphome", f"{PREFIX}_rf_targets_command", hanging)
    task = asyncio.create_task(rf.send(rf.targets[:2], True))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert rf.intents == {0: None, 1: False, 2: True}
    await rf.async_shutdown()


@pytest.mark.parametrize(
    "change",
    [
        {"sequential_rooms": False},
        {"command_active": 1},
        {"command_sequence": "1"},
        {"completed_sequence": None},
        {"completed_sequence": 9, "command_sequence": 5},
        {"completed_sequence": -1},
        {"command_result": 3},
    ],
)
async def test_malformed_protocol4_status_rejected(hass, bridge, change):
    """Completion fields are untrusted and validated strictly."""
    original = bridge.status

    def broken():
        return {**original(), **change}

    bridge.status = broken
    with pytest.raises(HomeAssistantError, match="Unsupported or malformed"):
        await read_status(hass, PREFIX)


async def test_transient_poll_failures_keep_last_inventory(hass, bridge):
    """Two consecutive failed polls keep covers available; the third does not."""
    rf = coordinator(hass)
    await rf.async_refresh()
    assert rf.last_update_success
    bridge.status_down = True
    for _ in range(2):
        await rf.async_refresh()
        assert rf.last_update_success
    await rf.async_refresh()
    assert not rf.last_update_success
    bridge.status_down = False
    await rf.async_refresh()
    assert rf.last_update_success
    bridge.status_down = True
    await rf.async_refresh()
    assert rf.last_update_success  # A success resets the count.
    await rf.async_shutdown()
