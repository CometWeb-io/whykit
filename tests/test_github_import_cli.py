"""User-facing saved-export routes, schema and error boundaries."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from pathlib import Path
import tempfile
import unittest

from _jsonschema import validate
from _vaults import ROOT, fresh_vault
from test_workflow_cli import call
from whykit.contract import OUTPUT_SCHEMAS
from whykit.scaffold import create_evidence


class GitHubImportCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "vault with spaces Łódź"
        fresh_vault(self.root)

    def args(self, kind):
        source = ROOT / f"examples/github-import/{kind}.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        evidence = create_evidence(self.root, source="Synthetic saved export", kind="dataset",
                                   location=data["html_url"], claims="Untrusted fixture attribution",
                                   sensitivity="restricted")
        return ["import", "github", "--root", str(self.root), "--kind", kind,
                "--repository", "example-labs/fixture", "--evidence", evidence,
                "--input", str(source), "--recorded-at", "2026-09-17T12:00:00Z"]

    def conforms(self, payload):
        schema = json.loads((ROOT / "schemas" / OUTPUT_SCHEMAS["import github"]).read_text(encoding="utf-8"))
        self.assertEqual(validate(payload, schema), [])

    def test_three_profiles_preview_apply_and_immediate_retry(self):
        for kind in ("issue", "pull-request", "workflow-run"):
            with self.subTest(kind=kind):
                args = self.args(kind)
                before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
                code, preview = call(*args)
                self.assertEqual(code, 0, preview)
                self.conforms(preview)
                self.assertIs(preview["applied"], False)
                self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
                raw = Path(args[args.index("--input") + 1]).read_bytes()
                self.assertEqual(preview["source_version"], "sha256:" + hashlib.sha256(raw).hexdigest())
                self.assertEqual(preview["size_bytes"], len(raw))
                code, applied = call(*args, "--write", "--expect-hash", preview["expected_sha256"])
                self.assertEqual(code, 0, applied)
                self.conforms(applied)
                for name in ("source_identity_authenticated", "remote_state_checked", "semantics_checked", "execution_authorized"):
                    self.assertIs(applied[name], False)
                self.assertIs(applied["applied"], True)
                capture = json.loads((self.root / applied["artifact_path"]).read_text(encoding="utf-8"))
                self.assertEqual(base64.b64decode(capture["content_base64"]), raw)
                receipt = json.loads((self.root / applied["receipt_path"]).read_text(encoding="utf-8"))
                receipt_schema = json.loads((ROOT / "schemas/github-import-receipt.schema.json").read_text(encoding="utf-8"))
                self.assertEqual(validate(receipt, receipt_schema), [])
                self.assertEqual(applied["metadata"], receipt["metadata"])
                if kind == "workflow-run":
                    self.assertIs(applied["metadata"]["conclusion"], None)
                after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                         if p.is_file() and ".whykit" not in p.relative_to(self.root).parts}
                code, retried = call(*args, "--write", "--expect-hash", preview["expected_sha256"])
                self.assertEqual(code, 0, retried)
                self.assertIs(retried["already_exists"], True)
                self.assertIs(retried["observation_added"], False)
                self.assertEqual(after, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                                         if p.is_file() and ".whykit" not in p.relative_to(self.root).parts})

    def test_stale_export_and_wrong_profile_return_bounded_errors(self):
        args = self.args("issue")
        source = self.base / "issue.json"
        source.write_bytes(Path(args[args.index("--input") + 1]).read_bytes())
        args[args.index("--input") + 1] = str(source)
        code, preview = call(*args)
        self.assertEqual(code, 0, preview)
        data = json.loads(source.read_bytes())
        secret = secrets.token_hex(16)
        data["body"] = secret
        source.write_text(json.dumps(data), encoding="utf-8")
        code, stale = call(*args, "--write", "--expect-hash", preview["expected_sha256"])
        self.assertEqual(code, 2, stale)
        self.assertEqual(stale["error"]["code"], "invalid_argument")
        self.assertNotIn(secret, json.dumps(stale))
        self.assertFalse((self.root / "00-context/imports").exists())
        args[args.index("--kind") + 1] = "workflow-run"
        code, refused = call(*args)
        self.assertEqual(code, 2, refused)
        self.assertNotIn(secret, json.dumps(refused))

    def test_json_usage_and_root_placements_keep_contract(self):
        code, error = call("import", "github", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(error["error"]["code"], "usage")
        args = self.args("issue")
        code, expected = call(*args)
        self.assertEqual(code, 0, expected)
        rest = args[4:]
        for prefix in (["--root", str(self.root), "import", "github"],
                       ["import", "--root", str(self.root), "github"]):
            with self.subTest(prefix=prefix):
                code, result = call(*prefix, *rest)
                self.assertEqual(code, 0, result)
                self.assertEqual(result, expected)
