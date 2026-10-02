from __future__ import annotations

import datetime as dt
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
    def test_crlf_front_matter_parses_and_scores_ready(self) -> None:
        from whykit.adopt import scan

        path = self.root / "notes" / "crlf.md"
        path.parent.mkdir(parents=True)
        content = front("CRLF note", status="draft") + "\n" + ("A substantive sentence for this portable document. " * 4)
        path.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))

        note = lint_mod.load_note(path)
        self.assertTrue(note.has_front)
        self.assertEqual(note.front["status"], "draft")
        self.assertNotIn("frontmatter.invalid", self.codes())
        self.assertNotIn("frontmatter.missing", self.codes())
        self.assertTrue(scan(self.root)[0].whykit_ready)

    def test_numbered_agent_contract_todo_is_reported(self) -> None:
        self.write("AGENTS.md", "1. TODO: choose a branching rule.\n")
        self.assertEqual(self.codes().count("agents.unconfigured"), 1)

    def test_list_where_scalar_metadata_expected_reports_errors_without_crashing(self) -> None:
        self.write(
            "notes/invalid.md",
            front("Invalid", status="[draft]", doc_type="[guide]")
            .replace("sensitivity: internal", "sensitivity: [internal]"),
        )
        codes = self.codes()
        self.assertIn("status.invalid", codes)
        self.assertIn("type.invalid", codes)
        self.assertIn("sensitivity.invalid", codes)

    def test_evidence_access_age_is_opt_in_and_as_of_today(self) -> None:
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference", status="draft")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 | Example measurements | analytics | 2026-01-01 | 2026-01-01 | https://example.com | Example claim |\n"
              "| E-002 | Old report | report | 2020-01-01 | 2020-01-01 | https://example.com/report | History |\n",
        )
        _, before = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 2, 2))
        self.assertNotIn("evidence.access_stale", [f.code for f in before])
        self.write("whykit.toml", 'format_version = 1\n[evidence_access_age_days]\nanalytics = 30\n')
        _, after = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 2, 2))
        stale = [f for f in after if f.code == "evidence.access_stale"]
        self.assertEqual(len(stale), 1)
        self.assertIn("E-001", stale[0].message)
        self.assertEqual(stale[0].level, "warning")

    def test_evidence_access_age_requires_a_real_access_date(self) -> None:
        self.write("whykit.toml", 'format_version = 1\n[evidence_access_age_days]\nanalytics = 30\n')
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference", status="draft")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 | Example measurements | analytics | 2026-01-01 |  | https://example.com | Example claim |\n",
        )
        _, findings = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 2, 2))
        self.assertIn("evidence.access_missing", [f.code for f in findings])

    def test_fresh_access_and_retired_history_do_not_trigger_age_warning(self) -> None:
        self.write("whykit.toml", 'format_version = 1\n[evidence_access_age_days]\nanalytics = 30\n')
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference", status="draft")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 | Current measurements | analytics | 2026-01-15 | 2026-01-15 | https://example.com | Claim |\n"
              "\n## Retired sources\n\n"
              "| ID | Source | Retired on | Why | Replaced by |\n"
              "|---|---|---|---|---|\n"
              "| E-002 | Historic measurements | 2026-01-01 | Replaced | E-001 |\n",
        )
        _, findings = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 2, 2))
        self.assertFalse([f for f in findings if f.code in {"evidence.access_stale", "evidence.access_missing"}])

    def test_future_access_date_cannot_make_evidence_look_fresh(self) -> None:
        self.write("whykit.toml", 'format_version = 1\n[evidence_access_age_days]\nanalytics = 30\n')
        self.write(
            "00-context/evidence-register.md",
            front("Evidence register", doc_type="reference", status="draft")
            + "\n| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n"
              "|---|---|---|---|---|---|---|\n"
              "| E-001 | Example measurements | analytics | 2026-01-01 | 2026-03-01 | https://example.com | Claim |\n",
        )
        _, findings = lint_mod.lint(self.root, orphans=False, secrets=False, today=dt.date(2026, 2, 2))
        self.assertIn("evidence.access_future", [f.code for f in findings])

    def test_invalid_access_age_policy_fails_config_validation(self) -> None:
        self.write("whykit.toml", 'format_version = 1\n[evidence_access_age_days]\nanalytics = -1\n')
        _, findings = lint_mod.lint(self.root, orphans=False, secrets=False)
        errors = [f.message for f in findings if f.code == "config.invalid"]
        self.assertEqual(len(errors), 1)
        self.assertIn("analytics", errors[0])

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


