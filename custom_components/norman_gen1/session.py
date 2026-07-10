"""Isolated HTTP sessions for the Norman hub's cookie-based protocol."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession


@callback
def async_create_norman_session(
    hass: HomeAssistant, *, auto_cleanup: bool = True
) -> aiohttp.ClientSession:
    """Create a Home Assistant-managed session without a shared cookie jar."""
    return async_create_clientsession(
        hass,
        auto_cleanup=auto_cleanup,
        cookie_jar=aiohttp.DummyCookieJar(),
    )
