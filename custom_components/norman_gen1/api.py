"""Async client for the local Norman Gen 1 hub protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.cookies import SimpleCookie
import logging
import math
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)
DEFAULT_APP_VERSION = "2.11.21"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
DEFAULT_OPEN_POSITION = 100
DEFAULT_TILT_OPEN_POSITION = 37
DEFAULT_CLOSE_POSITION = 0
REVERSED_CLOSE_POSITION = 100
TILT_ROOM_STYLES = {2, 3, 13}
REVERSED_CLOSE_ROOM_STYLES = {13}
REMOTE_SUCCESS_KEYS = ("remote", "result", "status", "success")
HUB_TEXT_FIELDS = ("hubName", "swVer", "firmwareVersion", "version", "status")


def room_target_id(room_id: int) -> str:
    """Return the options target id for a whole Norman room."""
    return f"room:{int(room_id)}"


def group_target_id(room_id: int, level: int) -> str:
    """Return the options target id for one room group/level."""
    return f"group:{int(room_id)}:{int(level)}"


def target_override_enabled(
    targets: Iterable[str], room_id: int, level: int | None = None
) -> bool:
    """Return whether an options target list applies to this room or group.

    A selected room target applies to both the room entity and all of its group
    entities. A selected group target only applies to that single group entity.
    """
    target_set = {str(target) for target in targets}
    if room_target_id(room_id) in target_set:
        return True
    return level is not None and group_target_id(room_id, level) in target_set


def position_is_closed(
    position: int, close_position: int, *, closes_at_both_ends: bool = False
) -> bool:
    """Return whether a reported position should be treated as closed."""
    if closes_at_both_ends:
        return position <= 0 or position >= 100
    if close_position >= 100:
        return position >= close_position
    return position <= close_position


class NormanGen1Error(Exception):
    """Base error for Norman Gen 1 API failures."""


class CannotConnect(NormanGen1Error):
    """Raised when the hub cannot be reached."""


class _HttpStatusError(CannotConnect):
    """Record a non-success HTTP response without exposing response content."""

    def __init__(self, endpoint: str, status: int) -> None:
        super().__init__(f"{endpoint} returned HTTP {status}")
        self.endpoint = endpoint
        self.status = status


class HubNeedsRestart(CannotConnect):
    """Raised when the hub's local login service remains unavailable."""


class InvalidAuth(NormanGen1Error):
    """Raised when the hub rejects the password."""


class InvalidSession(NormanGen1Error):
    """Raised when the hub rejects an authenticated session."""


class UnexpectedHub(NormanGen1Error):
    """Raised when an authenticated endpoint no longer identifies as the configured hub."""

    def __init__(self, expected_hub_id: str, actual_hub_id: str) -> None:
        """Initialize an identity mismatch with safe, non-credential identifiers."""
        super().__init__(
            f"Expected hub {expected_hub_id!r}, received {actual_hub_id!r}"
        )
        self.expected_hub_id = expected_hub_id
        self.actual_hub_id = actual_hub_id


class NoDevicesFound(NormanGen1Error):
    """Raised when the hub responds but returns no controllable devices."""


class CannotControl(NormanGen1Error):
    """Raised when the hub rejects or fails to confirm a control command."""


@dataclass(slots=True)
class NormanRoom:
    """Normalized room metadata returned by a Norman hub."""

    id: int
    name: str
    group_names: list[str]
    raw: dict[str, Any]


@dataclass(slots=True)
class NormanWindow:
    """Normalized shutter metadata and state returned by a Norman hub."""

    id: int
    name: str
    room_id: int
    level: int
    group_id: int | None
    position: int | None
    model: int
    battery: int | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PositionProfile:
    """Map Home Assistant cover positions to one Norman movement branch."""

    open_position: int
    close_position: int
    closes_at_both_ends: bool