FRONT_KEYS = (
    "type: guide\nstatus: draft\nowner: Test owner\ncreated: 2026-09-17\n"
    "last_updated: 2026-09-17\nsource_of_truth: false\nsensitivity: internal\n"
)


class FrontMatterEdgeCaseTests(VaultTestCase):
    """Valid files editors really produce must parse; broken ones must stay loud."""

    def test_column_zero_block_list_is_valid_yaml(self) -> None:
        path = self.write("notes/a.md", f"---\ntitle: A\n{FRONT_KEYS}tags:\n- alpha\n- beta\n---\n\n# A\n")
        note = lint_mod.load_note(path)
        self.assertIsNone(note.front_error)
        self.assertEqual(note.front["tags"], ["alpha", "beta"])

    def test_four_space_block_list_is_valid_yaml(self) -> None:
        path = self.write("notes/a.md", f"---\ntitle: A\n{FRONT_KEYS}aliases:\n    - First\n    - Second\n---\n")
        self.assertEqual(lint_mod.load_note(path).front["aliases"], ["First", "Second"])

    def test_compact_list_under_nested_key_is_valid_yaml(self) -> None:
        path = self.write(
            "notes/a.md",
            f"---\ntitle: A\n{FRONT_KEYS}provenance:\n  sources:\n  - one\n  - two\n  importer: manual\n---\n",
        )
        note = lint_mod.load_note(path)
        self.assertIsNone(note.front_error)
        self.assertEqual(note.front["provenance"], {"sources": ["one", "two"], "importer": "manual"})

    def test_inconsistent_indentation_is_still_rejected(self) -> None:
        path = self.write("notes/a.md", f"---\ntitle: A\n{FRONT_KEYS}tags:\n  - a\n    - b\n---\n")
        self.assertIn("indentation", lint_mod.load_note(path).front_error or "")

    def test_block_scalar_is_rejected_with_a_specific_message(self) -> None:
        path = self.write("notes/a.md", f"---\ntitle: >-\n  Folded\n{FRONT_KEYS}---\n")
        error = lint_mod.load_note(path).front_error or ""
        self.assertIn("block scalar", error)

    def test_utf8_bom_does_not_hide_front_matter(self) -> None:
        path = self.root / "notes" / "bom.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(("﻿" + front("BOM note")).encode("utf-8"))
        note = lint_mod.load_note(path)
        self.assertTrue(note.has_front)
        self.assertEqual(note.front["title"], "BOM note")
        self.assertNotIn("frontmatter.missing", self.codes())

    def test_front_matter_closed_at_end_of_file_without_newline(self) -> None:
        path = self.write("notes/a.md", f"---\ntitle: A\n{FRONT_KEYS}---")
        note = lint_mod.load_note(path)
        self.assertTrue(note.has_front)
        self.assertIsNone(note.front_error)
        self.assertEqual(note.body, "")

    def test_empty_front_matter_reports_missing_keys_not_an_unclosed_fence(self) -> None:
        self.write("notes/a.md", "---\n---\n\n# A\n")
        codes = self.codes()
        self.assertNotIn("frontmatter.invalid", codes)
        self.assertIn("frontmatter.required", codes)

    def test_unclosed_front_matter_is_still_invalid(self) -> None:
        self.write("notes/a.md", "---\ntitle: A\n\n# A\n")
        self.assertIn("frontmatter.invalid", self.codes())

    def test_body_skips_front_matter_by_line(self) -> None:
        path = self.write("notes/a.md", front("Body") + "\nFirst paragraph.\n")
        note = lint_mod.load_note(path)
        self.assertTrue(note.body.startswith("\n# Body"))
        self.assertNotIn("title:", note.body)

    def test_empty_required_value_is_flagged(self) -> None:
        self.write("notes/a.md", front("A").replace("owner: Test owner", "owner:"))
        self.assertIn("frontmatter.empty", self.codes())

    def test_template_placeholders_are_not_empty_values(self) -> None:
        self.write("templates/t.md", front("T", status="template").replace("owner: Test owner", "owner: TODO"))
        self.assertNotIn("frontmatter.empty", self.codes())

    def test_non_boolean_source_of_truth_is_flagged(self) -> None:
        for value in ("yes", '"true"', "1"):
            with self.subTest(value=value):
                self.write("notes/a.md", front("A", status="draft").replace("source_of_truth: false", f"source_of_truth: {value}"))
                self.assertIn("source_of_truth.invalid", self.codes())

    def test_boolean_source_of_truth_is_clean(self) -> None:
        self.write("notes/a.md", front("A", status="draft", source_of_truth=False))
        self.write("notes/b.md", front("B", source_of_truth=True))
        self.assertNotIn("source_of_truth.invalid", self.codes())


