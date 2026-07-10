from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.components.cover import ATTR_POSITION, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.norman_gen1 import (
    NormanDataUpdateCoordinator,
    api as api_module,
    async_setup_entry as async_setup_integration,
    async_unload_entry,
)
from custom_components.norman_gen1.api import (
    InvalidAuth,
    InvalidSession,
    NormanGen1Api,
    NormanRoom,
    NormanWindow,
    group_target_id,
)
from custom_components.norman_gen1.config_flow import ConfigFlow
from custom_components.norman_gen1.const import (
    CONF_APP_VERSION,
    DEFAULT_APP_VERSION,
    DEFAULT_PASSWORD,
    DOMAIN,
)
from custom_components.norman_gen1.coordinator import NormanData
from custom_components.norman_gen1.cover import (
    NormanGroupCover,
    NormanRoomCover,
    async_setup_entry,
)
from custom_components.norman_gen1.diagnostics import async_get_config_entry_diagnostics
from custom_components.norman_gen1.helpers import group_name


class FakeConfigEntries:
    def __init__(
        self, *, unload_result: bool = True, entries: list[ConfigEntry] | None = None
    ) -> None:
        self.unload_result = unload_result
        self.entries = {entry.entry_id: entry for entry in entries or []}
        self.forwarded = False
        self.updated_unique_id: str | None = None
        self.scheduled_reloads: list[str] = []

    async def async_unload_platforms(self, entry: ConfigEntry, platforms) -> bool:
        return self.unload_result

    def async_get_entry(self, entry_id: str) -> ConfigEntry | None:
        return self.entries.get(entry_id)

    def async_entries(self, domain: str | None = None) -> list[ConfigEntry]:
        return list(self.entries.values())

    async def async_forward_entry_setups(self, entry: ConfigEntry, platforms) -> None:
        self.forwarded = True

    def async_update_entry(self, entry: ConfigEntry, **kwargs) -> bool:
        if "unique_id" in kwargs:
            entry.unique_id = kwargs["unique_id"]
            self.updated_unique_id = kwargs["unique_id"]
        if "data" in kwargs:
            entry.data = dict(kwargs["data"])
        return True

    async def async_reload(self, entry_id: str) -> None:
        return None

    def async_schedule_reload(self, entry_id: str) -> None:
        self.scheduled_reloads.append(entry_id)


class FakeHass:
    def __init__(self, *, config_entries: FakeConfigEntries | None = None) -> None:
        self.config_entries = config_entries or FakeConfigEntries()
        self.data: dict = {}
        self.session = FakeClientSession()

    def async_create_task(self, coroutine):
        return asyncio.create_task(coroutine)


class FakeClientSession:
    """Minimal detachable session returned by the Home Assistant test stub."""

    def __init__(self) -> None:
        self.closed = False

    def detach(self) -> None:
        self.closed = True


class FakeCoordinator:
    def __init__(self, hass: FakeHass, data: dict) -> None:
        self.hass = hass
        self.data = data
        self.last_update_success = True
        self.listeners: list = []
        self.refresh_calls = 0
        self.shutdown_calls = 0

    def async_add_listener(self, listener):
        self.listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self.listeners:
                self.listeners.remove(listener)

        return unsubscribe

    async def async_request_refresh(self) -> None:
        self.refresh_calls += 1

    async def async_shutdown(self) -> None:
        self.shutdown_calls += 1


class RecordingControlApi:
    def __init__(self) -> None:
        self.host = "192.0.2.10"
        self.hub_info = {"hubId": "hub-1", "hubName": "Home"}
        self.calls: list[tuple[int, int, int, int]] = []

    @property
    def hub_id(self) -> str:
        return "hub-1"

    @asynccontextmanager
    async def authenticated_session(self):
        yield self.hub_info

    async def set_group_position(
        self, room_id: int, level: int, position: int, model: int = 1
    ) -> None:
        self.calls.append((room_id, level, position, model))


class FailingControlApi(RecordingControlApi):
    async def set_group_position(
        self, room_id: int, level: int, position: int, model: int = 1
    ) -> None:
        raise api_module.CannotControl("not acknowledged")


