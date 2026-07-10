"""The Norman Gen 1 Hub integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .api import NormanGen1Api, group_target_id, room_target_id
from .const import (
    CONF_APP_VERSION,
    CONF_KNOWN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
    DEFAULT_APP_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import NormanConfigEntry, NormanDataUpdateCoordinator
from .device import hub_device_info
from .session import async_create_norman_session

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: NormanConfigEntry) -> bool:
    """Set up a Norman Gen 1 hub from a config entry."""
    session = async_create_norman_session(hass)
    host = entry.data[CONF_HOST]
    expected_hub_id = entry.unique_id if entry.unique_id not in (None, host) else None
    api = NormanGen1Api(
        session,
        host,
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_APP_VERSION) or DEFAULT_APP_VERSION,
        expected_hub_id=expected_hub_id,
    )

    coordinator = NormanDataUpdateCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()

    if entry.unique_id is not None and entry.unique_id not in {api.hub_id, host}:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="wrong_hub",
        )
    if entry.unique_id in (None, host) and api.hub_id != entry.unique_id:
        await _async_migrate_registry_identity(hass, entry, host, api.hub_id)
        hass.config_entries.async_update_entry(entry, unique_id=api.hub_id)
    api.pin_hub_id(api.hub_id)
    _migrate_legacy_options(hass, entry, coordinator)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **hub_device_info(api),
    )

    _LOGGER.info(
        "Discovered Norman Gen 1 hub with %s room(s), %s shutter device(s), and %s group(s)",
        len(coordinator.data.rooms),
        len(coordinator.data.windows),
        sum(len(levels) for levels in coordinator.data.levels_by_room.values()),
    )

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_options(hass: HomeAssistant, entry: NormanConfigEntry) -> None:
    """Reload the config entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NormanConfigEntry) -> bool:
    """Unload a Norman Gen 1 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        await coordinator.async_shutdown()
        await coordinator.api.async_close()
    return unload_ok


@callback
def _migrate_legacy_options(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
    coordinator: NormanDataUpdateCoordinator,
) -> None:
    """Snapshot targets from pre-0.2 options before dynamic discovery begins."""
    if CONF_KNOWN_TARGETS in entry.options or not any(
        key in entry.options
        for key in (CONF_TILT_OPEN_TARGETS, CONF_REVERSED_CLOSE_TARGETS)
    ):
        return

    known_targets = {room_target_id(room.id) for room in coordinator.data.rooms}
    known_targets.update(
        group_target_id(room_id, level)
        for room_id, levels in coordinator.data.levels_by_room.items()
        for level in levels
    )
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_KNOWN_TARGETS: sorted(known_targets)},
    )


async def _async_migrate_registry_identity(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
    old_hub_id: str,
    new_hub_id: str,
) -> None:
    """Preserve legacy entity IDs and devices while learning a stable hub ID."""
    device_registry = dr.async_get(hass)
    old_identifier = (DOMAIN, old_hub_id)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if old_identifier not in device.identifiers:
            continue
        device_registry.async_update_device(
            device.id,
            new_identifiers=(device.identifiers - {old_identifier})
            | {(DOMAIN, new_hub_id)},
        )

    old_prefix = f"{old_hub_id}_room_"

    @callback
    def migrate_entity(entity: er.RegistryEntry) -> dict[str, str] | None:
        if not entity.unique_id.startswith(old_prefix):
            return None
        return {"new_unique_id": f"{new_hub_id}{entity.unique_id[len(old_hub_id) :]}"}

    await er.async_migrate_entries(hass, entry.entry_id, migrate_entity)
