"""Independent applicability, mixed-history visibility and snapshot boundaries."""
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
from _vaults import fill_decision
from whykit import compiled_context as compiler
from whykit.compiled_context import compile_context, serialize_compiled, verify_compiled
from whykit.immutability import approval_record_hash
from whykit.lint import load_note
from whykit.pack import build_pack
from whykit.parse_cache import persistent
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision
from whykit.vault_index import VaultIndex

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 9, 17)
PRIVATE_MARKERS = ("PRIVATE_REVIEW_NOTE_CANARY", "PRIVATE_REVIEWER_CANARY", "PRIVATE_TARGET_CANARY")


def note(root: Path, path: str, body: str, **metadata) -> Path:
    fields = {"title": "Visible reference", "aliases": [], "type": "guide", "status": "draft",
              "owner": "Ada Example", "created": DAY.isoformat(), "last_updated": DAY.isoformat(),
              "source_of_truth": False, "source_ids": [], "tags": [], "sensitivity": "public", **metadata}
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()) + "\n---\n\n" + body
    target.write_text(text, encoding="utf-8")
    return target


def tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class ContextApplicabilityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "vault"
        shutil.copytree(ROOT / "examples/approval", self.root)
        (self.root / "whykit.toml").write_text("[evidence_access_age_days]\nfixture = 90\n", encoding="utf-8")
        self.public_decision = next((self.root / "06-decisions").glob("d-001-*.md"))
        self.log = self.root / "00-context/review-log.md"
        self.hidden = note(self.root, "06-decisions/d-900-hidden.md", "Private rationale",
                           title=PRIVATE_MARKERS[2], aliases=["PRIVATE_ALIAS_CANARY"],
                           type="decision", status="approved", decision_id="D-900", sensitivity="restricted",
                           review_by="2026-12-16")
        decision_log = self.root / "06-decisions/decision-log.md"
        ledger = decision_log.read_text(encoding="utf-8")
        row = "| D-900 | Restricted decision | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-900-hidden]] |\n"
        decision_log.write_text(ledger.replace("| D-002 |", row + "| D-002 |", 1), encoding="utf-8")
        self.protected = {path: path.read_bytes() for path in (self.public_decision, self.log)}

    def compile(self, **options) -> dict:
        return compile_context(self.root, **{"today": DAY, "allowed_sensitivities": {"public"},
                                            "targets": ["D-001"], "max_bytes": 32000, **options})

    def decision(self, title: str, *, approved: bool = True, body: str = "", today=DAY, **fields) -> tuple[str, Path]:
        identity, path = create_decision(self.root, title, owner="Ada Example", sensitivity="public", source_ids=["E-001"], today=today)
        fill_decision(path)
        text = path.read_text(encoding="utf-8") + "\n" + body
        for key, value in fields.items():
            text = _frontmatter_replace(text, key, str(value))
        path.write_text(text, encoding="utf-8")
        if approved:
            options = {"reviewer": "Ada Example", "today": today, "next_review": "2026-12-16"}
            preview = approve_decision(self.root, identity, **options)
            approve_decision(self.root, identity, **options, write=True, expected_sha256=preview["expected_sha256"])
        return identity, path

    def context(self, identity: str, **options) -> tuple[dict, dict]:
        report = self.compile(targets=[identity], **options)
        self.assertEqual(report["status"], "compiled", report)
        self.assertEqual(len(report["contexts"]), 1, report)
        return report, report["contexts"][0]

    def append_review(self, target: Path | None = None, *, text: str = "Visible review", date: str = "2026-09-17", outcome: str = "confirmed") -> None:
        target = self.public_decision if target is None else target
        relative = target.relative_to(self.root).with_suffix("").as_posix()
        row = f"| {date} | [[{relative}]] | Ada Example | {outcome} | 2026-12-16 | 2026-12-16 | {text} |\n"
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(row)

    def hidden_review(self) -> None:
        row = f"| 2026-09-17 | [[06-decisions/{self.hidden.stem}]] | {PRIVATE_MARKERS[1]} | confirmed | — | — | {PRIVATE_MARKERS[0]} |\n"
        text = self.log.read_text(encoding="utf-8")
        text = text.replace("|---|---|---|---|---|---|---|\n", "|---|---|---|---|---|---|---|\n" + row, 1)
        self.log.write_text(text, encoding="utf-8")

    def test_public_explicit_review_log_target_never_embeds_denied_review_rows(self) -> None:
        self.hidden_review()
        report = self.compile(targets=["00-context/review-log"])
        wire = serialize_compiled(report)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, wire)
        self.assertNotIn("d-900-hidden", wire)

    def test_sparse_grant_query_cannot_rank_raw_log_using_only_denied_row_text(self) -> None:
        self.hidden_review()
        index = VaultIndex.load(self.root)
        grant = index.subset(lambda item: item.path in {self.log, self.public_decision})
        grant.derived["evidence_register"] = ({}, {}, [])
        with patch.object(VaultIndex, "load", side_effect=AssertionError("scope expansion")):
            report = self.compile(targets=[], query=PRIVATE_MARKERS[0], ranking="bm25", vault=grant)
        self.assertEqual(report["contexts"], [])
        self.assertEqual(report["omissions"]["query_matches"], 0)
        for marker in PRIVATE_MARKERS[1:]:
            self.assertNotIn(marker, serialize_compiled(report))

    def test_hidden_review_insertion_does_not_change_public_report_snapshot_or_verification(self) -> None:
        before = self.compile()
        self.hidden_review()
        after = self.compile()
        self.assertEqual(before, after)
        self.assertEqual(verify_compiled(self.root, before, today=DAY, allowed_sensitivities={"public"})["status"], "current")

    def test_native_restricted_decision_cannot_leak_its_title_through_public_decision_ledger(self) -> None:
        baseline = self.compile()
        marker = "PRIVATE_NATIVE_DECISION_LOG_CANARY"
        identity, path = create_decision(self.root, marker, owner="Ada Example", sensitivity="restricted",
                                         source_ids=["E-001"], today=DAY)
        self.assertEqual(self.compile(targets=[identity])["contexts"], [])
        public_log = self.compile(targets=["06-decisions/decision-log"])
        self.assertNotIn(marker, serialize_compiled(public_log))
        self.assertEqual(self.compile(), baseline)
        self.assertEqual(verify_compiled(self.root, baseline, today=DAY, allowed_sensitivities={"public"})["status"], "current")

    def test_public_call_never_hashes_raw_mixed_review_log_or_exports_global_row_metadata(self) -> None:
        self.hidden_review()
        raw_logs = {self.log.read_bytes(), (self.root / "06-decisions/decision-log.md").read_bytes()}
        original = hashlib.sha256
        def scoped_sha(raw=b"", *args, **kwargs):
            self.assertNotIn(raw, raw_logs, "raw mixed-log hash crossed the scoped compiler boundary")
            return original(raw, *args, **kwargs)
        with patch.object(hashlib, "sha256", side_effect=scoped_sha):
            report, context = self.context("D-001")
        self.assertEqual(set(context["history"]), {"supersedes", "superseded_by", "reviews", "unavailable", "truncated"})
        for review in context["history"]["reviews"]:
            self.assertEqual(set(review), {"date", "outcome", "row_sha256", "occurrence"})
        serialized = serialize_compiled(report)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, serialized)

    def test_legacy_approved_decision_remains_applicable_with_explicit_unknown_fact_validity(self) -> None:
        _, context = self.context("D-001")
        applicable = context["applicability"]
        self.assertEqual(applicable["as_of"], DAY.isoformat())
        self.assertEqual(applicable["state"], "applicable")
        self.assertTrue(applicable["premise"])
        self.assertEqual(applicable["local_approval"], "matched")
        self.assertEqual(applicable["declared_validity"], {"state": "unknown", "valid_from": None, "valid_until": None})
        self.assertIn("validity_unknown", applicable["reasons"])
        self.assertEqual(self.public_decision.read_bytes(), self.protected[self.public_decision])

    def test_explicit_future_expired_and_draft_decisions_are_only_nonbinding_references(self) -> None:
        cases = [("Future applicability", True, {"valid_from": "2026-09-18"}, "not_yet"),
                 ("Expired applicability", True, {"valid_from": "2026-09-01", "valid_until": "2026-09-17"}, "expired"),
                 ("Draft applicability", False, {}, "unknown")]
        for title, approved, fields, validity in cases:
            identity, path = self.decision(title, approved=approved, **fields)
            with self.subTest(title=title):
                _, context = self.context(identity)
                applicable = context["applicability"]
                self.assertEqual(applicable["state"], "reference")
                self.assertFalse(applicable["premise"])
                self.assertEqual(applicable["declared_validity"]["state"], validity)
                self.assertEqual(context["record"]["path"], path.relative_to(self.root).as_posix())

    def test_draft_reference_documents_continue_as_sources_without_becoming_decisions(self) -> None:
        path = note(self.root, "notes/draft-reference.md", "REFERENCE_MATCH boundary source", type="reference", status="draft")
        report, context = self.context("notes/draft-reference")
        self.assertEqual(context["applicability"]["state"], "not_decision")
        self.assertFalse(context["applicability"]["premise"])
        self.assertIn("REFERENCE_MATCH", context["content"])
        queried = self.compile(targets=[], query="REFERENCE_MATCH")
        self.assertIn(path.relative_to(self.root).as_posix(), {item["record"]["path"] for item in queried["contexts"]})
        self.assertFalse(report["execution_authorized"])

    def test_invalid_declared_validity_is_explicit_nonbinding_even_with_a_matching_receipt(self) -> None:
        identity, _ = self.decision("Invalid declared interval", valid_from="2026-02-30")
        _, context = self.context(identity)
        self.assertEqual(context["applicability"]["local_approval"], "matched")
        self.assertEqual(context["applicability"]["declared_validity"]["state"], "invalid")
        self.assertFalse(context["applicability"]["premise"])
        self.assertIn("validity_invalid", context["applicability"]["reasons"])

    def test_visible_duplicate_canonical_identity_is_unknown_while_hidden_duplicate_is_silent(self) -> None:
        baseline = self.compile()
        duplicate = self.public_decision.with_name("d-001-duplicate.md")
        text = self.public_decision.read_text(encoding="utf-8")
        duplicate.write_text(_frontmatter_replace(text, "sensitivity", "restricted"), encoding="utf-8")
        self.assertEqual(self.compile(), baseline)
        duplicate.write_text(text, encoding="utf-8")
        _, context = self.context(self.public_decision.relative_to(self.root).as_posix())
        self.assertEqual(context["applicability"]["state"], "unknown")
        self.assertFalse(context["applicability"]["premise"])
        self.assertIn("ambiguous_decision", context["applicability"]["reasons"])

    def test_ineligible_decisions_are_removed_before_both_rankers_stats_and_doc_budget(self) -> None:
        good, good_path = self.decision("Eligible governance choice", body="ELIGIBILITY_NEEDLE")
        _, draft_path = self.decision("ELIGIBILITY_NEEDLE", approved=False, body="ELIGIBILITY_NEEDLE " * 40)
        _, future_path = self.decision("Future ELIGIBILITY_NEEDLE", valid_from="2026-09-18", body="ELIGIBILITY_NEEDLE " * 40)
        _, expired_path = self.decision("Expired ELIGIBILITY_NEEDLE", valid_until="2026-09-17", body="ELIGIBILITY_NEEDLE " * 40)
        denied = {draft_path, future_path, expired_path}
        for ranking, name in (("substring", "query_vault"), ("bm25", "retrieve_bm25")):
            original = getattr(compiler, name)
            def checked(*args, original=original, **kwargs):
                paths = {item.path for item in kwargs["vault"].notes}
                self.assertTrue(paths.isdisjoint(denied))
                self.assertIn(good_path, paths)
                return original(*args, **kwargs)
            with self.subTest(ranking=ranking):
                with patch.object(compiler, name, side_effect=checked):
                    report = self.compile(targets=[], query="ELIGIBILITY_NEEDLE", ranking=ranking, max_docs=1)
                self.assertEqual([item["record"]["decision_id"] for item in report["contexts"]], [good])
                self.assertTrue(report["contexts"][0]["applicability"]["premise"])
                self.assertEqual(report["omissions"]["query_matches"], 0)

    def test_missing_granted_approval_history_never_falls_back_to_uncaptured_log(self) -> None:
        grant = VaultIndex.load(self.root).subset(lambda item: item.path == self.public_decision)
        grant.derived["evidence_register"] = ({}, {}, [])
        original_read = compiler._read
        original_text = Path.read_text
        original_bytes = Path.read_bytes
        omitted = {self.log, self.root / "06-decisions/decision-log.md"}
        def read(path):
            self.assertNotIn(Path(path), omitted)
            return original_read(path)
        def read_text(path, *args, **kwargs):
            self.assertNotIn(path, omitted)
            return original_text(path, *args, **kwargs)
        def read_bytes(path, *args, **kwargs):
            self.assertNotIn(path, omitted)
            return original_bytes(path, *args, **kwargs)
        with patch.object(VaultIndex, "load", side_effect=AssertionError("grant expansion")), patch.object(compiler, "_read", side_effect=read), patch.object(Path, "read_text", read_text), patch.object(Path, "read_bytes", read_bytes):
            _, context = self.context("D-001", vault=grant)
        self.assertFalse(context["applicability"]["premise"])
        self.assertEqual(context["applicability"]["local_approval"], "unavailable")
        self.assertTrue(context["history"]["unavailable"])

    def test_future_or_draft_successor_relation_does_not_itself_supersede_a_current_predecessor(self) -> None:
        self.decision("Draft successor", approved=False, supersedes="D-001")
        _, predecessor = self.context("D-001")
        self.assertTrue(predecessor["applicability"]["premise"])
        future, _ = self.decision("Future successor", valid_from="2026-09-18")
        old_hash = approval_record_hash(self.public_decision.read_text(encoding="utf-8"))
        self.public_decision.write_text(_frontmatter_replace(self.public_decision.read_text(encoding="utf-8"), "superseded_by", future), encoding="utf-8")
        self.assertEqual(approval_record_hash(self.public_decision.read_text(encoding="utf-8")), old_hash)
        _, predecessor = self.context("D-001")
        self.assertTrue(predecessor["applicability"]["premise"])
        self.assertEqual(predecessor["record"]["status"], "approved")

    def test_persisted_superseded_predecessor_remains_nonbinding_with_successor_outside_grant(self) -> None:
        successor, successor_path = self.decision("Current successor", supersedes="D-001")
        self.assertEqual(load_note(self.public_decision).front["status"], "superseded")
        grant = VaultIndex.load(self.root).subset(lambda item: item.path != successor_path)
        _, predecessor = self.context("D-001", vault=grant)
        self.assertFalse(predecessor["applicability"]["premise"])
        self.assertEqual(predecessor["applicability"]["state"], "reference")
        self.assertEqual(predecessor["history"]["superseded_by"], [])

    def test_review_history_is_bounded_row_local_cited_and_explicitly_truncated(self) -> None:
        for index in range(12):
            self.append_review(text="Visible row Żółć 東京 " + str(index))
        report, context = self.context("D-001", max_bytes=16000)
        history = context["history"]
        self.assertLessEqual(len(history["reviews"]), 8)
        self.assertTrue(history["truncated"])
        self.assertFalse(history["unavailable"])
        citations = {(item["kind"], item["target"], item["sha256"]) for item in report["citations"]}
        for review in history["reviews"]:
            self.assertRegex(review["row_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(("review", f"00-context/review-log.md#{review['row_sha256']}:{review['occurrence']}", review["row_sha256"]), citations)
        wire = serialize_compiled(report).encode("utf-8")
        self.assertEqual(len(wire), report["budget"]["used_bytes"])
        self.assertLessEqual(len(wire), 16000)
        self.assertTrue(wire.endswith(b"\n"))
        for budget in (compiler.MIN_MAX_BYTES, 2048, 4096):
            bounded = self.compile(max_bytes=budget)
            encoded = serialize_compiled(bounded).encode("utf-8")
            self.assertLessEqual(len(encoded), budget)
            self.assertEqual(json.loads(encoded), bounded)
            if bounded["status"] == "compiled":
                self.assertTrue(bounded["contexts"][0]["history"]["truncated"])

    def test_many_visible_draft_successors_bound_relations_without_displacing_the_current_premise(self) -> None:
        for index in range(10):
            self.decision("Future proposal " + str(index), approved=False, supersedes="D-001")
        report, context = self.context("D-001")
        self.assertTrue(context["applicability"]["premise"])
        self.assertLessEqual(len(context["history"]["superseded_by"]), 8)
        self.assertTrue(context["history"]["truncated"])
        self.assertLessEqual(len(serialize_compiled(report).encode("utf-8")), report["budget"]["max_bytes"])

    def test_half_open_expiry_changes_applicability_and_makes_the_previous_report_stale(self) -> None:
        identity, _ = self.decision("Half open interval", valid_from="2026-09-01", valid_until="2026-09-18")
        report, current = self.context(identity)
        self.assertTrue(current["applicability"]["premise"])
        self.assertEqual(current["applicability"]["declared_validity"]["state"], "active")
        tomorrow = DAY + dt.timedelta(days=1)
        self.assertEqual(verify_compiled(self.root, report, today=tomorrow, allowed_sensitivities={"public"})["status"], "stale")
        _, expired = self.context(identity, today=tomorrow)
        self.assertFalse(expired["applicability"]["premise"])
        self.assertEqual(expired["applicability"]["declared_validity"]["state"], "expired")

    def test_visible_history_changes_stale_report_but_hidden_rows_keep_hashes_and_occurrences(self) -> None:
        self.append_review(text="Duplicate visible row")
        self.append_review(text="Duplicate visible row")
        report, context = self.context("D-001")
        twins = [item for item in context["history"]["reviews"] if item["outcome"] == "confirmed"]
        self.assertEqual(sorted(item["occurrence"] for item in twins), [1, 2])
        self.hidden_review()
        self.assertEqual(self.compile(), report)
        self.append_review(text="New visible review")
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities={"public"})["status"], "stale")

    def test_future_local_approval_and_record_timestamps_cannot_be_current_premises(self) -> None:
        future, future_path = self.decision("Tomorrow approved", today=DAY + dt.timedelta(days=1))
        _, context = self.context(future)
        self.assertFalse(context["applicability"]["premise"])
        self.assertIn("record_future", context["applicability"]["reasons"])
        queried = self.compile(targets=[], query="Tomorrow approved")
        self.assertNotIn(future_path.relative_to(self.root).as_posix(), {item["record"]["path"] for item in queried["contexts"]})

    def test_future_review_confirmation_cannot_renew_current_governance(self) -> None:
        original = self.public_decision.read_text(encoding="utf-8")
        receipt_hash = approval_record_hash(original)
        relative = self.public_decision.relative_to(self.root).with_suffix("").as_posix()
        row = f"| 2026-09-18 | [[{relative}]] | Ada Example | confirmed | 2026-12-16 | 2027-01-01 | Future renewal |\n"
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(row)
        for updated in ("2026-09-17", "2026-09-18"):
            text = _frontmatter_replace(original, "review_by", "2027-01-01")
            text = _frontmatter_replace(text, "last_updated", updated)
            self.public_decision.write_text(text, encoding="utf-8")
            self.assertEqual(approval_record_hash(text), receipt_hash)
            with self.subTest(updated=updated):
                _, context = self.context("D-001")
                self.assertFalse(context["applicability"]["premise"])
                self.assertEqual(context["applicability"]["declared_validity"]["state"], "unknown")

    def test_backdated_confirmation_cannot_clear_a_later_dated_required_review(self) -> None:
        record_review(self.root, "D-001", reviewer="Ada Example", outcome="update-required",
                       note_text="New evidence requires review", today=dt.date(2026, 9, 30))
        record_review(self.root, "D-001", reviewer="Ada Example", outcome="confirmed",
                       next_review="2026-12-20", today=dt.date(2026, 9, 20))
        _, context = self.context("D-001", today=dt.date(2026, 10, 1))
        self.assertFalse(context["applicability"]["premise"])
        self.assertIn(context["applicability"]["state"], {"needs_review", "unknown"})

    def test_overdue_review_and_stale_evidence_are_needs_review_not_query_premises(self) -> None:
        _, context = self.context("D-001", today=dt.date(2026, 12, 17))
        self.assertEqual(context["applicability"]["state"], "needs_review")
        self.assertFalse(context["applicability"]["premise"])
        self.assertIn("review_overdue", context["applicability"]["reasons"])
        self.assertIn("evidence_stale", context["applicability"]["reasons"])
        report = self.compile(targets=[], query="Bind approval", today=dt.date(2026, 12, 17))
        self.assertTrue(all(item["record"].get("decision_id") != "D-001" for item in report["contexts"]))

    def test_visible_malformed_review_event_cannot_hide_behind_an_older_valid_approval(self) -> None:
        relative = self.public_decision.relative_to(self.root).with_suffix("").as_posix()
        row = f"| 2026-09-17 | [[{relative}]] | Ada Example | confirmed | — | — | Visible note | Unexpected eighth cell |\n"
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(row)
        _, context = self.context("D-001")
        self.assertFalse(context["applicability"]["premise"])
        self.assertTrue(context["history"]["unavailable"])

    def test_hidden_malformed_review_metadata_does_not_change_public_history_or_snapshot(self) -> None:
        baseline = self.compile()
        original = self.log.read_text(encoding="utf-8")
        for suffix in (" | Unexpected eighth cell", ""):
            row = f"| not-a-date | [[06-decisions/{self.hidden.stem}]] | {PRIVATE_MARKERS[1]} | invented | — | — | {PRIVATE_MARKERS[0]}{suffix} |\n"
            self.log.write_text(original + row, encoding="utf-8")
            self.assertEqual(self.compile(), baseline)
            self.assertEqual(verify_compiled(self.root, baseline, today=DAY, allowed_sensitivities={"public"})["status"], "current")

    def test_snapshot_does_not_mix_current_body_or_history_with_an_earlier_captured_hash(self) -> None:
        original = compiler._read
        raw = self.public_decision.read_bytes()
        changed = False
        def swapped(path):
            nonlocal changed
            value = original(path)
            if Path(path) == self.public_decision and not changed:
                self.public_decision.write_bytes(raw + b"\nBODY_SWAP_BOUNDARY_CANARY\n")
                changed = True
            return value
        with patch.object(compiler, "_read", side_effect=swapped):
            report = self.compile()
        self.assertTrue(changed)
        self.assertNotIn("BODY_SWAP_BOUNDARY_CANARY", serialize_compiled(report))
        context = report["contexts"][0]
        self.assertEqual(context["applicability"]["local_approval"], "matched")
        document = next(item for item in report["citations"] if item["target"] == self.public_decision.relative_to(self.root).as_posix())
        self.assertEqual(document["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities={"public"})["status"], "stale")

    def test_history_uses_captured_log_rows_without_a_later_filesystem_fallback(self) -> None:
        baseline = self.compile()
        original = compiler._read
        changed = False
        def swapped(path):
            nonlocal changed
            value = original(path)
            if Path(path) == self.log and not changed:
                self.append_review(text="POST_CAPTURE_HISTORY_CANARY")
                changed = True
            return value
        with patch.object(compiler, "_read", side_effect=swapped):
            report = self.compile()
        self.assertTrue(changed)
        self.assertEqual(report, baseline)
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities={"public"})["status"], "stale")

    def test_decision_ledger_lint_uses_the_captured_snapshot_and_not_later_disk_bytes(self) -> None:
        baseline = self.compile()
        decision_log = self.root / "06-decisions/decision-log.md"
        original = compiler._read
        changed = False
        def swapped(path):
            nonlocal changed
            value = original(path)
            if Path(path) == decision_log and not changed:
                text = value.decode("utf-8")
                row = next(line for line in text.splitlines(keepends=True) if line.startswith("| D-001 |"))
                decision_log.write_text(text.replace(row, "", 1), encoding="utf-8")
                changed = True
            return value
        with patch.object(compiler, "_read", side_effect=swapped):
            report = self.compile()
        self.assertTrue(changed)
        self.assertEqual(report, baseline)
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities={"public"})["status"], "stale")

    def test_full_utf8_schema_reforged_applicability_and_old_pack_compatibility(self) -> None:
        before = tree(self.root)
        legacy = build_pack(self.root, targets=["D-001"], max_docs=2, max_chars=4000)
        with persistent():
            report = self.compile()
        schema = json.loads((ROOT / "schemas/compiled-context.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])
        forged = json.loads(serialize_compiled(report))
        forged["contexts"][0]["applicability"]["premise"] = False
        compiler._seal(forged)
        self.assertNotEqual(verify_compiled(self.root, forged, today=DAY, allowed_sensitivities={"public"})["status"], "current")
        self.assertFalse(report["execution_authorized"])
        self.assertEqual(build_pack(self.root, targets=["D-001"], max_docs=2, max_chars=4000), legacy)
        self.assertEqual(tree(self.root), before)
