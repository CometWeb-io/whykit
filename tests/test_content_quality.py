"""Content-quality checks: template placeholders, code-span citations, graph
export escaping and the review window wording."""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import random
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whykit import placeholders  # noqa: E402
from whykit import review as review_module  # noqa: E402
from whykit.context import build_context  # noqa: E402
from whykit.graph import as_dot, as_mermaid, build_graph  # noqa: E402
from whykit.impact import analyze_impact  # noqa: E402
from whykit.lint import lint  # noqa: E402
from whykit.query import query_vault  # noqa: E402
from whykit.scaffold import create_decision  # noqa: E402
from whykit.trace import build_trace  # noqa: E402
from _vaults import historical_decision  # noqa: E402

TINY = ROOT / "examples" / "tiny"
DAY = dt.date(2026, 9, 17)


class _TinyCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = (Path(self._tmp.name) / "vault").resolve()
        shutil.copytree(TINY, self.vault)

    def findings(self, code: str) -> list:
        _, found = lint(self.vault, today=DAY)
        return [item for item in found if item.code == code]

    def decision(self, title: str, *, status: str = "approved", **kwargs) -> Path:
        if status == "approved":
            kwargs.setdefault("review_by", "2027-01-01")
        factory = historical_decision if status == "approved" else create_decision
        _, path = factory(
            self.vault, title, owner="Example owner", status=status,
            source_ids=kwargs.pop("source_ids", ["E-001"]), today=DAY, **kwargs,
        )
        return path

    @staticmethod
    def fill(path: Path, replacements: dict[str, str]) -> None:
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


FILLED = {
    placeholders.SCAFFOLD_SECTION_PROMPTS["Context"]: "Support volume doubled after the pricing change.",
    placeholders.SCAFFOLD_SECTION_PROMPTS["Decision"]: "Route billing questions to the finance queue.",
    placeholders.SCAFFOLD_SECTION_PROMPTS["Rationale"]: "Finance resolves them in one reply (E-001).",
    placeholders.ALTERNATIVES_EMPTY_ROW: "| Keep one queue | Simple | Slow replies | Measured too slow |",
    "### Positive\n\n-\n": "### Positive\n\n- Faster billing replies.\n",
    "### Negative and trade-offs\n\n-\n": "### Negative and trade-offs\n\n- One more queue to staff.\n",
}


