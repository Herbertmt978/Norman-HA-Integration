# Completed checkpoint

Implementation complete on `Herb/gen2-shadeauto`, based on `fdee4a2`.
The protocol import, generation selection, compatibility corrections, regression
coverage, documentation and DEV runtime validation are complete.

Evidence: [verification](90-evidence.md). All required local checks passed.
DEV103 was restored to `pre-norman-gen2-20260910` and verified stopped; BETA104
remained stopped. Production, releases and GitHub Actions were untouched.

The user subsequently authorized final testing, documentation cleanup and merging
to main. Publication will use a protected-branch PR after local gates pass.
The documentation and display branding now cover both generations; 0.4.0 is
prepared without creating a release tag. Final verification passed using DEV snapshot
`pre-norman-merge-20260910`; the guest was restored and verified stopped.
The remaining qualification limit is physical Gen 2 hardware; the real HA runtime
was exercised against a loopback HTTP fixture. This does not prevent review of
the implemented combined integration.

No scope or compatibility drift. The legacy domain, IDs and options are preserved.
No legacy owner was retired; Gen 2 owns its distinct API inside the same component.
