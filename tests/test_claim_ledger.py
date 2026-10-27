"""Canonical claim records preserve legacy data and declarative assessments."""
from __future__ import annotations

import concurrent.futures
from contextlib import ExitStack
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from _vaults import approved_decision, fresh_vault
from test_artifact_store import STAMP, register, tree_bytes
from whykit.artifact_store import capture_artifact
from whykit.claim_ledger import claim_evidence_ids, claim_visibility, create_claim, list_claims, read_claim, verify_claim
from whykit.explorer_index import build_explorer_index
from whykit.lint import lint, load_note
from whykit.mcp_server import VaultTools

TODAY = dt.date(2026, 10, 27)


class ClaimLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "vault"
        self.root.mkdir()
        register(self.root)
        self.source = self.root.parent / "source.bin"
        self.raw = b"\xef\xbb\xbf" + "Źródło: żółw\r\n".encode()
        self.source.write_bytes(self.raw)
        start = self.raw.index("ż".encode())
        self.link = {
            "evidence_id": "E-001", "source_version": "sha256:" + hashlib.sha256(self.raw).hexdigest(),
            "location": {"start_byte": start, "end_byte": start + len("żółw".encode()), "quote": "żółw"},
            "assessment": {"kind": "model", "by": "example-model/v1", "relation": "supports"},
        }

    def create(self, **kwargs) -> dict:
        return create_claim(self.root, "Źródło zawiera słowo żółw.", [self.link], title="Example claim", owner="Ada Example", today=TODAY, **kwargs)

    def apply(self, **kwargs) -> dict:
        plan = self.create(**kwargs)
        return self.create(write=True, expected_sha256=plan["expected_sha256"], **kwargs)

    def capture(self) -> dict:
        plan = capture_artifact(self.root, "E-001", self.source, recorded_at=STAMP)
        return capture_artifact(self.root, "E-001", self.source, recorded_at=STAMP, write=True, expected_sha256=plan["expected_sha256"])

    def observe(self, raw: bytes, stamp: str) -> dict:
        self.source.write_bytes(raw)
        plan = capture_artifact(self.root, "E-001", self.source, recorded_at=stamp)
        return capture_artifact(self.root, "E-001", self.source, recorded_at=stamp,
                                write=True, expected_sha256=plan["expected_sha256"])

    def test_anchor_match_and_observed_source_currentness_are_separate(self) -> None:
        first = self.capture()
        claim = self.apply()
        original = {name: (self.root / name).read_bytes() for name in (first["path"], claim["path"])}
        self.observe(b"Replacement bytes", "2026-10-27T11:00:00Z")
        report = verify_claim(self.root, claim["claim_id"], today=TODAY)
        self.assertEqual(report["links"][0]["status"], "matched")
        self.assertEqual(report["links"][0]["source_state"], "changed")
        self.observe(self.raw, "2026-10-27T12:00:00Z")
        returned = verify_claim(self.root, claim["claim_id"], today=TODAY)
        self.assertEqual(returned["links"][0]["source_state"], "current")
        for name, raw in original.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_legacy_capture_unknown_ties_ambiguous_and_future_utc_day(self) -> None:
        first = self.capture()
        pinned_bytes = (self.root / first["path"]).read_bytes()
        claim = self.apply()
        observation = self.root / "00-context/observations/E-001.json"
        observation.unlink(missing_ok=True)
        report = verify_claim(self.root, claim["claim_id"], today=TODAY)
        self.assertEqual(report["links"][0]["status"], "matched")
        self.assertEqual(report["links"][0]["source_state"], "unknown")
        self.observe(self.raw, STAMP)
        self.observe(b"Other bytes at the same time", STAMP)
        self.assertEqual(verify_claim(self.root, claim["claim_id"], today=TODAY)["links"][0]["source_state"], "ambiguous")
        self.observe(b"Future observation", "2026-10-28T00:00:00Z")
        self.assertEqual(verify_claim(self.root, claim["claim_id"], today=TODAY)["links"][0]["source_state"], "future")
        self.assertEqual((self.root / first["path"]).read_bytes(), pinned_bytes)

    def test_observation_metadata_is_not_read_for_denied_claims(self) -> None:
        self.capture()
        claim = self.apply()
        with patch("whykit.artifact_store.read_source_observation", side_effect=AssertionError("denied observation read")):
            report = verify_claim(self.root, claim["claim_id"], today=TODAY, allowed_sensitivities={"public"})
        self.assertFalse(report["exists"])
        self.assertEqual(report["links"], [])

    def test_denied_claims_never_load_hash_or_scan_hidden_records(self) -> None:
        directory = self.root / "00-context/claims"
        directory.mkdir(parents=True)
        path = directory / "c-001.md"
        forms = ("missing", "corrupt", "symlink") + (("fifo",) if hasattr(os, "mkfifo") else ())
        for form in forms:
            with self.subTest(form=form):
                path.unlink(missing_ok=True)
                if form == "corrupt":
                    path.write_bytes(b"invalid hidden claim")
                elif form == "symlink":
                    path.symlink_to(self.source)
                elif form == "fifo":
                    os.mkfifo(path)
                with ExitStack() as stack:
                    for helper in ("_read_claim", "_claims", "_hash", "_read_regular", "_parse_record"):
                        stack.enter_context(patch("whykit.claim_ledger." + helper,
                                                  side_effect=AssertionError("hidden claim I/O or hash")))
                    for allowed in ({"public"}, {"internal"}, set()):
                        self.assertIsNone(read_claim(self.root, "C-001", allowed_sensitivities=allowed))
                        self.assertEqual(list_claims(self.root, allowed_sensitivities=allowed), [])
                        report = verify_claim(self.root, "C-001", today=TODAY, allowed_sensitivities=allowed)
                        self.assertFalse(report["exists"])
                        self.assertEqual(report["links"], [])
                    with self.assertRaisesRegex(ValueError, "canonical C-NNN"):
                        read_claim(self.root, "../C-001", allowed_sensitivities={"public"})
                    with self.assertRaisesRegex(ValueError, "SHA-256"):
                        read_claim(self.root, "C-001", expected_sha256="invalid", allowed_sensitivities={"public"})
        path.unlink(missing_ok=True)

    def test_source_currentness_uses_the_declared_utc_day_not_a_trusted_instant(self) -> None:
        self.observe(self.raw, "2026-10-27T23:59:59Z")
        claim = self.apply()
        self.assertEqual(verify_claim(self.root, claim["claim_id"], today=TODAY)["links"][0]["source_state"], "current")
        previous = TODAY - dt.timedelta(days=1)
        self.assertEqual(verify_claim(self.root, claim["claim_id"], today=previous)["links"][0]["source_state"], "future")
        with self.assertRaisesRegex(ValueError, "calendar date"):
            verify_claim(self.root, claim["claim_id"], today=dt.datetime(2026, 10, 27))

    def test_verification_schema_keeps_anchor_status_and_source_state_distinct(self) -> None:
        self.capture()
        claim = self.apply()
        report = {"contract_version": 1, **verify_claim(self.root, claim["claim_id"], today=TODAY)}
        schema = json.loads((Path(__file__).parents[1] / "schemas/claim-verification.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])
        for state in ("current", "changed", "unknown", "ambiguous", "future", None):
            altered = copy.deepcopy(report)
            altered["links"][0]["source_state"] = state
            self.assertEqual(validate(altered, schema), [])
        for state in (True, 1, [], "matched"):
            altered = copy.deepcopy(report)
            altered["links"][0]["source_state"] = state
            self.assertNotEqual(validate(altered, schema), [])
        altered = copy.deepcopy(report)
        altered["links"][0]["status"] = "current"
        self.assertNotEqual(validate(altered, schema), [])

    def test_preview_write_read_and_verify_preserve_unicode_and_declarations(self) -> None:
        self.capture()
        before = tree_bytes(self.root)
        original = copy.deepcopy(self.link)
        plan = self.create()
        self.assertEqual(tree_bytes(self.root), before)
        self.assertFalse((self.root / "00-context/claims").exists())
        result = self.create(write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(result["claim_id"], "C-001")
        self.assertEqual(result["path"], "00-context/claims/c-001.md")
        record = read_claim(self.root, "C-001")
        self.assertEqual(record["record"]["type"], "claim")
        self.assertEqual(record["record"]["status"], "draft")
        self.assertIs(record["record"]["source_of_truth"], False)
        self.assertEqual(record["payload"]["links"], [original])
        schema = json.loads((Path(__file__).parents[1] / "schemas/claim-record.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(record["record"], schema), [])
        report = verify_claim(self.root, "C-001")
        self.assertEqual(report["links"][0]["status"], "matched")
        self.assertEqual(report["links"][0]["assessment"], original["assessment"])
        self.assertNotIn("content_base64", json.dumps(report))
        self.assertEqual(self.link, original)
        self.assertEqual(list_claims(self.root), [record])
        for path, data in before.items():
            if not path.startswith(".whykit/"):
                self.assertEqual((self.root / path).read_bytes(), data)
        _, findings = lint(self.root, [result["path"]], today=TODAY, orphans=False, secrets=False)
        self.assertEqual([f.code for f in findings if f.level == "error"], [])

    def test_new_capture_and_claim_leave_approved_legacy_reasoning_and_receipt_unchanged(self) -> None:
        legacy = self.root.parent / "legacy-vault"
        fresh_vault(legacy, today=TODAY)
        register(legacy)
        _, decision = approved_decision(legacy, "Preserve existing reasoning", today=TODAY,
                                        owner="Ada Example", source_ids=["E-001"])
        before = tree_bytes(legacy)
        plan = capture_artifact(legacy, "E-001", self.source, recorded_at=STAMP)
        capture_artifact(legacy, "E-001", self.source, recorded_at=STAMP,
                         write=True, expected_sha256=plan["expected_sha256"])
        claim = create_claim(legacy, "A separate new draft", [self.link], title="New claim", owner="Ada Example", today=TODAY)
        create_claim(legacy, "A separate new draft", [self.link], title="New claim", owner="Ada Example", today=TODAY,
                     write=True, expected_sha256=claim["expected_sha256"])
        self.assertIn("status: approved", decision.read_text(encoding="utf-8"))
        for path, raw in before.items():
            if not path.startswith(".whykit/"):
                self.assertEqual((legacy / path).read_bytes(), raw, path)

    def test_missing_capture_and_changed_quote_are_explicit_not_truth_verdicts(self) -> None:
        first = self.apply()
        self.assertEqual(verify_claim(self.root, first["claim_id"])["links"][0]["status"], "source_unavailable")
        self.capture()
        self.link["location"]["quote"] = "żółx"
        second = self.apply()
        self.assertEqual(verify_claim(self.root, second["claim_id"])["links"][0]["status"], "location_mismatch")
        empty = create_claim(self.root, "Unlinked hypothesis", [], title="No source", owner="Ada Example", today=TODAY)
        create_claim(self.root, "Unlinked hypothesis", [], title="No source", owner="Ada Example", today=TODAY,
                     write=True, expected_sha256=empty["expected_sha256"])
        self.assertTrue(verify_claim(self.root, empty["claim_id"])["unlinked"])

    def test_content_bound_allocation_refuses_stale_concurrent_claim(self) -> None:
        plan = self.create()
        def write():
            try:
                return self.create(write=True, expected_sha256=plan["expected_sha256"])["claim_id"]
            except ValueError:
                return "stale"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: write(), range(2)))
        self.assertEqual(sorted(results), ["C-001", "stale"])
        self.assertEqual(self.create()["claim_id"], "C-002")
        changed = self.create()
        with self.assertRaisesRegex(ValueError, "stale"):
            create_claim(self.root, "Different text", [self.link], title="Example claim", owner="Ada Example", today=TODAY,
                         write=True, expected_sha256=changed["expected_sha256"])

    def test_sensitivity_inherits_evidence_and_capture_and_can_only_be_filtered(self) -> None:
        register(self.root, "restricted")
        self.capture()
        register(self.root, "public")
        result = self.apply(sensitivity="public")
        self.assertEqual(result["record"]["sensitivity"], "restricted")
        self.assertIsNone(read_claim(self.root, result["claim_id"], allowed_sensitivities={"public"}))
        self.assertEqual(list_claims(self.root, allowed_sensitivities={"public"}), [])
        self.assertFalse(verify_claim(self.root, result["claim_id"], allowed_sensitivities={"public"})["exists"])

    def test_claim_quote_canary_never_reaches_public_export_or_lower_mcp_ceilings(self) -> None:
        canary = "PRIVATE_CLAIM_QUOTE_CANARY"
        self.source.write_bytes(canary.encode())
        self.link["source_version"] = "sha256:" + hashlib.sha256(canary.encode()).hexdigest()
        self.link["location"] = {"start_byte": 0, "end_byte": len(canary), "quote": canary}
        register(self.root, "restricted")
        artifact = self.capture()
        result = self.apply(sensitivity="public")
        self.assertEqual(result["record"]["sensitivity"], "restricted")
        (self.root / "Home.md").write_text("# Synthetic vault\n", encoding="utf-8")
        for downgrade in ("none", "label", "type-and-id"):
            if downgrade != "none":
                register(self.root, "public")
                path = self.root / result["path"]
                path.write_text(path.read_text(encoding="utf-8").replace('sensitivity: "restricted"', 'sensitivity: "public"'), encoding="utf-8")
                if downgrade == "type-and-id":
                    path.write_text(path.read_text(encoding="utf-8").replace('type: "claim"', 'type: "reference"').replace('claim_id: "C-001"\n', ""), encoding="utf-8")
            self.assertNotIn(canary, json.dumps(build_explorer_index(self.root)))
            envelope = json.loads((self.root / artifact["path"]).read_text(encoding="utf-8"))
            self.assertNotIn(envelope["content_base64"], json.dumps(build_explorer_index(self.root, private=True)))
            for ceiling in ("public", "internal", "confidential"):
                with self.subTest(downgrade=downgrade, ceiling=ceiling):
                    tools = VaultTools(self.root, max_sensitivity=ceiling)
                    self.assertEqual(tools.query(text=canary)["results"], [])
                    context = tools.context(result["path"])
                    self.assertFalse(context["exists"])
                    self.assertNotIn(canary, json.dumps(context))

    def test_pure_visibility_uses_only_supplied_text_and_visible_evidence_ids(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        note = load_note(path)
        with patch("whykit.claim_ledger._evidence_info", side_effect=AssertionError("unexpected vault I/O")), patch(
            "whykit.claim_ledger._load_artifact", side_effect=AssertionError("unexpected artifact I/O")
        ):
            self.assertEqual(claim_evidence_ids(note.text), {"E-001"})
            self.assertTrue(claim_visibility(note, {"E-001"}))
            self.assertTrue(claim_visibility(note, {"E-001"}, root=self.root))
            self.assertFalse(claim_visibility(note, set()))
            altered = load_note(path, text=note.text.replace('sensitivity: "restricted"', 'sensitivity: "public"'))
            self.assertFalse(claim_visibility(altered, {"E-001"}))
            moved = load_note(path.parent.parent / path.name, text=note.text)
            self.assertFalse(claim_visibility(moved, {"E-001"}))
            nested = load_note(self.root / "notes/nested/00-context/claims/c-001.md", text=note.text)
            self.assertFalse(claim_visibility(nested, {"E-001"}, root=self.root))

    def test_strict_envelope_ids_duplicate_keys_and_extra_fences(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        original = path.read_text(encoding="utf-8")
        for text in (original.replace('"claim_id": "C-001"', '"claim_id": "C-002"'),
                     original.replace('"format":', '"format": "wrong", "format":', 1),
                     original + "\n```json\n{}\n```\n",
                     original.replace("claim_id: \"C-001\"", "claim_id: \"C-002\"")):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_claim(self.root, "C-001")
        path.write_text(original, encoding="utf-8")
        for cid in ("C-001\n", "C-００１", "C-0001", "../C-001"):
            with self.subTest(cid=cid), self.assertRaises(ValueError):
                read_claim(self.root, cid)

    def test_temporal_dates_are_optional_real_and_ordered(self) -> None:
        self.assertNotIn("valid_from", self.create()["record"])
        result = self.apply(valid_from="2026-01-01", valid_until="2027-01-01")
        self.assertEqual(read_claim(self.root, result["claim_id"])["record"]["valid_until"], "2027-01-01")
        for options in ({"valid_from": "2026-02-30"}, {"valid_until": "20260101"},
                        {"valid_from": "2026-01-01", "valid_until": "2026-01-01"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.create(**options)

    def test_unknown_evidence_tampered_artifact_and_unsafe_claim_dir_refused(self) -> None:
        self.link["evidence_id"] = "E-999"
        with self.assertRaises(ValueError):
            self.create()
        self.link["evidence_id"] = "E-001"
        artifact = self.capture()
        claim = self.apply()
        (self.root / artifact["path"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            verify_claim(self.root, claim["claim_id"])
        if os.name != "nt":
            path = self.root / claim["path"]
            path.unlink()
            path.symlink_to(self.source)
            with self.assertRaises((ValueError, OSError)):
                read_claim(self.root, claim["claim_id"])

    def test_record_link_and_aggregate_bounds(self) -> None:
        with patch("whykit.claim_ledger.MAX_CLAIM_BYTES", 50), self.assertRaises(ValueError):
            self.create()
        with patch("whykit.claim_ledger.MAX_CLAIM_LINKS", 1), self.assertRaises(ValueError):
            create_claim(self.root, "Statement", [self.link, self.link], title="Two links", owner="Ada Example")
        self.apply()
        with patch("whykit.claim_ledger.MAX_LEDGER_BYTES", 20), self.assertRaises(ValueError):
            list_claims(self.root)
        with patch("whykit.claim_ledger.MAX_CLAIM_BYTES", 10), self.assertRaises(ValueError):
            read_claim(self.root, "C-001")
        path = self.root / "00-context/claims/c-001.md"
        path.write_text("malformed hidden record", encoding="utf-8")
        self.assertIsNone(read_claim(self.root, "C-001", allowed_sensitivities={"public"}))
        self.assertEqual(list_claims(self.root, allowed_sensitivities={"public"}), [])
        with self.assertRaises(ValueError):
            create_claim(self.root, "Statement", [], title="First\u2028Second", owner="Ada Example")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_claim_fifo_read_is_refused_without_hanging(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        path.unlink()
        os.mkfifo(path)
        child = subprocess.run([sys.executable, "-c", """
from pathlib import Path
import sys
from whykit.claim_ledger import read_claim
try:
    read_claim(Path(sys.argv[1]), 'C-001')
except (OSError, ValueError):
    raise SystemExit(0)
raise SystemExit('FIFO accepted')
""", str(self.root)], capture_output=True, text=True, timeout=3)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
