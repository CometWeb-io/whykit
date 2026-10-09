from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from _claims import claim_vault, read_view
from whykit import claims
from _jsonschema import validate


class ClaimReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.path = claim_vault(self.root)

    def api(self, name):
        method = getattr(claims, name, None)
        self.assertTrue(callable(method), f"Claim receipts require {name}")
        return method

    def encoded(self):
        return self.api("encode_claim_receipt")(
            read_view(self.root),
            "C-001",
            previous_review=None,
            next_review="2027-01-07",
        )

    def test_receipt_round_trip_and_determinism(self):
        value = self.encoded()
        self.assertTrue(value.startswith("claim-receipt/v1:"))
        self.assertEqual(value, self.encoded())
        receipt = self.api("decode_claim_receipt")(value)
        self.assertEqual(receipt["claim_id"], "C-001")
        self.assertEqual(receipt["next_review"], "2027-01-07")
        self.assertEqual(
            {r["evidence_id"] for r in receipt["relations"]}, {"E-001", "E-002"}
        )
        self.assertEqual(
            {r["relation"] for r in receipt["relations"]}, {"supports", "contradicts"}
        )

    def test_semantic_hash_ignores_only_lifecycle_envelope(self):
        digest = self.api("claim_record_hash")
        raw = "---\nclaim_id: C-001\nstatus: draft\nreview_by: 2026-12-01\nlast_verified: 2026-10-09\n---\nBody\n"
        expected = hashlib.sha256(b"---\nclaim_id: C-001\n---\nBody\n").hexdigest()
        self.assertEqual(digest(raw), expected)
        self.assertEqual(digest(raw.replace("draft", "approved")), expected)
        self.assertEqual(digest("\ufeff" + raw.replace("\n", "\r\n")), expected)
        self.assertNotEqual(digest(raw.replace("Body", "Changed body")), expected)
        with self.assertRaises(ValueError):
            digest("not front matter")

    def test_malformed_receipts_are_rejected_without_echoing_input(self):
        decode = self.api("decode_claim_receipt")
        for data in (
            "claim-receipt/v1:!",
            "claim-receipt/v2:e30",
            "claim-receipt/v1:" + "a" * (128 * 1024 + 1),
        ):
            with self.subTest(size=len(data)), self.assertRaises(ValueError) as result:
                decode(data)
            self.assertNotIn(data, str(result.exception))

    def test_receipt_shape_and_duplicate_keys_fail_closed(self):
        decode = self.api("decode_claim_receipt")
        receipt = decode(self.encoded())
        malformed = []
        for key, value in (
            ("claim_id", "D-001"),
            ("record_sha256", "invalid"),
            ("next_review", "2026-02-30"),
            ("relations", receipt["relations"] * 129),
            ("extra", "private sentinel"),
        ):
            changed = {**receipt, key: value}
            malformed.append(
                json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
            )
        malformed.append(b'{"claim_id":"C-001","claim_id":"C-002"}')
        malformed.append(b"[1,2,3]")
        malformed.append(b'{"format":NaN}')
        for raw in malformed:
            encoded = "claim-receipt/v1:" + base64.urlsafe_b64encode(
                raw
            ).decode().rstrip("=")
            with self.subTest(raw=raw[:50]), self.assertRaises(ValueError):
                decode(encoded)

    def test_unrelated_evidence_does_not_change_receipt(self):
        view = read_view(self.root)
        encode = self.api("encode_claim_receipt")
        before = encode(view, "C-001", previous_review=None, next_review="2027-01-07")
        view["evidence"]["E-999"] = {"state": "active", "source": "Unrelated example"}
        self.assertEqual(
            encode(view, "C-001", previous_review=None, next_review="2027-01-07"),
            before,
        )

    def test_claim_metadata_and_receipt_schemas(self):
        root = Path(__file__).resolve().parents[1]
        view = read_view(self.root)
        receipt = self.api("decode_claim_receipt")(self.encoded())
        for name, value in (
            ("claim-record", view["records"]["C-001"]["front"]),
            ("document-frontmatter-v2", view["records"]["C-001"]["front"]),
            ("claim-receipt", receipt),
        ):
            try:
                schema = json.loads(
                    (root / "schemas" / f"{name}.schema.json").read_text(
                        encoding="utf-8"
                    )
                )
            except FileNotFoundError:
                self.fail(f"The {name} data contract must be published for validation")
            self.assertEqual(validate(value, schema), [], name)
