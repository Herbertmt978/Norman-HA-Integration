"""Whole-house learned RF transport tests using synthetic native API services."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

from homeassistant.core import SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.config_flow import ConfigFlow
from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.diagnostics import async_get_config_entry_diagnostics
from custom_components.norman_gen1.rf import (
    RFCoordinator,
    async_unload_entry as unload_rf,
    binding_data,
    bridge_choices,
    parse_targets,
    read_status,
)

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
STATUS = {"protocol_version": 3, "batch_capacity": 8, "ready": True, "targets": TARGETS}


@pytest.fixture
def bridge(hass):
    """No real hub or radio is touched by these fixtures."""
    status, calls, faults = deepcopy(STATUS), [], {}

    async def read(call):
        if faults.get("status"):
            raise HomeAssistantError("Disconnected")
        return deepcopy(status)

    async def command(call):
        data = json.loads(call.data["targets_json"])
        calls.append(data)
        if faults.get("command") in data["slots"]:
            raise HomeAssistantError("Uncertain burst")
        if faults.get("cancel"):
            raise asyncio.CancelledError
        await asyncio.sleep(0)

    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_targets_status",
        read,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("esphome", f"{PREFIX}_rf_targets_command", command)
    return status, calls, faults


def saved_entry(hass):
    """Create a separate RF entry with three explicitly bound targets."""
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
    return entry


async def start_flow(hass):
    """Exercise the real generation selector and RF setup form."""
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"generation": "esphome_rf"}
    )


async def test_rf_requires_adopted_protocol3_bridge(hass):
    """Reject absent, old-protocol and arbitrary service targets."""
    assert bridge_choices(hass) == {}
    assert (await start_flow(hass))["reason"] == "no_rf_bridge"
    with pytest.raises(HomeAssistantError):
        await read_status(hass, "../arbitrary")
    with pytest.raises(HomeAssistantError):
        await read_status(hass, "unknown")


async def test_rf_flow_creates_panels_rooms_and_preserves_hub(
    hass, bridge, mock_norman_api
):
    """Create all covers and share assumed state without invoking the hub."""
    flow = await start_flow(hass)
    assert flow["step_id"] == "rf"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    assert flow["step_id"] == "rf_rooms"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Bedroom", "Study"]}
    )
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = flow["result"]
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entities) == 5
    states = [hass.states.get(entity.entity_id) for entity in entities]
    assert all(state.state == "unknown" for state in states)
    panel = next(
        state
        for state in states
        if state.attributes["command_scope"] == "panel"
        and state.attributes["target_slots"] == [0]
    )
    room = next(state for state in states if state.attributes["target_slots"] == [0, 1])
    assert panel.attributes["supported_features"] == 3
    assert panel.attributes["physical_feedback"] is False
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": room.entity_id}, blocking=True
    )
    assert [call["slots"] for call in bridge[1]] == [[0, 1]]
    assert bridge[1][0]["positions"] == [0, 100]
    assert hass.states.get(room.entity_id).state == "closed"
    assert hass.states.get(panel.entity_id).state == "closed"
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": panel.entity_id}, blocking=True
    )
    assert bridge[1][-1]["positions"] == [37]
    assert hass.states.get(room.entity_id).state == "open"
    assert await async_get_config_entry_diagnostics(hass, entry) == {
        "transport": "esphome_rf",
        "connected": True,
        "physical_feedback": False,
    }
    assert mock_norman_api.clients == []
    assert (await hass.config_entries.options.async_init(entry.entry_id))[
        "reason"
    ] == "rf_use_reconfigure"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(panel.entity_id).state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert PREFIX in bridge_choices(hass)


async def test_flow_unavailable_empty_and_reconfigure(hass, bridge):
    """Handle unavailable bridges and refresh bindings only on the same device."""
    status, _, faults = bridge
    flow = await start_flow(hass)
    faults["status"] = True
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    assert flow["errors"] == {"base": "rf_unavailable"}
    faults.clear()
    status["targets"] = []
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    assert flow["errors"] == {"base": "rf_not_learned"}
    status["targets"] = deepcopy(TARGETS)
    status["ready"] = False
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    assert flow["errors"] == {"base": "rf_not_learned"}
    status["ready"] = True
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Bedroom", "Study"]}
    )
    await hass.async_block_till_done()
    entry = flow["result"]

    async def other(call):
        raise AssertionError("Wrong bridge must never be invoked")

    hass.services.async_register(
        "esphome",
        "other_rf_targets_status",
        other,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("esphome", "other_rf_targets_command", other)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": "other"}
    )
    assert flow["errors"] == {"base": "rf_wrong_bridge"}
    status["targets"][0]["name"] = "Renamed panel"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Bedroom", "Study"]}
    )
    assert flow["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert entry.data["targets"][0]["name"] == "Renamed panel"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_selected_rooms_have_one_transmitter(hass, bridge):
    """Create only selected covers and reject overlap across different bridges."""
    flow = await start_flow(hass)
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Study"]}
    )
    await hass.async_block_till_done()
    entry = flow["result"]
    assert [target["slot"] for target in entry.data["targets"]] == [2]
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 2
    )
    duplicate = await start_flow(hass)
    duplicate = await hass.config_entries.flow.async_configure(
        duplicate["flow_id"], {"bridge": PREFIX}
    )
    assert duplicate["reason"] == "already_configured"

    async def other_status(call):
        return deepcopy(STATUS)

    async def other_command(call):
        raise AssertionError("Commissioning must never transmit")

    hass.services.async_register(
        "esphome",
        "other_rf_targets_status",
        other_status,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("esphome", "other_rf_targets_command", other_command)
    other = await start_flow(hass)
    other = await hass.config_entries.flow.async_configure(
        other["flow_id"], {"bridge": "other"}
    )
    other = await hass.config_entries.flow.async_configure(
        other["flow_id"], {"rooms": ["Study"]}
    )
    assert other["errors"] == {"base": "rf_room_claimed"}
    other = await hass.config_entries.flow.async_configure(
        other["flow_id"], {"rooms": ["Bedroom"]}
    )
    await hass.async_block_till_done()
    assert [target["slot"] for target in other["result"].data["targets"]] == [0, 1]
    assert not bridge[1]
    assert await hass.config_entries.async_unload(other["result"].entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_room_selection_rechecks_inventory_and_readiness(hass, bridge):
    """Require explicit reconfirmation when a bridge changes after selection."""
    flow = await start_flow(hass)
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": []}
    )
    assert flow["errors"] == {"base": "rf_invalid_rooms"}
    bridge[2]["status"] = True
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Study"]}
    )
    assert flow["errors"] == {"base": "rf_unavailable"}
    bridge[2].clear()
    bridge[0]["ready"] = False
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Study"]}
    )
    assert flow["errors"] == {"base": "rf_unavailable"}
    bridge[0]["ready"] = True
    bridge[0]["targets"][2]["profile_id"] = "f" * 16
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Study"]}
    )
    assert flow["errors"] == {"base": "rf_inventory_changed"}
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"rooms": ["Study"]}
    )
    await hass.async_block_till_done()
    assert flow["result"].data["targets"][0]["profile_id"] == "f" * 16
    assert await hass.config_entries.async_unload(flow["result"].entry_id)


async def test_room_step_without_bridge_returns_to_selection(hass, bridge):
    """An out-of-order step cannot bind a guessed bridge."""
    flow = ConfigFlow()
    flow.hass = hass
    assert (await flow.async_step_rf_rooms())["step_id"] == "rf"


@pytest.mark.parametrize("rooms", [[], ["Unknown"], ["Study", "Study"], [1]])
async def test_invalid_room_binding(hass, bridge, rooms):
    """Room binding rejects empty, unknown, duplicate and malformed selection."""
    with pytest.raises(HomeAssistantError):
        binding_data(await read_status(hass, PREFIX), rooms)


async def test_room_binding_rejects_oversize(hass, bridge):
    """Do not expose a whole-room action beyond the firmware batch capacity."""
    bridge[0]["targets"] = [
        dict(TARGETS[0], slot=i, profile_id=f"{i + 1:016x}") for i in range(9)
    ]
    with pytest.raises(HomeAssistantError):
        binding_data(await read_status(hass, PREFIX), ["Bedroom"])


@pytest.mark.parametrize(
    "change",
    [
        {"protocol_version": 1},
        {"protocol_version": 2},
        {"protocol_version": True},
        {"batch_capacity": True},
        {"batch_capacity": 32},
        {"ready": 1},
        {"targets": None},
        {"targets": "bad"},
        {"targets": [None]},
        {"targets": TARGETS * 11},
    ],
)
async def test_malformed_status_rejected(hass, bridge, change):
    """Reject malformed inventories before they can authorize transmission."""
    bridge[0].update(change)
    with pytest.raises(HomeAssistantError):
        await read_status(hass, PREFIX)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("slot", -1),
        ("slot", 32),
        ("slot", True),
        ("profile_id", "bad"),
        ("name", ""),
        ("name", "x" * 48),
        ("room", None),
        ("room", ""),
        ("open_position", 0),
        ("open_position", 100),
        ("open_position", True),
        ("close_position", 50),
        ("close_position", False),
        ("close_down_learned", False),
        ("close_up_learned", 1),
    ],
)
def test_bad_target_rejected(key, value):
    """Validate identity, labels and physically learned endpoint fields."""
    target = deepcopy(TARGETS[0])
    target[key] = value
    with pytest.raises(HomeAssistantError):
        parse_targets([target])


@pytest.mark.parametrize("key", ["slot", "profile_id"])
def test_duplicate_target_rejected(key):
    """A section cannot appear twice under ambiguous identities."""
    targets = deepcopy(TARGETS)
    targets[1][key] = targets[0][key]
    with pytest.raises(HomeAssistantError):
        parse_targets(targets)


async def test_non_mapping_response_rejected(hass, bridge):
    """A fire-and-forget response is not a usable inventory."""

    async def wrong(call):
        return None

    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_targets_status",
        wrong,
        supports_response=SupportsResponse.ONLY,
    )
    with pytest.raises(HomeAssistantError):
        await read_status(hass, PREFIX)


async def test_shared_serializer_and_uncertain_room_failure(hass, bridge):
    """A room is one batch; uncertainty affects every selected member only."""
    coordinator = RFCoordinator(hass, saved_entry(hass))
    await coordinator.async_refresh()
    await asyncio.gather(
        coordinator.send(coordinator.targets[:2], True),
        coordinator.send(coordinator.targets[2:], False),
    )
    assert [call["slots"] for call in bridge[1]] == [[0, 1], [2]]
    bridge[1].clear()
    bridge[2]["command"] = 1
    with pytest.raises(HomeAssistantError, match="all 2 selected"):
        await coordinator.send(coordinator.targets[:2], True)
    assert [call["slots"] for call in bridge[1]] == [[0, 1]]
    assert coordinator.intents == {0: None, 1: None, 2: False}
    await coordinator.async_shutdown()


async def test_missing_changed_or_cancelled_target_never_retries(hass, bridge):
    """Reject stale bindings and preserve uncertainty on cancellation."""
    coordinator = RFCoordinator(hass, saved_entry(hass))
    await coordinator.async_refresh()
    with pytest.raises(HomeAssistantError):
        await coordinator.send((), False)
    with pytest.raises(HomeAssistantError):
        await coordinator.send(coordinator.targets[:1] * 2, False)
    with pytest.raises(HomeAssistantError):
        await coordinator.send(coordinator.targets * 3, False)
    with pytest.raises(HomeAssistantError):
        await coordinator.send(parse_targets([dict(TARGETS[0], slot=12)]), False)
    for key, value in [
        ("profile_id", "f" * 16),
        ("room", "Other"),
        ("open_position", 38),
        ("close_position", 100),
    ]:
        bridge[0]["targets"][0] = dict(
            TARGETS[0], **{key: value}, close_up_learned=True
        )
        with pytest.raises(HomeAssistantError):
            await coordinator.send(coordinator.targets, False)
    bridge[0]["targets"] = []
    with pytest.raises(HomeAssistantError):
        await coordinator.send(coordinator.targets, False)
    bridge[0]["targets"] = deepcopy(TARGETS)
    bridge[0]["ready"] = False
    with pytest.raises(HomeAssistantError):
        await coordinator.send(coordinator.targets, False)
    assert not bridge[1]
    bridge[0]["ready"] = True
    bridge[2]["cancel"] = True
    with pytest.raises(asyncio.CancelledError):
        await coordinator.send(coordinator.targets[:2], True)
    assert coordinator.intents[0] is None and coordinator.intents[1] is None
    bridge[2].clear()
    bridge[2]["status"] = True
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    await coordinator.async_shutdown()


async def test_room_and_panel_unavailable_on_identity_change(hass, bridge):
    """Changed commissioning invalidates both the section and its room."""
    entry = saved_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    affected_ids = [
        item.entity_id
        for item in entities
        if 0 in hass.states.get(item.entity_id).attributes["target_slots"]
    ]
    bridge[0]["targets"][0]["profile_id"] = "f" * 16
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    affected = [
        hass.states.get(item.entity_id)
        for item in entities
        if item.entity_id in affected_ids
    ]
    assert all(state.state == "unavailable" for state in affected)
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await unload_rf(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
