"""Public CLI routes for the decision workflow, including installed consumers."""
from __future__ import annotations

import contextlib
import io
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from _jsonschema import validate
from _vaults import ROOT, fresh_vault
from whykit.cli import main
from whykit.contract import OUTPUT_SCHEMAS
from whykit.scaffold import create_evidence, create_note


def call(*args: str) -> tuple[int, dict]:
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        try:
            code = main(list(args))
        except SystemExit as exc:
            code = exc.code
    return code, json.loads(output.getvalue())


class WorkflowCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "vault with spaces"
        fresh_vault(self.root)

    def conforms(self, command: str, payload: dict):
        schema = json.loads((ROOT / "schemas" / OUTPUT_SCHEMAS[command]).read_text(encoding="utf-8"))
        self.assertEqual(validate(payload, schema), [])

    def test_eval_is_available_through_the_public_cli(self):
        status, result = call("agent", "eval", "--cases", str(ROOT / "examples/agent-eval/cases.jsonl"),
                              "--responses", str(ROOT / "examples/agent-eval/responses.jsonl"), "--json")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["contract_version"], 1)
        self.assertEqual(result["count"], 3)
        self.conforms("agent eval", result)

    def test_preflight_declares_no_execution_authority(self):
        proposal = self.base / "proposal.json"
        proposal.write_text(json.dumps({"proposal_id": "example", "decision_ids": ["D-999"]}), encoding="utf-8")
        status, result = call("--root", str(self.root), "agent", "preflight", "--proposal", str(proposal), "--json")
        self.assertEqual(status, 1, result)
        self.assertEqual(result["verdict"], "blocked")
        self.assertIs(result["execution_authorized"], False)
        self.conforms("agent preflight", result)

    def test_workspace_status_is_available_without_a_checkout_script(self):
        manifest = self.base / "workspace.toml"
        manifest.write_text('workspace_version = 1\naudience = "local"\n[[vaults]]\nalias = "example"\nroot = '
                            + json.dumps(self.root.name) + '\n', encoding="utf-8")
        status, result = call("workspace", "status", str(manifest), "--json")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["vaults"][0]["alias"], "example")
        self.conforms("workspace status", result)

    def test_bm25_is_opt_in_and_keeps_legacy_query_output(self):
        create_note(self.root, "SSO connection timeout", workstream="notes", owner="Ada Example")
        status, old = call("query", "sso timeout", "--root", str(self.root), "--json")
        self.assertEqual(status, 0)
        self.assertEqual(old["results"], [])
        status, ranked = call("query", "sso timeout", "--root", str(self.root), "--ranking", "bm25", "--json")
        self.assertEqual(status, 0, ranked)
        self.assertEqual(ranked["results"][0]["title"], "SSO connection timeout")
        self.assertGreater(ranked["results"][0]["score"], 0)
        self.assertEqual(ranked["query"]["ranking"], "bm25")
        self.conforms("query", ranked)

    def test_compiled_context_has_a_wire_budget_and_becomes_stale(self):
        path = create_note(self.root, "Żółta łódź", workstream="notes", owner="Ada Example", sensitivity="public")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["compile-context", str(path.relative_to(self.root)), "--root", str(self.root),
                           "--max-bytes", "4096", "--json"])
        self.assertEqual(status, 0, output.getvalue())
        wire = output.getvalue()
        self.assertLessEqual(len(wire.encode("utf-8")), 4096)
        result = json.loads(wire)
        self.assertEqual(result["budget"]["used_bytes"], len(wire.encode("utf-8")))
        self.conforms("compile-context", result)
        report = self.base / "context.json"
        report.write_text(wire, encoding="utf-8")
        code, check = call("verify-context", str(report), "--root", str(self.root), "--json")
        self.assertEqual(code, 0, check)
        self.assertEqual(check["status"], "current")
        self.conforms("verify-context", check)
        path.write_text(path.read_text(encoding="utf-8") + "\nChanged source.\n", encoding="utf-8")
        code, check = call("verify-context", str(report), "--root", str(self.root), "--json")
        self.assertEqual(code, 1, check)
        self.assertEqual(check["status"], "stale")

    def test_compiled_utf8_wire_bypasses_windows_newline_translation(self):
        path = create_note(self.root, "Żółć", workstream="notes", owner="Ada Example", sensitivity="public")
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="\r\n")
        with contextlib.redirect_stdout(stream):
            status = main(["compile-context", str(path.relative_to(self.root)), "--root", str(self.root), "--max-bytes", "4096"])
        stream.flush()
        wire = buffer.getvalue()
        stream.detach()
        self.assertEqual(status, 0)
        self.assertTrue(wire.endswith(b"\n"))
        self.assertNotIn(b"\r\n", wire)
        self.assertEqual(json.loads(wire)["budget"]["used_bytes"], len(wire))

    def test_deep_impact_and_historical_snapshot_have_public_cli_routes(self):
        path = create_note(self.root, "Source", workstream="notes", owner="Ada Example", sensitivity="public")
        dependent = create_note(self.root, "Dependent", workstream="notes", owner="Ada Example", sensitivity="public")
        dependent.write_text(dependent.read_text(encoding="utf-8") + f"\n[[{path.stem}]]\n", encoding="utf-8")
        code, result = call("deep-impact", str(path.relative_to(self.root)), "--root", str(self.root), "--json")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["results"], result)
        self.conforms("deep-impact", result)
        for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "synthetic snapshot")):
            subprocess.run(["git", "-c", "user.name=Example", "-c", "user.email=person@example.com", *args],
                           cwd=self.root, check=True, capture_output=True)
        code, result = call("knowledge-at", "--root", str(self.root), "--valid-on", dt.date.today().isoformat(), "--json")
        self.assertEqual(code, 0, result)
        self.assertEqual(len(result["recorded_revision"]), 40)
        self.assertEqual(result["recorded_time_source"], "git_committer_assertion")
        self.conforms("knowledge-at", result)
        with contextlib.chdir(self.root):
            code, refused = call("knowledge-at", "--root", str(self.base / "absent-vault"),
                                 "--valid-on", dt.date.today().isoformat(), "--json")
        self.assertEqual(code, 2, refused)
        self.assertIn("error", refused)
        self.assertNotIn("recorded_revision", refused)

    def test_eval_threshold_and_invalid_inputs_follow_error_contract(self):
        status, result = call("agent", "eval", "--cases", "missing", "--responses", "missing", "--min-pass-rate", "nan", "--json")
        self.assertEqual(status, 2)
        self.assertEqual(result["error"]["code"], "invalid_argument")

    def test_claim_verify_requires_current_observed_source(self):
        shutil.copytree(ROOT / "examples/provenance", self.root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", ".whykit"))
        args = ["claim", "verify", "C-001", "--root", str(self.root), "--max-sensitivity", "restricted"]
        code, before = call(*args, "--today", "2026-09-17")
        self.assertEqual(code, 0, before)
        source = self.base / "changed-source.txt"
        source.write_text("Changed source observation.\n", encoding="utf-8")
        capture = ["artifact", "capture", "--root", str(self.root), "--evidence", "E-004",
                   "--source-file", str(source), "--recorded-at", "2026-09-18T00:00:00Z"]
        code, preview = call(*capture)
        self.assertEqual(code, 0, preview)
        code, applied = call(*capture, "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 0, applied)
        code, after = call(*args, "--today", "2026-09-18")
        self.assertEqual(code, 1, after)
        self.assertTrue(all(link["status"] == "matched" and link["source_state"] == "changed" for link in after["links"]))
        self.conforms("claim verify", after)

    def test_capture_claim_verify_workflow_and_mcp_dependency_floor(self):
        eid = create_evidence(self.root, source="Synthetic source", location="https://example.com/fixture",
                              kind="fixture", claims="A synthetic marker", sensitivity="public")
        source = self.base / "source.txt"
        quote = "PRIVATE-ANCHOR-CANARY"
        source.write_text(quote, encoding="utf-8")
        args = ["artifact", "capture", "--root", str(self.root), "--evidence", eid, "--source-file", str(source),
                "--recorded-at", "2026-10-27T12:00:00Z", "--json"]
        before = {str(f): f.read_bytes() for f in self.root.rglob("*") if f.is_file()}
        code, preview = call(*args)
        self.assertEqual(code, 0, preview)
        self.assertIs(preview["applied"], False)
        self.assertEqual(before, {str(f): f.read_bytes() for f in self.root.rglob("*") if f.is_file()})
        self.conforms("artifact capture", preview)
        code, captured = call(*args, "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 0, captured)
        self.assertIs(captured["applied"], True)
        self.assertNotIn(quote, json.dumps(captured))
        links = self.base / "links.json"
        links.write_text(json.dumps([{"evidence_id": eid, "source_version": captured["source_version"],
                                    "location": {"start_byte": 0, "end_byte": len(quote), "quote": quote},
                                    "assessment": {"kind": "human", "by": "Ada Example", "relation": "supports"}}]),
                         encoding="utf-8")
        args = ["claim", "create", "The fixture contains a marker", "--owner", "Ada Example", "--links", str(links),
                "--root", str(self.root), "--json"]
        code, preview = call(*args)
        self.assertEqual(code, 0, preview)
        self.assertIs(preview["applied"], False)
        self.conforms("claim create", preview)
        code, claim = call(*args, "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 0, claim)
        self.assertIs(claim["applied"], True)
        code, public = call("claim", "list", "--root", str(self.root), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(public["claims"], [])
        self.conforms("claim list", public)
        code, shown = call("claim", "show", claim["claim_id"], "--root", str(self.root), "--max-sensitivity", "restricted", "--json")
        self.assertEqual(code, 0, shown)
        self.conforms("claim show", shown)
        code, verification = call("claim", "verify", claim["claim_id"], "--root", str(self.root), "--max-sensitivity", "restricted", "--json")
        self.assertEqual(code, 0, verification)
        self.assertEqual(verification["links"][0]["status"], "matched")
        self.conforms("claim verify", verification)
        from whykit.mcp_server import VaultTools
        tools = VaultTools(self.root, max_sensitivity="restricted")
        self.assertTrue(tools.context(claim["path"])["exists"])
        register = self.root / "00-context/evidence-register.md"
        text = register.read_text(encoding="utf-8")
        self.assertIn("| public |", text)
        register.write_text(text.replace("| public |", "| unreadable-label |"), encoding="utf-8")
        self.assertFalse(tools.context(claim["path"])["exists"])
        self.assertEqual(tools.query(text=quote)["results"], [])

    def test_preflight_refuses_stale_or_incomplete_compiled_context(self):
        import shutil
        from whykit.compiled_context import compile_context, serialize_compiled
        root = self.base / "approved example"
        shutil.copytree(ROOT / "examples/approval", root)
        (root / "whykit.toml").write_text('[evidence_access_age_days]\nfixture = 90\n', encoding="utf-8")
        day = dt.date(2026, 9, 17)
        proposal = self.base / "proposal.json"
        proposal.write_text(json.dumps({"proposal_id": "example", "decision_ids": ["D-001"]}), encoding="utf-8")
        context_path = self.base / "compiled.json"
        scope = {"public", "internal", "confidential", "restricted"}
        context_path.write_text(serialize_compiled(compile_context(root, targets=["D-001"], today=day,
                                                                   allowed_sensitivities=scope)), encoding="utf-8")
        args = ["agent", "preflight", "--root", str(root), "--proposal", str(proposal), "--context", str(context_path),
                "--max-sensitivity", "restricted", "--today", day.isoformat(), "--json"]
        code, report = call(*args)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["context_verification"]["status"], "current")
        self.conforms("agent preflight", report)
        self.assertIs(report["execution_authorized"], False)
        context_path.write_text(serialize_compiled(compile_context(root, targets=["Home.md"], today=day,
                                                                   allowed_sensitivities=scope)), encoding="utf-8")
        code, report = call(*args)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["context_verification"]["status"], "current")
        self.assertIn("context_missing_decision", report["findings"][0]["problems"])
        context_path.write_text(serialize_compiled(compile_context(root, targets=["D-001"], today=day,
                                                                   allowed_sensitivities=scope)), encoding="utf-8")
        record = next((root / "06-decisions").glob("d-001-*.md"))
        record.write_text(record.read_text(encoding="utf-8") + "\nChanged rationale.\n", encoding="utf-8")
        code, report = call(*args)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["context_verification"]["status"], "stale")
        self.assertIn("context_not_current", report["findings"][0]["problems"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_input_fifos_are_refused_before_a_blocking_read(self):
        fifo = self.base / "input.json"
        os.mkfifo(fifo)
        for expression in ("from whykit.agent_eval import evaluate; evaluate(path, path)",
                           "from whykit.agent_preflight import read_proposal; read_proposal(path)"):
            with self.subTest(expression=expression):
                code = "from pathlib import Path; import sys; path=Path(sys.argv[1]); " + expression
                result = subprocess.run([sys.executable, "-c", code, str(fifo)],
                                        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                                        capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"OSError", result.stderr)
