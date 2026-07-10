# Configurable position profiles and battery sensors

## Goal

Release v0.3.0 as a polished HACS update that keeps native Home Assistant cover
controls, lets users configure raw Open and Closed positions per room or panel,
uses simultaneous room broadcasts when they preserve the configured targets,
and exposes physical motor batteries without any additional hub polling or
login sessions.

## Plan basis

The user approved defaults of raw Open 37 and raw Closed 100, retained the need
to reverse Closed to 0, rejected duplicate button entities, physically verified
simultaneous commands in a nearby room, and authorized a GitHub push and release but
not a merge or Home Assistant submission. Existing v0.2.2 effective profiles
must be migrated exactly so an upgrade cannot reverse a previously configured
room.

## Architecture

- Store global numeric defaults plus sparse room/panel overrides in config-entry
  options. Resolution order is panel, room, then global default.
- Accept integer Open positions from 0 through 100. Closed is an endpoint (0 or
  100). Reject equal Open and Closed values.
- Migrate the v0.2.2 boolean target lists after the first discovered hub snapshot
  into equivalent numeric profiles, then remove the legacy keys atomically.
- Keep the native `CoverEntity` API. Map Home Assistant 0..100 positions through
  each entity's effective raw profile.
- Use the hub's room semantic broadcast only when every affected panel's
  configured target matches the room's hub-native semantic target. Otherwise
  send exact numeric group commands in one authenticated coordinator command
  session, preserving correctness for mixed overrides.
- Parse battery percentages at the API boundary and expose one translated,
  diagnostic coordinator-backed sensor per physical window/motor. Sensors read
  the existing `getWindowInfo` snapshot and never perform their own update.
- Attach battery sensors to existing room devices so the approved hub-to-room UI
  remains intact.

## Technology and compatibility

- Python 3.12+ and typed asynchronous Home Assistant integration APIs.
- Home Assistant 2024.11 minimum and current Home Assistant compatibility lanes.
- Voluptuous selectors and backend translations for options/entity names.
- HACS release metadata plus HACS, Hassfest, Ruff, strict mypy, and pytest gates.

## Tasks

### 1. Canonical numeric profile model and migration

Files: `const.py`, a focused profile module, `__init__.py`, `diagnostics.py`,
`tests/`, and `ha_tests/test_init.py`.

1. Add failing tests for default resolution, panel/room precedence, validation,
   position mapping, idempotent legacy migration, and preservation of existing
   v0.2.2 effective profiles.
2. Implement the smallest canonical profile model and migration to pass them.
3. Remove runtime dependence on legacy boolean option lists while retaining only
   the migration compatibility boundary.

### 2. Multi-step translated options flow

Files: `config_flow.py`, `strings.json`, `translations/en.json`, and
`ha_tests/test_options_flow.py`.

1. Add failing flow tests for editing global defaults, choosing a room/panel,
   setting numeric overrides, inheriting/resetting an override, invalid equal
   endpoints, and missing discovery data.
2. Implement a compact menu-based flow with numeric selectors and translated
   descriptions. Do not add duplicate buttons or entities.

### 3. Native cover behavior and simultaneous room operation

Files: `entity.py`, `cover.py`, `api.py`, `tests/test_api.py`, and
`ha_tests/test_cover.py`.

1. Add failing tests for per-panel and per-room Open/Close mapping, arbitrary HA
   position mapping, mixed-profile state calculation, broadcast eligibility, and
   exact numeric fallback.
2. Implement semantic room broadcast when every effective target matches the
   hub-native profile; otherwise honor every override with grouped numeric
   commands inside the existing authenticated command transaction.
3. Keep all stable cover unique IDs and room-device associations unchanged.

### 4. Cached physical battery sensors

Files: `api.py`, `coordinator.py`, `const.py`, new `sensor.py`, translations,
`tests/test_api.py`, and new `ha_tests/test_sensor.py`.

1. Add failing parser tests for integer and decimal-string 0..100 values and for
   rejected bool, float, malformed, missing, and out-of-range values.
2. Add failing real-HA tests for sensor metadata, translated names, stable unique
   IDs, room-device association, cached updates, unknown versus unavailable,
   dynamic discovery, unload, duplicate prevention, and zero additional hub API
   or login calls.
3. Implement normalized battery data, a lookup in coordinator data, and dynamic
   `CoordinatorEntity` sensors with no `async_update` method.

### 5. Release-facing documentation and metadata

Files: `manifest.json`, `README.md`, `docs/core-submission.md`, metadata tests,
and any affected fixtures.

1. Bump the integration to v0.3.0 and document defaults, override precedence,
   migration behavior, batteries, polling behavior, and room-broadcast fallback.
2. Keep factory credential documentation/defaults intact.
3. Document the HACS/Core boundary and the deliberate existing-room battery
   topology for future Core review.

### 6. Verification and release

1. Run focused tests after every RED/GREEN cycle.
2. Run Ruff check/format, strict mypy, focused unit tests, both Home Assistant
   compatibility suites, combined branch coverage, and metadata/JSON validation.
3. Review the complete diff and verify no credentials or private live hub data
   entered tracked files.
4. Commit factually, push the release branch, and require branch CI to pass.
5. Create and push annotated tag `v0.3.0`, require tag CI to pass, then create a
   full GitHub Release with upgrade notes so HACS can install it.
6. Do not merge, open a Core submission, or change `main`.

## Risks and mitigations

- **Upgrade direction regression:** migrate exact effective v0.2.2 values and
  test idempotence before exposing the new flow.
- **Mixed overrides cannot be one broadcast:** fall back to exact group commands
  rather than silently ignoring settings.
- **Hub session pressure:** battery entities consume only coordinator snapshots;
  command fan-out stays inside one authenticated transaction.
- **Ambiguous physical motors:** key sensors by stable physical window ID and
  disambiguate translated display names when records share a panel name.
- **Core topology review:** document why batteries attach to room devices in the
  HACS release, and leave the future Core PR free to revise topology.

## Retirement

The v0.2.2 `tilt_open_targets`, `reversed_close_targets`, and `known_targets`
options are migration-only after v0.3.0. Remove that migration in a later major
release only after supported installations have had a documented upgrade path.
