"""Graph export edge cases: odd links, retired evidence, every output format."""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from whykit import graph as graph_module
from whykit.graph import as_dot, as_mermaid, as_obsidian, build_graph

ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "examples" / "tiny"

FRONT = """---
title: {title}
type: guide
status: {status}
owner: Example owner
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: {sot}
sensitivity: internal
{extra}---

"""


class GraphEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = (Path(self._tmp.name) / "tiny")
        shutil.copytree(TINY, self.vault)
        self.vault = self.vault.resolve()
        register = self.vault / "00-context" / "evidence-register.md"
        register.write_text(
            register.read_text(encoding="utf-8").replace(
                "|  |  |  |  |  |",
                "| E-003 | Old survey | 2026-09-01 | Sample too small | E-002 |",
            ),
            encoding="utf-8",
        )

    def note(self, relative: str, body: str, *, title: str = "Note", status: str = "draft", sot: str = "false", extra: str = "") -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FRONT.format(title=title, status=status, sot=sot, extra=extra) + body, encoding="utf-8")

    def test_odd_wikilinks(self) -> None:
        self.note("notes/odd.md", (
            "[[ ]] and [[https://example.com/page]] are not vault links.\n"
            "[[Home#Read first|alias]] keeps only the target.\n"
            "[[nowhere]] is missing.\n"
            "`[[in-inline-code]]`\n\n```\n[[in-a-fence]]\n```\n"
            "[[odd]] links to itself, twice: [[odd]].\n"
        ))
        self.note("notes/sub/dup.md", "x")
        self.note("reports/dup.md", "x")
        self.note("notes/ambiguous.md", "[[dup]]")
        graph = build_graph(self.vault)
        edges = {(e["from"], e["to"], e["type"]) for e in graph["edges"]}
        self.assertIn(("notes/odd", "Home", "wikilink"), edges)
        self.assertEqual(sum(1 for e in graph["edges"] if e["from"] == e["to"] == "notes/odd"), 1)
        unresolved = {(u["from"], u["target"], u["reason"]) for u in graph["unresolved"]}
        self.assertIn(("notes/odd", "nowhere", "missing"), unresolved)
        self.assertIn(("notes/ambiguous", "dup", "ambiguous"), unresolved)
        self.assertFalse(any("code" in u["target"] or "fence" in u["target"] or "http" in u["target"] for u in graph["unresolved"]))

    def test_evidence_nodes_carry_register_status(self) -> None:
        self.note("notes/cites.md", "Backed by E-001, E-003 and E-404.\n")
        graph = build_graph(self.vault)
        evidence = {n["evidence_id"]: n for n in graph["nodes"] if n["kind"] == "evidence"}
        self.assertEqual(evidence["E-001"]["status"], "active")
        self.assertEqual(evidence["E-003"]["status"], "retired")
        self.assertEqual(evidence["E-404"]["status"], "missing")
        self.assertEqual(evidence["E-404"]["title"], "E-404")
        self.assertFalse(any(e["from"] == "00-context/evidence-register" and e["type"] == "evidence" for e in graph["edges"]))
        self.assertEqual(graph["stats"]["evidence"], len(evidence))

    def test_supersedes_edge_cases(self) -> None:
        self.note("06-decisions/d-002-next.md", "Replaces D-001.\n", extra="decision_id: D-002\nsupersedes: D-001\n")
        self.note("06-decisions/d-003-orphan.md", "x\n", extra="decision_id: D-003\nsupersedes: D-099\n")
        self.note("06-decisions/d-004-junk.md", "x\n", extra="decision_id: D-004\nsupersedes: not-an-id\n")
        graph = build_graph(self.vault)
        self.assertIn(
            {"from": "06-decisions/d-002-next", "to": "06-decisions/d-001-start-with-a-minimal-whykit-vault", "type": "supersedes"},
            graph["edges"],
        )
        self.assertIn(
            {"from": "06-decisions/d-003-orphan", "target": "D-099", "type": "supersedes", "reason": "missing"},
            graph["unresolved"],
        )
        self.assertFalse(any(u["from"] == "06-decisions/d-004-junk" for u in graph["unresolved"]))

    def test_canonical_only_drops_edges_to_excluded_documents(self) -> None:
        self.note("notes/canon.md", "[[Home]] [[notes/draft]] E-001\n", title="Canon", status="approved", sot="true")
        self.note("notes/draft.md", "x\n")
        self.note("06-decisions/d-002-next.md", "x\n", status="approved", sot="true", extra="decision_id: D-002\nsupersedes: D-001\n")
        graph = build_graph(self.vault, canonical_only=True)
        ids = {n["id"] for n in graph["nodes"]}
        self.assertEqual({i for i in ids if not i.startswith("evidence:")}, {"notes/canon", "06-decisions/d-002-next", "INTEROP", "OBSIDIAN"})
        self.assertNotIn("notes/draft", ids)
        for edge in graph["edges"]:
            self.assertIn(edge["to"], ids)
        self.assertIn({"from": "notes/canon", "to": "evidence:E-001", "type": "evidence"}, graph["edges"])

    def test_dot_output_is_well_formed_for_hostile_titles(self) -> None:
        rng = random.Random(99)
        alphabet = 'ab "\\<>#|;{}[]—ł'
        titles = ['"quoted" \\ back', *("".join(rng.choice(alphabet) for _ in range(12)) for _ in range(30))]
        for index, title in enumerate(titles):
            self.note(f"notes/t{index}.md", "[[Home]]\n", title=json.dumps(title))
        graph = build_graph(self.vault)
        dot = as_dot(graph)
        lines = dot.splitlines()
        self.assertEqual((lines[0], lines[-1]), ("digraph whykit {", "}"))
        node_line = re.compile(r'^  "((?:[^"\\]|\\.)*)" \[shape=(box|ellipse), label="((?:[^"\\]|\\.)*)"\];$')
        edge_line = re.compile(r'^  "((?:[^"\\]|\\.)*)" -> "((?:[^"\\]|\\.)*)" \[label="(wikilink|evidence|supersedes)"\];$')
        labels = []
        for line in lines[2:-1]:
            match = node_line.match(line) or edge_line.match(line)
            self.assertIsNotNone(match, line)
            if line.lstrip().startswith('"') and "shape=" in line:
                labels.append(re.sub(r"\\(.)", r"\1", match.group(3)))
        for title in titles:
            self.assertIn(title, labels)

    def test_mermaid_escapes_and_skips_dangling_edges(self) -> None:
        graph = {
            "nodes": [
                {"id": "a", "kind": "document", "title": 'A "quoted" <b>#1</b>\nsecond line', "decision_id": "D-001"},
                {"id": "evidence:E-001", "kind": "evidence", "title": "Src", "status": "active"},
                {"id": "evidence:E-002", "kind": "evidence", "title": "Old", "status": "retired"},
            ],
            "edges": [
                {"from": "a", "to": "evidence:E-001", "type": "evidence"},
                {"from": "a", "to": "gone", "type": "wikilink"},
                {"from": "a", "to": "a", "type": "custom"},
            ],
        }
        text = as_mermaid(graph)
        self.assertNotIn('"quoted"', text)
        self.assertIn("#quot;quoted#quot;", text)
        self.assertIn("#lt;b#gt;#35;1", text)
        self.assertNotIn("\nsecond", text)
        self.assertNotIn("gone", text)
        self.assertIn("n0 -.->|evidence| n1", text)
        self.assertIn("n0 -->|custom| n0", text)
        self.assertIn("class n1 evidence", text)
        self.assertIn("class n2 unusable", text)
        self.assertIn("class n0 decision", text)

    def test_obsidian_payload(self) -> None:
        payload = as_obsidian(build_graph(self.vault))
        self.assertEqual(payload["format"], "whykit.obsidian-graph/v1")
        for node in payload["nodes"]:
            if node["kind"] == "evidence":
                self.assertIsNone(node["path"])
            else:
                self.assertTrue((self.vault / node["path"]).is_file(), node["path"])


class GraphCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "tiny"
        shutil.copytree(TINY, self.vault)

    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = graph_module.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_every_format_renders(self) -> None:
        for fmt, marker in (("json", '"contract_version"'), ("dot", "digraph whykit {"), ("mermaid", "flowchart LR"), ("obsidian", "whykit.obsidian-graph/v1")):
            with self.subTest(fmt):
                code, out, _ = self.main("--root", str(self.vault), "--format", fmt)
                self.assertEqual(code, 0)
                self.assertIn(marker, out)

    def test_output_paths(self) -> None:
        code, out, _ = self.main("--root", str(self.vault), "--format", "dot", "--output", "reports/graph.dot")
        self.assertEqual(code, 0)
        self.assertIn("wrote reports/graph.dot", out)
        self.assertTrue((self.vault / "reports/graph.dot").read_text(encoding="utf-8").endswith("}\n"))
        absolute = self.vault.resolve() / "reports" / "graph.json"
        self.assertEqual(self.main("--root", str(self.vault), "--output", str(absolute))[0], 0)
        self.assertEqual(json.loads(absolute.read_text(encoding="utf-8"))["contract_version"], 1)

    def test_output_through_a_symlinked_root_spelling(self) -> None:
        alias = Path(self._tmp.name) / "alias"
        try:
            alias.symlink_to(self.vault, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        code, _, err = self.main("--root", str(alias), "--output", str(alias / "reports" / "g.json"))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.vault / "reports" / "g.json").is_file())

    def test_unsafe_outputs_are_refused(self) -> None:
        outside = Path(self._tmp.name) / "outside.json"
        for target in (str(outside), "../escape.json"):
            with self.subTest(target):
                code, _, err = self.main("--root", str(self.vault), "--output", target)
                self.assertEqual(code, 2)
                self.assertIn("--output must be a safe path inside the vault", err)
        self.assertFalse(outside.exists())
        if os.name != "nt":
            (self.vault / "reports" / "link.json").symlink_to(outside)
            code, _, _ = self.main("--root", str(self.vault), "--output", "reports/link.json")
            self.assertEqual(code, 2)
            self.assertFalse(outside.exists())

    def test_no_vault(self) -> None:
        code, _, err = self.main("--root", self._tmp.name)
        self.assertEqual(code, 2)
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
