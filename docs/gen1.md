# Norman Gen 1 guide

This guide covers the Gen 1 backend. For shared installation instructions and the
generation selector, see the [main README](../README.md). ShadeAuto hubs use the
[Gen 2 guide](gen2.md).

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

## Setup

Enter:

- **Host:** the hub's IP address or local hostname. A bare host, optional port, or root `http://` URL is accepted. HTTPS is rejected because the Gen 1 hub protocol is HTTP-only; credentials, paths, queries, and fragments are also rejected.
- **Password:** pre-filled with the factory password `123456789`.
- **App version:** defaults to `2.11.21`, the version string sent to the hub.

Setup logs in and verifies that the endpoint returns usable room or shutter data. The returned hub ID becomes the config-entry, device, and entity identity. A later response from a different hub is rejected before data is read or a command is sent.

The integration page contains the physical hub plus one child device per Norman room. Each room device has a room-wide main cover and its addressable panel/group covers. Existing entity unique IDs are retained when migrating older Gen 1 installations, so entity-based automations and user renames remain intact. Review device-based automation targets because the covers move from the hub device to their room device.

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

The ordinary arrows on Home Assistant's device page are its native Open and Close controls, not one-point steps. The position slider remains available for other positions. Room commands use the correlated panel levels by default, sending each exact target one second apart inside the same authenticated transaction. This paced path is more reliable across the Gen 1 firmware variants seen so far.

Under **Configure → Room commands**, a room can be explicitly allowed to use the hub's single room-wide Open or Close broadcast. The integration uses that path only when the configured endpoint exactly matches the hub-native semantic command; a non-native endpoint still uses exact level commands. Position-slider commands always use exact level commands, including `0%` and `100%`.

If a room has no usable panel levels, only Open or Close commands that exactly match a safe hub-native room broadcast are exposed. This fallback does not require the simultaneous-command option because no exact level path is available.

## Automations

Gen 1 adds no custom actions, triggers, or conditions. Use Home Assistant's standard `cover` actions and state triggers/conditions. For example, this automation moves a Norman cover to 35% each evening:

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

Downloaded diagnostics include normalized counts, positions, styles, movement options, effective room command modes, and numeric room/window correlation IDs. They exclude the host, password, hub ID, hub name, room/window names, raw device payloads, and all unknown login-payload fields.

## Troubleshooting

- **Cannot connect:** confirm the address is reachable from the Home Assistant host and that TCP port 80 is not blocked.
- **Hub needs a restart / repeated HTTP 500:** the embedded Cherokee CGI service can become globally unresponsive even though the hub still answers on port 80. Restart or power-cycle the hub, wait for its status light to settle, then reload the integration. The integration drains the failed response and makes one safe recovery attempt before showing this action.
- **Invalid authentication:** try the factory password `123456789` unless the hub password was changed.
- **No devices:** confirm rooms and shutters are visible to the official Norman app and paired with the hub.
- **Command accepted but the shutter did not move:** the hub cannot confirm RF delivery to a motor. Check hub RF range, motor battery, pairing, and the Norman USB repeaters.
- **A whole-room cover moves only one panel or no panels:** inspect that cover's
  state attributes in Home Assistant. `open_command` and `close_command` show
  whether the integration is using the hub's room broadcast or sequential
  `level_fanout`; `level_command_plan` shows the level, model, group IDs, and
  raw positions that will be sent. Enable debug logging for
  `custom_components.norman_gen1` to capture the same safe command plan in the
  logs.
- **Wrong direction:** use the per-room or per-panel movement-profile options.
- **Official app and Home Assistant:** both can use independent hub sessions. Update to the latest integration version so every Home Assistant transaction is serialized and logged out cleanly.
- **Weak shutter radio signal:** keep the Norman USB repeaters powered and positioned within the proprietary RF network. ESPHome Bluetooth proxies do not extend this link.

## Known limitations

- Hardware testing is currently limited to one Gen 1 hub; payloads from other firmware and regional variants are welcome.
- The hub does not reliably acknowledge accepted commands and cannot confirm whether a motor physically moved. Home Assistant still reports transport, authentication, malformed-response, and explicit hub errors.
- A room-wide broadcast is available only as a no-level fallback or for explicitly selected rooms whose configured endpoint matches the hub-native command. Mixed or non-native profiles necessarily use exact sequential level commands.
- A panel entity represents one commandable room level. Firmware may report multiple window records for the same level; those records remain one aggregate control.
- Battery sensors represent the individual physical window/motor records and are attached to the existing room device so the room-grouped UI remains intact.
- Direct proprietary RF or Bluetooth control is not provided; commands intentionally pass through the Norman hub and repeaters.
- Correlated panel-name, level-numbering, and motor-order changes trigger an automatic reload. User-assigned Home Assistant entity names remain unchanged.
- Gen 2 uses a separate backend; Gen 1 profiles and room broadcasts do not apply to ShadeAuto.
