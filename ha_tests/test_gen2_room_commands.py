"""ShadeAuto room command preflight, serialisation and failure regression tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.norman_gen1.gen2.api import (
    NormanApiClient,
    NormanApiError,
    NormanConnectionError,
)
from custom_components.norman_gen1.gen2.coordinator import NormanCoordinator

from .test_gen2 import entry

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def room_coordinator(hass):
    """Provide three room members and an unrelated shade with real refreshes."""
    api = MagicMock(spec=NormanApiClient)
    api.async_get_devices = AsyncMock(
        return_value={
            "results": {
                "RoomList": [
                    {
                        "RoomID": room_id,
                        "GroupList": [
                            {
                                "PeripheralList": [
                                    {"PeripheralUID": device_id}
                                    for device_id in device_ids
                                ]
                            }
                        ],
                    }
                    for room_id, device_ids in ((1, (7, 8, 9)), (2, (10,)))
                ]
            }
        }
    )
    api.async_get_status = AsyncMock(
        return_value={
            "Peripherals": [
                {
                    "PeripheralUID": device_id,
                    "BottomRailPosition": bottom,
                    "MiddleRailPosition": middle,
                }
                for device_id, bottom, middle in (
                    (7, 35, 65),
                    (8, 20, 80),
                    (9, 90, 10),
                    (10, 50, 50),
                )
            ]
        }
    )
    api.async_set_position = AsyncMock()
    coordinator = NormanCoordinator(hass, api, entry(hass))
    await coordinator.async_refresh()
    with patch.object(
        coordinator, "async_request_refresh", side_effect=coordinator.async_refresh
    ):
        yield coordinator
    await coordinator.async_shutdown()


async def test_room_preserves_each_child_rail_and_targets(room_coordinator):
    """Only members move, retaining per-child pending rails across stale status."""
    coordinator = room_coordinator
    await coordinator.async_set_position(8, bottom=None, middle=75)
    coordinator.api.async_set_position.reset_mock()
    await coordinator.async_set_room_position(1, bottom=45, middle=None)
    assert [
        call.args for call in coordinator.api.async_set_position.await_args_list
    ] == [
        (7, 45, 65),
        (8, 45, 75),
        (9, 45, 10),
    ]
    assert [
        coordinator.target_position(device_id, "bottom") for device_id in (7, 8, 9)
    ] == [
        45,
        45,
        45,
    ]
    assert coordinator.target_position(8, "middle") == 75
    assert coordinator.target_position(7, "middle") is None
    assert coordinator.target_position(10, "bottom") is None
    assert coordinator.data[7].bottom_rail_position == 35


@pytest.mark.parametrize("unknown_rail", ["BottomRailPosition", "MiddleRailPosition"])
async def test_room_preflight_prevents_partial_moves(room_coordinator, unknown_rail):
    """An unknown preserved or nudged rail on the last child blocks all commands."""
    coordinator = room_coordinator
    coordinator.api.async_get_status.return_value["Peripherals"][2][unknown_rail] = None
    await coordinator.async_refresh()
    with pytest.raises(HomeAssistantError, match="unknown"):
        await coordinator.async_set_room_position(1, bottom=5, middle=None, nudge=True)
    coordinator.api.async_set_position.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_room_member_missing_from_status_blocks_commands(room_coordinator):
    """A discovered member absent from status still participates in preflight."""
    coordinator = room_coordinator
    status = coordinator.api.async_get_status.return_value
    status["Peripherals"] = [
        item for item in status["Peripherals"] if item["PeripheralUID"] != 9
    ]
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data[9].room_id == 1
    assert coordinator.data[9].middle_rail_position is None
    with pytest.raises(HomeAssistantError, match="Cannot preserve an unknown rail"):
        await coordinator.async_set_room_position(1, bottom=45, middle=None)
    coordinator.api.async_set_position.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("failure", [NormanConnectionError(), NormanApiError()])
@pytest.mark.parametrize("failed_child", [0, 1])
async def test_room_api_failure_stops_and_refreshes(
    room_coordinator, failure, failed_child
):
    """A failed child is not retried and refresh runs after releasing the lock."""
    coordinator = room_coordinator
    coordinator.api.async_set_position.side_effect = [None] * failed_child + [failure]
    with pytest.raises(HomeAssistantError, match="some shades may have moved") as error:
        await asyncio.wait_for(
            coordinator.async_set_room_position(1, bottom=45, middle=None), timeout=1
        )
    assert error.value.__cause__ is failure
    assert coordinator.api.async_set_position.await_count == failed_child + 1
    coordinator.async_request_refresh.assert_awaited_once()
    assert coordinator.api.async_get_status.await_count == 2
    assert coordinator.target_position(7, "bottom") == (45 if failed_child else None)
    assert coordinator.target_position(8, "bottom") is None
    assert coordinator.target_position(9, "bottom") is None


async def test_room_failure_preserves_previously_accepted_target(room_coordinator):
    """An uncertain later command does not replace a child's accepted target."""
    coordinator = room_coordinator
    await coordinator.async_set_position(8, bottom=40, middle=None)
    coordinator.api.async_set_position.side_effect = [None, NormanConnectionError()]
    with pytest.raises(HomeAssistantError, match="uncertain"):
        await coordinator.async_set_room_position(1, bottom=45, middle=None)
    assert coordinator.target_position(7, "bottom") == 45
    assert coordinator.target_position(8, "bottom") == 40


