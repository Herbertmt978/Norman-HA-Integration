"""Config, reauthentication, reconfiguration, and options flows."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import math
from typing import Any, cast
from urllib.parse import urlsplit

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
import voluptuous as vol

from .api import (
    CannotConnect,
    HubNeedsRestart,
    InvalidAuth,
    InvalidSession,
    NoDevicesFound,
    NormanGen1Api,
    NormanRoom,
    NormanWindow,
    PositionProfile,
    group_target_id,
    room_target_id,
)
from .const import (
    CONF_APP_VERSION,
    CONF_CLOSE_POSITION,
    CONF_DEFAULT_CLOSE_POSITION,
    CONF_DEFAULT_OPEN_POSITION,
    CONF_INHERIT,
    CONF_LEGACY_PROFILE_MIGRATION,
    CONF_OPEN_POSITION,
    CONF_POSITION_PROFILES,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_SIMULTANEOUS_ROOMS,
    CONF_TARGET,
    CONF_TILT_OPEN_TARGETS,
    DEFAULT_APP_VERSION,
    DEFAULT_PASSWORD,
    DOMAIN,
)
from .helpers import clean_label, group_name
from .profiles import (
    make_position_profile,
    profile_as_options,
    resolve_configured_profile,
    resolve_default_profile,
    stored_position_profiles,
)
from .session import async_create_norman_session

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
POSITION_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=100,
        step=1,
        mode=selector.NumberSelectorMode.BOX,
    )
)
CLOSE_POSITION_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=["0", "100"],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


async def _validate_input(
    hass: HomeAssistant,
    data: dict[str, Any],
    transaction_lock: asyncio.Lock | None = None,
) -> dict[str, Any]:
    session = async_create_norman_session(hass, auto_cleanup=False)
    api = NormanGen1Api(
        session,
        data[CONF_HOST],
        data[CONF_PASSWORD],
        data.get(CONF_APP_VERSION) or DEFAULT_APP_VERSION,
        transaction_lock=transaction_lock
        or _runtime_transaction_lock(hass, data[CONF_HOST]),
    )
    try:
        try:
            info, rooms, windows = await _fetch_validation_snapshot(api)
        except InvalidSession:
            try:
                info, rooms, windows = await _fetch_validation_snapshot(api)
            except InvalidSession as err:
                raise CannotConnect(
                    "Hub repeatedly rejected the validation session"
                ) from err
        if not rooms and not any(window.room_id >= 0 for window in windows):
            raise NoDevicesFound(
                "Hub responded, but no rooms or shutter devices were discovered"
            )
        return info
    finally:
        try:
            await api.async_close()
        finally:
            session.detach()


async def _fetch_validation_snapshot(
    api: NormanGen1Api,
) -> tuple[dict[str, Any], list[NormanRoom], list[NormanWindow]]:
    """Fetch the identity and discovery data for one validation session."""
    async with api.authenticated_session() as info:
        rooms = await api.get_rooms()
        windows = await api.get_windows()
    return info, rooms, windows


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Norman Gen 1 Hub."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow for a configured hub."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate a user-supplied local hub and create its config entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                user_input[CONF_HOST] = _normalize_host(user_input[CONF_HOST])
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                user_input[CONF_APP_VERSION] = (
                    user_input.get(CONF_APP_VERSION) or DEFAULT_APP_VERSION
                )
                info, error = await _validate_for_flow(self.hass, user_input)
                if error is not None:
                    errors["base"] = error
                else:
                    info = cast(dict[str, Any], info)
                    hub_id = str(info.get("hubId") or user_input[CONF_HOST])
                    await self.async_set_unique_id(hub_id)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: user_input[CONF_HOST]},
                        reload_on_update=False,
                    )
                    hub_name = info.get("hubName")
                    return self.async_create_entry(
                        title=clean_label(hub_name)
                        if isinstance(hub_name, str)
                        else "Norman Gen 1 Hub",
                        data=user_input,
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(
                    CONF_PASSWORD, default=DEFAULT_PASSWORD
                ): PASSWORD_SELECTOR,
                vol.Optional(CONF_APP_VERSION, default=DEFAULT_APP_VERSION): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for a hub that rejected its saved password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and store a replacement local hub password."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            info, error = await _validate_for_flow(
                self.hass,
                data,
                _entry_transaction_lock(entry),
            )
            if error is not None:
                errors["base"] = error
            else:
                info = cast(dict[str, Any], info)
                hub_id = str(info.get("hubId") or data[CONF_HOST])
                if entry.unique_id is not None and entry.unique_id not in {
                    hub_id,
                    entry.data[CONF_HOST],
                }:
                    return self.async_abort(reason="wrong_hub")
                return self._update_entry_and_abort(
                    entry, hub_id, data, "reauth_successful"
                )

        schema = vol.Schema(
            {vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): PASSWORD_SELECTOR}
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate changed connection settings without allowing a different hub."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = _normalize_host(user_input[CONF_HOST])
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                user_input[CONF_APP_VERSION] = (
                    user_input.get(CONF_APP_VERSION) or DEFAULT_APP_VERSION
                )
                info, error = await _validate_for_flow(
                    self.hass,
                    user_input,
                    _entry_transaction_lock(entry),
                )
                if error is not None:
                    errors["base"] = error
                else:
                    info = cast(dict[str, Any], info)
                    hub_id = str(info.get("hubId") or user_input[CONF_HOST])
                    if (
                        entry.unique_id in (None, entry.data[CONF_HOST])
                        and user_input[CONF_HOST] != entry.data[CONF_HOST]
                    ):
                        return self.async_abort(reason="hub_identity_unavailable")
                    if (
                        not info.get("hubId")
                        and user_input[CONF_HOST] != entry.data[CONF_HOST]
                    ):
                        return self.async_abort(reason="hub_identity_unavailable")
                    if entry.unique_id is not None and entry.unique_id not in {
                        hub_id,
                        entry.data[CONF_HOST],
                    }:
                        return self.async_abort(reason="wrong_hub")
                    return self._update_entry_and_abort(
                        entry,
                        hub_id,
                        user_input,
                        "reconfigure_successful",
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(
                    CONF_PASSWORD, default=DEFAULT_PASSWORD
                ): PASSWORD_SELECTOR,
                vol.Optional(
                    CONF_APP_VERSION,
                    default=entry.data.get(CONF_APP_VERSION, DEFAULT_APP_VERSION),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    def _update_entry_and_abort(
        self,
        entry: config_entries.ConfigEntry,
        unique_id: str,
        data: dict[str, Any],
        reason: str,
    ) -> ConfigFlowResult:
        """Update an entry and reload it exactly once."""
        stored_unique_id = (
            entry.unique_id
            if entry.unique_id in (None, entry.data[CONF_HOST])
            else unique_id
        )
        changed = self.hass.config_entries.async_update_entry(
            entry,
            unique_id=stored_unique_id,
            data=data,
        )
        if not changed or not entry.update_listeners:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason=reason)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Norman Gen 1 options."""

    _selected_target: str | None = None
    _selected_target_name: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which movement-profile settings to edit."""
        entry = self._entry
        if entry.version < 2 or any(
            key in entry.options
            for key in (
                CONF_LEGACY_PROFILE_MIGRATION,
                CONF_TILT_OPEN_TARGETS,
                CONF_REVERSED_CLOSE_TARGETS,
            )
        ):
            return self.async_abort(reason="migration_pending")

        menu_options = ["defaults"]
        if _target_choices(entry):
            menu_options.append("target")
        if _room_choices(entry):
            menu_options.append("room_commands")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_room_commands(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose rooms allowed to use a simultaneous room command."""
        choices = _room_choices(self._entry)
        if not choices:
            return self.async_abort(reason="no_rooms")

        stored = _stored_simultaneous_rooms(self._entry.options)
        current = [target for target in choices if target in stored]
        if user_input is not None:
            selected = set(user_input[CONF_SIMULTANEOUS_ROOMS])
            updated = dict(self._entry.options)
            retained = [target for target in stored if target not in choices]
            updated[CONF_SIMULTANEOUS_ROOMS] = retained + [
                target for target in choices if target in selected
            ]
            return self.async_create_entry(data=updated)

        return self.async_show_form(
            step_id="room_commands",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SIMULTANEOUS_ROOMS, default=current
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=value, label=label)
                                for value, label in choices.items()
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the global raw Open and Closed positions."""
        errors: dict[str, str] = {}
        current = resolve_default_profile(self._entry.options)
        open_default: float = current.open_position
        close_default = str(current.close_position)

        if user_input is not None:
            open_default = user_input[CONF_DEFAULT_OPEN_POSITION]
            close_default = str(user_input[CONF_DEFAULT_CLOSE_POSITION])
            open_position = _whole_position(open_default)
            if open_position is None:
                errors[CONF_DEFAULT_OPEN_POSITION] = "whole_number"
            else:
                close_position = int(close_default)
                try:
                    profile = make_position_profile(open_position, close_position)
                except ValueError:
                    errors["base"] = (
                        "positions_must_differ"
                        if open_position == close_position
                        else "invalid_position"
                    )
                else:
                    options = dict(self._entry.options)
                    options[CONF_DEFAULT_OPEN_POSITION] = profile.open_position
                    options[CONF_DEFAULT_CLOSE_POSITION] = profile.close_position
                    options[CONF_POSITION_PROFILES] = stored_position_profiles(options)
                    return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="defaults",
            data_schema=_profile_schema(
                open_key=CONF_DEFAULT_OPEN_POSITION,
                close_key=CONF_DEFAULT_CLOSE_POSITION,
                open_default=open_default,
                close_default=close_default,
            ),
            errors=errors,
        )

    async def async_step_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a discovered or stored room/panel target."""
        choices = _target_choices(self._entry)
        if not choices:
            return self.async_abort(reason="no_targets")
        if user_input is not None:
            target = str(user_input[CONF_TARGET])
            self._selected_target = target
            self._selected_target_name = choices[target]
            return await self.async_step_profile()

        return self.async_show_form(
            step_id="target",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TARGET): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=value, label=label)
                                for value, label in choices.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit or inherit the selected room/panel profile."""
        target = cast(str, self._selected_target)
        options = self._entry.options
        profiles = stored_position_profiles(options)
        exact_profile = profiles.get(target)
        inherited_profile = _inherited_profile(options, target)
        current = (
            make_position_profile(
                exact_profile[CONF_OPEN_POSITION],
                exact_profile[CONF_CLOSE_POSITION],
            )
            if exact_profile is not None
            else inherited_profile
        )
        inherit_default = exact_profile is None
        open_default: float = current.open_position
        close_default = str(current.close_position)
        errors: dict[str, str] = {}

        if user_input is not None:
            inherit_default = bool(user_input[CONF_INHERIT])
            open_default = user_input[CONF_OPEN_POSITION]
            close_default = str(user_input[CONF_CLOSE_POSITION])
            if inherit_default:
                profiles.pop(target, None)
                updated = dict(options)
                updated[CONF_POSITION_PROFILES] = profiles
                return self.async_create_entry(data=updated)

            open_position = _whole_position(open_default)
            if open_position is None:
                errors[CONF_OPEN_POSITION] = "whole_number"
            else:
                close_position = int(close_default)
                try:
                    profile = make_position_profile(open_position, close_position)
                except ValueError:
                    errors["base"] = (
                        "positions_must_differ"
                        if open_position == close_position
                        else "invalid_position"
                    )
                else:
                    profiles[target] = profile_as_options(profile)
                    updated = dict(options)
                    updated[CONF_POSITION_PROFILES] = profiles
                    return self.async_create_entry(data=updated)

        schema = _profile_schema(
            open_key=CONF_OPEN_POSITION,
            close_key=CONF_CLOSE_POSITION,
            open_default=open_default,
            close_default=close_default,
            inherit_default=inherit_default,
        )
        return self.async_show_form(
            step_id="profile",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "target_name": self._selected_target_name or target,
            },
        )

    @property
    def _entry(self) -> config_entries.ConfigEntry:
        """Return the flow's entry on both minimum and current Home Assistant."""
        return cast(
            config_entries.ConfigEntry,
            self.hass.config_entries.async_get_entry(self.handler),
        )


