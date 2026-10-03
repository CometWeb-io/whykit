from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whykit.lint import find_vault_root, is_vault_root, resolve_root  # noqa: E402
from _vaults import fresh_vault  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)


class InitTests(unittest.TestCase):
    def test_init_defaults_to_vendor_neutral_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            init = run("init", str(target))
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue((target / "00-context" / "evidence-register.md").is_file())
            self.assertTrue((target / "06-decisions" / "decision-log.md").is_file())
            self.assertTrue((target / "notes" / "README.md").is_file())
            self.assertFalse((target / "01-strategy").exists())
            self.assertFalse((target / "07-research").exists())
            lint = run("lint", "--json", cwd=target)
            self.assertEqual(json.loads(lint.stdout)["errors"], 0, lint.stdout)

    def test_full_flag_keeps_the_workstream_rich_template_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            init = run("init", "--full", str(target))
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue((target / "01-strategy" / "README.md").is_file())
            self.assertTrue((target / "07-research" / "README.md").is_file())
            self.assertFalse((target / "notes" / "README.md").exists())
            lint = run("lint", "--json", cwd=target)
            self.assertEqual(json.loads(lint.stdout)["errors"], 0, lint.stdout)

    def test_conflicting_layout_flags_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            result = run("init", "--minimal", "--full", str(target))
            self.assertEqual(result.returncode, 2)
            self.assertFalse(target.exists())

    def test_init_produces_a_vault_that_lints_without_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            init = run("init", str(target))
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue(is_vault_root(target))

            lint = run("lint", cwd=target)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
            self.assertIn("0 error(s)", lint.stdout)

    def test_a_fresh_vault_warns_only_about_the_unanswered_agent_contract(self) -> None:
        """Adopters owe the vault four answers. Nothing else should be outstanding."""
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            run("init", str(target))
            report = json.loads(run("lint", "--json", cwd=target).stdout)
            codes = {f["code"] for f in report["findings"]}
            self.assertEqual(report["errors"], 0, report["findings"])
            self.assertEqual(codes, {"agents.unconfigured"}, codes)
            self.assertEqual(report["warnings"], 4, report["findings"])

    def test_init_does_not_ship_development_assets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            run("init", str(target))
            for unwanted in ("apps", "tests", "src", "pyproject.toml"):
                self.assertFalse((target / unwanted).exists(), unwanted)
            for wanted in ("Home.md", "AGENTS.md", "README.md", ".gitignore", "06-decisions"):
                self.assertTrue((target / wanted).exists(), wanted)
            # The vault is the adopter's own content; shipping our licence into it
            # would read as licensing their strategy under Apache-2.0.
            self.assertFalse((target / "LICENSE").exists())

    def test_init_refuses_a_non_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            target.mkdir()
            (target / "keepme.md").write_text("mine", encoding="utf-8")
            self.assertEqual(run("init", str(target)).returncode, 2)
            self.assertEqual(run("init", str(target), "--force").returncode, 0)
            self.assertTrue((target / "keepme.md").exists(), "--force must not wipe the destination")


