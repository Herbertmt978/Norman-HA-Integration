# ESPHome RF bridge — experimental

This is an unreleased section-and-room transport for the separately built
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

1. Build firmware0.9 (native protocol3) and commission the ESP32/nRF24 bridge using its own guide. Observe the
   actual panel for every learned direction; do not copy another home's frames.
2. Adopt the board through HA's ESPHome integration. This transport requires
   native ESPHome actions with structured response support, as documented in
   [ESPHome's API guide](https://esphome.io/components/api/#action-responses).
   The ordinary integration's HA2024.11 minimum is not a claim that an old native
   ESPHome integration supports this newer response protocol. A missing response
   capability is rejected during setup.
3. Add **Norman**, choose **ESPHome RF bridge (experimental)** and select the
   detected bridge, then explicitly select its rooms. Each selected learned
   section and complete room becomes a separate cover. Assign a room to its
   tested local bridge; repeat for another bridge with different rooms.
   Closing uses that target's commissioned preferred direction.

No hub host, password or rolling counter is entered in this flow. The ESP is
the sole owner of learned frames and persistent RF sequence. The selected bridge
fingerprints, slots, room labels and endpoints prevent changed commissioning
silently controlling different sections through an old HA entry. The firmware
has32 slots; each interleaved room command accepts up to8 unique targets.
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

This workflow uses existing commissioning and reconfiguration actions; it does
not require a new firmware build, movement command or production hub upgrade.
Per-home physical mappings belong in installation data, never factory firmware.

Each cover offers `cover.open_cover` and `cover.close_cover`. It does not expose
arbitrary positioning or stop. The pilot's raw endpoints37/0/100 are firmware
command labels, not measured HA percentages. Reconfigure refreshes explicitly
changed commissioned bindings on the same bridge; it cannot switch bridges.

Room commands are one bounded batch: the ESP preflights every target, durably
reserves every rolling code, then interleaves packets. Each gets100 copies at55ms
round cadence. Snapshot tests confirmed final positions, not simultaneous
motor starts. This is not a guessed
room-wide packet or simultaneous transmissions from one radio. One shared lock
serializes room and section actions; a successful batch normally takes about6s.

Successful completion means the ESP finished its bounded RF burst, not that a
motor acknowledged movement. Position starts unknown, is explicitly assumed
after a successful command. Every selected member becomes uncertain after a
failed batch, even if some motors may have moved. State returns to unknown after
entry reload. Do not use it as physical confirmation for safety decisions.

Existing hub covers are independent and their assumed state may be stale after
RF movement. There is no automatic hub fallback or retry after an uncertain RF
send. Removing the RF entry leaves the underlying ESPHome device, Bluetooth
proxy and commissioned autonomous relay operating.

## Verification and limits

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
