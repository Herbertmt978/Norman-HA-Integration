"""Data update coordinator for Norman devices."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.norman_gen1.const import DOMAIN

from .api import (
    NormanApiClient,
    NormanApiError,
    NormanConnectionError,
    NormanPeriodicReconnectError,
)
from .const import COVER_TYPE_SMARTDRAPE, RECONNECT_INTERVAL
from .models import NormanDevices, NormanPeripheralData

_LOGGER = logging.getLogger(__name__)


class NormanCoordinator(DataUpdateCoordinator[NormanDevices]):
    """Norman data update coordinator."""

    def __init__(
        self, hass: HomeAssistant, api: NormanApiClient, entry: ConfigEntry
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=None,
        )
        self.api = api
        self._device_info: dict[str, Any] = {}

    async def listen_notifications(self) -> None:
        """Continuously listen for hub notifications and refresh data on change."""
        while True:
            try:
                async for _notification in self.api.async_listen_notifications():
                    _LOGGER.debug("Received ShadeAuto state notification")
                    await self.async_refresh()
            except asyncio.CancelledError:
                _LOGGER.debug("Notification listener task cancelled")
                return
            except (NormanConnectionError, NormanApiError) as err:
                _LOGGER.error("Notification listener disconnected: %s", err)
            except NormanPeriodicReconnectError:
                _LOGGER.debug("Periodic reconnection time arrived")
                continue

            _LOGGER.info(
                "Reconnecting notification listener in %s seconds",
                RECONNECT_INTERVAL,
            )
            try:
                await asyncio.sleep(RECONNECT_INTERVAL)
                # Refresh device states when reconnecting to ensure we have the latest state
                # This handles cases where blind states changed while the hub was offline
                _LOGGER.debug(
                    "Refreshing device states after notification reconnection"
                )
                await self.async_refresh()
            except asyncio.CancelledError:
                _LOGGER.debug("Notification listener sleep cancelled")
                return

    async def _async_update_data(self) -> NormanDevices:
        """Fetch data from API.

        Returns:
            Dictionary with peripheral data keyed by device ID

        Raises:
            UpdateFailed: If the update operation fails

        """
        try:
            # Only fetch full device info if we haven't yet or if it's empty
            if not self._device_info:
                self._device_info = await self.api.async_get_devices()

            # Get status updates which are more lightweight
            status_data = await self.api.async_get_status()

            # Process and merge data from device info and status
            return self._process_data(self._device_info, status_data)
        except NormanConnectionError as err:
            raise UpdateFailed(f"Error communicating with Norman hub: {err}") from err
        except (NormanApiError, TypeError, ValueError, AttributeError) as err:
            raise UpdateFailed(f"Invalid response from Norman hub: {err}") from err

    def _process_data(
        self, device_info: dict[str, Any], status_data: dict[str, Any]
    ) -> NormanDevices:
        """Process and combine data from GetAllPeripheral and status endpoints.

        Args:
            device_info: Data from GetAllPeripheral endpoint
            status_data: Data from status endpoint

        Returns:
            Combined data keyed by device ID

        """
        if not isinstance(device_info.get("results", {}), dict) or not isinstance(
            status_data.get("Peripherals", []), list
        ):
            raise NormanApiError("Malformed ShadeAuto discovery or status")
        devices: NormanDevices = {}

        # Process device information (names, room, group)
        if "results" in device_info and "RoomList" in device_info["results"]:
            room_list = device_info["results"]["RoomList"]
            for room in room_list:
                room_id = room.get("RoomID")
                room_name = room.get("RoomName", "")

                # Process groups
                for group in room.get("GroupList", []):
                    group_id = group.get("GroupID")
                    group_name = group.get("GroupName", "")

                    # Process peripherals
                    for peripheral in group.get("PeripheralList", []):
                        peripheral_uid_raw = peripheral.get("PeripheralUID")
                        if peripheral_uid_raw is None:
                            continue
                        try:
                            peripheral_uid = int(peripheral_uid_raw)
                        except (TypeError, ValueError):
                            continue

                        # Create device entry using dataclass
                        device_type = COVER_TYPE_SMARTDRAPE  # Default to SmartDrape
                        # TODO: Support other blind types
                        devices[peripheral_uid] = NormanPeripheralData(
                            id=peripheral_uid,
                            name=peripheral.get(
                                "PeripheralName", f"Norman {peripheral_uid}"
                            ),
                            type=device_type,
                            room_id=room_id,
                            room_name=room_name,
                            group_id=group_id,
                            group_name=group_name,
                            module_type=peripheral.get("ModuleType"),
                            module_detail=peripheral.get("ModuleDetail"),
                        )

        # Add status information
        if "Peripherals" in status_data:
            for peripheral in status_data["Peripherals"]:
                peripheral_uid_raw = peripheral.get("PeripheralUID")
                if peripheral_uid_raw is None:
                    continue
                try:
                    peripheral_uid = int(peripheral_uid_raw)
                except (TypeError, ValueError):
                    continue

                if peripheral_uid not in devices:
                    # Create minimal device if not found in device_info
                    devices[peripheral_uid] = NormanPeripheralData(
                        id=peripheral_uid,
                        name=f"Norman {peripheral_uid}",
                        type=COVER_TYPE_SMARTDRAPE,
                    )

                # Update with status information on dataclass
                device = devices[peripheral_uid]
                device.bottom_rail_position = peripheral.get("BottomRailPosition")
                device.middle_rail_position = peripheral.get("MiddleRailPosition")
                device.target_bottom_rail_position = peripheral.get(
                    "TargetBottomRailPosition"
                )
                device.target_middle_rail_position = peripheral.get(
                    "TargetMiddleRailPosition"
                )
                device.battery_level = peripheral.get("BatteryVoltage")
                device.firmware_version = peripheral.get("FirmwareVersion")
                device.last_update = peripheral.get("Timestamp")

        return devices
