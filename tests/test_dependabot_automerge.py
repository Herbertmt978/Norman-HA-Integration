"""Execute the trusted Dependabot policy without GitHub credentials or writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/dependabot-automerge.yml"
WORKFLOW_TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
DEPENDABOT_TEXT = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")


def policy_script() -> str:
    """Extract the actual inline Bash policy from the trusted workflow YAML."""
    lines = WORKFLOW_TEXT.splitlines()
    policy_index = next(
        index for index, line in enumerate(lines) if line.strip() == "id: policy"
    )
    run_index = next(
        index
        for index in range(policy_index + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    script_lines = []
    for line in lines[run_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < 10:
            break
        script_lines.append(line)
    return textwrap.dedent("\n".join(script_lines))


class AutoMergePolicyTest(unittest.TestCase):
    """Exercise the workflow's shell gate with an inert fake `gh` command."""

    def run_policy(
        self, **changes: str
    ) -> tuple[subprocess.CompletedProcess[str], str | None]:
        """Supply metadata and fake GitHub responses; never call the real gh CLI."""
        environment = {
            **os.environ,
            "DEPENDENCY_GROUP": "",
            "DEPENDENCY_NAMES": "ruff",
            "DIRECTORY": "/",
            "MAINTAINER_CHANGES": "false",
            "NEW_VERSION": "0.15.22",
            "PACKAGE_ECOSYSTEM": "pip",
            "PR_URL": "https://github.invalid/pull/1",
            "REVIEW_DATA": '{"reviews": [], "reviewDecision": ""}',
            "UPDATE_TYPE": "version-update:semver-patch",
            "CHANGED_FILES": "requirements_test.txt",
            **changes,
        }
        bash = shutil.which("bash")
        if os.name == "nt":
            bash = r"C:\Program Files\Git\bin\bash.exe"
        if bash is None or not Path(bash).is_file():
            self.fail("Bash is required to verify the auto-merge policy")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            environment["GITHUB_OUTPUT"] = output.as_posix()
            result = subprocess.run(
                [
                    bash,
                    "-c",
                    "gh() {\n"
                    '  if [[ "$1 $2" == "pr diff" ]]; then\n'
                    '    printf "%s\\n" "$CHANGED_FILES"\n'
                    '  elif [[ "$1 $2" == "pr view" ]]; then\n'
                    '    [[ "$REVIEW_DATA" != "__ERROR__" ]] || return 91\n'
                    '    printf "%s\\n" "$REVIEW_DATA"\n'
                    "  else\n"
                    "    return 90\n"
                    "  fi\n"
                    "}\n" + policy_script(),
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            output_text = (
                output.read_text(encoding="utf-8") if output.exists() else None
            )
            return result, output_text

    def eligible(self, **changes: str) -> bool:
        """Return the real policy's eligibility result for fake metadata."""
        result, output = self.run_policy(**changes)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output is not None and output.strip() == "eligible=true"

    def test_review_api_failure_fails_closed(self) -> None:
        result, output = self.run_policy(REVIEW_DATA="__ERROR__")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("eligible=true", output or "")

    def test_potentially_truncated_review_history_fails_closed(self) -> None:
        result, output = self.run_policy(
            REVIEW_DATA=json.dumps({"reviews": [{}] * 100, "reviewDecision": ""})
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("eligible=true", output or "")

    def test_missing_aggregate_review_field_fails_closed(self) -> None:
        result, output = self.run_policy(REVIEW_DATA='{"reviews": []}')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("eligible=true", output or "")

    def test_latest_non_decision_review_does_not_clear_change_request(self) -> None:
        for state, submitted_at in (
            ("COMMENTED", "2026-09-21T10:00:00Z"),
            ("PENDING", None),
        ):
            with self.subTest(state=state):
                self.assertFalse(
                    self.eligible(
                        REVIEW_DATA=json.dumps(
                            {
                                "reviews": [
                                    {
                                        "author": {"login": "maintainer"},
                                        "state": "CHANGES_REQUESTED",
                                        "submittedAt": "2026-09-20T10:00:00Z",
                                    },
                                    {
                                        "author": {"login": "maintainer"},
                                        "state": state,
                                        "submittedAt": submitted_at,
                                    },
                                ],
                                "reviewDecision": "REVIEW_REQUIRED",
                            }
                        )
                    )
                )

    def test_malformed_review_payload_fails_closed(self) -> None:
        malformed_payloads = (
            {"reviews": None, "reviewDecision": None},
            {"reviews": ["not-an-object"], "reviewDecision": None},
            {
                "reviews": [
                    {
                        "author": None,
                        "state": "CHANGES_REQUESTED",
                        "submittedAt": "2026-09-20T10:00:00Z",
                    }
                ],
                "reviewDecision": None,
            },
            {
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "UNKNOWN",
                        "submittedAt": "2026-09-20T10:00:00Z",
                    }
                ],
                "reviewDecision": None,
            },
            {
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                        "submittedAt": "not-a-date",
                    }
                ],
                "reviewDecision": None,
            },
            {
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                        "submittedAt": "",
                    },
                    {
                        "author": {"login": "maintainer"},
                        "state": "APPROVED",
                        "submittedAt": "2026-09-21T10:00:00Z",
                    },
                ],
                "reviewDecision": "REVIEW_REQUIRED",
            },
            {
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                        "submittedAt": None,
                    }
                ],
                "reviewDecision": None,
            },
            {
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "COMMENTED",
                        "submittedAt": None,
                    }
                ],
                "reviewDecision": None,
            },
            {
                "reviews": [
                    {
                        "author": {"login": "maintainer"},
                        "state": "CHANGES_REQUESTED",
                    }
                ],
                "reviewDecision": None,
            },
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                result, output = self.run_policy(REVIEW_DATA=json.dumps(payload))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("eligible=true", output or "")

    def test_individual_stable_patch_and_minor_updates_are_eligible(self) -> None:
        for dependency in ("ruff", "pytest", "pytest-asyncio"):
            for update_type in (
                "version-update:semver-patch",
                "version-update:semver-minor",
            ):
                with self.subTest(dependency=dependency, update_type=update_type):
                    self.assertTrue(
                        self.eligible(
                            DEPENDENCY_NAMES=dependency,
                            UPDATE_TYPE=update_type,
                        )
                    )

    def test_sensitive_or_unmanaged_dependencies_stay_manual(self) -> None:
        for dependency in (
            "aiohttp",
            "coverage",
            "pytest-cov",
            "voluptuous",
            "pytest-homeassistant-custom-component",
            "mypy",
            "josepy",
            "pycares",
            "ruff,pytest",
            "",
        ):
            with self.subTest(dependency=dependency):
                self.assertFalse(self.eligible(DEPENDENCY_NAMES=dependency))
        self.assertFalse(self.eligible(PACKAGE_ECOSYSTEM="github_actions"))
        self.assertFalse(self.eligible(PACKAGE_ECOSYSTEM="uv"))
        self.assertFalse(self.eligible(DIRECTORY="/ha"))

    def test_groups_maintainer_changes_majors_and_prereleases_stay_manual(self) -> None:
        for changes in (
            {"DEPENDENCY_GROUP": "routine-test-tools"},
            {"MAINTAINER_CHANGES": "true"},
            {"MAINTAINER_CHANGES": ""},
            {"UPDATE_TYPE": "version-update:semver-major"},
            {"UPDATE_TYPE": ""},
            {"NEW_VERSION": "10.0.0rc1"},
            {"NEW_VERSION": "10.0.0-beta.1"},
            {"NEW_VERSION": "10.0.0.dev1"},
            {"NEW_VERSION": "10.0.0+local"},
            {"NEW_VERSION": ""},
            {"NEW_VERSION": "unknown"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.eligible(**changes))

    def test_only_the_test_requirements_file_can_change(self) -> None:
        for files in (
            "requirements_ha_minimum.txt",
            "requirements_ha_current.txt",
            "requirements_test.txt\nrequirements_ha_minimum.txt",
            "requirements_test.txt\ncustom_components/norman_gen1/manifest.json",
            "requirements_test.txt\n.github/workflows/tests.yml",
        ):
            with self.subTest(files=files):
                self.assertFalse(self.eligible(CHANGED_FILES=files))

    def test_latest_changes_requested_review_by_author_stays_manual(self) -> None:
        review = {
            "author": {"login": "maintainer"},
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-09-20T10:00:00Z",
        }
        self.assertFalse(
            self.eligible(
                REVIEW_DATA=json.dumps(
                    {"reviews": [review], "reviewDecision": "REVIEW_REQUIRED"}
                )
            )
        )

    def test_later_approval_by_same_reviewer_clears_older_request(self) -> None:
        self.assertTrue(
            self.eligible(
                REVIEW_DATA=json.dumps(
                    {
                        "reviews": [
                            {
                                "author": {"login": "maintainer"},
                                "state": "CHANGES_REQUESTED",
                                "submittedAt": "2026-09-20T10:00:00Z",
                            },
                            {
                                "author": {"login": "maintainer"},
                                "state": "APPROVED",
                                "submittedAt": "2026-09-21T10:00:00Z",
                            },
                        ],
                        "reviewDecision": "REVIEW_REQUIRED",
                    }
                )
            )
        )

    def test_approval_by_another_reviewer_does_not_clear_request(self) -> None:
        self.assertFalse(
            self.eligible(
                REVIEW_DATA=json.dumps(
                    {
                        "reviews": [
                            {
                                "author": {"login": "requester"},
                                "state": "CHANGES_REQUESTED",
                                "submittedAt": "2026-09-20T10:00:00Z",
                            },
                            {
                                "author": {"login": "approver"},
                                "state": "APPROVED",
                                "submittedAt": "2026-09-21T10:00:00Z",
                            },
                        ],
                        "reviewDecision": "REVIEW_REQUIRED",
                    }
                )
            )
        )

    def test_workflow_uses_trusted_metadata_and_server_side_gates(self) -> None:
        trigger = (
            WORKFLOW_TEXT.split("on:\n", maxsplit=1)[1]
            .split("\n# Read trusted", maxsplit=1)[0]
            .strip()
        )
        self.assertEqual(
            trigger,
            "pull_request_target:\n"
            "    types: [opened, reopened, synchronize, ready_for_review, converted_to_draft]",
        )

        guard_block = WORKFLOW_TEXT.split("  manage:\n", maxsplit=1)[1].split(
            "    runs-on:", maxsplit=1
        )[0]
        normalized_guard = " ".join(guard_block.replace("    if: >-", "", 1).split())
        self.assertEqual(
            normalized_guard,
            "github.repository == 'Herbertmt978/Norman-HA-Integration' && "
            "github.event.pull_request.user.login == 'dependabot[bot]' && "
            "github.event.pull_request.base.ref == github.event.repository.default_branch && "
            "github.event.pull_request.head.repo.full_name == github.repository && "
            "startsWith(github.event.pull_request.head.ref, 'dependabot/pip/')",
        )

        metadata_step = WORKFLOW_TEXT.split(
            "      - name: Read Dependabot metadata\n", maxsplit=1
        )[1].split("      - name: Check automatic merge policy\n", maxsplit=1)[0]
        metadata_condition = next(
            line.strip()
            for line in metadata_step.splitlines()
            if line.strip().startswith("if: ")
        )
        self.assertEqual(
            metadata_condition,
            "if: github.actor == 'dependabot[bot]' && "
            "github.event.pull_request.draft == false",
        )
        self.assertIn(
            "uses: dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98",
            metadata_step,
        )

        policy_step = WORKFLOW_TEXT.split(
            "      - name: Check automatic merge policy\n", maxsplit=1
        )[1].split(
            "      - name: Queue eligible update for automatic merge\n", maxsplit=1
        )[0]
        self.assertRegex(policy_step, r"(?m)^\s+GH_TOKEN: \$\{\{ github\.token \}\}$")
        for metadata in (
            "steps.metadata.outputs.dependency-group",
            "steps.metadata.outputs.maintainer-changes",
            "steps.metadata.outputs.update-type",
        ):
            with self.subTest(metadata=metadata):
                self.assertIn(metadata, policy_step)

        queue_step = WORKFLOW_TEXT.split(
            "      - name: Queue eligible update for automatic merge\n", maxsplit=1
        )[1]
        queue_command = next(
            line.strip()
            for line in queue_step.splitlines()
            if line.strip().startswith("run: ")
        )
        self.assertEqual(
            queue_command,
            'run: gh pr merge --auto --squash --match-head-commit "$PR_HEAD_SHA" "$PR_URL"',
        )
        self.assertIn("if: steps.policy.outputs.eligible == 'true'", queue_step)

        stale_merge_step = WORKFLOW_TEXT.split(
            "      - name: Clear stale automatic merge request\n", maxsplit=1
        )[1].split("      - name: Read Dependabot metadata\n", maxsplit=1)[0]
        self.assertIn(
            "if: github.event.action == 'synchronize' || "
            "github.event.action == 'converted_to_draft'",
            stale_merge_step,
        )
        self.assertIn('gh pr merge --disable-auto "$PR_URL"', stale_merge_step)
        self.assertIn(
            'gh pr view "$PR_URL" --json reviews,reviewDecision',
            policy_step,
        )
        self.assertIn('decision == "CHANGES_REQUESTED"', policy_step)
        self.assertIn('review["state"] == "CHANGES_REQUESTED"', policy_step)
        self.assertIn(
            'merge_relevant_states = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}',
            policy_step,
        )
        self.assertNotIn("actions/checkout", WORKFLOW_TEXT)
        self.assertNotIn("--admin", WORKFLOW_TEXT)
        self.assertRegex(
            WORKFLOW_TEXT,
            r"(?m)^\s+expected_files='requirements_test\.txt'$",
        )

    def test_dependabot_scope_is_monthly_and_allowlisted(self) -> None:
        self.assertEqual(DEPENDABOT_TEXT.count("package-ecosystem:"), 1)
        self.assertIn("package-ecosystem: pip", DEPENDABOT_TEXT)
        self.assertIn("directory: /", DEPENDABOT_TEXT)
        self.assertIn("interval: monthly", DEPENDABOT_TEXT)
        self.assertEqual(
            [
                line.strip().removeprefix("- dependency-name: ")
                for line in DEPENDABOT_TEXT.splitlines()
                if "- dependency-name:" in line
            ],
            ["ruff", "pytest", "pytest-asyncio"],
        )
        self.assertNotIn("groups:", DEPENDABOT_TEXT)


if __name__ == "__main__":
    unittest.main()
