"""The opt-in hybrid route and compiler share a bounded captured scope."""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from _vaults import ROOT
from test_compiled_context import note
from test_workflow_cli import call
from whykit.compiled_context import _seal, compile_context, serialize_compiled, verify_compiled
from whykit.vault_index import VaultIndex

DAY = dt.date(2026, 9, 17)


class HybridRetrievalCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "vault with spaces"
        self.root.mkdir()
        (self.root / "00-context").mkdir()
        note(self.root, "Home.md", "Vault map")
        note(self.root, "notes/seed.md", "quartzsignal [[notes/neighbor]]", title="Entry point")
        note(self.root, "notes/neighbor.md", "Operational procedure 🛰️", title="Related instructions")

    def schema(self, name, report):
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])

    def test_public_retrieve_route_reports_graph_proximity_without_authority(self):
        code, report = call("retrieve", "quartzsignal", "--root", str(self.root),
                            "--valid-on", DAY.isoformat(), "--limit", "2", "--json")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["format"], "whykit.hybrid-retrieval/v1")
        self.assertEqual([row["path"] for row in report["results"]], ["notes/seed.md", "notes/neighbor.md"])
        self.assertGreater(report["results"][1]["graph_support"], 0)
        self.assertEqual(report["results"][1]["explanation"]["seed"], "notes/seed.md")
        self.assertIs(report["execution_authorized"], False)
        self.assertIs(report["semantics_checked"], False)
        self.schema("hybrid-retrieval.schema.json", report)
        _, filtered = call("--root", str(self.root), "retrieve", "quartzsignal", "--valid-on", DAY.isoformat(),
                           "--type", "decision", "--json")
        self.assertEqual(filtered["results"], [])

    def test_invalid_date_and_limit_are_machine_errors(self):
        for arguments in (("--valid-on", "2026-02-30"),
                          ("--valid-on", DAY.isoformat(), "--limit", "101"),
                          ("--valid-on", DAY.isoformat(), "--limit", "-1"),
                          ("--valid-on", DAY.isoformat(), "--source", "E-1")):
            with self.subTest(arguments=arguments):
                code, report = call("retrieve", "quartzsignal", "--root", str(self.root), *arguments, "--json")
                self.assertEqual(code, 2, report)
                self.assertEqual(report["error"]["code"], "invalid_argument")
                self.schema("error.schema.json", report)

    def test_compiler_exposes_a_bounded_construction_refusal(self):
        with patch("whykit.hybrid_retrieval.MAX_CONSTRUCTION_NODES", 1):
            refused = compile_context(self.root, query="quartzsignal", ranking="hybrid", today=DAY)
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(refused["error"], "scope_too_large")
        self.assertNotIn("contexts", refused)
        self.schema("compiled-context.schema.json", refused)

    def test_explicit_match_beyond_retrieval_result_limit_is_not_counted_as_omitted(self):
        for number in range(105):
            note(self.root, f"mass/g{number:03d}.md", "masssignal")
        report = compile_context(self.root, targets=["mass/g104.md"], query="masssignal", ranking="hybrid",
                                 today=DAY, max_docs=2, max_bytes=32768)
        self.assertEqual(report["status"], "compiled")
        self.assertEqual({row["record"]["path"] for row in report["contexts"]}, {"mass/g104.md", "mass/g000.md"})
        self.assertEqual(report["omissions"]["query_matches"], 103)
        self.schema("compiled-context.schema.json", report)

    def test_compiler_explanations_are_budgeted_bound_and_reverified(self):
        index = VaultIndex.load(self.root)
        with patch("whykit.vault_index.VaultIndex.load", side_effect=AssertionError("scope reload")):
            compiled = compile_context(self.root, query="quartzsignal", ranking="hybrid", today=DAY,
                                       max_docs=2, max_bytes=16384, vault=index)
        self.assertEqual(compiled["status"], "compiled")
        self.assertEqual(len(compiled["contexts"]), 2)
        self.assertIn("retrieval", compiled)
        neighbor = next(row for row in compiled["contexts"] if row["record"]["path"] == "notes/neighbor.md")
        self.assertEqual(neighbor["retrieval"]["explanation"]["seed"], "notes/seed.md")
        wire = serialize_compiled(compiled)
        self.assertEqual(compiled["budget"]["used_bytes"], len(wire.encode("utf-8")))
        self.assertLessEqual(len(wire.encode("utf-8")), 16384)
        self.assertEqual(verify_compiled(self.root, wire, today=DAY)["status"], "current")
        self.schema("compiled-context.schema.json", compiled)
        forged = copy.deepcopy(compiled)
        forged["contexts"][1]["retrieval"]["graph_support"] += 0.01
        _seal(forged)
        self.assertEqual(verify_compiled(self.root, forged, today=DAY)["status"], "stale")
        hidden = note(self.root, "notes/hidden.md", "quartzsignal [[notes/neighbor]] PRIVATECANARY", sensitivity="restricted")
        self.assertEqual(compile_context(self.root, query="quartzsignal", ranking="hybrid", today=DAY,
                                         max_docs=2, max_bytes=16384), compiled)
        hidden.unlink()
        tiny = compile_context(self.root, query="quartzsignal", ranking="hybrid", today=DAY, max_bytes=160)
        self.assertEqual(tiny["status"], "refused")
        self.assertLessEqual(len(serialize_compiled(tiny).encode("utf-8")), 160)
        for ranking in ("substring", "bm25"):
            legacy = compile_context(self.root, query="quartzsignal", ranking=ranking, today=DAY)
            self.assertNotIn("retrieval", legacy)
            self.assertTrue(all("retrieval" not in row for row in legacy["contexts"]))

    def test_cli_recipe_has_current_context(self):
        code, report = call("compile-context", "--query", "quartzsignal", "--ranking", "hybrid",
                            "--root", str(self.root), "--today", DAY.isoformat(), "--max-docs", "2")
        self.assertEqual(code, 0, report)
        target = self.root.parent / "compiled.json"
        target.write_text(serialize_compiled(report), encoding="utf-8")
        code, verification = call("verify-context", str(target), "--root", str(self.root), "--today", DAY.isoformat())
        self.assertEqual(code, 0, verification)
        self.assertEqual(verification["status"], "current")
