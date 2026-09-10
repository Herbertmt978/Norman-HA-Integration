"""ShadeAuto integration, transport and control regression tests."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.diagnostics import async_get_config_entry_diagnostics
from custom_components.norman_gen1.gen2.api import (
    NormanApiClient,
    NormanApiError,
    NormanConnectionError,
    NormanPeriodicReconnectError,
)
from custom_components.norman_gen1.gen2.coordinator import NormanCoordinator
from custom_components.norman_gen1.gen2.cover import NormanBlind

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

DEVICES = {
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
                                "PeripheralUID": 7,
                                "PeripheralName": "Shade",
                                "ModuleType": 1,
                            }
                        ],
                    }
                ],
            }
        ]
    }
}
STATUS = {
    "Peripherals": [
        {"PeripheralUID": 7, "BottomRailPosition": 35, "MiddleRailPosition": 65}
    ]
}


@pytest.fixture
def shadeauto():
    """Provide a protocol-shaped ShadeAuto hub with a cancellable listener."""
    api = MagicMock(spec=NormanApiClient)
    api.thing_name = "shade-hub"
    api.async_validate_connection = AsyncMock(return_value=True)
    api.async_get_devices = AsyncMock(return_value=deepcopy(DEVICES))
    api.async_get_status = AsyncMock(return_value=deepcopy(STATUS))
    api.async_close = AsyncMock()
    api.async_set_position = AsyncMock()

    async def notifications():
        await asyncio.Event().wait()
        yield {}

    api.async_listen_notifications = notifications
    with (
        patch(
            "custom_components.norman_gen1.gen2.api.NormanApiClient", return_value=api
        ),
        patch("custom_components.norman_gen1.gen2.NormanApiClient", return_value=api),
    ):
        yield api


def entry(hass, identity="shade-hub"):
    """Create a saved Gen 2 entry."""
    result = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"gen2_{identity}",
        version=2,
        data={"generation": "gen2", CONF_HOST: "192.0.2.20"},
    )
    result.add_to_hass(hass)
    return result


async def test_generation_choice_and_gen2_setup(hass, shadeauto):
    """The real flow offers both generations and creates a working ShadeAuto cover."""
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert flow["step_id"] == "user"
    assert flow["data_schema"]({}) == {"generation": "gen1"}
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"generation": "gen2"}
    )
    assert flow["step_id"] == "gen2"
    assert list(flow["data_schema"].schema) == [CONF_HOST]
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_HOST: "http://192.0.2.20/"}
    )
    await hass.async_block_till_done()
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    configured = flow["result"]
    assert configured.data == {CONF_HOST: "192.0.2.20", "generation": "gen2"}
    assert configured.state is ConfigEntryState.LOADED
    entity_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_7"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["current_position"] == 35
    assert state.attributes["current_tilt_position"] == 65
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": entity_id, "position": 45},
        blocking=True,
    )
    shadeauto.async_set_position.assert_awaited_with(7, 45, 65)
    await hass.services.async_call(
        DOMAIN,
        "nudge_tilt",
        {"entity_id": entity_id, "step": 10},
        blocking=True,
    )
    shadeauto.async_set_position.assert_awaited_with(7, 45, 75)
    options = await hass.config_entries.options.async_init(configured.entry_id)
    assert options["reason"] == "no_gen2_options"

    assert await async_get_config_entry_diagnostics(hass, configured) == {
        "generation": "gen2",
        "device_count": 1,
        "connected": True,
    }
    assert await hass.config_entries.async_unload(configured.entry_id)
    await hass.async_block_till_done()
    assert shadeauto.async_close.await_count == 2


@pytest.mark.parametrize(
    ("method", "kwargs", "expected"),
    [
        ("async_open_cover", {}, (7, 100, 65)),
        ("async_close_cover", {}, (7, 0, 65)),
        ("async_set_cover_position", {"position": 40}, (7, 40, 65)),
        ("async_open_cover_tilt", {}, (7, 35, 100)),
        ("async_close_cover_tilt", {}, (7, 35, 0)),
        ("async_set_cover_tilt_position", {"tilt_position": 40}, (7, 35, 40)),
        ("async_nudge_position", {"step": -50}, (7, 0, 65)),
        ("async_nudge_tilt", {"step": 50}, (7, 35, 100)),
    ],
)
async def test_controls_preserve_other_rail(hass, shadeauto, method, kwargs, expected):
    """Every supported action preserves the other rail without state-attribute side effects."""
    configured = entry(hass)
    coordinator = NormanCoordinator(hass, shadeauto, configured)
    await coordinator.async_refresh()
    coordinator.async_request_refresh = AsyncMock()
    blind = NormanBlind(coordinator, 7, configured)
    await getattr(blind, method)(**kwargs)
    shadeauto.async_set_position.assert_awaited_once_with(*expected)
    await coordinator.async_shutdown()


async def test_unknown_position_and_failed_commands(hass, shadeauto):
    """Unknown rails prevent unintended movement; rejected commands preserve targets."""
    configured = entry(hass)
    coordinator = NormanCoordinator(hass, shadeauto, configured)
    await coordinator.async_refresh()
    blind = NormanBlind(coordinator, 7, configured)
    data = coordinator.data[7]
    data.middle_rail_position = None
    with pytest.raises(HomeAssistantError, match="unknown rail"):
        await blind.async_open_cover()
    with pytest.raises(HomeAssistantError, match="unknown"):
        await blind.async_nudge_tilt(1)
    shadeauto.async_set_position.assert_not_called()
    data.middle_rail_position = 65
    shadeauto.async_set_position.side_effect = NormanConnectionError
    with pytest.raises(HomeAssistantError, match="Failed"):
        await blind.async_open_cover()
    assert data.target_bottom_rail_position is None
    coordinator.data = {}
    assert not blind.available
    assert blind.is_closed is None
    assert blind.current_cover_tilt_position is None
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await blind.async_open_cover()
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await blind.async_nudge_position(1)
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NormanConnectionError(), "cannot_connect"),
        (NormanApiError(), "invalid_response"),
    ],
)
async def test_gen2_flow_failure(hass, shadeauto, error, expected):
    """Expected transport errors return forms and close validation resources."""
    shadeauto.async_validate_connection.side_effect = error
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"generation": "gen2"}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_HOST: "192.0.2.20"}
    )
    assert result["errors"] == {"base": expected}
    shadeauto.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    "host", ["", "http://user:pass@host/", "host:9999", "http://host/path"]
)
async def test_gen2_invalid_host(hass, host):
    """Reject addresses that cannot represent the fixed-port local hub."""
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"generation": "gen2"}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_HOST: host}
    )
    assert result["errors"] == {"base": "invalid_host"}


async def test_gen2_identity_reconfiguration(hass, shadeauto):
    """Reconfiguration preserves the generation and rejects a different hub."""
    configured = entry(hass)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": configured.entry_id}
    )
    shadeauto.thing_name = "other"
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_HOST: "192.0.2.21"}
    )
    assert result["reason"] == "wrong_hub"
    shadeauto.thing_name = "shade-hub"
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": configured.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_HOST: "192.0.2.21"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert configured.data == {CONF_HOST: "192.0.2.21", "generation": "gen2"}
    await hass.config_entries.async_unload(configured.entry_id)


async def test_duplicate_gen2_hub(hass, shadeauto):
    """The stable registration identity prevents duplicates at another address."""
    entry(hass)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"generation": "gen2"}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_HOST: "192.0.2.21"}
    )
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("error", [NormanConnectionError(), NormanApiError()])
async def test_setup_retry_cleans_up(hass, shadeauto, error):
    """A hub that disappears after configuration enters retry, not a broken loaded state."""
    configured = entry(hass)
    shadeauto.async_validate_connection.side_effect = error
    assert not await hass.config_entries.async_setup(configured.entry_id)
    assert configured.state is ConfigEntryState.SETUP_RETRY
    shadeauto.async_close.assert_awaited_once()


async def test_wrong_hub_startup_cleans_up(hass, shadeauto):
    """A reused IP cannot silently control a replacement hub."""
    configured = entry(hass, identity="different")
    assert not await hass.config_entries.async_setup(configured.entry_id)
    assert configured.state is ConfigEntryState.SETUP_ERROR
    shadeauto.async_close.assert_awaited_once()


def response_session(payload):
    """Make an aiohttp-shaped context manager that records response release."""
    response = MagicMock()
    response.json = AsyncMock(return_value=payload)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = context
    session.close = AsyncMock()
    return session, context, response


@pytest.mark.parametrize(
    ("method", "payload", "endpoint"),
    [
        ("async_validate_connection", {"ThingName": "hub"}, "registration"),
        (
            "async_get_devices",
            {"status": {"code": 0}, "results": {}},
            "GetAllPeripheral",
        ),
        ("async_get_status", {"Peripherals": []}, "status"),
    ],
)
async def test_api_endpoints_and_response_release(method, payload, endpoint):
    """The imported protocol posts to keito's original local API endpoints."""
    session, context, _ = response_session(payload)
    api = NormanApiClient("192.0.2.20", session)
    session.post.return_value.__aenter__.return_value.json.return_value = {
        "ThingName": "hub",
        **payload,
    }
    await api.async_validate_connection()
    context.__aexit__.reset_mock()
    await getattr(api, method)()
    assert session.post.call_args.args[0] == f"http://192.0.2.20:10123/NM/v1/{endpoint}"
    context.__aexit__.assert_awaited_once()
    await api.async_close()
    session.close.assert_not_called()


