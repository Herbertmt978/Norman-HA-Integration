"""ShadeAuto coordinator scheduling and change-notification regressions."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.gen2 import coordinator as coordinator_module
from custom_components.norman_gen1.gen2.api import (
    NormanApiClient,
    NormanConnectionError,
)
from custom_components.norman_gen1.gen2.coordinator import NormanCoordinator

from . import test_gen2_transport
from .test_gen2 import entry

shadeauto_transport = test_gen2_transport.shadeauto_transport

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def refresh_coordinator(hass):
    """Provide a coordinator with room members and usable middle rails."""
    api = MagicMock(spec=NormanApiClient)
    status_call_events = asyncio.Queue()
    api.async_get_devices = AsyncMock(
        return_value={
            "results": {
                "RoomList": [
                    {
                        "RoomID": 1,
                        "GroupList": [
                            {
                                "PeripheralList": [
                                    {"PeripheralUID": device_id}
                                    for device_id in (7, 8, 9)
                                ]
                            }
                        ],
                    }
                ]
            }
        }
    )
    status_data = {
        "Peripherals": [
            {
                "PeripheralUID": device_id,
                "BottomRailPosition": 35,
                "MiddleRailPosition": 65,
            }
            for device_id in (7, 8, 9)
        ]
    }

    async def get_status():
        status_call_events.put_nowait(None)
        return status_data

    api.async_get_status = AsyncMock(side_effect=get_status)
    api.async_set_position = AsyncMock()
    api.async_listen_notifications = AsyncMock()
    coordinator = NormanCoordinator(hass, api, entry(hass))
    coordinator.status_call_events = status_call_events
    await coordinator.async_refresh()
    status_call_events.get_nowait()
    with patch.object(
        coordinator, "async_request_refresh", side_effect=coordinator.async_refresh
    ):
        yield coordinator
    await coordinator.async_shutdown()


async def wait_for_status_call(coordinator):
    """Wait for one status read without relying on wall-clock scheduling guesses."""
    await asyncio.wait_for(coordinator.status_call_events.get(), timeout=1)


async def test_commands_coalesce_and_get_a_settle_followup(
    refresh_coordinator, monkeypatch
):
    """A command burst refreshes after quiet, then checks once after settling."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_FOLLOWUP_REFRESH_DELAY", 0.06)

    await coordinator.async_set_position(7, bottom=40, middle=None)
    await coordinator.async_set_position(7, bottom=45, middle=None)
    assert coordinator.api.async_get_status.await_count == 1

    await wait_for_status_call(coordinator)
    assert coordinator.api.async_get_status.await_count == 2
    await wait_for_status_call(coordinator)
    assert coordinator.api.async_get_status.await_count == 3


async def test_room_controls_are_not_interleaved_with_status_refresh(
    refresh_coordinator, monkeypatch
):
    """A queued status refresh waits until every serial child control finishes."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    status_count_at_start = coordinator.api.async_get_status.await_count
    first_control_started = asyncio.Event()
    release_first_control = asyncio.Event()
    control_count = 0

    async def control(*_args):
        nonlocal control_count
        control_count += 1
        if control_count == 1:
            first_control_started.set()
            await release_first_control.wait()
        assert coordinator.api.async_get_status.await_count == status_count_at_start

    coordinator.api.async_set_position.side_effect = control
    room_task = asyncio.create_task(
        coordinator.async_set_room_position(1, bottom=45, middle=None)
    )
    await first_control_started.wait()
    refresh_task = asyncio.create_task(coordinator.async_refresh())
    await asyncio.sleep(0)
    release_first_control.set()
    await asyncio.gather(room_task, refresh_task)
    assert coordinator.api.async_set_position.await_count == 3
    assert coordinator.api.async_get_status.await_count == status_count_at_start + 1


async def test_failed_attempt_still_schedules_a_refresh(
    refresh_coordinator, monkeypatch
):
    """An attempted control with an uncertain outcome still triggers a reread."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_FOLLOWUP_REFRESH_DELAY", 0.06)
    coordinator.api.async_set_position.side_effect = NormanConnectionError(
        "hub connection lost"
    )

    with pytest.raises(NormanConnectionError):
        await coordinator.async_set_position(7, bottom=40, middle=None)
    assert coordinator.api.async_get_status.await_count == 1

    await wait_for_status_call(coordinator)
    assert coordinator.api.async_get_status.await_count == 2


