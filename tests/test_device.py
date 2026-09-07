"""Device links across Home Assistant's device-registry API change."""

from types import SimpleNamespace

import pytest

from custom_components.norman_gen1 import device
from custom_components.norman_gen1.api import NormanRoom
from custom_components.norman_gen1.const import DOMAIN


@pytest.mark.parametrize("modern", [False, True])
def test_room_links_to_registered_hub(monkeypatch, modern):
    """Use the scoped registry ID on new HA, the legacy identifier on old HA."""
    monkeypatch.setattr(device, "_SUPPORTS_VIA_DEVICE_ID", modern)
    api = SimpleNamespace(hub_id="hub-1")
    room = NormanRoom(id=1, name="Living room", group_names=[], raw={})
    info = device.room_device_info(api, room, "hub-registry-id")
    assert info["identifiers"] == {(DOMAIN, "hub-1_room_1")}
    if modern:
        assert info["via_device_id"] == "hub-registry-id"
        assert "via_device" not in info
    else:
        assert info["via_device"] == (DOMAIN, "hub-1")
        assert "via_device_id" not in info


def test_modern_room_requires_registered_hub(monkeypatch):
    """Never silently discard the hub relationship on the modern API."""
    monkeypatch.setattr(device, "_SUPPORTS_VIA_DEVICE_ID", True)
    room = NormanRoom(id=1, name="Living room", group_names=[], raw={})
    with pytest.raises(ValueError, match="Register the Norman hub"):
        device.room_device_info(SimpleNamespace(hub_id="hub-1"), room, None)
