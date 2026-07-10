"""Small Home Assistant test doubles used by the unit test suite."""

from __future__ import annotations

from enum import IntFlag, StrEnum
import sys
from types import ModuleType
from typing import Any, TypeVar


def _package(name: str) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    return module


def _module(name: str) -> ModuleType:
    module = ModuleType(name)
    sys.modules[name] = module
    return module


homeassistant = _package("homeassistant")
components = _package("homeassistant.components")
helpers = _package("homeassistant.helpers")

config_entries = _module("homeassistant.config_entries")
const = _module("homeassistant.const")
core = _module("homeassistant.core")
data_entry_flow = _module("homeassistant.data_entry_flow")
exceptions = _module("homeassistant.exceptions")
cover = _module("homeassistant.components.cover")
diagnostics = _module("homeassistant.components.diagnostics")
aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
config_validation = _module("homeassistant.helpers.config_validation")
device_registry = _module("homeassistant.helpers.device_registry")
entity_registry = _module("homeassistant.helpers.entity_registry")
entity_platform = _module("homeassistant.helpers.entity_platform")
selector = _module("homeassistant.helpers.selector")
update_coordinator = _module("homeassistant.helpers.update_coordinator")

homeassistant.components = components
homeassistant.config_entries = config_entries
homeassistant.const = const
homeassistant.core = core
homeassistant.data_entry_flow = data_entry_flow
homeassistant.exceptions = exceptions
homeassistant.helpers = helpers
components.cover = cover
components.diagnostics = diagnostics
helpers.aiohttp_client = aiohttp_client
helpers.config_validation = config_validation
helpers.device_registry = device_registry
helpers.entity_registry = entity_registry
helpers.entity_platform = entity_platform
helpers.selector = selector
helpers.update_coordinator = update_coordinator


class Platform(StrEnum):
    COVER = "cover"
    SENSOR = "sensor"


const.CONF_HOST = "host"
const.CONF_PASSWORD = "password"
const.Platform = Platform


class HomeAssistant:
    """Minimal Home Assistant marker class."""


def callback(func):
    """Return a callback unchanged."""
    return func


core.HomeAssistant = HomeAssistant
core.callback = callback


class ConfigEntry:
    """Minimal config entry with unload callback support."""

    def __init__(
        self,
        *,
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        entry_id: str = "entry-1",
        unique_id: str | None = "hub-1",
        title: str = "Norman hub",
    ) -> None:
        self.data = data or {}
        self.options = options or {}
        self.entry_id = entry_id
        self.unique_id = unique_id
        self.title = title
        self.unload_callbacks: list[Any] = []
        self.update_listeners: list[Any] = []
        self.runtime_data: Any = None

    @classmethod
    def __class_getitem__(cls, item: Any):
        return cls

    def async_on_unload(self, callback_func):
        self.unload_callbacks.append(callback_func)
        return callback_func

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self.update_listeners:
                self.update_listeners.remove(listener)

        return unsubscribe