async def test_notification_uses_debounce_without_resetting_command_followup(
    refresh_coordinator, monkeypatch
):
    """Notifications share the near-term debounce and preserve command follow-up."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_FOLLOWUP_REFRESH_DELAY", 0.10)
    await coordinator.async_set_position(7, bottom=40, middle=None)
    await wait_for_status_call(coordinator)

    notification = asyncio.Event()

    async def listen_once():
        yield {"status": "changed"}
        await notification.wait()

    coordinator.api.async_listen_notifications = listen_once
    listener_task = asyncio.create_task(coordinator.listen_notifications())
    try:
        await wait_for_status_call(coordinator)
        await wait_for_status_call(coordinator)
        assert coordinator.api.async_get_status.await_count == 4
    finally:
        listener_task.cancel()
        await asyncio.gather(listener_task, return_exceptions=True)


async def test_shutdown_cancels_deferred_refreshes(refresh_coordinator, monkeypatch):
    """Unload cancels both scheduled reads and leaves no late hub request."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.03)
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_FOLLOWUP_REFRESH_DELAY", 0.06)
    await coordinator.async_set_position(7, bottom=40, middle=None)

    await coordinator.async_shutdown()
    await asyncio.sleep(0.08)
    assert coordinator.api.async_get_status.await_count == 1


async def test_equal_data_suppresses_listener_but_failure_transition_notifies(
    refresh_coordinator,
):
    """Equal status is quiet while an availability transition still notifies."""
    coordinator = refresh_coordinator
    updates = []
    remove_listener = coordinator.async_add_listener(lambda: updates.append(True))
    try:
        await coordinator.async_refresh()
        assert updates == []

        coordinator.api.async_get_status.side_effect = NormanConnectionError(
            "hub unavailable"
        )
        await coordinator.async_refresh()
        assert updates == [True]
        assert not coordinator.last_update_success
    finally:
        remove_listener()


async def test_idle_poll_is_configured_without_unload_side_effects(refresh_coordinator):
    """The coordinator retains a conservative idle poll interval."""
    assert refresh_coordinator.update_interval == timedelta(seconds=60)


