"""Guided RF setup and profile management without real hardware or RF writes."""

from copy import deepcopy
import json

from homeassistant.core import SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.rf_learning import learning_request, parse_profiles

from .test_rf import PREFIX, STATUS, TARGETS, saved_entry, start_flow

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def learning_bridge(hass):
    """Model the versioned HA/ESP boundary with no packet synthesis."""
    state = {
        "status": {**deepcopy(STATUS), "targets": []},
        "calls": [],
        "faults": {},
        "relay": [],
    }

    async def status(call):
        if isinstance(state["faults"].get("status"), Exception):
            raise state["faults"]["status"]
        if state["faults"].get("status"):
            raise HomeAssistantError("offline")
        return deepcopy(state["status"])

    async def command(call):
        state["calls"].append({"operation": "transmit", **dict(call.data)})

    async def relay(call):
        state["relay"].append(call.data["enabled"])
        if state["faults"].get("relay"):
            raise state["faults"]["relay"]

    async def learn(call):
        request = json.loads(call.data["request_json"])
        state["calls"].append(request)
        op = request["operation"]
        if state["faults"].get(op):
            return {
                "learning_version": 1,
                "success": False,
                "error": state["faults"][op],
            }
        result = {
            "learning_version": 1,
            "success": True,
            "active": True,
            "saved_slot": -1,
        }
        if op == "begin":
            state["kind"] = request["kind"]
        elif op == "commit":
            if state["kind"] == "panel":
                target = {
                    **deepcopy(TARGETS[0]),
                    "name": request["name"],
                    "room": request["room"],
                    "close_position": request["close_position"],
                    "close_down_learned": True,
                    "close_up_learned": True,
                }
                state["status"]["targets"] = [target]
            else:
                state["relay_profiles"] = [
                    {
                        "kind": "relay",
                        "slot": 0,
                        "profile_id": "a" * 16,
                        "name": request["name"],
                        "room": "",
                    }
                ]
            result["saved_slot"] = 0
        elif op == "profiles":
            result["profiles"] = [
                {
                    "kind": "panel",
                    **{
                        key: target[key]
                        for key in ("slot", "profile_id", "name", "room")
                    },
                }
                for target in state["status"]["targets"]
            ] + state.get("relay_profiles", [])
        elif op in ("remove", "rename"):
            rows = (
                state["status"]["targets"]
                if request["kind"] == "panel"
                else state["relay_profiles"]
            )
            target = next(row for row in rows if row["slot"] == request["slot"])
            assert target["profile_id"] == request["profile_id"]
            if op == "remove":
                rows.remove(target)
            else:
                target.update(name=request["name"], room=request["room"])
            if state.get("after_mutation_fault"):
                state["faults"]["status"] = state["after_mutation_fault"]
        return result

    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_targets_status",
        status,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("esphome", f"{PREFIX}_rf_targets_command", command)
    hass.services.async_register("esphome", f"{PREFIX}_rf_set_relay", relay)
    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_learning",
        learn,
        supports_response=SupportsResponse.ONLY,
    )
    return state


async def manage(hass):
    """Start at a completely empty, adopted ESPHome bridge."""
    flow = await start_flow(hass)
    return await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"bridge": PREFIX}
    )


async def choose(hass, flow, step):
    """Use the real HA menu selector."""
    return await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": step}
    )


async def submit(hass, flow, data):
    """Submit the current form through HA validation."""
    return await hass.config_entries.flow.async_configure(flow["flow_id"], data)


