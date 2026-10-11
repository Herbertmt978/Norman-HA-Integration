"""Exercise ShadeAuto room covers through Home Assistant's public services."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, call, patch

from homeassistant.const import CONF_HOST
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.gen2.api import NormanApiClient

from . import test_gen2
from .test_gen2 import DEVICES, entry

shadeauto = test_gen2.shadeauto

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def rooms(shadeauto):
    """Two rooms with different rail positions and separate peripheral IDs."""
    discovery = deepcopy(DEVICES)
    first = discovery["results"]["RoomList"][0]
    first["GroupList"][0]["PeripheralList"].append(
        {"PeripheralUID": 8, "PeripheralName": "Second shade", "ModuleType": 1}
    )
    other = deepcopy(first)
    other.update(RoomID=2, RoomName="Office")
    other["GroupList"][0]["PeripheralList"] = [
        {"PeripheralUID": 9, "PeripheralName": "Office shade", "ModuleType": 1}
    ]
    discovery["results"]["RoomList"].append(other)
    shadeauto.async_get_devices.return_value = discovery
    shadeauto.async_get_status.return_value = {
        "Peripherals": [
            {"PeripheralUID": 7, "BottomRailPosition": 20, "MiddleRailPosition": 80},
            {"PeripheralUID": 8, "BottomRailPosition": 70, "MiddleRailPosition": 30},
            {"PeripheralUID": 9, "BottomRailPosition": 90, "MiddleRailPosition": 10},
        ]
    }
    return shadeauto


async def setup_rooms(hass):
    """Set up the integration and find its room via its persistent identity."""
    configured = entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    await hass.async_block_till_done()
    room_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_room_1"
    )
    assert room_id is not None
    return configured, room_id


async def test_room_discovery_identity_and_reload(hass, rooms):
    """Room entities coexist with existing covers and survive reload unchanged."""
    configured, entity_id = await setup_rooms(hass)
    registry = er.async_get(hass)
    state = hass.states.get(entity_id)
    assert state.name == "Living room Shades"
    assert state.attributes["current_position"] == 45
    assert state.attributes["current_tilt_position"] == 55
    assert state.attributes["member_count"] == 2
    assert state.state == "open"
    assert registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_7")
    assert registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_8")
    office = registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_room_2")
    assert hass.states.get(office).attributes["member_count"] == 1
    assert await hass.config_entries.async_reload(configured.entry_id)
    await hass.async_block_till_done()
    assert (
        registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_room_1")
        == entity_id
    )
    assert hass.states.get(entity_id).attributes["member_count"] == 2
    assert await hass.config_entries.async_unload(configured.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"


@pytest.mark.parametrize(
    ("domain", "action", "data", "expected"),
    [
        ("cover", "open_cover", {}, [call(7, 100, 80), call(8, 100, 30)]),
        ("cover", "close_cover", {}, [call(7, 0, 80), call(8, 0, 30)]),
        (
            "cover",
            "set_cover_position",
            {"position": 45},
            [call(7, 45, 80), call(8, 45, 30)],
        ),
        ("cover", "open_cover_tilt", {}, [call(7, 20, 100), call(8, 70, 100)]),
        ("cover", "close_cover_tilt", {}, [call(7, 20, 0), call(8, 70, 0)]),
        (
            "cover",
            "set_cover_tilt_position",
            {"tilt_position": 45},
            [call(7, 20, 45), call(8, 70, 45)],
        ),
        (DOMAIN, "nudge_position", {"step": 40}, [call(7, 60, 80), call(8, 100, 30)]),
        (DOMAIN, "nudge_tilt", {"step": -40}, [call(7, 20, 40), call(8, 70, 0)]),
    ],
)
async def test_room_actions(hass, rooms, domain, action, data, expected):
    """All public actions affect only this room, preserving each child's other rail."""
    _, entity_id = await setup_rooms(hass)
    await hass.services.async_call(
        domain, action, {"entity_id": entity_id, **data}, blocking=True
    )
    assert rooms.async_set_position.await_args_list == expected


