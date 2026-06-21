# Norman Gen 1 Hub Home Assistant Integration

Local Home Assistant custom integration for Norman Gen 1 shutter/blind hubs.

This project was created because I could not get a Gen 2 hub to test with, and the existing public options I found were aimed at newer hubs. It works with my Gen 1 hub, but the API was inferred from local network traffic, so other Gen 1 firmware versions or regional hub variants may behave differently.

## Features

- Local polling and local commands, with no cloud dependency for shutter control.
- Creates `cover` entities for each room returned by the hub.
- Creates `cover` entities for each room group/level, which is useful for plantation shutter panels.
- Discovers room, shutter, and group IDs from the hub during setup; no captured device IDs are hardcoded.
- Supports open, close, and set position.
- Uses a mid-position open target for tilt-style plantation shutter rooms when the hub reports those rooms as needing one, so `open` does not drive the louvers through open and closed again.
- Lets users override plantation shutter open/close direction per room or panel from the integration Configure screen.
- Keeps cover controls available while shutters are moving, then refreshes after a 10 second settle period.
- Raises Home Assistant errors when the hub cannot be reached or does not confirm a control command.
- Includes local brand assets for Home Assistant/HACS.

## What It Talks To

The integration uses the local Gen 1 HTTP API exposed by the hub:

- `POST /cgi-bin/cgi/GatewayLogin`
- `POST /cgi-bin/cgi/getRoomInfo`
- `POST /cgi-bin/cgi/getWindowInfo`
- `POST /cgi-bin/cgi/RemoteControl`

Gen 2 hubs are not supported by this integration unless they expose the same Gen 1 endpoints.

## Finding Your Hub IP Address

You need the hub's local IP address before adding the integration.

Good ways to find it:

- Check your router, firewall, or Wi-Fi controller client list. Look for a Norman hub, a hostname starting with `NORMANHUB`, or a device you can open in a browser on port `80`.
- In the Norman app, check whether the hub details show its network address.
- From a computer on the same network, inspect your ARP table:

```powershell
arp -a
```

- If you have `nmap`, scan your local subnet and then try the likely addresses in a browser:

```bash
nmap -sn 192.168.1.0/24
```

Replace `192.168.1.0/24` with your own LAN subnet. Common examples are `192.168.0.0/24`, `192.168.1.0/24`, or `10.0.0.0/24`.

Once you think you have the IP, visit:

```text
http://<hub-ip>/
```

If the Norman hub web page loads, use that IP in Home Assistant.

## Password

Many Gen 1 hubs appear to use this default local password:

```text
123456789
```

If that does not work, use the password configured for your hub in the Norman app or hub settings.

## HACS Installation

Use this link if you already have HACS installed:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=Norman_Gen1_HA_Integration&category=integration)

Or add it by hand:

1. In Home Assistant, open HACS.
2. Go to the three-dot menu and choose **Custom repositories**.
3. Add this repository URL:

```text
https://github.com/Herbertmt978/Norman_Gen1_HA_Integration
```

4. Set the category to **Integration**.
5. Install **Norman Gen 1 Hub**.
6. Restart Home Assistant.
7. Go to **Settings -> Devices & services -> Add integration**.
8. Search for **Norman Gen 1 Hub**.
9. Enter the hub IP address, password, and app version.

The default app version is `2.11.21`. Leave it as-is unless you know your hub expects something else.

During setup the integration logs into the hub and scans `getRoomInfo` and `getWindowInfo`. Those responses are used to build the Home Assistant entities, so hub-specific IDs such as room IDs, shutter IDs, group IDs, and levels should be picked up dynamically.

## Plantation Shutter Direction Options

The integration includes tested plantation shutter defaults, but different Gen 1 hubs or motor pairings may report the same style with a different physical direction.

To adjust this after setup:

1. Go to **Settings -> Devices & services**.
2. Open **Norman Gen 1 Hub**.
3. Choose **Configure**.
4. Use **Open to position 37** for rooms or panels where Home Assistant's open command should stop at the visual open louver angle.
5. Use **Close to position 100** for rooms or panels where the close direction is reversed.

