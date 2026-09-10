"""API client for Norman window coverings."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from __future__ import annotations

import asyncio
import codecs
from collections.abc import AsyncIterator
import json
import logging
import time
from typing import Any, cast

import aiohttp
from aiohttp import ClientResponse, ClientTimeout
from aiohttp.client_exceptions import ClientError
from homeassistant.exceptions import HomeAssistantError

from .const import NOTIF_MAX_DURATION, READ_CHUNK_SIZE

_LOGGER = logging.getLogger(__name__)

# API endpoints
ENDPOINT_REGISTRATION = "/NM/v1/registration"
ENDPOINT_GET_ALL_PERIPHERAL = "/NM/v1/GetAllPeripheral"
ENDPOINT_STATUS = "/NM/v1/status"
ENDPOINT_CONTROL = "/NM/v1/control"


class NormanApiError(HomeAssistantError):
    """Exception to indicate an API error occurred."""


class NormanConnectionError(HomeAssistantError):
    """Exception to indicate a connection error occurred."""


class NormanPeriodicReconnectError(HomeAssistantError):
    """Exception to indicate a period reconnection (not really an error)."""


class NormanApiClient:
    """API client for Norman Hub."""

    def __init__(self, host: str, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize the API client.

        Args:
            host: IP address or hostname of the Norman hub

        """
        self.host = host
        self.base_url = f"http://{host}:10123"
        self._owns_session = session is None
        self._session = session if session is not None else aiohttp.ClientSession()
        self._thing_name: str | None = None
        self._notif_response: ClientResponse | None = None

    @property
    def thing_name(self) -> str | None:
        """Return the stable hub identity supplied during registration."""
        return self._thing_name

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Post a protocol request, validate its shape and release the response."""
        async with self._session.post(
            f"{self.base_url}{endpoint}", json=payload, timeout=ClientTimeout(total=10)
        ) as response:
            response.raise_for_status()
            data = await response.json()
        if not isinstance(data, dict):
            raise NormanApiError("Expected a JSON object from ShadeAuto hub")
        return cast(dict[str, Any], data)

    async def async_validate_connection(self) -> bool:
        """Test if we can connect to the Norman hub.

        Returns:
            True if connection is successful

        Raises:
            NormanConnectionError: If connection fails

        """
        await self._async_registration()
        return True

    async def _async_registration(self) -> dict[str, Any]:
        """Send registration request to get ThingName.

        Returns:
            Registration response data

        Raises:
            NormanApiError: If API returns an error

        """
        timestamp = int(time.time())
        payload = {"Timestamp": timestamp}

        try:
            data = await self._post(ENDPOINT_REGISTRATION, payload)

            if data.get("Error", 0) != 0:
                raise NormanApiError(
                    f"Registration failed with error code: {data.get('Error')}"
                )

            thing_name = data.get("ThingName")
            if not isinstance(thing_name, str) or not thing_name.strip():
                raise NormanApiError("Registration response has no hub identity")
            self._thing_name = thing_name
        except (ClientError, TimeoutError) as err:
            raise NormanConnectionError(
                f"Failed to connect to Norman hub: {err}"
            ) from err
        except (json.JSONDecodeError, KeyError) as err:
            raise NormanApiError(f"Invalid response from Norman hub: {err}") from err
        else:
            return data

    async def async_get_devices(self) -> dict[str, Any]:
        """Get list of all devices from the Norman hub.

        Returns:
            Dictionary with device information

        Raises:
            NormanApiError: If API returns an error
            NormanConnectionError: If connection fails

        """
        # First ensure we have a ThingName
        if not self._thing_name:
            await self._async_registration()

        timestamp = int(time.time())
        task_id = int(time.time() * 1000) % 10000  # Random task ID
        payload: dict[str, Any] = {
            "ThingName": self._thing_name,
            "TaskID": task_id,
            "Timestamp": timestamp,
        }

        try:
            data = await self._post(ENDPOINT_GET_ALL_PERIPHERAL, payload)

            status = data.get("status", {})
            if not isinstance(status, dict):
                raise NormanApiError("Invalid peripheral status response")
            if status.get("code", 0) != 0:
                error_msg = status.get("error", "Unknown error")
                raise NormanApiError(f"GetAllPeripheral failed: {error_msg}")
        except (ClientError, TimeoutError) as err:
            raise NormanConnectionError(
                f"Failed to connect to Norman hub: {err}"
            ) from err
        except (json.JSONDecodeError, KeyError) as err:
            raise NormanApiError(f"Invalid response from Norman hub: {err}") from err
        else:
            return data

    async def async_get_status(self) -> dict[str, Any]:
        """Get current status of all devices.

        Returns:
            Dictionary with device status information

        Raises:
            NormanApiError: If API returns an error
            NormanConnectionError: If connection fails

        """
        timestamp = int(time.time())
        payload = {"Timestamp": timestamp}

        try:
            data = await self._post(ENDPOINT_STATUS, payload)

            if data.get("Error", 0) != 0:
                raise NormanApiError(
                    f"Status request failed with error code: {data.get('Error')}"
                )
        except (ClientError, TimeoutError) as err:
            raise NormanConnectionError(
                f"Failed to connect to Norman hub: {err}"
            ) from err
        except (json.JSONDecodeError, KeyError) as err:
            raise NormanApiError(f"Invalid response from Norman hub: {err}") from err
        else:
            return data

    async def async_set_position(
        self, device_id: int, bottom_rail_position: int, middle_rail_position: int
    ) -> None:
        """Set cover position.

        Args:
            device_id: ID of the Norman device
            bottom_rail_position: Bottom rail position (0=closed, 100=open)
            middle_rail_position: Middle rail position (0=closed, 100=open)

        Raises:
            NormanApiError: If API returns an error
            NormanConnectionError: If connection fails

        """
        timestamp = int(time.time())
        task_id = int(time.time() * 1000) % 10000  # Random task ID
        payload = {
            "PeripheralUID": device_id,
            "Timestamp": timestamp,
            "TaskID": task_id,
            "BottomRailPosition": bottom_rail_position,
            "MiddleRailPosition": middle_rail_position,
        }

        try:
            data = await self._post(ENDPOINT_CONTROL, payload)

            if data.get("Error", 0) != 0:
                raise NormanApiError(
                    f"Control request failed with error code: {data.get('Error')}"
                )

        except (ClientError, TimeoutError) as err:
            raise NormanConnectionError(
                f"Failed to connect to Norman hub: {err}"
            ) from err
        except (json.JSONDecodeError, KeyError) as err:
            raise NormanApiError(f"Invalid response from Norman hub: {err}") from err

    async def async_close(self) -> None:
        """Close the API client session."""
        if self._notif_response:
            self._notif_response.close()
            self._notif_response = None
        if self._owns_session:
            await self._session.close()

    async def async_listen_notifications(self) -> AsyncIterator[dict[str, Any]]:
        """Read concatenated JSON, including split UTF-8 and braces in strings."""
        decoder = json.JSONDecoder()
        utf8 = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        lifetime = asyncio.timeout(NOTIF_MAX_DURATION)
        try:
            async with (
                lifetime,
                self._session.post(
                    f"{self.base_url}/NM/v1/notification",
                    timeout=ClientTimeout(total=None, connect=10),
                ) as response,
            ):
                response.raise_for_status()
                self._notif_response = response
                while chunk := await response.content.read(READ_CHUNK_SIZE):
                    buffer += utf8.decode(chunk)
                    if len(buffer) > 1048576:
                        raise NormanApiError("Notification exceeds maximum size")
                    buffer = buffer.lstrip()
                    while buffer:
                        try:
                            obj, end = decoder.raw_decode(buffer)
                        except json.JSONDecodeError:
                            break
                        buffer = buffer[end:].lstrip()
                        if isinstance(obj, dict) and "PeripheralList" in obj:
                            yield obj
                if buffer.strip():
                    raise NormanApiError("Incomplete notification response")
        except TimeoutError as err:
            if lifetime.expired():
                raise NormanPeriodicReconnectError from err
            raise NormanConnectionError("Notification connection timed out") from err
        except (ClientError, UnicodeError) as err:
            raise NormanConnectionError("Notification connection failed") from err
        finally:
            if self._notif_response is not None:
                self._notif_response.close()
                self._notif_response = None
