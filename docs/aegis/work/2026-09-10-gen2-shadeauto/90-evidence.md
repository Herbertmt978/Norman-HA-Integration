# Verification evidence

Candidate: `Herb/gen2-shadeauto`, based on `fdee4a2` (v0.3.4).
Imported source: keito `f280ea21c2d9717b5e044f6a81a7c67d147dc48d`.
Initial qualification did not publish or change production. A subsequent user
request authorizes merging; the candidate is now prepared as 0.4.0. No release tag
or production installation is part of this merge.

| Check | Result |
| --- | --- |
| Local unit suite | 117 passed, 19 subtests passed |
| HA 2024.11.0 minimum | 152 passed |
| HA 2026.7.1 pinned lane | 152 passed |
| HA 2026.9.1 latest lane | 152 passed |
| Strict MyPy, pinned/latest HA | 19 source files passed |
| Ruff lint / format | Passed; 38 files formatted |
| HA-layer coverage excluding API/profile modules | Required 95% floor passed |
| Config-flow statement and branch coverage | 100% |
| Combined statement and branch coverage | 97%; required 95% floor passed |
| Hassfest | 1 integration, 0 invalid |
| DEV runtime | HA 2026.10.0.dev202609070228 |
| Installed source readback | All 25 component files match the candidate |
| Configuration and restart | `ha core check`, restart, authenticated HTTP/API passed |
| Existing Gen 1 runtime | All 32 entities available; entry data/options, entity unique IDs, entity IDs and device links unchanged |
| Gen 2 runtime fixture | Real local HTTP registration, discovery, status, two-rail control, nudge, reload and push updates passed |
| Runtime logs | No Norman errors or traceback; normal custom-integration warning only |

The HA lanes ran in isolated containers on HAOS-DEV using the existing test
runtimes. The Gen 2 runtime check used a loopback-only aiohttp hub fixture and the
real running HA config-flow and service APIs. Its entry was removed after each
run. Gen 1 control, room/profile behavior, batteries, options and migration remain
covered by the existing unit/HA suites. Physical Gen 2 hardware behavior has not
been verified; no undocumented stop operation is exposed.

Local commands: `ruff check .`, `ruff format --check .`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests -q`.
Linux HA commands follow `.github/workflows/tests.yml`, including strict MyPy,
HA tests and all existing coverage floors. No GitHub Actions jobs were triggered.

Cleanup: all seven task containers removed; DEV gracefully shut down, pre-change
snapshot restored, and original stopped state verified. BETA remained stopped. The private home operations record retains the rollback
anchor and local diagnostic artifact locations.

## Documentation and merge follow-up

The README covers both generations, with separate Gen 1 and ShadeAuto guides.
Historical release notes retain their original Gen 1 scope. The display name is
Norman, repository links use the current slug, and the bundled logo reuses the
existing text-free shutter icon so it has no obsolete Gen 1-only label.
A generated text-logo alternative was rejected because it did not preserve
transparency; the original icon artwork is used unchanged.

Final 0.4.0 merge qualification repeated the 117-unit/19-subtest suite, all three
152-test HA lanes, strict typing, coverage floors, Ruff and Hassfest. All 25
installed files matched the final candidate, all 32 Gen 1 entities remained
available with unchanged settings/identities, and ShadeAuto HTTP fixture controls,
reload and pushed updates passed. DEV was gracefully shut down, restored to
`pre-norman-merge-20260910` and verified stopped; BETA remained stopped.