@pytest.mark.parametrize("opposite", [False, True])
async def test_new_bridge_learning_creates_panel_and_room(
    hass, learning_bridge, opposite
):
    """Learn, confirm and create covers without sending any setup RF."""
    flow = await manage(hass)
    assert flow["step_id"] == "rf_manage"
    flow = await choose(hass, flow, "rf_learn_panel")
    flow = await submit(
        hass,
        flow,
        {
            "name": " Bottom left ",
            "room": " Office ",
            "close_direction": "up",
            "learn_opposite": opposite,
        },
    )
    for action in (
        ["Open", "Close upwards", "Close downwards"]
        if opposite
        else ["Open", "Close upwards"]
    ):
        assert flow["description_placeholders"]["action"] == action
        flow = await submit(hass, flow, {"next_action": "continue"})
    assert flow["step_id"] == "rf_learn_confirm"
    flow = await submit(hass, flow, {"confirmed": True, "enable_relay": True})
    assert learning_bridge["relay"] == [True]
    assert not any(call["operation"] == "transmit" for call in learning_bridge["calls"])
    flow = await choose(hass, flow, "rf_rooms")
    flow = await submit(hass, flow, {"rooms": ["Office"]})
    assert flow["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), flow["result"].entry_id
    )
    assert len(entries) == 2
    assert {
        hass.states.get(entity.entity_id).attributes["command_scope"]
        for entity in entries
    } == {"panel", "room"}
    for entity in entries:
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": entity.entity_id}, blocking=True
        )
        await hass.services.async_call(
            "cover", "close_cover", {"entity_id": entity.entity_id}, blocking=True
        )
    assert (
        len(
            [
                call
                for call in learning_bridge["calls"]
                if call["operation"] == "transmit"
            ]
        )
        == 4
    )


async def test_relay_only_learning_and_finish(hass, learning_bridge):
    """A room forwarding action must not create a direct command or cover."""
    flow = await choose(hass, await manage(hass), "rf_learn_relay")
    flow = await submit(hass, flow, {"name": "Office Open"})
    assert flow["description_placeholders"]["action"] == "Office Open"
    flow = await submit(hass, flow, {"next_action": "continue"})
    flow = await submit(hass, flow, {"confirmed": True, "enable_relay": False})
    flow = await choose(hass, flow, "rf_finish_relay")
    await hass.async_block_till_done()
    assert flow["result"].data["targets"] == []
    assert (
        er.async_entries_for_config_entry(er.async_get(hass), flow["result"].entry_id)
        == []
    )
    assert learning_bridge["relay"] == []


@pytest.mark.parametrize("error", [HomeAssistantError, TimeoutError])
@pytest.mark.parametrize("retry", [False, True])
async def test_saved_profile_retries_only_relay_setting(
    hass, learning_bridge, error, retry
):
    """A relay failure never sends a second commit for an already saved profile."""
    flow = await choose(hass, await manage(hass), "rf_learn_relay")
    flow = await submit(hass, flow, {"name": "Office Open"})
    flow = await submit(hass, flow, {"next_action": "continue"})
    learning_bridge["faults"]["relay"] = error("offline")
    flow = await submit(hass, flow, {"confirmed": True, "enable_relay": True})
    assert flow["step_id"] == "rf_enable_relay"
    assert flow["errors"] == {"base": "rf_relay_enable_failed"}
    assert len(learning_bridge["relay_profiles"]) == 1
    # Repeated follow-up failure must still leave the saved profile alone.
    flow = await submit(hass, flow, {"enable_relay": True})
    assert flow["step_id"] == "rf_enable_relay"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {"enable_relay": retry})
    assert flow["step_id"] == "rf_manage"
    assert len(learning_bridge["relay"]) == (3 if retry else 2)
    assert [c["operation"] for c in learning_bridge["calls"]].count("commit") == 1
    assert not any(c["operation"] == "transmit" for c in learning_bridge["calls"])


@pytest.mark.parametrize("error", [HomeAssistantError, TimeoutError])
@pytest.mark.parametrize("operation", ["remove", "rename"])
async def test_saved_profile_change_retries_only_binding_refresh(
    hass, learning_bridge, error, operation
):
    """An acknowledged change survives repeated status failures without re-mutation."""
    learning_bridge["status"]["targets"] = deepcopy(TARGETS)
    entry = saved_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    flow = await submit(hass, flow, {"bridge": PREFIX})
    flow = await choose(hass, flow, "rf_profile")
    flow = await submit(hass, flow, {"profile": "panel:2"})
    learning_bridge["after_mutation_fault"] = error("offline")
    flow = await submit(
        hass,
        flow,
        {"operation": operation, "name": "Top left", "room": "Bedroom"},
    )
    if operation == "remove":
        flow = await submit(hass, flow, {"confirmed": True})
    assert flow["step_id"] == "rf_profile_refresh"
    assert flow["errors"] == {"base": "rf_profile_refresh_failed"}
    flow = await submit(hass, flow, {})
    assert flow["step_id"] == "rf_profile_refresh"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {})
    assert flow["step_id"] == "rf_manage"
    await hass.async_block_till_done()
    assert entry.data["targets"][:2] == TARGETS[:2]
    assert len(entry.data["targets"]) == (2 if operation == "remove" else 3)
    if operation == "rename":
        assert entry.data["targets"][2]["name"] == "Top left"
        assert entry.data["targets"][2]["room"] == "Bedroom"
        assert entry.data["targets"][2]["profile_id"] == TARGETS[2]["profile_id"]
    assert len(
        er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ) == (3 if operation == "remove" else 4)
    assert [c["operation"] for c in learning_bridge["calls"]].count(operation) == 1
    assert not any(c["operation"] == "transmit" for c in learning_bridge["calls"])


