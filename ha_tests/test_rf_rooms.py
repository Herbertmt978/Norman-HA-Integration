"""Protocol 4 bridge-paced RF rooms using a synthetic stateful native API bridge."""

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
FAST = (patch("custom_components.norman_gen1.rf.ROOM_POLL_SECONDS", 0),)


class Bridge:
    """Mutable protocol 4 bridge: rooms complete after a configurable poll count."""

    def __init__(self) -> None:
        """Start idle with no accepted rooms."""
        self.sequence = 0
        self.completed = 0
        self.result = "none_since_boot"
        self.polls_until_done = 1
        self.pending_polls = 0
        self.outcome = "complete_not_acknowledged"
        self.room_calls: list[dict] = []
        self.target_calls: list[dict] = []
        self.poll_failures = 0
        self.accept: dict | None = None
        self.reject = False
        self.forget = False

    def status(self) -> dict:
        """Advance a pending room by one poll and report protocol 4 status."""
        if self.pending_polls:
            self.pending_polls -= 1
            if not self.pending_polls:
                self.completed, self.result = self.sequence, self.outcome
        return {
            "protocol_version": 4,
            "batch_capacity": 8,
            "ready": True,
            "targets": deepcopy(TARGETS),
            "sequential_rooms": True,
            "command_active": bool(self.pending_polls),
            "command_sequence": self.sequence,
            "completed_sequence": self.completed,
            "command_result": self.result,
        }


@pytest.fixture
def bridge(hass):
    """Register status, single-target and room actions; no radio is touched."""
    fake = Bridge()

    async def read(call):
        if fake.pending_polls and fake.poll_failures:
            fake.poll_failures -= 1
            raise HomeAssistantError("Transient API stall")
        return fake.status()

    async def command(call):
        fake.target_calls.append(json.loads(call.data["targets_json"]))

    async def room(call):
        fake.room_calls.append(json.loads(call.data["targets_json"]))
        if fake.reject:
            raise HomeAssistantError("Room rejected")
        fake.sequence += 1
        response = fake.accept or {"sequence": fake.sequence, "expected_ms": 10}
        fake.pending_polls = fake.polls_until_done
        if fake.forget:  # Bridge rebooted mid-room: its counters restart from zero.
            fake.sequence = fake.completed = fake.pending_polls = 0
        return response

    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_targets_status",
        read,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("esphome", f"{PREFIX}_rf_targets_command", command)
    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_room_command",
        room,
        supports_response=SupportsResponse.ONLY,
    )
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


async def test_room_is_one_bridge_paced_command(hass, bridge):
    """A protocol 4 room is accepted once, then completion is polled."""
    rf = coordinator(hass)
    bridge.polls_until_done = 3
    with FAST[0]:
        await rf.send(rf.targets[:2], True)
    assert bridge.room_calls == [
        {
            "slots": [0, 1],
            "profile_ids": [TARGETS[0]["profile_id"], TARGETS[1]["profile_id"]],
            "positions": [0, 100],
        }
    ]
    assert bridge.target_calls == []
    assert rf.intents == {0: True, 1: True, 2: None}
    await rf.async_shutdown()


async def test_single_section_keeps_target_command(hass, bridge):
    """One section uses the existing blocking single-target action."""
    rf = coordinator(hass)
    with FAST[0]:
        await rf.send(rf.targets[2:], False)
    assert bridge.room_calls == []
    assert [call["slots"] for call in bridge.target_calls] == [[2]]
    assert rf.intents[2] is False
    await rf.async_shutdown()


async def test_polling_tolerates_transient_status_failures(hass, bridge):
    """A stalled status read is retried as a read, never as movement."""
    rf = coordinator(hass)
    bridge.polls_until_done = 2
    bridge.poll_failures = 2
    with FAST[0]:
        await rf.send(rf.targets[:2], True)
    assert bridge.poll_failures == 0
    assert len(bridge.room_calls) == 1
    assert rf.intents == {0: True, 1: True, 2: None}
    await rf.async_shutdown()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"outcome": "radio_tx_failed"}, "bridge reported radio_tx_failed"),
        ({"forget": True}, "lost or superseded"),
        (
            {"accept": {"sequence": 0, "expected_ms": 10}},
            "Malformed RF room acceptance",
        ),
        ({"accept": {"sequence": 1, "expected_ms": 0}}, "Malformed RF room acceptance"),
        (
            {"accept": {"sequence": 1, "expected_ms": 999_999}},
            "Malformed RF room acceptance",
        ),
        (
            {"accept": {"sequence": True, "expected_ms": 10}},
            "Malformed RF room acceptance",
        ),
        ({"reject": True}, "Room rejected"),
    ],
)
async def test_room_failures_make_every_section_uncertain(
    hass, bridge, change, message
):
    """Failure, reboot, malformed acceptance or rejection never retries."""
    rf = coordinator(hass)
    rf.intents.update({0: False, 1: False, 2: True})
    for key, value in change.items():
        setattr(bridge, key, value)
    with FAST[0], pytest.raises(HomeAssistantError) as caught:
        await rf.send(rf.targets[:2], True)
    assert message in str(caught.value)
    assert "all 2 selected sections are uncertain" in str(caught.value)
    assert "No automatic retry" in str(caught.value)
    assert len(bridge.room_calls) == 1
    assert bridge.target_calls == []
    assert rf.intents == {0: None, 1: None, 2: True}
    await rf.async_shutdown()


async def test_completion_deadline_makes_room_uncertain(hass, bridge):
    """A room that never reports completion expires instead of hanging."""
    rf = coordinator(hass)
    bridge.polls_until_done = 10_000
    with (
        FAST[0],
        patch("custom_components.norman_gen1.rf.ROOM_COMPLETION_MARGIN_SECONDS", 0),
        pytest.raises(
            HomeAssistantError, match="No room completion before the deadline"
        ),
    ):
        await rf.send(rf.targets[:2], False)
    assert rf.intents == {0: None, 1: None, 2: None}
    await rf.async_shutdown()


async def test_cancellation_while_polling_is_uncertain(hass, bridge):
    """Caller cancellation after acceptance leaves the room uncertain."""
    rf = coordinator(hass)
    rf.intents.update({0: False, 1: False, 2: True})
    bridge.polls_until_done = 10_000
    task = asyncio.create_task(rf.send(rf.targets[:2], True))
    for _ in range(50):
        if bridge.room_calls:
            break
        await asyncio.sleep(0)
    assert bridge.room_calls
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rf.intents == {0: None, 1: None, 2: True}
    await rf.async_shutdown()


async def test_protocol4_without_room_action_falls_back_per_section(hass, bridge):
    """A bridge exposing protocol 4 status but no room action sends sections."""
    hass.services.async_remove("esphome", f"{PREFIX}_rf_room_command")
    rf = coordinator(hass)
    with FAST[0]:
        await rf.send(rf.targets[:2], True)
    assert bridge.room_calls == []
    assert [call["slots"] for call in bridge.target_calls] == [[0], [1]]
    await rf.async_shutdown()


@pytest.mark.parametrize(
    "change",
    [
        {"sequential_rooms": False},
        {"command_active": 1},
        {"command_sequence": "1"},
        {"completed_sequence": None},
        {"completed_sequence": 2, "command_sequence": 1},
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
