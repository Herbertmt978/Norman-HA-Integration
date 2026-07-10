# Norman Gen 1 Hub for Home Assistant

A local-polling Home Assistant integration for Norman Gen 1 shutter and blind hubs.

The Gen 1 protocol was inferred from local network traffic and verified against a real hub. It is not affiliated with or supported by Norman. Gen 2 hubs are not supported unless they expose the same local endpoints.

## What it provides

- One child device for every room, linked to the physical hub device.
- One room-wide `cover` as each room device's main control.
- One `cover` for every discovered room group or plantation-shutter panel.
- Native Open, Close, and target-position control with configurable raw targets.
- Global, room, and panel movement profiles; new installs default to Open `37`
  and Closed `100`.
- One diagnostic battery-percentage sensor for every physical motor reported by
  the hub, named to match its commandable panel.
- Dynamic addition of rooms and panels discovered after setup.
- Reauthentication, connection reconfiguration, translated errors, and privacy-safe diagnostics.
- Local communication only; shutter control has no cloud dependency.

Home Assistant 2024.11.0 or newer is required.

## Factory password

The Norman Gen 1 factory password is:

```text
123456789
```

That value is intentionally kept in this repository and pre-filled in the setup, reauthentication, and reconfiguration forms. Replace it only if the hub password has been changed from the factory value.

## Security note

The Gen 1 hub exposes an HTTP API rather than HTTPS. The password and commands therefore travel over unencrypted HTTP on the local network. Keep the hub and Home Assistant on a trusted LAN or isolated IoT network; do not expose the hub API to the internet.

## Supported protocol

The integration uses these local endpoints:

- `GatewayLogin`
- `getRoomInfo`
- `getWindowInfo`
- `RemoteControl`
- `AdminLogout` and `GatewayLogout`

Every integration operation is serialized as one login → request(s) → logout transaction. The hub issues a session cookie at login and refreshes it on later requests. Live testing confirms that the official app and Home Assistant can hold independent sessions, while serialization prevents Home Assistant's own polls, validation, and commands from overlapping.

## Installation with HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=Norman_Gen1_HA_Integration&category=integration)

Alternatively:

1. Open HACS and choose **Custom repositories**.
2. Add `https://github.com/Herbertmt978/Norman_Gen1_HA_Integration` as an **Integration**.
3. Install **Norman Gen 1 Hub**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration** and search for **Norman Gen 1 Hub**.

For a manual installation, copy `custom_components/norman_gen1` into Home Assistant's `custom_components` directory and restart Home Assistant.

## Setup

Enter:

- **Host:** the hub's IP address or local hostname. A bare host, optional port, or root `http://` URL is accepted. HTTPS is rejected because the Gen 1 hub protocol is HTTP-only; credentials, paths, queries, and fragments are also rejected.
- **Password:** pre-filled with the factory password `123456789`.
- **App version:** defaults to `2.11.21`, the version string sent to the hub.

Setup logs in and verifies that the endpoint returns usable room or shutter data. The returned hub ID becomes the config-entry, device, and entity identity. A later response from a different hub is rejected before data is read or a command is sent.

The integration page contains the physical hub plus one child device per Norman room. Each room device has a room-wide main cover and its addressable panel/group covers. Existing entity unique IDs are retained during this upgrade, so entity-based automations and user renames remain intact. Review device-based automation targets because the covers move from the hub device to their room device.

### Finding the hub

Check the client list in your router or network controller for a Norman device or a hostname beginning with `NORMANHUB`. You can also inspect the local ARP table:

```powershell
arp -a
```

Or scan the appropriate LAN subnet:

```bash
nmap -sn 192.168.1.0/24
```

Opening `http://<hub-address>/` in a browser can help confirm the address.

## Radio and USB repeaters

Home Assistant communicates with the hub over the local LAN. The hub and Norman USB repeaters then use Norman's **proprietary 2.4 GHz RF** network to reach the shutters. The repeaters join that network automatically and retransmit control traffic. The [Gen 1 hub manual](https://fcc.report/FCC-ID/ppqhub01/4063119.pdf) recommends a repeater in each room and another near the stairs when the signal must cross floors.

This radio is not Bluetooth. ESPHome Bluetooth proxies cannot observe or repeat its packets, and the integration deliberately leaves the proven hub/repeater RF path in place. Direct radio support would require a separate hardware and protocol reverse-engineering project.

## Position behavior

Home Assistant always exposes positions as `0%` closed to `100%` visually open. The hub's raw movement range is mapped onto that convention:

| Setting | New-install default | Allowed values |
|---|---:|---:|
| Open | Hub position `37` | Any whole number from `0` to `100` |
| Closed | Hub position `100` | End stop `0` or `100` |

For tilt shutters, either physical end stop can represent closed louvers. The integration does not learn an open target from a transient in-motion position.

Open **Configure** on the integration to change the defaults or set an override for a room or individual panel. A panel inherits its room, and a room inherits the global defaults, unless that target is explicitly pinned. Open and Closed cannot use the same raw position.

Existing v0.2 installations are migrated to exact numeric profiles after the first successful discovery. Their current effective Open and Closed directions are retained; the 37/100 defaults apply to new installations and newly discovered targets. Existing cover unique IDs and automation targets are unchanged.

The ordinary arrows on Home Assistant's device page are its native Open and Close controls, not one-point steps. The position slider remains available for other positions. A room Open or Close uses the hub's one room-wide broadcast when every panel target matches the hub-native room profile, so those motors start together. If panel overrides differ, the integration sends the exact configured group targets sequentially inside the same authenticated transaction rather than ignoring an override.

If a room has no usable panel levels, only Open or Close commands that exactly match a safe hub-native room broadcast are exposed.

## Automations