class ConfigFlow:
    """Minimal config flow result helpers."""

    def __init_subclass__(cls, *, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def __init__(self) -> None:
        self.context: dict[str, Any] = {}
        self.hass: Any = None
        self.unique_id: str | None = None

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(
        self, *, updates: dict[str, Any] | None = None
    ) -> None:
        self.unique_id_updates = updates

    def async_create_entry(self, *, title: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(
        self, *, step_id: str, data_schema, errors=None
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_abort(self, *, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(
        self,
        entry: ConfigEntry,
        *,
        data: dict[str, Any],
        reason: str = "reauth_successful",
        **kwargs: Any,
    ) -> dict[str, Any]:
        entry.data = dict(data)
        if unique_id := kwargs.get("unique_id"):
            entry.unique_id = unique_id
        return {"type": "abort", "reason": reason, "data": data}


class OptionsFlow:
    """Minimal options flow result helpers."""

    def async_create_entry(
        self, *, title: str = "", data: dict[str, Any]
    ) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(
        self, *, step_id: str, data_schema, errors=None, description_placeholders=None
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders,
        }

    def async_show_menu(
        self, *, step_id: str, menu_options: list[str]
    ) -> dict[str, Any]:
        return {
            "type": "menu",
            "step_id": step_id,
            "menu_options": menu_options,
        }

    def async_abort(self, *, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}


config_entries.ConfigEntry = ConfigEntry
config_entries.ConfigFlow = ConfigFlow
config_entries.ConfigFlowResult = dict[str, Any]
config_entries.OptionsFlow = OptionsFlow
data_entry_flow.FlowResult = dict[str, Any]


class HomeAssistantError(Exception):
    """Minimal Home Assistant service error."""

    def __init__(
        self,
        *args: Any,
        translation_domain: str | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(*args or ((translation_key or "home_assistant_error"),))
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class ConfigEntryAuthFailed(HomeAssistantError):
    """Minimal config-entry authentication error."""


exceptions.HomeAssistantError = HomeAssistantError
exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed


class ConfigEntryError(HomeAssistantError):
    """Minimal config-entry setup error."""


exceptions.ConfigEntryError = ConfigEntryError


class DeviceInfo(dict):
    """Minimal mapping-compatible device info."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs)


device_registry.DeviceInfo = DeviceInfo


class DeviceRegistry:
    """Minimal device registry accepting device creation calls."""

    def async_get_or_create(self, **kwargs: Any) -> None:
        return None


def async_get_device_registry(hass: Any) -> DeviceRegistry:
    """Return a minimal device registry."""
    return DeviceRegistry()


device_registry.async_get = async_get_device_registry


cover.ATTR_POSITION = "position"


class CoverDeviceClass(StrEnum):
    SHUTTER = "shutter"


class CoverEntityFeature(IntFlag):
    OPEN = 1
    CLOSE = 2
    SET_POSITION = 4


class CoverEntity:
    """Minimal cover entity marker class."""


cover.CoverDeviceClass = CoverDeviceClass
cover.CoverEntity = CoverEntity
cover.CoverEntityFeature = CoverEntityFeature


def async_redact_data(data: Any, keys: set[str]) -> Any:
    if isinstance(data, dict):
        return {
            key: "**REDACTED**" if key in keys else async_redact_data(value, keys)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [async_redact_data(value, keys) for value in data]
    return data


diagnostics.async_redact_data = async_redact_data


T = TypeVar("T")


class UpdateFailed(Exception):
    """Minimal coordinator update error."""


class DataUpdateCoordinator[T]:
    """Minimal coordinator with listener and refresh behavior."""

    def __init__(self, hass: Any, **kwargs: Any) -> None:
        self.hass = hass
        self.config_entry = kwargs.get("config_entry")
        self.data: T | None = None
        self.last_update_success = True
        self.listeners: list[Any] = []
        self.shutdown_calls = 0

    async def async_config_entry_first_refresh(self) -> None:
        self.data = await self._async_update_data()

    async def async_request_refresh(self) -> None:
        self.data = await self._async_update_data()
        for listener in tuple(self.listeners):
            listener()

    def async_add_listener(self, listener):
        self.listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self.listeners:
                self.listeners.remove(listener)

        return unsubscribe

    async def async_shutdown(self) -> None:
        self.shutdown_calls += 1


class CoordinatorEntity[T]:
    """Minimal coordinator-backed entity."""

    def __init__(self, coordinator: T) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entity_id = "cover.test"
        self.state_write_count = 0

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def async_write_ha_state(self) -> None:
        self.state_write_count += 1

    async def async_will_remove_from_hass(self) -> None:
        return None


update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
update_coordinator.CoordinatorEntity = CoordinatorEntity
update_coordinator.UpdateFailed = UpdateFailed


def async_get_clientsession(hass: Any):
    return hass.session


def async_create_clientsession(hass: Any, **kwargs: Any):
    return hass.session


def multi_select(choices: dict[str, str]):
    def validate(value):
        return [str(item) for item in value]

    return validate


aiohttp_client.async_get_clientsession = async_get_clientsession
aiohttp_client.async_create_clientsession = async_create_clientsession
config_validation.multi_select = multi_select
entity_platform.AddEntitiesCallback = Any


class TextSelectorType(StrEnum):
    PASSWORD = "password"


class TextSelectorConfig(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs)


class TextSelector:
    def __init__(self, config: TextSelectorConfig) -> None:
        self.config = config

    def __call__(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("expected string")
        return value


selector.TextSelector = TextSelector
selector.TextSelectorConfig = TextSelectorConfig
selector.TextSelectorType = TextSelectorType


class NumberSelectorMode(StrEnum):
    BOX = "box"


class SelectSelectorMode(StrEnum):
    DROPDOWN = "dropdown"


class NumberSelectorConfig(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs)


class SelectSelectorConfig(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs)


class SelectOptionDict(dict):
    def __init__(self, *, value: str, label: str) -> None:
        super().__init__(value=value, label=label)


class NumberSelector:
    def __init__(self, config: NumberSelectorConfig) -> None:
        self.config = config

    def __call__(self, value: Any) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected number")
        return value


class SelectSelector:
    def __init__(self, config: SelectSelectorConfig) -> None:
        self.config = config

    def __call__(self, value: Any) -> str:
        value = str(value)
        allowed = {
            str(option.get("value")) if isinstance(option, dict) else str(option)
            for option in self.config["options"]
        }
        if value not in allowed:
            raise ValueError("unknown option")
        return value


class BooleanSelector:
    def __call__(self, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError("expected bool")
        return value


selector.BooleanSelector = BooleanSelector
selector.NumberSelector = NumberSelector
selector.NumberSelectorConfig = NumberSelectorConfig
selector.NumberSelectorMode = NumberSelectorMode
selector.SelectOptionDict = SelectOptionDict
selector.SelectSelector = SelectSelector
selector.SelectSelectorConfig = SelectSelectorConfig
selector.SelectSelectorMode = SelectSelectorMode
