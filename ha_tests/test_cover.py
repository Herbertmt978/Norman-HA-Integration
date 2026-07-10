"""Test cover behavior through Home Assistant services and state."""

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.norman_gen1.api import (
    CannotConnect,
    CannotControl,
    InvalidAuth,
    InvalidSession,
    NormanRoom,
    NormanWindow,
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


async def test_group_commands_use_home_assistant_position_semantics(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Translate service calls to the expected raw panel commands."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1_level_1")
    api = entry.runtime_data.api

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_group_position.assert_awaited_once_with(1, 1, 100, 1)
    assert hass.states.get(entity_id).attributes[ATTR_CURRENT_POSITION] == 100

    api.set_group_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: entity_id, ATTR_POSITION: 45},
        blocking=True,
    )
    api.set_group_position.assert_awaited_once_with(1, 1, 45, 1)

    api.set_group_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_group_position.assert_awaited_once_with(1, 1, 0, 1)


async def test_room_commands_use_discovered_levels_and_models(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Send room services through the discovered panel-level command path."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1")
    api = entry.runtime_data.api

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_room_position.assert_awaited_once_with(1, [1], 100, {1: 1})

    api.set_room_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: entity_id, ATTR_POSITION: 25},
        blocking=True,
    )
    api.set_room_position.assert_awaited_once_with(1, [1], 25, {1: 1})

    api.set_room_position.reset_mock()
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    api.set_room_position.assert_awaited_once_with(1, [1], 0, {1: 1})


async def test_unconfirmed_command_raises_translated_error(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Expose a rejected hub command as a translated service error."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1_level_1")
    mock_norman_api.control_error = CannotControl("not acknowledged")

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert caught.value.translation_key == "command_not_confirmed"


async def test_identity_change_blocks_control_before_command(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Never send a movement command after the endpoint identifies as another hub."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1_level_1")
    api = entry.runtime_data.api
    mock_norman_api.hub_info["hubId"] = "hub-2"

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert caught.value.translation_key == "wrong_hub"
    api.set_group_position.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (CannotConnect("offline"), "cannot_connect"),
        (InvalidAuth("bad password"), "invalid_auth"),
    ],
)
async def test_connection_and_auth_errors_are_translated(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
    error: Exception,
    translation_key: str,
) -> None:
    """Translate connection-layer failures from a service command."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1_level_1")
    mock_norman_api.auth_error = error

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert caught.value.translation_key == translation_key


async def test_control_session_retries_once_then_reports_rejection(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Retry one rejected session and translate a second rejection."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1_level_1")
    mock_norman_api.auth_errors = [
        InvalidSession("expired"),
        InvalidSession("expired again"),
    ]

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert caught.value.translation_key == "session_rejected"


async def test_control_session_recovers_on_retry(
    hass: HomeAssistant,
    setup_integration,
    mock_norman_api,
) -> None:
    """Complete the command when the retry obtains a valid session."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, "hub-1_room_1_level_1")
    mock_norman_api.auth_errors = [InvalidSession("expired"), None]

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )

    entry.runtime_data.api.set_group_position.assert_awaited_once()


async def test_reversed_room_without_levels_cannot_send_full_open_as_close(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Hide and reject a reversed close target when no panel levels exist."""
    mock_norman_api.rooms = [
        NormanRoom(
            id=1,
            name="Shutters",
            group_names=[],
            raw={"Style": 13},
        )
    ]
    mock_norman_api.windows = [
        NormanWindow(
            id=1,
            name="Shutters",
            room_id=1,
            level=-1,
            group_id=None,
            position=100,
            model=1,
            battery=None,
            raw={},
        )
    ]
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = _entity_id(hass, mock_config_entry.entry_id, "hub-1_room_1")
    state = hass.states.get(entity_id)
    assert state.attributes.get("supported_features", 0) == 0

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_CLOSE_COVER,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert caught.value.translation_key in {None, "service_not_supported"}
    mock_config_entry.runtime_data.api.set_room_position.assert_not_awaited()
