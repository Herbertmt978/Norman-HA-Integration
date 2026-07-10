# Norman Gen 1 Hub for Home Assistant

A local-polling Home Assistant integration for Norman Gen 1 shutter and blind hubs.

The Gen 1 protocol was inferred from local network traffic and verified against a real hub. It is not affiliated with or supported by Norman. Gen 2 hubs are not supported unless they expose the same local endpoints.

## What it provides

- One `cover` entity for every room.
- One `cover` entity for every discovered room group or plantation-shutter panel.
- Open, close, and target-position control.
- Safe position mapping for conventional and tilt-style shutters.
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

Every operation is serialized as one login → request(s) → logout transaction. This matters because the hub behaves as a single-session device and concurrent sessions can invalidate one another.

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

## Position behavior

Home Assistant always exposes positions as `0%` closed to `100%` visually open. The hub's raw movement range is mapped onto that convention:

| Shutter profile | Home Assistant closed | Home Assistant open |
|---|---:|---:|
| Conventional | Hub position `0` | Hub position `100` |
| Tilt styles 2 and 3 | Hub position `0` | Visual-open position `37` |
| Reversed tilt style 13 | Hub position `100` | Visual-open position `37` |

For tilt shutters, either physical end stop can represent closed louvers. The integration does not learn an open target from a transient in-motion position.

Open **Configure** on the integration to override the tilt-open or reversed-close profile for a room or individual panel. A room selection applies to its panels. Explicit choices are remembered, while a newly discovered room still receives its safe automatic profile.

If a room has no usable panel levels, only room-wide commands that the hub can represent safely are exposed. In particular, a reversed `100` close target is never sent through the hub's `fullopen` fallback.

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
- A rejected session is retried once with a fresh login.
- Authentication failures start Home Assistant's reauthentication flow.
- Communication, malformed-data, empty-snapshot, and hub-identity failures make entities unavailable without deleting their last known registry entries.
- New rooms and groups are added dynamically after a successful poll.
- After a command, the requested state is shown optimistically for 10 seconds before a refresh.

## Reauthentication and reconfiguration

When the saved password is rejected, Home Assistant starts reauthentication and pre-fills the factory password. The replacement is accepted only if the same hub responds.

Use **Reconfigure** to change the host, password, or app version. A host change is accepted only when Home Assistant can compare stable hub IDs. A legacy entry that has only a host-based identity must first reconnect at its old address so the hub ID can be learned; otherwise remove and add the integration again.

## Diagnostics and privacy

Downloaded diagnostics include normalized counts, positions, styles, movement options, and a small whitelist of safe firmware/status fields. They exclude the host, password, hub ID, hub name, room/window names, raw device payloads, and all unknown login-payload fields.

## Removal

Remove the config entry from **Settings → Devices & services**. Home Assistant unloads the cover platform, stops coordinator refreshes, waits for any active transaction, logs out, and releases the client. Remove the HACS repository separately if the integration is no longer required.

## Troubleshooting

- **Cannot connect:** confirm the address is reachable from the Home Assistant host and that TCP port 80 is not blocked.
- **Invalid authentication:** try the factory password `123456789` unless the hub password was changed.
- **No devices:** confirm rooms and shutters are visible to the official Norman app and paired with the hub.
- **Command not confirmed:** check hub RF range, motor battery, and pairing. A handheld remote can work even when the hub's pairing or range is wrong.
- **Wrong direction:** use the per-room or per-panel movement-profile options.
- **Official app stops working while Home Assistant polls:** update to the latest integration version; all transactions now log out and are serialized.
- **HACS icon is unavailable:** Home Assistant 2026.3 or newer can serve the bundled custom-integration brand images locally. Restart Home Assistant after installing or updating the integration, then refresh the browser. HACS versions that still use the public Brands service may continue to show a placeholder; this does not affect the integration itself.

## Known limitations

- Hardware testing is currently limited to one Gen 1 hub; payloads from other firmware and regional variants are welcome.
- The hub can acknowledge a command even if a motor does not physically move.
- Room-level positions are implemented by sending the target to every discovered group level.
- Entity display names are learned when entities are first created. Rename entities in Home Assistant if hub-side names later change.
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

### 0.2.1

- Fixed the post-upgrade `GatewayLogin` HTTP 500 regression by carrying hub-issued session cookies through the forced logout and single login retry.
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