class DecisionPlaceholderTests(_TinyCopy):
    def test_scaffolded_record_is_flagged_for_every_unfinished_status(self) -> None:
        for status in ("approved", "in_review", "draft"):
            with self.subTest(status=status):
                path = self.decision(f"Choice {status}", status=status)
                hits = [f for f in self.findings("decision.placeholder") if f.path.endswith(path.name)]
                self.assertEqual(len(hits), 1, hits)
                self.assertEqual(hits[0].level, "warning")
                for section in ("Context", "Decision", "Rationale", "Alternatives considered", "Consequences"):
                    self.assertIn(f"`## {section}`", hits[0].message)
                # --source was given, so the Evidence section is real.
                self.assertNotIn("`## Evidence`", hits[0].message)
                self.assertTrue(hits[0].message.startswith(status))

    def test_evidence_todo_is_flagged_when_no_source_was_given(self) -> None:
        path = self.decision("No sources", source_ids=[])
        self.fill(path, FILLED)
        hits = self.findings("decision.placeholder")
        self.assertEqual([h.message.count("`## ") for h in hits], [1])
        self.assertIn("`## Evidence`", hits[0].message)
        line = path.read_text(encoding="utf-8").split("\n").index(placeholders.SCAFFOLD_EVIDENCE_TODO) + 1
        self.assertEqual(hits[0].line, line)

    def test_filled_record_is_clean_and_the_shipped_example_is_clean(self) -> None:
        path = self.decision("Finished")
        self.fill(path, FILLED)
        self.assertEqual(self.findings("decision.placeholder"), [])

    def test_history_and_templates_are_never_flagged(self) -> None:
        # D-001 in the tiny example is superseded and still holds the prompts:
        # history is not rewritten, so it must stay quiet.
        record = self.vault / "06-decisions" / "d-001-start-with-a-minimal-whykit-vault.md"
        self.assertIn(placeholders.SCAFFOLD_SECTION_PROMPTS["Decision"], record.read_text(encoding="utf-8"))
        for status in ("archived", "template"):
            path = self.decision(f"Old {status}", status="draft")
            self.fill(path, {"status: draft": f"status: {status}"})
        self.assertEqual(self.findings("decision.placeholder"), [])

    def test_prompts_quoted_in_code_are_not_leftovers(self) -> None:
        path = self.decision("Quotes the scaffold")
        replacements = dict(FILLED)
        replacements[placeholders.SCAFFOLD_SECTION_PROMPTS["Decision"]] = (
            "Keep the scaffold prompt `State the choice in one sentence.` in the docs.\n\n"
            "```text\nState the choice in one sentence.\n```"
        )
        self.fill(path, replacements)
        self.assertEqual(self.findings("decision.placeholder"), [])

    def test_a_section_holding_only_a_code_block_is_written(self) -> None:
        path = self.decision("Code only")
        replacements = dict(FILLED)
        replacements[placeholders.SCAFFOLD_SECTION_PROMPTS["Decision"]] = "```toml\nretention_days = 30\n```"
        self.fill(path, replacements)
        self.assertEqual(self.findings("decision.placeholder"), [])

    def test_prompt_left_above_real_prose_is_still_flagged(self) -> None:
        path = self.decision("Half done")
        replacements = dict(FILLED)
        prompt = placeholders.SCAFFOLD_SECTION_PROMPTS["Rationale"]
        replacements[prompt] = prompt + "\nBecause finance answers in one reply."
        self.fill(path, replacements)
        hits = self.findings("decision.placeholder")
        self.assertEqual(len(hits), 1)
        self.assertIn("`## Rationale`", hits[0].message)
        self.assertNotIn("`## Decision`", hits[0].message)

    def test_empty_prose_section_is_flagged(self) -> None:
        path = self.decision("Empty decision")
        replacements = dict(FILLED)
        replacements[placeholders.SCAFFOLD_SECTION_PROMPTS["Decision"]] = ""
        self.fill(path, replacements)
        hits = self.findings("decision.placeholder")
        self.assertEqual(len(hits), 1)
        self.assertIn("`## Decision`", hits[0].message)

    def test_one_option_can_be_stated_instead_of_filling_the_table(self) -> None:
        path = self.decision("Only option")
        replacements = dict(FILLED)
        replacements[placeholders.ALTERNATIVES_EMPTY_ROW] = (
            placeholders.ALTERNATIVES_EMPTY_ROW + "\n\nThere was only one supplier in the region."
        )
        self.fill(path, replacements)
        self.assertEqual(self.findings("decision.placeholder"), [])

    def test_hand_copied_template_prose_is_recognized(self) -> None:
        # The rule reads the shipped template, so its prompts are leftovers too.
        text = placeholders.TEMPLATE_PATH.read_text(encoding="utf-8")
        text = (
            text.replace("decision_id: D-XXX", "decision_id: D-002")
            .replace("status: template", "status: approved")
            .replace("owner: TODO", "owner: Example owner")
            .replace("YYYY-MM-DD", "2026-09-17")
            .replace("\nD-XXX\n", "\nD-002\n")
            .replace("tags: []\n", "tags: []\nreview_by: 2027-01-01\n")
        )
        (self.vault / "06-decisions" / "d-002-copied.md").write_text(text, encoding="utf-8")
        log = self.vault / "06-decisions" / "decision-log.md"
        log.write_text(log.read_text(encoding="utf-8").replace(
            "accepted | [[06-decisions/d-001",
            "superseded | [[06-decisions/d-001",
        ), encoding="utf-8")
        hits = [f for f in self.findings("decision.placeholder") if f.path.endswith("d-002-copied.md")]
        self.assertEqual(len(hits), 1)
        for section in ("Context", "Decision", "Rationale", "Alternatives considered", "Consequences"):
            self.assertIn(f"`## {section}`", hits[0].message)

    def test_rule_tracks_the_scaffold_and_the_template(self) -> None:
        known = placeholders.template_section_prompts()
        for section in placeholders.PROSE_SECTIONS:
            self.assertIn(placeholders.normalize(placeholders.SCAFFOLD_SECTION_PROMPTS[section]), known[section])
        # Every prose paragraph the template ships under a decision section is known.
        lines = placeholders.TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
        sections = {heading: (start, end) for heading, start, end in placeholders.split_sections(lines)}
        for section in placeholders.PROSE_SECTIONS:
            start, end = sections[section]
            for _, text in placeholders.paragraphs(lines[start:end]):
                self.assertIn(text, known[section])
        # And the scaffold really renders from the shared constants.
        body = self.decision("Rendered").read_text(encoding="utf-8")
        for prompt in placeholders.SCAFFOLD_SECTION_PROMPTS.values():
            self.assertIn(f"\n{prompt}\n", body)
        self.assertIn(placeholders.ALTERNATIVES_EMPTY_ROW, body)

    def test_adopted_records_without_scaffold_headings_are_left_alone(self) -> None:
        path = self.decision("Adopted")
        text = path.read_text(encoding="utf-8")
        head, _, _ = text.partition("# Decision record")
        path.write_text(head + "# ADR 7\n\nWe chose Postgres because the team knows it.\n", encoding="utf-8")
        self.assertEqual(self.findings("decision.placeholder"), [])


