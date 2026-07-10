"""Test config-entry setup, refresh, discovery, and unload behavior."""

from datetime import timedelta

import aiohttp
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.norman_gen1.api import (
    CannotConnect,
    InvalidAuth,
    InvalidSession,
    NormanRoom,
    NormanWindow,
)
from custom_components.norman_gen1.const import DEFAULT_SCAN_INTERVAL, DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _integration_entities(
    hass: HomeAssistant, entry_id: str
) -> dict[str, er.RegistryEntry]:
    """Return entity-registry entries keyed by their Norman unique ID."""
    registry = er.async_get(hass)
    return {
        entity.unique_id: entity
        for entity in registry.entities.values()
        if entity.config_entry_id == entry_id
    }


async def _advance_poll(hass: HomeAssistant, minutes: int = 1) -> None:
    """Advance time through a natural coordinator polling interval."""
    async_fire_time_changed(
        hass,
        dt_util.now() + DEFAULT_SCAN_INTERVAL * minutes + timedelta(seconds=1),
    )
    await hass.async_block_till_done()


async def test_setup_registers_entities_device_and_unloads(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Set up room/panel entities and cleanly release the client on unload."""
    entry = setup_integration
    entities = _integration_entities(hass, entry.entry_id)

    assert set(entities) == {"hub-1_room_1", "hub-1_room_1_level_1"}
    assert hass.states.get(entities["hub-1_room_1"].entity_id).state == "closed"
    assert hass.states.get(entities["hub-1_room_1_level_1"].entity_id).state == "closed"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "hub-1")})
    assert device is not None
    assert device.manufacturer == "Norman"
    assert device.model == "Gen 1 Hub"
    assert device.sw_version == "1.0"

    runtime_client = entry.runtime_data.api
    runtime_session = runtime_client.session
    assert isinstance(runtime_session.cookie_jar, aiohttp.DummyCookieJar)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    runtime_client.async_close.assert_awaited_once()
    assert runtime_session.closed
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_dynamic_room_and_group_are_added_after_natural_refresh(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Discover new room and group entities without reloading the entry."""
    entry = setup_integration
    mock_norman_api.rooms.append(
        NormanRoom(
            id=2,
            name="Dining room",
            group_names=["Panel"],
            raw={"Style": 99},
        )
    )
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Panel",
            room_id=2,
            level=1,
            group_id=None,
            position=100,
            model=1,
            battery=None,
            raw={},
        )
    )

    await _advance_poll(hass)

    entities = _integration_entities(hass, entry.entry_id)
    assert set(entities) == {
        "hub-1_room_1",
        "hub-1_room_1_level_1",
        "hub-1_room_2",
        "hub-1_room_2_level_1",
    }
    assert hass.states.get(entities["hub-1_room_2"].entity_id).state == "open"


