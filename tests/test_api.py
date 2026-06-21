from __future__ import annotations

import asyncio
import importlib
import pathlib
import sys
import types
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT_PATH = ROOT / "custom_components" / "norman_gen1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

norman_package = types.ModuleType("custom_components.norman_gen1")
norman_package.__path__ = [str(COMPONENT_PATH)]
sys.modules.setdefault("custom_components.norman_gen1", norman_package)

api_module = importlib.import_module("custom_components.norman_gen1.api")
CannotControl = api_module.CannotControl
NormanGen1Api = api_module.NormanGen1Api
remember_open_position = api_module.remember_open_position
room_open_position = api_module.room_open_position
room_close_position = api_module.room_close_position
room_target_id = api_module.room_target_id
group_target_id = api_module.group_target_id
target_override_enabled = api_module.target_override_enabled
position_is_closed = api_module.position_is_closed


class RecordingApi(NormanGen1Api):
    def __init__(self) -> None:
        super().__init__(object(), "192.0.2.10", "password")
        self.calls: list[dict[str, Any]] = []

    async def _remote_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {"errorCode": 0}


class FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        text: str,
        json_data: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._text = text
        self._json_data = json_data
        self.headers = headers or {}

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def text(self) -> str:
        return self._text

    async def json(self, content_type: Any = None) -> Any:
        if self._json_data is None:
            raise ValueError("No JSON payload configured")
        return self._json_data


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> FakeResponse:
        self.requests.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self._responses:
            raise AssertionError("No fake response remaining")
        return self._responses.pop(0)


class TestRoomPositionControl(unittest.TestCase):
    def test_intermediate_position_is_remembered_as_open_target(self) -> None:
        self.assertEqual(remember_open_position(None, 37), 37)
        self.assertEqual(remember_open_position(37, 0), 37)
        self.assertEqual(remember_open_position(37, 100), 37)
        self.assertIsNone(remember_open_position(None, 0))
        self.assertIsNone(remember_open_position(None, 100))

    def test_room_open_position_uses_tilt_style_default(self) -> None:
        self.assertEqual(room_open_position({"Style": 2}, None), 37)
        self.assertEqual(room_open_position({"Style": 3}, None), 37)
        self.assertEqual(room_open_position({"Style": 13}, None), 37)
        self.assertEqual(room_open_position({"Style": 13}, 42), 37)
        self.assertEqual(room_open_position({"Style": 99}, None), 100)
        self.assertEqual(room_open_position({"Style": 99}, 42), 42)

    def test_room_close_position_uses_tested_style_defaults(self) -> None:
        self.assertEqual(room_close_position({"Style": 2}), 0)
        self.assertEqual(room_close_position({"Style": 3}), 0)
        self.assertEqual(room_close_position({"Style": 13}), 100)
        self.assertEqual(room_close_position({"Style": 99}), 0)

    def test_user_override_can_force_tilt_open_target(self) -> None:
        self.assertEqual(room_open_position({"Style": 99}, None, True), 37)
        self.assertEqual(room_open_position({"Style": 13}, 42, False), 100)
        self.assertEqual(room_open_position({"Style": 99}, 42, False), 42)

    def test_user_override_can_force_close_direction(self) -> None:
        self.assertEqual(room_close_position({"Style": 99}, True), 100)
        self.assertEqual(room_close_position({"Style": 13}, False), 0)

    def test_room_override_targets_apply_to_room_and_groups(self) -> None:
        targets = [room_target_id(35053), group_target_id(6559, 2)]

        self.assertTrue(target_override_enabled(targets, 35053))
        self.assertTrue(target_override_enabled(targets, 35053, 4))
        self.assertFalse(target_override_enabled(targets, 6559))
        self.assertTrue(target_override_enabled(targets, 6559, 2))
        self.assertFalse(target_override_enabled(targets, 6559, 3))

    def test_position_is_closed_handles_tilt_shutter_end_stops(self) -> None:
        self.assertTrue(position_is_closed(0, 37, 0))
        self.assertTrue(position_is_closed(100, 37, 100))
        self.assertFalse(position_is_closed(37, 37, 100))

    def test_position_is_closed_handles_normal_covers(self) -> None:
        self.assertTrue(position_is_closed(0, 100, 0))
        self.assertFalse(position_is_closed(100, 100, 0))

    def test_room_close_uses_discovered_group_levels(self) -> None:
        api = RecordingApi()

        asyncio.run(api.set_room_position(56548, [3, 1, 1, 0], 0, {1: 2}))

        self.assertEqual(
            api.calls,
            [
                {"type": "level", "Lid": 0, "id": 56548, "action": 0, "model": 1},
                {"type": "level", "Lid": 1, "id": 56548, "action": 0, "model": 2},
                {"type": "level", "Lid": 3, "id": 56548, "action": 0, "model": 1},
            ],
        )

    def test_room_open_uses_discovered_group_levels(self) -> None:
        api = RecordingApi()

        asyncio.run(api.set_room_position(56548, [0, 1], 100))

        self.assertEqual(
            api.calls,
            [
                {"type": "level", "Lid": 0, "id": 56548, "action": 100, "model": 1},
                {"type": "level", "Lid": 1, "id": 56548, "action": 100, "model": 1},
            ],
        )

    def test_room_close_falls_back_to_full_close_without_levels(self) -> None:
        api = RecordingApi()

        asyncio.run(api.set_room_position(56548, [], 0))

        self.assertEqual(api.calls, [{"type": "fullclose", "action": 2, "id": 56548}])

    def test_intermediate_position_needs_discovered_levels(self) -> None:
        api = RecordingApi()

        with self.assertRaises(CannotControl):
            asyncio.run(api.set_room_position(56548, [], 50))


class TestGatewayLoginRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_login_forces_logout_and_retries_after_http_500(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=500,
                    text="<html><title>500 Internal Server Error</title></html>",
                    headers={"Content-Type": "text/html"},
                ),
                FakeResponse(
                    status=200,
                    text='{ "status": "Success" }',
                    json_data={"status": "Success"},
                ),
                FakeResponse(
                    status=200,
                    text='{ "status": "Success" }',
                    json_data={"status": "Success"},
                    headers={"Set-Cookie": "Session=0"},
                ),
                FakeResponse(
                    status=200,
                    text='{ "hubId": "MBAHUB_FAE224", "hubName": "home" }',
                    json_data={"hubId": "MBAHUB_FAE224", "hubName": "home"},
                    headers={"Set-Cookie": "Session=24680"},
                ),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        info = await api.login()

        self.assertEqual(info["hubName"], "home")
        self.assertEqual(api._session_cookie, "Session=24680")
        self.assertEqual(
            [request["url"].rsplit("/", 1)[-1] for request in session.requests],
            ["GatewayLogin", "AdminLogout", "GatewayLogout", "GatewayLogin"],
        )
        self.assertNotIn("Cookie", session.requests[1]["headers"])
        self.assertNotIn("Cookie", session.requests[2]["headers"])

    async def test_login_still_tries_gateway_logout_if_admin_logout_fails(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=500,
                    text="<html><title>500 Internal Server Error</title></html>",
                    headers={"Content-Type": "text/html"},
                ),
                FakeResponse(
                    status=500,
                    text="<html><title>500 Internal Server Error</title></html>",
                ),
                FakeResponse(
                    status=200,
                    text='{ "status": "Success" }',
                    json_data={"status": "Success"},
                ),
                FakeResponse(
                    status=200,
                    text='{ "hubId": "MBAHUB_FAE224", "hubName": "home" }',
                    json_data={"hubId": "MBAHUB_FAE224", "hubName": "home"},
                    headers={"Set-Cookie": "Session=24680"},
                ),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        info = await api.login()

        self.assertEqual(info["hubName"], "home")
        self.assertEqual(
            [request["url"].rsplit("/", 1)[-1] for request in session.requests],
            ["GatewayLogin", "AdminLogout", "GatewayLogout", "GatewayLogin"],
        )


if __name__ == "__main__":
    unittest.main()