@pytest.mark.parametrize("confirmed", [False, True])
async def test_reconfigure_profile_remove_is_explicit_and_scoped(
    hass, learning_bridge, confirmed
):
    """Remove one bound panel and keep all unrelated panel/room identities."""
    learning_bridge["status"]["targets"] = deepcopy(TARGETS)
    entry = saved_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    flow = await submit(hass, flow, {"bridge": PREFIX})
    flow = await choose(hass, flow, "rf_profile")
    flow = await submit(hass, flow, {"profile": "panel:0"})
    flow = await submit(
        hass, flow, {"operation": "remove", "name": "Panel 0", "room": "Bedroom"}
    )
    assert flow["step_id"] == "rf_profile_remove"
    flow = await submit(hass, flow, {"confirmed": confirmed})
    assert len(entry.data["targets"]) == (2 if confirmed else 3)
    assert len(
        er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ) == (4 if confirmed else 5)
    assert (
        bool(
            [call for call in learning_bridge["calls"] if call["operation"] == "remove"]
        )
        == confirmed
    )


async def test_reconfigure_rename_and_regroup_preserves_identity(hass, learning_bridge):
    """Labels and room membership change without changing learned RF identity."""
    learning_bridge["status"]["targets"] = deepcopy(TARGETS)
    entry = saved_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    flow = await submit(hass, flow, {"bridge": PREFIX})
    flow = await choose(hass, flow, "rf_profile")
    flow = await submit(hass, flow, {"profile": "panel:2"})
    flow = await submit(
        hass, flow, {"operation": "rename", "name": "Top left", "room": "Bedroom"}
    )
    assert entry.data["targets"][2]["profile_id"] == TARGETS[2]["profile_id"]
    assert entry.data["targets"][2]["name"] == "Top left"
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 4
    )


async def test_learning_retry_cancel_and_refused_confirmation(hass, learning_bridge):
    """Aborting any capture leaves the inventory empty."""
    flow = await choose(hass, await manage(hass), "rf_learn_panel")
    flow = await submit(
        hass,
        flow,
        {
            "name": "",
            "room": "Office",
            "close_direction": "down",
            "learn_opposite": False,
        },
    )
    assert flow["errors"]["base"] == "rf_learn_invalid_name"
    flow = await submit(
        hass,
        flow,
        {
            "name": "Bottom",
            "room": "Office",
            "close_direction": "down",
            "learn_opposite": False,
        },
    )
    learning_bridge["faults"]["accept"] = "mixed_actions"
    flow = await submit(hass, flow, {"next_action": "continue"})
    assert flow["errors"]["base"] == "rf_learn_mixed_actions"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {"next_action": "retry"})
    flow = await submit(hass, flow, {"next_action": "cancel"})
    flow = await choose(hass, flow, "rf_learn_relay")
    flow = await submit(hass, flow, {"name": "Room Open"})
    flow = await submit(hass, flow, {"next_action": "continue"})
    flow = await submit(hass, flow, {"confirmed": False, "enable_relay": False})
    assert learning_bridge["status"]["targets"] == []
    assert not any(call["operation"] == "commit" for call in learning_bridge["calls"])


