"""Home Assistant device descriptors for Norman hubs and rooms."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .api import NormanGen1Api, NormanRoom
from .const import DOMAIN, MANUFACTURER
from .helpers import clean_label


def hub_device_identifier(hub_id: str) -> tuple[str, str]:
    """Return the stable Home Assistant identifier for a Norman hub."""
    return (DOMAIN, str(hub_id))


def room_device_identifier(hub_id: str, room_id: int) -> tuple[str, str]:
    """Return the stable Home Assistant identifier for one Norman room."""
    return (DOMAIN, f"{hub_id}_room_{int(room_id)}")


def hub_device_info(api: NormanGen1Api) -> DeviceInfo:
    """Return the physical Norman hub's device information."""
    hub_name = api.hub_info.get("hubName")
    name = (
        f"Norman Hub {clean_label(hub_name)}"
        if isinstance(hub_name, str) and hub_name.strip()
        else "Norman Gen 1 Hub"
    )
    sw_version = api.hub_info.get("swVer")
    return DeviceInfo(
        identifiers={hub_device_identifier(api.hub_id)},
        name=name,
        manufacturer=MANUFACTURER,
        model="Gen 1 Hub",
        sw_version=sw_version if isinstance(sw_version, str) else None,
        configuration_url=f"http://{api.host}/",
    )


def room_device_info(api: NormanGen1Api, room: NormanRoom) -> DeviceInfo:
    """Return one logical room device routed through the Norman hub."""
    room_name = clean_label(room.name)
    return DeviceInfo(
        identifiers={room_device_identifier(api.hub_id, room.id)},
        name=room_name,
        manufacturer=MANUFACTURER,
        model="Room",
        suggested_area=room_name,
        via_device=hub_device_identifier(api.hub_id),
    )
