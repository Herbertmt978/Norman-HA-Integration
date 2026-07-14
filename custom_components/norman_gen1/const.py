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
PLATFORMS = (Platform.COVER, Platform.SENSOR)

DEFAULT_PROFILE_OPEN_POSITION = 37
DEFAULT_PROFILE_CLOSE_POSITION = 100

CONF_APP_VERSION = "app_version"
CONF_DEFAULT_OPEN_POSITION = "default_open_position"
CONF_DEFAULT_CLOSE_POSITION = "default_close_position"
CONF_POSITION_PROFILES = "position_profiles"
CONF_SIMULTANEOUS_ROOMS = "simultaneous_rooms"
CONF_OPEN_POSITION = "open_position"
CONF_CLOSE_POSITION = "close_position"
CONF_TARGET = "target"
CONF_INHERIT = "inherit"
CONF_LEGACY_PROFILE_MIGRATION = "legacy_profile_migration"

# v0.2 compatibility keys. These are read only by the v0.3 migration.
CONF_TILT_OPEN_TARGETS = "tilt_open_targets"
CONF_REVERSED_CLOSE_TARGETS = "reversed_close_targets"
CONF_KNOWN_TARGETS = "known_targets"
