# 2026-07-10 initial baseline

## Repository state

- Source branch: `Herb/v0.2.2-live-hub-and-room-ui`
- Exact commit: `44ce7bb`
- Release tag: `v0.2.2`
- Work branch: `Herb/v0.3.0-profiles-and-battery`
- Worktree was clean before the work branch was created.

## Verified checks

- Focused unit tests: 87 passed, plus 18 subtests.
- Home Assistant 2024.11.0 integration tests: 62 passed.
- Home Assistant 2026.7.1 integration tests: 62 passed.
- Ruff 0.15.21 check and format check: passed.
- Mypy 2.2.0 strict mode: passed for all integration modules.

The Windows Home Assistant pytest plugin imports the POSIX-only `fcntl` module,
so focused unit tests were run with plugin autoload disabled and
`pytest_asyncio.plugin` enabled explicitly. Both real Home Assistant suites ran
under cached Ubuntu/WSL environments, matching CI's Linux execution model.

## Verified hardware behavior

- The factory credential remains `123456789` and is part of the supported hub
  protocol; it must not be removed from repository defaults.
- A nearby test room's semantic `fullopen`/`fullclose` commands with action 2 moved all
  panels simultaneously. The user physically confirmed both directions.
- Exact numeric group-level commands work for those panels.
- The verified room uses raw open 37 and raw closed 100.
- A more remote test room accepts commands and reports target state but has not moved
  physically; the user identified the RF/repeater path as the likely cause.
  This is not treated as an integration command failure while the nearby room works.
- Window information already contains decimal-string battery percentages in the
  normal coordinator response.

## Compatibility boundary

This repository remains a HACS/custom-integration release track supporting Home
Assistant 2024.11 and current Home Assistant. Core preparation guidance lives in
`docs/core-submission.md`; a future Core pull request will use a published client
library and current Core APIs rather than copying HACS metadata unchanged.
