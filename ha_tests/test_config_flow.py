"""Test Norman config flows through Home Assistant's real flow manager."""

import aiohttp
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.api import CannotConnect, InvalidAuth, InvalidSession
from custom_components.norman_gen1.const import (
    CONF_APP_VERSION,
    DEFAULT_APP_VERSION,
    DEFAULT_PASSWORD,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _user_input(host: str = "192.0.2.10") -> dict[str, str]:
    """Return a complete user-flow submission."""
    return {
        CONF_HOST: host,
        CONF_PASSWORD: DEFAULT_PASSWORD,
        CONF_APP_VERSION: DEFAULT_APP_VERSION,
    }


def _add_legacy_registry_entries(
    hass: HomeAssistant, entry: MockConfigEntry
) -> tuple[str, str, dict[str, str]]:
    """Prepopulate the registry shape created by a host-identity release."""
    old_hub_id = entry.data[CONF_HOST]
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, old_hub_id)},
        name="Legacy Norman hub",
    )
    registry = er.async_get(hass)
    room = registry.async_get_or_create(
        COVER_DOMAIN,
        DOMAIN,
        f"{old_hub_id}_room_1",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="legacy_room",
    )
    group = registry.async_get_or_create(
        COVER_DOMAIN,
        DOMAIN,
        f"{old_hub_id}_room_1_level_1",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="legacy_panel",
    )
    return (
        old_hub_id,
        device.id,
        {
            "hub-1_room_1": room.entity_id,
            "hub-1_room_1_level_1": group.entity_id,
        },
    )


def _assert_legacy_registry_was_migrated(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    old_hub_id: str,
    old_device_id: str,
    expected_entity_ids: dict[str, str],
) -> None:
    """Assert registry identities changed without duplicating user objects."""
    registry = er.async_get(hass)
    migrated = {
        entity.unique_id: entity.entity_id
        for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    assert migrated == expected_entity_ids
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, "hub-1")})
    assert device is not None
    assert device.id == old_device_id
    assert device_registry.async_get_device({(DOMAIN, old_hub_id)}) is None


