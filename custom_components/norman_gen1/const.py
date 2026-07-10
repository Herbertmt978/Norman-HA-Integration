"""Constants for the Norman Gen 1 integration."""

from datetime import timedelta

from homeassistant.const import Platform

from .api import DEFAULT_APP_VERSION as API_DEFAULT_APP_VERSION

DOMAIN = "norman_gen1"
MANUFACTURER = "Norman"
DEFAULT_PASSWORD = "123456789"
DEFAULT_APP_VERSION = API_DEFAULT_APP_VERSION
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
COMMAND_SETTLE_SECONDS = 10
PLATFORMS = (Platform.COVER,)

CONF_APP_VERSION = "app_version"
CONF_TILT_OPEN_TARGETS = "tilt_open_targets"
CONF_REVERSED_CLOSE_TARGETS = "reversed_close_targets"

CONF_KNOWN_TARGETS = "known_targets"
