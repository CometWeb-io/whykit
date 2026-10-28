from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from _jsonschema import validate
from whykit.claim_provenance import validate_claim, verify_anchor
from whykit.contract import CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]
SOURCE = b"\xef\xbb\xbfFirst line.\r\nCache works.\r\n"


def claim() -> dict:
    start = SOURCE.index(b"Cache")
    return {
        "format": "whykit.claim-provenance/v1",
        "claim_id": "C-001",
        "statement": "The captured source says the cache works.",
        "links": [{
            "evidence_id": "E-001",
            "source_version": "sha256:" + hashlib.sha256(SOURCE).hexdigest(),
            "location": {"start_byte": start, "end_byte": start + 12, "quote": "Cache works."},
            "assessment": {"kind": "model", "by": "example-model/v1", "relation": "supports"},
        }],
    }


class ClaimProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads((ROOT / "schemas/claim-provenance.schema.json").read_text(encoding="utf-8"))

    def test_valid_payload_and_explicit_no_evidence_are_distinct(self) -> None:
        value = claim()
        original = copy.deepcopy(value)
        self.assertIsNone(validate_claim(value))
        self.assertEqual(validate(value, self.schema), [])
        self.assertEqual(verify_anchor(value["links"][0], SOURCE), "matched")
        self.assertEqual(value, original)
        value["links"] = []
        self.assertIsNone(validate_claim(value))
        self.assertEqual(validate(value, self.schema), [])
        del value["links"]
        with self.assertRaises(ValueError):
            validate_claim(value)
        self.assertTrue(validate(value, self.schema))

    def test_structural_schema_and_runtime_reject_bad_fields(self) -> None:
        cases = [([], "format", "v2"), ([], "claim_id", "C-001\n"),
                 ([], "claim_id", "E-001"), ([], "statement", " \n"),
                 ([], "links", None), ([], "approved", True),
                 (["links", 0], "evidence_id", "E-001\n"),
                 (["links", 0], "evidence_id", "../source"),
                 (["links", 0], "source_version", "sha256:" + "A" * 64),
                 (["links", 0], "source_version", "sha256:" + "a" * 64 + "\n"),
                 (["links", 0], "source_version", "main"),
                 (["links", 0], "location", "lines:1-2"),
                 (["links", 0, "location"], "start_byte", True),
                 (["links", 0, "location"], "start_byte", -1),
                 (["links", 0, "location"], "end_byte", 1.5),
                 (["links", 0, "location"], "quote", ""),
                 (["links", 0, "location"], "line", 1),
                 (["links", 0, "assessment"], "kind", "verified"),
                 (["links", 0, "assessment"], "by", "  "),
                 (["links", 0, "assessment"], "relation", "approved"),
                 (["links", 0, "assessment"], "authenticated", True)]
        for path, key, replacement in cases:
            with self.subTest(path=path, key=key, value=replacement):
                value = claim()
                target = value
                for part in path:
                    target = target[part]
                target[key] = replacement
                with self.assertRaises(ValueError):
                    validate_claim(value)
                self.assertTrue(validate(value, self.schema))
        for value in (None, [], "claim", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_claim(value)

    def test_required_link_fields_cannot_be_omitted(self) -> None:
        for section in ([], ["location"], ["assessment"]):
            link = claim()["links"][0]
            target = link if not section else link[section[0]]
            for key in tuple(target):
                bad = copy.deepcopy(link)
                del (bad if not section else bad[section[0]])[key]
                with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                    verify_anchor(bad, SOURCE)

    def test_order_length_and_utf8_are_runtime_constraints(self) -> None:
        for change in ({"end_byte": 0}, {"start_byte": 20, "end_byte": 19},
                       {"end_byte": 200}, {"quote": "Different length"}, {"quote": "\ud800"}):
            value = claim()
            value["links"][0]["location"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_claim(value)

    def test_raw_version_precedes_fragment_and_never_normalizes(self) -> None:
        link = claim()["links"][0]
        self.assertEqual(verify_anchor(link, None), "source_unavailable")
        for source in (b"", SOURCE.replace(b"\r\n", b"\n"), SOURCE[3:], SOURCE + b" ", SOURCE.replace(b"First", b"Other")):
            with self.subTest(source=source):
                self.assertEqual(verify_anchor(link, source), "version_mismatch")
        link["location"]["quote"] = "Cache fails."
        self.assertEqual(verify_anchor(link, SOURCE), "location_mismatch")
        link["location"].update(start_byte=100, end_byte=112)
        self.assertEqual(verify_anchor(link, SOURCE), "location_mismatch")
        for source in ("Cache works.", bytearray(SOURCE), 1):
            with self.subTest(source=source), self.assertRaises(ValueError):
                verify_anchor(link, source)

    def test_byte_offsets_include_multibyte_text(self) -> None:
        source = "Początek: żółw\r\n".encode()
        value = claim()
        link = value["links"][0]
        link["source_version"] = "sha256:" + hashlib.sha256(source).hexdigest()
        start = source.index("ż".encode())
        link["location"] = {"start_byte": start, "end_byte": start + len("żółw".encode()), "quote": "żółw"}
        validate_claim(value)
        self.assertEqual(verify_anchor(link, source), "matched")
        link["location"]["end_byte"] = start + len("żółw")
        with self.assertRaises(ValueError):
            validate_claim(value)

    def test_model_and_human_assessments_are_preserved_without_a_verdict(self) -> None:
        value = claim()
        human = copy.deepcopy(value["links"][0])
        human["assessment"] = {"kind": "human", "by": "Ada Example", "relation": "contradicts"}
        value["links"].append(human)
        original = copy.deepcopy(value)
        validate_claim(value)
        self.assertEqual(validate(value, self.schema), [])
        self.assertEqual([verify_anchor(link, SOURCE) for link in value["links"]], ["matched", "matched"])
        self.assertEqual(value, original)

    def test_optional_payload_does_not_change_legacy_provenance_or_report_version(self) -> None:
        schema = json.loads((ROOT / "schemas/provenance.schema.json").read_text(encoding="utf-8"))
        for provenance in ({}, {"producer": "example/v1", "human_reviewed": False},
                           {"producer": "example/v1", "claim_payload": claim()}):
            with self.subTest(provenance=provenance):
                self.assertEqual(validate(provenance, schema), [])
        self.assertEqual(CONTRACT_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