class NormanGen1Api:
    """Minimal local API client for Norman Gen 1 hubs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        password: str,
        app_version: str = DEFAULT_APP_VERSION,
        *,
        expected_hub_id: str | None = None,
        transaction_lock: asyncio.Lock | None = None,
    ) -> None:
        """Initialize the client without opening a connection."""
        self._session = session
        self.host = (
            host.strip().removeprefix("http://").removeprefix("https://").strip("/")
        )
        self.password = password
        self.app_version = app_version
        self._session_cookie: str | None = None
        self._session_cookie_generation = 0
        self._transaction_lock = transaction_lock or asyncio.Lock()
        self._expected_hub_id = expected_hub_id
        self.hub_info: dict[str, Any] = {}

    @property
    def base_url(self) -> str:
        """Return the hub CGI endpoint root."""
        return f"http://{self.host}/cgi-bin/cgi"

    @property
    def hub_id(self) -> str:
        """Return the authenticated hub ID, falling back to its stable host."""
        return str(self.hub_info.get("hubId") or self.host)

    @property
    def transaction_lock(self) -> asyncio.Lock:
        """Return the lock that serializes the hub's single-session protocol."""
        return self._transaction_lock

    def pin_hub_id(self, hub_id: str) -> None:
        """Require all future logins to identify as the verified hub."""
        hub_id = str(hub_id)
        if self._expected_hub_id is not None and hub_id != self._expected_hub_id:
            raise UnexpectedHub(self._expected_hub_id, hub_id)
        self._expected_hub_id = hub_id

    @asynccontextmanager
    async def authenticated_session(self) -> AsyncIterator[dict[str, Any]]:
        """Serialize one complete login, operation, and logout transaction."""
        async with self._transaction_lock:
            try:
                yield await self.login()
            finally:
                await self.logout()

    async def login(self) -> dict[str, Any]:
        """Authenticate and return the hub identity payload."""
        self._session_cookie = None
        payload = {"password": self.password, "app_version": self.app_version}
        try:
            data = await self._post("GatewayLogin", payload, require_session=False)
        except CannotConnect as err:
            if not _is_gateway_login_server_error(err):
                raise
            _LOGGER.warning(
                "GatewayLogin returned HTTP 500 from Norman hub %s; forcing logout endpoints before retrying once",
                self.host,
            )
            transition_cookie = await self.logout(force=True)
            try:
                data = await self._post(
                    "GatewayLogin",
                    payload,
                    require_session=False,
                    request_cookie=transition_cookie,
                )
            except CannotConnect as retry_err:
                if _is_gateway_login_server_error(retry_err):
                    raise HubNeedsRestart(
                        "The Norman hub did not recover its login service; "
                        "restart the hub and try again"
                    ) from retry_err
                raise
        if "errorCode" in data:
            error_code = _as_int(data.get("errorCode"))
            if error_code is None or error_code != 0:
                raise CannotConnect(
                    "Hub returned a malformed or nonzero login errorCode"
                )
        actual_hub_id = self.host
        normalized_info: dict[str, Any] = {}
        if "hubId" in data and data.get("hubId") not in (None, ""):
            parsed_hub_id = _as_identifier(data.get("hubId"))
            if parsed_hub_id is None:
                raise CannotConnect("Hub returned a malformed hubId")
            actual_hub_id = parsed_hub_id
            normalized_info["hubId"] = parsed_hub_id
        if self._expected_hub_id is not None and actual_hub_id != self._expected_hub_id:
            raise UnexpectedHub(self._expected_hub_id, actual_hub_id)
        for key in HUB_TEXT_FIELDS:
            if (value := _as_display_text(data.get(key))) is not None:
                normalized_info[key] = value
        if "errorCode" in data:
            normalized_info["errorCode"] = _as_int(data.get("errorCode"))
        self.hub_info = normalized_info
        return normalized_info

    async def logout(self, *, force: bool = False) -> str | None:
        """Log out best-effort and return a new hub transition cookie, if any."""
        if not force and not self._session_cookie:
            return None
        initial_cookie_generation = self._session_cookie_generation
        try:
            for endpoint in ("AdminLogout", "GatewayLogout"):
                try:
                    await self._post(endpoint, {}, auto_login=False)
                except NormanGen1Error:
                    _LOGGER.debug("Ignoring %s failure", endpoint, exc_info=True)
        finally:
            transition_cookie = (
                self._session_cookie
                if self._session_cookie_generation != initial_cookie_generation
                else None
            )
            self._session_cookie = None
        return transition_cookie

    async def async_close(self) -> None:
        """Wait for any active transaction, then close the hub session."""
        async with self._transaction_lock:
            await self.logout()

    async def get_rooms(self) -> list[NormanRoom]:
        """Return parsed rooms from the current authenticated session."""
        data = await self._post("getRoomInfo", {})
        rooms = []
        for room in _mapping_records(data, "rooms"):
            room_id = _first_int(room, "Id", "id")
            if room_id is None or room_id < 0:
                _LOGGER.warning("Skipping Norman room with a missing or invalid Id")
                continue
            raw_group_names = room.get("groupname")
            if raw_group_names is None:
                group_names: list[str] = []
            elif isinstance(raw_group_names, list):
                group_names = [
                    name
                    for value in raw_group_names
                    if (name := _as_display_text(value)) is not None
                ]
            else:
                group_names = (
                    [group_name]
                    if (group_name := _as_display_text(raw_group_names)) is not None
                    else []
                )
            rooms.append(
                NormanRoom(
                    id=room_id,
                    name=_as_display_text(room.get("Name")) or str(room_id),
                    group_names=group_names,
                    raw=room,
                )
            )
        return rooms

    async def get_windows(self) -> list[NormanWindow]:
        """Return parsed shutters from the current authenticated session."""
        data = await self._post("getWindowInfo", {})
        return self._parse_windows(data)

    async def full_open_room(self, room_id: int) -> None:
        """Send the hub's room-wide full-open command."""
        await self._remote_control({"type": "fullopen", "action": 2, "id": room_id})

    async def full_close_room(self, room_id: int) -> None:
        """Send the hub's room-wide full-close command."""
        await self._remote_control({"type": "fullclose", "action": 2, "id": room_id})

    async def set_group_position(
        self, room_id: int, level: int, position: int, model: int = 1
    ) -> None:
        """Move one discovered room level to a raw hub position."""
        position = max(0, min(100, int(position)))
        await self._remote_control(
            {
                "type": "level",
                "Lid": int(level),
                "id": int(room_id),
                "action": position,
                "model": model,
            }
        )

    async def set_room_positions(
        self,
        room_id: int,
        positions_by_level: Mapping[int, int],
        models_by_level: Mapping[int, int] | None = None,
    ) -> None:
        """Move discovered room levels to their exact raw hub positions."""
        positions = {
            int(level): max(0, min(100, int(position)))
            for level, position in positions_by_level.items()
        }
        if not positions:
            raise CannotControl(
                f"Cannot set room {room_id} because no group levels were discovered"
            )

        _LOGGER.debug(
            "Controlling Norman room %s via %s group level command(s)",
            room_id,
            len(positions),
        )
        levels = sorted(positions)
        for index, level in enumerate(levels):
            model = models_by_level.get(level, 1) if models_by_level else 1
            await self.set_group_position(
                room_id,
                level,
                positions[level],
                model,
            )
            if index < len(levels) - 1:
                await asyncio.sleep(0.15)

    async def _remote_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._post("RemoteControl", payload, allow_error_response=True)
        error_code = _as_int(data.get("errorCode"))
        if "errorCode" in data and error_code is None:
            message = "RemoteControl returned a malformed errorCode"
            _LOGGER.warning("%s", message)
            raise CannotControl(message)
        if error_code not in (None, 0):
            message = f"RemoteControl returned errorCode {error_code}"
            _LOGGER.warning("%s", message)
            raise CannotControl(message)
        confirmed = error_code == 0 or any(
            _is_success_value(data.get(key)) for key in REMOTE_SUCCESS_KEYS
        )
        if not confirmed:
            message = "RemoteControl did not contain a recognized success indicator"
            _LOGGER.warning("%s", message)
            raise CannotControl(message)
        return data

    async def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        require_session: bool = True,
        auto_login: bool = True,
        allow_error_response: bool = False,
        request_cookie: str | None = None,
    ) -> dict[str, Any]:
        if require_session and auto_login and not self._session_cookie:
            await self.login()

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": f"SmartShutterControl/103 HomeAssistant NormanGen1/{self.app_version}",
        }
        if request_cookie is not None:
            headers["Cookie"] = request_cookie
        elif require_session and self._session_cookie:
            headers["Cookie"] = self._session_cookie
        url = f"{self.base_url}/{endpoint}"
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if session_cookie := _session_cookie_from_headers(response.headers):
                    self._session_cookie = session_cookie
                    self._session_cookie_generation += 1
                # Cherokee can still be running the failed CGI request after it
                # sends the response headers. Consume the complete response before
                # starting login recovery so requests never overlap at the hub.
                await response.read()
                if response.status in (401, 403):
                    if require_session:
                        raise InvalidSession(
                            f"{endpoint} rejected the authenticated session"
                        )
                    raise InvalidAuth("Hub rejected the password")
                if response.status != 200:
                    raise _HttpStatusError(endpoint, response.status)
                try:
                    data = await response.json(content_type=None)
                except Exception as err:
                    raise CannotConnect(
                        f"{endpoint} returned a non-JSON response"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err)) from err
        if not isinstance(data, dict):
            raise CannotConnect(
                f"{endpoint} returned an unexpected {type(data).__name__} payload"
            )
        if "errorCode" in data:
            error_code = _as_int(data.get("errorCode"))
            if error_code == -13:
                if require_session:
                    raise InvalidSession("Hub rejected the request/session")
                raise InvalidAuth("Hub rejected the password")
            if not allow_error_response and error_code != 0:
                raise CannotConnect(
                    f"{endpoint} returned a malformed or nonzero errorCode"
                )
        return data

    def _parse_windows(
        self,
        data: dict[str, Any],
        *,
        default_room_id: int | None = None,
        default_level: int | None = None,
    ) -> list[NormanWindow]:
        windows = []
        for window in _mapping_records(data, "windows"):
            window_id = _first_int(window, "Id", "id")
            if window_id is None:
                _LOGGER.warning("Skipping Norman window without a numeric Id")
                continue
            room_id = _first_int(window, "roomId", "RId", default=default_room_id)
            level = _first_int(window, "Level", "Lid", default=default_level)
            position = _as_int(window.get("position"))
            if position is not None and not 0 <= position <= 100:
                _LOGGER.warning("Ignoring an out-of-range Norman window position")
                position = None
            parsed_room_id = _as_int(room_id)
            parsed_level = _as_int(level)
            parsed_model = _as_int(window.get("model"))
            windows.append(
                NormanWindow(
                    id=window_id,
                    name=_as_display_text(window.get("Name")) or str(window_id),
                    room_id=parsed_room_id if parsed_room_id is not None else -1,
                    level=parsed_level if parsed_level is not None else -1,
                    group_id=_as_int(window.get("groupId")),
                    position=position,
                    model=parsed_model if parsed_model is not None else 1,
                    battery=_as_battery_percentage(window.get("battery")),
                    raw=window,
                )
            )
        return windows


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            return None
    return None