class CodeSpanCitationTests(_TinyCopy):
    """An E-NNN inside code is an example of the syntax, never a citation."""

    def setUp(self) -> None:
        super().setUp()
        register = self.vault / "00-context" / "evidence-register.md"
        text = register.read_text(encoding="utf-8")
        anchor = "| E-002 | Team interview 2026-09-10 |"
        row = next(line for line in text.splitlines() if line.startswith(anchor))
        extra = "\n".join(row.replace("E-002", eid) for eid in ("E-003", "E-004"))
        register.write_text(text.replace(row, row + "\n" + extra), encoding="utf-8")
        self.decision_path = self.decision("Cites in code", source_ids=[])
        self.fill(self.decision_path, {
            placeholders.SCAFFOLD_EVIDENCE_TODO: (
                "- Cite sources as `E-003`.\n\n```markdown\nsource_ids: [E-004]\n```"
            ),
        })
        note = self.vault / "notes" / "syntax.md"
        note.write_text(
            "---\ntitle: Citation syntax\ntype: guide\nstatus: draft\nowner: Example owner\n"
            "created: 2026-09-17\nlast_updated: 2026-09-17\nsource_of_truth: false\n"
            "sensitivity: internal\nsource_ids: []\ntags: []\n---\n\n"
            "Real citation: E-002.\n\nWrite `E-003` in prose.\n\n~~~\nE-004\n~~~\n\n[[06-decisions/"
            + self.decision_path.stem + "]]\n",
            encoding="utf-8",
        )

    def test_graph_impact_context_query_and_trace_agree(self) -> None:
        graph = build_graph(self.vault)
        cited = {(e["from"], e["to"]) for e in graph["edges"] if e["type"] == "evidence"}
        self.assertIn(("notes/syntax", "evidence:E-002"), cited)
        for eid in ("E-003", "E-004"):
            self.assertFalse(any(to == f"evidence:{eid}" for _, to in cited), eid)
            self.assertFalse(any(n["id"] == f"evidence:{eid}" for n in graph["nodes"]), eid)
            impact = analyze_impact(self.vault, eid)
            self.assertEqual(impact["reference_count"], 0, eid)
            self.assertEqual(query_vault(self.vault, source_id=eid)["total"], 0, eid)

        self.assertEqual(analyze_impact(self.vault, "notes/syntax")["evidence"], ["E-002"])
        self.assertEqual(analyze_impact(self.vault, "E-002")["reference_count"], 3)  # D-001, D-002, the note
        context = build_context(self.vault, "notes/syntax")
        self.assertEqual([item["id"] for item in context["evidence"]], ["E-002"])

        decision_id = "D-003"
        self.assertEqual(analyze_impact(self.vault, decision_id)["evidence"], [])
        trace = build_trace(self.vault, today=DAY, decision=decision_id)
        record = trace["decisions"][0]
        self.assertEqual(record["evidence"], [])
        self.assertIn("no_evidence", record["gaps"])

    def test_fact_callouts_use_the_same_rule(self) -> None:
        note = self.vault / "notes" / "syntax.md"
        note.write_text(note.read_text(encoding="utf-8") + "\n> [!fact] Cite it as `E-001`.\n", encoding="utf-8")
        codes = [f.code for f in lint(self.vault, today=DAY)[1] if f.path == "notes/syntax.md"]
        self.assertIn("fact.inline_evidence", codes)


