# Norman Gen 2 (ShadeAuto) guide

This guide covers the ShadeAuto backend. Use the [main README](../README.md)
for installation and [the Gen 1 guide](gen1.md) for older hubs.

## Setup and discovery

1. Pair your blinds with the hub using the ShadeAuto app.
2. Add **Norman** in Home Assistant and select **Gen 2 (ShadeAuto)**.
3. Enter the local IP address or hostname. Port `10123` is fixed and supplied by
   the integration, so do not append a port. A root HTTP URL is accepted.

Setup registers with the hub, reads its identity, then checks discovery and
status. Each discovered peripheral becomes a cover. Its unique ID includes the
hub identity, so matching peripheral numbers on separate hubs do not collide.
Gen 1 passwords, movement profiles and room broadcasts do not apply to this backend.

### Room covers

Each discovered room with an integer or numeric-string room ID and at least one shade also gets a
separate **Shades** cover, alongside the individual covers. For example, a room
named Living room gets a Living room Shades cover. Existing individual cover
identities are unchanged. Reload the integration after changing room membership
in ShadeAuto, because discovery metadata is read at setup.

Individual and room controls share a per-hub queue. Control requests start at
least 750 ms apart and carry distinct timestamps and task IDs, plus the hub
identity returned during registration. A slow request may take longer; different
hubs have independent queues. This is a compatibility measure based on other
ShadeAuto clients, awaiting physical Gen 2 feedback.

Room controls send the existing per-shade commands sequentially; they are not a
single hub broadcast and do not guarantee simultaneous movement. Open, close,
position, tilt and nudge actions affect only the shades discovered in that room.
Each shade keeps its own other rail position or pending target. A nudge is relative
to each shade's own position, not the room average.

All member positions are checked before any command is sent. If an untouched
second rail has been reported by the hub but its position is unknown, nothing moves.
A shade that has never reported a valid non-null middle-rail position or target receives bottom-only
commands, with the middle field omitted from the request. It has no tilt actions.
Once a second rail has been observed during the current integration load, a later
null or missing field does not reclassify it. Rail observation starts afresh on reload.
A mixed room offers bottom-rail actions only. If a hub request fails after commands have started, the
action reports the failure, stops sending further commands and refreshes status.
Some shades may already have moved; a connection failure can also leave the failed
shade's movement uncertain. Check the actual shades before trying again. There is
no automatic retry or rollback movement.

The room position is the average of its members' reported positions. It is unknown
if any member's position is unknown. The room is closed only when every member is
closed, and becomes unavailable if its hub is unavailable or it has no members.
The member count and mean pending targets are available as state attributes.

