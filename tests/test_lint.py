from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whykit import lint as lint_mod  # noqa: E402


def front(
    title: str,
    *,
    doc_type: str = "guide",
    status: str = "approved",
    source_of_truth: bool = False,
    source_ids: list[str] | None = None,
    aliases: list[str] | None = None,
    extra: str = "",
) -> str:
    aliases = aliases or []
    source_ids = source_ids or []
    alias_yaml = "[]" if not aliases else "[" + ", ".join(aliases) + "]"
    source_yaml = "[]" if not source_ids else "[" + ", ".join(source_ids) + "]"
    return f"""---
title: {title}
aliases: {alias_yaml}
type: {doc_type}
status: {status}
owner: Test owner
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: {str(source_of_truth).lower()}
sensitivity: internal
source_ids: {source_yaml}
tags: []
{extra}---

# {title}
"""


class VaultTestCase(unittest.TestCase):
    """Shared scaffolding. Subclass this, not a sibling suite, or its tests re-run."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def codes(self, *, secrets: bool = False) -> list[str]:
        _, findings = lint_mod.lint(
            self.root, orphans=False, secrets=secrets
        )
        return [f.code for f in findings]


class LintRegressionTests(VaultTestCase):
    def test_naming_file_without_front_matter_is_not_special_cased(self) -> None:
        self.write("NAMING.md", "# Naming notes\n\nUse a consistent vocabulary.\n")

        self.assertIn("frontmatter.missing", self.codes())

    def test_unlinked_naming_file_is_reported_as_orphan(self) -> None:
        self.write("NAMING.md", front("Naming notes"))
        _, findings = lint_mod.lint(self.root, orphans=True, secrets=False)

        self.assertIn("note.orphan", [finding.code for finding in findings])

    def test_supersedes_reference_is_not_a_duplicate_decision_id(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\n")
            + "\n## Decision ID\n\nD-001\n",
        )
        self.write(
            "06-decisions/d-002-second.md",
            front(
                "Second",
                doc_type="decision",
                extra="decision_id: D-002\nsupersedes: D-001\n",
            )
            + "\n## Decision ID\n\nD-002\n\nSupersedes D-001.\n",
        )
        self.assertNotIn("decision.duplicate", self.codes())

    def test_draft_replacement_does_not_prematurely_supersede_approved_predecessor(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", status="approved", extra="decision_id: D-001\nreview_by: 2027-01-01\n"),
        )
        self.write(
            "06-decisions/d-002-second.md",
            front("Second", doc_type="decision", status="draft", extra="decision_id: D-002\nsupersedes: D-001\n"),
        )
        self.write(
            "06-decisions/decision-log.md",
            front("Decision log", doc_type="decision")
            + "\n| ID | Decision | Date | Owner | Status | Record |\n"
              "|---|---|---|---|---|---|\n"
              "| D-001 | First | 2026-09-17 | Test owner | accepted | [[06-decisions/d-001-first]] |\n"
              "| D-002 | Second | 2026-09-17 | Test owner | proposed | [[06-decisions/d-002-second]] |\n",
        )
        self.assertNotIn("decision.supersedes_status", self.codes())

    def test_multiple_approved_replacements_are_rejected_as_a_fork(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", status="superseded", extra="decision_id: D-001\nsuperseded_by: D-002\n"),
        )
        self.write(
            "06-decisions/d-002-second.md",
            front("Second", doc_type="decision", status="approved", extra="decision_id: D-002\nsupersedes: D-001\nreview_by: 2027-01-01\n"),
        )
        self.write(
            "06-decisions/d-003-third.md",
            front("Third", doc_type="decision", status="approved", extra="decision_id: D-003\nsupersedes: D-001\nreview_by: 2027-01-01\n"),
        )
        self.write(
            "06-decisions/decision-log.md",
            front("Decision log", doc_type="decision")
            + "\n| ID | Decision | Date | Owner | Status | Record |\n"
              "|---|---|---|---|---|---|\n"
              "| D-001 | First | 2026-09-17 | Test owner | superseded | [[06-decisions/d-001-first]] |\n"
              "| D-002 | Second | 2026-09-17 | Test owner | accepted | [[06-decisions/d-002-second]] |\n"
              "| D-003 | Third | 2026-09-17 | Test owner | accepted | [[06-decisions/d-003-third]] |\n",
        )
        self.assertIn("decision.supersession_fork", self.codes())

    def test_supersession_cycle_is_rejected(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", status="draft", extra="decision_id: D-001\nsupersedes: D-002\n"),
        )
        self.write(
            "06-decisions/d-002-second.md",
            front("Second", doc_type="decision", status="draft", extra="decision_id: D-002\nsupersedes: D-001\n"),
        )
        self.write(
            "06-decisions/decision-log.md",
            front("Decision log", doc_type="decision")
            + "\n| ID | Decision | Date | Owner | Status | Record |\n"
              "|---|---|---|---|---|---|\n"
              "| D-001 | First | 2026-09-17 | Test owner | proposed | [[06-decisions/d-001-first]] |\n"
              "| D-002 | Second | 2026-09-17 | Test owner | proposed | [[06-decisions/d-002-second]] |\n",
        )
        self.assertIn("decision.supersession_cycle", self.codes())

    def test_superseded_by_must_match_the_approved_reverse_edge(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", status="superseded", extra="decision_id: D-001\nsuperseded_by: D-003\n"),
        )
        self.write(
            "06-decisions/d-002-second.md",
            front("Second", doc_type="decision", status="approved", extra="decision_id: D-002\nsupersedes: D-001\nreview_by: 2027-01-01\n"),
        )
        self.write(
            "06-decisions/decision-log.md",
            front("Decision log", doc_type="decision")
            + "\n| ID | Decision | Date | Owner | Status | Record |\n"
              "|---|---|---|---|---|---|\n"
              "| D-001 | First | 2026-09-17 | Test owner | superseded | [[06-decisions/d-001-first]] |\n"
              "| D-002 | Second | 2026-09-17 | Test owner | accepted | [[06-decisions/d-002-second]] |\n",
        )
        self.assertIn("decision.superseded_by_mismatch", self.codes())

    def test_true_duplicate_decision_id_is_rejected(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\n"),
        )
        self.write(
            "06-decisions/d-002-second.md",
            front("Second", doc_type="decision", extra="decision_id: D-001\n"),
        )
        self.assertIn("decision.duplicate", self.codes())

    def test_empty_evidence_placeholder_does_not_satisfy_citation(self) -> None:
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 |  | report |  |  |  |  |\n",
        )
        self.write(
            "01-strategy/test.md",
            front("Test", source_ids=["E-001"]),
        )
        self.assertIn("evidence.missing", self.codes())

    def test_ambiguous_wikilink_requires_a_path(self) -> None:
        self.write("a/foo.md", front("Foo A"))
        self.write("b/foo.md", front("Foo B"))
        self.write("Home.md", "# Home\n\n[[foo]]\n")
        self.assertIn("wikilink.ambiguous", self.codes())

    def test_alias_resolves_without_error(self) -> None:
        self.write("a/alpha.md", front("Alpha", aliases=["Unique alias"]))
        self.write("Home.md", "# Home\n\n[[Unique alias]]\n")
        self.assertNotIn("wikilink.missing", self.codes())
        self.assertNotIn("wikilink.ambiguous", self.codes())

    def test_draft_cannot_be_source_of_truth(self) -> None:
        self.write(
            "01-strategy/test.md",
            front("Test", status="draft", source_of_truth=True),
        )
        self.assertIn("canonical.unapproved", self.codes())

    def test_point_in_time_report_requires_date_in_filename(self) -> None:
        self.write("reports/audit.md", front("Audit", doc_type="research"))
        self.assertIn("report.undated", self.codes())

    def test_secret_scanner_checks_json_not_only_markdown(self) -> None:
        self.write("Home.md", "# Home\n")
        self.write(
            "imports/payload.json",
            '{"api_key":"abcdefghijklmnopqrstuvwx123456"}',
        )
        self.assertIn("secret.detected", self.codes(secrets=True))

    def test_fact_callout_without_evidence_id_is_visible(self) -> None:
        self.write(
            "01-strategy/test.md",
            front("Test") + "\n> [!fact] Verified fact\n> This has no evidence ID.\n",
        )
        self.assertIn("fact.inline_evidence", self.codes())

    def test_fact_callout_inside_code_fence_is_not_checked(self) -> None:
        self.write(
            "01-strategy/test.md",
            front("Test") + "\n```markdown\n> [!fact] Example only\n```\n",
        )
        self.assertNotIn("fact.inline_evidence", self.codes())

    def test_retired_evidence_is_a_warning_not_a_dangling_error(self) -> None:
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-002 | Current source | report | 2026-09-01 | 2026-09-02 | https://example.test | current |\n"
              "\n## Retired sources\n\n"
              "| ID | Source | Retired on | Why | Replaced by |\n"
              "|---|---|---|---|---|\n"
              "| E-001 | Old source | 2026-09-03 | superseded | E-002 |\n",
        )
        self.write("01-strategy/test.md", front("Test", source_ids=["E-001"]))
        codes = self.codes()
        self.assertIn("evidence.retired", codes)
        self.assertNotIn("evidence.missing", codes)

    def test_duplicate_evidence_id_is_rejected(self) -> None:
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 | First | report | 2026-09-01 | 2026-09-02 | https://a.test | first |\n"
              "| E-001 | Second | report | 2026-09-01 | 2026-09-02 | https://b.test | second |\n",
        )
        self.assertIn("evidence.duplicate", self.codes())

    def test_decision_record_must_be_indexed_in_decision_log(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\n"),
        )
        self.write("06-decisions/decision-log.md", front("Decision log", doc_type="decision"))
        self.assertIn("decision_log.unindexed", self.codes())

    def test_decision_log_record_id_must_match_row_id(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\n"),
        )
        self.write(
            "06-decisions/decision-log.md",
            front("Decision log", doc_type="decision")
            + "\n| ID | Decision | Date | Owner | Status | Record |\n"
              "|---|---|---|---|---|---|\n"
              "| D-002 | Wrong | 2026-09-17 | Test owner | accepted | [[06-decisions/d-001-first]] |\n",
        )
        self.assertIn("decision_log.id_mismatch", self.codes())


class ReviewDateTests(VaultTestCase):
    """An accepted decision with no expiry is how a vault goes quietly stale."""

    def test_approved_decision_without_a_review_date_is_flagged(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\n"),
        )
        self.assertIn("decision.review_missing", self.codes())

    def test_a_review_date_clears_the_warning(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\nreview_by: 2027-01-01\n"),
        )
        self.assertNotIn("decision.review_missing", self.codes())

    def test_a_placeholder_date_does_not_count_as_a_review_date(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\nreview_by: YYYY-MM-DD\n"),
        )
        self.assertIn("decision.review_missing", self.codes())

    def test_draft_decisions_are_not_asked_for_a_review_date(self) -> None:
        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", status="draft", extra="decision_id: D-001\n"),
        )
        self.assertNotIn("decision.review_missing", self.codes())

    def test_overdue_reviews_are_reported_as_of_a_given_date(self) -> None:
        import datetime as dt

        self.write(
            "06-decisions/d-001-first.md",
            front("First", doc_type="decision", extra="decision_id: D-001\nreview_by: 2026-06-01\n"),
        )
        _, before = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 1, 1))
        _, after = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 9, 17))
        self.assertNotIn("review_by.overdue", [f.code for f in before])
        self.assertIn("review_by.overdue", [f.code for f in after])


class AgentContractTests(VaultTestCase):
    """An unanswered agent contract is worse than an absent one: it looks decided."""

    def test_unanswered_todo_is_flagged(self) -> None:
        self.write("AGENTS.md", "# AGENTS.md\n\n- TODO: state the language to reply in.\n")
        self.assertIn("agents.unconfigured", self.codes())

    def test_an_answered_contract_is_clean(self) -> None:
        self.write("AGENTS.md", "# AGENTS.md\n\n- Reply in Polish; write documents in English.\n")
        self.assertNotIn("agents.unconfigured", self.codes())

    def test_the_word_todo_inside_prose_is_not_a_marker(self) -> None:
        self.write("AGENTS.md", "# AGENTS.md\n\nDo not leave a TODO comment in a decision record.\n")
        self.assertNotIn("agents.unconfigured", self.codes())

    def test_a_todo_inside_a_code_fence_is_an_example_not_an_obligation(self) -> None:
        self.write("AGENTS.md", "# AGENTS.md\n\n```\n- TODO: fill this in\n```\n")
        self.assertNotIn("agents.unconfigured", self.codes())

    def test_a_vault_with_no_contract_is_flagged(self) -> None:
        self.write("Home.md", "# Home\n")
        self.assertIn("agents.absent", self.codes())


class ProvenanceTests(VaultTestCase):
    """Producers may annotate notes without the format growing vendor fields."""

    def test_a_provenance_block_is_accepted(self) -> None:
        self.write(
            "01-strategy/test.md",
            front("Test", extra='provenance:\n  producer: "example.decision-review/v1"\n  human_reviewed: true\n'),
        )
        codes = self.codes()
        self.assertNotIn("frontmatter.invalid", codes)
        self.assertNotIn("frontmatter.required", codes)

    def test_provenance_block_list_and_inline_comments_use_the_builtin_parser(self) -> None:
        self.write(
            "01-strategy/provenance-list.md",
            front(
                "Provenance list",
                extra=(
                    'provenance:\n'
                    '  producer: "example.decision-review/v1" # producer comment\n'
                    '  upstream:\n'
                    '    - pack_001\n'
                    '    - "pack,002"\n'
                    '  human_reviewed: true\n'
                ),
            ),
        )
        # Override the default empty tags with a list that includes commas inside quotes.
        text = (self.root / "01-strategy/provenance-list.md").read_text(encoding="utf-8")
        text = text.replace(
            "tags: []",
            'tags: ["pricing, packaging", "agent"] # safe inline comment',
            1,
        )
        (self.root / "01-strategy/provenance-list.md").write_text(text, encoding="utf-8")
        note = lint_mod.load_note(self.root / "01-strategy/provenance-list.md")
        self.assertIsNone(note.front_error)
        self.assertEqual(note.front["tags"], ["pricing, packaging", "agent"])
        self.assertEqual(note.front["provenance"]["upstream"], ["pack_001", "pack,002"])
        self.assertIs(note.front["provenance"]["human_reviewed"], True)

    def test_rich_yaml_is_rejected_instead_of_becoming_environment_dependent(self) -> None:
        self.write(
            "01-strategy/rich-yaml.md",
            front("Rich YAML", extra="provenance:\n  producer:\n    name: unsupported\n"),
        )
        note = lint_mod.load_note(self.root / "01-strategy/rich-yaml.md")
        self.assertIsNotNone(note.front_error)


if __name__ == "__main__":
    unittest.main()

class VaultBoundaryTests(VaultTestCase):
    def test_wikilink_cannot_escape_the_vault(self) -> None:
        self.write("Home.md", "# Home\n\n[[../outside]]\n")
        self.write("00-context/README.md", "# Context\n")
        self.assertIn("wikilink.outside", self.codes())

    def test_positional_lint_path_cannot_escape_the_vault(self) -> None:
        self.write("Home.md", "# Home\n")
        self.write("00-context/README.md", "# Context\n")
        outside = self.root.parent / "outside-whykit-test.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        try:
            with self.assertRaises(lint_mod.VaultPathError):
                lint_mod.collect_markdown(self.root, [str(outside)])
        finally:
            outside.unlink(missing_ok=True)

class MarkdownTableParsingTests(VaultTestCase):
    def test_evidence_register_allows_escaped_pipe_in_cell(self) -> None:
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 | Alpha \\| Beta | report | 2026-09-01 | 2026-09-02 | https://example.test | claim |\n",
        )
        active, _, _ = lint_mod.evidence_register(self.root)
        self.assertEqual(active["E-001"]["source"], "Alpha | Beta")
        self.assertEqual(active["E-001"]["location"], "https://example.test")

    def test_decision_log_allows_wikilink_alias_pipe(self) -> None:
        self.write(
            "06-decisions/decision-log.md",
            front("Decision log", doc_type="decision")
            + "\n| ID | Decision | Date | Owner | Status | Record |\n"
              "|---|---|---|---|---|---|\n"
              "| D-001 | First | 2026-09-17 | Test owner | accepted | [[06-decisions/d-001-first|First decision]] |\n",
        )
        rows = lint_mod.decision_log_rows(self.root)
        self.assertEqual(rows[0]["record"], "06-decisions/d-001-first")

class MarkdownLinkTests(VaultTestCase):
    def test_missing_relative_markdown_link_is_visible(self) -> None:
        self.write("Home.md", "# Home\n\n[Missing](notes/missing.md)\n")
        self.write("00-context/README.md", "# Context\n")
        self.assertIn("markdown_link.missing", self.codes())

    def test_existing_relative_markdown_link_is_clean(self) -> None:
        self.write("Home.md", "# Home\n\n[Company](00-context/company.md)\n")
        self.write("00-context/company.md", front("Company"))
        self.assertNotIn("markdown_link.missing", self.codes())

    def test_markdown_link_outside_vault_is_visible(self) -> None:
        self.write("Home.md", "# Home\n\n[Outside](../outside.md)\n")
        self.write("00-context/README.md", "# Context\n")
        self.assertIn("markdown_link.outside", self.codes())

    def test_external_and_anchor_links_are_ignored(self) -> None:
        self.write("Home.md", "# Home\n\n[Web](https://example.test) [Section](#section)\n")
        self.write("00-context/README.md", "# Context\n")
        codes = self.codes()
        self.assertNotIn("markdown_link.missing", codes)
        self.assertNotIn("markdown_link.outside", codes)
