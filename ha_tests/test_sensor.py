"""Test physical-window battery sensors through Home Assistant."""

from homeassistant.components.sensor import (
    ATTR_STATE_CLASS,
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.norman_gen1.api import NormanRoom, NormanWindow
from custom_components.norman_gen1.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _battery_entities(
    hass: HomeAssistant, entry_id: str
) -> dict[str, er.RegistryEntry]:
    """Return battery entities keyed by their stable Norman unique ID."""
    registry = er.async_get(hass)
    return {
        entity.unique_id: entity
        for entity in registry.entities.values()
        if entity.config_entry_id == entry_id and entity.domain == SENSOR_DOMAIN
    }


async def test_physical_windows_create_room_battery_diagnostics(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Expose every physical motor battery on its existing room device."""
    mock_norman_api.windows = [
        NormanWindow(
            id=11,
            name=" Panel 1   top ",
            room_id=1,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=100,
            raw={},
        ),
        NormanWindow(
            id=12,
            name="Panel 1 bottom",
            room_id=1,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=21,
            raw={},
        ),
    ]

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entities = _battery_entities(hass, mock_config_entry.entry_id)
    assert set(entities) == {
        "hub-1_window_11_battery",
        "hub-1_window_12_battery",
    }

    room_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "hub-1_room_1")}
    )
    assert room_device is not None

    expected = {
        "hub-1_window_11_battery": ("100", "Panel 1 top battery"),
        "hub-1_window_12_battery": ("21", "Panel 1 bottom battery"),
    }
    for unique_id, (value, name) in expected.items():
        registry_entry = entities[unique_id]
        state = hass.states.get(registry_entry.entity_id)
        assert state is not None
        assert state.state == value
        assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.BATTERY
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE
        assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
        assert state.attributes[ATTR_FRIENDLY_NAME] == f"Living room {name}"
        assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
        assert registry_entry.device_id == room_device.id
        assert registry_entry.original_name == name

    # Sensors consume the coordinator snapshot; their setup performs no hub reads.
    api = mock_config_entry.runtime_data.api
    assert api.authenticated_session.call_count == 1
    assert api.get_rooms.await_count == 1
    assert api.get_windows.await_count == 1


async def test_battery_sensors_are_discovered_once_after_refresh(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Add a newly discovered physical motor without duplicating existing sensors."""
    entry = setup_integration
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Right panel",
            room_id=1,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=82,
            raw={},
        )
    )

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert set(_battery_entities(hass, entry.entry_id)) == {
        "hub-1_window_1_battery",
        "hub-1_window_2_battery",
    }

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert set(_battery_entities(hass, entry.entry_id)) == {
        "hub-1_window_1_battery",
        "hub-1_window_2_battery",
    }


async def test_battery_value_updates_from_the_shared_coordinator_snapshot(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Refresh a battery value through the existing room/window transaction."""
    entry = setup_integration
    entity = _battery_entities(hass, entry.entry_id)["hub-1_window_1_battery"]
    window = mock_norman_api.windows[0]
    mock_norman_api.windows[0] = NormanWindow(
        id=window.id,
        name=window.name,
        room_id=window.room_id,
        level=window.level,
        group_id=window.group_id,
        position=window.position,
        model=window.model,
        battery=21,
        raw=window.raw,
    )
    api = entry.runtime_data.api

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity.entity_id).state == "21"
    assert api.get_rooms.await_count == 2
    assert api.get_windows.await_count == 2


async def test_motor_room_change_keeps_one_stable_battery_entity(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Treat the hub-global physical window ID as the sensor identity."""
    entry = setup_integration
    original = mock_norman_api.windows[0]
    mock_norman_api.rooms.append(NormanRoom(2, "Second room", ["Panel"], {"Style": 13}))
    mock_norman_api.windows[0] = NormanWindow(
        id=original.id,
        name=original.name,
        room_id=2,
        level=1,
        group_id=original.group_id,
        position=37,
        model=original.model,
        battery=55,
        raw=original.raw,
    )

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    entities = _battery_entities(hass, entry.entry_id)
    assert set(entities) == {"hub-1_window_1_battery"}
    assert hass.states.get(entities["hub-1_window_1_battery"].entity_id).state == "55"
    office_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "hub-1_room_2")}
    )
    assert office_device is not None
    assert entities["hub-1_window_1_battery"].device_id == office_device.id


async def test_unknown_battery_stays_available_until_window_disappears(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Distinguish an unknown reading from a missing physical window."""
    entry = setup_integration
    entity = _battery_entities(hass, entry.entry_id)["hub-1_window_1_battery"]
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    window = mock_norman_api.windows[0]
    mock_norman_api.windows = [
        NormanWindow(
            id=window.id,
            name=window.name,
            room_id=window.room_id,
            level=window.level,
            group_id=window.group_id,
            position=window.position,
            model=window.model,
            battery="invalid",  # type: ignore[arg-type]
            raw=window.raw,
        )
    ]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    mock_norman_api.windows = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
