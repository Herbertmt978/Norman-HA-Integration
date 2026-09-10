# Norman Home Assistant integration

Control **Norman Gen 1** and **Gen 2 (ShadeAuto)** shutter and blind hubs over your
local network. Choose your hub generation during setup. Add each hub separately
if your home uses both generations.

Requires **Home Assistant 2024.11.0 or newer**. The integration does not require a
cloud account. This project is independent of Norman.

## Choose your generation

| | Gen 1 | Gen 2 (ShadeAuto) |
| --- | --- | --- |
| Setup | Hub address, password and app version | Hub address; no Gen 1 password |
| Local API | HTTP, normally port 80 | HTTP on fixed port 10123 |
| Controls | Room and panel/group covers, Open, Close and position | Peripheral covers, Open, Close, position and tilt/second rail |
| Position settings | Global, room and panel movement profiles | Upstream SmartDrape/two-rail mapping, 0–100 |
| Battery sensors | One diagnostic percentage sensor per reported motor | Not currently exposed |
| Updates | Polling every 60 seconds | Local notifications with status refreshes |
| Custom actions | None | Nudge position and nudge tilt |
| Guide | [Gen 1 operation and troubleshooting](docs/gen1.md) | [Gen 2 / ShadeAuto operation and troubleshooting](docs/gen2.md) |

## Installation

Version **0.4.0** adds support for both generations. Install the latest release
through HACS, or use the manual method below.

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=Norman-HA-Integration&category=integration)

1. In HACS, open **Custom repositories**.
2. Add [Herbertmt978/Norman-HA-Integration](https://github.com/Herbertmt978/Norman-HA-Integration) with category **Integration**.
3. Download **Norman**, then restart Home Assistant.
4. Open **Settings > Devices & services > Add integration** and search for **Norman**.
5. Choose **Gen 1** or **Gen 2 (ShadeAuto)**, then enter that hub's connection details.

For manual installation, download this repository and copy the complete
`custom_components/norman_gen1` directory into Home Assistant's `custom_components`
directory, then restart and follow steps 4–5.

### Gen 1 setup

Enter the local hub address, password and app version. The app version defaults
to `2.11.21`. The Norman Gen 1 factory password is:

```text
123456789
```

It is pre-filled; replace it if you changed the hub password. See the
[Gen 1 guide](docs/gen1.md) for discovery, room controls, movement profiles,
batteries and reauthentication.

### Gen 2 (ShadeAuto) setup

Select **Gen 2 (ShadeAuto)** and enter the hub's local IP address or hostname.
The integration uses port `10123` automatically; do not append a port. A root
`http://` URL is also accepted. Pair the blinds with the hub in the ShadeAuto app
first. No Gen 1 password, app-version string or movement profile is needed.

See the [ShadeAuto guide](docs/gen2.md) for rail behavior, nudge actions,
notifications and the current hardware-validation limits.

## Upgrading an existing Gen 1 installation

Existing entries continue to use Gen 1 automatically. You do not need to remove
or re-add them. Their entity IDs, device links, battery sensors and movement
settings are preserved. To add a ShadeAuto hub, add another **Norman** entry.

The internal integration domain and installation folder remain `norman_gen1`
for compatibility with existing installations. This identifier does **not** limit
the integration to Gen 1. Keep it unchanged when installing manually or using
the Gen 2 nudge actions.

## Automations

Both generations use Home Assistant's standard `cover` actions, such as
`cover.open_cover`, `cover.close_cover` and `cover.set_cover_position`.

```yaml
action: cover.set_cover_position
target:
  entity_id: cover.living_room_shutters
data:
  position: 35
```

ShadeAuto also supports `cover.set_cover_tilt_position`,
`norman_gen1.nudge_position` and `norman_gen1.nudge_tilt`.
These nudge actions apply to Gen 2 covers; examples are in the
[ShadeAuto guide](docs/gen2.md#actions).

## Connection changes and removal

Use **Reconfigure** to change an existing hub's connection settings. The
integration checks that the new address belongs to the same hub. Generation is
chosen when adding an entry; add a separate entry for a different-generation hub.

Remove an entry from **Settings > Devices & services** to unload its entities
and connections. Remove the HACS repository separately if no hubs use it.

## Network and hardware limits

Both backends use local HTTP. Keep the hubs on a trusted LAN or isolated IoT
network and do not expose their APIs to the internet. Gen 1 sends its hub password
over that local HTTP connection. ShadeAuto uses its local registration API.

Gen 1 has been checked against an existing physical hub. Gen 2 derives from
keito's SmartDrape/two-rail implementation and has passed automated protocol and
real Home Assistant runtime tests with an HTTP hub fixture. Gen 2 hardware
confidence comes from [keito's upstream SmartDrape testing](https://github.com/keito/home-assistant-norman/);
this combined implementation has not been separately tested on physical Gen 2 hardware.
Other device-specific mappings need verified device data. No unverified stop
command or direct RF/Bluetooth control is advertised.

## Development and verification

Unit tests cover protocol and Gen 1 behavior; `ha_tests` exercises both generations
through real Home Assistant test runtimes. CI requires at least 95% combined
coverage and 100% config-flow coverage, with minimum-version and pinned-version
lanes. A scheduled lane checks the latest HA test harness.

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
python -m pytest tests -q
```

For HA tests on Linux, install `requirements_ha_minimum.txt` or
`requirements_ha_current.txt` in a separate environment, then run
`python -m pytest ha_tests -q`. Full typing and coverage commands are in
[the test workflow](.github/workflows/tests.yml).

See the [verification record](docs/aegis/work/2026-09-10-gen2-shadeauto/90-evidence.md),
[changelog](docs/CHANGELOG.md) and [future Core submission checklist](docs/core-submission.md).

## Attribution and license

The Gen 2 backend is derived from
[keito/home-assistant-norman](https://github.com/keito/home-assistant-norman), with
changes for HA compatibility, connection cleanup, response validation,
notification parsing and control-state handling. Its Apache-2.0 license and
attribution are retained in [NOTICE](NOTICE) and
[licenses/keito-Apache-2.0.txt](licenses/keito-Apache-2.0.txt).
The remaining project code retains its [MIT license](LICENSE).
