"""Test transient Gen 1 login failures without hiding other hub errors."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace, TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock, call, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
import pytest

from custom_components.norman_gen1 import api as api_module
from custom_components.norman_gen1.api import (
    CannotConnect,
    HubNeedsRestart,
    InvalidAuth,
    NormanGen1Api,
    NormanRoom,
    NormanWindow,
    UnexpectedHub,
)
from custom_components.norman_gen1.coordinator import NormanDataUpdateCoordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeResponse:
    """Minimal aiohttp response with a controlled status and JSON body."""

    def __init__(
        self,
        status: int,
        *,
        body: dict[str, Any] | None = None,
        cookie: str | None = None,
    ) -> None:
        """Set the HTTP status, body, and optional session cookie."""
        self.status = status
        self.body = body or {}
        self.headers = {"Set-Cookie": cookie} if cookie else {}

    async def __aenter__(self) -> Self:
        """Return this response for the API client's async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Leave response handling to the test client."""
        return False

    async def read(self) -> bytes:
        """Return a JSON-shaped response body for status parsing."""
        return b"{}"

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        """Return the scripted response payload."""
        return self.body


class FakeSession:
    """Serve scripted responses while recording each hub request."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        """Copy the scripted responses for this fake session."""
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a POST and return the next scripted response."""
        self.requests.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No fake HTTP response remains")
        return self.responses.pop(0)


def _login_retry_responses(*, final_status: int) -> list[FakeResponse]:
    """Return one failed login, one logout transition, then another login."""
    return [
        FakeResponse(500, cookie="Session=stale; Path=/"),
        FakeResponse(200, body={"errorCode": 0}, cookie="Session=admin; Path=/"),
        FakeResponse(200, body={"errorCode": 0}, cookie="Session=transition; Path=/"),
        FakeResponse(
            final_status,
            body={"hubId": "hub-1"} if final_status == 200 else None,
            cookie="Session=fresh; Path=/" if final_status == 200 else None,
        ),
    ]


def _login_asyncio_facade(sleep: AsyncMock) -> SimpleNamespace:
    """Isolate the API module's backoff from Home Assistant's event loop."""
    return SimpleNamespace(Lock=asyncio.Lock, sleep=sleep)


async def test_login_retries_http_500_after_five_seconds_and_recovers() -> None:
    """One forced logout is followed by a delayed GatewayLogin retry."""
    session = FakeSession(_login_retry_responses(final_status=200))
    api = NormanGen1Api(session, "192.0.2.10", "password")
    sleep = AsyncMock()
    with patch.object(api_module, "asyncio", _login_asyncio_facade(sleep)):
        info = await api.login()

    endpoints = [request["url"].rsplit("/", 1)[-1] for request in session.requests]
    assert endpoints == [
        "GatewayLogin",
        "AdminLogout",
        "GatewayLogout",
        "GatewayLogin",
    ]
    sleep.assert_has_awaits([call(5.0)])
    assert session.requests[-1]["headers"]["Cookie"] == "Session=transition"
    assert info["hubId"] == "hub-1"


async def test_login_http_500_exhausts_three_attempts_and_logs_out_once() -> None:
    """Persistent login 500s consume a bounded retry budget."""
    responses = _login_retry_responses(final_status=500)
    responses.extend(
        [
            FakeResponse(500, cookie="Session=second; Path=/"),
            FakeResponse(500, cookie="Session=third; Path=/"),
        ]
    )
    session = FakeSession(responses)
    api = NormanGen1Api(session, "192.0.2.10", "password")
    sleep = AsyncMock()
    with (
        patch.object(api_module, "asyncio", _login_asyncio_facade(sleep)),
        pytest.raises(HubNeedsRestart, match="check the hub connection"),
    ):
        await api.login()

    endpoints = [request["url"].rsplit("/", 1)[-1] for request in session.requests]
    assert endpoints.count("GatewayLogin") == 3
    assert endpoints.count("AdminLogout") == 1
    assert endpoints.count("GatewayLogout") == 1
    sleep.assert_has_awaits([call(5.0), call(5.0)])
    assert session.requests[-1]["headers"].get("Cookie") is None


async def test_login_does_not_retry_invalid_credentials() -> None:
    """Authentication errors remain immediate and do not trigger logout."""
    session = FakeSession([FakeResponse(403)])
    api = NormanGen1Api(session, "192.0.2.10", "password")
    sleep = AsyncMock()
    with (
        patch.object(api_module, "asyncio", _login_asyncio_facade(sleep)),
        pytest.raises(InvalidAuth),
    ):
        await api.login()

    assert len(session.requests) == 1
    assert session.requests[0]["url"].endswith("GatewayLogin")
    sleep.assert_not_awaited()


class CoordinatorApi:
    """Small protocol double for the coordinator's snapshot boundary."""

    def __init__(self) -> None:
        """Prepare one representative room and physical motor."""
        self.errors: list[Exception] = []
        self.room = NormanRoom(1, "Living room", ["Left"], {"Style": 99})
        self.window = NormanWindow(
            id=7,
            name="Left panel",
            room_id=1,
            level=0,
            group_id=1,
            position=37,
            model=1,
            battery=80,
            raw={},
        )

    @asynccontextmanager
    async def authenticated_session(self):
        """Raise the next scripted failure before yielding a session."""
        if self.errors:
            raise self.errors.pop(0)
        yield {}

    async def get_rooms(self) -> list[NormanRoom]:
        """Return the one discovered room."""
        return [self.room]

    async def get_windows(self) -> list[NormanWindow]:
        """Return the one discovered motor."""
        return [self.window]


