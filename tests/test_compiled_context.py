"""Wire budgets, grant isolation and unsigned freshness of compiled context."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import unsupported_keywords, validate
from whykit.compiled_context import (
    CompiledContextError, MIN_MAX_BYTES, _seal, compile_context, serialize_compiled, verify_compiled,
)
from whykit.pack import build_pack
from whykit.sensitivity import classified_evidence
from whykit.vault_index import VaultIndex

DAY = dt.date(2026, 9, 17)


def note(root, path, body, **metadata):
    fields = {"title": "Visible record", "aliases": [], "type": "guide", "status": "draft",
              "owner": "Ada Example", "created": DAY.isoformat(), "last_updated": DAY.isoformat(),
              "source_of_truth": False, "source_ids": [], "tags": [], "sensitivity": "public", **metadata}
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}"
                                            for key, value in fields.items()) + "\n---\n\n" + body,
                      encoding="utf-8")
    return target


class CompiledContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "vault"
        self.root.mkdir()
        (self.root / "00-context").mkdir()
        note(self.root, "Home.md", "Vault map")
        self.path = note(self.root, "notes/visible.md", "needle public process")
        self.register = note(self.root, "00-context/evidence-register.md",
            "| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| E-001 | Public source | fixture | 2026-09-17 | 2026-09-17 | https://example.com | Public fact | public |\n"
            "| E-002 | PRIVATE_SOURCE | fixture | 2026-09-17 | 2026-09-17 | https://private.example | PRIVATE_CLAIM | restricted |\n")
        self.policy = self.root / "whykit.toml"
        self.policy.write_text("[evidence_access_age_days]\nfixture = 90\n", encoding="utf-8")

    def compile(self, **kwargs):
        return compile_context(self.root, today=DAY, **{"targets": ["notes/visible"], **kwargs})

    def verify(self, report, **kwargs):
        return verify_compiled(self.root, report, today=DAY, **kwargs)

    def test_deterministic_wire_manifest_and_no_writes(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        report = self.compile()
        self.assertEqual(report, self.compile())
        wire = serialize_compiled(report)
        self.assertEqual(json.loads(wire), report)
        self.assertEqual(report["budget"]["used_bytes"], len(wire.encode("utf-8")))
        self.assertLessEqual(len(wire.encode("utf-8")), report["budget"]["max_bytes"])
        self.assertEqual(self.verify(wire)["status"], "current")
        self.assertFalse(report["execution_authorized"])
        self.assertFalse(self.verify(report)["signature_verified"])
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_host_persistent_cache_scope_does_not_create_mutation_state(self):
        from whykit.parse_cache import persistent
        with persistent():
            report = self.compile()
            self.assertEqual(self.verify(report)["status"], "current")
        self.assertFalse((self.root / ".whykit").exists())

    def test_utf8_budget_covers_entire_json_and_newline(self):
        note(self.root, "notes/visible.md", "Zażółć 🛰️ 東京\n" * 5000)
        report = self.compile(max_bytes=4096)
        self.assertEqual(report["status"], "compiled")
        wire = serialize_compiled(report).encode("utf-8")
        self.assertLessEqual(len(wire), 4096)
        self.assertEqual(len(wire), report["budget"]["used_bytes"])
        self.assertTrue(report["contexts"][0]["content_truncated"])
        self.assertEqual(self.verify(wire)["status"], "current")

    def test_tiny_budgets_are_explicit_complete_refusals(self):
        with self.assertRaisesRegex(CompiledContextError, "budget_too_small"):
            self.compile(max_bytes=1)
        report = self.compile(max_bytes=MIN_MAX_BYTES)
        self.assertEqual(report["status"], "refused")
        self.assertLessEqual(len(serialize_compiled(report).encode()), MIN_MAX_BYTES)
        self.assertEqual(json.loads(serialize_compiled(report))["error"], "budget_too_small")
        self.assertEqual(self.verify(report)["status"], "refused")

    def test_hidden_notes_titles_aliases_and_counts_do_not_affect_artifact(self):
        hidden = note(self.root, "notes/hidden.md", "needle PRIVATE_BODY", title="PRIVATE_TITLE",
                      aliases=["PRIVATE_ALIAS"], sensitivity="restricted")
        for ranking in ("substring", "bm25"):
            report = self.compile(targets=[], query="needle", ranking=ranking)
            wire = serialize_compiled(report)
            for marker in ("PRIVATE_BODY", "PRIVATE_TITLE", "PRIVATE_ALIAS", "hidden.md"):
                self.assertNotIn(marker, wire)
            hidden.write_text(hidden.read_text(encoding="utf-8").replace("PRIVATE_BODY", "changed private body")
                              .replace("PRIVATE_TITLE", "CHANGED_TITLE").replace("PRIVATE_ALIAS", "CHANGED_ALIAS"), encoding="utf-8")
            note(self.root, "notes/another-hidden.md", "needle hidden", sensitivity="restricted")
            self.assertEqual(report, self.compile(targets=[], query="needle", ranking=ranking))
            self.assertEqual(self.verify(report)["status"], "current")

    def test_hidden_target_is_indistinguishable_from_missing(self):
        hidden = note(self.root, "notes/hidden.md", "PRIVATE_BODY", sensitivity="restricted")
        report = self.compile(targets=["notes/hidden"])
        hidden.unlink()
        self.assertEqual(report, self.compile(targets=["notes/hidden"]))
        self.assertFalse(report["contexts"])
        self.assertEqual(report["omissions"]["unresolved"][0]["reason"], "missing")

    def test_per_row_register_and_hidden_evidence_never_enter_context(self):
        note(self.root, "notes/visible.md", "needle E-001 and E-002", source_ids=["E-001", "E-002"])
        report = self.compile(targets=["notes/visible", "E-001", "E-002", "00-context/evidence-register"])
        wire = serialize_compiled(report)
        self.assertNotIn("PRIVATE_SOURCE", wire)
        self.assertNotIn("PRIVATE_CLAIM", wire)
        self.assertNotIn("private.example", wire)
        self.assertNotIn('"line":"', wire)
        self.assertNotIn("| E-001 |", wire)
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("PRIVATE_SOURCE", "OTHER_SECRET"), encoding="utf-8")
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("| E-001 |", "| E-777 | Hidden insertion | fixture | 2026-09-17 | 2026-09-17 | https://hidden.example | Secret fact | restricted |\n| E-001 |", 1), encoding="utf-8")
        self.assertEqual(report, self.compile(targets=["notes/visible", "E-001", "E-002", "00-context/evidence-register"]))
        self.assertEqual(self.verify(report)["status"], "current")

    def test_granted_index_is_never_replaced_by_full_reload(self):
        note(self.root, "notes/ungranted.md", "needle UNGRANTED_BODY")
        grant = VaultIndex.load(self.root).subset(lambda item: item.path == self.path)
        with patch("whykit.vault_index.VaultIndex.load", side_effect=AssertionError("full reload")):
            report = self.compile(targets=[], query="needle", ranking="bm25", vault=grant)
            self.assertEqual(self.verify(report, vault=grant)["status"], "current")
        self.assertEqual(len(report["contexts"]), 1)
        self.assertNotIn("UNGRANTED_BODY", serialize_compiled(report))
        self.assertNotIn("ungranted.md", serialize_compiled(report))

    def test_hidden_raw_register_and_denied_notes_are_not_hashed(self):
        hidden = note(self.root, "notes/hidden.md", "PRIVATE_HASH_INPUT", sensitivity="restricted")
        forbidden = {self.register.read_bytes(), hidden.read_bytes()}
        original = hashlib.sha256
        def guarded(raw=b"", **kwargs):
            self.assertNotIn(raw, forbidden)
            return original(raw, **kwargs)
        with patch("hashlib.sha256", side_effect=guarded):
            self.compile()

    def test_native_claim_quotes_fail_closed_before_scoring_or_hashing(self):
        claim = note(self.root, "00-context/claims/c-001.md", 'needle PRIVATE_CLAIM_QUOTE',
                     type="claim", source_ids=["E-002"], claim_id="C-001")
        for ranking in ("substring", "bm25"):
            report = self.compile(targets=[], query="needle", ranking=ranking)
            self.assertNotIn("PRIVATE_CLAIM_QUOTE", serialize_compiled(report))
            claim.write_text(claim.read_text(encoding="utf-8").replace("PRIVATE_CLAIM_QUOTE", "CHANGED_QUOTE"), encoding="utf-8")
            self.assertEqual(report, self.compile(targets=[], query="needle", ranking=ranking))

    def test_restricted_capture_claim_compilation_and_scoped_evidence_grant(self):
        from whykit.artifact_store import capture_artifact
        from whykit.claim_ledger import create_claim

        source = self.root.parent / "synthetic-source.txt"
        raw = b"Quoted process"
        source.write_bytes(raw)
        stamp = "2026-09-17T00:00:00Z"
        preview = capture_artifact(self.root, "E-001", source, recorded_at=stamp)
        capture_artifact(self.root, "E-001", source, recorded_at=stamp,
                         write=True, expected_sha256=preview["expected_sha256"])
        links = [{"evidence_id": "E-001", "source_version": "sha256:" + hashlib.sha256(raw).hexdigest(),
                  "location": {"start_byte": 0, "end_byte": len(raw), "quote": raw.decode()},
                  "assessment": {"kind": "human", "by": "Ada Example", "relation": "supports"}}]
        options = {"title": "Process claim", "owner": "Ada Example", "today": DAY}
        preview = create_claim(self.root, "needle source describes the process", links, **options)
        claim = create_claim(self.root, "needle source describes the process", links, write=True,
                             expected_sha256=preview["expected_sha256"], **options)
        allowed = {"public", "internal", "confidential", "restricted"}
        report = self.compile(targets=[claim["claim_id"]], allowed_sensitivities=allowed)
        self.assertEqual(report["status"], "compiled")
        self.assertIn("Quoted process", serialize_compiled(report))
        self.assertEqual(report["contexts"][0]["record"]["claim_id"], claim["claim_id"])
        self.assertEqual(self.verify(report, allowed_sensitivities=allowed)["status"], "current")
        for ceiling in ({"public"}, {"public", "internal"}):
            denied = self.compile(targets=[claim["claim_id"]], allowed_sensitivities=ceiling)
            self.assertNotIn("Quoted process", serialize_compiled(denied))
            self.assertEqual(denied["contexts"], [])
        full = VaultIndex.load(self.root)
        grant = full.subset(lambda item: item.path == self.root / claim["path"])
        grant.derived["evidence_register"] = ({}, {}, [])
        denied = self.compile(targets=[claim["claim_id"]], allowed_sensitivities=allowed, vault=grant)
        self.assertEqual(denied["contexts"], [])
        self.assertNotIn("Quoted process", serialize_compiled(denied))
        claim_path = self.root / claim["path"]
        claim_path.write_text(claim_path.read_text(encoding="utf-8").replace('type: "claim"', 'type: "reference"')
                              .replace("type: claim", "type: reference"), encoding="utf-8")
        denied = self.compile(targets=[claim["claim_id"]], allowed_sensitivities=allowed, vault=grant)
        self.assertEqual(denied["contexts"], [])
        self.assertNotIn("Quoted process", serialize_compiled(denied))
        claim_path.write_text("\n".join(line for line in claim_path.read_text(encoding="utf-8").splitlines()
                                         if not line.startswith("claim_id:")) + "\n", encoding="utf-8")
        denied = self.compile(targets=[claim["path"]], allowed_sensitivities=allowed, vault=grant)
        self.assertEqual(denied["contexts"], [])
        self.assertNotIn("Quoted process", serialize_compiled(denied))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_refresh_refuses_fifo_and_caps_regular_note_reads(self):
        grant = VaultIndex.load(self.root).subset(lambda item: item.path == self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaisesRegex(CompiledContextError, "unavailable_inputs"):
            self.compile(vault=grant)
        self.path.unlink()
        note(self.root, "notes/visible.md", "x" * 1000)
        with patch("whykit.compiled_context.MAX_SOURCE_BYTES", 128):
            with self.assertRaisesRegex(CompiledContextError, "input_too_large"):
                self.compile(vault=grant)

    def test_explicit_decision_id_is_preserved_for_preflight_coverage(self):
        note(self.root, "06-decisions/d-001-process.md", "Review the process E-001", type="decision", decision_id="D-001")
        report = self.compile(targets=["D-001"])
        self.assertEqual(report["contexts"][0]["record"]["decision_id"], "D-001")

    def test_hidden_decision_log_cannot_change_visible_warning_codes(self):
        note(self.root, "06-decisions/d-001-process.md", "Public process E-001", type="decision", decision_id="D-001")
        log = note(self.root, "06-decisions/decision-log.md",
                   "| D-001 | Private index title | 2026-09-17 | Ada Example | proposed | [[06-decisions/d-001-process]] |\n",
                   sensitivity="restricted")
        report = self.compile(targets=["D-001"])
        self.assertTrue(any(item["code"] == "compiled.decision_log_unavailable" for item in report["warnings"]))
        log.write_text(log.read_text(encoding="utf-8").replace("D-001", "D-999"), encoding="utf-8")
        self.assertEqual(report, self.compile(targets=["D-001"]))

    def test_selected_evidence_warnings_survive_without_raw_line_numbers(self):
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("| 2026-09-17 | 2026-09-17 |", "| bad | 2026-09-17 |", 1), encoding="utf-8")
        report = self.compile(targets=["E-001"])
        self.assertTrue(any(item["code"] == "evidence.date" for item in report["warnings"]))
        self.assertTrue(all(item["line"] is None for item in report["warnings"]))

    def test_granted_evidence_is_refreshed_without_expanding_row_grant(self):
        original = VaultIndex.load(self.root)
        active, retired, occurrences, _ = classified_evidence(original)
        grant = original.subset(lambda item: item.path == self.path)
        grant.derived["evidence_register"] = ({"E-001": active["E-001"]}, {}, occurrences)
        report = self.compile(targets=["E-001"], vault=grant)
        self.assertIn("Public source", serialize_compiled(report))
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("Public source", "Updated public source"), encoding="utf-8")
        self.assertEqual(self.verify(report, vault=grant)["status"], "stale")
        self.assertNotIn("PRIVATE_SOURCE", serialize_compiled(self.compile(targets=["E-001"], vault=grant)))

    def test_public_current_bytes_policy_new_match_and_date_are_bound(self):
        report = self.compile(targets=[], query="needle", max_docs=1)
        self.assertEqual(self.verify(report, ranking="bm25")["reason"], "parameters_changed")
        self.assertEqual(verify_compiled(self.root, report, today=DAY + dt.timedelta(days=1))["reason"], "as_of_changed")
        note(self.root, "notes/new.md", "needle new match")
        self.assertEqual(self.verify(report)["status"], "stale")
        (self.root / "notes/new.md").unlink()
        self.path.write_text(self.path.read_text(encoding="utf-8") + "\npublic update", encoding="utf-8")
        self.assertEqual(self.verify(report)["status"], "stale")
        report = self.compile()
        self.policy.write_text("[evidence_access_age_days]\nfixture = 91\n", encoding="utf-8")
        self.assertEqual(self.verify(report)["status"], "stale")

    def test_granted_path_metadata_is_refreshed_before_current_label_filtering(self):
        path = note(self.root, "notes/reclassified.md", "needle now public", sensitivity="restricted")
        grant = VaultIndex.load(self.root)
        report = self.compile(targets=[], query="needle", vault=grant)
        self.assertNotIn("reclassified.md", serialize_compiled(report))
        path.write_text(path.read_text(encoding="utf-8").replace('"restricted"', '"public"'), encoding="utf-8")
        self.assertEqual(self.verify(report, vault=grant)["status"], "stale")
        current = self.compile(targets=[], query="needle", vault=grant)
        self.assertIn("reclassified.md", serialize_compiled(current))
        self.assertEqual(self.verify(current, vault=grant)["status"], "current")

    def test_denied_body_size_and_encoding_do_not_affect_a_path_grant(self):
        hidden = note(self.root, "notes/denied.md", "private body", sensitivity="restricted")
        grant = VaultIndex.load(self.root)
        report = self.compile(vault=grant)
        header = hidden.read_bytes().split(b"\n---\n", 1)[0] + b"\n---\n"
        hidden.write_bytes(header + b"\xff" * 5000)
        with patch("whykit.compiled_context.MAX_SOURCE_BYTES", 2048):
            self.assertEqual(report, self.compile(vault=grant))

    def test_forged_content_snapshot_and_extra_keys_are_detected(self):
        report = self.compile()
        for mutate in (lambda r: r["contexts"][0].update(content="Forged content"),
                       lambda r: r["manifest"].update(snapshot_sha256="a" * 64),
                       lambda r: r["contexts"][0].update(untrusted_extra=True)):
            forged = copy.deepcopy(report)
            mutate(forged)
            _seal(forged)
            self.assertEqual(self.verify(forged)["status"], "stale")
        self.assertEqual(self.verify({**report, "untrusted_extra": True})["status"], "refused")
        forged = copy.deepcopy(report)
        forged["inputs"]["max_docs"] = True
        self.assertEqual(self.verify(forged)["status"], "refused")

    def test_untrusted_report_is_bounded_and_errors_do_not_echo_private_values(self):
        for raw in (b'{"field":NaN}', b'{"field":Infinity}', b'{"field":1,"field":2}', b'[' * 2000,
                    b'\xff', b' ' * (4 * 1024 * 1024 + 1)):
            result = self.verify(raw)
            self.assertEqual(result["status"], "refused")
        report = self.compile()
        for value in (float("nan"), float("inf"), "x" * (4 * 1024 * 1024 + 1)):
            forged = {**report, "preamble": value}
            self.assertEqual(self.verify(forged)["status"], "refused")
        cyclic = self.compile()
        cyclic["contexts"].append(cyclic)
        self.assertEqual(self.verify(cyclic)["status"], "refused")
        with patch("whykit.compiled_context.parse_config", side_effect=OSError("PRIVATE_ERROR_MARKER")):
            with self.assertRaises(CompiledContextError) as caught:
                self.compile()
        self.assertNotIn("PRIVATE_ERROR_MARKER", str(caught.exception))

    def test_scope_cannot_be_expanded_by_untrusted_report(self):
        note(self.root, "notes/internal.md", "INTERNAL_BODY", sensitivity="internal")
        report = self.compile(targets=["notes/internal"], allowed_sensitivities={"internal"})
        self.assertEqual(self.verify(report)["reason"], "scope_mismatch")
        self.assertEqual(self.verify(report, allowed_sensitivities={"internal"})["status"], "current")

    def test_visible_warnings_survive_body_truncation_and_legacy_pack_is_unchanged(self):
        note(self.root, "notes/visible.md", "needle " * 5000, status="approved", review_by="2026-09-16")
        legacy = build_pack(self.root, targets=["notes/visible"])
        report = self.compile(max_bytes=4096)
        self.assertTrue(report["warnings"])
        self.assertTrue(report["contexts"][0]["content_truncated"])
        self.assertEqual(legacy, build_pack(self.root, targets=["notes/visible"]))
        self.assertEqual(legacy["format"], "whykit.context-bundle/v1")

    def test_symlink_and_outside_targets_do_not_open_external_content(self):
        outside = self.root.parent / "outside.md"
        outside.write_text("PRIVATE_EXTERNAL_BODY", encoding="utf-8")
        self.path.unlink()
        try:
            self.path.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        report = self.compile(targets=["notes/visible", str(outside)])
        self.assertNotIn("PRIVATE_EXTERNAL_BODY", serialize_compiled(report))
        self.assertEqual(report["contexts"], [])
        self.policy.unlink()
        self.policy.symlink_to(outside)
        with self.assertRaisesRegex(CompiledContextError, "unavailable_inputs"):
            self.compile()

    def test_public_schema_describes_success_and_refusal(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/compiled-context.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(unsupported_keywords(schema), [])
        for report in (self.compile(), self.compile(max_bytes=MIN_MAX_BYTES)):
            self.assertEqual(validate(report, schema), [])
        tampered = self.compile()
        tampered["execution_authorized"] = True
        self.assertTrue(validate(tampered, schema))


if __name__ == "__main__":
    unittest.main()