Selecting a room applies to that room entity and all of its panel/group entities. Selecting a panel only changes that panel. The `open_position` and `close_position` entity attributes show the positions Home Assistant will send.

## Manual Installation

Copy `custom_components/norman_gen1` into your Home Assistant `custom_components` folder and restart Home Assistant.

## Entity Names

The hub assigns numeric IDs to rooms and panels, but Home Assistant entity names are based on the room and group names returned by `getRoomInfo`.

If the names look odd in Home Assistant, check how rooms and groups are named in the Norman app. The integration does not assume fixed names like "Room 1", "Room 2", or "Office"; it uses whatever the hub reports.

## Known Limitations

- Tested against one Gen 1 hub only.
- Gen 2 hubs are untested and likely need a different API.
- Some firmware versions may return different field names or command responses.
- The hub can acknowledge a command even when a shutter motor does not physically move. If the official Norman app also cannot move that room or panel, check hub placement, RF range, motor battery, and pairing before troubleshooting this integration.
- A handheld Norman remote moving a shutter does not prove that the hub can move it. The handheld remote may be paired directly with the motor while the hub has a stale pairing, poor range, or a different room/panel mapping.
- BroadLink-style RF learning is not a guaranteed fallback. If an RM Pro stays in learning mode while the shutter still responds to the Norman remote, the Norman remote is probably using a frequency or protocol the BroadLink cannot learn.
- If setup cannot reach the hub, authentication fails, or the hub returns no rooms/shutters, Home Assistant will show a setup error.
- If a command is sent but the hub does not confirm it, Home Assistant will raise a service error instead of silently assuming success.
- The hub can acknowledge a command before shutters finish moving, so this integration assumes the requested position for 10 seconds before polling again.
- Room-level intermediate positions are applied by sending the same target position to each room group/level.
- Some plantation shutter motors use both end stops as closed louver angles. On the tested hub, Lounge, Bedroom, and Office needed position `37` as the visual open target. The integration now keeps tested room styles on fixed targets so transient in-motion positions do not get remembered by mistake.
- Close uses position `0` for the tested Lounge and Bedroom styles. The tested Office style closes in the opposite direction, so it uses position `100` for close.

Issues and packet captures from other Gen 1 hubs are welcome, especially if a hub returns different room, group, or window data.

## Changelog

### 0.1.12

- Added a one-time session reset when `GatewayLogin` returns the hub's Cherokee `500 Internal Server Error` page. The integration now calls `AdminLogout` and `GatewayLogout` and retries login once, which recovers hubs that get stuck with a stale local CGI session.

### 0.1.7

- Fixed room-level open and close commands for hubs where Norman's `fullopen`/`fullclose` room command reports success but does not move every shutter. Room entities now send the same group/level commands used by the panel entities, using discovered group levels and panel model values.
- Added a focused unit test for room-level control so room close/open continues to use discovered group commands.

### 0.1.8

- Log out of the hub after setup checks, polling, and control commands so Home Assistant does not hold the Gen 1 hub session open and block the Norman phone app.
- Added troubleshooting guidance for rooms or panels that do not move in either Home Assistant or the official Norman app.

### 0.1.9

- Added tilt-style plantation shutter open-position handling. Rooms reported by the hub with tested tilt styles now use position `37` for `open`, because `100` can drive the louvers past open and closed again. This covers the tested Lounge, Bedroom, and Office room styles.
- Room and panel entities expose the `open_position` attribute so users can see which open target is being used.

### 0.1.10

- Corrected the tested Office room style so it keeps using `100` for `open`; Office open was already working correctly.
- Added an explicit `close_position` attribute and kept close at `0` for the tested room styles.
- Prevented transient learned positions from overriding known tested room styles while shutters are moving.

### 0.1.11

- Corrected the tested Office plantation shutter style after live testing showed `100` was the close direction, not the visual open position.
- Office-style shutters now use position `37` for open and position `100` for close.
- Lounge and Bedroom tested styles continue to use position `37` for open and position `0` for close.
- Added a Configure screen so users can choose which rooms or panels open to position `37` and which close to position `100`.
- Treat either end stop as closed for tilt-style plantation shutters, so reversed shutters do not show as open when they are closed at position `100`.
