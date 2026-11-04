"""Installed-command contracts for measurements and exact-byte v2 migration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from _vaults import ROOT
from test_workflow_cli import call
from whykit.contract import OUTPUT_SCHEMAS
from whykit import knowledge_v2


class OutcomeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "vault"
        shutil.copytree(ROOT / "examples/provenance", self.root,
                        ignore=shutil.ignore_patterns(".git", ".whykit"))
        self.input = self.base / "outcome.json"
        claim = self.root / "00-context/claims/c-001.md"
        self.data = {"decision_id": "D-003", "metric": {"name": "Fixture count", "unit": "items",
                     "definition": "A declared synthetic value; source semantic agreement is unchecked."},
                     "baseline": None, "target": {"value": "3.000", "by": None},
                     "observed": {"value": "2.500", "as_of": "2026-09-17", "claim": {
                         "claim_id": "C-001", "sha256": hashlib.sha256(claim.read_bytes()).hexdigest()}},
                     "implementation_refs": ["https://example.com/pull/1"]}
        self.input.write_text(json.dumps(self.data), encoding="utf-8")

    def conforms(self, command, payload):
        schema = json.loads((ROOT / "schemas" / OUTPUT_SCHEMAS[command]).read_text(encoding="utf-8"))
        self.assertEqual(validate(payload, schema), [])

    def create(self):
        args = ["outcome", "create", "--root", str(self.root), "--input", str(self.input),
                "--owner", "Ada Example", "--today", "2026-09-17"]
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, preview = call(*args)
        self.assertEqual(code, 0, preview)
        self.assertIs(preview["applied"], False)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.conforms("outcome create", preview)
        code, result = call(*args, "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 0, result)
        self.assertIs(result["applied"], True)
        self.conforms("outcome create", result)
        return result

    def test_create_read_verify_preserves_decimal_and_uncertainty(self):
        result = self.create()
        self.assertEqual(result["payload"]["observed"]["value"], "2.500")
        for action in ("list", "show", "verify"):
            args = ["outcome", action]
            if action != "list":
                args.append("O-001")
            args += ["--root", str(self.root), "--max-sensitivity", "restricted"]
            if action == "verify":
                args += ["--today", "2026-09-17"]
            code, report = call(*args)
            self.assertEqual(code, 0, report)
            self.conforms("outcome " + action, report)
            if action == "verify":
                self.assertIs(report["observed_source_verified"], True)
                for field in ("values_checked", "causality_checked", "execution_authorized"):
                    self.assertIs(report[field], False)
        code, hidden = call("outcome", "list", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(hidden["outcomes"], [])

    def test_target_or_unlinked_zero_does_not_verify_an_observed_source(self):
        for observed in (None, {"value": "0", "as_of": "2026-09-17", "claim": None}):
            self.data["observed"] = observed
            self.input.write_text(json.dumps(self.data), encoding="utf-8")
            created = self.create()
            code, result = call("outcome", "verify", created["outcome_id"], "--root", str(self.root),
                                "--max-sensitivity", "restricted", "--today", "2026-09-17")
            self.assertEqual(code, 1, result)
            self.assertIs(result["observed_source_verified"], False)

    def test_knowledge_view_is_scoped_and_versioned(self):
        self.create()
        code, public = call("knowledge", "view", "--root", str(self.root))
        self.assertEqual(code, 0, public)
        self.assertNotIn('"value": "2.500"', json.dumps(public))
        code, private = call("knowledge", "view", "--root", str(self.root), "--max-sensitivity", "restricted")
        self.assertEqual(code, 0, private)
        self.assertEqual({obj["kind"] for obj in private["objects"]},
                         {"Claim", "EvidenceArtifact", "Decision", "ReviewEvent", "Outcome"})
        self.conforms("knowledge view", public)
        self.conforms("knowledge view", private)

    @unittest.skipUnless(knowledge_v2._NATIVE_IO_AVAILABLE, "safe migration I/O requires POSIX primitives")
    def test_real_bundle_restore_preserves_raw_bytes_and_legacy_consumers(self):
        self.create()
        extra = self.root / "attachments/binary.dat"
        extra.parent.mkdir()
        extra.write_bytes(b"\x00\xff\xef\xbb\xbfRaw\r\nno-final-newline")
        source = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                  if p.is_file() and ".whykit" not in p.parts}
        bundle = self.base / "private-bundle.json"
        export = ["knowledge", "bundle", "--root", str(self.root), "--output", str(bundle)]
        code, refusal = call(*export)
        self.assertEqual(code, 2, refusal)
        self.assertFalse(bundle.exists())
        code, preview = call(*export, "--private")
        self.assertEqual(code, 0, preview)
        self.assertFalse(bundle.exists())
        self.conforms("knowledge bundle", preview)
        code, exported = call(*export, "--private", "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 0, exported)
        self.conforms("knowledge bundle", exported)
        destination = self.base / "restored"
        restore = ["knowledge", "restore", str(bundle), "--into", str(destination)]
        code, preview = call(*restore)
        self.assertEqual(code, 0, preview)
        self.assertFalse(destination.exists())
        self.assertIs(preview["exact_bytes_preserved"], False)
        self.conforms("knowledge restore", preview)
        code, restored = call(*restore, "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 0, restored)
        self.assertIs(restored["exact_bytes_preserved"], True)
        self.assertIs(restored["git_history_transferred"], False)
        self.conforms("knowledge restore", restored)
        for name, raw in source.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
            self.assertEqual((destination / name).read_bytes(), raw)
        code, report = call("outcome", "verify", "O-001", "--root", str(destination),
                            "--max-sensitivity", "restricted", "--today", "2026-09-17")
        self.assertEqual(code, 0, report)

    def test_unsupported_migration_platform_is_an_actionable_refusal(self):
        with patch.object(knowledge_v2, "_NATIVE_IO_AVAILABLE", False):
            code, report = call("knowledge", "bundle", "--root", str(self.root), "--private",
                                "--output", str(self.base / "bundle.json"))
        self.assertEqual(code, 2)
        self.assertEqual(report["error"]["code"], "operation_rejected")
        self.assertIn("POSIX", report["error"]["message"])
        self.assertFalse((self.base / "bundle.json").exists())
