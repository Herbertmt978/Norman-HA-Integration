# Baseline governance

A baseline is an immutable record of the repository state before a substantial
change. Create a new dated baseline instead of editing an old one when the
release branch, supported Home Assistant versions, test harness, or public
behavior changes materially.

Every replacement baseline must include:

1. The branch and exact commit.
2. The commands and results for unit tests, supported Home Assistant tests,
   linting, formatting, and strict typing.
3. Known environment limitations and the verified workaround.
4. The user-observed hardware behavior on which protocol decisions depend.

Do not describe an unverified assumption as baseline behavior. Live hardware
commands require the user's explicit permission and physical observation.
