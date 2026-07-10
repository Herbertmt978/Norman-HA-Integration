"""Test shutter-profile options and later device discovery."""

from datetime import timedelta

from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.norman_gen1.api import NormanRoom, NormanWindow
from custom_components.norman_gen1.const import (
    CONF_KNOWN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
    DEFAULT_SCAN_INTERVAL,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entity_id(hass: HomeAssistant, entry_id: str, unique_id: str) -> str:
    """Return the entity ID for one integration unique ID."""
    registry = er.async_get(hass)
    return next(
        entity.entity_id
        for entity in registry.entities.values()
        if entity.config_entry_id == entry_id and entity.unique_id == unique_id
    )


async def _save_empty_options(hass: HomeAssistant, entry_id: str) -> None:
    """Explicitly disable both movement-profile overrides."""
    result = await hass.config_entries.options.async_init(entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TILT_OPEN_TARGETS: [],
            CONF_REVERSED_CLOSE_TARGETS: [],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def _advance_poll(hass: HomeAssistant) -> None:
    """Advance through a natural coordinator refresh."""
    async_fire_time_changed(
        hass,
        dt_util.now() + DEFAULT_SCAN_INTERVAL + timedelta(seconds=1),
    )
    await hass.async_block_till_done()


async def _assert_room_profile(
    hass: HomeAssistant,
    entity_id: str,
    api,
    room_id: int,
    open_position: int,
    close_position: int,
) -> None:
    """Assert profile behavior through public cover services."""
    api.set_room_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_room_position.assert_awaited_once_with(room_id, [1], open_position, {1: 1})
    api.set_room_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_room_position.assert_awaited_once_with(room_id, [1], close_position, {1: 1})


async def test_options_store_known_targets_and_reload_once(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Persist explicit selections and a discovery snapshot for future defaults."""
    mock_norman_api.rooms[0].raw = {"Style": 13}
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    clients_before = len(mock_norman_api.clients)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    defaults = result["data_schema"]({})
    assert defaults[CONF_TILT_OPEN_TARGETS] == ["room:1"]
    assert defaults[CONF_REVERSED_CLOSE_TARGETS] == ["room:1"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TILT_OPEN_TARGETS: [],
            CONF_REVERSED_CLOSE_TARGETS: [],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert mock_config_entry.options == {
        CONF_TILT_OPEN_TARGETS: [],
        CONF_REVERSED_CLOSE_TARGETS: [],
        CONF_KNOWN_TARGETS: ["group:1:1", "room:1"],
    }
    assert len(mock_norman_api.clients) == clients_before + 1


async def test_new_style_13_room_keeps_automatic_safe_profile(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Apply automatic style defaults to rooms discovered after options were saved."""
    entry = setup_integration
    await _save_empty_options(hass, entry.entry_id)
    mock_norman_api.rooms.append(
        NormanRoom(
            id=2,
            name="Plantation shutters",
            group_names=["Panel"],
            raw={"Style": 13},
        )
    )
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Panel",
            room_id=2,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=None,
            raw={},
        )
    )

    await _advance_poll(hass)

    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_2")
    await _assert_room_profile(hass, entity_id, entry.runtime_data.api, 2, 37, 100)


async def test_legacy_options_snapshot_preserves_old_choices_and_new_defaults(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Migrate pre-0.2 choices without disabling safe defaults for later rooms."""
    mock_norman_api.rooms[0].raw = {"Style": 13}
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_TILT_OPEN_TARGETS: [],
            CONF_REVERSED_CLOSE_TARGETS: [],
        },
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.options[CONF_KNOWN_TARGETS] == [
        "group:1:1",
        "room:1",
    ]

    existing_id = _entity_id(hass, mock_config_entry.entry_id, "hub-1_room_1")
    await _assert_room_profile(
        hass, existing_id, mock_config_entry.runtime_data.api, 1, 100, 0
    )

    mock_norman_api.rooms.append(
        NormanRoom(
            id=2,
            name="New plantation shutters",
            group_names=["Panel"],
            raw={"Style": 13},
        )
    )
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Panel",
            room_id=2,
            level=1,
            group_id=None,
            position=37,
            model=1,
            battery=None,
            raw={},
        )
    )

    await _advance_poll(hass)

    new_id = _entity_id(hass, mock_config_entry.entry_id, "hub-1_room_2")
    await _assert_room_profile(
        hass, new_id, mock_config_entry.runtime_data.api, 2, 37, 100
    )


async def test_new_group_in_known_room_inherits_explicit_room_disable(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Do not re-enable a profile for a new group in a known disabled room."""
    mock_norman_api.rooms[0].raw = {"Style": 13}
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await _save_empty_options(hass, mock_config_entry.entry_id)
    mock_norman_api.rooms[0] = NormanRoom(
        id=1,
        name="Living room",
        group_names=["Left panel", "Right panel"],
        raw={"Style": 13},
    )
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Right panel",
            room_id=1,
            level=2,
            group_id=None,
            position=100,
            model=1,
            battery=None,
            raw={},
        )
    )

    await _advance_poll(hass)

    entity_id = _entity_id(hass, mock_config_entry.entry_id, "hub-1_room_1_level_2")
    api = mock_config_entry.runtime_data.api
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_group_position.assert_awaited_once_with(1, 2, 100, 1)
    api.set_group_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_group_position.assert_awaited_once_with(1, 2, 0, 1)


async def test_options_form_survives_unloaded_entry_and_unknown_targets(
    hass: HomeAssistant,
) -> None:
    """Retain saved targets when no current coordinator snapshot is available."""
    entry = MockConfigEntry(
        domain="norman_gen1",
        title="Norman hub",
        unique_id="hub-1",
        data={},
        options={
            CONF_TILT_OPEN_TARGETS: ["room:99"],
            CONF_REVERSED_CLOSE_TARGETS: ["group:99:1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = result["data_schema"]({})

    assert result["type"] is FlowResultType.FORM
    assert defaults[CONF_TILT_OPEN_TARGETS] == ["room:99"]
    assert defaults[CONF_REVERSED_CLOSE_TARGETS] == ["group:99:1"]
