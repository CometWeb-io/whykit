from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _vaults import fresh_vault
from whykit.io import stage_transaction


class RecoverCommandTests(unittest.TestCase):
    def test_recover_finishes_a_pending_write_without_creating_a_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            target = root / "notes/recovered.md"
            tx = stage_transaction(root, {target: "# Recovered\n"})
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            result = subprocess.run([sys.executable, "-m", "whykit.cli", "recover", "--root", str(root), "--json"],
                                    capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            from _jsonschema import validate
            schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/recovery-result.schema.json").read_text(encoding="utf-8"))
            self.assertEqual(validate(payload, schema), [])
            self.assertTrue(payload["recovery_completed"])
            self.assertEqual(target.read_text(encoding="utf-8"), "# Recovered\n")
            self.assertFalse(tx.exists())
