"""HA-guided capture; the ESP alone validates frames and owns rolling state."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
import hashlib
import json
from typing import Any
from uuid import uuid4

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, selector
import voluptuous as vol

from .rf import (
    CONF_BRIDGE,
    CONF_TARGETS,
    RF,
    RFStatus,
    bridge_choices,
    parse_targets,
    read_status,
)


def supports_learning(hass: HomeAssistant, bridge: str) -> bool:
    """Only offer the wizard for an adopted bridge with the learning action."""
    return bridge in bridge_choices(hass) and hass.services.has_service(
        "esphome", f"{bridge}_rf_learning"
    )


async def learning_request(
    hass: HomeAssistant, bridge: str, session: str, operation: str, **data: Any
) -> dict[str, Any]:
    """Call the bounded firmware API without transporting captured RF bytes."""
    if not supports_learning(hass, bridge):
        raise HomeAssistantError("rf_learning_unavailable")
    async with asyncio.timeout(12):
        result = await hass.services.async_call(
            "esphome",
            f"{bridge}_rf_learning",
            {
                "request_json": json.dumps(
                    {**data, "operation": operation, "session": session}
                )
            },
            blocking=True,
            return_response=True,
        )
    if (
        not isinstance(result, dict)
        or result.get("learning_version") != 1
        or type(result.get("success")) is not bool
    ):
        raise HomeAssistantError("rf_learning_unavailable")
    if not result["success"]:
        known = {
            "mixed_actions",
            "need_two_presses",
            "wrong_endpoint",
            "capture_expired",
            "expired",
            "learning_busy",
            "session_mismatch",
            "radio_unavailable",
            "already_learned",
            "storage_full",
            "storage_or_profile_error",
        }
        error = result.get("error")
        raise HomeAssistantError(
            f"rf_learn_{error}"
            if isinstance(error, str) and error in known
            else "rf_learning_unavailable"
        )
    return result


def _label(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) < 48
        and all(32 <= ord(char) <= 126 for char in value)
    )


def parse_profiles(rows: Any) -> dict[str, dict[str, Any]]:
    """Validate the separate panel/relay inventory before offering mutations."""
    if not isinstance(rows, list) or len(rows) > 64:
        raise HomeAssistantError("rf_learning_unavailable")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("kind") not in ("panel", "relay")
            or type(row.get("slot")) is not int
            or not 0 <= row["slot"] < 32
            or not isinstance(row.get("profile_id"), str)
            or len(row["profile_id"]) != 16
            or any(char not in "0123456789abcdef" for char in row["profile_id"])
            or not _label(row.get("name"))
            or not isinstance(row.get("room"), str)
        ):
            raise HomeAssistantError("rf_learning_unavailable")
        key = f"{row['kind']}:{row['slot']}"
        if key in result:
            raise HomeAssistantError("rf_learning_unavailable")
        result[key] = row
    return result


class RFLearningFlow(config_entries.ConfigFlow):
    """Shared setup/reconfigure steps; no hub credentials or commands are used."""

    _rf_bridge = ""
    _rf_status: RFStatus | None = None
    _learning_session = ""
    _learning_kind = "panel"
    _learning_endpoint = 0
    _learning_details: dict[str, Any]
    _profile_choices: dict[str, dict[str, Any]]
    _selected_profile: dict[str, Any]

    async def async_step_rf_manage(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Learn more sections/actions or finish selecting the available rooms."""
        try:
            self._rf_status = await read_status(self.hass, self._rf_bridge)
        except (HomeAssistantError, TimeoutError):
            return self.async_show_form(
                step_id="rf_manage",
                data_schema=vol.Schema({}),
                errors={"base": "rf_unavailable"},
            )
        choices = ["rf_learn_panel", "rf_learn_relay", "rf_profile"]
        choices.append("rf_rooms" if self._rf_status.targets else "rf_finish_relay")
        return self.async_show_menu(step_id="rf_manage", menu_options=choices)

    async def async_step_rf_learn_panel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start learning one motorised section, never a native room command."""
        self._learning_kind = "panel"
        return await self._learning_details_form(user_input)

    async def async_step_rf_learn_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Learn one forwarding-only controller action, including native rooms."""
        self._learning_kind = "relay"
        return await self._learning_details_form(user_input)

    async def _learning_details_form(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        panel = self._learning_kind == "panel"
        if user_input is not None:
            if not _label(user_input.get("name")) or (
                panel and not _label(user_input.get("room"))
            ):
                errors["base"] = "rf_learn_invalid_name"
            else:
                self._learning_details = dict(user_input)
                self._learning_details["name"] = user_input["name"].strip()
                if panel:
                    self._learning_details["room"] = user_input["room"].strip()
                self._learning_session = uuid4().hex
                try:
                    await self._learning_call("begin", kind=self._learning_kind)
                    self._learning_endpoint = 0
                    await self._learning_call("capture", endpoint=0)
                except (HomeAssistantError, TimeoutError) as err:
                    errors["base"] = self._learning_error(err)
                    await self._learning_cancel()
                else:
                    return await self.async_step_rf_capture()
        fields: dict[Any, Any] = {vol.Required("name"): str}
        if panel:
            fields.update(
                {
                    vol.Required("room"): str,
                    vol.Required(
                        "close_direction", default="up"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["up", "down"], translation_key="rf_close_direction"
                        )
                    ),
                    vol.Required("learn_opposite", default=False): bool,
                }
            )
        return self.async_show_form(
            step_id=f"rf_learn_{self._learning_kind}",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_rf_capture(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep a receive-only window open until explicit user confirmation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            choice = user_input["next_action"]
            if choice == "cancel":
                await self._learning_cancel()
                return await self.async_step_rf_manage()
            try:
                if choice == "retry":
                    await self._learning_call(
                        "capture", endpoint=self._learning_endpoint
                    )
                else:
                    await self._learning_call("accept")
                    if (
                        self._learning_kind == "relay"
                        or self._learning_endpoint == 2
                        or (
                            self._learning_endpoint == 1
                            and not self._learning_details["learn_opposite"]
                        )
                    ):
                        return await self.async_step_rf_learn_confirm()
                    self._learning_endpoint += 1
                    await self._learning_call(
                        "capture", endpoint=self._learning_endpoint
                    )
            except (HomeAssistantError, TimeoutError) as err:
                errors["base"] = self._learning_error(err)
        if self._learning_kind == "relay":
            action = self._learning_details["name"]
        elif self._learning_endpoint == 0:
            action = "Open"
        else:
            direction = self._learning_details["close_direction"]
            if self._learning_endpoint == 2:
                direction = "down" if direction == "up" else "up"
            action = f"Close {direction}wards"
        return self.async_show_form(
            step_id="rf_capture",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "next_action", default="continue"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["continue", "retry", "cancel"],
                            translation_key="rf_capture_action",
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "action": action,
                "name": self._learning_details["name"],
            },
        )

    async def async_step_rf_learn_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Commit only after the owner confirms the physical action and target."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["confirmed"]:
                await self._learning_cancel()
                return await self.async_step_rf_manage()
            data = {"name": self._learning_details["name"], "confirmed": True}
            if self._learning_kind == "panel":
                data.update(
                    {
                        "room": self._learning_details["room"],
                        "close_position": 100
                        if self._learning_details["close_direction"] == "up"
                        else 0,
                    }
                )
            try:
                await self._learning_call("commit", **data)
            except (HomeAssistantError, TimeoutError) as err:
                errors["base"] = self._learning_error(err)
            else:
                return await self.async_step_rf_enable_relay(
                    {"enable_relay": user_input["enable_relay"]}
                )
        return self.async_show_form(
            step_id="rf_learn_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("confirmed", default=False): bool,
                    vol.Required("enable_relay", default=True): bool,
                }
            ),
            errors=errors,
            description_placeholders={"name": self._learning_details["name"]},
        )

    async def async_step_rf_enable_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retry only the optional relay setting after a confirmed profile save."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["enable_relay"]:
                return await self.async_step_rf_manage()
            try:
                async with asyncio.timeout(12):
                    await self.hass.services.async_call(
                        "esphome",
                        f"{self._rf_bridge}_rf_set_relay",
                        {"enabled": True},
                        blocking=True,
                    )
            except (HomeAssistantError, TimeoutError):
                errors["base"] = "rf_relay_enable_failed"
            else:
                return await self.async_step_rf_manage()
        return self.async_show_form(
            step_id="rf_enable_relay",
            data_schema=vol.Schema({vol.Required("enable_relay", default=True): bool}),
            errors=errors,
        )

    async def async_step_rf_finish_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow a relay-only bridge without inventing shutter entities."""
        data = {"generation": RF, CONF_BRIDGE: self._rf_bridge, CONF_TARGETS: []}
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        if entry is not None:
            # A relay-only finish must never remove an existing room binding.
            return self.async_abort(reason="reconfigure_successful")
        return self.async_create_entry(
            title=f"Norman RF {self._rf_bridge.replace('_', ' ')}", data=data
        )

    async def _learning_call(self, operation: str, **data: Any) -> dict[str, Any]:
        return await learning_request(
            self.hass, self._rf_bridge, self._learning_session, operation, **data
        )

    async def async_step_rf_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an exact saved identity before editing or removing it."""
        errors: dict[str, str] = {}
        if user_input is not None and "profile" in user_input:
            self._selected_profile = self._profile_choices[user_input["profile"]]
            return await self.async_step_rf_profile_edit()
        try:
            response = await self._learning_call("profiles")
            self._profile_choices = parse_profiles(response.get("profiles"))
        except (HomeAssistantError, TimeoutError) as err:
            errors["base"] = self._learning_error(err)
            self._profile_choices = {}
        if not self._profile_choices and not errors:
            return await self.async_step_rf_manage()
        return self.async_show_form(
            step_id="rf_profile",
            data_schema=vol.Schema(
                {
                    vol.Required("profile"): vol.In(
                        {
                            key: f"{row['room']} / {row['name']} ({row['kind']})"
                            for key, row in self._profile_choices.items()
                        }
                    )
                }
                if self._profile_choices
                else {}
            ),
            errors=errors,
        )

    async def async_step_rf_profile_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename/regroup without changing RF state, or require removal confirmation."""
        profile = self._selected_profile
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["operation"] == "remove":
                return await self.async_step_rf_profile_remove()
            if not _label(user_input["name"]) or (
                profile["kind"] == "panel" and not _label(user_input["room"])
            ):
                errors["base"] = "rf_learn_invalid_name"
            else:
                try:
                    if profile["kind"] == "panel":
                        status = await read_status(self.hass, self._rf_bridge)
                        if (
                            sum(
                                target.room == user_input["room"].strip()
                                and target.profile_id != profile["profile_id"]
                                for target in status.targets
                            )
                            >= 8
                        ):
                            return self.async_show_form(
                                step_id="rf_profile_edit",
                                data_schema=vol.Schema(
                                    {
                                        vol.Required(
                                            "operation", default="rename"
                                        ): vol.In(["rename", "remove"]),
                                        vol.Required(
                                            "name", default=user_input["name"]
                                        ): str,
                                        vol.Required(
                                            "room", default=user_input["room"]
                                        ): str,
                                    }
                                ),
                                errors={"base": "rf_invalid_rooms"},
                            )
                    await self._learning_call(
                        "rename",
                        kind=profile["kind"],
                        slot=profile["slot"],
                        profile_id=profile["profile_id"],
                        name=user_input["name"].strip(),
                        room=user_input.get("room", "").strip(),
                    )
                except (HomeAssistantError, TimeoutError) as err:
                    errors["base"] = self._learning_error(err)
                else:
                    return await self.async_step_rf_profile_refresh()
        fields: dict[Any, Any] = {
            vol.Required("operation", default="rename"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["rename", "remove"], translation_key="rf_profile_action"
                )
            ),
            vol.Required("name", default=profile["name"]): str,
        }
        if profile["kind"] == "panel":
            fields[vol.Required("room", default=profile["room"])] = str
        return self.async_show_form(
            step_id="rf_profile_edit", data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_rf_profile_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove only the explicitly confirmed profile, never reset a bridge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["confirmed"]:
                return await self.async_step_rf_manage()
            try:
                profile = self._selected_profile
                await self._learning_call(
                    "remove",
                    kind=profile["kind"],
                    slot=profile["slot"],
                    profile_id=profile["profile_id"],
                    confirmed=True,
                )
            except (HomeAssistantError, TimeoutError) as err:
                errors["base"] = self._learning_error(err)
            else:
                return await self.async_step_rf_profile_refresh()
        return self.async_show_form(
            step_id="rf_profile_remove",
            data_schema=vol.Schema({vol.Required("confirmed", default=False): bool}),
            errors=errors,
            description_placeholders={"name": self._selected_profile["name"]},
        )

    async def async_step_rf_profile_refresh(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retry reconciliation without repeating an acknowledged profile mutation."""
        try:
            await self._refresh_managed_bindings()
        except (HomeAssistantError, TimeoutError):
            return self.async_show_form(
                step_id="rf_profile_refresh",
                data_schema=vol.Schema({}),
                errors={"base": "rf_profile_refresh_failed"},
            )
        return await self.async_step_rf_manage()

    async def _refresh_managed_bindings(self) -> None:
        """Refresh existing bindings after an explicit edit; new panels stay opt-in."""
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        if entry is None:
            return
        status = await read_status(self.hass, self._rf_bridge)
        if not status.ready:
            raise HomeAssistantError("rf_learning_unavailable")
        old = parse_targets(entry.data[CONF_TARGETS])
        identities = {target.profile_id for target in old}
        retained = [
            target for target in status.targets if target.profile_id in identities
        ]
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TARGETS: [asdict(t) for t in retained]}
        )
        await self.hass.config_entries.async_reload(entry.entry_id)
        expected = {f"rf_{self._rf_bridge}_panel_{target.slot}" for target in retained}
        expected.update(
            f"rf_{self._rf_bridge}_room_{hashlib.sha256(room.encode()).hexdigest()[:12]}"
            for room in {target.room for target in retained}
        )
        registry = er.async_get(self.hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "cover" and entity.unique_id not in expected:
                registry.async_remove(entity.entity_id)

    async def _learning_cancel(self) -> None:
        # Disconnected sessions also expire on the ESP after ten minutes.
        with suppress(HomeAssistantError, TimeoutError):
            await self._learning_call("cancel")

    @staticmethod
    def _learning_error(err: HomeAssistantError | TimeoutError) -> str:
        message = str(err)
        return message if message.startswith("rf_learn") else "rf_learning_unavailable"
