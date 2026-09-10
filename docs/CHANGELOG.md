# Changelog

## 0.4.1 — 10 September 2026

- Preserve accepted ShadeAuto rail targets across delayed status reads and serialize overlapping commands.
- Handle notification connection timeouts as failures with the normal retry delay.
- Reject invalid rail positions before they reach Home Assistant state or control requests.

## 0.4.0 — 10 September 2026

- Supports Gen 1 and Gen 2 (ShadeAuto) hubs in one integration, selected during setup.
- Integrates the attributed ShadeAuto local API with current HA compatibility, response cleanup and safer two-rail controls.
- Preserves existing Gen 1 IDs, options, controls and battery sensors.
- Updates the repository branding, installation instructions and generation-specific guides.

## Historical releases

Versions through 0.3.4 predate the combined-generation implementation. Their
release notes below describe the Gen 1 behavior available at that time.

### 0.3.4

- Uses the registered hub's device ID to preserve room and battery-device links on newer Home Assistant versions.
- Retains compatibility with Home Assistant 2024.11 and older supported device-registry APIs.
- Leaves entity IDs, room command settings, position profiles, and automations unchanged.

### 0.3.3

- Makes paced, exact level commands the reliable default for whole-room Open, Close, and position control.
- Adds an explicit per-room option for simultaneous native Open/Close broadcasts, while retaining safe broadcast fallback for rooms without usable levels.
- Exposes the effective command route and privacy-safe numeric room/window correlations in entity attributes and diagnostics.

### 0.3.2

- Treats a valid Gen 1 `RemoteControl` HTTP response with no explicit error as accepted, matching the hub's fire-and-forget behaviour and preventing false command-failure notifications.
- Continues to report connection, authentication, session, malformed-response, unexpected-hub, and explicit non-zero `errorCode` failures.
- Rewords the remaining command error so it is reserved for genuine failures rather than a missing acknowledgement field.

### 0.3.1

- Aligns every correlated motor-battery name with the commandable room/panel label shown under Controls instead of exposing inconsistent hub-internal names.
- Adds translated `motor 1`, `motor 2` suffixes only when one commandable panel contains multiple physical motors, following the hub's panel-slot order with a stable window-ID fallback.
- Keeps battery unique IDs and existing entity IDs unchanged, and preserves user-assigned Home Assistant names during the upgrade.

### 0.3.0

- Added numeric Open and Closed settings with global defaults and sparse room or panel overrides. New installs use Open `37` and Closed `100`; Closed can be reversed to `0`.
- Migrates every discovered v0.2 movement profile to equivalent numeric values without changing existing directions or cover unique IDs.
- Uses the live-verified room-wide hub command when every target matches, so compatible room panels open or close together; mixed overrides retain exact per-panel control.
- Maps aggregate room state through each panel's own profile and keeps the native Home Assistant position slider for arbitrary positions.
- Added translated diagnostic battery sensors for physical motors using the existing 60-second snapshot, with no additional hub poll or login.
- Added real Home Assistant tests for numeric option flows, migration, mixed-profile commands and state, semantic broadcasts, batteries, dynamic discovery, and minimum-version compatibility.

### 0.2.2

- Verified login, discovery, concurrent sessions, control acknowledgement, and logout against the physical Gen 1 hub.
- Fully drains failed CGI responses before recovery and gives an actionable hub-restart message when the embedded login service remains unavailable.
- Accepts the hub's real `{"remote":"ok"}` control acknowledgement instead of reporting a successful command as unconfirmed.
- Organizes covers into hub-linked room devices: a room-wide main cover plus honest panel/group subcontrols, while preserving existing entity unique IDs.
- Documents the proprietary 2.4 GHz Norman repeater network and why ESPHome Bluetooth proxies cannot replace it.

### 0.2.1

- Carried hub-issued session cookies through the forced logout and single login retry.
- Added real HTTP regression coverage for cookies returned with the 500 response, logout transition cookies, and identical cookie values reissued during logout.
- Added a validation guard for the bundled local icon and logo and documented Home Assistant and HACS branding compatibility.

### 0.2.0

- Reworked the integration around a typed config-entry coordinator and shared entity base.
- Serialized complete hub transactions and validation against the same runtime lock.
- Added permanent hub-identity pinning, reauthentication, safe reconfiguration, dynamic discovery, and shutdown coordination.
- Isolated hub cookies from Home Assistant's shared session and added upgrade-safe option, entity, and device registry migrations.
- Corrected conventional, tilt, and reversed-tilt position semantics and blocked unsafe no-level fallbacks.
- Hardened response parsing, authentication/session classification, command confirmation, diagnostics privacy, and host validation.
- Added real Home Assistant tests across the declared minimum and current versions, current-Core linting, and a 95% combined coverage gate.
- Kept `123456789` as the documented and pre-filled factory password.
- Raised the minimum Home Assistant version to 2024.11.0.

### 0.1.12

- Added a one-time session reset and retry when `GatewayLogin` returns the hub's stale-session HTTP 500 page.

### 0.1.7–0.1.11

- Added group-level room control, transaction logout, plantation-shutter position handling, reversed close support, and configurable room/panel movement profiles.