class FakeUnloadApi:
    def __init__(self) -> None:
        self.logout_calls = 0

    async def async_close(self) -> None:
        self.logout_calls += 1


def _room(room_id: int, *, style: int = 99, name: str | None = None) -> NormanRoom:
    return NormanRoom(
        id=room_id,
        name=name or f"Room {room_id}",
        group_names=["Panel A", "Panel B"],
        raw={"Style": style},
    )


def _window(
    window_id: int, room_id: int, level: int, *, model: int = 1
) -> NormanWindow:
    return NormanWindow(
        id=window_id,
        name=f"Window {window_id}",
        room_id=room_id,
        level=level,
        group_id=None,
        position=0,
        model=model,
        battery=None,
        raw={"model": "malformed raw value"},
    )


def _coordinator_data(
    rooms: list[NormanRoom], windows: list[NormanWindow]
) -> NormanData:
    windows_by_room: dict[int, list[NormanWindow]] = {}
    windows_by_group: dict[tuple[int, int], list[NormanWindow]] = {}
    levels_by_room: dict[int, list[int]] = {}
    for window in windows:
        windows_by_room.setdefault(window.room_id, []).append(window)
        windows_by_group.setdefault((window.room_id, window.level), []).append(window)
        levels_by_room.setdefault(window.room_id, []).append(window.level)
    return NormanData(
        rooms=rooms,
        windows=windows,
        rooms_by_id={room.id: room for room in rooms},
        windows_by_room=windows_by_room,
        windows_by_group=windows_by_group,
        levels_by_room={
            room_id: sorted(set(levels)) for room_id, levels in levels_by_room.items()
        },
    )


