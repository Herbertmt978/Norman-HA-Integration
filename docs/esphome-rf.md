# ESPHome RF bridge — experimental

This is an experimental section-and-room transport, included from v0.5.0, for the separately built
[Norman RF Bridge](https://github.com/Herbertmt978/norman-rf-bridge). It does not
turn a Zigbee coordinator or Wi-Fi adapter into a Norman radio.

**12 September camera tests:** five office sections and four lounge panels
passed individual direct close/open, followed by a successful whole-room pair
for each room using its local bridge on firmware0.9. The interleaved scheduler
was unchanged. Earlier0.7/0.7.1 remote-placement room tests moved only one of five.
The new result is not prolonged reliability or exclusive ESP-only delivery:
the original hub and repeaters remained powered. Burst completion is not a
motor acknowledgment. Native room packet generation remains unqualified.

## Setup

1. Install firmware0.10.0 (native protocol3 plus learning API1) on the ESP32/nRF24
   bridge. Fit the antenna and follow its wiring guide.
2. Adopt the board through HA's ESPHome integration. This transport requires
   native ESPHome actions with structured response support, as documented in
   [ESPHome's API guide](https://esphome.io/components/api/#action-responses).
   The ordinary integration's HA2024.11 minimum is not a claim that an old native
   ESPHome integration supports this newer response protocol. A missing response
   capability is rejected during setup.
3. Add **Norman**, choose **ESPHome RF bridge (experimental)** and select the
   detected bridge. Use **Add a motorised section** or **Add an action for
   repeating**, then explicitly select its rooms. Each selected learned
   section and complete room becomes a separate cover. Assign a room to its
   tested local bridge; repeat for another bridge with different rooms.
   Closing uses that target's commissioned preferred direction.

The [learning and profile manual](https://github.com/Herbertmt978/norman-rf-bridge/blob/main/docs/learning.md)
walks through the controller presses, confirmation, naming, room grouping and
removal. Use **Reconfigure** later to add, rename, regroup or remove saved
profiles. A new empty bridge can enter the wizard; a relay-only bridge can be
saved without creating fictional shutter covers. Older firmware retains manual
commissioning and room selection, without the new wizard.

No hub host, password or rolling counter is entered in this flow. The ESP is
the sole owner of learned frames and persistent RF sequence. The selected bridge
fingerprints, slots, room labels and endpoints prevent changed commissioning
silently controlling different sections through an old HA entry. The firmware
has32 slots; each room accepts up to8 unique sections, sent one at a time.
Setup rejects rooms over that limit and profiles already bound to another RF
entry. Repeating another bridge's frames is still permitted: only direct-command
ownership is exclusive. This does not coordinate counters across separate HA
instances or manual ESPHome actions; avoid multiple independent senders.

## Operation

### Physical names and discovery

The bridge inventory supplies section names; the integration does not infer a
physical location from a hub's panel number, RF address or assumed cover state.
During commissioning, identify each section by a watched single-target command
and restore it before identifying another. Save the confirmed label on that
same ESP slot, leaving its room, fingerprint, templates and counter intact.
Do not guess an unobserved label from the remaining positions.

New setup imports the saved labels for selected rooms. For an existing RF entry,
**Reconfigure** the same bridge to refresh names or change its selected rooms;
inventory polling does not automatically
replace the labels captured at setup. Section unique IDs depend on bridge and
slot, not display name. Keep slots stable and retain any HA custom-name overrides
deliberately. Room names also define membership and room-cover identity, so
renaming or moving a room requires separate review. Existing Norman hub covers
are independent and keep their names and entity IDs.

The new wizard requires firmware0.10.0; the older inventory-only reconfigure
route remains available. Neither route requires a Norman hub upgrade.
Per-home physical mappings belong in installation data, never factory firmware.

Each cover offers `cover.open_cover` and `cover.close_cover`. It does not expose
arbitrary positioning or stop. The pilot's raw endpoints37/0/100 are firmware
command labels, not measured HA percentages. Reconfigure refreshes explicitly
changed commissioned bindings on the same bridge; it cannot switch bridges.

Room commands send one section at a time. With bridge firmware 0.12.0
(protocol 4), HA sends one room command after an inventory preflight; the bridge
sends each section's full burst (100 copies at55ms) with a short gap before the
next, then reports completion, which HA polls for. A four-section room takes
about24s. Because the bridge holds the whole room, a Wi-Fi or API stall cannot
stop later sections, and its automatic repeats cover every section. A newer
command preempts a running repeat. Older firmware receives one single-target
command per section from HA instead. This is not a guessed room-wide packet.

Earlier versions sent a room as one firmware batch that interleaved every
section's packets. That intermittently left sections unmoved even though their
frames were on air, as the hub's own room-wide broadcast did. On 13 October 2026
the lounge batch missed one of four panels on each of two camera-observed
actions; three sequential close/open pairs then moved all 24 panel movements,
with automatic repeats off. Paced sections also match the hub integration's
reliable room path. This is a qualification of one room, not a whole-house or
long-term reliability claim. With older firmware, each section's pending
automatic repeats are cancelled when the next section is sent, so only the last
section of a room receives them; protocol 4 bridges repeat the whole room.

Successful completion means the ESP finished its bounded RF burst, not that a
motor acknowledged movement. Position starts unknown, is explicitly assumed
after a successful command. A failed section becomes uncertain, even if its
motor may have moved; later sections in that room are not sent and keep their
previous state. Nothing is retried automatically. State returns to unknown
after entry reload. Do not use it as physical confirmation for safety decisions.

Inventory polling, pre-command inventory reads and command actions have a
12-second deadline. Transport failures include the operation, elapsed time and
exception type, including when the underlying timeout has no message. Enable
debug logging for `custom_components.norman_gen1.rf` to record operation start,
completion and cancellation timing.

A failed pre-command inventory read sends no movement command and preserves
previous assumed positions. Once a command has been submitted, a timeout or
cancellation leaves every selected section uncertain; other sections retain
their previous intent. Cancellation propagates and the command lock is released.
There is no automatic movement retry. These diagnostics do not establish why a
bridge stops responding or whether a shutter physically moved.

Existing hub covers are independent and their assumed state may be stale after
RF movement. There is no automatic hub fallback or HA-level retry after an
uncertain RF send. Firmware 0.9.2 can send two later identical bursts after a
successful local command; see the repeat policy below. Removing the RF entry
leaves the underlying ESPHome device, Bluetooth
proxy and commissioned autonomous relay operating.

## Verification and limits

Production camera checks on12September exposed intermittent delivery after
the initial successful pairs. One combined two-room Close produced partial
office movement and a lounge transport error. A later five-second-separated
room sequence completed successfully in HA but closed only four of five office
sections and two of four lounge panels. Its Open left two office sections
closed; individual Opens restored them. A five-second gap is therefore not a
demonstrated fix. The sunrise/sunset migration was initially held for that reason.

Bridge 0.10.0 owns automatic command repeats: at most two extra identical
bursts, two seconds after each successful completion, with a60-second expiry and no additional
rolling-code reservations. Each ESPHome device has an **RF automatic command
repeats** switch, default ON. New command attempts, observed conflicts,
reconfiguration, disable and radio faults cancel pending work. Boot never
replays an old command. An active burst is not interrupted, so a new action
while the radio is busy may fail and need to be sent again after it finishes.
The original HA response still covers only the first burst, not the outcome
of background repeats. There is no physical acknowledgement.

The manual diagnostic added in 0.9.1 remains available and shares the same
repeat budget. These covers send one command; they don't add a second retry
loop. See the [firmware policy](https://github.com/Herbertmt978/norman-rf-bridge/blob/main/docs/commissioning.md#automatic-command-repeats).

At the owner's request, the existing office/lounge sunrise/sunset automation
was subsequently migrated and enabled using the two local ESP room covers.
The sun triggers are unchanged. As verified on 13 October 2026, the lounge action follows 25 seconds after
the office action completes, with three later passes five minutes apart. There
is no Home Assistant startup trigger. The previous hub configuration is backed up.
Norman phone-app schedules are unchanged and must be disabled separately if
they conflict. This is an explicit choice to use the experimental controls,
not a whole-house reliability pass: the automatic-repeat trial reached 4/4
lounge panels closed but still missed one of five office sections. Both rooms
were reopened. The earlier downstairs transmitter fault remains unexplained.

The room-selection candidate passed230 tests in minimum, pinned and cached
latest HA harnesses, plus117 unit tests; combined coverage98%, config-flow100%,
strict typing, Ruff and Hassfest pass. These tests
substitute ESPHome services and do not establish native radio support on the
oldest HA release. An isolated DEV instance adopted both real bridges and
created exactly nine selected section covers and two room covers. All began
unknown, stayed unknown after reload, and the32 existing hub registry records
and settings were preserved. No movement command was sent during that DEV
setup check. Camera observation remains a separate qualification.

The earlier one-panel candidate passed isolated DEV checks of32 existing Norman
settings/entities, actual ESPHome adoption and unknown state after reload. The
multipanel candidate created14 section and3 room covers in real DEV. The owner
physically confirmed all five study sections using the sequential0.6 sender;
interleaved0.7 and0.7.1 physical trials failed; local0.9 trials later passed as above. These are
distinct results, not a claim of whole-house range or fresh
baseline preservation in every iteration. Production replacement and GitHub
publication are separate steps.

The bench firmware is not encrypted/authenticated. Keep it on a trusted local
network. Measured feedback, all-house range, prolonged
counter coexistence and customer-safe provisioning remain further work.