async def test_runtime_hub_identity_change_makes_entities_unavailable_and_recovers(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Refuse data from a different hub until the configured hub returns."""
    entry = setup_integration
    entities = _integration_entities(hass, entry.entry_id)
    room_entity_id = entities["hub-1_room_1"].entity_id

    mock_norman_api.hub_info["hubId"] = "hub-2"
    await _advance_poll(hass)
    assert hass.states.get(room_entity_id).state == "unavailable"

    mock_norman_api.hub_info["hubId"] = "hub-1"
    await _advance_poll(hass, 2)
    assert hass.states.get(room_entity_id).state == "closed"


@pytest.mark.parametrize(
    "error",
    [
        CannotConnect("offline"),
        InvalidSession("expired"),
    ],
)
async def test_refresh_failure_makes_entities_unavailable(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
    error: Exception,
) -> None:
    """Mark entities unavailable when a scheduled refresh cannot complete."""
    entry = setup_integration
    entity_id = _integration_entities(hass, entry.entry_id)["hub-1_room_1"].entity_id
    if isinstance(error, InvalidSession):
        mock_norman_api.auth_errors = [error, InvalidSession("expired again")]
    else:
        mock_norman_api.auth_error = error

    await _advance_poll(hass)

    assert hass.states.get(entity_id).state == "unavailable"


async def test_empty_refresh_keeps_entities_but_marks_them_unavailable(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Treat an empty hub snapshot as a failed update, not device removal."""
    entry = setup_integration
    entity_id = _integration_entities(hass, entry.entry_id)["hub-1_room_1"].entity_id
    mock_norman_api.rooms = []
    mock_norman_api.windows = []

    await _advance_poll(hass)

    assert hass.states.get(entity_id).state == "unavailable"
    assert "hub-1_room_1" in _integration_entities(hass, entry.entry_id)


async def test_unknown_positions_produce_unknown_cover_state(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Report unknown rather than inventing state when no position is available."""
    entry = setup_integration
    entity_id = _integration_entities(hass, entry.entry_id)["hub-1_room_1"].entity_id
    window = mock_norman_api.windows[0]
    mock_norman_api.windows[0] = NormanWindow(
        id=window.id,
        name=window.name,
        room_id=window.room_id,
        level=window.level,
        group_id=window.group_id,
        position=None,
        model=window.model,
        battery=window.battery,
        raw=window.raw,
    )

    await _advance_poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


async def test_partial_unknown_positions_keep_room_and_group_unknown(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Never claim an aggregate is closed while one panel position is unknown."""
    entry = setup_integration
    mock_norman_api.windows.append(
        NormanWindow(
            id=2,
            name="Right panel",
            room_id=1,
            level=1,
            group_id=None,
            position=None,
            model=1,
            battery=None,
            raw={},
        )
    )

    await _advance_poll(hass)

    entities = _integration_entities(hass, entry.entry_id)
    assert hass.states.get(entities["hub-1_room_1"].entity_id).state == "unknown"
    assert (
        hass.states.get(entities["hub-1_room_1_level_1"].entity_id).state == "unknown"
    )


async def test_window_only_snapshot_generates_stable_room_entity(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Generate fallback room metadata when only window data is returned."""
    mock_norman_api.rooms = []

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entities = _integration_entities(hass, mock_config_entry.entry_id)
    assert "hub-1_room_1" in entities
    assert mock_config_entry.runtime_data.data.rooms_by_id[1].name == "Room 1"


async def test_setup_auth_failure_starts_reauthentication(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Surface an invalid saved password as a config-entry auth failure."""
    mock_norman_api.auth_error = InvalidAuth("bad password")

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )


async def test_setup_upgrades_legacy_host_unique_id(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Learn and store the stable hub ID during setup of a legacy entry."""
    hass.config_entries.async_update_entry(
        mock_config_entry, unique_id=mock_config_entry.data["host"]
    )
    old_hub_id = mock_config_entry.data["host"]
    device_registry = dr.async_get(hass)
    legacy_device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, old_hub_id)},
        name="Legacy Norman hub",
    )
    entity_registry = er.async_get(hass)
    legacy_room = entity_registry.async_get_or_create(
        COVER_DOMAIN,
        DOMAIN,
        f"{old_hub_id}_room_1",
        config_entry=mock_config_entry,
        device_id=legacy_device.id,
        suggested_object_id="custom_living_room",
    )
    legacy_group = entity_registry.async_get_or_create(
        COVER_DOMAIN,
        DOMAIN,
        f"{old_hub_id}_room_1_level_1",
        config_entry=mock_config_entry,
        device_id=legacy_device.id,
        suggested_object_id="custom_left_panel",
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.unique_id == "hub-1"
    entities = _integration_entities(hass, mock_config_entry.entry_id)
    assert set(entities) == {"hub-1_room_1", "hub-1_room_1_level_1"}
    assert entities["hub-1_room_1"].entity_id == legacy_room.entity_id
    assert entities["hub-1_room_1_level_1"].entity_id == legacy_group.entity_id
    migrated_device = device_registry.async_get_device({(DOMAIN, "hub-1")})
    assert migrated_device is not None
    assert migrated_device.id == legacy_device.id
    assert device_registry.async_get_device({(DOMAIN, old_hub_id)}) is None