def _as_battery_percentage(value: Any) -> int | None:
    """Normalize a physical motor battery without accepting lossy values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        battery = value
    elif isinstance(value, str):
        try:
            battery = int(value.strip(), 10)
        except ValueError:
            return None
    else:
        return None
    return battery if 0 <= battery <= 100 else None


def _as_identifier(value: Any) -> str | None:
    """Return a non-empty stable scalar suitable for registry identifiers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _as_display_text(value: Any) -> str | None:
    """Normalize an optional scalar before exposing it to Home Assistant."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return " ".join(value.split()) or None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    return None


def _first_int(
    data: dict[str, Any], *keys: str, default: int | None = None
) -> int | None:
    """Return the first parseable integer from equivalent hub fields."""
    for key in keys:
        if (value := _as_int(data.get(key))) is not None:
            return value
    return default


def _mapping_records(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return mapping records from a hub collection."""
    if key not in data:
        raise CannotConnect(f"Hub response did not contain {key}")
    value = data[key]
    if not isinstance(value, list):
        raise CannotConnect(
            f"Hub returned malformed {key}: expected a list, got {type(value).__name__}"
        )
    records: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            records.append(item)
        else:
            _LOGGER.warning(
                "Skipping malformed Norman %s record of type %s",
                key,
                type(item).__name__,
            )
    return records