class LinkResolutionEdgeCaseTests(VaultTestCase):
    def test_attachment_embed_resolves_by_file_name(self) -> None:
        self.write("assets/diagram.png", "png")
        self.write("notes/a.md", front("A") + "\n![[diagram.png]]\n")
        codes = self.codes()
        self.assertNotIn("embed.missing", codes)
        self.assertNotIn("wikilink.missing", codes)

    def test_attachment_link_resolves_by_vault_path(self) -> None:
        self.write("assets/brief.pdf", "pdf")
        self.write("notes/a.md", front("A") + "\n[[assets/brief.pdf]]\n")
        self.assertNotIn("wikilink.missing", self.codes())

    def test_missing_attachment_embed_is_still_an_error(self) -> None:
        self.write("notes/a.md", front("A") + "\n![[missing.png]]\n")
        self.assertIn("embed.missing", self.codes())

    def test_attachments_in_ignored_directories_do_not_count(self) -> None:
        self.write(".obsidian/cache.png", "png")
        self.write("notes/a.md", front("A") + "\n![[cache.png]]\n")
        self.assertIn("embed.missing", self.codes())

    def test_escaped_alias_pipe_in_table_keeps_the_path(self) -> None:
        # Two notes share the stem, so only the path disambiguates; the `\|`
        # Obsidian writes inside tables must not glue a backslash onto it.
        self.write("one/topic.md", front("One"))
        self.write("two/topic.md", front("Two"))
        self.write("notes/a.md", front("A") + "\n| Link |\n|---|\n| [[one/topic\\|First]] |\n")
        codes = self.codes()
        self.assertNotIn("wikilink.ambiguous", codes)
        self.assertNotIn("wikilink.missing", codes)

    def test_escaped_alias_pipe_after_heading(self) -> None:
        self.write("one/topic.md", front("One"))
        self.write("notes/a.md", front("A") + "\n| [[one/topic#Part\\|First]] |\n")
        self.assertNotIn("wikilink.missing", self.codes())

    def test_backslash_path_still_resolves(self) -> None:
        self.write("one/topic.md", front("One"))
        self.write("notes/a.md", front("A") + "\n[[one\\topic]]\n")
        self.assertNotIn("wikilink.missing", self.codes())

    def test_link_index_matches_nfc_text_to_nfd_file_names(self) -> None:
        import unicodedata

        nfd = unicodedata.normalize("NFD", "zażółć")
        nfc = unicodedata.normalize("NFC", "zażółć")
        missing_root = self.root / "not-on-disk"
        note = lint_mod.Note(path=missing_root / "notes" / f"{nfd}.md", text="")
        index = lint_mod._build_index([note])
        resolved, ambiguous = lint_mod._resolve(missing_root, nfc, index)
        self.assertEqual(resolved, note.path)
        self.assertFalse(ambiguous)

    def test_missing_markdown_image_is_visible(self) -> None:
        self.write("notes/a.md", front("A") + "\n![Chart](../assets/chart.png)\n")
        self.assertIn("markdown_link.missing", self.codes())

    def test_existing_and_remote_markdown_images_are_clean(self) -> None:
        self.write("assets/chart.png", "png")
        self.write(
            "notes/a.md",
            front("A") + "\n![Chart](../assets/chart.png) ![Remote](https://example.com/x.png)\n",
        )
        self.assertNotIn("markdown_link.missing", self.codes())


