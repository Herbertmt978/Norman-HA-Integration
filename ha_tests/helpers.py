"""Registry assertions shared across supported Home Assistant versions."""

from homeassistant.helpers import device_registry as dr

from custom_components.norman_gen1.const import DOMAIN


def find_device(
    registry: dr.DeviceRegistry, entry_id: str, identifier: str
) -> dr.DeviceEntry | None:
    """Find a Norman device within its config entry, not globally."""
    return next(
        (
            device
            for device in dr.async_entries_for_config_entry(registry, entry_id)
            if (DOMAIN, identifier) in device.identifiers
        ),
        None,
    )
