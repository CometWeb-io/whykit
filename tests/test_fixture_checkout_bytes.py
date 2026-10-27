"""Bound fixture bytes must survive Git's Windows-style text checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from whykit import artifact_store

ROOT = Path(__file__).resolve().parents[1]
BOUND_TREES = ("examples/approval", "examples/provenance", "examples/outcomes",
               "tests/fixtures/hybrid-retrieval", "tests/fixtures/impact")


class FixtureCheckoutBytesTests(unittest.TestCase):
    def test_generator_preserves_inventory_with_windows_default_text_newlines(self) -> None:
        generator = runpy.run_path(str(ROOT / "tests/fixtures/hybrid-retrieval/generate.py"))
        expected = json.loads((ROOT / "tests/fixtures/hybrid-retrieval/corpus-manifest.json").read_bytes())
        original_open = Path.open

        def windows_text_open(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
            if "w" in mode and "b" not in mode and newline is None:
                newline = "\r\n"
            return original_open(path, mode, buffering, encoding, errors, newline)

        with tempfile.TemporaryDirectory(prefix="whykit-fixture-generator-") as temporary:
            # Emulate only the default text newline; real explicit-byte and
            # explicit-newline runtime writes retain their normal behavior.
            with patch.object(Path, "open", windows_text_open):
                actual = generator["generate"](Path(temporary) / "vault")
            self.assertEqual(actual, expected)

    def test_autocrlf_checkout_preserves_native_ledgers_and_frozen_hashes(self) -> None:
        git = shutil.which("git")
        if git is None:
            self.skipTest("Git unavailable")
        env = {"PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull}
        if "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        with tempfile.TemporaryDirectory(prefix="whykit-fixture-checkout-") as temporary:
            repo = Path(temporary)

            def run(*args: str) -> None:
                subprocess.run([git, "-c", "core.attributesFile=" + os.devnull, "-C", str(repo), *args],
                               env=env, check=True, capture_output=True, timeout=20)

            run("init", "-q")
            run("config", "core.autocrlf", "false")
            originals = {path.relative_to(ROOT).as_posix(): path.read_bytes()
                         for tree in BOUND_TREES for path in (ROOT / tree).rglob("*")
                         if path.is_file() and "__pycache__" not in path.parts}
            originals[".gitattributes"] = (ROOT / ".gitattributes").read_bytes()
            for relative, raw in originals.items():
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            # No commit, remote, global configuration or existing checkout is modified.
            run("add", "--all")
            for relative in originals:
                (repo / relative).unlink()
            run("-c", "core.autocrlf=true", "checkout-index", "--all", "--force")

            for relative, raw in originals.items():
                if relative != ".gitattributes":
                    with self.subTest(preserved=relative):
                        self.assertEqual((repo / relative).read_bytes(), raw)
            for relative in originals:
                if "/00-context/observations/" in relative:
                    with self.subTest(canonical_ledger=relative):
                        vault = repo / relative.split("/00-context/")[0]
                        try:
                            ledger, _, _ = artifact_store._load_observations(vault, Path(relative).stem, "public")
                        except ValueError as exc:
                            self.fail(str(exc))
                        self.assertEqual(ledger["format"], "whykit.source-observations/v1")

            hybrid = repo / "tests/fixtures/hybrid-retrieval"
            manifest = json.loads((hybrid / "corpus-manifest.json").read_bytes())
            for relative, binding in manifest["files"].items():
                with self.subTest(frozen_inventory=relative):
                    raw = (hybrid / "vault" / relative).read_bytes()
                    self.assertEqual(len(raw), binding["bytes"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), binding["sha256"])
            oracle = repo / "tests/fixtures/impact/oracle-v1.json"
            self.assertEqual(hashlib.sha256(oracle.read_bytes()).hexdigest(),
                             "3de1166a328aa76c8812256802f58a90b5f3da37a8098717e7fa2199de222a53")


if __name__ == "__main__":
    unittest.main()