async def test_room_command_error_survives_failed_status_refresh(room_coordinator):
    """Failed recovery marks data unavailable without hiding the partial-move warning."""
    coordinator = room_coordinator
    command_error = NormanApiError("Control rejected")
    coordinator.api.async_set_position.side_effect = [None, command_error]
    coordinator.api.async_get_status.side_effect = NormanConnectionError("Hub offline")
    with pytest.raises(HomeAssistantError, match="some shades may have moved") as error:
        await coordinator.async_set_room_position(1, bottom=45, middle=None)
    assert error.value.__cause__ is command_error
    assert "movement is uncertain" in str(error.value)
    assert coordinator.api.async_set_position.await_count == 2
    coordinator.async_request_refresh.assert_awaited_once()
    assert coordinator.api.async_get_status.await_count == 2
    assert not coordinator.last_update_success
    assert coordinator.target_position(7, "bottom") == 45
    assert coordinator.target_position(8, "bottom") is None
    assert coordinator.target_position(9, "bottom") is None


async def test_room_with_no_members_is_rejected(room_coordinator):
    """An empty room cannot issue a command or request an unnecessary refresh."""
    with pytest.raises(HomeAssistantError, match="no available members"):
        await room_coordinator.async_set_room_position(3, bottom=45, middle=None)
    room_coordinator.api.async_set_position.assert_not_awaited()
    room_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("room_id", [True, "1", 1.0, None])
async def test_room_rejects_non_integer_identity(room_coordinator, room_id):
    """A malformed room identity must not alias a real integer room."""
    with pytest.raises(HomeAssistantError, match="room ID must be an integer"):
        await room_coordinator.async_set_room_position(room_id, bottom=45, middle=None)
    room_coordinator.api.async_set_position.assert_not_awaited()


async def test_room_membership_is_resolved_for_each_command(room_coordinator):
    """Moved members and malformed room IDs cannot leak into the old room command."""
    coordinator = room_coordinator
    coordinator.data[8].room_id = 2
    coordinator.data[9].room_id = True
    await coordinator.async_set_room_position(1, bottom=45, middle=None)
    coordinator.api.async_set_position.assert_awaited_once_with(7, 45, 65)


async def test_unavailable_room_rejects_all_commands(room_coordinator):
    """A failed coordinator cannot act on stale room membership or positions."""
    coordinator = room_coordinator
    coordinator.api.async_get_status.side_effect = NormanConnectionError()
    await coordinator.async_refresh()
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await coordinator.async_set_room_position(1, bottom=45, middle=None)
    coordinator.api.async_set_position.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_room_nudges_use_child_targets_and_clamp(room_coordinator):
    """Repeated room nudges are relative to each child and bounded to valid rails."""
    coordinator = room_coordinator
    await coordinator.async_set_room_position(1, bottom=15, middle=-15, nudge=True)
    await coordinator.async_set_room_position(1, bottom=15, middle=-15, nudge=True)
    assert [
        call.args for call in coordinator.api.async_set_position.await_args_list
    ] == [
        (7, 50, 50),
        (8, 35, 65),
        (9, 100, 0),
        (7, 65, 35),
        (8, 50, 50),
        (9, 100, 0),
    ]


async def test_room_and_individual_commands_share_lock(room_coordinator):
    """An individual command cannot interleave with a room's child commands."""
    coordinator = room_coordinator
    started, release = asyncio.Event(), asyncio.Event()

    async def control(*args):
        started.set()
        await release.wait()

    coordinator.api.async_set_position.side_effect = control
    room_task = asyncio.create_task(
        coordinator.async_set_room_position(1, bottom=45, middle=None)
    )
    await started.wait()
    individual_task = asyncio.create_task(
        coordinator.async_set_position(7, bottom=None, middle=75)
    )
    release.set()
    await asyncio.wait_for(asyncio.gather(room_task, individual_task), timeout=1)
    assert [
        call.args for call in coordinator.api.async_set_position.await_args_list
    ] == [
        (7, 45, 65),
        (8, 45, 80),
        (9, 45, 10),
        (7, 45, 75),
    ]


async def test_room_cancellation_refreshes_attempted_command(room_coordinator):
    """Cancellation leaves no accepted target but still refreshes uncertain movement."""
    coordinator = room_coordinator
    coordinator.api.async_set_position.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await coordinator.async_set_room_position(1, bottom=45, middle=None)
    coordinator.api.async_set_position.assert_awaited_once_with(7, 45, 65)
    coordinator.async_request_refresh.assert_awaited_once()
    assert coordinator.target_position(7, "bottom") is None


async def test_room_publishes_targets_before_debounced_refresh(room_coordinator):
    """Listeners see accepted targets immediately without optimistic positions."""
    coordinator = room_coordinator
    snapshots = []

    def capture_state():
        snapshots.append(
            (
                coordinator.target_position(7, "middle"),
                coordinator.data[7].middle_rail_position,
            )
        )

    async def deferred_refresh():
        assert snapshots == [(75, 65)]

    coordinator.async_request_refresh.side_effect = deferred_refresh
    remove_listener = coordinator.async_add_listener(capture_state)
    try:
        await coordinator.async_set_room_position(1, bottom=None, middle=75)
        assert snapshots == [(75, 65)]
        coordinator.async_request_refresh.assert_awaited_once()
    finally:
        remove_listener()
