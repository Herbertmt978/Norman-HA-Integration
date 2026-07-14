"""Test Norman Gen 1 options."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.config_flow import (
    _stored_simultaneous_rooms,
    _whole_position,
)
from custom_components.norman_gen1.const import (
    CONF_CLOSE_POSITION,
    CONF_DEFAULT_CLOSE_POSITION,
    CONF_DEFAULT_OPEN_POSITION,
    CONF_INHERIT,
    CONF_OPEN_POSITION,
    CONF_POSITION_PROFILES,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_SIMULTANEOUS_ROOMS,
    CONF_TARGET,
    CONF_TILT_OPEN_TARGETS,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _open_menu_step(
    hass: HomeAssistant,
    entry_id: str,
    step_id: str,
):
    result = await hass.config_entries.options.async_init(entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": step_id},
    )


async def _select_target(
    hass: HomeAssistant,
    entry_id: str,
    target: str,
):
    result = await _open_menu_step(hass, entry_id, "target")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "target"
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET: target},
    )


async def test_menu_and_defaults_show_requested_values(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Offer global and target editing with 37/100 as canonical defaults."""
    entry = setup_integration

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["defaults", "target", "room_commands"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "defaults"},
    )
    defaults = result["data_schema"]({})
    assert defaults == {
        CONF_DEFAULT_OPEN_POSITION: 37,
        CONF_DEFAULT_CLOSE_POSITION: "100",
    }


async def test_unloaded_entry_without_targets_offers_only_defaults(
    hass: HomeAssistant,
) -> None:
    """Hide target editing until a room, panel, or saved profile exists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Norman hub",
        unique_id="hub-1",
        version=2,
        data={},
        options={
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["defaults"]


async def test_room_commands_abort_if_discovery_disappears(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Defend a room-command step resumed after all rooms disappear."""
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    entry.runtime_data.data.rooms.clear()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "room_commands"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_rooms"


def test_stored_simultaneous_rooms_ignore_malformed_and_duplicate_targets() -> None:
    """Retain only unique, non-negative room option IDs in stored order."""
    assert _stored_simultaneous_rooms(
        {
            CONF_SIMULTANEOUS_ROOMS: [
                1,
                "room:2",
                "room:2",
                "group:3",
                "room",
                "room:not-a-number",
                "room:-1",
                "room:4",
            ]
        }
    ) == ["room:2", "room:4"]


