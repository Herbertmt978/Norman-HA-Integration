"""Constants for the Norman integration."""

# Derived from keito/home-assistant-norman; modified. Apache-2.0 (see NOTICE).

from homeassistant.const import Platform

# Platforms
PLATFORMS = [Platform.COVER]

# Config entry keys
CONF_HOST = "host"

RECONNECT_INTERVAL = 15  # seconds to wait after disconnect before reconnecting
NOTIF_MAX_DURATION = 300  # seconds, max time before forcing a reconnect

READ_CHUNK_SIZE = 1024

# Cover types
COVER_TYPE_SMARTDRAPE = "smartdrape"  # Has position and tilt capabilities

ATTR_TARGET_POSITION = "target_position"
ATTR_TARGET_TILT = "target_tilt"

SERVICE_NUDGE_POSITION = "nudge_position"
SERVICE_NUDGE_TILT = "nudge_tilt"
