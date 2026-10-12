"""Exercise Gen 2 control pacing through HTTP and Home Assistant services."""

from __future__ import annotations

import asyncio
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.gen2 import api as api_module
from custom_components.norman_gen1.gen2.api import NormanApiClient, NormanApiError

from . import test_gen2

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

DEVICE_POSITIONS = {7: (20, 80), 8: (70, 30), 9: (90, 10)}


class FakeClock:
    """Control only the API module's clocks and control-slot sleep."""

    def __init__(self) -> None:
        """Start with a fixed wall clock and controllable monotonic time."""
        self.wall = 1_700_000_000.25
        self.ticks = 100.0
        self.block_sleep = False
        self.sleep_started = asyncio.Event()
        self.release_sleep = asyncio.Event()

    def time(self) -> float:
        """Return the fixed wall clock value."""
        return self.wall

    def monotonic(self) -> float:
        """Return monotonic time advanced by fake sleeps."""
        return self.ticks

    async def sleep(self, delay: float) -> None:
        """Advance the fake clock, optionally pausing for cancellation tests."""
        if self.block_sleep:
            self.sleep_started.set()
            await self.release_sleep.wait()
        self.ticks += delay
        await asyncio.sleep(0)


@pytest.fixture
async def shadeauto_transport(hass, socket_enabled):
    """Run the real client against a local HTTP hub with controllable time."""
    clock = FakeClock()
    controls: list[tuple[float, dict]] = []
    single_rail_ids: set[int] = set()
    requests: list[str] = []
    control_responses: list[dict] = []
    control_started = asyncio.Event()
    release_control = asyncio.Event()
    control_state = {"block_next": False}
    app = web.Application()

    async def registration(_request: web.Request) -> web.Response:
        return web.json_response({"Error": 0, "ThingName": "shade-hub"})

    async def devices(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": {"code": 0},
                "results": {
                    "RoomList": [
                        {
                            "RoomID": 1,
                            "RoomName": "Living room",
                            "GroupList": [
                                {
                                    "GroupID": 1,
                                    "PeripheralList": [
                                        {
                                            "PeripheralUID": device_id,
                                            "PeripheralName": f"Shade {device_id}",
                                            "ModuleType": 1,
                                        }
                                        for device_id in DEVICE_POSITIONS
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        )

    async def status(_request: web.Request) -> web.Response:
        requests.append("status")
        return web.json_response(
            {
                "Error": 0,
                "Peripherals": [
                    {
                        "PeripheralUID": device_id,
                        "BottomRailPosition": bottom,
                        **(
                            {"MiddleRailPosition": middle}
                            if device_id not in single_rail_ids
                            else {}
                        ),
                    }
                    for device_id, (bottom, middle) in DEVICE_POSITIONS.items()
                ],
            }
        )

    async def control(request: web.Request) -> web.Response:
        requests.append("control")
        controls.append((clock.monotonic(), await request.json()))
        if control_state["block_next"]:
            control_state["block_next"] = False
            control_started.set()
            await release_control.wait()
        response = control_responses.pop(0) if control_responses else {"Error": 0}
        return web.json_response(response)

    async def notification(_request: web.Request) -> web.Response:
        # Keep the integration's listener open until the test unloads its entry.
        await asyncio.Event().wait()
        return web.json_response({})

    app.router.add_post("/NM/v1/registration", registration)
    app.router.add_post("/NM/v1/GetAllPeripheral", devices)
    app.router.add_post("/NM/v1/status", status)
    app.router.add_post("/NM/v1/control", control)
    app.router.add_post("/NM/v1/notification", notification)
    server = TestServer(app, shutdown_timeout=0.1)
    await server.start_server()
    base_url = str(server.make_url("/")).rstrip("/")

    # Replace the API module's references only. HA and aiohttp keep real time
    # and their real asyncio module, while the client's lock remains genuine.
    async_facade = SimpleNamespace(
        Lock=asyncio.Lock,
        sleep=clock.sleep,
        timeout=asyncio.timeout,
        CancelledError=asyncio.CancelledError,
    )
    with (
        patch.object(api_module, "time", clock),
        patch.object(api_module, "asyncio", async_facade),
    ):
        try:
            yield SimpleNamespace(
                clock=clock,
                controls=controls,
                single_rail_ids=single_rail_ids,
                requests=requests,
                control_responses=control_responses,
                control_started=control_started,
                release_control=release_control,
                block_control=lambda: control_state.__setitem__("block_next", True),
                base_url=base_url,
                make_client=lambda: _make_client(hass, base_url),
                client_factory=lambda host, session: _make_client(
                    hass, base_url, session=session
                ),
            )
        finally:
            release_control.set()
            await server.close()


def _make_client(hass, base_url: str, *, session=None) -> NormanApiClient:
    """Create a real client using the test server as its transport endpoint."""
    if session is None:
        session = async_get_clientsession(hass)
    client = NormanApiClient("shadeauto.test", session)
    client.base_url = base_url
    return client


@pytest.mark.parametrize("target", ["cover_group", "room_cover"])
@pytest.mark.parametrize("single_rail", [False, True])
async def test_cover_service_paths_space_real_control_requests(
    hass, shadeauto_transport, target, single_rail
):
    """Three real covers and the Norman room cover share spaced HTTP controls."""
    transport = shadeauto_transport
    if single_rail:
        transport.single_rail_ids.add(9)
    config_entry = test_gen2.entry(hass)
    group_entry = None
    try:
        with patch(
            "custom_components.norman_gen1.gen2.NormanApiClient",
            side_effect=transport.client_factory,
        ):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        if target == "cover_group":
            entity_ids = [
                registry.async_get_entity_id(
                    "cover", DOMAIN, f"gen2_shade-hub_{device_id}"
                )
                for device_id in DEVICE_POSITIONS
            ]
            assert all(entity_ids)
            group_entry = MockConfigEntry(
                domain="group",
                title="Test shade group",
                data={},
                options={
                    "group_type": "cover",
                    "name": "Test shade group",
                    "entities": entity_ids,
                    "hide_members": False,
                },
            )
            group_entry.add_to_hass(hass)
            assert await hass.config_entries.async_setup(group_entry.entry_id)
            await hass.async_block_till_done()
            group_entity_id = "cover.test_shade_group"
            assert hass.states.get(group_entity_id) is not None
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": group_entity_id, "position": 42},
                blocking=True,
            )
        else:
            room_entity_id = registry.async_get_entity_id(
                "cover", DOMAIN, "gen2_shade-hub_room_1"
            )
            assert room_entity_id is not None
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": room_entity_id, "position": 42},
                blocking=True,
            )

        assert len(transport.controls) == 3
        first_control = transport.requests.index("control")
        assert transport.requests[first_control:] == ["control"] * 3
        send_times = [sent_at for sent_at, _payload in transport.controls]
        assert all(later - earlier >= 0.75 for earlier, later in pairwise(send_times))
        payloads = [payload for _sent_at, payload in transport.controls]
        assert {payload["PeripheralUID"] for payload in payloads} == set(
            DEVICE_POSITIONS
        )
        assert {payload["ThingName"] for payload in payloads} == {"shade-hub"}
        assert {payload["BottomRailPosition"] for payload in payloads} == {42}
        assert {
            payload["PeripheralUID"]: payload["MiddleRailPosition"]
            for payload in payloads
            if "MiddleRailPosition" in payload
        } == {
            device_id: rails[1]
            for device_id, rails in DEVICE_POSITIONS.items()
            if device_id not in transport.single_rail_ids
        }
        timestamps = [payload["Timestamp"] for payload in payloads]
        task_ids = [payload["TaskID"] for payload in payloads]
        assert len(set(timestamps)) == len(timestamps)
        assert len(set(task_ids)) == len(task_ids)
        assert all(0 <= task_id <= 0x7FFFFFFF for task_id in task_ids)
    finally:
        if group_entry is not None and group_entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(group_entry.entry_id)
        if config_entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(config_entry.entry_id)
            await hass.async_block_till_done()


async def test_cancelled_waiting_control_does_not_post_or_reserve_ids(
    hass, shadeauto_transport
):
    """A cancelled spacing wait leaves the next real command able to proceed."""
    transport = shadeauto_transport
    client = transport.make_client()
    try:
        await client.async_set_position(7, 40, 60)
        transport.clock.block_sleep = True
        waiting = asyncio.create_task(client.async_set_position(8, 41, 59))
        await asyncio.wait_for(transport.clock.sleep_started.wait(), timeout=1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert len(transport.controls) == 1

        transport.clock.block_sleep = False
        await client.async_set_position(9, 42, 58)
        assert len(transport.controls) == 2
        first_time, first = transport.controls[0]
        second_time, second = transport.controls[1]
        assert second_time - first_time >= 0.75
        assert first["TaskID"] != second["TaskID"]
        assert first["Timestamp"] != second["Timestamp"]
    finally:
        await client.async_close()


async def test_failed_control_is_not_retried_and_still_spaces_next_request(
    hass, shadeauto_transport
):
    """A possibly delivered error consumes its slot and does not auto-retry."""
    transport = shadeauto_transport
    client = transport.make_client()
    transport.control_responses.append({"Error": 9})
    try:
        with pytest.raises(NormanApiError, match="error code: 9"):
            await client.async_set_position(7, 40, 60)
        await client.async_set_position(8, 41, 59)

        assert len(transport.controls) == 2
        assert transport.controls[1][0] - transport.controls[0][0] >= 0.75
        assert (
            transport.controls[0][1]["PeripheralUID"],
            transport.controls[1][1]["PeripheralUID"],
        ) == (7, 8)
        assert transport.controls[0][1]["TaskID"] != transport.controls[1][1]["TaskID"]
        assert (
            transport.controls[0][1]["Timestamp"]
            != transport.controls[1][1]["Timestamp"]
        )
    finally:
        await client.async_close()


async def test_concurrent_controls_keep_ids_unique_after_clock_rollback(
    hass, shadeauto_transport
):
    """Concurrent requests keep unique identifiers and spacing through clock rollback."""
    transport = shadeauto_transport
    client = transport.make_client()
    try:
        await client.async_set_position(7, 40, 60)
        transport.clock.wall -= 60
        await asyncio.gather(
            client.async_set_position(8, 41, 59),
            client.async_set_position(9, 42, 58),
        )

        assert len(transport.controls) == 3
        send_times = [sent_at for sent_at, _payload in transport.controls]
        assert all(later - earlier >= 0.75 for earlier, later in pairwise(send_times))
        payloads = [payload for _sent_at, payload in transport.controls]
        assert {payload["PeripheralUID"] for payload in payloads} == {7, 8, 9}
        assert len({payload["Timestamp"] for payload in payloads}) == 3
        assert len({payload["TaskID"] for payload in payloads}) == 3
    finally:
        await client.async_close()


async def test_frozen_clock_control_ids_increment_through_31_bit_wrap(
    hass, shadeauto_transport
):
    """Frozen wall time still produces distinct IDs across the 31-bit wrap."""
    transport = shadeauto_transport
    transport.clock.wall = 0x7FFFFFFF / 1000
    client = transport.make_client()
    try:
        for device_id in (7, 8, 9, 7):
            await client.async_set_position(device_id, 40, 60)

        task_ids = [payload["TaskID"] for _sent_at, payload in transport.controls]
        assert task_ids == [0x7FFFFFFF, 0, 1, 2]
        timestamps = [payload["Timestamp"] for _sent_at, payload in transport.controls]
        assert len(set(timestamps)) == 4
    finally:
        await client.async_close()


async def test_cancelled_control_after_server_receives_request_is_not_retried(
    hass, shadeauto_transport
):
    """An ambiguous cancelled POST is counted once and the next command is spaced."""
    transport = shadeauto_transport
    client = transport.make_client()
    transport.block_control()
    waiting = asyncio.create_task(client.async_set_position(7, 40, 60))
    try:
        await asyncio.wait_for(transport.control_started.wait(), timeout=1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

        transport.release_control.set()
        await client.async_set_position(8, 41, 59)

        assert len(transport.controls) == 2
        first_time, first = transport.controls[0]
        second_time, second = transport.controls[1]
        assert second_time - first_time >= 0.75
        assert first["TaskID"] != second["TaskID"]
        assert first["Timestamp"] != second["Timestamp"]
    finally:
        transport.release_control.set()
        if not waiting.done():
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        await client.async_close()
