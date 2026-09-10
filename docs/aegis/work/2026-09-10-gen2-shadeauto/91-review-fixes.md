# ShadeAuto reliability review fixes — 10 September 2026

The owner requested fixes for three reproduced defects in v0.4.0: consecutive
commands could restore an old rail position, notification connection timeouts
were treated as planned rotation, and malformed rail values crossed into controls.

Command target ownership now lives in the coordinator, alongside serialized status
reads and control requests. Entity methods no longer mutate fetched models or
resolve nudge starting positions independently. Accepted targets remain separate
from physical state until acknowledged or a bounded 30-second timeout expires.
The existing protocol-to-model boundary validates all four position fields.
The notification reader distinguishes its own lifetime expiry from transport timeouts.

Validation on HAOS-DEV103, using existing isolated test environments:

- 188 HA tests on each of HA 2024.11.0, 2026.7.1 and 2026.9.1.
- 117 unit tests plus 19 subtests; Ruff lint and format checks passed.
- Strict mypy passed for all 19 source files on pinned and latest environments.
- Coverage: 97% combined, 96% HA layer, 100% config flow.
- Regression tests cover delayed status, consecutive nudges, overlapping commands,
  rejected commands, acknowledgement, expiry, all four malformed rail fields,
  transport timeouts and scheduled rotation.

No physical movement or production deployment was performed. Gen 1 source is
unchanged. DEV began stopped and was snapshotted before testing; lifecycle completion
is recorded in the private home architecture map. Gen 2 hardware provenance remains
upstream SmartDrape testing; these corrections have automated HA validation.
