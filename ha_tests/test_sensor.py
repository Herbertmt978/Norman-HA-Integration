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
    registry = er.async_get(hass)
    old_entities = {
        window_id: registry.async_get_or_create(
            SENSOR_DOMAIN,
            DOMAIN,
            f"hub-1_window_{window_id}_battery",
            config_entry=mock_config_entry,
            suggested_object_id=f"legacy_motor_{window_id}_battery",
            has_entity_name=True,
            original_name=original_name,
            translation_key="window_battery",
        )
        for window_id, original_name in {
            11: "Panel 1 bottom battery",
            12: "Panel 1 top battery",
        }.items()
    }
    old_entities[12] = registry.async_update_entity(
        old_entities[12].entity_id,
        name="Favourite motor battery",
    )
    old_entity_ids = {
        f"hub-1_window_{window_id}_battery": entity.entity_id
        for window_id, entity in old_entities.items()
    }
    # The response order is not an identity. Motor suffixes follow the hub's
    # per-level slot even when physical window IDs arrive in another order.
    mock_norman_api.windows = [
        NormanWindow(
            id=12,
            name=" Panel 1   top ",
            room_id=1,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=100,
            raw={},
            sort_order=2,
        ),
        NormanWindow(
            id=11,
            name="Panel 1 bottom",
            room_id=1,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=21,
            raw={},
            sort_order=1,
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
        "hub-1_window_11_battery": (
            "21",
            "Left panel motor 1 battery",
            "Living room Left panel motor 1 battery",
        ),
        "hub-1_window_12_battery": (
            "100",
            "Left panel motor 2 battery",
            "Favourite motor battery",
        ),
    }
    for unique_id, (value, name, friendly_name) in expected.items():
        registry_entry = entities[unique_id]
        state = hass.states.get(registry_entry.entity_id)
        assert state is not None
        assert state.state == value
        assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.BATTERY
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE
        assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
        assert state.attributes[ATTR_FRIENDLY_NAME] == friendly_name
        assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
        assert registry_entry.device_id == room_device.id
        assert registry_entry.original_name == name
        assert registry_entry.entity_id == old_entity_ids[unique_id]
        assert registry_entry.translation_key == "group_motor_battery"

    assert entities["hub-1_window_12_battery"].name == "Favourite motor battery"

    # Sensors consume the coordinator snapshot; their setup performs no hub reads.
    api = mock_config_entry.runtime_data.api
    assert api.authenticated_session.call_count == 1
    assert api.get_rooms.await_count == 1
    assert api.get_windows.await_count == 1


async def test_battery_names_follow_commandable_group_labels(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Replace inconsistent physical-motor names with their panel labels."""
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        "hub-1_window_31_battery",
        config_entry=mock_config_entry,
        suggested_object_id="id_abcd_battery",
        original_name="Id abcd battery",
    )
    existing = registry.async_update_entity(
        existing.entity_id,
        name="My workshop battery",
    )
    mock_norman_api.rooms = [
        NormanRoom(
            id=1,
            name="Workshop",
            group_names=["Panel Alpha", "Panel Beta", "Window bay"],
            raw={"Style": 13},
        )
    ]
    mock_norman_api.windows = [
        NormanWindow(
            id=31,
            name="Id abcd",
            room_id=1,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=100,
            raw={},
        ),
        NormanWindow(
            id=32,
            name="panel beta",
            room_id=1,
            level=2,
            group_id=None,
            position=37,
            model=1,
            battery=100,
            raw={},
        ),
        NormanWindow(
            id=33,
            name="Installer panel 3",
            room_id=1,
            level=3,
            group_id=None,
            position=37,
            model=1,
            battery=100,
            raw={},
        ),
    ]

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entities = _battery_entities(hass, mock_config_entry.entry_id)
    assert {
        unique_id: entity.original_name for unique_id, entity in entities.items()
    } == {
        "hub-1_window_31_battery": "Panel Alpha battery",
        "hub-1_window_32_battery": "Panel Beta battery",
        "hub-1_window_33_battery": "Window bay battery",
    }
    upgraded = entities["hub-1_window_31_battery"]
    assert upgraded.entity_id == existing.entity_id
    assert upgraded.name == "My workshop battery"


async def test_generated_fallback_battery_names_are_translated(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Translate missing group and physical-motor labels without English placeholders."""
    mock_norman_api.rooms = [NormanRoom(1, "Test room", [], {"Style": 13})]
    mock_norman_api.windows = [
        NormanWindow(51, "Internal A", 1, 1, None, 37, 1, 50, {}, 1),
        NormanWindow(52, "Internal B", 1, 1, None, 37, 1, 60, {}, 2),
        NormanWindow(53, "   ", 1, -1, None, 37, 1, 70, {}),
    ]

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entities = _battery_entities(hass, mock_config_entry.entry_id)
    assert {
        unique_id: entity.original_name for unique_id, entity in entities.items()
    } == {
        "hub-1_window_51_battery": "Group 1 motor 1 battery",
        "hub-1_window_52_battery": "Group 1 motor 2 battery",
        "hub-1_window_53_battery": "Unassigned motor 53 battery",
    }


async def test_battery_sensors_are_discovered_once_after_refresh(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Add a motor and relabel its existing group without duplicating sensors."""
    entry = setup_integration
    initial = _battery_entities(hass, entry.entry_id)["hub-1_window_1_battery"]
    assert initial.original_name == "Left panel battery"
    er.async_get(hass).async_update_entity(
        initial.entity_id,
        name="My left motor",
    )
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
    entities = _battery_entities(hass, entry.entry_id)
    assert entities["hub-1_window_1_battery"].original_name == (
        "Left panel motor 1 battery"
    )
    assert entities["hub-1_window_2_battery"].original_name == (
        "Left panel motor 2 battery"
    )
    assert entities["hub-1_window_1_battery"].name == "My left motor"

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert set(_battery_entities(hass, entry.entry_id)) == {
        "hub-1_window_1_battery",
        "hub-1_window_2_battery",
    }


async def test_new_zero_level_reloads_existing_one_based_battery_name(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Reload names when a newly discovered level changes the level index base."""
    mock_norman_api.rooms[0] = NormanRoom(
        id=1,
        name="Living room",
        group_names=["Panel Zero", "Panel One"],
        raw={"Style": 99},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entry = mock_config_entry
    initial = _battery_entities(hass, entry.entry_id)
    assert initial["hub-1_window_1_battery"].original_name == "Panel Zero battery"

    # The integration was initially set up with only level 1, so that level was
    # paired with the first panel label. Discovering level 0 makes the hub's
    # numbering unambiguously zero-based and moves level 1 to the second label.
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Internal zero",
            room_id=1,
            level=0,
            group_id=None,
            position=37,
            model=1,
            battery=82,
            raw={},
        )
    )

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    entities = _battery_entities(hass, entry.entry_id)
    assert entities["hub-1_window_1_battery"].original_name == "Panel One battery"
    assert entities["hub-1_window_2_battery"].original_name == "Panel Zero battery"


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
    moved_room_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "hub-1_room_2")}
    )
    assert moved_room_device is not None
    assert entities["hub-1_window_1_battery"].device_id == moved_room_device.id


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

    mock_norman_api.windows = [window]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert len(mock_norman_api.clients) == 1
