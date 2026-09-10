"""ShadeAuto lifecycle, adapted from keito's Apache-2.0 integration."""

from __future__ import annotations

from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NormanApiClient, NormanApiError, NormanConnectionError
from .coordinator import NormanCoordinator

PLATFORMS = [Platform.COVER]


def gen2_coordinator(entry: ConfigEntry) -> NormanCoordinator:
    """Return the runtime belonging to a ShadeAuto entry."""
    return cast(NormanCoordinator, entry.runtime_data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start ShadeAuto and release resources if any setup stage fails."""
    api = NormanApiClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = NormanCoordinator(hass, api, entry)
    try:
        await api.async_validate_connection()
        _validate_identity(entry, api)
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_create_background_task(
            hass, coordinator.listen_notifications(), "ShadeAuto notifications"
        )
    except (NormanApiError, NormanConnectionError) as err:
        await coordinator.async_shutdown()
        await api.async_close()
        raise ConfigEntryNotReady("Cannot connect to ShadeAuto hub") from err
    except BaseException:
        await coordinator.async_shutdown()
        await api.async_close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload ShadeAuto platforms and close the notification connection."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = gen2_coordinator(entry)
    await coordinator.async_shutdown()
    await coordinator.api.async_close()
    return True


def _validate_identity(entry: ConfigEntry, api: NormanApiClient) -> None:
    """Reject a replacement hub at an existing address."""
    if entry.unique_id != f"gen2_{api.thing_name}":
        raise ConfigEntryError("The address belongs to a different ShadeAuto hub")
