"""Knowledge-graph correctness, performance contracts and traceability."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
EXAMPLE = ROOT / "examples" / "northline"
sys.path.insert(0, str(ROOT / "src"))

from whykit import graph as graph_module  # noqa: E402
from whykit import impact as impact_module  # noqa: E402
from whykit import lint as lint_module  # noqa: E402
from whykit import snapshot as snapshot_module  # noqa: E402
from whykit.backlinks import build_backlinks  # noqa: E402
from whykit.evidence import retire_evidence  # noqa: E402
from whykit.graph import as_mermaid, build_graph  # noqa: E402
from whykit.impact import analyze_impact  # noqa: E402
from whykit.trace import build_trace  # noqa: E402
from whykit.vault_index import VaultIndex  # noqa: E402

TODAY = dt.date(2026, 9, 17)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)


def _set_front(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            lines[index] = f"{key}: {value}"
            break
    else:
        lines.insert(1, f"{key}: {value}")
    path.write_text("\n".join(lines), encoding="utf-8")


class VaultCopyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "northline"
        shutil.copytree(EXAMPLE, self.vault)
        self.vault = self.vault.resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()


class GraphCorrectnessTests(VaultCopyTestCase):
    def test_memoised_resolver_matches_linter_resolution(self) -> None:
        index = VaultIndex.load(self.vault)
        dup = self.vault / "07-research" / "qualification.md"
        dup.write_text("---\ntitle: Dup\n---\n", encoding="utf-8")
        index = VaultIndex.load(self.vault)
        for target in ("qualification", "05-operations/qualification", "D-007", "icp", "nope", "../escape"):
            expected = lint_module._resolve(self.vault, target, index.link_index)
            self.assertEqual(index.resolve_link(target), expected, target)
            self.assertEqual(index.resolve_link(target), expected, f"{target} (cached)")
        self.assertEqual(index.resolve_link("qualification"), (None, True))

    def test_graph_resolves_each_distinct_target_once(self) -> None:
        index = VaultIndex.load(self.vault)
        with mock.patch("whykit.vault_index._resolve", wraps=lint_module._resolve) as resolver:
            graph = build_graph(self.vault, vault=index)
        targets = [call.args[1] for call in resolver.call_args_list]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertGreater(graph["stats"]["edges_by_type"]["wikilink"], len(targets) - len(graph["unresolved"]) - 1)

    def test_canonical_only_graph_never_points_at_excluded_nodes(self) -> None:
        # D-011 supersedes D-010; make only the successor canonical.
        _set_front(self.vault / "06-decisions" / "d-011-direct-with-evidence.md", "source_of_truth", "true")
        graph = build_graph(self.vault, canonical_only=True)
        ids = {node["id"] for node in graph["nodes"]}
        self.assertIn("06-decisions/d-011-direct-with-evidence", ids)
        self.assertNotIn("06-decisions/d-010-direct", ids)
        for edge in graph["edges"]:
            self.assertIn(edge["from"], ids, edge)
            self.assertIn(edge["to"], ids, edge)
        full = build_graph(self.vault)
        self.assertIn(
            {"from": "06-decisions/d-011-direct-with-evidence", "to": "06-decisions/d-010-direct", "type": "supersedes"},
            full["edges"],
        )

    def test_supersedes_pointing_at_duplicate_decision_id_is_ambiguous(self) -> None:
        clone = self.vault / "07-research" / "d-009-copy.md"
        clone.write_text("---\ntitle: Copy\ndecision_id: D-009\n---\n", encoding="utf-8")
        graph = build_graph(self.vault)
        supersedes = [edge for edge in graph["edges"] if edge["type"] == "supersedes"]
        self.assertEqual(
            supersedes,
            [{"from": "06-decisions/d-011-direct-with-evidence", "to": "06-decisions/d-010-direct", "type": "supersedes"}],
        )
        self.assertIn(
            {"from": "06-decisions/d-010-direct", "target": "D-009", "type": "supersedes", "reason": "ambiguous"},
            graph["unresolved"],
        )


class BacklinksTests(VaultCopyTestCase):
    def test_bare_stem_finds_nested_note(self) -> None:
        report = build_backlinks(self.vault, "qualification")
        self.assertTrue(report["exists"])
        self.assertEqual(report["id"], "05-operations/qualification")
        self.assertIn("06-decisions/d-007-hubspot", {item["from"] for item in report["backlinks"]})

    def test_alias_and_absolute_path_resolve_to_same_node(self) -> None:
        by_alias = build_backlinks(self.vault, "D-007")
        by_path = build_backlinks(self.vault, str(self.vault / "06-decisions" / "d-007-hubspot.md"))
        by_rel = build_backlinks(self.vault, "06-decisions/d-007-hubspot.md")
        self.assertEqual(by_alias["id"], "06-decisions/d-007-hubspot")
        self.assertEqual(by_path["id"], by_alias["id"])
        self.assertEqual(by_rel["id"], by_alias["id"])
        self.assertTrue(by_path["exists"])

    def test_ambiguous_stem_is_not_silently_picked(self) -> None:
        (self.vault / "07-research" / "qualification.md").write_text("---\ntitle: Dup\n---\n", encoding="utf-8")
        report = build_backlinks(self.vault, "qualification")
        self.assertFalse(report["exists"])

    def test_cli_backlinks_by_stem_exits_zero(self) -> None:
        result = run("backlinks", "qualification", "--root", str(self.vault), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], "05-operations/qualification")


class SharedParseTests(VaultCopyTestCase):
    def test_evidence_impact_does_not_build_the_graph(self) -> None:
        with mock.patch.object(impact_module, "build_graph", side_effect=AssertionError("graph built")):
            report = analyze_impact(self.vault, "E-004")
        self.assertTrue(report["exists"])
        self.assertGreater(report["reference_count"], 0)

    def test_retire_reports_reference_count_without_graph(self) -> None:
        with mock.patch.object(impact_module, "build_graph", side_effect=AssertionError("graph built")):
            payload = retire_evidence(self.vault, "E-004", reason="superseded survey", replaced_by="E-005", today=TODAY)
        self.assertGreater(payload["references"], 0)

    def test_snapshot_parses_the_vault_once(self) -> None:
        # Status, graph and the file table all used to call VaultIndex.load.
        self.assertIs(snapshot_module.VaultIndex, graph_module.VaultIndex)
        real_load = VaultIndex.load
        with mock.patch.object(VaultIndex, "load", side_effect=real_load) as loader, \
                mock.patch.object(snapshot_module, "load_note", side_effect=AssertionError("note reparsed")):
            first = snapshot_module.build_snapshot(self.vault, today=TODAY)
        self.assertEqual(loader.call_count, 1)
        second = snapshot_module.build_snapshot(self.vault, today=TODAY)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["documents"], len(VaultIndex.load(self.vault).notes))


class MermaidTests(VaultCopyTestCase):
    def test_mermaid_escapes_labels_and_declares_every_endpoint(self) -> None:
        graph = {
            "nodes": [
                {"id": "a/b c", "kind": "document", "title": 'Say "hi" <now> #1', "decision_id": "D-001"},
                {"id": "evidence:E-001", "kind": "evidence", "title": "Src", "status": "retired"},
                {"id": "z", "kind": "document", "title": "Z"},
            ],
            "edges": [
                {"from": "a/b c", "to": "evidence:E-001", "type": "evidence"},
                {"from": "a/b c", "to": "z", "type": "supersedes"},
                {"from": "z", "to": "a/b c", "type": "wikilink"},
                {"from": "z", "to": "not-a-node", "type": "wikilink"},
            ],
        }
        text = as_mermaid(graph)
        lines = text.splitlines()
        self.assertEqual(lines[0], "flowchart LR")
        self.assertIn('  n0["Say #quot;hi#quot; #lt;now#gt; #35;1<br/><small>a/b c</small>"]', lines)
        self.assertIn('  n1(["Src"])', lines)
        self.assertIn("  n0 -.->|evidence| n1", lines)
        self.assertIn("  n0 ==>|supersedes| n2", lines)
        self.assertIn("  n2 -->|wikilink| n0", lines)
        self.assertNotIn("not-a-node", text)
        self.assertIn("  class n1 unusable", lines)
        self.assertIn("  class n0 decision", lines)
        self.assertNotIn('"hi"', text)

    def test_cli_mermaid_export(self) -> None:
        result = run("graph", "--root", str(self.vault), "--format", "mermaid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("flowchart LR\n"))
        self.assertIn("==>|supersedes|", result.stdout)


class TraceTests(VaultCopyTestCase):
    def _record(self, report: dict, decision_id: str) -> dict:
        return next(item for item in report["decisions"] if item["decision_id"] == decision_id)

    def test_clean_example_has_no_stale_or_retired_gaps_without_policy(self) -> None:
        report = build_trace(self.vault, today=TODAY)
        self.assertEqual(report["summary"]["decisions"], 11)
        self.assertEqual(report["summary"]["live"], 9)
        self.assertEqual(report["summary"]["gaps"]["stale_evidence"], 0)
        self.assertEqual(report["summary"]["gaps"]["retired_evidence"], 0)
        d009 = self._record(report, "D-009")
        self.assertFalse(d009["live"])
        self.assertEqual(d009["superseded_by"], ["D-010"])

    def test_direct_and_inherited_evidence(self) -> None:
        report = build_trace(self.vault, today=TODAY)
        d007 = self._record(report, "D-007")
        self.assertEqual({item["id"] for item in d007["evidence"]}, {"E-002", "E-004"})
        self.assertTrue(all(item["via"] == "05-operations/qualification" for item in d007["evidence"]))
        d002 = self._record(report, "D-002")
        self.assertTrue(all(item["via"] is None for item in d002["evidence"]))

    def _strip_d011_evidence(self) -> None:
        decision = self.vault / "06-decisions" / "d-011-direct-with-evidence.md"
        text = decision.read_text(encoding="utf-8")
        text = text.replace('source_ids: ["E-013"]', "source_ids: []", 1).replace(" [E-013]", "").replace("- E-013 — ", "- ")
        decision.write_text(text, encoding="utf-8")

    def test_example_decisions_all_cite_evidence(self) -> None:
        report = build_trace(self.vault, today=TODAY)
        self.assertEqual(report["summary"]["gaps"]["no_evidence"], 0)
        d011 = self._record(report, "D-011")
        self.assertEqual({item["id"] for item in d011["evidence"]}, {"E-013"})
        self.assertEqual(d011["gaps"], [])
        self.assertEqual(self._record(report, "D-010")["superseded_by"], ["D-011"])

    def test_successor_does_not_inherit_evidence_from_superseded_decision(self) -> None:
        # D-009 cites E-013 too; once D-011 drops its own citation it must not borrow it up the chain.
        self._strip_d011_evidence()
        report = build_trace(self.vault, today=TODAY)
        d011 = self._record(report, "D-011")
        self.assertEqual(d011["evidence"], [])
        self.assertEqual(d011["gaps"], ["no_evidence"])

    def test_retired_missing_and_stale_evidence_are_flagged(self) -> None:
        retire_evidence(self.vault, "E-004", reason="superseded survey", replaced_by="E-005", today=TODAY)
        decision = self.vault / "06-decisions" / "d-004-english.md"
        decision.write_text(decision.read_text(encoding="utf-8") + "\nAlso see E-999.\n", encoding="utf-8")
        report = build_trace(self.vault, today=TODAY, access_age_days={"interview": 30})
        d002 = self._record(report, "D-002")
        by_id = {item["id"]: item for item in d002["evidence"]}
        self.assertEqual(by_id["E-004"]["state"], "retired")
        self.assertEqual(by_id["E-004"]["replaced_by"], "E-005")
        self.assertTrue(by_id["E-002"]["stale"])
        self.assertEqual(by_id["E-002"]["max_age_days"], 30)
        self.assertIn("retired_evidence", d002["gaps"])
        self.assertIn("stale_evidence", d002["gaps"])
        d004 = self._record(report, "D-004")
        self.assertIn("missing_evidence", d004["gaps"])
        self.assertEqual({item["id"]: item["state"] for item in d004["evidence"]}["E-999"], "missing")

    def test_policy_window_matches_lint_boundary(self) -> None:
        # E-002 was accessed 2026-07-02: exactly 77 days before TODAY.
        at_limit = build_trace(self.vault, today=TODAY, access_age_days={"interview": 77})
        over = build_trace(self.vault, today=TODAY, access_age_days={"interview": 76})
        self.assertFalse({i["id"]: i for i in self._record(at_limit, "D-002")["evidence"]}["E-002"]["stale"])
        self.assertTrue({i["id"]: i for i in self._record(over, "D-002")["evidence"]}["E-002"]["stale"])

    def test_future_access_date_is_not_stale(self) -> None:
        report = build_trace(self.vault, today=dt.date(2026, 1, 1), default_max_age=0)
        self.assertEqual(report["summary"]["gaps"]["stale_evidence"], 0)

    def test_policy_from_whykit_toml(self) -> None:
        config = self.vault / "whykit.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "format_version = 1\n", "format_version = 1\n\n[evidence_access_age_days]\ninterview = 30\n", 1
            ),
            encoding="utf-8",
        )
        report = build_trace(self.vault, today=TODAY)
        self.assertIn("stale_evidence", self._record(report, "D-002")["gaps"])
        self.assertNotIn("stale_evidence", self._record(report, "D-003")["gaps"])

    def test_cli_strict_gaps_only_and_unknown_decision(self) -> None:
        ok = run("trace", "--root", str(self.vault), "--today", "2026-09-17", "--decision", "D-002", "--strict")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("D-002", ok.stdout)
        clean = run("trace", "--root", str(self.vault), "--today", "2026-09-17", "--gaps-only", "--strict", "--json")
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertEqual(json.loads(clean.stdout)["decisions"], [])
        self._strip_d011_evidence()
        gaps = run("trace", "--root", str(self.vault), "--today", "2026-09-17", "--gaps-only", "--strict", "--json")
        self.assertEqual(gaps.returncode, 1, gaps.stderr)
        payload = json.loads(gaps.stdout)
        self.assertEqual([item["decision_id"] for item in payload["decisions"]], ["D-011"])
        missing = run("trace", "--root", str(self.vault), "--decision", "D-404")
        self.assertEqual(missing.returncode, 1)
        bad = run("trace", "--root", str(self.vault), "--decision", "E-001")
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
