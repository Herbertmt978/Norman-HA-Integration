"""Exercise Gen2 room diagnostics through Home Assistant's download endpoint."""

from copy import deepcopy
import json

from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest

from . import test_gen2
from .test_gen2 import DEVICES, entry

shadeauto = test_gen2.shadeauto

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_public_diagnostics_download_reports_room_identity_counts(
    hass, hass_client, shadeauto
):
    """Download diagnostics reports safe counts without room names or raw IDs."""
    discovery = deepcopy(DEVICES)
    discovery["results"]["RoomList"] = [
        {
            "RoomID": "1",
            "RoomName": "private-valid-room-name",
            "GroupList": [
                {
                    "GroupID": 1,
                    "PeripheralList": [
                        {"PeripheralUID": 7, "PeripheralName": "Private shade"}
                    ],
                }
            ],
        },
        {"RoomID": None, "RoomName": "private-null-room-name", "GroupList": []},
        {"RoomID": True, "GroupList": []},
        {"RoomID": 1.5, "GroupList": []},
        {"RoomID": "private-raw-room-id", "GroupList": []},
        {"RoomName": "private-missing-room-name", "GroupList": []},
        {"RoomID": [], "GroupList": []},
        {"RoomID": {"private": "payload"}, "GroupList": []},
    ]
    shadeauto.async_get_devices.return_value = discovery
    configured = entry(hass)
    assert await hass.config_entries.async_setup(configured.entry_id)
    await hass.async_block_till_done()

    assert await async_setup_component(hass, "diagnostics", {})
    await hass.async_block_till_done()
    client = await hass_client()
    response = await client.get(f"/api/diagnostics/config_entry/{configured.entry_id}")
    assert response.status == 200
    payload = await response.json()
    room_discovery = payload["data"]["room_discovery"]
    assert room_discovery == {
        "raw_room_count": 8,
        "unique_room_id_count": 1,
        "usable_room_count": 1,
        "rejected_room_count": 7,
        "room_id_type_counts": {
            "NoneType": 1,
            "bool": 1,
            "dict": 1,
            "float": 1,
            "list": 1,
            "missing": 1,
            "str": 2,
        },
        "rejection_reason_counts": {
            "boolean": 1,
            "float": 1,
            "malformed": 1,
            "missing": 1,
            "null": 1,
            "unsupported_type": 2,
        },
    }
    rendered = json.dumps(payload)
    assert "private-valid-room-name" not in rendered
    assert "private-null-room-name" not in rendered
    assert "private-raw-room-id" not in rendered
    assert '"payload"' not in rendered
    assert "Private shade" not in rendered
    assert "gen2_shade-hub_7" in [
        item.unique_id
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), configured.entry_id
        )
    ]