@pytest.mark.parametrize("payload", [[], {}, {"Error": 5}, {"ThingName": 4}])
async def test_invalid_registration(payload):
    """Bad responses cannot create a seemingly valid hub entry."""
    session, context, _ = response_session(payload)
    api = NormanApiClient("host", session)
    with pytest.raises(NormanApiError):
        await api.async_validate_connection()
    context.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("error", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_transport_failure_normalized(error):
    """Timeouts are connection failures usable by HA setup/retry handling."""
    session, context, _ = response_session({})
    context.__aenter__.side_effect = error
    api = NormanApiClient("host", session)
    with pytest.raises(NormanConnectionError):
        await api.async_get_status()


async def test_control_payload():
    """The API sends both requested rails and the exact peripheral UID."""
    session, context, _ = response_session({"Error": 0})
    api = NormanApiClient("host", session)
    await api.async_set_position(7, 35, 65)
    payload = session.post.call_args.kwargs["json"]
    assert {
        k: payload[k]
        for k in ("PeripheralUID", "BottomRailPosition", "MiddleRailPosition")
    } == {"PeripheralUID": 7, "BottomRailPosition": 35, "MiddleRailPosition": 65}
    context.__aexit__.assert_awaited_once()


async def test_notification_json_framing():
    """Braces inside strings, adjacent objects and split Unicode survive streaming."""
    session, context, response = response_session({})
    raw = '{"Ack":0}{"PeripheralList":[],"Name":"café } {"}'.encode()
    cut = raw.index(b"\xc3") + 1
    response.content.read = AsyncMock(side_effect=[raw[:cut], raw[cut:], b""])
    api = NormanApiClient("host", session)
    results = [item async for item in api.async_listen_notifications()]
    assert results == [{"PeripheralList": [], "Name": "café } {"}]
    response.close.assert_called_once()
    context.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("failure", [NormanConnectionError(), NormanApiError(), None])
async def test_coordinator_refresh_failure(hass, shadeauto, failure):
    """Transport and malformed payloads mark the coordinator unavailable."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    if failure:
        shadeauto.async_get_status.side_effect = failure
    else:
        shadeauto.async_get_status.return_value = {"Peripherals": {}}
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    await coordinator.async_shutdown()


async def test_sparse_discovery_and_status(hass, shadeauto):
    """Invalid IDs are skipped and status-only devices can still be controlled."""
    shadeauto.async_get_devices.return_value = {
        "results": {
            "RoomList": [
                {
                    "GroupList": [
                        {
                            "PeripheralList": [
                                {},
                                {"PeripheralUID": "bad"},
                                {"PeripheralUID": 7},
                            ]
                        }
                    ]
                }
            ]
        }
    }
    shadeauto.async_get_status.return_value = {
        "Peripherals": [
            {},
            {"PeripheralUID": "bad"},
            {"PeripheralUID": "8", "BottomRailPosition": 10, "MiddleRailPosition": 20},
        ]
    }
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    await coordinator.async_refresh()
    assert set(coordinator.data) == {7, 8}
    assert coordinator.data[8].bottom_rail_position == 10
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    "failure",
    [NormanConnectionError(), NormanApiError(), asyncio.CancelledError(), None],
)
async def test_notification_reconnect_and_cancellation(hass, shadeauto, failure):
    """Notifications refresh state and listener errors reconnect with bounded delay."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    coordinator.async_refresh = AsyncMock()

    async def notifications():
        yield {"PeripheralList": []}
        if failure is not None:
            raise failure

    shadeauto.async_listen_notifications = notifications
    with patch(
        "custom_components.norman_gen1.gen2.coordinator.asyncio.sleep",
        side_effect=[None, asyncio.CancelledError()],
    ):
        await coordinator.listen_notifications()
    assert coordinator.async_refresh.await_count >= 1
    await coordinator.async_shutdown()


async def test_periodic_notification_reconnect(hass, shadeauto):
    """An expected stream rotation reconnects immediately and remains cancellable."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    calls = 0

    async def notifications():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NormanPeriodicReconnectError
        raise asyncio.CancelledError
        yield {}

    shadeauto.async_listen_notifications = notifications
    await coordinator.listen_notifications()
    assert calls == 2
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    "method",
    [
        "async_validate_connection",
        "async_get_devices",
        "async_get_status",
        "async_set_position",
    ],
)
@pytest.mark.parametrize("failure", ["timeout", "json", "api"])
async def test_api_failure_classes(method, failure):
    """Each protocol operation maps response/transport failures to the HA-facing errors."""
    session, context, response = response_session({"ThingName": "hub"})
    api = NormanApiClient("host", session)
    await api.async_validate_connection()
    if failure == "timeout":
        context.__aenter__.side_effect = TimeoutError()
        expected = NormanConnectionError
    elif failure == "json":
        response.json.side_effect = json.JSONDecodeError("bad", "", 0)
        expected = NormanApiError
    else:
        response.json.return_value = {"Error": 1, "status": {"code": 1, "error": "bad"}}
        expected = NormanApiError
    with pytest.raises(expected):
        await getattr(api, method)(
            *((7, 30, 60) if method == "async_set_position" else ())
        )


async def test_device_discovery_registers_and_validates_status():
    """Discovery registers once if needed and rejects invalid API status metadata."""
    session, _, response = response_session({})
    response.json.side_effect = [{"ThingName": "hub"}, {"status": []}]
    api = NormanApiClient("host", session)
    with pytest.raises(NormanApiError, match="status"):
        await api.async_get_devices()
    assert api.thing_name == "hub"
    assert session.post.call_count == 2


async def test_owned_session_close():
    """Only a client-created session is closed by API cleanup."""
    session, _, _ = response_session({})
    with patch(
        "custom_components.norman_gen1.gen2.api.aiohttp.ClientSession",
        return_value=session,
    ):
        api = NormanApiClient("host")
    await api.async_close()
    session.close.assert_awaited_once()


@pytest.mark.parametrize("chunks", [[b"{", b""], [b" " * 1048577], [b"\xff"]])
async def test_invalid_notification_stream(chunks):
    """Incomplete, oversized and invalid UTF-8 streams fail without leaking responses."""
    session, context, response = response_session({})
    response.content.read = AsyncMock(side_effect=chunks)
    api = NormanApiClient("host", session)
    with pytest.raises((NormanApiError, NormanConnectionError)):
        _ = [item async for item in api.async_listen_notifications()]
    context.__aexit__.assert_awaited_once()
    response.close.assert_called_once()


async def test_notification_rotation_and_connection_failure():
    """The maximum stream lifetime bounds idle connections and HTTP errors are normalized."""
    session, context, response = response_session({})
    api = NormanApiClient("host", session)

    async def blocked_read(size):
        await asyncio.Event().wait()

    response.content.read = blocked_read
    with (
        patch("custom_components.norman_gen1.gen2.api.NOTIF_MAX_DURATION", 0),
        pytest.raises(NormanPeriodicReconnectError),
    ):
        _ = [item async for item in api.async_listen_notifications()]
    response.close.assert_called_once()
    context.__aenter__.side_effect = aiohttp.ClientConnectionError()
    with pytest.raises(NormanConnectionError):
        _ = [item async for item in api.async_listen_notifications()]


async def test_delayed_status_preserves_commands_and_nudges(hass, shadeauto):
    """Stale reads cannot undo a just-accepted rail target or repeated nudge."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    await coordinator.async_refresh()
    await coordinator.async_set_position(7, bottom=45, middle=None)
    await coordinator.async_set_position(7, bottom=None, middle=75)
    await coordinator.async_set_position(7, bottom=5, middle=None, nudge=True)
    assert [call.args for call in shadeauto.async_set_position.await_args_list] == [
        (7, 45, 65),
        (7, 45, 75),
        (7, 50, 75),
    ]
    # Current position stays physical; only the command target is optimistic.
    assert coordinator.data[7].bottom_rail_position == 35
    assert coordinator.target_position(7, "bottom") == 50
    await coordinator.async_shutdown()


@pytest.mark.parametrize("acknowledgement", ["current", "target", "expiry"])
async def test_pending_target_relinquishes_to_hub(hass, shadeauto, acknowledgement):
    """Acknowledgement or a bounded timeout restores hub ownership of targets."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    await coordinator.async_refresh()
    with patch(
        "custom_components.norman_gen1.gen2.coordinator.monotonic", return_value=10
    ):
        await coordinator.async_set_position(7, bottom=45, middle=None)
    if acknowledgement != "expiry":
        field = (
            "BottomRailPosition"
            if acknowledgement == "current"
            else "TargetBottomRailPosition"
        )
        shadeauto.async_get_status.return_value["Peripherals"][0][field] = 45
        with patch(
            "custom_components.norman_gen1.gen2.coordinator.monotonic", return_value=11
        ):
            await coordinator.async_refresh()
        # A later app command is authoritative once our command is acknowledged.
        shadeauto.async_get_status.return_value["Peripherals"][0].update(
            BottomRailPosition=20, TargetBottomRailPosition=25
        )
    with patch(
        "custom_components.norman_gen1.gen2.coordinator.monotonic",
        return_value=41 if acknowledgement == "expiry" else 12,
    ):
        await coordinator.async_refresh()
        await coordinator.async_set_position(7, bottom=None, middle=75)
    expected = 35 if acknowledgement == "expiry" else 25
    shadeauto.async_set_position.assert_awaited_with(7, expected, 75)
    await coordinator.async_shutdown()


async def test_failed_command_does_not_replace_pending_target(hass, shadeauto):
    """A rejected command leaves the last accepted target intact."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    await coordinator.async_refresh()
    await coordinator.async_set_position(7, bottom=45, middle=None)
    shadeauto.async_set_position.side_effect = NormanConnectionError()
    with pytest.raises(NormanConnectionError):
        await coordinator.async_set_position(7, bottom=90, middle=None)
    assert coordinator.target_position(7, "bottom") == 45
    await coordinator.async_shutdown()


async def test_concurrent_commands_are_serialized(hass, shadeauto):
    """A second command resolves its preserved rail after the first is accepted."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    await coordinator.async_refresh()
    started, release = asyncio.Event(), asyncio.Event()

    async def control(*args):
        started.set()
        await release.wait()

    shadeauto.async_set_position.side_effect = control
    first = asyncio.create_task(
        coordinator.async_set_position(7, bottom=45, middle=None)
    )
    await started.wait()
    second = asyncio.create_task(
        coordinator.async_set_position(7, bottom=None, middle=75)
    )
    release.set()
    await asyncio.gather(first, second)
    assert [call.args for call in shadeauto.async_set_position.await_args_list] == [
        (7, 45, 65),
        (7, 45, 75),
    ]
    await coordinator.async_shutdown()


@pytest.mark.parametrize("value", ["35", True, 35.5, -1, 101, [], {}])
@pytest.mark.parametrize(
    "field",
    [
        "BottomRailPosition",
        "MiddleRailPosition",
        "TargetBottomRailPosition",
        "TargetMiddleRailPosition",
    ],
)
async def test_invalid_rail_status_blocks_controls(hass, shadeauto, field, value):
    """Every rail field is validated before entering entity state or a control request."""
    coordinator = NormanCoordinator(hass, shadeauto, entry(hass))
    await coordinator.async_refresh()
    shadeauto.async_get_status.return_value["Peripherals"][0][field] = value
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await coordinator.async_set_position(7, bottom=None, middle=75)
    shadeauto.async_set_position.assert_not_called()
    await coordinator.async_shutdown()


@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ServerTimeoutError()])
async def test_notification_transport_timeout_is_not_rotation(failure):
    """Connection timeouts take the delayed reconnect path, not periodic rotation."""
    session, context, _ = response_session({})
    context.__aenter__.side_effect = failure
    api = NormanApiClient("host", session)
    with pytest.raises(NormanConnectionError, match="timed out"):
        _ = [item async for item in api.async_listen_notifications()]