async def test_room_targets_and_individual_control_share_state(hass, rooms):
    """A room tilt command preserves a pending individual position command."""
    _, entity_id = await setup_rooms(hass)
    individual = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_7"
    )
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": individual, "position": 50},
        blocking=True,
    )
    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": entity_id, "tilt_position": 40},
        blocking=True,
    )
    assert rooms.async_set_position.await_args_list == [
        call(7, 50, 80),
        call(7, 50, 40),
        call(8, 70, 40),
    ]
    state = hass.states.get(entity_id)
    assert state.attributes["target_tilt"] == 40


@pytest.mark.parametrize(
    ("positions", "expected_state", "expected_position"),
    [([0, 0], "closed", 0), ([0, 1], "open", 0), ([None, 0], "unknown", None)],
)
async def test_room_closed_requires_all_members(
    hass, rooms, positions, expected_state, expected_position
):
    """An unknown member is not hidden and rounding cannot imply full closure."""
    rooms.async_get_status.return_value["Peripherals"][0]["BottomRailPosition"] = (
        positions[0]
    )
    rooms.async_get_status.return_value["Peripherals"][1]["BottomRailPosition"] = (
        positions[1]
    )
    _, entity_id = await setup_rooms(hass)
    state = hass.states.get(entity_id)
    assert state.state == expected_state
    assert state.attributes.get("current_position") == expected_position


async def test_room_becomes_unavailable_when_empty_or_hub_fails(hass, rooms):
    """Room availability follows coordinator membership and connection state."""
    configured, entity_id = await setup_rooms(hass)
    coordinator = configured.runtime_data
    saved = coordinator.data
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    coordinator.async_set_updated_data(saved)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "open"
    coordinator.async_set_update_error(Exception("test disconnection"))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"


@pytest.mark.parametrize("room_id", [None, True, "1", 1.5])
async def test_invalid_room_identity_does_not_create_group(hass, shadeauto, room_id):
    """Keep individual covers even when room identity is missing or malformed."""
    shadeauto.async_get_devices.return_value["results"]["RoomList"][0]["RoomID"] = (
        room_id
    )
    configured = entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(
        er.async_get(hass), configured.entry_id
    )
    assert [item.unique_id for item in entities] == ["gen2_shade-hub_7"]


async def test_unnamed_room_has_stable_fallback(hass, shadeauto):
    """A missing display name does not remove a valid room identity."""
    shadeauto.async_get_devices.return_value["results"]["RoomList"][0]["RoomName"] = ""
    _, entity_id = await setup_rooms(hass)
    assert hass.states.get(entity_id).name == "Room 1 Shades"


async def test_matching_room_numbers_on_separate_hubs_stay_isolated(hass, rooms):
    """Registration and commands distinguish identical room and peripheral numbers."""
    second_api = MagicMock(spec=NormanApiClient)
    second_api.thing_name = "second-hub"
    second_api.async_validate_connection = AsyncMock(return_value=True)
    second_api.async_get_devices = AsyncMock(
        return_value=deepcopy(rooms.async_get_devices.return_value)
    )
    second_api.async_get_status = AsyncMock(
        return_value=deepcopy(rooms.async_get_status.return_value)
    )
    second_api.async_set_position = AsyncMock()
    second_api.async_close = AsyncMock()
    second_api.async_listen_notifications = rooms.async_listen_notifications
    first_entry = entry(hass)
    with patch(
        "custom_components.norman_gen1.gen2.NormanApiClient",
        side_effect=[rooms, second_api],
    ):
        assert await hass.config_entries.async_setup(first_entry.entry_id)
        await hass.async_block_till_done()
        second_entry = entry(hass, identity="second-hub")
        hass.config_entries.async_update_entry(
            second_entry, data={**second_entry.data, CONF_HOST: "192.0.2.21"}
        )
        assert await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    first_room = registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_room_1")
    second_room = registry.async_get_entity_id(
        "cover", DOMAIN, "gen2_second-hub_room_1"
    )
    assert first_room is not None
    assert second_room is not None
    assert first_room != second_room
    assert (
        registry.async_get(first_room).device_id
        != registry.async_get(second_room).device_id
    )
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": second_room}, blocking=True
    )
    rooms.async_set_position.assert_not_awaited()
    assert second_api.async_set_position.await_args_list == [
        call(7, 100, 80),
        call(8, 100, 30),
    ]
