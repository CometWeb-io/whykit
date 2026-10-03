"""WhyKit where teams already work: pre-commit, SARIF and GitHub annotations.

* ``.pre-commit-hooks.yaml`` exposes ``whykit-lint`` and ``whykit-history``.
  Their entries must be real CLI commands, and (when the pre-commit framework
  is installed: ``uv sync --group hooks``) they are installed and run through
  ``pre-commit try-repo`` against a throwaway Git repository.
* ``whykit lint --format sarif`` is a SARIF 2.1.0 log: stable ``ruleId`` = rule
  code, work-tree-relative locations, and a ``helpUri`` that resolves to an
  anchor in ``docs/rules.md``.
* ``--format github`` on ``lint`` and ``check`` prints workflow commands that
  GitHub turns into pull-request annotations.
* ``whykit history --staged`` is the pre-commit form of the history gate.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
TINY = ROOT / "examples" / "tiny"
SCHEMAS = ROOT / "schemas"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from whykit.ci_formats import RULES_DOC_URI, workflow_command  # noqa: E402
from whykit.cli import build_parser  # noqa: E402
from whykit.contract import FORMAT_SCHEMAS, OUTPUT_SCHEMAS  # noqa: E402
from whykit.rules import RULES  # noqa: E402

HAS_GIT = shutil.which("git") is not None
GIT_IDENTITY = ("-c", "user.name=Example", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false")
TODAY = "2026-09-17"


def run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *argv], cwd=cwd or ROOT,
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60,
    )


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args], cwd=cwd, check=True,
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60,
    ).stdout


def init_repo(path: Path) -> None:
    git("init", "-q", "-b", "main", cwd=path)


def commit_all(path: Path, message: str = "snapshot") -> None:
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", message, cwd=path)


def break_home(vault: Path) -> int:
    """Append a dangling wikilink to Home.md and return its line number."""
    home = vault / "Home.md"
    text = home.read_text(encoding="utf-8")
    home.write_text(text + "\n[[does-not-exist]]\n", encoding="utf-8")
    return len((text + "\n").splitlines()) + 1


def sarif_schema() -> dict:
    return json.loads((SCHEMAS / FORMAT_SCHEMAS["lint --format sarif"]).read_text(encoding="utf-8"))


def load_hooks() -> list[dict[str, str]]:
    """Parse .pre-commit-hooks.yaml: a flat list of scalar mappings (stdlib only)."""
    hooks: list[dict[str, str]] = []
    for raw in (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        match = re.match(r"^(- |  )([\w-]+):\s*(.*)$", line)
        assert match, f"unexpected line in .pre-commit-hooks.yaml: {raw!r}"
        if match.group(1) == "- ":
            hooks.append({})
        value = match.group(3)
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        hooks[-1][match.group(2)] = value
    return hooks


class PreCommitManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hooks = {hook["id"]: hook for hook in load_hooks()}

    def test_exposes_lint_and_history(self) -> None:
        self.assertEqual(set(self.hooks), {"whykit-lint", "whykit-history"})
        for hook in self.hooks.values():
            with self.subTest(hook=hook["id"]):
                self.assertEqual(hook["language"], "python")
                # Links and indexes cross files: never lint only the changed ones.
                self.assertEqual(hook["pass_filenames"], "false")
                self.assertTrue(hook.get("name") and hook.get("description"))
        self.assertEqual(self.hooks["whykit-history"]["stages"], "[pre-commit]")

    def test_entries_are_real_cli_commands(self) -> None:
        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
        for hook in self.hooks.values():
            program, *argv = shlex.split(hook["entry"])
            with self.subTest(hook=hook["id"]):
                self.assertEqual(scripts.get(program), "whykit.cli:main")
                # pre-commit appends user `args`; --root is the documented one.
                parsed = build_parser().parse_args([*argv, "--root", "vault"])
                self.assertEqual(parsed.command, hook["id"].removeprefix("whykit-"))
                self.assertEqual(parsed.root, "vault")
        self.assertTrue(build_parser().parse_args(shlex.split(self.hooks["whykit-history"]["entry"])[1:]).staged)

    def test_file_filters_trigger_on_vault_changes_only(self) -> None:
        lint = re.compile(self.hooks["whykit-lint"]["files"])
        history = re.compile(self.hooks["whykit-history"]["files"])
        for path in ("Home.md", "knowledge/notes/x.md", "whykit.toml", "knowledge/whykit.toml"):
            self.assertTrue(lint.search(path), path)
        for path in ("src/app.py", "pyproject.toml", "notes/x.md.bak"):
            self.assertFalse(lint.search(path), path)
        for path in ("06-decisions/d-001-x.md", "kb/06-decisions/d-002-y.md", "00-context/review-log.md"):
            self.assertTrue(history.search(path), path)
        for path in ("notes/x.md", "00-context/evidence-register.md", "06-decisions.md"):
            self.assertFalse(history.search(path), path)


@unittest.skipUnless(HAS_GIT and os.name != "nt", "needs git and a POSIX shell")
class PreCommitTryRepoTests(unittest.TestCase):
    """Install the hooks the way a consumer repository would, then run them."""

    @classmethod
    def setUpClass(cls) -> None:
        probe = subprocess.run([sys.executable, "-m", "pre_commit", "--version"], capture_output=True, text=True)
        if probe.returncode != 0:
            if os.environ.get("WHYKIT_REQUIRE_PRE_COMMIT"):
                raise AssertionError("pre-commit is required here: uv sync --group hooks")
            raise unittest.SkipTest("pre-commit is not installed (uv sync --group hooks)")
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        # A hermetic copy of the hook repository: exactly what pip needs to
        # install the package, committed, so uncommitted or untracked state of
        # the developer's checkout cannot leak in or go missing.
        cls.hook_repo = tmp / "whykit-hooks"
        cls.hook_repo.mkdir()
        for name in ("pyproject.toml", "hatch_build.py", "README.md", "LICENSE", "NOTICE", "CHANGELOG.md", ".pre-commit-hooks.yaml"):
            shutil.copy2(ROOT / name, cls.hook_repo / name)
        shutil.copytree(ROOT / "src", cls.hook_repo / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        init_repo(cls.hook_repo)
        commit_all(cls.hook_repo, "hooks")
        cls.home = tmp / "pre-commit-home"
        cls.env = {**os.environ, "PRE_COMMIT_HOME": str(cls.home), "PRE_COMMIT_COLOR": "never"}
        cls.consumer_parent = tmp

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def consumer(self, name: str) -> Path:
        repo = self.consumer_parent / name
        shutil.copytree(TINY, repo / "kb")
        (repo / "app.py").write_text("print('unrelated code')\n", encoding="utf-8")
        init_repo(repo)
        commit_all(repo, "baseline")
        return repo

    def run_hook(self, repo: Path, hook: str, *extra: str) -> subprocess.CompletedProcess[str]:
        """Run *hook* with `args: [--root, kb]`, as a consumer's config would.

        try-repo cannot pass hook arguments, so this form goes through a
        config file (kept outside the repository, so pre-commit does not
        refuse it as an unstaged config). The bare try-repo test covers the
        default form.
        """
        config = {"repos": [{"repo": str(self.hook_repo), "rev": git("rev-parse", "HEAD", cwd=self.hook_repo).strip(),
                             "hooks": [{"id": hook, "args": ["--root", "kb"]}]}]}
        config_file = self.consumer_parent / f"{repo.name}-pre-commit-config.yaml"
        config_file.write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pre_commit", "run", hook, "--config", str(config_file), *extra],
            cwd=repo, env=self.env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600,
        )
        if result.returncode not in (0, 1):
            self.fail(f"pre-commit could not run ({result.returncode}):\n{result.stdout}{result.stderr}")
        return result

    def test_try_repo_installs_both_hooks_and_passes_a_clean_vault(self) -> None:
        repo = self.consumer_parent / "vault-at-root"
        shutil.copytree(TINY, repo)
        init_repo(repo)
        commit_all(repo)
        result = subprocess.run(
            [sys.executable, "-m", "pre_commit", "try-repo", str(self.hook_repo), "--all-files"],
            cwd=repo, env=self.env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"WhyKit lint\.+Passed")
        self.assertRegex(result.stdout, r"WhyKit history \(staged\)\.+Passed")

        # The same try-repo route must block a broken link.
        break_home(repo)
        failed = subprocess.run(
            [sys.executable, "-m", "pre_commit", "try-repo", str(self.hook_repo), "whykit-lint", "--all-files"],
            cwd=repo, env=self.env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600,
        )
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("wikilink.missing", failed.stdout)

    def test_lint_hook_blocks_a_broken_vault_in_a_subdirectory(self) -> None:
        repo = self.consumer("lint-subdir")
        self.assertEqual(self.run_hook(repo, "whykit-lint", "--all-files").returncode, 0)
        break_home(repo / "kb")
        git("add", "-A", cwd=repo)
        result = self.run_hook(repo, "whykit-lint")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("wikilink.missing", result.stdout)

    def test_history_hook_blocks_a_staged_rewrite_but_not_an_unstaged_one(self) -> None:
        repo = self.consumer("history-subdir")
        record = next((repo / "kb" / "06-decisions").glob("d-001-*.md"))
        record.write_text(record.read_text(encoding="utf-8") + "\nRewritten reasoning.\n", encoding="utf-8")
        # Unstaged: not part of the commit, so not blocked (pre-commit only
        # runs a hook when a staged file matches, so force the run).
        unstaged = self.run_hook(repo, "whykit-history", "--files", "kb/06-decisions/" + record.name)
        self.assertEqual(unstaged.returncode, 0, unstaged.stdout)
        git("add", "-A", cwd=repo)
        staged = self.run_hook(repo, "whykit-history")
        self.assertEqual(staged.returncode, 1, staged.stdout)
        self.assertIn("append-only", staged.stdout)
        self.assertIn(record.name, staged.stdout)


class SarifTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def sarif(self, *argv: str, exit_code: int = 0) -> dict:
        result = run("lint", *argv, "--format", "sarif", "--today", TODAY)
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        log = json.loads(result.stdout)
        self.assertEqual(validate(log, sarif_schema()), [])
        return log

    def test_clean_vault_is_a_valid_log_with_the_full_rule_catalog(self) -> None:
        vault = self.tmp / "tiny"
        shutil.copytree(TINY, vault)
        log = self.sarif("--root", str(vault))
        self.assertEqual(log["version"], "2.1.0")
        run_ = log["runs"][0]
        self.assertEqual(run_["results"], [])
        self.assertEqual([rule["id"] for rule in run_["tool"]["driver"]["rules"]], [rule.code for rule in RULES])
        self.assertEqual(run_["properties"]["contract_version"], 1)
        self.assertEqual(run_["properties"]["files"], 23)

    def test_every_help_uri_lands_on_an_anchor_in_the_rule_reference(self) -> None:
        vault = self.tmp / "tiny"
        shutil.copytree(TINY, vault)
        rules_doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
        anchors = set(re.findall(r'<a name="([^"]+)"></a>', rules_doc))
        for rule in self.sarif("--root", str(vault))["runs"][0]["tool"]["driver"]["rules"]:
            with self.subTest(rule=rule["id"]):
                page, _, anchor = rule["helpUri"].partition("#")
                self.assertEqual(page, RULES_DOC_URI)
                self.assertEqual(anchor, rule["id"])
                self.assertIn(anchor, anchors)

    def test_rule_reference_is_generated_from_the_catalog(self) -> None:
        result = run("rules", "--markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
        table = re.search(r"<!-- rules:start -->\n(.*)<!-- rules:end -->", doc, re.S)
        self.assertIsNotNone(table)
        self.assertEqual(table.group(1), result.stdout)

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_findings_point_at_work_tree_paths_with_stable_rule_ids(self) -> None:
        repo = self.tmp / "repo"
        shutil.copytree(TINY, repo / "knowledge")
        init_repo(repo)
        line = break_home(repo / "knowledge")
        log = self.sarif("--root", str(repo / "knowledge"), exit_code=1)
        run_ = log["runs"][0]
        rules = run_["tool"]["driver"]["rules"]
        [result] = [r for r in run_["results"] if r["ruleId"] == "wikilink.missing"]
        self.assertEqual(result["level"], "error")
        self.assertEqual(rules[result["ruleIndex"]]["id"], "wikilink.missing")
        location = result["locations"][0]["physicalLocation"]
        self.assertEqual(location["artifactLocation"], {"uri": "knowledge/Home.md", "uriBaseId": "%SRCROOT%"})
        self.assertEqual(location["region"], {"startLine": line})
        base = run_["originalUriBaseIds"]["%SRCROOT%"]["uri"]
        self.assertEqual(Path(base.removeprefix("file://")).resolve(), repo.resolve())
        self.assertEqual(run_["properties"]["vault_prefix"], "knowledge/")

    def test_outside_git_paths_stay_vault_relative_and_are_uri_encoded(self) -> None:
        vault = self.tmp / "loose"
        shutil.copytree(TINY, vault)
        (vault / "notes" / "a b.md").write_text("no front matter [[missing-target]]\n", encoding="utf-8")
        log = self.sarif("--root", str(vault), exit_code=1)
        uris = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in log["runs"][0]["results"]}
        self.assertIn("notes/a%20b.md", uris)

    def test_fingerprint_survives_a_line_shift(self) -> None:
        vault = self.tmp / "shift"
        shutil.copytree(TINY, vault)
        break_home(vault)

        def wikilink_result() -> dict:
            log = self.sarif("--root", str(vault), exit_code=1)
            return next(r for r in log["runs"][0]["results"] if r["ruleId"] == "wikilink.missing")

        before = wikilink_result()
        home = vault / "Home.md"
        text = home.read_text(encoding="utf-8")
        body_start = text.index("\n---", 4) + 5
        home.write_text(text[:body_start] + "\nAn added paragraph.\n" + text[body_start:], encoding="utf-8")
        after = wikilink_result()
        self.assertNotEqual(before["locations"], after["locations"])
        self.assertEqual(before["partialFingerprints"], after["partialFingerprints"])

    def test_strict_changes_the_exit_code_not_the_report(self) -> None:
        vault = self.tmp / "warn"
        shutil.copytree(TINY, vault)
        (vault / "notes" / "stray.md").write_text(
            (vault / "notes").glob("*.md").__next__().read_text(encoding="utf-8"), encoding="utf-8",
        )
        relaxed = self.sarif("--root", str(vault))
        strict = self.sarif("--root", str(vault), "--strict", exit_code=1)
        self.assertTrue(relaxed["runs"][0]["results"])
        self.assertEqual(relaxed["runs"][0]["results"], strict["runs"][0]["results"])
        self.assertTrue(all(r["level"] == "warning" for r in relaxed["runs"][0]["results"]))

    def test_conflicting_and_failed_invocations(self) -> None:
        conflict = run("lint", "--root", str(TINY), "--json", "--format", "sarif")
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(json.loads(conflict.stdout)["error"]["code"], "usage")
        # No vault: SARIF has no error object, so stdout stays empty rather
        # than handing an upload step a document that is not SARIF.
        missing = run("lint", "--root", str(self.tmp / "nowhere"), "--format", "sarif")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("vault", missing.stderr.lower())
        # `--format json` is the JSON contract, error object included.
        json_missing = run("lint", "--root", str(self.tmp / "nowhere"), "--format", "json")
        self.assertEqual(json.loads(json_missing.stdout)["error"]["code"], "vault_not_found")
        same = run("lint", "--root", str(TINY), "--format", "json", "--today", TODAY)
        self.assertEqual(validate(json.loads(same.stdout), json.loads((SCHEMAS / OUTPUT_SCHEMAS["lint"]).read_text(encoding="utf-8"))), [])


class GithubAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_workflow_command_escaping(self) -> None:
        self.assertEqual(
            workflow_command("error", "50% done\nnext line", file="a,b:c.md", line=3, title="T: x"),
            "::error file=a%2Cb%3Ac.md,line=3,title=T%3A x::50%25 done%0Anext line",
        )
        self.assertEqual(workflow_command("warning", "plain"), "::warning::plain")
        # A line without a file means nothing to GitHub; it is dropped.
        self.assertEqual(workflow_command("error", "m", line=4), "::error::m")

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_lint_annotates_findings_at_work_tree_paths(self) -> None:
        repo = self.tmp / "repo"
        shutil.copytree(TINY, repo / "kb")
        init_repo(repo)
        line = break_home(repo / "kb")
        result = run("lint", "--root", str(repo / "kb"), "--format", "github", "--today", TODAY)
        self.assertEqual(result.returncode, 1, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith(f"::error file=kb/Home.md,line={line},title=WhyKit wikilink.missing::"), lines[0])
        self.assertIn(f"{RULES_DOC_URI}#wikilink.missing", lines[0])
        self.assertEqual(lines[-1], "whykit lint: 23 files — 1 error(s), 0 warning(s)")

    def test_strict_warnings_get_a_gate_level_error(self) -> None:
        vault = self.tmp / "warn"
        shutil.copytree(TINY, vault)
        (vault / "notes" / "stray.md").write_text(
            (vault / "notes").glob("*.md").__next__().read_text(encoding="utf-8"), encoding="utf-8",
        )
        relaxed = run("lint", "--root", str(vault), "--format", "github", "--today", TODAY)
        self.assertEqual(relaxed.returncode, 0, relaxed.stderr)
        self.assertRegex(relaxed.stdout, r"(?m)^::warning file=")
        self.assertNotIn("::error", relaxed.stdout)
        strict = run("lint", "--root", str(vault), "--format", "github", "--strict", "--today", TODAY)
        self.assertEqual(strict.returncode, 1)
        self.assertRegex(strict.stdout.splitlines()[-1], r"^::error title=WhyKit::whykit lint --strict: \d+ warning\(s\) fail this gate$")

    def test_clean_vault_prints_only_the_summary(self) -> None:
        result = run("lint", "--root", str(TINY), "--format", "github", "--today", TODAY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "whykit lint: 23 files — 0 error(s), 0 warning(s)\n")

    def test_check_annotates_lint_findings_and_keeps_the_gate_report(self) -> None:
        vault = self.tmp / "broken"
        shutil.copytree(TINY, vault)
        break_home(vault)
        result = run("check", "--root", str(vault), "--profile", "ci", "--format", "github", "--today", TODAY)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^::error file=Home\.md,line=\d+,title=WhyKit wikilink\.missing::")
        self.assertIn("WhyKit ci gate — FAIL", result.stdout)
        conflict = run("check", "--root", str(vault), "--json", "--format", "github")
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(json.loads(conflict.stdout)["error"]["code"], "usage")
        as_json = run("check", "--root", str(vault), "--format", "json", "--today", TODAY)
        self.assertEqual(validate(json.loads(as_json.stdout), json.loads((SCHEMAS / OUTPUT_SCHEMAS["check"]).read_text(encoding="utf-8"))), [])

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_check_annotates_a_rewritten_decision_on_the_record(self) -> None:
        repo = self.tmp / "history"
        shutil.copytree(TINY, repo / "kb")
        init_repo(repo)
        commit_all(repo, "base")
        record = next((repo / "kb" / "06-decisions").glob("d-001-*.md"))
        record.write_text(record.read_text(encoding="utf-8") + "\nRewritten reasoning.\n", encoding="utf-8")
        commit_all(repo, "rewrite")
        result = run("check", "--root", str(repo / "kb"), "--profile", "ci", "--base", "HEAD~1", "--format", "github", "--today", TODAY)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"::error file=kb/06-decisions/{record.name},title=WhyKit history::", result.stdout)


@unittest.skipUnless(HAS_GIT, "needs git")
class StagedHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        shutil.copytree(TINY, self.repo / "kb")
        init_repo(self.repo)
        self.vault = self.repo / "kb"
        self.record = next((self.vault / "06-decisions").glob("d-001-*.md"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def history(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return run("history", "--staged", "--root", str(self.vault), *argv)

    def test_before_the_first_commit_nothing_can_be_rewritten(self) -> None:
        git("add", "-A", cwd=self.repo)
        result = self.history("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload["head"], payload["staged"], payload["passed"]), ("INDEX", True, True))

    def test_only_the_staged_rewrite_is_blocked(self) -> None:
        commit_all(self.repo, "base")
        self.record.write_text(self.record.read_text(encoding="utf-8") + "\nRewritten reasoning.\n", encoding="utf-8")
        self.assertEqual(self.history().returncode, 0)
        git("add", "-A", cwd=self.repo)
        blocked = self.history("--json")
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        payload = json.loads(blocked.stdout)
        schema = json.loads((SCHEMAS / OUTPUT_SCHEMAS["history"]).read_text(encoding="utf-8"))
        self.assertEqual(validate(payload, schema), [])
        self.assertEqual(payload["blocked"], [{"status": "M", "path": f"06-decisions/{self.record.name}"}])
        # The committed form of the same change is what `--base/--head` sees.
        commit_all(self.repo, "rewrite")
        self.assertEqual(run("history", "--base", "HEAD~1", "--root", str(self.vault)).returncode, 1)

    def test_a_staged_review_log_append_passes(self) -> None:
        commit_all(self.repo, "base")
        recorded = run("review", "--root", str(self.vault), "record", "D-002", "--reviewer", "Pat Example", "--today", TODAY)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        git("add", "-A", cwd=self.repo)
        result = self.history()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("staged changes", result.stdout)

    def test_usage_errors(self) -> None:
        commit_all(self.repo, "base")
        both = self.history("--head", "HEAD", "--json")
        self.assertEqual(both.returncode, 2)
        self.assertEqual(json.loads(both.stdout)["error"]["code"], "usage")
        neither = run("history", "--root", str(self.vault), "--json")
        self.assertEqual(neither.returncode, 2)
        self.assertEqual(json.loads(neither.stdout)["error"]["code"], "usage")
        outside = run("history", "--staged", "--root", str(Path(self._tmp.name)), "--json")
        self.assertEqual(outside.returncode, 2)
        self.assertEqual(json.loads(outside.stdout)["error"]["code"], "git_error")


class ContractRegistrationTests(unittest.TestCase):
    def test_format_schemas_exist_and_are_documented(self) -> None:
        automation = (ROOT / "docs" / "automation.md").read_text(encoding="utf-8")
        for fmt, name in FORMAT_SCHEMAS.items():
            with self.subTest(format=fmt):
                self.assertTrue((SCHEMAS / name).is_file())
                self.assertIn(name, automation)
                self.assertIn(f"`{fmt}`", automation)
        self.assertIn("--format github", automation)

    def test_parser_offers_the_documented_formats(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(parser.parse_args(["lint", "--format", "sarif"]).format, "sarif")
            self.assertEqual(parser.parse_args(["check", "--format", "github"]).format, "github")
            with self.assertRaises(SystemExit):
                parser.parse_args(["check", "--format", "sarif"])


if __name__ == "__main__":
    unittest.main()