The integration registers no custom actions, triggers, or conditions. Use Home Assistant's standard `cover` actions and state triggers/conditions. For example, this automation moves a Norman cover to 35% each evening:

```yaml
alias: Set Norman shutters for the evening
triggers:
  - trigger: time
    at: "19:30:00"
actions:
  - action: cover.set_cover_position
    target:
      entity_id: cover.living_room_shutters
    data:
      position: 35
```

Typical use cases include scheduled privacy positions, closing rooms when everyone leaves, and opening selected panels at sunrise. State-based automation conditions should allow for `unknown` or `unavailable` while a panel position or the hub cannot be read.

## Data updates and availability

- The hub is polled every 60 seconds.
- Room metadata and shutter state are fetched in one serialized authenticated transaction.
- During normal polling, motor battery percentages come from the same `getWindowInfo` response and add no separate poll, login, cookie, or network request.
- Battery names use the same room and level metadata as the Controls panel. If one panel contains multiple physical motors, they are numbered deterministically as `motor 1`, `motor 2`, and so on.
- A rejected session is retried once with a fresh login.
- A failed Cherokee CGI response is fully consumed before recovery starts, so recovery requests cannot overlap a still-running failed request.
- Authentication failures start Home Assistant's reauthentication flow.
- Communication, malformed-data, empty-snapshot, and hub-identity failures make entities unavailable without deleting their last known registry entries.
- New rooms, groups, and physical-motor battery sensors are added dynamically after a successful poll.
- If a known motor's correlated panel label or motor number changes, the config entry reloads once so all translated battery names remain consistent.
- The Gen 1 control endpoint is fire-and-forget: a valid HTTP response with no explicit hub error is accepted without requiring an acknowledgement field.
- After a command, the requested state is shown optimistically for 10 seconds before a refresh.

## Reauthentication and reconfiguration

When the saved password is rejected, Home Assistant starts reauthentication and pre-fills the factory password. The replacement is accepted only if the same hub responds.

Use **Reconfigure** to change the host, password, or app version. A host change is accepted only when Home Assistant can compare stable hub IDs. A legacy entry that has only a host-based identity must first reconnect at its old address so the hub ID can be learned; otherwise remove and add the integration again.

## Diagnostics and privacy

Downloaded diagnostics include normalized counts, positions, styles, movement options, and a small whitelist of safe firmware/status fields. They exclude the host, password, hub ID, hub name, room/window names, raw device payloads, and all unknown login-payload fields.

## Removal

Remove the config entry from **Settings → Devices & services**. Home Assistant unloads the cover and sensor platforms, stops coordinator refreshes, waits for any active transaction, logs out, and releases the client. Remove the HACS repository separately if the integration is no longer required.

## Troubleshooting

- **Cannot connect:** confirm the address is reachable from the Home Assistant host and that TCP port 80 is not blocked.
- **Hub needs a restart / repeated HTTP 500:** the embedded Cherokee CGI service can become globally unresponsive even though the hub still answers on port 80. Restart or power-cycle the hub, wait for its status light to settle, then reload the integration. The integration drains the failed response and makes one safe recovery attempt before showing this action.
- **Invalid authentication:** try the factory password `123456789` unless the hub password was changed.
- **No devices:** confirm rooms and shutters are visible to the official Norman app and paired with the hub.
- **Command accepted but the shutter did not move:** the hub cannot confirm RF delivery to a motor. Check hub RF range, motor battery, pairing, and the Norman USB repeaters.
- **Wrong direction:** use the per-room or per-panel movement-profile options.
- **Official app and Home Assistant:** both can use independent hub sessions. Update to the latest integration version so every Home Assistant transaction is serialized and logged out cleanly.
- **Weak shutter radio signal:** keep the Norman USB repeaters powered and positioned within the proprietary RF network. ESPHome Bluetooth proxies do not extend this link.
- **HACS icon is unavailable:** Home Assistant 2026.3 or newer can serve the bundled custom-integration brand images locally. Restart Home Assistant after installing or updating the integration, then refresh the browser. HACS versions that still use the public Brands service may continue to show a placeholder; this does not affect the integration itself.

## Known limitations

- Hardware testing is currently limited to one Gen 1 hub; payloads from other firmware and regional variants are welcome.
- The hub does not reliably acknowledge accepted commands and cannot confirm whether a motor physically moved. Home Assistant still reports transport, authentication, malformed-response, and explicit hub errors.
- A room-wide broadcast is available only when all configured panel targets match the hub-native command. Mixed or non-native profiles necessarily use exact sequential group commands.
- A panel entity represents one commandable room level. Firmware may report multiple window records for the same level; those records remain one aggregate control.
- Battery sensors represent the individual physical window/motor records and are attached to the existing room device so the room-grouped UI remains intact.
- Direct proprietary RF or Bluetooth control is not provided; commands intentionally pass through the Norman hub and repeaters.
- Correlated panel-name, level-numbering, and motor-order changes trigger an automatic reload. User-assigned Home Assistant entity names remain unchanged.
- Gen 2 compatibility is not claimed.

## Development and verification

The repository keeps protocol tests separate from tests that run through a real Home Assistant instance. CI enforces current Home Assistant's Ruff rules and at least 95% combined branch coverage, then executes the public-behavior suite on both Home Assistant 2024.11 and the current release.

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
pytest tests -q
```

Real Home Assistant tests use `requirements_ha_minimum.txt` or `requirements_ha_current.txt` and run `pytest ha_tests -q` on Linux.

An eventual Home Assistant Core submission will also require extracting the protocol client into a typed asynchronous PyPI package, adding the Core-specific manifest/quality-scale files, and preparing separate documentation and brands pull requests. The integration code and behavioral tests are structured to make that extraction mechanical rather than architectural.

## Changelog

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