def _session_cookie_from_headers(headers: Mapping[str, str]) -> str | None:
    """Extract the Norman Session cookie without accepting unrelated cookies."""
    cookie_headers: list[str] = []
    if callable(get_all := getattr(headers, "getall", None)):
        cookie_headers.extend(get_all("Set-Cookie", []))
    else:
        cookie_headers.extend(
            value for key in ("Set-Cookie", "set-cookie") if (value := headers.get(key))
        )

    for header in cookie_headers:
        parsed = SimpleCookie()
        parsed.load(header)
        if session := parsed.get("Session"):
            return f"Session={session.value}"

    if session_header := headers.get("session") or headers.get("Session"):
        value = session_header.split(";", 1)[0]
        return value if value.lower().startswith("session=") else f"Session={value}"
    return None


def _is_success_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"ok", "success", "true"}
    return False


def _is_gateway_login_server_error(err: CannotConnect) -> bool:
    return (
        isinstance(err, _HttpStatusError)
        and err.endpoint == "GatewayLogin"
        and err.status == 500
    )


def room_open_position(
    room_raw: dict[str, Any],
    use_tilt_open: bool | None = None,
) -> int:
    """Return the best open target for a room.

    Some Norman plantation shutter rooms use the middle of the travel as the
    visually open louver position, with both end stops being closed angles.
    """
    if room_uses_tilt_open(room_raw, use_tilt_open):
        return DEFAULT_TILT_OPEN_POSITION
    return DEFAULT_OPEN_POSITION


