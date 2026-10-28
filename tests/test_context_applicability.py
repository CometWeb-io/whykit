"""Local governance eligibility is separate from asserted fact validity and authority."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from _vaults import ROOT, fill_decision
from whykit.compiled_context import compile_context, serialize_compiled, verify_compiled
from whykit.immutability import _approval_event_matches, _review_log_parts, approval_event_matches_rows, approval_record_hash
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision

DAY = dt.date(2026, 9, 17)
SCOPE = {"public", "internal", "confidential", "restricted"}


class ContextApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "vault"
        shutil.copytree(ROOT / "examples/approval", self.root)
        (self.root / "whykit.toml").write_text('[evidence_access_age_days]\nfixture = 90\n', encoding="utf-8")

    def compile(self, **kwargs):
        return compile_context(self.root, today=DAY, allowed_sensitivities=SCOPE, **{"targets": ["D-001"], **kwargs})

    def decision(self, *, validity=None, supersedes=None, approve=True):
        did, path = create_decision(self.root, "Synthetic needle process", owner="Ada Example", source_ids=["E-001"],
                                    supersedes=supersedes, today=DAY)
        fill_decision(path)
        if validity:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("\n---\n", "\n" + "\n".join(f"{key}: {value}" for key,value in validity.items()) + "\n---\n", 1), encoding="utf-8")
        if approve:
            plan = approve_decision(self.root, did, reviewer="Ada Example", today=DAY, next_review="2026-12-01")
            approve_decision(self.root, did, reviewer="Ada Example", today=DAY, next_review="2026-12-01", write=True, expected_sha256=plan["expected_sha256"])
        return did, path

    def test_legacy_unknown_validity_remains_only_locally_applicable(self):
        report = self.compile()
        context = report["contexts"][0]
        metadata = context["applicability"]
        self.assertEqual(metadata["state"], "applicable")
        self.assertTrue(metadata["premise"])
        self.assertEqual(metadata["local_approval"], "matched")
        self.assertEqual(metadata["declared_validity"]["state"], "unknown")
        self.assertIn("validity_unknown", metadata["reasons"])
        self.assertFalse(report["execution_authorized"])
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities=SCOPE)["status"], "current")

    def test_drafts_and_future_expired_are_explicit_references_but_not_ranked_premises(self):
        for dates, approve in ((None, False), ({"valid_from": "2026-10-01"}, True),
                               ({"valid_from": "2026-01-01", "valid_until": DAY.isoformat()}, True)):
            did, _ = self.decision(validity=dates, approve=approve)
            report = self.compile(targets=[did])
            self.assertFalse(report["contexts"][0]["applicability"]["premise"])
            self.assertEqual(report["contexts"][0]["applicability"]["state"], "reference")
            queried = self.compile(targets=[], query="Synthetic needle process", max_docs=64)
            self.assertNotIn(did, [item["record"].get("decision_id") for item in queried["contexts"]])

    def test_history_has_row_citations_without_whole_review_log_citation(self):
        report = self.compile()
        history = report["contexts"][0]["history"]
        self.assertTrue(history["reviews"])
        self.assertFalse(history["unavailable"])
        raw = (self.root / "00-context/review-log.md").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        wire = serialize_compiled(report)
        self.assertNotIn(digest, wire)
        rows = [citation for citation in report["citations"] if citation["kind"] == "review"]
        self.assertEqual(rows[0]["sha256"], history["reviews"][0]["row_sha256"])
        self.assertEqual(self.compile(targets=["00-context/review-log"])["contexts"], [])

    def test_supersession_history_and_visible_change_make_report_stale(self):
        old = self.compile()
        did, _ = self.decision(supersedes="D-001")
        report = self.compile(targets=["D-001", did])
        predecessor = next(item for item in report["contexts"] if item["record"]["decision_id"] == "D-001")
        self.assertFalse(predecessor["applicability"]["premise"])
        self.assertEqual(predecessor["history"]["superseded_by"][0]["decision_id"], did)
        self.assertEqual(verify_compiled(self.root, old, today=DAY, allowed_sensitivities=SCOPE)["status"], "stale")

    def test_receipt_rows_extraction_preserves_legacy_semantics(self):
        decision = next((self.root / "06-decisions").glob("d-001-*.md"))
        relative = decision.relative_to(self.root).as_posix()
        text = decision.read_text(encoding="utf-8")
        log = (self.root / "00-context/review-log.md").read_text(encoding="utf-8")
        original = _review_log_parts(log)[1]
        invalid_date = [["2026-02-30", *row[1:]] for row in original]
        claims = text.replace("\n---\n", '\nprovenance:\n  claim_versions: \'{"C-001":"sha256:' + "a" * 64 + '"}\'\n---\n', 1)
        pinned_rows = [[*row[:6], "record-sha256:" + approval_record_hash(claims) + row[6][row[6].index("; snapshot-sha256:"):]] for row in original]
        cases = [(text, rows) for rows in (original, original + original, [], invalid_date,
                                          [[*row[:6], row[6] + " | escaped"] for row in original])]
        cases += [(claims, pinned_rows), (claims + "\nChanged pinned premise.\n", pinned_rows)]
        for decision_text, rows in cases:
            # Regenerate a properly escaped table to compare parser+matching with pure cells.
            from whykit.scaffold import _table_cell
            header = "---\ntitle: Review\n---\n| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |\n|---|---|---|---|---|---|---|\n"
            serialized = header + "".join("| " + " | ".join(_table_cell(cell) for cell in row) + " |\n" for row in rows)
            parsed = _review_log_parts(serialized)
            self.assertIsNotNone(parsed)
            self.assertEqual(_approval_event_matches(decision_text, relative, None, serialized),
                             approval_event_matches_rows(decision_text, relative, [], parsed[1]))

    def test_new_fields_count_toward_entire_wire_budget_and_schema(self):
        report = self.compile(max_bytes=4096)
        self.assertLessEqual(len(serialize_compiled(report).encode("utf-8")), 4096)
        schema = json.loads((ROOT / "schemas/compiled-context.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])

    def test_pure_metadata_and_captured_lint_do_not_reopen_dependencies(self):
        from whykit.context_applicability import context_metadata
        from whykit.config import parse_config
        from whykit.lint import check_decision_log, check_review_log, load_note
        from whykit.review_history import project_review_rows
        from whykit.sensitivity import classified_evidence
        from whykit.vault_index import VaultIndex
        index = VaultIndex.load(self.root)
        rows = classified_evidence(index)[:3]
        projected, available, incomplete = project_review_rows(index.note_for(self.root / "00-context/review-log.md"), index, scoped=True)
        hashes = {note.path: hashlib.sha256(note.path.read_bytes()).hexdigest() for note in index.notes}
        config = parse_config((self.root / "whykit.toml").read_text(encoding="utf-8"))
        log = index.note_for(self.root / "06-decisions/decision-log.md")
        with patch("pathlib.Path.open", side_effect=AssertionError("captured helper reopened a file")):
            metadata = context_metadata(index, projected, available, incomplete, rows, {}, config, DAY, hashes)
            check_decision_log(self.root, index.notes, [], index.resolve_link, text=log.text)
            check_review_log(self.root, index.notes, [], index.resolve_link,
                             text=index.note_for(self.root / "00-context/review-log.md").text)
            empty_findings = []
            check_review_log(self.root, [], empty_findings, index.resolve_link, text="")
            self.assertTrue(any(item.code == "review_log.table" for item in empty_findings))
            malformed = load_note(self.root / "00-context/review-log.md",
                                  text="| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |\n\n")
            self.assertFalse(project_review_rows(malformed, index, scoped=True)[1])
        decision = next(path for path in metadata if path.name.startswith("d-001-"))
        self.assertTrue(metadata[decision]["applicability"]["premise"])

    def test_history_cap_retains_effective_dated_event_after_many_backdated_confirmations(self):
        record_review(self.root, "D-001", reviewer="Ada Example", outcome="update-required",
                      note_text="New evidence requires review", today=dt.date(2026, 9, 30))
        for _ in range(10):
            record_review(self.root, "D-001", reviewer="Ada Example", outcome="confirmed",
                          next_review="2026-12-20", today=dt.date(2026, 9, 20))
        report = compile_context(self.root, targets=["D-001"], today=dt.date(2026, 10, 1), allowed_sensitivities=SCOPE)
        context = report["contexts"][0]
        self.assertFalse(context["applicability"]["premise"])
        self.assertEqual(len(context["history"]["reviews"]), 8)
        self.assertTrue(context["history"]["truncated"])
        self.assertEqual(context["history"]["reviews"][-1]["outcome"], "update-required")
        self.assertEqual(context["history"]["reviews"][-1]["date"], "2026-09-30")

    def test_alias_rows_never_gain_a_public_target_when_a_hidden_twin_is_filtered(self):
        from whykit.knowledge_v2 import project_knowledge_v2
        public = next((self.root / "06-decisions").glob("d-001-*.md"))
        public.write_text(_frontmatter_replace(public.read_text(encoding="utf-8"), "aliases", "[SHARED_REVIEW_ALIAS]"), encoding="utf-8")
        _, hidden = create_decision(self.root, "Hidden synthetic record", owner="Ada Example", sensitivity="restricted", today=DAY)
        hidden.write_text(_frontmatter_replace(hidden.read_text(encoding="utf-8"), "aliases", "[SHARED_REVIEW_ALIAS]"), encoding="utf-8")
        before = compile_context(self.root, targets=["D-001"], today=DAY)
        before_v2 = project_knowledge_v2(self.root)
        log = self.root / "00-context/review-log.md"
        with log.open("a", encoding="utf-8") as handle:
            handle.write("| 2026-09-17 | [[SHARED_REVIEW_ALIAS]] | HIDDEN_REVIEWER_CANARY | update-required | — | — | HIDDEN_REVIEW_NOTE_CANARY |\n")
        self.assertEqual(compile_context(self.root, targets=["D-001"], today=DAY), before)
        self.assertEqual(project_knowledge_v2(self.root), before_v2)
        ledger = self.root / "06-decisions/decision-log.md"
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write("| D-001 | HIDDEN_LEDGER_CANARY | 2026-09-17 | HIDDEN_OWNER_CANARY | accepted | [[SHARED_REVIEW_ALIAS]] |\n")
        self.assertEqual(compile_context(self.root, targets=["D-001"], today=DAY), before)

    def test_global_ledger_case_variants_never_enter_context_ranking_or_raw_hashes(self):
        ledgers = ("06-decisions/decision-log.md", "00-context/review-log.md")
        markers = ("PRIVATE_CASE_DECISION", "PRIVATE_CASE_REVIEWER", "PRIVATE_CASE_REVIEW_NOTE")
        for variant in ("filename", "components"):
            with self.subTest(variant=variant):
                root = Path(self.temp.name).resolve() / variant
                shutil.copytree(ROOT / "examples/approval", root)
                (root / "whykit.toml").write_text('[evidence_access_age_days]\nfixture = 90\n', encoding="utf-8")
                hidden, _ = create_decision(root, markers[0], owner="Ada Example", sensitivity="restricted", source_ids=["E-001"], today=DAY)
                record_review(root, hidden, reviewer=markers[1], outcome="update-required", note_text=markers[2], today=DAY)
                changed, aliases = [], []
                for number, relative in enumerate(ledgers):
                    original = root / relative
                    alias = f"CASE_LEDGER_ALIAS_{number}"
                    aliases.append(alias)
                    original.write_text(_frontmatter_replace(original.read_text(encoding="utf-8"), "aliases", json.dumps([alias])), encoding="utf-8")
                    target = (original.with_name(original.name.upper()) if variant == "filename"
                              else root / relative.split("/")[0].upper() / Path(relative).name.upper())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    original.rename(target)
                    changed.append(target)
                forbidden_raw = {path.read_bytes() for path in changed}
                original_sha = hashlib.sha256
                def safe_sha(raw=b"", *args, forbidden=forbidden_raw, original=original_sha, **kwargs):
                    self.assertNotIn(raw, forbidden, "raw private mixed log was hashed")
                    return original(raw, *args, **kwargs)
                def compile_case(*, case_root=root, **kwargs):
                    return compile_context(case_root, today=DAY, max_bytes=32000, **kwargs)
                query_reports = {}
                with patch.object(hashlib, "sha256", side_effect=safe_sha):
                    targets = [*ledgers, *[path.relative_to(root).as_posix() for path in changed],
                               *[path.relative_to(root).with_suffix("").as_posix() for path in changed], *aliases]
                    for target in targets:
                        self.assertEqual(compile_case(targets=[target])["contexts"], [], target)
                    for ranking in ("substring", "bm25"):
                        for marker in markers:
                            report = compile_case(query=marker, ranking=ranking)
                            query_reports[ranking, marker] = report
                            for context in report["contexts"]:
                                self.assertNotIn(context["record"].get("path", "").casefold(), ledgers)
                            for private in markers:
                                self.assertNotIn(private, json.dumps(report["contexts"]))
                    baseline = compile_case(targets=["D-001"])
                self.assertTrue(baseline["contexts"][0]["history"]["unavailable"])
                for marker in markers:
                    self.assertNotIn(marker, serialize_compiled(baseline))
                for target, relative in zip(changed, ledgers, strict=True):
                    target.rename(root / relative)
                record_review(root, hidden, reviewer="ANOTHER_PRIVATE_CASE_REVIEWER", outcome="update-required",
                              note_text="ANOTHER_PRIVATE_CASE_NOTE", today=DAY)
                for target, relative in zip(changed, ledgers, strict=True):
                    (root / relative).rename(target)
                self.assertEqual(compile_case(targets=["D-001"]), baseline)
                for (ranking, marker), report in query_reports.items():
                    self.assertEqual(compile_case(query=marker, ranking=ranking), report)
