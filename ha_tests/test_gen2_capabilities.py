"""Cover actions follow reported rails without inventing hardware mappings."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.norman_gen1.const import DOMAIN
from custom_components.norman_gen1.gen2 import coordinator as coordinator_module
from custom_components.norman_gen1.gen2.cover import NormanBlind
from custom_components.norman_gen1.gen2.room import NormanRoomCover
from custom_components.norman_gen1.gen2.sensor import NormanBatteryReading

from . import test_gen2, test_gen2_transport

shadeauto = test_gen2.shadeauto
shadeauto_transport = test_gen2_transport.shadeauto_transport
pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_bottom_only_cover_and_room_omit_middle_rail(hass, shadeauto):
    """Missing middle fields allow bottom control and disable tilt services."""
    shadeauto.async_get_status.return_value = {
        "Peripherals": [{"PeripheralUID": 7, "BottomRailPosition": 35}]
    }
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    cover = registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_7")
    room = registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_room_1")
    for entity_id in (cover, room):
        features = hass.states.get(entity_id).attributes["supported_features"]
        assert features & CoverEntityFeature.SET_POSITION
        assert not features & CoverEntityFeature.SET_TILT_POSITION
        await hass.services.async_call(
            DOMAIN, "nudge_position", {"entity_id": entity_id, "step": 5}, blocking=True
        )
    assert [call.args for call in shadeauto.async_set_position.await_args_list] == [
        (7, 40, None),
        (7, 45, None),
    ]
    with pytest.raises(HomeAssistantError):
        await configured.runtime_data.async_set_position(7, bottom=None, middle=50)
    await hass.config_entries.async_unload(configured.entry_id)


async def test_middle_rail_observed_once_is_not_discarded(hass, shadeauto):
    """Intermittent missing TDBU data still blocks unsafe preservation."""
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    coordinator = configured.runtime_data
    shadeauto.async_get_status.return_value = {
        "Peripherals": [{"PeripheralUID": 7, "BottomRailPosition": 35}]
    }
    await coordinator.async_refresh()
    assert coordinator.data[7].supports_middle_rail
    with pytest.raises(HomeAssistantError, match="unknown rail"):
        await coordinator.async_set_position(7, bottom=40, middle=None)
    shadeauto.async_set_position.assert_not_awaited()
    await hass.config_entries.async_unload(configured.entry_id)


@pytest.mark.parametrize("target", [None, 0])
async def test_null_middle_requires_a_valid_observed_value(hass, shadeauto, target):
    """A null placeholder does not create tilt support; a valid target does."""
    shadeauto.async_get_status.return_value["Peripherals"][0].update(
        MiddleRailPosition=None, TargetMiddleRailPosition=target
    )
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    coordinator = configured.runtime_data
    cover_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_7"
    )
    features = hass.states.get(cover_id).attributes["supported_features"]
    assert bool(features & CoverEntityFeature.SET_TILT_POSITION) == (target is not None)
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": cover_id}, blocking=True
    )
    shadeauto.async_set_position.assert_awaited_once_with(7, 100, target)
    if target is not None:
        shadeauto.async_get_status.return_value["Peripherals"][0].update(
            MiddleRailPosition=None, TargetMiddleRailPosition=None
        )
        # Clear the local command's other rail target: only bottom was requested.
        await coordinator.async_refresh()
        with pytest.raises(HomeAssistantError, match="unknown rail"):
            await coordinator.async_set_room_position(1, bottom=40, middle=None)
    await hass.config_entries.async_unload(configured.entry_id)


async def test_real_control_request_can_omit_middle(hass, shadeauto_transport):
    """The HTTP body leaves a missing middle rail out rather than sending null."""
    transport = shadeauto_transport
    client = transport.make_client()
    try:
        await client.async_set_position(7, 40, None)
        payload = transport.controls[0][1]
        assert payload["BottomRailPosition"] == 40
        assert "MiddleRailPosition" not in payload
    finally:
        await client.async_close()


async def test_cover_direction_uses_pending_and_reported_target(hass, shadeauto):
    """Accepted commands expose direction immediately, then settle on status."""
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    coordinator = configured.runtime_data
    cover = NormanBlind(coordinator, 7, configured)
    room = NormanRoomCover(coordinator, 1, "Living room", configured)
    assert cover.is_opening is None and room.is_closing is None
    await coordinator.async_set_position(7, bottom=50, middle=None)
    assert cover.is_opening is True and cover.is_closing is False
    assert room.is_opening is True and room.is_closing is False
    await coordinator.async_set_position(7, bottom=10, middle=None)
    assert cover.is_closing is True and room.is_closing is True
    shadeauto.async_get_status.return_value["Peripherals"][0].update(
        BottomRailPosition=10, TargetBottomRailPosition=10
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=40))
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    assert cover.is_opening is False and cover.is_closing is False
    assert room.is_opening is False and room.is_closing is False
    await hass.config_entries.async_unload(configured.entry_id)


@pytest.mark.parametrize(
    "value", [3.7, 75, 0, None, True, "3.7", float("nan"), float("inf")]
)
async def test_battery_reading_never_guesses_units(hass, shadeauto, value):
    """Optional diagnostics preserve numbers and reject malformed readings."""
    configured = test_gen2.entry(hass)
    shadeauto.async_get_status.return_value["Peripherals"][0]["BatteryVoltage"] = value
    assert await hass.config_entries.async_setup(configured.entry_id)
    sensor = NormanBatteryReading(configured.runtime_data, configured, 7)
    expected = value if type(value) in (float, int) and value in (3.7, 75, 0) else None
    assert sensor.native_value == expected
    assert sensor.native_unit_of_measurement is None
    assert sensor.device_class is None
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "gen2_shade-hub_7_battery_reading"
    )
    assert (
        registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert not hass.states.get(entity_id)
    await hass.config_entries.async_unload(configured.entry_id)


async def test_identical_status_clears_expired_direction_in_entity_state(
    hass, shadeauto
):
    """An unchanged status still publishes expiry of a local pending target."""
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    coordinator = configured.runtime_data
    entity_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_7"
    )
    with patch(
        "custom_components.norman_gen1.gen2.coordinator.monotonic", return_value=10
    ):
        await coordinator.async_set_position(7, bottom=50, middle=None)
        assert hass.states.get(entity_id).state == "opening"
        assert hass.states.get(entity_id).attributes["target_position"] == 50
    with patch(
        "custom_components.norman_gen1.gen2.coordinator.monotonic", return_value=50
    ):
        await coordinator.async_refresh()
        assert hass.states.get(entity_id).state == "open"
        assert hass.states.get(entity_id).attributes["target_position"] is None
    await hass.config_entries.async_unload(configured.entry_id)


async def test_enabled_battery_reading_uses_existing_shade_device(hass, shadeauto):
    """Opting into the sensor exposes the raw reading on the real HA platform."""
    configured = test_gen2.entry(hass)
    shadeauto.async_get_status.return_value["Peripherals"][0]["BatteryVoltage"] = 3.7
    assert await hass.config_entries.async_setup(configured.entry_id)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "gen2_shade-hub_7_battery_reading"
    )
    cover_id = registry.async_get_entity_id("cover", DOMAIN, "gen2_shade-hub_7")
    assert (
        registry.async_get(entity_id).device_id
        == registry.async_get(cover_id).device_id
    )
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(configured.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "3.7"
    assert "unit_of_measurement" not in state.attributes
    assert "device_class" not in state.attributes
    shadeauto.async_get_status.return_value["Peripherals"][0]["BatteryVoltage"] = 75
    await configured.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == "75"
    await hass.config_entries.async_unload(configured.entry_id)


async def test_room_mixed_directions_are_unknown(hass, shadeauto):
    """A public room action cannot report simultaneous opening and closing."""
    peripherals = shadeauto.async_get_devices.return_value["results"]["RoomList"][0][
        "GroupList"
    ][0]["PeripheralList"]
    peripherals.append({"PeripheralUID": 8, "PeripheralName": "Other shade"})
    shadeauto.async_get_status.return_value["Peripherals"].append(
        {"PeripheralUID": 8, "BottomRailPosition": 80, "MiddleRailPosition": 65}
    )
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    room_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_room_1"
    )
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": room_id, "position": 50},
        blocking=True,
    )
    room = NormanRoomCover(configured.runtime_data, 1, "Living room", configured)
    assert room.is_opening is None and room.is_closing is None
    assert hass.states.get(room_id).state == "open"
    await hass.config_entries.async_unload(configured.entry_id)


async def test_stale_hub_target_expires_without_a_status_change(
    hass, shadeauto, monkeypatch
):
    """A stopped shade loses its motion claim while preserving its rail target."""
    monkeypatch.setattr(coordinator_module, "MOTION_REPORT_TIMEOUT", 0.03)
    shadeauto.async_get_status.return_value["Peripherals"][0][
        "TargetBottomRailPosition"
    ] = 90
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    coordinator = configured.runtime_data
    cover_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_7"
    )
    assert hass.states.get(cover_id).state == "open"
    shadeauto.async_get_status.return_value["Peripherals"][0]["BottomRailPosition"] = 40
    await coordinator.async_refresh()
    assert hass.states.get(cover_id).state == "opening"
    await asyncio.sleep(0.05)
    assert hass.states.get(cover_id).state == "open"
    assert hass.states.get(cover_id).attributes["target_position"] == 90
    # Equal data and a continuing stale target must not restart the window.
    await coordinator.async_refresh()
    assert hass.states.get(cover_id).state == "open"
    await hass.config_entries.async_unload(configured.entry_id)


async def test_pending_direction_expires_without_status_read(
    hass, shadeauto, monkeypatch
):
    """A local pending target clears from HA even before the follow-up read."""
    monkeypatch.setattr(coordinator_module, "PENDING_TARGET_TIMEOUT", 0.03)
    configured = test_gen2.entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    cover_id = er.async_get(hass).async_get_entity_id(
        "cover", DOMAIN, "gen2_shade-hub_7"
    )
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": cover_id}, blocking=True
    )
    assert hass.states.get(cover_id).state == "opening"
    await asyncio.sleep(0.05)
    assert hass.states.get(cover_id).state == "open"
    assert hass.states.get(cover_id).attributes["target_position"] is None
    assert shadeauto.async_get_status.await_count == 1
    await hass.config_entries.async_unload(configured.entry_id)
