"""Top-K preserves exact totals and deterministic order across ties."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _vaults import fresh_vault
from whykit.query import query_vault


class QuerySelectionTests(unittest.TestCase):
    def test_limits_match_the_full_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            for n in range(12):
                (root / "notes" / f"probe-{n}.md").write_text(
                    '---\ntitle: "Probe"\nstatus: draft\nsensitivity: public\n---\nProbe\n', encoding="utf-8")
            full = query_vault(root, text="probe", limit=100)
            self.assertEqual(full["total"], 12)
            for limit in (0, 1, 5, 20):
                page = query_vault(root, text="probe", limit=limit)
                self.assertEqual(page["total"], 12)
                self.assertEqual(page["results"], full["results"][:limit])