async def test_rejected_command_keeps_previous_verification_reads(
    refresh_coordinator, monkeypatch
):
    """A preflight failure cannot erase an earlier command's status follow-up."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    monkeypatch.setattr(coordinator_module, "COMMAND_FOLLOWUP_REFRESH_DELAY", 0.06)
    await coordinator.async_set_position(7, bottom=40, middle=None)
    coordinator.data[8].middle_rail_position = None
    with pytest.raises(HomeAssistantError, match="unknown rail"):
        await coordinator.async_set_position(8, bottom=40, middle=None)
    await wait_for_status_call(coordinator)
    await wait_for_status_call(coordinator)
    assert coordinator.api.async_get_status.await_count == 3
    assert coordinator.api.async_set_position.await_count == 1


async def _setup_http_entry(hass, transport):
    """Load the actual integration against the local HTTP hub."""
    configured = MockConfigEntry(
        domain=DOMAIN,
        unique_id="gen2_shade-hub",
        version=2,
        data={"generation": "gen2", "host": "shadeauto.test"},
    )
    configured.add_to_hass(hass)
    with patch(
        "custom_components.norman_gen1.gen2.NormanApiClient",
        side_effect=transport.client_factory,
    ):
        assert await hass.config_entries.async_setup(configured.entry_id)
    return configured


async def test_notification_does_not_cancel_inflight_http_status(
    hass, shadeauto_transport, monkeypatch
):
    """A real stream update during a real status read leaves that read running."""
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    transport = shadeauto_transport
    configured = await _setup_http_entry(hass, transport)
    try:
        initial_status_count = transport.requests.count("status")
        transport.block_status()
        transport.notifications.put_nowait(None)
        await asyncio.wait_for(transport.status_started.wait(), timeout=1)
        transport.notifications.put_nowait(None)
        await asyncio.sleep(0.03)
        # Replacement must not free the coordinator lock by cancelling the first read.
        assert transport.requests.count("status") == initial_status_count + 1
        transport.release_status.set()
        await asyncio.sleep(0.03)
        assert configured.runtime_data.last_update_success
        assert transport.requests.count("status") >= initial_status_count + 1
        assert await hass.config_entries.async_unload(configured.entry_id)
    finally:
        transport.release_status.set()
        await hass.config_entries.async_unload(configured.entry_id)


async def test_unload_cancels_detached_inflight_refresh(
    hass, shadeauto_transport, monkeypatch
):
    """A refresh detached from its timer remains owned and cancellable on unload."""
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    transport = shadeauto_transport
    configured = await _setup_http_entry(hass, transport)
    try:
        initial_status_count = transport.requests.count("status")
        transport.block_status()
        transport.notifications.put_nowait(None)
        await asyncio.wait_for(transport.status_started.wait(), timeout=1)
        coordinator = configured.runtime_data
        assert await asyncio.wait_for(
            hass.config_entries.async_unload(configured.entry_id), timeout=1
        )
        transport.release_status.set()
        await asyncio.sleep(0.02)
        assert not coordinator._refresh_tasks  # noqa: SLF001
        assert transport.requests.count("status") == initial_status_count + 1
    finally:
        transport.release_status.set()
        await hass.config_entries.async_unload(configured.entry_id)


async def test_continuous_http_notifications_have_a_maximum_wait(
    hass, shadeauto_transport, monkeypatch
):
    """Frequent stream messages cannot indefinitely postpone the status read."""
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.03)
    monkeypatch.setattr(coordinator_module, "NOTIFICATION_REFRESH_MAX_WAIT", 0.07)
    transport = shadeauto_transport
    configured = await _setup_http_entry(hass, transport)
    try:
        initial_status_count = transport.requests.count("status")
        for _ in range(12):
            transport.notifications.put_nowait(None)
            await asyncio.sleep(0.01)
        assert transport.requests.count("status") > initial_status_count
        assert await hass.config_entries.async_unload(configured.entry_id)
    finally:
        transport.release_status.set()
        await hass.config_entries.async_unload(configured.entry_id)


async def test_notification_maximum_wait_still_waits_for_commands(
    refresh_coordinator, monkeypatch
):
    """The max-wait cap cannot insert a read into a queued room command burst."""
    coordinator = refresh_coordinator
    monkeypatch.setattr(coordinator_module, "COMMAND_REFRESH_DELAY", 0.01)
    monkeypatch.setattr(coordinator_module, "NOTIFICATION_REFRESH_MAX_WAIT", 0.03)
    monkeypatch.setattr(coordinator_module, "COMMAND_QUIET_PERIOD", 0.01)
    started = asyncio.Event()
    release = asyncio.Event()

    async def control(*_args):
        started.set()
        await release.wait()

    coordinator.api.async_set_position.side_effect = control
    command = asyncio.create_task(
        coordinator.async_set_room_position(1, bottom=50, middle=None)
    )
    await started.wait()
    notifications = asyncio.Queue()

    async def listen():
        while True:
            await notifications.get()
            yield {"PeripheralList": []}

    coordinator.api.async_listen_notifications = listen
    listener = asyncio.create_task(coordinator.listen_notifications())
    try:
        for _ in range(8):
            notifications.put_nowait(None)
            await asyncio.sleep(0.01)
        assert coordinator.api.async_get_status.await_count == 1
        release.set()
        await command
        await wait_for_status_call(coordinator)
    finally:
        release.set()
        listener.cancel()
        await asyncio.gather(listener, command, return_exceptions=True)