async def test_coordinator_keeps_snapshot_for_two_login_failures_then_recovers(
    hass, mock_config_entry
) -> None:
    """Only two consecutive HubNeedsRestart polls reuse the previous data."""
    api = CoordinatorApi()
    coordinator = NormanDataUpdateCoordinator(hass, api, mock_config_entry)
    await coordinator.async_refresh()
    original = coordinator.data
    assert original is not None
    assert coordinator.last_update_success

    api.errors.extend(
        [
            HubNeedsRestart("retry login"),
            HubNeedsRestart("retry login"),
            HubNeedsRestart("retry login"),
        ]
    )
    await coordinator.async_refresh()
    assert coordinator.data is original
    assert coordinator.last_update_success
    assert coordinator.transient_failure_count == 1
    await coordinator.async_refresh()
    assert coordinator.data is original
    assert coordinator.last_update_success
    assert coordinator.transient_failure_count == 2
    await coordinator.async_refresh()
    assert coordinator.data is original
    assert not coordinator.last_update_success
    assert coordinator.transient_failure_count == 3

    await coordinator.async_refresh()
    recovered = coordinator.data
    assert coordinator.last_update_success
    assert recovered is not None
    assert recovered is not original
    assert coordinator.transient_failure_count == 0

    api.errors.append(HubNeedsRestart("retry login"))
    await coordinator.async_refresh()
    assert coordinator.data is recovered
    assert coordinator.last_update_success
    assert coordinator.transient_failure_count == 1


async def test_coordinator_does_not_reuse_data_before_first_success(
    hass, mock_config_entry
) -> None:
    """A failed first refresh leaves the coordinator without invented state."""
    api = CoordinatorApi()
    api.errors.append(HubNeedsRestart("retry login"))
    coordinator = NormanDataUpdateCoordinator(hass, api, mock_config_entry)

    await coordinator.async_refresh()
    assert coordinator.data is None
    assert not coordinator.last_update_success
    assert coordinator.transient_failure_count == 1


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (InvalidAuth("bad password"), ConfigEntryAuthFailed),
        (UnexpectedHub("expected", "other"), ConfigEntryError),
        (CannotConnect("malformed room payload"), UpdateFailed),
    ],
)
async def test_coordinator_does_not_mask_auth_identity_or_other_errors(
    hass, mock_config_entry, failure, expected
) -> None:
    """Only the dedicated transient login failure may retain old data."""
    api = CoordinatorApi()
    coordinator = NormanDataUpdateCoordinator(hass, api, mock_config_entry)
    await coordinator.async_refresh()
    original = coordinator.data
    assert original is not None
    api.errors.append(failure)

    await coordinator.async_refresh()
    assert coordinator.data is original
    assert not coordinator.last_update_success
    assert coordinator.transient_failure_count == 0
    assert isinstance(coordinator.last_exception, expected)


async def test_hard_error_invalidates_snapshot_retention_until_a_good_poll(
    hass, mock_config_entry
) -> None:
    """A later login failure cannot revive a snapshot across a hard failure."""
    api = CoordinatorApi()
    coordinator = NormanDataUpdateCoordinator(hass, api, mock_config_entry)
    await coordinator.async_refresh()
    original = coordinator.data
    assert original is not None
    assert coordinator.last_update_success

    api.errors.append(InvalidAuth("bad password"))
    await coordinator.async_refresh()
    assert coordinator.data is original
    assert not coordinator.last_update_success
    assert coordinator.transient_failure_count == 0
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)

    api.errors.append(HubNeedsRestart("retry login"))
    await coordinator.async_refresh()
    assert coordinator.data is original
    assert not coordinator.last_update_success
    assert coordinator.transient_failure_count == 1
    assert isinstance(coordinator.last_exception, UpdateFailed)

    await coordinator.async_refresh()
    refreshed = coordinator.data
    assert refreshed is not None
    assert refreshed is not original
    assert coordinator.last_update_success
    assert coordinator.transient_failure_count == 0

    api.errors.append(HubNeedsRestart("retry login"))
    await coordinator.async_refresh()
    assert coordinator.data is refreshed
    assert coordinator.last_update_success
    assert coordinator.transient_failure_count == 1


async def test_public_diagnostics_report_retained_snapshot_freshness(
    hass, hass_client, setup_integration, mock_norman_api
):
    """A public download exposes the failed poll count while keeping covers usable."""
    coordinator = setup_integration.runtime_data
    mock_norman_api.auth_errors = [HubNeedsRestart("Temporary login failure")]
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert await async_setup_component(hass, "diagnostics", {})
    client = await hass_client()
    response = await client.get(
        f"/api/diagnostics/config_entry/{setup_integration.entry_id}"
    )
    assert response.status == 200
    payload = await response.json()
    assert payload["data"]["transient_login_failed_polls"] == 1
    assert payload["data"]["snapshot"]["room_count"] == 1
