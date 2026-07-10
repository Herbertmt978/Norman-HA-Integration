# Home Assistant Core submission checklist

This repository is the HACS/custom-integration release track. A Core pull request should be prepared from current `home-assistant/core` development rather than copying HACS-only metadata unchanged.

## Before opening the Core pull request

- Confirm the permanent integration domain (`norman_gen1` or another maintainer-approved name) before creating brands and documentation entries.
- Confirm with maintainers that the continuing Gen 1 user base and locally reachable hardware meet Core's integration eligibility expectations.
- Extract `api.py` into a small typed, asynchronous Python package with its own parser, transport, session, identity, and command tests.
- Publish the package to PyPI with GitHub trusted publishing/OIDC and a provenance attestation. Do not use a static PyPI token or an unguarded manual release workflow.
- Pin the released package in the Core manifest and include its logger.
- Keep the initial submission focused on the `cover` platform and only the movement options required for safe control. The diagnostic battery sensor is isolated in its own platform so it can be included when reviewers accept the physical-window model or deferred to a follow-up. Be prepared to defer diagnostics, reauthentication, reconfiguration, and dynamic discovery if reviewers request a smaller Bronze first pull request.

## Core-specific integration files

- Remove HACS-only `version` and `issue_tracker` manifest fields.
- Point `documentation` at the Home Assistant documentation page.
- Use `ConfigEntry[Coordinator]`, `entry.runtime_data`, `config_entry=entry`, injected Home Assistant sessions, and current callback/result types.
- Replace the custom integration's Home Assistant 2024.11 options-entry compatibility lookup with the current `self.config_entry` property.
- Generate `quality_scale.yaml` against current Core immediately before submission. Include every rule required by the current schema, including rules that are exempt; do not copy stale rule lists.
- Mark dynamic-device and stale-device rules honestly. This integration adds newly discovered entities but intentionally leaves missing entities unavailable rather than removing registry devices.
- Add or update generated Core metadata only through Hassfest.

## Test expectations transferred from ScorpionTrack review

- Mock the public client/library boundary, not internal validation or coordinator methods.
- Start user flows without invented initial data, submit with `async_configure`, and assert the created unique ID.
- Test error recovery to `CREATE_ENTRY` on the same flow.
- Use the standard `mock_config_entry` fixture.
- Exercise coordinator behavior through entities and natural time advancement rather than constructing coordinators or mutating runtime data directly.
- Do not construct entities directly in Core tests.
- Use `snapshot_platform` for the platform-state baseline and normal registry fixtures for device assertions.
- Keep setup/unload behavior in `test_init.py`; keep platform behavior in `test_cover.py`.
- Cover reauthentication, reconfiguration, options, diagnostics, connection/auth failures, unavailable/recovery behavior, dynamic discovery, command errors, and identity changes.
- If batteries are included, test normalized 0–100 values, unknown versus unavailable, dynamic physical-window discovery, translated names, stable IDs, and proof that sensors add no client calls.
- Maintain more than 95% integration coverage and 100% branch coverage for config, reauth, reconfigure, and options flows.

## Battery topology decision

The HACS release creates one sensor per physical `getWindowInfo` record but attaches those sensors to the existing room device. Battery display names use the same room/level join as their commandable group cover; multiple physical motors behind one level receive translated motor numbers in the normalized hub slot order, with physical window ID as the fallback and entity identity. A change to a known motor's correlated label or motor number schedules one config-entry reload rather than mutating Home Assistant's private entity-name caches. This preserves the user-facing hub → room → controls layout while group covers remain the commandable units. Revisit this explicitly with Core reviewers: separate physical child devices may be more literal, but would fragment covers from their motor diagnostics and add one device per shutter. The sensor platform consumes the cover coordinator snapshot and must never introduce a second update coordinator or polling interval.

## Command response decision

The Gen 1 `RemoteControl` endpoint is fire-and-forget and does not reliably include an acknowledgement field even when the shutters move. The client therefore treats a valid HTTP 200 mapping with no explicit `errorCode` as accepted. It still rejects transport, authentication, session, malformed-payload, unexpected-hub, malformed `errorCode`, and non-zero `errorCode` failures. Core-facing tests must preserve that boundary and must not claim that an accepted hub request proves RF delivery or physical motor movement.

## Companion pull requests and validation

- Prepare Home Assistant documentation and brands pull requests and link them from the Core PR.
- Use the standard Core pull-request template and keep the first review scope small.
- Run Ruff format/check, Hassfest, mypy, pylint, the requirements generators/checks, and the full component test directory in a clean Linux Core environment.
- Regenerate and inspect derived files after rebasing onto current `dev`.
- Avoid force-pushing or renaming the branch once maintainer review has begun.
