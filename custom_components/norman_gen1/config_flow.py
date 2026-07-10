"""Config, reauthentication, reconfiguration, and options flows."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import logging
from typing import Any, cast
from urllib.parse import urlsplit

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .api import (
    CannotConnect,
    InvalidAuth,
    InvalidSession,
    NoDevicesFound,
    NormanGen1Api,
    NormanRoom,
    NormanWindow,
    group_target_id,
    room_close_position,
    room_open_position,
    room_target_id,
)
from .const import (
    CONF_APP_VERSION,
    CONF_KNOWN_TARGETS,
    CONF_REVERSED_CLOSE_TARGETS,
    CONF_TILT_OPEN_TARGETS,
    DEFAULT_APP_VERSION,
    DEFAULT_PASSWORD,
    DOMAIN,
)
from .helpers import clean_label, group_name
from .session import async_create_norman_session

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
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

    VERSION = 1

    @staticmethod
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure per-room and per-panel shutter movement profiles."""
        entry = self._entry
        choices, tilt_defaults, reversed_defaults = _target_choices(self.hass, entry)

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_TILT_OPEN_TARGETS: list(
                        user_input.get(CONF_TILT_OPEN_TARGETS, [])
                    ),
                    CONF_REVERSED_CLOSE_TARGETS: list(
                        user_input.get(CONF_REVERSED_CLOSE_TARGETS, [])
                    ),
                    CONF_KNOWN_TARGETS: sorted(choices),
                },
            )

        tilt_targets = entry.options.get(CONF_TILT_OPEN_TARGETS, tilt_defaults)
        reversed_targets = entry.options.get(
            CONF_REVERSED_CLOSE_TARGETS, reversed_defaults
        )
        _add_unknown_targets(choices, tilt_targets)
        _add_unknown_targets(choices, reversed_targets)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TILT_OPEN_TARGETS, default=list(tilt_targets)
                ): cv.multi_select(choices),
                vol.Optional(
                    CONF_REVERSED_CLOSE_TARGETS, default=list(reversed_targets)
                ): cv.multi_select(choices),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    @property
    def _entry(self) -> config_entries.ConfigEntry:
        """Return the flow's entry on both minimum and current Home Assistant."""
        return cast(
            config_entries.ConfigEntry,
            self.hass.config_entries.async_get_entry(self.handler),
        )


def _target_choices(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
) -> tuple[dict[str, str], list[str], list[str]]:
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return {}, [], []

    rooms: list[NormanRoom] = coordinator.data.rooms
    levels_by_room: dict[int, list[int]] = coordinator.data.levels_by_room
    choices: dict[str, str] = {}
    tilt_defaults: list[str] = []
    reversed_defaults: list[str] = []

    for room in sorted(rooms, key=lambda item: clean_label(item.name)):
        room_label = clean_label(room.name)
        room_key = room_target_id(room.id)
        choices[room_key] = f"{room_label} (room)"
        if room_open_position(room.raw) == 37:
            tilt_defaults.append(room_key)
        if room_close_position(room.raw) == 100:
            reversed_defaults.append(room_key)

        for level in levels_by_room.get(room.id, []):
            levels = levels_by_room.get(room.id, [])
            group_label = clean_label(group_name(room.group_names, level, levels))
            choices[group_target_id(room.id, level)] = f"{room_label} - {group_label}"

    return choices, tilt_defaults, reversed_defaults


def _add_unknown_targets(choices: dict[str, str], targets: Iterable[str]) -> None:
    for target in targets:
        choices.setdefault(str(target), f"{target} (not currently discovered)")


async def _validate_for_flow(
    hass: HomeAssistant,
    data: dict[str, Any],
    transaction_lock: asyncio.Lock | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate flow data and return a Home Assistant error key when needed."""
    try:
        return await _validate_input(hass, data, transaction_lock), None
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
