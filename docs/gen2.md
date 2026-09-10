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
Gen 1 passwords, movement profiles, room broadcasts and battery sensors do not
apply to this backend.

The mapping comes from keito's SmartDrape implementation: the bottom rail maps
to cover position and the middle rail maps to tilt. With a compatible two-rail
shade, tilt controls the second rail. Both use 0–100. Device-specific mappings
beyond this upstream model require verified device data.

## Actions

Use the standard cover actions for opening, closing and setting the position or
tilt. Controlling one rail preserves the latest known target of the other rail,
or its reported position if no target is available. If the other rail is unknown,
the command fails instead of guessing a position.

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
notification stream. A notification triggers a fresh status request. Commands
also request a status refresh. The listener reconnects after a connection failure
and periodically renews the stream. Failed status requests mark covers unavailable;
valid later responses restore availability.

Reload the entry after pairing additional peripherals, because full discovery
metadata is read at setup. Battery voltage is present in the upstream data model
but this backend does not currently expose battery entities.

## Reconfiguration and diagnostics

Use **Reconfigure** to change the hub address. The returned registration identity
must match the saved hub. To use a different hub, add another entry. ShadeAuto has
no additional options or Gen 1 password-recovery flow.

Diagnostics contain the generation, device count and connection status. They do
not include the hub address, identity, device names or raw API responses.

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
