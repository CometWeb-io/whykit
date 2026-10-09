"""The machine contract: every JSON stdout validates, and failures are JSON too.

Success payloads from every command that writes JSON are validated against the
schema `whykit.contract.OUTPUT_SCHEMAS` names for it. Failures under `--json`
must produce exactly one error object on stdout with a stable code, keep the
exit code, and leave stderr byte-for-byte what a human run prints.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import unsupported_keywords, validate  # noqa: E402
from _vaults import CLI, fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit.contract import ERROR_CODES, ERROR_EXIT_CODES, ERROR_SCHEMA, OUTPUT_SCHEMAS, error_payload  # noqa: E402

EXAMPLE = ROOT / "examples" / "northline"
SCHEMAS = ROOT / "schemas"
TODAY = "2026-09-17"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def call(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:  # argparse usage errors and --help
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


class SchemaCatalogTests(unittest.TestCase):
    def test_schemas_only_use_keywords_the_test_validator_understands(self) -> None:
        for path in sorted(SCHEMAS.glob("*.json")):
            with self.subTest(schema=path.name):
                self.assertEqual(unsupported_keywords(json.loads(path.read_text(encoding="utf-8"))), [])

    def test_every_mapped_schema_exists_and_pins_contract_version(self) -> None:
        for command, name in {**OUTPUT_SCHEMAS, "<error>": ERROR_SCHEMA}.items():
            with self.subTest(command=command):
                schema = load_schema(name)
                self.assertIn("contract_version", schema["required"])
                self.assertEqual(schema["properties"]["contract_version"], {"const": 1})

    def test_error_schema_lists_exactly_the_registered_codes(self) -> None:
        schema = load_schema(ERROR_SCHEMA)
        self.assertEqual(schema["properties"]["error"]["properties"]["code"]["enum"], list(ERROR_CODES))

    def test_every_json_capable_command_has_a_schema(self) -> None:
        def walk(parser: argparse.ArgumentParser, prefix: list[str]) -> list[str]:
            found: list[str] = []
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        found += walk(child, [*prefix, name])
            if prefix and any("--json" in action.option_strings for action in parser._actions):
                found.append(" ".join(prefix))
            return found

        json_commands = set(walk(cli.build_parser(), []))
        # `snapshot` has no --json flag but always writes JSON to stdout.
        self.assertEqual(json_commands | {"snapshot", "rules <code>"}, set(OUTPUT_SCHEMAS))

    def test_automation_doc_lists_every_error_code_and_schema(self) -> None:
        doc = (ROOT / "docs" / "automation.md").read_text(encoding="utf-8")
        for code in ERROR_CODES:
            self.assertIn(f"`{code}`", doc, code)
        for name in {*OUTPUT_SCHEMAS.values(), ERROR_SCHEMA}:
            self.assertIn(name, doc, name)

    def test_error_payload_refuses_unregistered_codes(self) -> None:
        with self.assertRaises(ValueError):
            error_payload("made_up", "nope")


class SuccessPayloadTests(unittest.TestCase):
    """Real outputs from real vaults, validated against the published schemas."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.northline = cls.tmp / "northline"
        shutil.copytree(EXAMPLE, cls.northline)
        git("init", "-q", cwd=cls.northline)
        git("add", "-A", cwd=cls.northline)
        git("commit", "-q", "-m", "baseline", cwd=cls.northline)
        cls.fresh = cls.tmp / "fresh"
        fresh_vault(cls.fresh)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def assertConforms(self, command: str, argv: list[str], *, exit_codes: tuple[int, ...] = (0,)) -> dict:
        code, out, err = call(*argv)
        self.assertIn(code, exit_codes, f"{argv}: exit {code}\n{err}")
        payload = json.loads(out)
        self.assertNotIn("error", payload, argv)
        self.assertEqual(payload["contract_version"], 1, argv)
        self.assertEqual(validate(payload, load_schema(OUTPUT_SCHEMAS[command])), [], argv)
        return payload

    def test_read_only_commands(self) -> None:
        root = ["--root", str(self.northline)]
        cases: list[tuple[str, list[str], tuple[int, ...]]] = [
            ("lint", ["lint", *root, "--json", "--today", TODAY], (0,)),
            ("status", ["status", *root, "--json", "--today", TODAY], (0,)),
            ("graph", ["graph", *root, "--json"], (0,)),
            ("graph", ["graph", *root], (0,)),
            ("backlinks", ["backlinks", "D-001", *root, "--json"], (0,)),
            ("backlinks", ["backlinks", "D-999", *root, "--json"], (1,)),
            ("impact", ["impact", "E-001", *root, "--json"], (0,)),
            ("impact", ["impact", "D-001", *root, "--json"], (0,)),
            ("impact", ["impact", "Home.md", *root, "--json"], (0,)),
            ("impact", ["impact", "E-999", *root, "--json"], (1,)),
            ("trace", ["trace", *root, "--json", "--today", TODAY], (0,)),
            ("trace", ["trace", *root, "--decision", "D-999", "--json", "--today", TODAY], (1,)),
            ("query", ["query", "pricing", *root, "--json"], (0,)),
            ("context", ["context", "D-001", *root, "--json"], (0,)),
            ("context", ["context", "E-001", *root, "--json"], (0,)),
            ("pack", ["pack", "D-001", *root, "--json"], (0,)),
            ("review list", ["review", *root, "list", "--json", "--today", TODAY], (0,)),
            ("snapshot", ["snapshot", *root, "--today", TODAY], (0,)),
            ("check", ["check", *root, "--profile", "local", "--json", "--today", TODAY], (0, 1)),
            ("policy", ["policy", *root, "--json"], (0,)),
            ("evidence list", ["evidence", *root, "list", "--json"], (0,)),
            ("history", ["history", *root, "--base", "HEAD", "--json"], (0,)),
            ("diff", ["diff", *root, "--base", "HEAD", "--json", "--today", TODAY], (0,)),
            ("diff", ["diff", *root, "--base", "HEAD", "--format", "json", "--today", TODAY], (0,)),
            ("rules", ["rules", "--json"], (0,)),
            ("rules <code>", ["rules", "evidence.missing", "--json"], (0,)),
            ("doctor", ["doctor", *root, "--json"], (0, 1)),
            ("explorer-index", ["explorer-index", *root, "--today", TODAY], (0,)),
        ]
        for command, argv, exits in cases:
            with self.subTest(argv=argv):
                self.assertConforms(command, argv, exit_codes=exits)

    def test_verify_snapshot(self) -> None:
        code, out, _ = call("snapshot", "--root", str(self.northline), "--today", TODAY)
        self.assertEqual(code, 0)
        baseline = self.tmp / "baseline.json"
        baseline.write_text(out, encoding="utf-8")
        self.assertConforms(
            "verify-snapshot",
            ["verify-snapshot", str(baseline), "--root", str(self.northline), "--today", TODAY, "--json"],
        )

    def test_doctor_without_a_vault_is_a_failed_report_not_an_error(self) -> None:
        empty = self.tmp / "not-a-vault"
        empty.mkdir(exist_ok=True)
        self.assertConforms("doctor", ["doctor", "--root", str(empty), "--json"], exit_codes=(1,))

    def test_mutating_commands(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            root = ["--root", str(vault)]
            evidence = self.assertConforms("new evidence", [
                "new", *root, "evidence", "--source", "Example survey", "--location", "https://example.com/survey",
                "--type", "survey", "--claims", "Respondents prefer email", "--json",
            ])
            self.assertConforms("new decision", ["new", *root, "decision", "Use email first", "--source", evidence["id"], "--json"])
            self.assertConforms("new note", ["new", *root, "note", "Survey notes", "--workstream", "notes", "--json"])
            self.assertConforms("evidence retire", ["evidence", *root, "retire", evidence["id"], "--why", "Superseded survey", "--json"])
            self.assertConforms("review record", [
                "review", *root, "record", "Home.md", "--reviewer", "Ada Example", "--outcome", "update-required", "--json",
            ])
            self.assertConforms("init", ["init", str(Path(raw) / "second"), "--json"])
            source = Path(raw) / "existing"
            source.mkdir()
            (source / "decision.md").write_text("# We chose email\n\nBecause it is cheap.\n", encoding="utf-8")
            self.assertConforms("adopt", ["adopt", str(source), "--into", str(vault), "--json"])


class ErrorObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.vault = cls.tmp / "vault"
        fresh_vault(cls.vault)
        cls.nowhere = cls.tmp / "nowhere"
        cls.nowhere.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def assertJsonError(self, argv: list[str], code: str, *, human_argv: list[str] | None = None) -> dict:
        status, out, err = call(*argv)
        self.assertEqual(status, ERROR_EXIT_CODES[code], f"{argv}\n{err}")
        payload = json.loads(out)  # exactly one JSON document, nothing else
        self.assertEqual(validate(payload, load_schema(ERROR_SCHEMA)), [], payload)
        self.assertEqual(payload["error"]["code"], code, payload)
        self.assertTrue(err.strip(), "the human message must still reach stderr")
        self.assertNotIn("hint:", payload["error"]["message"])
        if human_argv is not None:
            # Without --json: same exit code, same stderr, nothing on stdout.
            h_status, h_out, h_err = call(*human_argv)
            self.assertEqual((h_status, h_out, h_err), (status, "", err))
        return payload

    def test_vault_not_found_on_every_vault_command(self) -> None:
        root = ["--root", str(self.nowhere)]
        commands = [
            ["lint", *root], ["status", *root], ["backlinks", "D-001", *root], ["impact", "D-001", *root],
            ["trace", *root], ["query", "x", *root], ["context", "D-001", *root], ["pack", "D-001", *root, "--format", "markdown"],
            ["review", *root, "list"], ["verify-snapshot", "s.json", *root], ["check", *root], ["policy", *root],
            ["evidence", *root, "list"], ["new", *root, "decision", "X"], ["graph", *root, "--format", "dot"],
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                json_argv = [*argv[:-2], "--json"] if "--format" in argv else [*argv, "--json"]
                payload = self.assertJsonError(json_argv, "vault_not_found", human_argv=argv)
                if argv[0] not in ("trace",):
                    self.assertIsNotNone(payload["error"]["hint"])

    def test_invalid_arguments(self) -> None:
        root = ["--root", str(self.vault)]
        for argv in (
            ["lint", *root, "--today", "2026-02-30"],
            ["status", *root, "--due-days", "-1"],
            ["query", *root, "--limit", "-1"],
            ["query", *root, "--source", "nope"],
            ["trace", *root, "--decision", "X-1"],
            ["context", "D-001", *root, "--max-chars", "-1"],
            ["rules", "no.such_rule"],
            ["lint", "../outside.md", *root],
        ):
            with self.subTest(argv=argv):
                self.assertJsonError([*argv, "--json"], "invalid_argument", human_argv=argv)

    def test_usage_errors_from_argparse_and_conflicting_flags(self) -> None:
        payload = self.assertJsonError(["query", "--json", "--limit", "many"], "usage", human_argv=["query", "--limit", "many"])
        self.assertIn("--limit", payload["error"]["message"])
        self.assertJsonError(["graph", "--json", "--format", "dot", "--root", str(self.vault)], "usage")
        self.assertJsonError(["init", str(self.tmp / "x"), "--minimal", "--full", "--json"], "usage")
        self.assertJsonError(["rules", "--json", "--markdown"], "usage")
        self.assertJsonError(["pack", "--json", "--root", str(self.vault)], "usage")
        self.assertJsonError(["lint", "--jso", "--bogus"], "usage")

    def test_mutation_refusals(self) -> None:
        root = ["--root", str(self.vault)]
        self.assertJsonError(["new", *root, "decision", "X", "--supersedes", "D-999", "--json"], "operation_rejected")
        self.assertJsonError(["new", *root, "decision", "X", "--source", "E-999", "--json"], "operation_rejected")
        self.assertJsonError(["init", str(self.vault), "--json"], "target_exists",
                             human_argv=["init", str(self.vault)])
        self.assertJsonError(["graph", *root, "--json", "--output", "../escape.json"], "unsafe_path")
        self.assertJsonError(["adopt", str(self.vault / "Home.md"), "--into", str(self.vault), "--json"], "invalid_target")

    def test_a_missing_target_exits_1_on_every_command(self) -> None:
        """One rule: a vault record named as the target that does not exist exits 1.

        Read commands answer with a report (`exists: false`); commands that
        would change the target answer with a `not_found` error object.
        """
        root = ["--root", str(self.vault)]
        self.assertJsonError(["evidence", *root, "retire", "E-999", "--why", "gone", "--json"], "not_found",
                             human_argv=["evidence", *root, "retire", "E-999", "--why", "gone"])
        self.assertJsonError(["review", *root, "record", "D-999", "--reviewer", "Ada Example", "--json"], "not_found",
                             human_argv=["review", *root, "record", "D-999", "--reviewer", "Ada Example"])
        for argv in (
            ["backlinks", "D-999", *root], ["impact", "D-999", *root], ["context", "D-999", *root],
            ["trace", "--decision", "D-999", *root],
        ):
            with self.subTest(argv=argv):
                status, out, _ = call(*argv, "--json")
                self.assertEqual(status, 1)
                self.assertNotIn("error", json.loads(out))
        status, out, _ = call("pack", "D-999", *root, "--json")
        self.assertEqual(status, 1)
        self.assertEqual(len(json.loads(out)["missing"]), 1)

    def test_missing_decision_log_is_a_broken_vault(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            (vault / "06-decisions" / "decision-log.md").unlink()
            self.assertJsonError(["new", "--root", str(vault), "decision", "X", "--json"], "vault_invalid")

    def test_status_accepted_names_the_valid_value(self) -> None:
        argv = ["new", "--root", str(self.vault), "decision", "X", "--status", "accepted"]
        payload = self.assertJsonError([*argv, "--json"], "usage", human_argv=argv)
        self.assertIn("draft", payload["error"]["message"])
        self.assertIn("whykit review approve", payload["error"]["hint"])
        _, _, err = call(*argv)
        self.assertIn("hint: decision logs display approved decisions", err)

    def test_each_error_code_has_one_documented_exit_code(self) -> None:
        self.assertEqual(set(ERROR_EXIT_CODES), set(ERROR_CODES))
        doc = (ROOT / "docs" / "automation.md").read_text(encoding="utf-8")
        for code, exit_code in ERROR_EXIT_CODES.items():
            self.assertIn(f"| `{code}` | {exit_code} |", doc, code)

    def test_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            (vault / "whykit.toml").write_text("format_version = [unclosed\n", encoding="utf-8")
            for argv in (["policy"], ["status"], ["check"]):
                with self.subTest(argv=argv):
                    self.assertJsonError([*argv, "--root", str(vault), "--json"], "invalid_config",
                                         human_argv=[*argv, "--root", str(vault)])

    def test_explorer_index_refuses_a_vault_with_lint_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            (vault / "Home.md").write_text("---\ntitle: broken\n---\n# Home\n", encoding="utf-8")
            payload = self.assertJsonError(["explorer-index", "--root", str(vault)], "vault_invalid")
            self.assertIn("lint error", payload["error"]["message"])
            self.assertIn("whykit lint", payload["error"]["hint"])

    def test_git_failures(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            git("init", "-q", cwd=repo)
            self.assertJsonError(["history", "--root", str(repo), "--base", "no-such-ref", "--json"], "git_error")
            self.assertJsonError(["diff", "--root", str(repo), "--base", "no-such-ref", "--json"], "git_error")
        with mock.patch("whykit.immutability.changed_records", side_effect=FileNotFoundError("git")):
            payload = self.assertJsonError(["history", "--base", "HEAD", "--json"], "missing_dependency")
            self.assertIn("Git", payload["error"]["hint"])

    def test_filesystem_refusal_and_interrupt_are_structured(self) -> None:
        root = ["--root", str(self.vault)]
        with mock.patch.object(cli, "cmd_status", side_effect=PermissionError(13, "Permission denied", "/x")):
            payload = self.assertJsonError(["status", *root, "--json"], "io_error")
            self.assertIn("WHYKIT_DEBUG", payload["error"]["hint"])
        with mock.patch.object(cli, "cmd_status", side_effect=KeyboardInterrupt):
            self.assertJsonError(["status", *root, "--json"], "interrupted")

    def test_a_crash_is_an_internal_error_object_without_a_traceback(self) -> None:
        root = ["--root", str(self.vault)]
        boom = RuntimeError("unexpected state\nsecond line")
        with mock.patch.dict(os.environ, {"WHYKIT_DEBUG": ""}), \
                mock.patch.object(cli, "cmd_status", side_effect=boom):
            payload = self.assertJsonError(["status", *root, "--json"], "internal_error",
                                           human_argv=["status", *root])
            _, _, err = call("status", *root)
        self.assertEqual(ERROR_EXIT_CODES["internal_error"], 70)
        self.assertEqual(payload["error"]["message"], "internal error in `whykit status`: RuntimeError: unexpected state")
        self.assertIn("github.com/CometWeb-io/whykit/issues", payload["error"]["hint"])
        self.assertIn("WHYKIT_DEBUG=1", payload["error"]["hint"])
        self.assertNotIn("Traceback", err)

    def test_whykit_debug_adds_the_traceback_on_stderr_only(self) -> None:
        root = ["--root", str(self.vault)]
        with mock.patch.dict(os.environ, {"WHYKIT_DEBUG": "1"}), \
                mock.patch.object(cli, "cmd_status", side_effect=ZeroDivisionError("x")):
            status, out, err = call("status", *root, "--json")
        self.assertEqual(status, 70)
        self.assertEqual(json.loads(out)["error"]["code"], "internal_error")
        self.assertIn("Traceback (most recent call last)", err)
        self.assertNotIn("Traceback", out)

    def test_every_error_code_is_exercised_by_this_suite(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        for code in ERROR_CODES:
            self.assertIn(f'"{code}"', source, code)

    def test_end_to_end_through_the_console_script(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "status", "--root", str(self.nowhere), "--json"],
            text=True, encoding="utf-8", errors="replace", capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "vault_not_found")
        self.assertIn("not a WhyKit vault", result.stderr)


if __name__ == "__main__":
    unittest.main()
