"""Context freshness binds permitted claim sources without reading denied inputs."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from _vaults import ROOT
from whykit import artifact_store
from whykit.compiled_context import compile_context, serialize_compiled, verify_compiled
from whykit.vault_index import VaultIndex

DAY = dt.date(2026, 9, 17)
ALL = {"public", "internal", "confidential", "restricted"}


class ContextSourceObservationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "vault"
        shutil.copytree(ROOT / "examples/provenance", self.root,
                        ignore=shutil.ignore_patterns(".git", ".whykit"))

    def compile(self, *, allowed=ALL, targets=None, vault=None):
        return compile_context(self.root, targets=targets or ["C-001"], today=DAY,
                               allowed_sensitivities=allowed, vault=vault)

    def capture(self, raw: bytes, stamp="2026-09-17T13:00:00Z"):
        source = self.base / "source.txt"
        source.write_bytes(raw)
        preview = artifact_store.capture_artifact(self.root, "E-004", source, recorded_at=stamp)
        return artifact_store.capture_artifact(self.root, "E-004", source, recorded_at=stamp,
                                               write=True, expected_sha256=preview["expected_sha256"])

    def test_new_source_version_invalidates_prior_claim_context(self):
        report = self.compile()
        self.assertEqual(report["status"], "compiled")
        public = self.compile(allowed={"public"}, targets=["D-003"])
        self.capture(b"A new observation with different bytes.\n")
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities=ALL)["status"], "stale")
        updated = self.compile(targets=["D-003"])
        self.assertIn("compiled.source_observation_changed", {item["code"] for item in updated["warnings"]})
        for maximum in (160, 4096):
            bounded = compile_context(self.root, targets=["D-003"], today=DAY,
                                      max_bytes=maximum, allowed_sensitivities=ALL)
            self.assertLessEqual(len(serialize_compiled(bounded).encode("utf-8")), maximum)
        self.assertEqual(public, self.compile(allowed={"public"}, targets=["D-003"]))

    def test_new_observation_of_same_bytes_still_invalidates_prior_context(self):
        import base64
        import json

        envelope = next((self.root / "00-context/artifacts/E-004").glob("*.json"))
        original = envelope.read_bytes()
        raw = base64.b64decode(json.loads(original)["content_base64"])
        report = self.compile()
        self.capture(raw)
        self.assertEqual(envelope.read_bytes(), original)
        self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities=ALL)["status"], "stale")

    def test_changed_capture_cannot_leave_prior_context_current(self):
        report = self.compile()
        envelope = next((self.root / "00-context/artifacts/E-004").glob("*.json"))
        envelope.write_bytes(envelope.read_bytes() + b" ")
        self.assertNotEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities=ALL)["status"], "current")

    def test_denied_sources_are_not_read_or_hashed(self):
        report = self.compile(allowed={"public"}, targets=["D-003"])
        original = artifact_store._read_regular

        def read(path, **kwargs):
            self.assertFalse({"artifacts", "observations"} & set(Path(path).parts), path)
            return original(path, **kwargs)

        with patch("whykit.artifact_store._read_regular", side_effect=read):
            self.assertEqual(report, self.compile(allowed={"public"}, targets=["D-003"]))
        self.capture(b"PRIVATE_CHANGED_SOURCE\n")
        with patch("whykit.artifact_store._read_regular", side_effect=read):
            self.assertEqual(report, self.compile(allowed={"public"}, targets=["D-003"]))

    def test_ungranted_claim_dependencies_are_not_loaded(self):
        full = VaultIndex.load(self.root)
        grant = full.subset(lambda note: note.front.get("decision_id") == "D-003")
        original = artifact_store._read_regular

        def read(path, **kwargs):
            self.assertFalse({"artifacts", "observations"} & set(Path(path).parts), path)
            return original(path, **kwargs)

        with patch("whykit.artifact_store._read_regular", side_effect=read):
            report = self.compile(targets=["D-003"], vault=grant)
            self.assertEqual(report["status"], "compiled")
            self.assertEqual(verify_compiled(self.root, report, today=DAY, allowed_sensitivities=ALL,
                                           vault=grant)["status"], "current")
