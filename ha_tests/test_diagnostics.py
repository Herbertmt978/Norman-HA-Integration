"""Test privacy-safe diagnostics from a loaded Norman entry."""

from homeassistant.core import HomeAssistant
import pytest

from custom_components.norman_gen1.const import (
    CONF_KNOWN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
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
            CONF_TILT_OPEN_TARGETS: ["room:1"],
            CONF_REVERSED_CLOSE_TARGETS: ["group:1:1"],
            CONF_KNOWN_TARGETS: ["room:1", "group:1:1"],
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
        "tilt_open_target_count": 1,
        "reversed_close_target_count": 1,
        "known_target_count": 2,
    }