class RootResolutionTests(unittest.TestCase):
    """The first-run footgun: a vault path given positionally was ignored as a root."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.vault = self.base / "knowledge"
        fresh_vault(self.vault)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_positional_vault_directory_becomes_the_root(self) -> None:
        root, paths, note = resolve_root(None, ["knowledge"], cwd=self.base)
        self.assertEqual(root, self.vault.resolve())
        self.assertEqual(paths, [])
        self.assertIn("knowledge", note or "")

    def test_linting_another_vault_by_path_does_not_invent_errors(self) -> None:
        result = run("lint", "knowledge", cwd=self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 error(s)", result.stdout)

    def test_the_root_is_found_from_a_subdirectory(self) -> None:
        deep = self.vault / "07-research" / "competitors"
        deep.mkdir(parents=True, exist_ok=True)
        self.assertEqual(find_vault_root(deep), self.vault.resolve())
        self.assertEqual(run("lint", cwd=deep).returncode, 0)

    def test_explicit_root_still_wins(self) -> None:
        root, paths, _ = resolve_root(str(self.vault), ["knowledge"], cwd=self.base)
        self.assertEqual(root, self.vault.resolve())
        self.assertEqual(paths, ["knowledge"])

    def test_a_directory_that_is_not_a_vault_is_rejected_clearly(self) -> None:
        result = run("lint", "--root", str(self.base))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a WhyKit vault", result.stderr)
        self.assertIn("hint:", result.stderr)


class AdoptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.vault = self.base / "knowledge"
        fresh_vault(self.vault, "--full")
        self.legacy = self.base / "old-docs"
        (self.legacy / "adr").mkdir(parents=True)
        (self.legacy / "adr" / "0001-use-postgres.md").write_text(
            "# Use Postgres\n\n## Status\n\nAccepted\n\n## Context\n\n"
            + "We needed a database that the team already knew how to operate. " * 3
            + "\n\n## Decision\n\nPostgres.\n", encoding="utf-8"
        )
        (self.legacy / "notes.md").write_text("# Notes\n\n" + "Something substantive was written here. " * 8, encoding="utf-8")
        (self.legacy / "copy.md").write_text("# Notes\n\n" + "Something substantive was written here. " * 8, encoding="utf-8")
        (self.legacy / "stub.md").write_text("# Just a heading\n", encoding="utf-8")
        (self.legacy / "empty.md").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_classifies_without_writing(self) -> None:
        result = run("adopt", str(self.legacy), "--into", str(self.vault))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dry run", result.stdout)
        self.assertIn("decision?", result.stdout)
        self.assertFalse((self.vault / ".import-staging").exists())

    def test_duplicates_and_empties_are_identified(self) -> None:
        from whykit.adopt import scan

        by_name = {c.relative: c for c in scan(self.legacy)}
        self.assertEqual(by_name["empty.md"].assessment, "empty")
        self.assertEqual(by_name["stub.md"].assessment, "heading-only")
        # Identical bytes: whichever is seen first in sorted order is treated as
        # the original. Which one that is does not matter; that exactly one is
        # flagged, and that it names its twin, does.
        pair = [by_name["copy.md"], by_name["notes.md"]]
        duplicates = [c for c in pair if c.assessment == "duplicate"]
        self.assertEqual(len(duplicates), 1, [c.assessment for c in pair])
        self.assertIn(duplicates[0].duplicate_of, {"copy.md", "notes.md"})
        self.assertNotEqual(duplicates[0].duplicate_of, duplicates[0].relative)
        self.assertTrue(by_name["adr/0001-use-postgres.md"].looks_like_decision)
        self.assertFalse(by_name["notes.md"].looks_like_decision)

    def test_write_stages_files_and_leaves_workstreams_untouched(self) -> None:
        result = run("adopt", str(self.legacy), "--into", str(self.vault), "--write", "--owner", "Test owner")
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = list((self.vault / ".import-staging").rglob("*.md"))
        self.assertTrue(staged)
        self.assertFalse(any("empty.md" == p.name for p in staged), "empty files are not staged")

        records = list((self.vault / "07-research" / "sources").glob("ingestion-*.md"))
        self.assertTrue(records)
        body = records[0].read_text(encoding="utf-8")
        self.assertIn("SHA-256", body)
        self.assertIn("adr/0001-use-postgres.md", body)

        # Staging must never make the vault fail its own checks.
        self.assertEqual(run("lint", cwd=self.vault).returncode, 0)

    def test_repeated_write_keeps_each_batch_and_receipt_intact(self) -> None:
        first = run("adopt", str(self.legacy), "--into", str(self.vault), "--write", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_payload = json.loads(first.stdout)
        first_migration = self.vault / first_payload["migration"]
        first_staged = first_migration.parent / "adr" / "0001-use-postgres.md"
        first_bytes = first_staged.read_bytes()
        first_record = (self.vault / first_payload["ingestion_record"]).read_bytes()

        source_file = self.legacy / "adr" / "0001-use-postgres.md"
        source_file.write_text(source_file.read_text(encoding="utf-8") + "\nNew evidence arrived.\n", encoding="utf-8")
        second = run("adopt", str(self.legacy), "--into", str(self.vault), "--write", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        second_payload = json.loads(second.stdout)
        second_migration = self.vault / second_payload["migration"]
        second_staged = second_migration.parent / "adr" / "0001-use-postgres.md"

        self.assertNotEqual(first_migration.parent, second_migration.parent)
        self.assertEqual(first_staged.read_bytes(), first_bytes)
        self.assertEqual((self.vault / first_payload["ingestion_record"]).read_bytes(), first_record)
        self.assertEqual(second_staged.read_bytes(), source_file.read_bytes())

    def test_source_migration_note_is_not_replaced_by_generated_report(self) -> None:
        source_note = self.legacy / "MIGRATION.md"
        source_note.write_text(
            "# Historical migration\n\n" + "This document records the original migration decisions and their context. " * 4,
            encoding="utf-8",
        )
        result = run("adopt", str(self.legacy), "--into", str(self.vault), "--write", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        generated_report = self.vault / payload["migration"]
        staged_source = generated_report.parent / "MIGRATION.md"

        self.assertNotEqual(generated_report, staged_source)
        self.assertEqual(staged_source.read_bytes(), source_note.read_bytes())
        self.assertIn("## Bring into the vault", generated_report.read_text(encoding="utf-8"))

    def test_adr_only_write_stages_decisions_not_unsupported_or_stubs(self) -> None:
        result = run(
            "adopt", str(self.legacy), "--into", str(self.vault),
            "--profile", "adr-only", "--write", "--owner", "Test owner", "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["score"]["useful"], 1)
        staging = self.vault / ".import-staging"
        self.assertEqual(
            [path.name for path in staging.rglob("*.md") if path.name != "MIGRATION.md"],
            ["0001-use-postgres.md"],
        )
        record = self.vault / payload["ingestion_record"]
        self.assertIn("unsupported", record.read_text(encoding="utf-8"))

    def test_adopting_the_vault_into_itself_is_refused(self) -> None:
        result = run("adopt", str(self.vault), "--into", str(self.vault))
        self.assertEqual(result.returncode, 2)

    def test_adopting_a_parent_that_contains_the_vault_is_refused(self) -> None:
        result = run("adopt", str(self.base), "--into", str(self.vault))
        self.assertEqual(result.returncode, 2)
        self.assertIn("overlapping", result.stderr)

    def test_scan_does_not_follow_markdown_symlink_outside_source(self) -> None:
        from whykit.adopt import scan

        outside = self.base / "outside-secret.md"
        outside.write_text("# Secret\n\n" + "outside material " * 20, encoding="utf-8")
        link = self.legacy / "linked-secret.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        self.assertNotIn("linked-secret.md", {c.relative for c in scan(self.legacy)})

    def test_adopt_refuses_if_source_changes_after_inventory(self) -> None:
        from unittest.mock import patch
        from whykit.adopt import adopt, scan

        candidates = scan(self.legacy)
        target = next(c for c in candidates if c.assessment == "useful" and not c.looks_like_decision)
        target.path.write_text(target.path.read_text(encoding="utf-8") + "\nchanged after scan\n", encoding="utf-8")
        with patch("whykit.adopt.scan", return_value=candidates):
            with self.assertRaisesRegex(RuntimeError, "source changed during adoption"):
                adopt(self.legacy, self.vault, write=True, owner="Test owner")


class ContractTests(unittest.TestCase):
    def test_sdist_excludes_checkout_only_test_suite(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        included = project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]

        self.assertNotIn("tests", included)

        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        package_job = workflow.split("  package:", 1)[1].split(
            "  windows-portability:", 1
        )[0]
        # scripts/check_dist.py rejects tests/ and anything outside the sdist
        # allowlist; tests/test_packaging.py covers those rules.
        self.assertIn("python3 scripts/check_dist.py dist", package_job)

    def test_readme_uses_the_canonical_product_positioning(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Git-native evidence and decision ledger", readme)
        self.assertNotIn("Obsidian vault + CI ledger", readme)
        self.assertNotIn("# ![CI]", readme)
        self.assertIn("https://docs.astral.sh/uv/getting-started/installation/", readme)
        self.assertIn("init ../my-ledger", readme)
        self.assertIn("lint --root ../my-ledger", readme)
        self.assertIn("adopt ../old-docs --into ../my-ledger --profile obsidian-loose", readme)
        self.assertIn("adopt ../old-docs --into ../my-ledger --profile generic --write", readme)
        self.assertIn(
            "[Code of Conduct](CODE_OF_CONDUCT.md)",
            readme,
        )

    def test_readme_adopt_flow_targets_the_new_vault_from_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            checkout = base / "whykit"
            checkout.mkdir()
            vault = base / "my-ledger"
            source = base / "old-docs"
            source.mkdir()
            (source / "notes.md").write_text(
                "# Interview notes\n\n" + "Substantive interview details. " * 8,
                encoding="utf-8",
            )
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)

            dry_run = run(
                "adopt", "../old-docs", "--into", "../my-ledger",
                "--profile", "obsidian-loose", "--json", cwd=checkout,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertFalse((vault / ".import-staging").exists())

            staged = run(
                "adopt", "../old-docs", "--into", "../my-ledger",
                "--profile", "generic", "--write", cwd=checkout,
            )
            self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
            self.assertTrue(list((vault / ".import-staging").rglob("*.md")))

    def test_guide_checkout_commands_match_the_real_repository_layout(self) -> None:
        guide = (ROOT / "docs" / "guide.md").read_text(encoding="utf-8")
        self.assertIn("uv sync --locked", guide)
        self.assertRegex(guide, r"git clone[^\n]+\ncd whykit\nuv sync --locked")
        self.assertIn("uv run whykit init ../my-ledger", guide)
        self.assertIn("uv run whykit lint --root ../my-ledger", guide)
        self.assertNotIn("uv tool install --editable .", guide)
        self.assertIn("not on PyPI yet", guide)
        self.assertIn("do not run", guide.casefold())
        self.assertIn("--workstream notes", guide)
        with tempfile.TemporaryDirectory() as td:
            minimal_vault = Path(td) / "minimal"
            init = run("init", "--minimal", str(minimal_vault))
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue((minimal_vault / "notes").is_dir())
            self.assertFalse((minimal_vault / "07-research").exists())
            self.assertIn(f"uv run whykit lint --root {minimal_vault}", init.stdout)
            self.assertNotIn("&& whykit lint", init.stdout)
        self.assertNotIn("whykit/scripts/whykit.py", guide)
        self.assertNotIn("warning:32", guide)
        self.assertNotIn("warning:56", guide)
        self.assertNotIn("warning:97", guide)
        self.assertIn(
            "The exact warning count and line numbers can change",
            " ".join(guide.split()),
        )
        self.assertNotIn("[![CI]", guide)
        self.assertIn("Obsidian is an optional editor", guide)

    def test_unreleased_package_is_not_recommended_from_public_package_index(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        scripts_readme = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        shim = (ROOT / "scripts" / "whykit.py").read_text(encoding="utf-8")
        self.assertIn("no public PyPI release", readme)
        self.assertIn("not published to PyPI yet", scripts_readme)
        self.assertIn("Do not run `uv tool install whykit`", scripts_readme)
        self.assertIn("current end-user installation path", shim)
        self.assertNotIn("pipx install whykit is the supported route", shim)

    def test_public_docs_do_not_claim_repository_access_is_required(self) -> None:
        public_docs = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in ("README.md", "docs/guide.md")
        ).casefold()
        self.assertNotIn("private preview", public_docs)
        self.assertNotIn("access is currently required", public_docs)

    def test_public_release_checklist_is_operational_without_private_status_snapshots(self) -> None:
        checklist = (ROOT / ".github" / "RELEASE.md").read_text(encoding="utf-8")
        normalized_checklist = " ".join(checklist.split())
        for requirement in (
            "every branch and tag",
            "pull-request refs",
            "author and committer identities",
            "deleted or renamed files in commit history",
            "file removed from the current tree remains available in older commits",
            "coordinate affected clones and refs",
            "CI rejects AI-tool identities and attribution trailers",
            "Actions runs, logs and artifacts",
            "Gitleaks checks for credential patterns",
            "reserved domains",
            "private vulnerability reporting",
            "exact `main` commit",
            "explicit decision and coordination plan",
            "configure a `main` ruleset",
            "optional Explorer check non-blocking",
            "private-preview or access-required notice",
            "Python 3.11–3.14",
            "exact tag",
            "independent reviewer",
            "does not reserve the project name",
            "real, private vault",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, normalized_checklist)

        # Release guidance is public and evergreen; transient private-repo
        # review findings belong in the maintainer's private launch record.
        private_snapshot_patterns = (
            r"\bAs of \d{4}-\d{2}-\d{2}\b",
            r"\b[0-9a-f]{7,40}\b",
            r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
            r"(?:retained )?(?:Actions logs|repetitions)\b",
            r"\bprotected:\s*(?:true|false)\b",
        )
        for pattern in private_snapshot_patterns:
            with self.subTest(private_snapshot_pattern=pattern):
                self.assertNotRegex(checklist, pattern)

    def test_public_changelog_omits_internal_release_naming_notes(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for internal_note in (
            "internal working name",
            "internal hardening release",
            "private-preview hardening release candidate",
        ):
            with self.subTest(internal_note=internal_note):
                self.assertNotIn(internal_note.casefold(), changelog.casefold())

    def test_ci_exercises_windows_mutation_lock_implementation(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("windows-portability:", workflow)
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn("Windows mutation-lock implementation", workflow)
        self.assertIn("uv run python -m unittest discover -s tests -v", workflow)

    def test_workflow_actions_and_uv_are_pinned_to_immutable_versions(self) -> None:
        workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertTrue(workflows)
        for workflow_path in workflows:
            lines = workflow_path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                uses = re.search(r"^\s+- uses: ([^\s#]+)(?:\s+#.*)?$", line)
                if uses:
                    reference = uses.group(1).rsplit("@", 1)[-1]
                    with self.subTest(workflow=workflow_path.name, action=uses.group(1)):
                        if reference == "./":
                            self.assertTrue((ROOT / "action.yml").is_file())
                        else:
                            self.assertRegex(reference, r"^[0-9a-f]{40}$")
                if "uses: astral-sh/setup-uv@" in line:
                    config = "\n".join(lines[index + 1 : index + 6])
                    with self.subTest(workflow=workflow_path.name, action="setup-uv"):
                        self.assertIn('version: "0.12.17"', config)

    def test_ci_cancels_superseded_refs_and_bounds_each_runner(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertEqual(workflow.count("runs-on:"), workflow.count("timeout-minutes:"))
        self.assertIn("timeout-minutes: 30", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertIn("timeout-minutes: 10", workflow)

    def test_ci_exercises_the_composite_action_with_an_explicit_python_runtime(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0",
            workflow,
        )
        self.assertIn("name: Profile mode on the Northline example", workflow)
        self.assertIn("name: Legacy mode on the tiny example", workflow)
        self.assertIn("uses: ./", workflow)
        self.assertIn("profile: ci", workflow)
        self.assertIn('history: "false"', workflow)
        # Example review dates are fixed, so the Action run pins its as-of day.
        self.assertIn('today: "2026-09-17"', workflow)

    def test_release_reuses_ci_and_publishes_only_its_own_checked_build(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_call:", ci)
        self.assertIn("uses: ./.github/workflows/ci.yml", release)
        self.assertIn("needs: quality", release)
        self.assertIn("bash scripts/release-baseline.sh", ci)
        self.assertIn("environment: pypi", release)
        self.assertIn("id-token: write", release)
        self.assertIn("uv build", release)
        self.assertIn("pypa/gh-action-pypi-publish@", release)
        # The build runs without the OIDC token; the publish job holds it and
        # only downloads the archives that job checked, from this same run.
        build, publish = release.split("\n  build:\n", 1)[1].split("\n  publish:\n", 1)
        self.assertIn("uv build", build)
        self.assertNotIn("id-token", build)
        self.assertNotIn("uv build", publish)
        self.assertIn("needs: build", publish)
        self.assertIn("name: release-distributions", publish)
        for cross_run_input in ("run-id:", "github-token:", "repository:", "pattern:"):
            self.assertNotIn(cross_run_input, publish)
        self.assertNotIn("download-artifact", build)

    @unittest.skipIf(os.name == "nt", "release Bash helper runs only in Linux CI")
    def test_release_baseline_uses_previous_release_or_fails_closed(self) -> None:
        script = ROOT / "scripts" / "release-baseline.sh"

        def git(vault: Path, *args: str) -> str:
            result = subprocess.run(
                ["git", *args], cwd=vault, text=True, encoding="utf-8", errors="replace", capture_output=True, check=True
            )
            return result.stdout.strip()

        def baseline(
            vault: Path,
            *,
            event: str,
            ref: str,
            sha: str,
            pr_base: str = "",
        ) -> subprocess.CompletedProcess[str]:
            env = os.environ.copy()
            env.update(
                EVENT_NAME=event,
                GITHUB_REF=ref,
                GITHUB_SHA=sha,
                PR_BASE_SHA=pr_base,
            )
            return subprocess.run(
                ["bash", str(script)], cwd=vault, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True
            )

        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            git(vault, "init", "-q")
            git(vault, "config", "user.name", "WhyKit Test")
            git(vault, "config", "user.email", "whykit-test@example.invalid")
            (vault / "record.md").write_text("first\n", encoding="utf-8")
            git(vault, "add", "record.md")
            git(vault, "commit", "-q", "-m", "initial")
            root = git(vault, "rev-parse", "HEAD")

            (vault / "record.md").write_text("second\n", encoding="utf-8")
            git(vault, "commit", "-qam", "second")
            head = git(vault, "rev-parse", "HEAD")

            first_release = baseline(
                vault, event="push", ref="refs/tags/v0.2.0", sha=head
            )
            self.assertEqual(first_release.returncode, 0, first_release.stderr)
            self.assertEqual(first_release.stdout.strip(), root)

            git(vault, "tag", "v0.1.0", root)
            previous_release = baseline(
                vault, event="push", ref="refs/tags/v0.2.0", sha=head
            )
            self.assertEqual(previous_release.returncode, 0, previous_release.stderr)
            self.assertEqual(previous_release.stdout.strip(), "v0.1.0")

            pull_request = baseline(
                vault,
                event="pull_request",
                ref="refs/pull/1/merge",
                sha=head,
                pr_base=root,
            )
            self.assertEqual(pull_request.returncode, 0, pull_request.stderr)
            self.assertEqual(pull_request.stdout.strip(), root)

            root_release = baseline(
                vault, event="push", ref="refs/tags/v0.1.0", sha=root
            )
            self.assertEqual(root_release.returncode, 2)
            self.assertIn("Unable to determine a real release baseline", root_release.stderr)

            branch = git(vault, "branch", "--show-current")
            root_push = baseline(
                vault, event="push", ref=f"refs/heads/{branch}", sha=root
            )
            self.assertEqual(root_push.returncode, 2)
            self.assertEqual(root_push.stdout.strip(), "")
            self.assertIn("A parent commit is required", root_push.stderr)

    def test_secret_scan_keeps_redaction_without_logging_commit_identity(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        secret_scan = workflow.split("  secret-scan:", 1)[1].split("  explorer:", 1)[0]
        self.assertIn("--redact", secret_scan)
        self.assertNotIn("--verbose", secret_scan)
        self.assertIn("author/email metadata", secret_scan)

    def test_secret_scan_job_checks_commit_attribution_without_printing_values(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        secret_scan = workflow.split("  secret-scan:", 1)[1].split("  explorer:", 1)[0]
        self.assertIn("scripts/check_commit_attribution.py", secret_scan)
        checker = (ROOT / "scripts" / "check_commit_attribution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Identity values are omitted", checker)
        self.assertIn('["git", "interpret-trailers", "--parse"]', checker)

    def test_readme_repository_links_resolve_inside_repository(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
        for link in links:
            with self.subTest(link=link):
                parsed = urlparse(link)
                if not parsed.scheme and parsed.path:
                    target = (ROOT / unquote(parsed.path)).resolve()
                    self.assertTrue(target.is_relative_to(ROOT.resolve()), link)
                    self.assertTrue(target.exists(), link)
                    continue
                self.assertEqual(parsed.scheme, "https", link)
                if parsed.hostname == "github.com":
                    parts = parsed.path.strip("/").split("/")
                    is_repo_file_link = (
                        parts[:2] == ["CometWeb-io", "whykit"]
                        and len(parts) > 4
                        and parts[2] in {"blob", "tree"}
                    )
                    if is_repo_file_link:
                        target = ROOT.joinpath(*parts[4:])
                        self.assertTrue(target.exists(), link)

    def test_local_links_in_public_project_docs_resolve(self) -> None:
        documents = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "scripts" / "README.md",
            ROOT / ".github" / "RELEASE.md",
            *sorted((ROOT / "docs").glob("*.md")),
        ]
        for document in documents:
            body = document.read_text(encoding="utf-8")
            body = re.sub(r"(?ms)^```.*?^```\s*", "", body)
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
                parsed = urlparse(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                resolved = (document.parent / unquote(parsed.path)).resolve()
                with self.subTest(document=document.relative_to(ROOT), target=target):
                    self.assertTrue(resolved.is_relative_to(ROOT.resolve()), target)
                    self.assertTrue(resolved.exists(), target)

    def test_northline_example_is_explicitly_synthetic_and_uses_no_real_identity_fields(self) -> None:
        example = ROOT / "examples" / "northline"
        content = "\n".join(path.read_text(encoding="utf-8") for path in example.rglob("*.md"))
        self.assertIn("No real interviews", content)
        self.assertIn("not a transcript or record of a", content)
        self.assertIn("Synthetic fixture", content)
        self.assertNotRegex(content, r"\bHRB\s+\d+")
        self.assertNotRegex(content, r"Registered name:")
        self.assertTrue((example / "07-research/interviews/example-region-maintenance-lead-2026-06-18.md").is_file())

    def test_json_schemas_are_valid_and_declare_a_draft(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.json")):
            with self.subTest(path=path.name):
                parsed = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(parsed.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
                self.assertTrue(parsed.get("$id", "").endswith(path.name))

    def test_the_documented_commands_all_exist(self) -> None:
        documented = {
            "init", "adopt", "lint", "new", "status", "graph", "backlinks", "impact",
            "query", "context", "pack", "review", "snapshot", "verify-snapshot",
            "check", "policy", "evidence", "history", "rules", "doctor",
            "install-hooks", "explorer-index", "serve",
        }
        help_text = run("--help").stdout
        for command in documented:
            self.assertIn(command, help_text, command)

    def test_version_is_reported_and_matches_the_package(self) -> None:
        from whykit import __version__

        self.assertIn(__version__, run("--version").stdout)

    def test_pre_commit_hooks_use_repository_local_policy(self) -> None:
        from whykit.cli import PRE_COMMIT_HOOK

        checkout_hook = (ROOT / "scripts" / "pre-commit").read_text(encoding="utf-8")
        for content in (PRE_COMMIT_HOOK, checkout_hook):
            self.assertIn("check --profile local", content)
            self.assertNotIn("lint --no-orphans", content)

    def test_github_action_installs_its_own_revision_and_fails_closed_on_shallow_history(self) -> None:
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn('WHYKIT_ACTION_PATH: ${{ github.action_path }}', action)
        self.assertIn('pip install --disable-pip-version-check --quiet "$WHYKIT_ACTION_PATH"', action)
        self.assertNotIn('pip install --disable-pip-version-check --quiet \\n          "whykit', action)
        self.assertIn('History check requires actions/checkout with fetch-depth: 0', action)
        self.assertIn('profile:', action)
        self.assertIn('base:', action)
        self.assertIn('whykit check', action)
        self.assertNotIn('History check skipped', action)


if __name__ == "__main__":
    unittest.main()