class TestCoordinator(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_password_requests_reauthentication(self) -> None:
        class RejectingApi:
            @asynccontextmanager
            async def authenticated_session(self):
                raise InvalidAuth("bad password")
                yield

        coordinator = NormanDataUpdateCoordinator(
            FakeHass(), RejectingApi(), ConfigEntry()
        )

        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_invalid_session_is_retried_once(self) -> None:
        class RecoveringApi:
            def __init__(self) -> None:
                self.transactions = 0
                self.room_calls = 0

            @asynccontextmanager
            async def authenticated_session(self):
                self.transactions += 1
                yield {}

            async def get_rooms(self):
                self.room_calls += 1
                if self.room_calls == 1:
                    raise InvalidSession("stale")
                return [_room(1)]

            async def get_windows(self):
                return [_window(1, 1, 0)]

        api = RecoveringApi()
        coordinator = NormanDataUpdateCoordinator(FakeHass(), api, ConfigEntry())

        data = await coordinator._async_update_data()

        self.assertEqual(api.transactions, 2)
        self.assertEqual(len(data.rooms), 1)

    async def test_invalid_windows_without_rooms_are_not_usable_devices(self) -> None:
        class InvalidDeviceApi:
            @asynccontextmanager
            async def authenticated_session(self):
                yield {}

            async def get_rooms(self):
                return []

            async def get_windows(self):
                return [_window(1, -1, -1)]

        coordinator = NormanDataUpdateCoordinator(
            FakeHass(), InvalidDeviceApi(), ConfigEntry()
        )

        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_last_known_room_metadata_survives_a_partial_refresh(self) -> None:
        class PartialRoomApi:
            def __init__(self) -> None:
                self.refresh = 0

            @asynccontextmanager
            async def authenticated_session(self):
                yield {}

            async def get_rooms(self):
                self.refresh += 1
                return [_room(1, style=13, name="Office")] if self.refresh == 1 else []

            async def get_windows(self):
                return [_window(1, 1, 0)]

        coordinator = NormanDataUpdateCoordinator(
            FakeHass(), PartialRoomApi(), ConfigEntry()
        )

        first = await coordinator._async_update_data()
        second = await coordinator._async_update_data()

        self.assertEqual(first.rooms_by_id[1].raw["Style"], 13)
        self.assertEqual(second.rooms_by_id[1].raw["Style"], 13)
        self.assertEqual(second.rooms_by_id[1].name, "Office")


class TestGroupNames(unittest.TestCase):
    def test_group_names_support_zero_based_levels(self) -> None:
        self.assertEqual(group_name(["Top", "Bottom"], 0, [0, 1]), "Top")
        self.assertEqual(group_name(["Top", "Bottom"], 1, [0, 1]), "Bottom")

    def test_group_names_support_one_based_levels(self) -> None:
        self.assertEqual(group_name(["Top", "Bottom"], 1, [1, 2]), "Top")
        self.assertEqual(group_name(["Top", "Bottom"], 2, [1, 2]), "Bottom")

    def test_group_names_keep_indexes_when_a_level_is_missing(self) -> None:
        self.assertEqual(group_name(["Top", "Middle", "Bottom"], 2, [0, 2]), "Bottom")
        self.assertEqual(group_name(["Top", "Middle", "Bottom"], 3, [1, 3]), "Bottom")

    def test_group_names_fall_back_for_out_of_range_levels(self) -> None:
        self.assertEqual(group_name([], 4, [1, 4]), "Group 4")
        self.assertEqual(group_name([], -1, [-1]), "Group 1")


class TestPositionProfiles(unittest.TestCase):
    def test_tilt_profiles_map_home_assistant_open_to_visual_open(self) -> None:
        profile = api_module.resolve_position_profile({"Style": 2})

        self.assertEqual(profile.open_position, 37)
        self.assertEqual(profile.close_position, 0)
        self.assertEqual(api_module.ha_position_to_hub(100, profile), 37)
        self.assertEqual(api_module.hub_position_to_ha(37, profile), 100)
        self.assertEqual(api_module.hub_position_to_ha(0, profile), 0)
        self.assertEqual(api_module.hub_position_to_ha(100, profile), 0)

    def test_reversed_tilt_profile_uses_the_selected_close_branch(self) -> None:
        profile = api_module.resolve_position_profile({"Style": 13})

        self.assertEqual(profile.open_position, 37)
        self.assertEqual(profile.close_position, 100)
        self.assertEqual(api_module.ha_position_to_hub(0, profile), 100)
        self.assertEqual(api_module.ha_position_to_hub(100, profile), 37)
        self.assertEqual(api_module.hub_position_to_ha(0, profile), 0)
        self.assertEqual(api_module.hub_position_to_ha(100, profile), 0)

    def test_reversed_close_override_cannot_equal_the_open_target(self) -> None:
        profile = api_module.resolve_position_profile(
            {"Style": 99},
            use_tilt_open=False,
            use_reversed_close=True,
        )

        self.assertNotEqual(profile.open_position, profile.close_position)
        self.assertEqual(profile.open_position, 37)
        self.assertEqual(profile.close_position, 100)


class TestEntityLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_new_entities_are_added_once_after_discovery(self) -> None:
        hass = FakeHass()
        entry = ConfigEntry(entry_id="entry-1")
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        api.hub_info = {"hubId": "hub-1"}
        room_one = _room(1)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room_one], [_window(1, 1, 0)])
        )
        coordinator.api = api
        entry.runtime_data = coordinator
        added: list = []

        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
        self.assertEqual(len(added), 2)
        self.assertEqual(len(coordinator.listeners), 1)

        room_two = _room(2)
        coordinator.data = _coordinator_data(
            [room_one, room_two],
            [_window(1, 1, 0), _window(2, 2, 0)],
        )
        coordinator.listeners[0]()
        coordinator.listeners[0]()

        self.assertEqual(len(added), 4)

    async def test_group_becomes_unavailable_when_no_longer_discovered(self) -> None:
        hass = FakeHass()
        entry = ConfigEntry()
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        room = _room(1)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room], [_window(1, 1, 0)])
        )
        entity = NormanGroupCover(entry, api, coordinator, room, 0, "Panel A")

        self.assertTrue(entity.available)
        coordinator.data = _coordinator_data([room], [])
        self.assertFalse(entity.available)
        self.assertIsNone(entity.current_cover_position)
        self.assertIsNone(entity.is_closed)
        self.assertEqual(entity._model(), 1)

    async def test_room_uses_current_metadata_and_normalized_model(self) -> None:
        hass = FakeHass()
        entry = ConfigEntry()
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        initial_room = _room(1, style=99)
        current_room = _room(1, style=13)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([current_room], [_window(1, 1, 0, model=1)])
        )
        room_entity = NormanRoomCover(entry, api, coordinator, initial_room)
        group_entity = NormanGroupCover(
            entry, api, coordinator, initial_room, 0, "Panel A"
        )

        self.assertEqual(room_entity._position_profile.open_position, 37)
        self.assertEqual(group_entity._model(), 1)

    async def test_group_specific_tilt_option_controls_closed_semantics(self) -> None:
        hass = FakeHass()
        room = _room(1, style=99)
        window = _window(1, 1, 0)
        window.position = 100
        coordinator = FakeCoordinator(hass, _coordinator_data([room], [window]))
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        selected_entry = ConfigEntry(
            options={"tilt_open_targets": [group_target_id(1, 0)]}
        )
        unselected_entry = ConfigEntry(options={"tilt_open_targets": []})

        selected = NormanGroupCover(
            selected_entry, api, coordinator, room, 0, "Panel A"
        )
        unselected = NormanGroupCover(
            unselected_entry, api, coordinator, room, 0, "Panel A"
        )

        self.assertTrue(selected.is_closed)
        self.assertFalse(unselected.is_closed)

    async def test_opposite_tilt_end_stops_make_the_room_closed(self) -> None:
        hass = FakeHass()
        room = _room(1, style=2)
        first = _window(1, 1, 0)
        second = _window(2, 1, 1)
        first.position = 0
        second.position = 100
        coordinator = FakeCoordinator(hass, _coordinator_data([room], [first, second]))
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        entity = NormanRoomCover(ConfigEntry(), api, coordinator, room)

        self.assertTrue(entity.is_closed)

    async def test_tilt_slider_open_maps_to_visual_open_command(self) -> None:
        hass = FakeHass()
        room = _room(1, style=2)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room], [_window(1, 1, 0)])
        )
        api = RecordingControlApi()
        entity = NormanGroupCover(ConfigEntry(), api, coordinator, room, 0, "Panel A")

        await entity.async_set_cover_position(**{ATTR_POSITION: 100})

        self.assertEqual(api.calls, [(1, 0, 37, 1)])
        self.assertEqual(entity.current_cover_position, 100)
        await entity.async_will_remove_from_hass()

    async def test_room_position_feature_tracks_discovered_levels(self) -> None:
        hass = FakeHass()
        room = _room(1, style=99)
        coordinator = FakeCoordinator(hass, _coordinator_data([room], []))
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        entity = NormanRoomCover(ConfigEntry(), api, coordinator, room)

        self.assertEqual(
            entity.supported_features,
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE,
        )
        coordinator.data = _coordinator_data([room], [_window(1, 1, 0)])
        self.assertEqual(
            entity.supported_features,
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION,
        )

    async def test_room_rejects_unrepresentable_targets_without_levels(self) -> None:
        hass = FakeHass()
        room = _room(1, style=13)
        coordinator = FakeCoordinator(hass, _coordinator_data([room], []))
        entity = NormanRoomCover(
            ConfigEntry(),
            NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD),
            coordinator,
            room,
        )

        for command in (
            entity.async_open_cover,
            entity.async_close_cover,
            lambda: entity.async_set_cover_position(**{ATTR_POSITION: 50}),
        ):
            with self.subTest(command=command), self.assertRaises(HomeAssistantError):
                await command()

    async def test_delayed_refresh_is_cancelled_on_entity_removal(self) -> None:
        hass = FakeHass()
        room = _room(1)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room], [_window(1, 1, 0)])
        )
        api = NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD)
        entity = NormanRoomCover(ConfigEntry(), api, coordinator, room)

        await entity._refresh_after_command(100)
        await entity.async_will_remove_from_hass()
        await asyncio.sleep(0)

        self.assertEqual(coordinator.refresh_calls, 0)
        self.assertIsNone(entity._refresh_task)

    async def test_delayed_refresh_completes_and_clears_optimistic_state(self) -> None:
        hass = FakeHass()
        room = _room(1)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room], [_window(1, 1, 0)])
        )
        entity = NormanRoomCover(
            ConfigEntry(),
            NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD),
            coordinator,
            room,
        )

        with patch("custom_components.norman_gen1.entity.COMMAND_SETTLE_SECONDS", 0):
            await entity._refresh_after_command(100)
            await entity._refresh_task

        self.assertEqual(coordinator.refresh_calls, 1)
        self.assertIsNone(entity._optimistic_position)

    async def test_stale_delayed_refresh_does_not_request_update(self) -> None:
        hass = FakeHass()
        room = _room(1)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room], [_window(1, 1, 0)])
        )
        entity = NormanRoomCover(
            ConfigEntry(),
            NormanGen1Api(object(), "192.0.2.10", DEFAULT_PASSWORD),
            coordinator,
            room,
        )
        entity._refresh_generation = 2

        with patch("custom_components.norman_gen1.entity.COMMAND_SETTLE_SECONDS", 0):
            await entity._delayed_refresh(1)

        self.assertEqual(coordinator.refresh_calls, 0)

    async def test_control_errors_use_translated_home_assistant_errors(self) -> None:
        hass = FakeHass()
        room = _room(1)
        coordinator = FakeCoordinator(
            hass, _coordinator_data([room], [_window(1, 1, 0)])
        )
        entity = NormanGroupCover(
            ConfigEntry(), FailingControlApi(), coordinator, room, 0, "Panel A"
        )

        with self.assertRaises(HomeAssistantError) as caught:
            await entity.async_open_cover()

        self.assertEqual(caught.exception.translation_domain, DOMAIN)
        self.assertEqual(caught.exception.translation_key, "command_not_confirmed")


