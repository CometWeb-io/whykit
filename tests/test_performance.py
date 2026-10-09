"""Scale behaviour: output identity, request-scoped caches and time budgets.

The time budgets are deliberately loose (several times the measured cost on a
laptop) so a slow CI runner does not flake; they exist to catch a return of
quadratic behaviour, which costs tens of seconds even at 1,000 notes. Set
``WHYKIT_SKIP_PERF=1`` to skip the timed tests on a runner that is slower still.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _vaults import historical_decision  # noqa: E402
from synthetic_vault import AS_OF, DIGEST_COMMANDS, generate, output_digests  # noqa: E402
from whykit import lint as lint_mod  # noqa: E402
from whykit.cli import main as whykit_main  # noqa: E402

# Recorded with the implementation that preceded the request-scoped caches
# (before any of the optimisations in this file's subject existed), on a
# 400-note synthetic vault. These stay as recorded: they prove the caches
# changed no output. A mismatch means a command's output changed: if that is
# intended, regenerate with
#   python tests/synthetic_vault.py /tmp/v400 --notes 400 --digests
# and add the command to INTENTIONAL_CHANGES below with the reason.
GOLDEN_400 = {
    "backlinks": "6f6cb139395c3677f3b981368e2d0f40882853e5e4e5e4e091d066f32a24b4ef",
    "check": "84f0a3760a0473c2444e24f622bbd20bfce031321b79e030988dcef52fd0761e",
    "context": "18a62c0ddc4ec147c85b880d1c0a648875ffe5fc125e314430d496c8eac49d52",
    "explorer-index": "cf205dbb8cea84897b488abcc281bf96698d5e94b1096b16657b4caba9082a22",
    "graph": "74073f1f6afdf20a6423808116dd8b321914a4f2408aea242c759992fd8e5468",
    "impact": "2aa6199354e4ddf580f32423a6e0eb90940844e6bddd16856a9553bf36037ed8",
    "lint": "fdb9cb952a520f2363e2b962ce96648251c16535e9a09a409ae145030699baf5",
    "pack": "c2ce8f0cbecea75228ee6e2f84c7e6e02dc438d2b30a7405399da3a7e67a80a4",
    "query": "adc811955c86e2070e67eb5ce5393264cfc6450032c592a7623b9cec04848e02",
    "snapshot": "24258a5a13f63b41d532808211b8feabfe3d7ce16d18a11d203dbef76f426eac",
    "status": "e1a55cb5b738b8d89932503092b444ae70edf5e8c94724b5bb078ca91364d5fe",
    "trace": "308b1aa952a07091bd0c56097691fd182de9fe574ff3d177e8d4943f986af09a",
}

# Commands whose output moved on purpose after GOLDEN_400 was recorded. Each
# entry names the behaviour change that explains the new bytes; the diff
# against the original output was reviewed and contains nothing else.
# Every other command must still match its original digest exactly.
INTENTIONAL_CHANGES: dict[str, tuple[str, str]] = {
    # An archived note citing retired evidence (07-research/note-00065, E-040)
    # is history, so `evidence.retired` no longer fires on it: one warning less.
    "lint": (
        "f01ae8c5c9ea5c009b025dafa49147f1fadfd6b51bc230d3e104b97f066b702c",
        "evidence.retired is not reported on superseded or archived records",
    ),
    "check": (
        "e87802143d77a232e8de0517a4e1633e9f346a13261a80344bf2c2b97887366c",
        "evidence.retired is not reported on superseded or archived records; "
        "explicit history_checked and checks.history.checked (false without base)",
    ),
    "status": (
        "677e6cdb9a16bba31c24756a7b724b4fb96c854995b2c73b6307050ae31f02db",
        "evidence.retired is not reported on superseded or archived records",
    ),
    # 56848f33… was the evidence.retired change (summary.warnings); `snapshot
    # --format v1` still reproduces it byte for byte. The default is now v2,
    # whose output differs only in "format" and the added "normalization" key
    # (the synthetic vault is LF-only without BOMs, so every hash is unchanged).
    "snapshot": (
        "7eb76cd644407ecceaf564da3873c6195c42b5339281cd05a0974c031f8b0635",
        "evidence.retired is not reported on superseded or archived records (summary.warnings); "
        "snapshot format v2 by default (format + normalization keys)",
    ),
    # The synthetic vault has lint errors, so explorer-index refuses. It used
    # to write nothing to stdout; it now writes the machine-contract error
    # object (code vault_invalid), with the same exit code 1.
    "explorer-index": (
        "f9e14f64e9352ee51edb9eccd957c21cd8355ca9e1fe1c591ac7e6d94c16f4f8",
        "JSON error object (vault_invalid) on stdout; public errors withhold private findings and counts",
    ),
}

# `snapshot --format v1` must keep producing the bytes the v1 default produced
# before v2 existed (the evidence.retired-era digest), so old baselines and
# tooling that pins v1 see no change.
SNAPSHOT_V1_DIGEST = "56848f33191fc62cd0731b351367ad6bd627efee0e87a49c4bf7cc64c5b672ce"

PERF_NOTES = 1000
# Seconds per command at PERF_NOTES. Measured at well under 1 s each; before
# the caches, `lint` took 13 s and `pack` 49 s on the same vault.
PERF_BUDGET = 8.0


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


class SyntheticVaultTest(unittest.TestCase):
    def test_generator_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate(Path(a), 120)
            generate(Path(b), 120)
            self.assertEqual(_tree_digest(Path(a)), _tree_digest(Path(b)))

    @unittest.skipIf(os.name == "nt", "golden digests are recorded with POSIX paths")
    def test_command_output_matches_golden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 400)
            self.assertEqual(sorted(GOLDEN_400), sorted(name for name, _ in DIGEST_COMMANDS))
            actual = output_digests(vault)
        self.assertLessEqual(set(INTENTIONAL_CHANGES), set(GOLDEN_400))
        for name, original in GOLDEN_400.items():
            expected, why = INTENTIONAL_CHANGES.get(name, (original, ""))
            with self.subTest(command=name):
                self.assertNotEqual(expected == original, bool(why), f"{name}: stale INTENTIONAL_CHANGES entry")
                self.assertEqual(actual[name], expected, f"`whykit {name}` output changed on the synthetic vault")

    @unittest.skipIf(os.name == "nt", "golden digests are recorded with POSIX paths")
    def test_snapshot_v1_reproduces_the_pre_v2_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 400)
            command = ("snapshot-v1", ["snapshot", "--today", AS_OF, "--compact", "--format", "v1"])
            actual = output_digests(vault, (command,))
        self.assertEqual(actual["snapshot-v1"], SNAPSHOT_V1_DIGEST)


@unittest.skipIf(os.environ.get("WHYKIT_SKIP_PERF"), "WHYKIT_SKIP_PERF is set")
class PerformanceBudgetTest(unittest.TestCase):
    vault: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.vault = generate(Path(cls._tmp.name) / "vault", PERF_NOTES)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_read_commands_stay_within_budget(self) -> None:
        for name, argv in DIGEST_COMMANDS:
            with self.subTest(command=name):
                started = time.perf_counter()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = whykit_main([argv[0], "--root", str(self.vault), *argv[1:]])
                elapsed = time.perf_counter() - started
                self.assertIn(code, (0, 1))
                self.assertLess(elapsed, PERF_BUDGET, f"`whykit {name}` took {elapsed:.1f}s on {PERF_NOTES} notes")

    def test_link_resolution_is_not_quadratic(self) -> None:
        # Resolving every link costs one filesystem walk per distinct target,
        # not one per occurrence: count Path.resolve calls during a full lint.
        calls = 0
        original = Path.resolve

        def counting(self: Path, strict: bool = False) -> Path:
            nonlocal calls
            calls += 1
            return original(self, strict=strict)

        Path.resolve = counting  # type: ignore[method-assign]
        try:
            files, _ = lint_mod.lint(self.vault, today=None)
        finally:
            Path.resolve = original  # type: ignore[method-assign]
        self.assertLess(calls, 4 * len(files), f"{calls} resolve() calls for {len(files)} files")


class LineNumbersTest(unittest.TestCase):
    def test_matches_counting_from_the_start(self) -> None:
        text = "a\nbb\n\nccc\n" * 3
        numbers = lint_mod._LineNumbers(text)
        # In order, repeated, then backwards (a fresh scan restarts the count).
        for offset in [0, 1, 2, 2, 5, 9, len(text) - 1, len(text), 3, 0, 7]:
            self.assertEqual(numbers.at(offset), text[:offset].count("\n") + 1, offset)


@unittest.skipIf(os.name == "nt", "the parent-reuse fast path is POSIX-only")
class RequestCacheTest(unittest.TestCase):
    def test_cached_realpath_matches_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "real" / "nested").mkdir(parents=True)
            (base / "real" / "nested" / "file.md").write_text("x", encoding="utf-8")
            (base / "dirlink").symlink_to(base / "real", target_is_directory=True)
            (base / "rellink").symlink_to(Path("real") / "nested", target_is_directory=True)
            (base / "real" / "filelink.md").symlink_to(base / "real" / "nested" / "file.md")
            (base / "dangling.md").symlink_to(base / "nowhere.md")
            (base / "loop-a").symlink_to(base / "loop-b")
            (base / "loop-b").symlink_to(base / "real")
            candidates = [
                base / "real" / "nested" / "file.md",
                base / "dirlink" / "nested" / "file.md",
                base / "rellink" / "file.md",
                base / "real" / "filelink.md",
                base / "dirlink" / "filelink.md",
                base / "dangling.md",
                base / "missing" / "deeper" / "file.md",
                base / "dirlink" / "nested" / ".." / "file.md",
                base / "dirlink" / ".",
                base / "loop-a" / "nested",
                Path("relative") / "path.md",
            ]
            expected = [path.resolve() for path in candidates]
            with lint_mod.path_cache():
                # Twice: the second pass is served from the cache.
                for _ in range(2):
                    self.assertEqual([lint_mod._real(path) for path in candidates], expected)

    def test_cache_does_not_outlive_its_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            (base / "a").mkdir()
            (base / "b").mkdir()
            link = base / "current"
            link.symlink_to(base / "a", target_is_directory=True)
            with lint_mod.path_cache():
                self.assertEqual(lint_mod._real(link / "x.md"), base / "a" / "x.md")
            link.unlink()
            link.symlink_to(base / "b", target_is_directory=True)
            # A long-lived process (the MCP server) must see the new target.
            with lint_mod.path_cache():
                self.assertEqual(lint_mod._real(link / "x.md"), base / "b" / "x.md")
            self.assertEqual(lint_mod.rel(base, link / "x.md"), "b/x.md")

    def test_cached_register_is_copied_and_tracks_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "00-context").mkdir()
            register = root / "00-context" / "evidence-register.md"
            header = "| ID | Source | Type | Date | Accessed | Location | Claims |\n|---|---|---|---|---|---|---|\n"
            register.write_text(header + "| E-001 | One | internal | 2026-01-01 | 2026-01-01 | here | c |\n", encoding="utf-8")
            with lint_mod.path_cache():
                active, _, _ = lint_mod.evidence_register(root)
                active["E-001"]["source"] = "mutated by a caller"
                active["E-999"] = {}
                again, _, _ = lint_mod.evidence_register(root)
                self.assertEqual(again["E-001"]["source"], "One")
                self.assertNotIn("E-999", again)
                register.write_text(
                    header + "| E-001 | One | internal | 2026-01-01 | 2026-01-01 | here | c |\n"
                    "| E-002 | Two | internal | 2026-01-02 | 2026-01-02 | there | d |\n",
                    encoding="utf-8",
                )
                os.utime(register, ns=(1, 1))
                self.assertEqual(sorted(lint_mod.evidence_register(root)[0]), ["E-001", "E-002"])


class CacheScopeAcrossCommandsTest(unittest.TestCase):
    """The request cache never survives a command, a refused write or an MCP call."""

    def setUp(self) -> None:
        from _vaults import fresh_vault

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        fresh_vault(self.vault)

    def cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = whykit_main([*argv, "--root", str(self.vault)])
            except SystemExit as exc:  # argparse paths
                code = int(exc.code or 0)
        # Whatever happened, no request scope may be left behind.
        self.assertIsNone(lint_mod._REQUEST.get(), argv)
        return code, out.getvalue()

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_refused_write_between_reads_leaves_no_stale_state(self) -> None:
        import json
        import stat

        self.assertEqual(self.cli("new", "evidence", "--source", "Export", "--type", "dataset",
                                  "--location", "https://example.com/export.csv", "--claims", "c")[0], 0)
        historical_decision(self.vault, "First", owner="Ops", source_ids=["E-001"], today=dt.date.today(),
                            review_by=(dt.date.today() + dt.timedelta(days=90)).isoformat())
        self.assertIn(self.cli("status", "--json")[0], (0, 1))
        log = self.vault / "00-context" / "review-log.md"
        os.chmod(log, stat.S_IREAD)
        try:
            if os.access(log, os.W_OK):  # pragma: no cover - running as root
                self.skipTest("root ignores file permissions")
            code, _ = self.cli("review", "record", "D-001", "--reviewer", "Ops")
            self.assertEqual(code, 2)
        finally:
            os.chmod(log, stat.S_IREAD | stat.S_IWRITE)
        rows_before = log.read_text(encoding="utf-8").count("[[")
        self.assertEqual(self.cli("review", "record", "D-001", "--reviewer", "Ops")[0], 0)
        # The next read sees the row the write added, not a cached view.
        code, out = self.cli("lint", "--json")
        report = json.loads(out)
        self.assertEqual(report["errors"], 0, report["findings"])
        self.assertEqual(log.read_text(encoding="utf-8").count("[["), rows_before + 1)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_swap_between_commands_is_seen(self) -> None:
        import json

        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        inside = self.vault / "notes" / "real"
        inside.mkdir()
        link = self.vault / "notes" / "current"
        link.symlink_to(inside, target_is_directory=True)
        (self.vault / "Home.md").write_text(
            (self.vault / "Home.md").read_text(encoding="utf-8") + "\n[here](notes/current/x.md)\n", encoding="utf-8")
        (inside / "x.md").write_text("x", encoding="utf-8")
        codes = lambda: [item["code"] for item in json.loads(self.cli("lint", "--json")[1])["findings"]]  # noqa: E731
        self.assertNotIn("markdown_link.outside", codes())
        link.unlink()
        link.symlink_to(outside, target_is_directory=True)
        self.assertIn("markdown_link.outside", codes())

    def test_each_mcp_call_gets_its_own_cache(self) -> None:
        from whykit.mcp_server import VaultTools

        created: list[object] = []
        original = lint_mod._RequestCache

        class Counting(original):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                super().__init__()
                created.append(self)

        tools = VaultTools(self.vault, max_sensitivity="internal")
        lint_mod._RequestCache = Counting  # type: ignore[misc]
        try:
            first, failed = tools.call("status", {"today": "2026-10-01"})
            self.assertFalse(failed, first)
            self.assertIsNone(lint_mod._REQUEST.get())
            second, failed = tools.call("query", {"text": "vault"})
            self.assertFalse(failed, second)
            self.assertIsNone(lint_mod._REQUEST.get())
            _, failed = tools.call("context", {"target": "does-not-exist"})
            self.assertIsNone(lint_mod._REQUEST.get())
        finally:
            lint_mod._RequestCache = original  # type: ignore[misc]
        # One scope per call; nothing nested inside a call opened another.
        self.assertEqual(len(created), 3)
        self.assertEqual(len({id(cache) for cache in created}), 3)


if __name__ == "__main__":
    unittest.main()