@pytest.mark.parametrize(
    "error",
    [
        "need_two_presses",
        "wrong_endpoint",
        "expired",
        "learning_busy",
        "storage_full",
        "unknown",
    ],
)
async def test_learning_api_error_boundary(hass, learning_bridge, error):
    """Only bounded error identifiers, never RF data, reach the flow."""
    learning_bridge["faults"]["begin"] = error
    with pytest.raises(HomeAssistantError, match="rf_learn"):
        await learning_request(hass, PREFIX, "a" * 32, "begin", kind="panel")


@pytest.mark.parametrize(
    "rows",
    [
        None,
        [None],
        [{}],
        [{"kind": "other"}],
        [
            {
                "kind": "panel",
                "slot": 0,
                "profile_id": "z" * 16,
                "name": "Panel",
                "room": "Room",
            }
        ],
    ],
)
def test_invalid_profile_inventory(rows):
    """Malformed inventory must never become a mutation target."""
    with pytest.raises(HomeAssistantError):
        parse_profiles(rows)


async def test_wizard_transport_failures_and_recovery(hass, learning_bridge):
    """Every disconnected setup form remains retryable without RF or writes."""
    flow = await manage(hass)
    learning_bridge["faults"]["profiles"] = "expired"
    flow = await choose(hass, flow, "rf_profile")
    assert flow["errors"]["base"] == "rf_learn_expired"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {})
    assert flow["step_id"] == "rf_manage"
    flow = await choose(hass, flow, "rf_learn_relay")
    learning_bridge["faults"]["begin"] = "learning_busy"
    learning_bridge["faults"]["cancel"] = "session_mismatch"
    flow = await submit(hass, flow, {"name": "Office Open"})
    assert flow["errors"]["base"] == "rf_learn_learning_busy"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {"name": "Office Open"})
    flow = await submit(hass, flow, {"next_action": "continue"})
    learning_bridge["faults"]["commit"] = "storage_full"
    flow = await submit(hass, flow, {"confirmed": True, "enable_relay": True})
    assert flow["errors"]["base"] == "rf_learn_storage_full"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {"confirmed": True, "enable_relay": False})
    flow = await choose(hass, flow, "rf_profile")
    flow = await submit(hass, flow, {"profile": "relay:0"})
    flow = await submit(hass, flow, {"operation": "rename", "name": ""})
    assert flow["errors"]["base"] == "rf_learn_invalid_name"
    learning_bridge["faults"]["rename"] = "storage_or_profile_error"
    flow = await submit(hass, flow, {"operation": "rename", "name": "Open office"})
    assert flow["errors"]["base"] == "rf_learn_storage_or_profile_error"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {"operation": "rename", "name": "Open office"})
    flow = await choose(hass, flow, "rf_profile")
    flow = await submit(hass, flow, {"profile": "relay:0"})
    flow = await submit(hass, flow, {"operation": "remove", "name": "Open office"})
    learning_bridge["faults"]["remove"] = "storage_or_profile_error"
    flow = await submit(hass, flow, {"confirmed": True})
    assert flow["errors"]["base"] == "rf_learn_storage_or_profile_error"
    learning_bridge["faults"].clear()
    flow = await submit(hass, flow, {"confirmed": True})
    assert learning_bridge["relay_profiles"] == []


async def test_learning_missing_and_malformed_service(hass, learning_bridge):
    """Capability and response validation fail before offering any authority."""
    with pytest.raises(HomeAssistantError, match="rf_learning_unavailable"):
        await learning_request(hass, "unknown", "a" * 32, "begin", kind="panel")

    async def malformed(call):
        return {"learning_version": 2, "success": "yes"}

    hass.services.async_register(
        "esphome",
        f"{PREFIX}_rf_learning",
        malformed,
        supports_response=SupportsResponse.ONLY,
    )
    with pytest.raises(HomeAssistantError, match="rf_learning_unavailable"):
        await learning_request(hass, PREFIX, "a" * 32, "begin", kind="panel")


def test_duplicate_profile_inventory():
    """A duplicate slot cannot silently select a different record."""
    row = {
        "kind": "panel",
        "slot": 0,
        "profile_id": "a" * 16,
        "name": "Panel",
        "room": "Room",
    }
    with pytest.raises(HomeAssistantError):
        parse_profiles([row, row])
