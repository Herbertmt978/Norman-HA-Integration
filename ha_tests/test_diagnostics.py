"""Test privacy-safe diagnostics from a loaded Norman entry."""

from homeassistant.core import HomeAssistant
import pytest

from custom_components.norman_gen1.const import (
    CONF_CLOSE_POSITION,
    CONF_DEFAULT_CLOSE_POSITION,
    CONF_DEFAULT_OPEN_POSITION,
    CONF_OPEN_POSITION,
    CONF_POSITION_PROFILES,
)
from custom_components.norman_gen1.diagnostics import async_get_config_entry_diagnostics

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_diagnostics_redact_config_and_whitelist_hub_fields(
    hass: HomeAssistant,
    mock_config_entry,
    mock_norman_api,
) -> None:
    """Never expose credentials, identity, or unknown login payload fields."""
    entry = mock_config_entry
    hass.config_entries.async_update_entry(
        entry,
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
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.api.hub_info.update(
        {
            "hubId": "private-hub-id",
            "hubName": "Private home",
            "unknownToken": "private-token",
            "serialNumber": "private-serial",
            "status": {"password": "nested-private-value"},
        }
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    rendered = repr(result)

    assert "192.0.2.10" not in rendered
    assert "123456789" not in rendered
    assert "private-hub-id" not in rendered
    assert "Private home" not in rendered
    assert "private-token" not in rendered
    assert "private-serial" not in rendered
    assert "nested-private-value" not in rendered
    assert "room:1" not in rendered
    assert "group:1:1" not in rendered
    assert result["hub"] == {"swVer": "1.0"}
    assert result["snapshot"]["room_count"] == 1
    assert result["snapshot"]["window_count"] == 1
    assert result["options"] == {
        "default_open_position": 37,
        "default_close_position": 100,
        "profile_override_count": 2,
        "simultaneous_room_ids": [],
    }
