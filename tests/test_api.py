from __future__ import annotations

import asyncio
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

import custom_components.norman_gen1.api as api_module
from custom_components.norman_gen1.api import (
    CannotConnect,
    CannotControl,
    InvalidAuth,
    InvalidSession,
    NormanGen1Api,
    PositionProfile,
    UnexpectedHub,
    group_target_id,
    hub_position_to_ha,
    position_is_closed,
    room_close_position,
    room_open_position,
    room_target_id,
    target_override_enabled,
)


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

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def text(self) -> str:
        return self._text

    async def read(self) -> bytes:
        return self._text.encode()

    async def json(self, content_type: Any = None) -> Any:
        if self._json_data is None:
            raise ValueError("No JSON payload configured")
        return self._json_data


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
        allow_redirects: bool,
    ) -> FakeResponse:
        self.requests.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        if not self._responses:
            raise AssertionError("No fake response remaining")
        return self._responses.pop(0)


class TestRoomPositionControl(unittest.TestCase):
    def test_room_open_position_uses_tilt_style_default(self) -> None:
        self.assertEqual(room_open_position({"Style": 2}), 37)
        self.assertEqual(room_open_position({"Style": 3}), 37)
        self.assertEqual(room_open_position({"Style": 13}), 37)
        self.assertEqual(room_open_position({"Style": 99}), 100)

    def test_room_close_position_uses_tested_style_defaults(self) -> None:
        self.assertEqual(room_close_position({"Style": 2}), 0)
        self.assertEqual(room_close_position({"Style": 3}), 0)
        self.assertEqual(room_close_position({"Style": 13}), 100)
        self.assertEqual(room_close_position({"Style": 99}), 0)

    def test_user_override_can_force_tilt_open_target(self) -> None:
        self.assertEqual(room_open_position({"Style": 99}, True), 37)
        self.assertEqual(room_open_position({"Style": 13}, False), 100)
        self.assertEqual(room_open_position({"Style": 99}, False), 100)

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
        self.assertTrue(position_is_closed(0, 0, closes_at_both_ends=True))
        self.assertTrue(position_is_closed(100, 100, closes_at_both_ends=True))
        self.assertFalse(position_is_closed(37, 100, closes_at_both_ends=True))

    def test_position_is_closed_handles_normal_covers(self) -> None:
        self.assertTrue(position_is_closed(0, 0))
        self.assertFalse(position_is_closed(100, 0))

    def test_normal_cover_never_treats_open_end_stop_as_closed(self) -> None:
        self.assertFalse(position_is_closed(100, 0, closes_at_both_ends=False))

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

    def test_room_commands_sleep_only_between_groups(self) -> None:
        api = RecordingApi()

        with patch(
            "custom_components.norman_gen1.api.asyncio.sleep", AsyncMock()
        ) as sleep:
            asyncio.run(api.set_room_position(56548, [0, 1], 100))

        sleep.assert_awaited_once_with(0.15)

    def test_intermediate_position_needs_discovered_levels(self) -> None:
        api = RecordingApi()

        with self.assertRaises(CannotControl):
            asyncio.run(api.set_room_position(56548, [], 50))


class TestGatewayLoginRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_login_drains_streaming_500_before_starting_recovery(
        self,
    ) -> None:
        first_login = True
        body_handler_done = asyncio.Event()
        body_finished = False
        logout_started_after_body: list[bool] = []

        async def gateway_login(request: web.Request) -> web.StreamResponse:
            nonlocal body_finished, first_login
            if not first_login:
                return web.json_response(
                    {"hubId": "test-hub-1", "hubName": "home"},
                    headers={"Set-Cookie": "Session=fresh; Path=/"},
                )

            first_login = False
            response = web.StreamResponse(
                status=500,
                headers={"Content-Type": "text/html"},
            )
            await response.prepare(request)
            await response.write(b"<html><body>")
            try:
                await asyncio.sleep(0.1)
                await response.write(b"Internal Server Error</body></html>")
                await response.write_eof()
                body_finished = True
            except (ConnectionError, RuntimeError):
                # The red test's current implementation releases the connection
                # without consuming the remaining body. Keep the server fixture
                # alive long enough to record the protocol ordering either way.
                pass
            finally:
                body_handler_done.set()
            return response

        async def logout(request: web.Request) -> web.Response:
            logout_started_after_body.append(body_finished)
            return web.json_response({"status": "Success"})

        app = web.Application()
        app.router.add_post("/cgi-bin/cgi/GatewayLogin", gateway_login)
        app.router.add_post("/cgi-bin/cgi/{endpoint:AdminLogout|GatewayLogout}", logout)
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as session:
                api = NormanGen1Api(
                    session,
                    f"{server.host}:{server.port}",
                    "123456789",
                )

                info = await api.login()
                await asyncio.wait_for(body_handler_done.wait(), timeout=1)
        finally:
            await server.close()

        self.assertEqual(info["hubName"], "home")
        self.assertEqual(logout_started_after_body, [True, True])

    async def test_login_uses_error_response_cookie_to_clear_stale_session(
        self,
    ) -> None:
        stale_session = True
        requests: list[tuple[str, str | None]] = []

        async def gateway_login(request: web.Request) -> web.Response:
            requests.append(("GatewayLogin", request.headers.get("Cookie")))
            if stale_session:
                return web.Response(
                    status=500,
                    text="<html><title>500 Internal Server Error</title></html>",
                    headers={"Set-Cookie": "Session=stale; Path=/"},
                )
            return web.json_response(
                {"hubId": "test-hub-1", "hubName": "home"},
                headers={"Set-Cookie": "Session=fresh; Path=/"},
            )

        async def logout(request: web.Request) -> web.Response:
            nonlocal stale_session
            endpoint = request.match_info["endpoint"]
            cookie = request.headers.get("Cookie")
            requests.append((endpoint, cookie))
            if cookie == "Session=stale":
                stale_session = False
            return web.json_response({"status": "Success"})

        app = web.Application()
        app.router.add_post("/cgi-bin/cgi/GatewayLogin", gateway_login)
        app.router.add_post("/cgi-bin/cgi/{endpoint:AdminLogout|GatewayLogout}", logout)
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as session:
                api = NormanGen1Api(
                    session,
                    f"{server.host}:{server.port}",
                    "123456789",
                )

                info = await api.login()
        finally:
            await server.close()

        self.assertEqual(info["hubName"], "home")
        self.assertEqual(api._session_cookie, "Session=fresh")
        self.assertEqual(
            requests,
            [
                ("GatewayLogin", None),
                ("AdminLogout", "Session=stale"),
                ("GatewayLogout", "Session=stale"),
                ("GatewayLogin", None),
            ],
        )

    async def test_login_uses_reissued_logout_cookie_for_stale_session_retry(
        self,
    ) -> None:
        requests: list[tuple[str, str | None]] = []

        async def gateway_login(request: web.Request) -> web.Response:
            cookie = request.headers.get("Cookie")
            requests.append(("GatewayLogin", cookie))
            if cookie != "Session=0":
                return web.Response(
                    status=500,
                    text="<html><title>500 Internal Server Error</title></html>",
                    headers={"Set-Cookie": "Session=0; Path=/"},
                )
            return web.json_response(
                {"hubId": "test-hub-1", "hubName": "home"},
                headers={"Set-Cookie": "Session=fresh; Path=/"},
            )

        async def logout(request: web.Request) -> web.Response:
            endpoint = request.match_info["endpoint"]
            requests.append((endpoint, request.headers.get("Cookie")))
            headers = (
                {"Set-Cookie": "Session=0; Path=/"}
                if endpoint == "GatewayLogout"
                else None
            )
            return web.json_response({"status": "Success"}, headers=headers)

        app = web.Application()
        app.router.add_post("/cgi-bin/cgi/GatewayLogin", gateway_login)
        app.router.add_post("/cgi-bin/cgi/{endpoint:AdminLogout|GatewayLogout}", logout)
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as session:
                api = NormanGen1Api(
                    session,
                    f"{server.host}:{server.port}",
                    "123456789",
                )

                info = await api.login()
        finally:
            await server.close()

        self.assertEqual(info["hubName"], "home")
        self.assertEqual(api._session_cookie, "Session=fresh")
        self.assertEqual(
            requests,
            [
                ("GatewayLogin", None),
                ("AdminLogout", "Session=0"),
                ("GatewayLogout", "Session=0"),
                ("GatewayLogin", "Session=0"),
            ],
        )

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
                    text='{ "hubId": "test-hub-1", "hubName": "home" }',
                    json_data={"hubId": "test-hub-1", "hubName": "home"},
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
                    text='{ "hubId": "test-hub-1", "hubName": "home" }',
                    json_data={"hubId": "test-hub-1", "hubName": "home"},
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

    async def test_persistent_generic_login_500_requests_hub_restart(self) -> None:
        session = FakeSession(
            [
                FakeResponse(status=500, text="Internal Server Error"),
                FakeResponse(status=500, text="Internal Server Error"),
                FakeResponse(status=500, text="Internal Server Error"),
                FakeResponse(status=500, text="Internal Server Error"),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        with self.assertRaises(api_module.HubNeedsRestart) as caught:
            await api.login()

        self.assertEqual(
            str(caught.exception),
            "The Norman hub did not recover its login service; restart the hub and try again",
        )
        self.assertEqual(
            [request["url"].rsplit("/", 1)[-1] for request in session.requests],
            ["GatewayLogin", "AdminLogout", "GatewayLogout", "GatewayLogin"],
        )

    async def test_string_invalid_auth_error_code_is_normalized(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text='{ "errorCode": "-13" }',
                    json_data={"errorCode": "-13"},
                )
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        with self.assertRaises(InvalidAuth):
            await api.login()


class TestAdditionalLoginBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_zero_error_code_is_accepted(self) -> None:
        for error_code in (0, "0"):
            with self.subTest(error_code=error_code):
                session = FakeSession(
                    [
                        FakeResponse(
                            status=200,
                            text="{}",
                            json_data={"errorCode": error_code, "hubId": "hub-1"},
                            headers={"Set-Cookie": "Session=1"},
                        )
                    ]
                )
                api = NormanGen1Api(session, "192.0.2.10", "123456789")

                info = await api.login()

                self.assertEqual(info["hubId"], "hub-1")

    async def test_login_does_not_reuse_a_cookie_missing_from_new_response(
        self,
    ) -> None:
        session = FakeSession(
            [FakeResponse(status=200, text="{}", json_data={"hubId": "hub-1"})]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=stale"

        await api.login()

        self.assertIsNone(api._session_cookie)

    async def test_login_error_does_not_echo_password_from_response(self) -> None:
        password = "private-custom-password"
        session = FakeSession([FakeResponse(status=403, text=f"password={password}")])
        api = NormanGen1Api(session, "192.0.2.10", password)

        with self.assertRaises(InvalidAuth) as caught:
            await api.login()

        self.assertNotIn(password, str(caught.exception))

    async def test_login_never_follows_redirects(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={},
                    headers={"Set-Cookie": "Session=1"},
                )
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        await api.login()

        self.assertFalse(session.requests[0]["allow_redirects"])

    async def test_http_auth_status_is_classified_as_invalid_password(self) -> None:
        session = FakeSession([FakeResponse(status=401, text="unauthorized")])
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        with self.assertRaises(InvalidAuth):
            await api.login()


class TestApiPayloadValidation(unittest.IsolatedAsyncioTestCase):
    async def test_nonzero_data_error_code_is_not_treated_as_empty_success(
        self,
    ) -> None:
        session = FakeSession(
            [FakeResponse(status=200, text="{}", json_data={"errorCode": "-7"})]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        with self.assertRaises(CannotConnect):
            await api.get_rooms()

    async def test_http_auth_status_is_classified_as_invalid_session(self) -> None:
        session = FakeSession([FakeResponse(status=403, text="forbidden")])
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        with self.assertRaises(InvalidSession):
            await api.get_rooms()

    def test_malformed_top_level_collection_is_rejected(self) -> None:
        api = NormanGen1Api(object(), "192.0.2.10", "123456789")

        for payload in ({}, {"windows": {}}, {"windows": "bad"}):
            with self.subTest(payload=payload), self.assertRaises(CannotConnect):
                api._parse_windows(payload)

    async def test_unrelated_numeric_field_does_not_confirm_remote_command(
        self,
    ) -> None:
        session = FakeSession(
            [FakeResponse(status=200, text="{}", json_data={"model": 1})]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        with self.assertRaises(CannotControl):
            await api.set_group_position(1, 0, 100)

    async def test_known_status_field_confirms_remote_command(self) -> None:
        session = FakeSession(
            [FakeResponse(status=200, text="{}", json_data={"status": "success"})]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        await api.set_group_position(1, 0, 100)

    async def test_live_remote_ok_field_confirms_remote_command(self) -> None:
        session = FakeSession(
            [FakeResponse(status=200, text="{}", json_data={"remote": "ok"})]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        await api.set_group_position(1, 0, 100)

    def test_window_parser_normalizes_aliases_and_malformed_numbers(self) -> None:
        api = NormanGen1Api(object(), "192.0.2.10", "123456789")

        windows = api._parse_windows(
            {
                "windows": [
                    {
                        "Id": None,
                        "id": "7",
                        "roomId": None,
                        "RId": "8",
                        "Level": None,
                        "Lid": "1",
                        "position": float("inf"),
                        "model": "not-a-number",
                    }
                ]
            }
        )

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].id, 7)
        self.assertEqual(windows[0].room_id, 8)
        self.assertEqual(windows[0].level, 1)
        self.assertIsNone(windows[0].position)
        self.assertEqual(windows[0].model, 1)

    async def test_single_group_name_is_not_split_into_characters(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={
                        "rooms": [{"Id": 1, "Name": "Office", "groupname": "Panel"}]
                    },
                )
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        rooms = await api.get_rooms()

        self.assertEqual(rooms[0].group_names, ["Panel"])

    def test_model_zero_is_preserved(self) -> None:
        api = NormanGen1Api(object(), "192.0.2.10", "123456789")

        windows = api._parse_windows(
            {"windows": [{"Id": 1, "roomId": 1, "Level": 0, "model": 0}]}
        )

        self.assertEqual(windows[0].model, 0)

    def test_fractional_positions_are_rejected_without_truncation(self) -> None:
        api = NormanGen1Api(object(), "192.0.2.10", "123456789")

        windows = api._parse_windows(
            {
                "windows": [
                    {"Id": 1, "roomId": 1, "Level": 0, "position": 37.9},
                    {"Id": 2, "roomId": 1, "Level": 0, "position": 37.0},
                ]
            }
        )

        self.assertIsNone(windows[0].position)
        self.assertEqual(windows[1].position, 37)


class TestAuthenticatedSession(unittest.IsolatedAsyncioTestCase):
    async def test_dummy_cookie_jar_cannot_override_managed_session_cookie(
        self,
    ) -> None:
        received_cookies: list[str | None] = []

        async def rooms(request: web.Request) -> web.Response:
            received_cookies.append(request.headers.get("Cookie"))
            return web.json_response({"rooms": []})

        app = web.Application()
        app.router.add_post("/cgi-bin/cgi/getRoomInfo", rooms)
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as session:
                session.cookie_jar.update_cookies({"Session": "jar-session"})
                api = NormanGen1Api(
                    session,
                    f"{server.host}:{server.port}",
                    "123456789",
                )
                api._session_cookie = "Session=managed-session"

                self.assertEqual(await api.get_rooms(), [])
        finally:
            await server.close()

        self.assertEqual(received_cookies, ["Session=managed-session"])

    async def test_authenticated_sessions_are_serialized(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={},
                    headers={"Set-Cookie": "Session=1"},
                ),
                FakeResponse(status=200, text="{}", json_data={}),
                FakeResponse(status=200, text="{}", json_data={}),
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={},
                    headers={"Set-Cookie": "Session=2"},
                ),
                FakeResponse(status=200, text="{}", json_data={}),
                FakeResponse(status=200, text="{}", json_data={}),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        active = 0
        maximum_active = 0

        async def operation() -> None:
            nonlocal active, maximum_active
            async with api.authenticated_session():
                active += 1
                maximum_active = max(maximum_active, active)
                await asyncio.sleep(0)
                active -= 1

        await asyncio.gather(operation(), operation())

        self.assertEqual(maximum_active, 1)


class TestProtocolEdgeCoverage(unittest.IsolatedAsyncioTestCase):
    async def test_identity_properties_and_pinning(self) -> None:
        api = NormanGen1Api(
            object(),
            "http://192.0.2.10/",
            "123456789",
            expected_hub_id="hub-1",
        )

        self.assertEqual(api.base_url, "http://192.0.2.10/cgi-bin/cgi")
        self.assertEqual(api.hub_id, "192.0.2.10")
        self.assertIsInstance(api.transaction_lock, asyncio.Lock)
        api.pin_hub_id("hub-1")
        with self.assertRaises(UnexpectedHub):
            api.pin_hub_id("hub-2")

    async def test_login_rejects_changed_identity_and_malformed_error(self) -> None:
        for payload, expected_error in (
            ({"hubId": "hub-2"}, UnexpectedHub),
            ({"hubId": {"nested": "value"}}, CannotConnect),
            ({"errorCode": "bad"}, CannotConnect),
        ):
            with self.subTest(payload=payload):
                session = FakeSession(
                    [FakeResponse(status=200, text="{}", json_data=payload)]
                )
                api = NormanGen1Api(
                    session,
                    "192.0.2.10",
                    "123456789",
                    expected_hub_id="hub-1",
                )
                with self.assertRaises(expected_error):
                    await api.login()

    async def test_login_normalizes_optional_home_assistant_metadata(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={
                        "hubId": 42,
                        "hubName": "  My   hub  ",
                        "swVer": 1.25,
                        "firmwareVersion": {"private": "value"},
                        "status": ["unexpected"],
                        "unknownToken": "do-not-retain",
                    },
                )
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        info = await api.login()

        self.assertEqual(
            info,
            {"hubId": "42", "hubName": "My hub", "swVer": "1.25"},
        )
        self.assertEqual(api.hub_info, info)

    async def test_non_gateway_server_error_is_not_retried(self) -> None:
        session = FakeSession([FakeResponse(status=503, text="offline")])
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        with self.assertRaises(CannotConnect):
            await api.login()

        self.assertEqual(len(session.requests), 1)

    async def test_logout_without_cookie_and_serialized_close(self) -> None:
        api = NormanGen1Api(object(), "192.0.2.10", "123456789")
        await api.logout()

        session = FakeSession(
            [
                FakeResponse(status=200, text="{}", json_data={}),
                FakeResponse(status=200, text="{}", json_data={}),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"
        await api.async_close()

        self.assertIsNone(api._session_cookie)
        self.assertEqual(len(session.requests), 2)

    async def test_room_and_window_parsers_cover_optional_fields(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={
                        "rooms": [
                            {"Id": -1, "Name": "Invalid"},
                            {"Name": "Missing"},
                            {"Id": 1, "Name": "Office"},
                            {
                                "Id": 2,
                                "Name": "Lounge",
                                "groupname": ["Left", 2],
                            },
                        ]
                    },
                ),
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={
                        "windows": [
                            "bad-record",
                            {"Name": "Missing"},
                            {
                                "Id": 1,
                                "roomId": 1,
                                "Level": 0,
                                "position": 101,
                            },
                        ]
                    },
                ),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"

        rooms = await api.get_rooms()
        windows = await api.get_windows()

        self.assertEqual([room.group_names for room in rooms], [[], ["Left", "2"]])
        self.assertEqual(len(windows), 1)
        self.assertIsNone(windows[0].position)

    async def test_auto_login_and_session_error_code_paths(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={"hubId": "hub-1"},
                    headers={"session": "24680; Path=/"},
                ),
                FakeResponse(
                    status=200,
                    text="{}",
                    json_data={"windows": []},
                ),
            ]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")

        self.assertEqual(await api.get_windows(), [])
        self.assertEqual(api._session_cookie, "Session=24680")

        session = FakeSession(
            [FakeResponse(status=200, text="{}", json_data={"errorCode": -13})]
        )
        api = NormanGen1Api(session, "192.0.2.10", "123456789")
        api._session_cookie = "Session=1"
        with self.assertRaises(InvalidSession):
            await api.get_windows()

    async def test_transport_and_payload_failures_are_normalized(self) -> None:
        cases = [
            FakeResponse(status=200, text="not-json"),
            FakeResponse(status=200, text="[]", json_data=[]),
        ]
        for response in cases:
            with self.subTest(response=response):
                api = NormanGen1Api(FakeSession([response]), "192.0.2.10", "123456789")
                with self.assertRaises(CannotConnect):
                    await api.login()

        class ErrorSession:
            def post(self, *args: Any, **kwargs: Any):
                raise aiohttp.ClientError("network down")

        with self.assertRaises(CannotConnect):
            await NormanGen1Api(ErrorSession(), "192.0.2.10", "123456789").login()

    async def test_remote_control_rejects_malformed_and_nonzero_codes(self) -> None:
        for error_code in ("bad", 7):
            with self.subTest(error_code=error_code):
                session = FakeSession(
                    [
                        FakeResponse(
                            status=200,
                            text="{}",
                            json_data={"errorCode": error_code},
                        )
                    ]
                )
                api = NormanGen1Api(session, "192.0.2.10", "123456789")
                api._session_cookie = "Session=1"
                with self.assertRaises(CannotControl):
                    await api.set_group_position(1, 0, 100)

    async def test_all_supported_remote_success_shapes(self) -> None:
        for payload in ({"success": True}, {"result": 1}, {"status": "OK"}):
            with self.subTest(payload=payload):
                api = NormanGen1Api(
                    FakeSession(
                        [FakeResponse(status=200, text="{}", json_data=payload)]
                    ),
                    "192.0.2.10",
                    "123456789",
                )
                api._session_cookie = "Session=1"
                await api.set_group_position(1, 0, 100)

    async def test_full_open_fallback_without_levels(self) -> None:
        api = RecordingApi()

        await api.set_room_position(4, [], 100)

        self.assertEqual(
            api.calls,
            [{"type": "fullopen", "action": 2, "id": 4}],
        )

    def test_degenerate_profiles_do_not_divide_by_zero(self) -> None:
        self.assertEqual(hub_position_to_ha(0, PositionProfile(0, 100, True)), 0)
        self.assertEqual(hub_position_to_ha(100, PositionProfile(100, 0, True)), 0)
        self.assertIsNone(hub_position_to_ha(50, PositionProfile(50, 50, False)))


if __name__ == "__main__":
    unittest.main()
