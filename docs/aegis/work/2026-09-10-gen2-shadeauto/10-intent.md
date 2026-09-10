# Combined Norman generations

Add a Gen 1 / Gen 2 (ShadeAuto) setup choice to the current Norman integration.
Use keito's local API implementation, correcting current HA incompatibilities.
Preserve existing Gen 1 identities, options, protocol and controls.

Baseline: main fdee4a2 (v0.3.4); upstream keito f280ea21c2d9717b5e044f6a81a7c67d147dc48d;
the existing config flow, runtime, platforms, HA tests, CI and home operations map
have been inspected. No missing baseline. Retain the norman_gen1 domain for
installed users; generation defaults to Gen 1 when absent. Gen 2 owns a separate
backend within the same distributed component. No Gen 1 protocol retirement.

Work: import attributed Gen 2 code; route setup/runtime; fix transport, lifecycle
and command-state failures; add regression tests and docs; validate locally and
on HAOS-DEV; restore dev resources. No production deployment or publication.

Acceptance: existing Gen 1 tests pass; setup selects the correct backend;
Gen 2 registration/discovery/status/control/notification paths tested;
current HA import, check, restart and log gates pass. Report separately any
hardware-only validation unavailable without a Gen 2 hub.

Stop states: done, blocked by an external dependency, needs verification, or
scope exceeded. No additional approval gates within the authorized work.

Follow-up authorization: the user requested final testing, a documentation and
branding review for both generations, and merging to main. Use the required PR
checks, preserve legacy identities and retain the physical Gen 2 qualification
limit. A release tag or production deployment remains outside this follow-up.