class FactEvidenceTests(VaultTestCase):
    REGISTER = (
        "\n## Active sources\n\n"
        "| ID | Source | Type | Date | Accessed | Location | Claims |\n"
        "|---|---|---|---|---|---|---|\n"
        "| E-001 | Survey | dataset | 2026-09-01 | 2026-09-02 | https://example.test/survey | demand |\n"
        "\n## Retired sources\n\n"
        "| ID | Source | Retired on | Why | Replaced by |\n"
        "|---|---|---|---|---|\n"
        "| E-002 | Old survey | 2026-09-10 | outdated | E-001 |\n"
    )

    def setUp(self) -> None:
        super().setUp()
        self.write("00-context/evidence-register.md", front("Evidence register", doc_type="reference") + self.REGISTER)

    def test_fact_citing_an_unregistered_id_is_flagged(self) -> None:
        self.write("notes/a.md", front("A") + "\n> [!fact] Claim\n> Supported by E-999.\n")
        self.assertIn("fact.evidence_missing", self.codes())

    def test_fact_citing_active_or_retired_evidence_is_clean(self) -> None:
        self.write("notes/a.md", front("A") + "\n> [!fact] Claim\n> Supported by E-001 and E-002.\n")
        codes = self.codes()
        self.assertNotIn("fact.evidence_missing", codes)
        self.assertNotIn("fact.inline_evidence", codes)

    def test_ids_outside_fact_callouts_are_not_checked(self) -> None:
        self.write("notes/a.md", front("A") + "\nAllocate E-999 next.\n\n> [!fact] Claim\n> E-001.\n")
        self.assertNotIn("fact.evidence_missing", self.codes())


class ReviewLogFormattingTests(VaultTestCase):
    def test_formatter_padded_header_is_accepted(self) -> None:
        self.write(
            "00-context/review-log.md",
            front("Review log", doc_type="reference")
            + "\n| Date       | Target | Reviewer | Outcome   | Previous review | Next review | Note |\n"
              "| ---------- | ------ | -------- | --------- | --------------- | ----------- | ---- |\n"
              "| 2026-09-20 | [[notes/a]] | Research | confirmed | — | 2026-12-01 | ok |\n",
        )
        self.write("notes/a.md", front("A"))
        self.assertFalse([c for c in self.codes() if c.startswith("review_log.")])

    def test_missing_review_log_table_is_still_an_error(self) -> None:
        self.write("00-context/review-log.md", front("Review log", doc_type="reference") + "\nNo table.\n")
        self.assertIn("review_log.table", self.codes())


class LintPathArgumentTests(VaultTestCase):
    def test_nonexistent_path_is_a_usage_error_not_a_clean_run(self) -> None:
        self.write("Home.md", "# Home\n")
        with self.assertRaises(lint_mod.VaultPathError):
            lint_mod.lint(self.root, ["notes/typo.md"])

    def test_non_markdown_file_is_a_usage_error(self) -> None:
        self.write("assets/data.csv", "a,b\n")
        with self.assertRaises(lint_mod.VaultPathError):
            lint_mod.lint(self.root, ["assets/data.csv"])

    def test_existing_markdown_path_is_linted(self) -> None:
        self.write("notes/a.md", front("A"))
        files, _ = lint_mod.lint(self.root, ["notes/a.md"])
        self.assertEqual([p.name for p in files], ["a.md"])


class CheckTodayArgumentTests(unittest.TestCase):
    def test_check_rejects_non_calendar_iso_forms_like_lint_does(self) -> None:
        import contextlib
        import io

        from whykit import check as check_mod

        example = Path(__file__).resolve().parents[1] / "examples" / "northline"
        for value in ("20260917", "2026-W38-4"):
            with self.subTest(value=value):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(check_mod.main(["--root", str(example), "--today", value]), 2)
                self.assertIn("not a real ISO date", err.getvalue())