def _dot_unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), value)


class GraphExportEscapingTests(_TinyCopy):
    ALPHABET = 'ab "\\<>#|;&{}[]—ł`\n\r\t\v\f\x00\x1b\x85\u2028\u2029'

    def titles(self) -> list[str]:
        rng = random.Random(20261003)
        fixed = ["Line one\nLine two", "CRLF\r\nend", "trailing backslash \\", "\\n is not a newline", "tab\there"]
        return fixed + ["".join(rng.choice(self.ALPHABET) for _ in range(16)) for _ in range(60)]

    def write_notes(self, titles: list[str]) -> None:
        for index, title in enumerate(titles):
            path = self.vault / "notes" / f"t{index}.md"
            path.write_text(
                f"---\ntitle: {json.dumps(title)}\ntype: guide\nstatus: draft\nowner: Example owner\n"
                "created: 2026-09-17\nlast_updated: 2026-09-17\nsource_of_truth: false\n"
                "sensitivity: internal\n---\n\n[[Home]]\n",
                encoding="utf-8",
            )

    def test_dot_keeps_one_statement_per_line(self) -> None:
        titles = self.titles()
        self.write_notes(titles)
        graph = build_graph(self.vault)
        dot = as_dot(graph)
        # Every reader's notion of a line, not only "\n".
        lines = dot.splitlines()
        self.assertEqual(len(lines), 3 + len(graph["nodes"]) + len(graph["edges"]))
        self.assertIsNone(re.search(r"[\x00-\x1f\x7f\x85\u2028\u2029]", dot.replace("\n", "")))
        string = r'"((?:[^"\\]|\\.)*)"'
        node_line = re.compile(rf"^  {string} \[shape=(box|ellipse), label={string}\];$")
        edge_line = re.compile(rf"^  {string} -> {string} \[label=\"(wikilink|evidence|supersedes)\"\];$")
        labels = {}
        for line in lines[2:-1]:
            match = node_line.match(line) or edge_line.match(line)
            self.assertIsNotNone(match, repr(line))
            if "shape=" in line:
                labels[_dot_unescape(match.group(1))] = _dot_unescape(match.group(3))
        for index, title in enumerate(titles):
            expected = re.sub(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", "\n", title)
            expected = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", " ", expected)
            self.assertEqual(labels[f"notes/t{index}"], expected, repr(title))

    def test_mermaid_keeps_one_statement_per_line(self) -> None:
        self.write_notes(self.titles())
        graph = build_graph(self.vault)
        text = as_mermaid(graph)
        self.assertIsNone(re.search(r"[\x00-\x1f\x7f\x85\u2028\u2029]", text.replace("\n", "")))
        statement = re.compile(r'^  (n\d+(\["[^"\n]*"\]|\(\["[^"\n]*"\]\))|n\d+ \S+\|\w+\| n\d+|classDef \w+ .+|class [n\d,]+ \w+)$')
        for line in text.splitlines()[1:]:
            self.assertRegex(line, statement)
        self.assertNotRegex(text, r"&(?!#)")


class ReviewWindowTests(_TinyCopy):
    def run_list(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review_module.main(["--root", str(self.vault), "list", *argv])
        self.assertEqual(code, 0)
        return out.getvalue().strip().splitlines()[-1]

    def test_empty_queue_names_the_window(self) -> None:
        self.assertEqual(
            self.run_list("--today", "2026-09-17", "--due-days", "10"),
            "0 review(s) overdue or due by 2026-09-27 (10 day(s) from 2026-09-17)",
        )
        self.assertEqual(
            self.run_list("--today", "2026-09-17", "--overdue-only", "--owner", "Nobody"),
            "0 review(s) overdue as of 2026-09-17, owner matching 'Nobody'",
        )

    def test_default_window_comes_from_policy(self) -> None:
        line = self.run_list("--today", "2027-03-01")
        self.assertEqual(line, "1 review(s) overdue or due by 2027-03-31 (30 day(s) from 2027-03-01)")


if __name__ == "__main__":
    unittest.main()