class TestUnload(unittest.IsolatedAsyncioTestCase):
    async def test_failed_platform_unload_preserves_runtime_data(self) -> None:
        entry = ConfigEntry(entry_id="entry-1")
        api = FakeUnloadApi()
        hass = FakeHass(config_entries=FakeConfigEntries(unload_result=False))
        coordinator = FakeCoordinator(hass, _coordinator_data([], []))
        coordinator.api = api
        entry.runtime_data = coordinator

        result = await async_unload_entry(hass, entry)

        self.assertFalse(result)
        self.assertEqual(api.logout_calls, 0)
        self.assertEqual(coordinator.shutdown_calls, 0)

    async def test_successful_platform_unload_cleans_runtime_data(self) -> None:
        entry = ConfigEntry(entry_id="entry-1")
        api = FakeUnloadApi()
        hass = FakeHass(config_entries=FakeConfigEntries(unload_result=True))
        coordinator = FakeCoordinator(hass, _coordinator_data([], []))
        coordinator.api = api
        entry.runtime_data = coordinator

        result = await async_unload_entry(hass, entry)

        self.assertTrue(result)
        self.assertEqual(api.logout_calls, 1)
        self.assertEqual(coordinator.shutdown_calls, 1)


class TestSetup(unittest.IsolatedAsyncioTestCase):
    async def test_setup_uses_default_app_version(self) -> None:
        captured: dict[str, str] = {}

        class SetupApi:
            def __init__(
                self,
                session,
                host: str,
                password: str,
                app_version: str,
                **kwargs,
            ) -> None:
                captured["app_version"] = app_version
                self.hub_info = {"hubId": "hub-1", "hubName": "Home"}

            @property
            def hub_id(self) -> str:
                return "hub-1"

            def pin_hub_id(self, hub_id: str) -> None:
                return None

        class SetupCoordinator:
            def __init__(self, hass, api, config_entry) -> None:
                self.api = api
                self.data = _coordinator_data([_room(1)], [_window(1, 1, 0)])

            async def async_config_entry_first_refresh(self) -> None:
                return None

        entry = ConfigEntry(
            entry_id="entry-1",
            unique_id="hub-1",
            data={CONF_HOST: "192.0.2.10", CONF_PASSWORD: DEFAULT_PASSWORD},
        )
        hass = FakeHass(config_entries=FakeConfigEntries(entries=[entry]))

        with (
            patch("custom_components.norman_gen1.NormanGen1Api", SetupApi),
            patch(
                "custom_components.norman_gen1.NormanDataUpdateCoordinator",
                SetupCoordinator,
            ),
        ):
            result = await async_setup_integration(hass, entry)

        self.assertTrue(result)
        self.assertEqual(captured["app_version"], DEFAULT_APP_VERSION)
        self.assertTrue(hass.config_entries.forwarded)

    async def test_setup_refuses_a_different_hub_at_the_saved_host(self) -> None:
        class SetupApi:
            def __init__(
                self,
                session,
                host: str,
                password: str,
                app_version: str,
                **kwargs,
            ) -> None:
                self.hub_info = {"hubId": "hub-b"}

            @property
            def hub_id(self) -> str:
                return "hub-b"

            def pin_hub_id(self, hub_id: str) -> None:
                return None

        class SetupCoordinator:
            def __init__(self, hass, api, config_entry) -> None:
                self.api = api
                self.data = _coordinator_data([_room(1)], [_window(1, 1, 0)])

            async def async_config_entry_first_refresh(self) -> None:
                return None

        entry = ConfigEntry(
            entry_id="entry-1",
            unique_id="hub-a",
            data={CONF_HOST: "192.0.2.10", CONF_PASSWORD: DEFAULT_PASSWORD},
        )
        hass = FakeHass(config_entries=FakeConfigEntries(entries=[entry]))

        with (
            patch("custom_components.norman_gen1.NormanGen1Api", SetupApi),
            patch(
                "custom_components.norman_gen1.NormanDataUpdateCoordinator",
                SetupCoordinator,
            ),
            self.assertRaises(ConfigEntryError),
        ):
            await async_setup_integration(hass, entry)

        self.assertFalse(hass.config_entries.forwarded)
        self.assertNotIn(DOMAIN, hass.data)


class TestDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostics_redact_credentials_and_hub_identity(self) -> None:
        password = "custom-private-password"
        host = "192.0.2.10"
        entry = ConfigEntry(
            entry_id="entry-1",
            data={
                CONF_HOST: host,
                CONF_PASSWORD: password,
                CONF_APP_VERSION: DEFAULT_APP_VERSION,
            },
        )
        api = NormanGen1Api(object(), host, password)
        api.hub_info = {
            "hubId": "private-hub-id",
            "hubName": "Private Home",
            "swVer": "1.0",
        }
        coordinator = FakeCoordinator(
            FakeHass(), _coordinator_data([_room(1)], [_window(1, 1, 0)])
        )
        coordinator.api = api
        hass = FakeHass()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(hass, entry)
        rendered = repr(result)

        self.assertNotIn(password, rendered)
        self.assertNotIn(host, rendered)
        self.assertNotIn("private-hub-id", rendered)
        self.assertNotIn("Private Home", rendered)
        self.assertEqual(result["snapshot"]["room_count"], 1)
        self.assertEqual(result["snapshot"]["window_count"], 1)


class TestConfigFlow(unittest.IsolatedAsyncioTestCase):
    async def test_factory_password_remains_the_setup_default(self) -> None:
        self.assertEqual(DEFAULT_PASSWORD, "123456789")
        flow = ConfigFlow()
        flow.hass = FakeHass()

        result = await flow.async_step_user()
        validated = result["data_schema"]({CONF_HOST: "192.0.2.10"})

        self.assertEqual(validated[CONF_PASSWORD], DEFAULT_PASSWORD)

    async def test_reauth_preserves_connection_data_and_updates_password(self) -> None:
        entry = ConfigEntry(
            entry_id="entry-1",
            unique_id="hub-1",
            data={
                CONF_HOST: "192.0.2.10",
                CONF_PASSWORD: DEFAULT_PASSWORD,
                CONF_APP_VERSION: DEFAULT_APP_VERSION,
            },
        )
        flow = ConfigFlow()
        flow.hass = FakeHass(config_entries=FakeConfigEntries(entries=[entry]))
        flow.context = {"entry_id": entry.entry_id}

        form = await flow.async_step_reauth(entry.data)
        self.assertEqual(form["step_id"], "reauth_confirm")

        with patch(
            "custom_components.norman_gen1.config_flow._validate_input",
            AsyncMock(return_value={"hubId": "hub-1", "hubName": "Home"}),
        ):
            result = await flow.async_step_reauth_confirm(
                {CONF_PASSWORD: "replacement"}
            )

        self.assertEqual(result["reason"], "reauth_successful")
        self.assertEqual(entry.data[CONF_HOST], "192.0.2.10")
        self.assertEqual(entry.data[CONF_APP_VERSION], DEFAULT_APP_VERSION)
        self.assertEqual(entry.data[CONF_PASSWORD], "replacement")

    async def test_reauth_refuses_credentials_for_a_different_hub(self) -> None:
        entry = ConfigEntry(
            entry_id="entry-1",
            unique_id="hub-1",
            data={
                CONF_HOST: "192.0.2.10",
                CONF_PASSWORD: DEFAULT_PASSWORD,
                CONF_APP_VERSION: DEFAULT_APP_VERSION,
            },
        )
        flow = ConfigFlow()
        flow.hass = FakeHass(config_entries=FakeConfigEntries(entries=[entry]))
        flow.context = {"entry_id": entry.entry_id}

        with patch(
            "custom_components.norman_gen1.config_flow._validate_input",
            AsyncMock(return_value={"hubId": "hub-2", "hubName": "Other"}),
        ):
            result = await flow.async_step_reauth_confirm(
                {CONF_PASSWORD: "replacement"}
            )

        self.assertEqual(result, {"type": "abort", "reason": "wrong_hub"})
        self.assertEqual(entry.data[CONF_PASSWORD], DEFAULT_PASSWORD)

    async def test_reauth_keeps_legacy_id_until_setup_migration(self) -> None:
        entry = ConfigEntry(
            entry_id="entry-1",
            unique_id="192.0.2.10",
            data={
                CONF_HOST: "192.0.2.10",
                CONF_PASSWORD: DEFAULT_PASSWORD,
                CONF_APP_VERSION: DEFAULT_APP_VERSION,
            },
        )
        flow = ConfigFlow()
        flow.hass = FakeHass(config_entries=FakeConfigEntries(entries=[entry]))
        flow.context = {"entry_id": entry.entry_id}

        with patch(
            "custom_components.norman_gen1.config_flow._validate_input",
            AsyncMock(return_value={"hubId": "hub-1", "hubName": "Home"}),
        ):
            result = await flow.async_step_reauth_confirm(
                {CONF_PASSWORD: "replacement"}
            )

        self.assertEqual(result["reason"], "reauth_successful")
        self.assertEqual(entry.unique_id, "192.0.2.10")
        self.assertEqual(flow.hass.config_entries.scheduled_reloads, [entry.entry_id])

    async def test_reconfigure_validates_and_updates_connection_settings(self) -> None:
        entry = ConfigEntry(
            entry_id="entry-1",
            unique_id="hub-1",
            data={
                CONF_HOST: "192.0.2.10",
                CONF_PASSWORD: DEFAULT_PASSWORD,
                CONF_APP_VERSION: DEFAULT_APP_VERSION,
            },
        )
        flow = ConfigFlow()
        flow.hass = FakeHass(config_entries=FakeConfigEntries(entries=[entry]))
        flow.context = {"entry_id": entry.entry_id}

        form = await flow.async_step_reconfigure()
        self.assertEqual(form["step_id"], "reconfigure")

        updated_data = {
            CONF_HOST: "192.0.2.20",
            CONF_PASSWORD: DEFAULT_PASSWORD,
            CONF_APP_VERSION: "2.12.0",
        }
        with patch(
            "custom_components.norman_gen1.config_flow._validate_input",
            AsyncMock(return_value={"hubId": "hub-1", "hubName": "Home"}),
        ):
            result = await flow.async_step_reconfigure(updated_data)

        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(entry.data, updated_data)


if __name__ == "__main__":
    unittest.main()
