from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from _claims import claim_vault
from _vaults import fresh_vault
from whykit import cli
from whykit.config import load_config


def inventory(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".whykit" not in p.relative_to(root).parts
    }


class ClaimMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        fresh_vault(self.root, "--minimal")

    def enable(self, **kwargs):
        method = getattr(cli, "set_claims_enabled", None)
        self.assertTrue(
            callable(method), "Claim opt-in must support a config-only preview/apply"
        )
        return method(self.root, True, **kwargs)

    def test_enable_preview_apply_idempotence(self):
        before = inventory(self.root)
        plan = self.enable()
        self.assertFalse(plan["applied"])
        self.assertEqual(inventory(self.root), before)
        self.assertTrue(plan["manual_claim_authoring_required"])
        self.assertEqual(
            base64.b64decode(plan["original_config_base64"]), before["whykit.toml"]
        )
        self.enable(write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(load_config(self.root)[0]["claims"], {"format_version": 1})
        current = inventory(self.root)
        self.assertEqual(
            {p for p in current if current[p] != before[p]}, {"whykit.toml"}
        )
        plan = self.enable()
        self.enable(write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(inventory(self.root), current)

    def test_cas_rejects_config_and_inventory_change(self):
        for filename in ("whykit.toml", "notes/added.md"):
            plan = self.enable()
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            original = path.read_bytes() if path.exists() else None
            path.write_bytes((original or b"") + b"\n# Changed after preview\n")
            before = inventory(self.root)
            with self.assertRaises(ValueError):
                self.enable(write=True, expected_sha256=plan["expected_sha256"])
            self.assertEqual(inventory(self.root), before)
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_disable_blocks_existing_claims_without_erasing_data(self):
        root = self.root.parent / "with-claims"
        claim_vault(root)
        method = getattr(cli, "set_claims_enabled", None)
        self.assertTrue(callable(method), "Claim opt-in needs a safe disable operation")
        before = inventory(root)
        with self.assertRaises(ValueError):
            method(root, False)
        self.assertEqual(inventory(root), before)

    def test_apply_requires_reviewed_hash(self):
        with self.assertRaises(ValueError):
            self.enable(write=True)

    def test_inventory_counts_sources_and_decisions(self):
        from whykit.scaffold import create_evidence, create_decision

        create_evidence(
            self.root,
            source="Survey",
            location="https://example.com/survey",
            kind="survey",
            claims="Observed behavior",
        )
        create_decision(self.root, "Evaluate behavior")
        result = self.enable()
        self.assertEqual(result["unchanged_evidence"], 1)
        self.assertEqual(result["unchanged_decisions"], 1)

    def test_cli_preview_then_apply_and_create_claim(self):
        import contextlib
        import io
        import json

        def call(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(list(args))
            self.assertEqual(code, 0, out.getvalue())
            return json.loads(out.getvalue())

        preview = call("claims", "enable", "--root", str(self.root), "--json")
        self.assertFalse(preview["applied"])
        call(
            "claims",
            "enable",
            "--root",
            str(self.root),
            "--write",
            "--expect-hash",
            preview["expected_sha256"],
            "--json",
        )
        claim = call(
            "new",
            "--root",
            str(self.root),
            "claim",
            "Readable records",
            "--statement",
            "Records are readable.",
            "--scope",
            "Desktop v2.1",
            "--valid-from",
            "2026-10-01",
            "--today",
            "2026-10-09",
            "--json",
        )
        self.assertEqual(claim["id"], "C-001")
        from whykit.contract import output_schema_name

        self.assertEqual(
            output_schema_name("new claim"), "claim-create-result.schema.json"
        )