Room control is included from **v0.5.0** and has not yet been checked on physical
Gen 2 hardware. The maintainer only has a Gen 1 hub. See the [release notes and
rollback instructions](releases/v0.5.0.md). The room commands in
[nsleigh/norman-blinds](https://github.com/nsleigh/norman-blinds) use the different
legacy CGI API; they are not used for ShadeAuto. A verified Gen 2 app request would
be needed before adding a native room-broadcast command.

There is no verified capability table for `ModuleType`/`ModuleDetail`. Second-rail
capabilities therefore follow the fields reported by the hub, rather than guessed
model numbers. This requires physical testing on single-rail and mixed rooms.

The two-rail mapping comes from keito's SmartDrape implementation: the bottom rail maps
to cover position and the middle rail maps to tilt. With a compatible two-rail
shade, tilt controls the second rail. Both use 0–100. Device-specific mappings
beyond this upstream model require verified device data.

## Actions

Use the standard cover actions for opening, closing and setting the position or
tilt. Controlling one rail preserves the latest known target of the other rail,
or its reported position if no target is available. An unknown previously reported
rail blocks the command instead of guessing a position. A middle rail never
reported by the hub is omitted on bottom-only commands. Accepted targets are retained
for up to 30 seconds while the hub catches up, so consecutive commands and nudges
preserve each other. Hub acknowledgement or timeout restores the reported target.
Malformed rail values make the hub unavailable until a valid status refresh.

```yaml
action: cover.set_cover_tilt_position
target:
  entity_id: cover.living_room_shade
data:
  tilt_position: 50
```

Two additional actions move relative to the latest target or reported position:

| Action | Behavior |
| --- | --- |
| `norman_gen1.nudge_position` | Adds `step` to the bottom-rail position |
| `norman_gen1.nudge_tilt` | Adds `step` to tilt/second-rail position |

`step` is an integer from -100 to 100; the resulting target is clamped to 0–100.
A positive step increases the rail value, and a negative step decreases it. Use
the physical device's behavior to interpret the tilt direction. The action domain
is still `norman_gen1` because it is shared with existing installations.

```yaml
action: norman_gen1.nudge_position
target:
  entity_id: cover.living_room_shade
data:
  step: 5
```

No stop action is exposed: the imported local API has no verified stop command.

## State updates and reconnection

The integration discovers devices, fetches their status and opens a local
notification stream. Notifications and command bursts schedule a status request
after two quiet seconds, with a five-second maximum wait during a continuous
notification stream. Queued control commands and their quiet period still finish
before that request starts. A later notification does not cancel an active read.
A second status read follows 35 seconds after the latest attempted command, and a
60-second idle poll catches missed notifications. No movement command is retried.
The listener reconnects after a connection failure and periodically renews the
stream. Failed status requests mark covers unavailable; valid later responses
restore availability.

Opening and closing states compare a live local pending target with the current
position. A reported hub target can indicate direction for up to 120 seconds after
a detected position change; an unchanged stale target cannot extend that window.
Expiry updates the entity even without a new status response. Rail preservation
still uses the latest target independently of this motion limit. Missing positions
or targets remain unknown, and a room with members moving in opposite directions
reports neither opening nor closing. These are bounded direction indications,
not measured motion or an estimated travel time.

Reload the entry after pairing additional peripherals, because full discovery
metadata is read at setup. An optional **Battery reading** diagnostic sensor is
created for each shade and disabled by default. Enable it in the entity settings
if needed. It exposes finite numeric `BatteryVoltage` values unchanged, with no
unit, percentage conversion, battery device class or low-battery threshold.
Community clients disagree about the units; a nominal 3.7 V battery does not
establish what the hub reports. A missing or malformed reading remains unknown.

## Reconfiguration and diagnostics

Use **Reconfigure** to change the hub address. The returned registration identity
must match the saved hub. To use a different hub, add another entry. ShadeAuto has
no additional options or Gen 1 password-recovery flow.

Diagnostics contain the generation, device count and connection status, plus a
`room_discovery` summary: raw room count, distinct valid room-ID count, usable
room count, rejected room count, original room-ID type counts and rejection
reason counts. Counts describe discovery at setup, not physical movement. They
do not include the hub address, identity, room IDs, names or raw API responses.

Download diagnostics from **Settings → Devices & services → Norman**, open the
Gen 2 entry's three-dot menu and select **Download diagnostics**. If room devices
are missing after restarting on b4, attach this download to the issue. Invalid
room identities also produce a warning with only their type and rejection
reason. Individual covers remain available when their room identity is invalid.

## Troubleshooting

- **Cannot connect:** confirm you selected Gen 2, that the hub is reachable from
  Home Assistant and that TCP port 10123 is allowed on the local network.
- **Invalid response:** confirm the address belongs to a ShadeAuto hub, not a Gen 1
  hub or unrelated HTTP server. Keep sanitized device data when reporting an issue.
- **No covers:** check that peripherals are paired in ShadeAuto, then reload the entry.
- **Unknown rail position:** refresh/reload and confirm the hub reports both rails.
  A command cannot safely preserve a rail whose position is unknown.
- **A different hub is reported:** the address resolves to another hub. Correct the
  address or add a new entry for the replacement hub.
- **Unexpected tilt/second-rail behavior:** this backend follows the upstream
  SmartDrape/two-rail mapping. Include the device type and sanitized payload in an
  issue before assuming a different motor layout is supported.

## Protocol, attribution and validation

The backend uses `/NM/v1/registration`, `/NM/v1/GetAllPeripheral`,
`/NM/v1/status`, `/NM/v1/control` and `/NM/v1/notification` over local HTTP.
It derives from [keito/home-assistant-norman](https://github.com/keito/home-assistant-norman).
See [NOTICE](../NOTICE) and the [Apache-2.0 license](../licenses/keito-Apache-2.0.txt).

The combined implementation has passed automated API and HA tests, plus setup,
controls, reload and notification tests through a running Home Assistant instance
using a local HTTP fixture. Physical Gen 2 hardware has not yet been verified for
this combined implementation. This qualification limit is separate from the
existing physical Gen 1 readback and regression coverage.