def _target_choices(entry: config_entries.ConfigEntry) -> dict[str, str]:
    coordinator = getattr(entry, "runtime_data", None)
    choices: dict[str, str] = {}
    if coordinator is not None and coordinator.data is not None:
        levels_by_room: dict[int, list[int]] = coordinator.data.levels_by_room
        for room in _discovered_rooms(entry):
            room_label = clean_label(room.name)
            choices[room_target_id(room.id)] = room_label
            levels = levels_by_room.get(room.id, [])
            for level in levels:
                group_label = clean_label(group_name(room.group_names, level, levels))
                choices[group_target_id(room.id, level)] = (
                    f"{room_label} / {group_label}"
                )

    for target in stored_position_profiles(entry.options):
        choices.setdefault(target, target)
    return choices


def _room_choices(entry: config_entries.ConfigEntry) -> dict[str, str]:
    """Return stable option IDs and labels for currently discovered rooms."""
    return {
        room_target_id(room.id): clean_label(room.name)
        for room in _discovered_rooms(entry)
    }


def _stored_simultaneous_rooms(options: Mapping[str, Any]) -> list[str]:
    """Return valid saved room targets while preserving their stored order."""
    raw_targets = options.get(CONF_SIMULTANEOUS_ROOMS)
    if not isinstance(raw_targets, list):
        return []
    targets: list[str] = []
    for target in raw_targets:
        if not isinstance(target, str) or target in targets:
            continue
        prefix, separator, raw_room_id = target.partition(":")
        if prefix != "room" or separator != ":":
            continue
        try:
            room_id = int(raw_room_id)
        except ValueError:
            continue
        if room_id >= 0:
            targets.append(room_target_id(room_id))
    return targets


