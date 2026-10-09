"""Withholding reasons belong to a private report, never the public artifact."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _vaults import fresh_vault
from whykit.explorer_index import build_explorer_index, build_publication_preview


class PublicationPreviewTests(unittest.TestCase):
    def test_preview_is_private_source_bound_and_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            private = root / "notes/private.md"
            private.write_text('---\ntitle: "Private canary"\nsensitivity: restricted\n---\nSecret canary\n', encoding="utf-8")
            public = root / "notes/public.md"
            public.write_text('---\ntitle: "Public hub"\nsensitivity: public\n---\n[[notes/private]]\n', encoding="utf-8")
            preview = build_publication_preview(root)
            reasons = {item["path"]: item["reasons"] for item in preview["withheld"]}
            from _jsonschema import validate
            schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/publication-preview.schema.json").read_text(encoding="utf-8"))
            self.assertEqual(validate(preview, schema), [])
            invalid = {**preview, "withheld": [{"path": "notes/private.md", "reasons": []}]}
            self.assertTrue(validate(invalid, schema))
            self.assertTrue(preview["private"])
            self.assertIn("non_public_sensitivity", reasons["notes/private.md"])
            self.assertIn("withheld_dependency:notes/private", reasons["notes/public.md"])
            public_json = json.dumps(build_explorer_index(root))
            for hidden in ("Private canary", "Secret canary", "notes/private", "withheld_dependency", "source_manifest_sha256"):
                self.assertNotIn(hidden, public_json)
            before = preview["source_manifest_sha256"]
            private.write_text(private.read_text(encoding="utf-8") + "Updated\n", encoding="utf-8")
            self.assertNotEqual(before, build_publication_preview(root)["source_manifest_sha256"])