async def test_user_flow_uses_factory_default_and_creates_entry(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """The real flow manager should retain the known factory password default."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert DEFAULT_PASSWORD == "123456789"
    defaults = result["data_schema"]({CONF_HOST: "192.0.2.10"})
    assert defaults[CONF_PASSWORD] == DEFAULT_PASSWORD
    assert defaults[CONF_APP_VERSION] == DEFAULT_APP_VERSION

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "http://192.0.2.10/",
            CONF_PASSWORD: DEFAULT_PASSWORD,
            CONF_APP_VERSION: DEFAULT_APP_VERSION,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["data"][CONF_PASSWORD] == DEFAULT_PASSWORD
    assert result["result"].unique_id == "hub-1"
    assert result["result"].state is ConfigEntryState.LOADED
    assert len(mock_norman_api.clients) == 2
    validation_session = mock_norman_api.clients[0].session
    runtime_session = mock_norman_api.clients[1].session
    assert isinstance(validation_session.cookie_jar, aiohttp.DummyCookieJar)
    assert validation_session.closed
    mock_norman_api.clients[0].async_close.assert_awaited_once()
    assert isinstance(runtime_session.cookie_jar, aiohttp.DummyCookieJar)
    assert not runtime_session.closed
    assert await hass.config_entries.async_unload(result["result"].entry_id)
    await hass.async_block_till_done()


async def test_user_flow_uses_fallback_title_when_hub_name_is_missing(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """Create a safely named entry when optional hub metadata is absent."""
    mock_norman_api.hub_info.pop("hubName")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Norman Gen 1 Hub"
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(result["result"].entry_id)
    await hass.async_block_till_done()


async def test_reauth_updates_only_password(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Reauthentication should preserve connection data and update the password."""
    entry = setup_integration
    clients_before = len(mock_norman_api.clients)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    defaults = result["data_schema"]({})
    assert defaults[CONF_PASSWORD] == DEFAULT_PASSWORD

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "replacement"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data == {
        CONF_HOST: "192.0.2.10",
        CONF_PASSWORD: "replacement",
        CONF_APP_VERSION: DEFAULT_APP_VERSION,
    }
    assert entry.unique_id == "hub-1"
    assert entry.state is ConfigEntryState.LOADED
    assert len(mock_norman_api.clients) == clients_before + 2


async def test_reauth_with_unchanged_password_still_reloads(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Recover a loaded entry even when reauth data itself does not change."""
    entry = setup_integration
    clients_before = len(mock_norman_api.clients)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: DEFAULT_PASSWORD},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(mock_norman_api.clients) == clients_before + 2


async def test_reauth_recovers_on_same_flow_after_validation_error(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Keep reauthentication open after an error and accept a later retry."""
    entry = setup_integration
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    mock_norman_api.auth_error = InvalidAuth("bad password")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "replacement"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_norman_api.auth_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "replacement"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (InvalidAuth("bad password"), "invalid_auth"),
        (CannotConnect("offline"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_user_flow_recovers_after_client_error(
    hass: HomeAssistant,
    mock_norman_api,
    error: Exception,
    error_key: str,
) -> None:
    """Recover on the same public flow after every mapped client failure."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    mock_norman_api.auth_error = error

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error_key}

    mock_norman_api.auth_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED


async def test_user_flow_recovers_after_no_devices(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """Keep the form open when the endpoint has no usable devices."""
    rooms = mock_norman_api.rooms
    windows = mock_norman_api.windows
    mock_norman_api.rooms = []
    mock_norman_api.windows = []
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}

    mock_norman_api.rooms = rooms
    mock_norman_api.windows = windows
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_retries_one_rejected_session(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """Retry validation once when the hub rejects a transient session."""
    mock_norman_api.auth_errors = [InvalidSession("expired"), None]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_norman_api.auth_errors == []


async def test_user_flow_maps_two_rejected_sessions_to_connection_error(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """Stop retrying and keep the form open after a second session rejection."""
    mock_norman_api.auth_errors = [
        InvalidSession("expired"),
        InvalidSession("expired again"),
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "host",
    [
        "",
        "ftp://hub.local",
        "https://hub.local/",
        "http://hub.local/path",
        "http://hub.local?query=1",
        "http://hub.local#fragment",
        "hub.local:bad-port",
    ],
)
async def test_user_flow_rejects_unsafe_host_shapes(
    hass: HomeAssistant,
    mock_norman_api,
    host: str,
) -> None:
    """Accept only a bare host/port or root HTTP URL."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input(host)
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    assert mock_norman_api.clients == []


async def test_user_flow_normalizes_ipv6_and_blank_app_version(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """Canonicalize IPv6 and use the known app default for a blank value."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    user_input = _user_input("http://[2001:db8::1]:8080/")
    user_input[CONF_APP_VERSION] = ""

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "[2001:db8::1]:8080"
    assert result["data"][CONF_APP_VERSION] == DEFAULT_APP_VERSION
    assert all(
        client.app_version == DEFAULT_APP_VERSION for client in mock_norman_api.clients
    )


async def test_user_flow_rejects_invalid_host_then_recovers(
    hass: HomeAssistant,
    mock_norman_api,
) -> None:
    """Reject URL credentials and paths without calling the client."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("http://user:secret@hub.local/admin?x=1")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    assert mock_norman_api.clients == []

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("HUB.local:80")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "hub.local:80"


async def test_duplicate_hub_aborts_and_updates_host(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Keep one entry per physical hub while accepting a corrected host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("192.0.2.20")
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == "192.0.2.20"


async def test_duplicate_loaded_hub_validation_uses_runtime_lock(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Serialize duplicate detection with the already loaded hub client."""
    entry = setup_integration
    runtime_lock = entry.runtime_data.api.transaction_lock
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_norman_api.clients[-1].transaction_lock is runtime_lock


async def test_reauth_refuses_a_different_hub(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Never replace credentials with those for another physical hub."""
    entry = setup_integration
    mock_norman_api.hub_info["hubId"] = "hub-2"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "replacement"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hub"
    assert entry.data[CONF_PASSWORD] == DEFAULT_PASSWORD


async def test_reauth_upgrades_legacy_host_unique_id(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Replace a legacy host fallback with the hub's stable identity."""
    hass.config_entries.async_update_entry(
        mock_config_entry, unique_id=mock_config_entry.data[CONF_HOST]
    )
    old_hub_id, old_device_id, entity_ids = _add_legacy_registry_entries(
        hass, mock_config_entry
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": mock_config_entry.entry_id,
        },
        data=dict(mock_config_entry.data),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "replacement"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.unique_id == "hub-1"
    assert mock_config_entry.state is ConfigEntryState.LOADED
    _assert_legacy_registry_was_migrated(
        hass,
        mock_config_entry,
        old_hub_id,
        old_device_id,
        entity_ids,
    )


async def test_reconfigure_updates_same_hub_and_shares_runtime_lock(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Validate a new host without overlapping the live single-session client."""
    entry = setup_integration
    runtime_lock = entry.runtime_data.api.transaction_lock
    clients_before = len(mock_norman_api.clients)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    defaults = result["data_schema"]({})
    assert defaults[CONF_PASSWORD] == DEFAULT_PASSWORD
    assert defaults[CONF_HOST] == entry.data[CONF_HOST]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.0.2.20",
            CONF_PASSWORD: "replacement",
            CONF_APP_VERSION: "2.12.0",
        },
    )
    validation_client = mock_norman_api.clients[-2]
    assert validation_client.transaction_lock is runtime_lock
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {
        CONF_HOST: "192.0.2.20",
        CONF_PASSWORD: "replacement",
        CONF_APP_VERSION: "2.12.0",
    }
    assert entry.state is ConfigEntryState.LOADED
    assert len(mock_norman_api.clients) == clients_before + 2


async def test_reconfigure_recovers_on_same_flow_after_validation_error(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Keep reconfiguration open after an error and accept a later retry."""
    entry = setup_integration
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    mock_norman_api.auth_error = CannotConnect("offline")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_norman_api.auth_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_rejects_new_host_without_stable_response_identity(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Never move a stable entry when the new endpoint omits its hub ID."""
    entry = setup_integration
    mock_norman_api.hub_info.pop("hubId")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("192.0.2.20")
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "hub_identity_unavailable"
    assert entry.data[CONF_HOST] == "192.0.2.10"


async def test_reconfigure_refuses_different_hub(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Abort reconfiguration when the submitted endpoint has another hub ID."""
    entry = setup_integration
    mock_norman_api.hub_info["hubId"] = "hub-2"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("192.0.2.20")
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hub"


async def test_same_host_reconfigure_migrates_legacy_registry_identity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Learn a stable ID on reload without duplicating legacy registry objects."""
    hass.config_entries.async_update_entry(
        mock_config_entry, unique_id=mock_config_entry.data[CONF_HOST]
    )
    old_hub_id, old_device_id, entity_ids = _add_legacy_registry_entries(
        hass, mock_config_entry
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": mock_config_entry.entry_id,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input()
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == "hub-1"
    assert mock_config_entry.state is ConfigEntryState.LOADED
    _assert_legacy_registry_was_migrated(
        hass,
        mock_config_entry,
        old_hub_id,
        old_device_id,
        entity_ids,
    )


async def test_reconfigure_cannot_move_unverified_legacy_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Do not claim a new address is the same hub without a learned hub ID."""
    hass.config_entries.async_update_entry(
        mock_config_entry, unique_id=mock_config_entry.data[CONF_HOST]
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": mock_config_entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("192.0.2.20")
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "hub_identity_unavailable"


async def test_reconfigure_invalid_host_keeps_form_open(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_norman_api,
) -> None:
    """Reject unsafe connection data without changing a loaded entry."""
    entry = setup_integration
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input("https://user:secret@hub.local/path")
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    assert entry.data[CONF_HOST] == "192.0.2.10"


@pytest.mark.parametrize("source", [SOURCE_REAUTH, SOURCE_RECONFIGURE])
async def test_entry_flow_aborts_if_entry_was_removed(
    hass: HomeAssistant,
    source: str,
) -> None:
    """Abort reauth/reconfigure cleanly if the target entry disappeared."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": source, "entry_id": "missing-entry"},
        data={} if source == SOURCE_REAUTH else None,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_entry_missing"
