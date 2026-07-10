"""The Norman Gen 1 Hub integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .api import NormanGen1Api
from .const import (
    CONF_APP_VERSION,
    CONF_LEGACY_PROFILE_MIGRATION,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
    DEFAULT_APP_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import NormanConfigEntry, NormanDataUpdateCoordinator
from .device import hub_device_info
from .profiles import migrate_legacy_profile_options
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
    _migrate_profile_options(hass, entry, coordinator)
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


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
) -> bool:
    """Mark v0.2 entries for profile migration after hub discovery."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            version=2,
            options={
                **entry.options,
                CONF_LEGACY_PROFILE_MIGRATION: True,
            },
        )
    return True


@callback
def _migrate_profile_options(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
    coordinator: NormanDataUpdateCoordinator,
) -> None:
    """Initialize numeric options or preserve a v0.2 entry's exact profile."""
    options = dict(entry.options)
    needs_legacy_migration = options.pop(
        CONF_LEGACY_PROFILE_MIGRATION, False
    ) is True or any(
        key in options for key in (CONF_TILT_OPEN_TARGETS, CONF_REVERSED_CLOSE_TARGETS)
    )
    migrated = migrate_legacy_profile_options(
        options,
        coordinator.data.rooms if needs_legacy_migration else [],
        coordinator.data.levels_by_room if needs_legacy_migration else {},
    )
    if migrated != entry.options:
        hass.config_entries.async_update_entry(entry, options=migrated)


async def _async_migrate_registry_identity(
    hass: HomeAssistant,
    entry: NormanConfigEntry,
    old_hub_id: str,
    new_hub_id: str,
) -> None:
    """Preserve legacy entity IDs and devices while learning a stable hub ID."""
    device_registry = dr.async_get(hass)
    old_room_prefix = f"{old_hub_id}_room_"
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identifiers = set(device.identifiers)
        migrated_identifiers: set[tuple[str, str]] = set()
        changed = False
        for domain, identifier in identifiers:
            if domain != DOMAIN:
                migrated_identifiers.add((domain, identifier))
                continue
            if identifier == old_hub_id:
                migrated_identifiers.add((DOMAIN, new_hub_id))
                changed = True
                continue
            if identifier.startswith(old_room_prefix):
                migrated_identifiers.add(
                    (DOMAIN, f"{new_hub_id}{identifier[len(old_hub_id) :]}")
                )
                changed = True
                continue
            migrated_identifiers.add((domain, identifier))
        if not changed:
            continue
        device_registry.async_update_device(
            device.id,
            new_identifiers=migrated_identifiers,
        )

    old_prefix = f"{old_hub_id}_"

    @callback
    def migrate_entity(entity: er.RegistryEntry) -> dict[str, str] | None:
        if not entity.unique_id.startswith(old_prefix):
            return None
        return {"new_unique_id": f"{new_hub_id}{entity.unique_id[len(old_hub_id) :]}"}

    await er.async_migrate_entries(hass, entry.entry_id, migrate_entity)
