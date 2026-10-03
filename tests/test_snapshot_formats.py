"""Snapshot formats: v2 is portable across line endings and BOMs, v1 stays exact.

A baseline taken on Linux must verify on a Windows checkout with
``core.autocrlf=true`` (CRLF everywhere) and survive an editor adding a UTF-8
byte-order mark, while any edit a reader could see is still drift. Baselines
written before v2 existed keep their raw-byte meaning.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from _vaults import CLI, fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit.snapshot import (  # noqa: E402
    NORMALIZATION_V2,
    SNAPSHOT_FORMAT_V1,
    SNAPSHOT_FORMAT_V2,
    build_snapshot,
    compare_snapshot,
    normalize_content,
)

TODAY = dt.date(2026, 9, 17)
SCHEMAS = ROOT / "schemas"


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def windows_checkout(root: Path, *, bom: bool = False) -> None:
    """Rewrite every snapshotted file the way core.autocrlf=true checks it out."""
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".toml"}:
            data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class NormalizationTests(unittest.TestCase):
    def test_strips_one_leading_bom_and_turns_crlf_into_lf(self) -> None:
        self.assertEqual(normalize_content(b"\xef\xbb\xbfa\r\nb\r\n"), b"a\nb\n")
        self.assertEqual(normalize_content(b"a\nb\n"), b"a\nb\n")

    def test_keeps_everything_a_reader_could_see(self) -> None:
        self.assertEqual(normalize_content(b"a\rb"), b"a\rb")  # lone CR
        self.assertEqual(normalize_content(b"a\xef\xbb\xbfb"), b"a\xef\xbb\xbfb")  # BOM mid-file
        self.assertEqual(normalize_content(b"\xef\xbb\xbf\xef\xbb\xbfa"), b"\xef\xbb\xbfa")  # only one
        self.assertEqual(normalize_content(b"a \n"), b"a \n")  # trailing whitespace
        self.assertEqual(normalize_content(b"a\r\r\n"), b"a\r\n")  # CR before CRLF survives


class SnapshotFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.lf = self.tmp / "lf"
        fresh_vault(self.lf)

    def copy(self, name: str) -> Path:
        target = self.tmp / name
        shutil.copytree(self.lf, target)
        return target

    def test_v2_is_the_default_and_records_its_normalization(self) -> None:
        snapshot = build_snapshot(self.lf, today=TODAY)
        self.assertEqual(snapshot["format"], SNAPSHOT_FORMAT_V2)
        self.assertEqual(snapshot["normalization"], {"utf8_bom": "strip", "line_endings": "crlf-to-lf"})
        self.assertEqual(validate(snapshot, schema("snapshot.schema.json")), [])

    def test_v1_snapshot_has_no_normalization_and_validates(self) -> None:
        snapshot = build_snapshot(self.lf, today=TODAY, snapshot_format=SNAPSHOT_FORMAT_V1)
        self.assertEqual(snapshot["format"], SNAPSHOT_FORMAT_V1)
        self.assertNotIn("normalization", snapshot)
        self.assertEqual(validate(snapshot, schema("snapshot.schema.json")), [])

    def test_schema_ties_normalization_to_the_format(self) -> None:
        v2 = build_snapshot(self.lf, today=TODAY)
        without = {key: value for key, value in v2.items() if key != "normalization"}
        self.assertNotEqual(validate(without, schema("snapshot.schema.json")), [])
        v1 = build_snapshot(self.lf, today=TODAY, snapshot_format=SNAPSHOT_FORMAT_V1)
        self.assertNotEqual(validate(v1 | {"normalization": NORMALIZATION_V2}, schema("snapshot.schema.json")), [])
        other = v2 | {"normalization": {"utf8_bom": "keep", "line_endings": "crlf-to-lf"}}
        self.assertNotEqual(validate(other, schema("snapshot.schema.json")), [])

    def test_lf_only_vault_hashes_identically_in_both_formats(self) -> None:
        v1 = build_snapshot(self.lf, today=TODAY, snapshot_format=SNAPSHOT_FORMAT_V1)
        v2 = build_snapshot(self.lf, today=TODAY)
        self.assertEqual(v1["files"], v2["files"])
        self.assertEqual(v1["snapshot_id"], v2["snapshot_id"])

    def test_round_trips_match_in_both_formats(self) -> None:
        for snapshot_format in (SNAPSHOT_FORMAT_V1, SNAPSHOT_FORMAT_V2):
            with self.subTest(format=snapshot_format):
                baseline = json.loads(json.dumps(build_snapshot(self.lf, today=TODAY, snapshot_format=snapshot_format)))
                report = compare_snapshot(self.lf, baseline, today=TODAY)
                self.assertTrue(report["matches"], report)
                self.assertEqual(report["format"], snapshot_format)
                self.assertEqual(report["current_snapshot_id"], baseline["snapshot_id"])
                self.assertEqual(validate(report, schema("snapshot-verify.schema.json")), [])

    def test_v2_linux_baseline_matches_a_windows_checkout_with_bom(self) -> None:
        baseline = build_snapshot(self.lf, today=TODAY)
        windows = self.copy("windows")
        windows_checkout(windows, bom=True)
        report = compare_snapshot(windows, baseline, today=TODAY)
        self.assertTrue(report["matches"], report)
        self.assertEqual((report["changed"], report["metadata_changed"]), ([], []))
        self.assertEqual(build_snapshot(windows, today=TODAY), baseline)

    def test_v2_windows_baseline_matches_a_linux_checkout(self) -> None:
        windows = self.copy("windows")
        windows_checkout(windows)
        report = compare_snapshot(self.lf, build_snapshot(windows, today=TODAY), today=TODAY)
        self.assertTrue(report["matches"], report)

    def test_v1_baseline_keeps_its_exact_byte_semantics(self) -> None:
        baseline = build_snapshot(self.lf, today=TODAY, snapshot_format=SNAPSHOT_FORMAT_V1)
        windows = self.copy("windows")
        windows_checkout(windows)
        report = compare_snapshot(windows, baseline, today=TODAY)
        self.assertFalse(report["matches"])
        self.assertEqual(report["format"], SNAPSHOT_FORMAT_V1)
        self.assertIn("Home.md", report["changed"])
        self.assertIn("whykit.toml", report["changed"])

    def test_v2_still_detects_real_content_changes_on_a_crlf_checkout(self) -> None:
        baseline = build_snapshot(self.lf, today=TODAY)
        windows = self.copy("windows")
        windows_checkout(windows, bom=True)
        home = windows / "Home.md"
        home.write_bytes(home.read_bytes().replace(b"\r\n", b"\r\nedited\r\n", 1))
        company = windows / "00-context" / "company.md"
        company.write_bytes(company.read_bytes() + b"\r\r\n")  # a lone CR is content
        report = compare_snapshot(windows, baseline, today=TODAY)
        self.assertFalse(report["matches"])
        self.assertEqual(report["changed"], ["00-context/company.md", "Home.md"])

    def test_v2_detects_a_bom_that_is_not_leading(self) -> None:
        baseline = build_snapshot(self.lf, today=TODAY)
        home = self.lf / "Home.md"
        home.write_bytes(home.read_bytes() + b"\xef\xbb\xbf")
        self.assertEqual(compare_snapshot(self.lf, baseline, today=TODAY)["changed"], ["Home.md"])

    def test_unknown_format_or_normalization_is_refused_not_reported_as_drift(self) -> None:
        baseline = build_snapshot(self.lf, today=TODAY)
        for broken in (
            baseline | {"format": "whykit.snapshot/v9"},
            baseline | {"normalization": {"utf8_bom": "strip", "line_endings": "native"}},
            {key: value for key, value in baseline.items() if key != "normalization"},
        ):
            with self.subTest(broken={k: broken.get(k) for k in ("format", "normalization")}):
                with self.assertRaises(ValueError):
                    compare_snapshot(self.lf, broken, today=TODAY)
        with self.assertRaises(ValueError):
            build_snapshot(self.lf, today=TODAY, snapshot_format="whykit.snapshot/v9")

    def test_cli_writes_either_format_and_verifies_across_line_endings(self) -> None:
        day = TODAY.isoformat()
        for flag, expected in ((None, SNAPSHOT_FORMAT_V2), ("v2", SNAPSHOT_FORMAT_V2), ("v1", SNAPSHOT_FORMAT_V1)):
            with self.subTest(flag=flag):
                argv = ["snapshot", "--root", str(self.lf), "--today", day, "--compact"]
                code, out, _ = call(*argv, *(("--format", flag) if flag else ()))
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["format"], expected)
        code, _, _ = call("snapshot", "--root", str(self.lf), "--today", day, "--output", "base.json")
        self.assertEqual(code, 0)
        windows = self.copy("windows")
        windows_checkout(windows, bom=True)
        code, out, err = call("verify-snapshot", str(self.lf / "base.json"), "--root", str(windows), "--today", day, "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["format"], SNAPSHOT_FORMAT_V2)

    def test_cli_refuses_an_unknown_format_with_invalid_argument(self) -> None:
        (self.lf / "future.json").write_text(json.dumps({"format": "whykit.snapshot/v9"}), encoding="utf-8")
        code, out, err = call("verify-snapshot", "future.json", "--root", str(self.lf), "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "invalid_argument")
        self.assertIn("whykit.snapshot/v9", err)
        self.assertIn(SNAPSHOT_FORMAT_V2, err)


class ConfigByteOrderMarkTests(unittest.TestCase):
    def test_whykit_toml_with_a_bom_is_read_like_one_without(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            before = call("policy", "--root", str(vault), "--json")
            config = vault / "whykit.toml"
            config.write_bytes(b"\xef\xbb\xbf" + config.read_bytes().replace(b"\n", b"\r\n"))
            self.assertEqual(call("policy", "--root", str(vault), "--json"), before)

    def test_whykit_toml_that_is_not_utf8_is_invalid_config_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            (vault / "whykit.toml").write_bytes(b"format_version = 1\n# caf\xe9\n")
            code, out, _ = call("policy", "--root", str(vault), "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "invalid_config")


class EntryPointTests(unittest.TestCase):
    """Every way to start WhyKit goes through the console hardening."""

    def test_only_the_cli_and_mcp_server_are_runnable_modules(self) -> None:
        runnable = sorted(
            path.name for path in (ROOT / "src" / "whykit").glob("*.py")
            if 'if __name__ == "__main__"' in path.read_text(encoding="utf-8")
        )
        self.assertEqual(runnable, ["cli.py", "mcp_server.py"])

    def test_mcp_server_hardens_stdio_before_parsing(self) -> None:
        from unittest import mock

        from whykit import mcp_server

        with mock.patch("whykit.console.harden_stdio") as harden, self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            mcp_server.main([])  # --root is required: argparse exits after hardening
        harden.assert_called_once_with()

    def test_python_dash_m_cli_survives_an_ascii_console(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            fresh_vault(vault)
            (vault / "Home.md").write_text(
                (vault / "Home.md").read_text(encoding="utf-8").replace("title: ", "title: Zażółć — ", 1),
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0", "LC_ALL": "C", "LANG": "C",
                   "PYTHONPATH": str(ROOT / "src")}
            result = subprocess.run(
                [sys.executable, "-m", "whykit.cli", "query", "Zażółć", "--root", str(vault)],
                capture_output=True, env=env, timeout=120,
            )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertNotIn(b"UnicodeEncodeError", result.stderr)
        self.assertTrue(result.stdout.isascii())
        # CLI shim still used by the rest of the suite.
        self.assertTrue(CLI.is_file())


if __name__ == "__main__":
    unittest.main()
