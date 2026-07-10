"""Real Home Assistant fixtures for compatibility tests."""

import asyncio
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.norman_gen1.api import NormanRoom, NormanWindow, UnexpectedHub
from custom_components.norman_gen1.const import (
    CONF_APP_VERSION,
    DEFAULT_APP_VERSION,
    DEFAULT_PASSWORD,
    DOMAIN,
)


def _default_rooms() -> list[NormanRoom]:
    return [
        NormanRoom(
            id=1,
            name="Living room",
            group_names=["Left panel"],
            raw={"Style": 99},
        )
    ]


def _default_windows() -> list[NormanWindow]:
    return [
        NormanWindow(
            id=1,
            name="Left panel",
            room_id=1,
            level=1,
            group_id=None,
            position=0,
            model=1,
            battery=None,
            raw={},
        )
    ]


@dataclass
class MockNormanApiState:
    """Mutable protocol-client behavior shared by created mock clients."""

    hub_info: dict[str, Any] = field(
        default_factory=lambda: {
            "hubId": "hub-1",
            "hubName": "Home",
            "swVer": "1.0",
        }
    )
    rooms: list[NormanRoom] = field(default_factory=_default_rooms)
    windows: list[NormanWindow] = field(default_factory=_default_windows)
    auth_error: Exception | None = None
    auth_errors: list[Exception | None] = field(default_factory=list)
    rooms_error: Exception | None = None
    windows_error: Exception | None = None
    control_error: Exception | None = None
    clients: list[MagicMock] = field(default_factory=list)

    def create_client(
        self,
        session: Any,
        host: str,
        password: str,
        app_version: str = DEFAULT_APP_VERSION,
        *,
        expected_hub_id: str | None = None,
        transaction_lock: asyncio.Lock | None = None,
    ) -> MagicMock:
        """Create a protocol-shaped mock client backed by this state."""
        api = MagicMock()
        api.host = host
        api.session = session
        api.password = password
        api.app_version = app_version
        api.hub_info = {}
        api.transaction_lock = transaction_lock or asyncio.Lock()
        pinned_hub_id = [expected_hub_id]

        @asynccontextmanager
        async def authenticated_session():
            auth_error = (
                self.auth_errors.pop(0) if self.auth_errors else self.auth_error
            )
            if auth_error is not None:
                raise auth_error
            actual_hub_id = str(self.hub_info.get("hubId") or host)
            if pinned_hub_id[0] is not None and actual_hub_id != pinned_hub_id[0]:
                raise UnexpectedHub(pinned_hub_id[0], actual_hub_id)
            api.hub_info = dict(self.hub_info)
            api.hub_id = actual_hub_id
            yield dict(self.hub_info)

        async def get_rooms() -> list[NormanRoom]:
            if self.rooms_error is not None:
                raise self.rooms_error
            return list(self.rooms)

        async def get_windows() -> list[NormanWindow]:
            if self.windows_error is not None:
                raise self.windows_error
            return list(self.windows)

        async def control(*args: Any, **kwargs: Any) -> None:
            if self.control_error is not None:
                raise self.control_error

        def pin_hub_id(hub_id: str) -> None:
            pinned_hub_id[0] = hub_id
            api.hub_id = hub_id

        api.authenticated_session.side_effect = authenticated_session
        api.get_rooms = AsyncMock(side_effect=get_rooms)
        api.get_windows = AsyncMock(side_effect=get_windows)
        api.set_room_position = AsyncMock(side_effect=control)
        api.set_group_position = AsyncMock(side_effect=control)
        api.async_close = AsyncMock()
        api.pin_hub_id.side_effect = pin_hub_id
        api.hub_id = str(self.hub_info.get("hubId") or host)
        self.clients.append(api)
        return api

    @property
    def runtime_client(self) -> MagicMock:
        """Return the most recently created client."""
        return self.clients[-1]


@pytest.fixture
def mock_norman_api() -> Iterator[MockNormanApiState]:
    """Patch the integration's client boundary with controllable behavior."""
    state = MockNormanApiState()
    with (
        patch(
            "custom_components.norman_gen1.config_flow.NormanGen1Api",
            side_effect=state.create_client,
        ),
        patch(
            "custom_components.norman_gen1.NormanGen1Api",
            side_effect=state.create_client,
        ),
    ):
        yield state


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a configured Norman hub entry to Home Assistant."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Norman hub",
        unique_id="hub-1",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PASSWORD: DEFAULT_PASSWORD,
            CONF_APP_VERSION: DEFAULT_APP_VERSION,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_norman_api: MockNormanApiState,
) -> MockConfigEntry:
    """Set up the integration through Home Assistant's config-entry manager."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