def _discovered_rooms(entry: config_entries.ConfigEntry) -> list[NormanRoom]:
    """Return the entry's live rooms in deterministic display order."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return []
    rooms: list[NormanRoom] = coordinator.data.rooms
    return sorted(rooms, key=lambda item: clean_label(item.name))


def _profile_schema(
    *,
    open_key: str,
    close_key: str,
    open_default: float,
    close_default: str,
    inherit_default: bool | None = None,
) -> vol.Schema:
    fields: dict[vol.Marker, Any] = {}
    if inherit_default is not None:
        fields[vol.Required(CONF_INHERIT, default=inherit_default)] = (
            selector.BooleanSelector()
        )
    fields[vol.Required(open_key, default=open_default)] = POSITION_SELECTOR
    fields[vol.Required(close_key, default=close_default)] = CLOSE_POSITION_SELECTOR
    return vol.Schema(fields)


def _whole_position(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    position = int(numeric)
    return position if 0 <= position <= 100 else None


def _inherited_profile(options: Mapping[str, Any], target: str) -> PositionProfile:
    profiles = stored_position_profiles(options)
    profiles.pop(target, None)
    inherited_options = {**options, CONF_POSITION_PROFILES: profiles}
    parts = target.split(":")
    try:
        if len(parts) == 2 and parts[0] == "room":
            return resolve_configured_profile(inherited_options, int(parts[1]))
        if len(parts) == 3 and parts[0] == "group":
            return resolve_configured_profile(
                inherited_options,
                int(parts[1]),
                int(parts[2]),
            )
    except ValueError:
        pass
    return resolve_default_profile(inherited_options)


async def _validate_for_flow(
    hass: HomeAssistant,
    data: dict[str, Any],
    transaction_lock: asyncio.Lock | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate flow data and return a Home Assistant error key when needed."""
    try:
        return await _validate_input(hass, data, transaction_lock), None
    except HubNeedsRestart:
        return None, "hub_needs_restart"
    except CannotConnect:
        return None, "cannot_connect"
    except InvalidAuth:
        return None, "invalid_auth"
    except NoDevicesFound:
        return None, "no_devices"
    except Exception:
        _LOGGER.exception("Unexpected exception validating Norman Gen 1 hub")
        return None, "unknown"


def _normalize_host(host: str) -> str:
    """Return a canonical IP/hostname and reject unsafe URL components."""
    value = host.strip()
    if not value:
        raise ValueError("Host is empty")
    parsed = urlsplit(value if "://" in value else f"//{value}")
    if parsed.scheme and parsed.scheme.lower() != "http":
        raise ValueError("Unsupported URL scheme")
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Host must not include credentials, a path, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError("Invalid port") from err
    hostname = parsed.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{hostname}:{port}" if port is not None else hostname


def _runtime_transaction_lock(hass: HomeAssistant, host: str) -> asyncio.Lock | None:
    """Reuse the live client's lock when validating an already configured hub."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None and coordinator.api.host == host:
            return cast(asyncio.Lock, coordinator.api.transaction_lock)
    return None


def _entry_transaction_lock(entry: config_entries.ConfigEntry) -> asyncio.Lock | None:
    """Return an entry's live hub lock, including while its host is changing."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return None
    return cast(asyncio.Lock, coordinator.api.transaction_lock)