async def test_room_commands_default_to_safe_fanout_and_preserve_options(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Opt rooms into broadcasts without changing their position profiles."""
    entry = setup_integration
    original_options = dict(entry.options)
    result = await _open_menu_step(hass, entry.entry_id, "room_commands")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room_commands"
    room_selector = next(iter(result["data_schema"].schema.values()))
    assert room_selector.config["options"] == [
        {"value": "room:1", "label": "Living room"}
    ]
    assert room_selector.config["multiple"] is True
    assert result["data_schema"]({}) == {CONF_SIMULTANEOUS_ROOMS: []}
    validated = result["data_schema"]({CONF_SIMULTANEOUS_ROOMS: ["room:1"]})
    assert validated == {CONF_SIMULTANEOUS_ROOMS: ["room:1"]}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        validated,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options == {
        **original_options,
        CONF_SIMULTANEOUS_ROOMS: ["room:1"],
    }

    result = await _open_menu_step(hass, entry.entry_id, "room_commands")
    assert result["data_schema"]({}) == {CONF_SIMULTANEOUS_ROOMS: ["room:1"]}


async def test_room_commands_preserve_saved_rooms_missing_from_discovery(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Do not erase an opt-in during a partial room discovery snapshot."""
    entry = setup_integration
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_SIMULTANEOUS_ROOMS: ["room:99", "room:1"],
        },
    )
    result = await _open_menu_step(hass, entry.entry_id, "room_commands")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SIMULTANEOUS_ROOMS: ["room:1"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options[CONF_SIMULTANEOUS_ROOMS] == ["room:99", "room:1"]


async def test_target_step_aborts_if_discovery_disappears(
    hass: HomeAssistant,
) -> None:
    """Defend a target step resumed after all choices have disappeared."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Norman hub",
        unique_id="hub-1",
        version=2,
        data={},
        options={
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {
                "room:99": {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                }
            },
        },
    )
    entry.add_to_hass(hass)
    result = await _open_menu_step(hass, entry.entry_id, "target")
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {},
        },
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET: "room:99"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_targets"


async def test_defaults_save_integers_and_preserve_sparse_profiles(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Save global endpoints without discarding migrated room overrides."""
    entry = setup_integration
    existing_profiles = dict(entry.options[CONF_POSITION_PROFILES])
    clients_before = len(mock_norman_api.clients)
    result = await _open_menu_step(hass, entry.entry_id, "defaults")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_OPEN_POSITION: 42,
            CONF_DEFAULT_CLOSE_POSITION: "0",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options == {
        CONF_DEFAULT_OPEN_POSITION: 42,
        CONF_DEFAULT_CLOSE_POSITION: 0,
        CONF_POSITION_PROFILES: existing_profiles,
    }
    assert len(mock_norman_api.clients) == clients_before + 1


@pytest.mark.parametrize(
    ("open_position", "close_position", "expected_error"),
    [
        (37.5, "100", {CONF_DEFAULT_OPEN_POSITION: "whole_number"}),
        (100, "100", {"base": "positions_must_differ"}),
    ],
)
async def test_defaults_reject_fractional_or_equal_positions(
    hass: HomeAssistant,
    setup_integration,
    open_position,
    close_position,
    expected_error,
) -> None:
    """Store only whole-number, distinct raw endpoints."""
    result = await _open_menu_step(hass, setup_integration.entry_id, "defaults")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_OPEN_POSITION: open_position,
            CONF_DEFAULT_CLOSE_POSITION: close_position,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected_error


async def test_panel_override_defaults_to_inherited_room_profile(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Show the effective room values before a panel is explicitly pinned."""
    result = await _select_target(
        hass,
        setup_integration.entry_id,
        "group:1:1",
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "profile"
    assert result["description_placeholders"]["target_name"] == (
        "Living room / Left panel"
    )
    assert result["data_schema"]({}) == {
        CONF_INHERIT: True,
        CONF_OPEN_POSITION: 100,
        CONF_CLOSE_POSITION: "0",
    }


async def test_room_override_inherits_global_profile(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Resolve room inheritance directly from the global defaults."""
    result = await _select_target(hass, setup_integration.entry_id, "room:1")

    assert result["step_id"] == "profile"
    assert result["data_schema"]({})[CONF_INHERIT] is False


async def test_panel_override_is_saved_without_changing_room_override(
    hass: HomeAssistant,
    setup_integration,
) -> None:
    """Pin one panel while retaining the migrated room profile."""
    entry = setup_integration
    room_profile = dict(entry.options[CONF_POSITION_PROFILES]["room:1"])
    result = await _select_target(hass, entry.entry_id, "group:1:1")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_INHERIT: False,
            CONF_OPEN_POSITION: 61,
            CONF_CLOSE_POSITION: "100",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options[CONF_POSITION_PROFILES] == {
        "room:1": room_profile,
        "group:1:1": {
            CONF_OPEN_POSITION: 61,
            CONF_CLOSE_POSITION: 100,
        },
    }


@pytest.mark.parametrize(
    ("open_position", "close_position", "expected_error"),
    [
        (37.5, "100", {CONF_OPEN_POSITION: "whole_number"}),
        (100, "100", {"base": "positions_must_differ"}),
    ],
)
async def test_target_profile_rejects_fractional_or_equal_positions(
    hass: HomeAssistant,
    setup_integration,
    open_position,
    close_position,
    expected_error,
) -> None:
    """Apply the same whole-number and distinct-endpoint validation to overrides."""
    result = await _select_target(hass, setup_integration.entry_id, "group:1:1")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_INHERIT: False,
            CONF_OPEN_POSITION: open_position,
            CONF_CLOSE_POSITION: close_position,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected_error


async def test_inherit_removes_only_the_selected_override(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Restore panel inheritance without deleting its room's profile."""
    hass.config_entries.async_update_entry(
        mock_config_entry,
        version=2,
        options={
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {
                "room:1": {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                },
                "group:1:1": {
                    CONF_OPEN_POSITION: 61,
                    CONF_CLOSE_POSITION: 100,
                },
            },
        },
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    result = await _select_target(hass, mock_config_entry.entry_id, "group:1:1")
    assert result["data_schema"]({})[CONF_INHERIT] is False

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_INHERIT: True,
            CONF_OPEN_POSITION: 61,
            CONF_CLOSE_POSITION: "100",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert mock_config_entry.options[CONF_POSITION_PROFILES] == {
        "room:1": {
            CONF_OPEN_POSITION: 42,
            CONF_CLOSE_POSITION: 0,
        }
    }


async def test_unloaded_entry_retains_stored_target_choices(
    hass: HomeAssistant,
) -> None:
    """Allow editing known sparse profiles without a live coordinator snapshot."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Norman hub",
        unique_id="hub-1",
        version=2,
        data={},
        options={
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {
                "room:99": {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                }
            },
        },
    )
    entry.add_to_hass(hass)

    result = await _open_menu_step(hass, entry.entry_id, "target")
    validated = result["data_schema"]({CONF_TARGET: "room:99"})

    assert validated[CONF_TARGET] == "room:99"


@pytest.mark.parametrize("target", ["room:not-an-integer", "legacy-target"])
async def test_malformed_stored_target_uses_safe_default_inheritance(
    hass: HomeAssistant,
    target: str,
) -> None:
    """Keep the options UI recoverable if an old target ID is malformed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Norman hub",
        unique_id="hub-1",
        version=2,
        data={},
        options={
            CONF_DEFAULT_OPEN_POSITION: 37,
            CONF_DEFAULT_CLOSE_POSITION: 100,
            CONF_POSITION_PROFILES: {
                target: {
                    CONF_OPEN_POSITION: 42,
                    CONF_CLOSE_POSITION: 0,
                }
            },
        },
    )
    entry.add_to_hass(hass)

    result = await _select_target(hass, entry.entry_id, target)

    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({})[CONF_INHERIT] is False


def test_whole_position_rejects_boolean_and_non_numeric_values() -> None:
    """Reject Python values that cannot originate from a valid number selector."""
    assert _whole_position(True) is None
    assert _whole_position(object()) is None


async def test_unloaded_v02_entry_cannot_erase_pending_legacy_choices(
    hass: HomeAssistant,
) -> None:
    """Require first setup to migrate old selections before editing options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Norman hub",
        unique_id="hub-1",
        version=1,
        data={},
        options={
            CONF_TILT_OPEN_TARGETS: ["room:1"],
            CONF_REVERSED_CLOSE_TARGETS: ["group:1:1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "migration_pending"