def room_uses_tilt_open(
    room_raw: dict[str, Any], use_tilt_open: bool | None = None
) -> bool:
    """Return whether a room uses a mid-travel visual-open position."""
    if use_tilt_open is not None:
        return use_tilt_open
    return _as_int(room_raw.get("Style")) in TILT_ROOM_STYLES


def room_close_position(
    room_raw: dict[str, Any], use_reversed_close: bool | None = None
) -> int:
    """Return the close target for a room."""
    if use_reversed_close is True:
        return REVERSED_CLOSE_POSITION
    if use_reversed_close is False:
        return DEFAULT_CLOSE_POSITION
    if _as_int(room_raw.get("Style")) in REVERSED_CLOSE_ROOM_STYLES:
        return REVERSED_CLOSE_POSITION
    return DEFAULT_CLOSE_POSITION


def resolve_position_profile(
    room_raw: dict[str, Any],
    *,
    use_tilt_open: bool | None = None,
    use_reversed_close: bool | None = None,
) -> PositionProfile:
    """Resolve one safe Home Assistant-to-hub movement profile."""
    close_position = room_close_position(room_raw, use_reversed_close)
    uses_tilt = room_uses_tilt_open(room_raw, use_tilt_open)
    if close_position == REVERSED_CLOSE_POSITION:
        uses_tilt = True
    open_position = DEFAULT_TILT_OPEN_POSITION if uses_tilt else DEFAULT_OPEN_POSITION
    return PositionProfile(
        open_position=open_position,
        close_position=close_position,
        closes_at_both_ends=uses_tilt,
    )


def ha_position_to_hub(position: int, profile: PositionProfile) -> int:
    """Map a Home Assistant position onto the configured hub movement branch."""
    position = max(0, min(100, int(position)))
    return round(
        profile.close_position
        + (profile.open_position - profile.close_position) * position / 100
    )


def hub_position_to_ha(position: int, profile: PositionProfile) -> int | None:
    """Map a reported hub position to Home Assistant's closed-to-open scale."""
    position = max(0, min(100, int(position)))
    if profile.closes_at_both_ends:
        if not 0 < profile.open_position < 100:
            return 0
        if position <= profile.open_position:
            mapped = position * 100 / profile.open_position
        else:
            span = 100 - profile.open_position
            mapped = (100 - position) * 100 / span
        return max(0, min(100, round(mapped)))
    span = profile.open_position - profile.close_position
    if span == 0:
        return None
    mapped = (position - profile.close_position) * 100 / span
    return max(0, min(100, round(mapped)))
