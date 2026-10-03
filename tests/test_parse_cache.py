"""The persistent parse cache in ``.whykit/cache/``: reuse, invalidation, safety.

Every test compares against a run without the cache: the cache may only make
a command faster, never change what it prints.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from synthetic_vault import DIGEST_COMMANDS, generate, output_digests  # noqa: E402
from whykit import lint as lint_mod  # noqa: E402
from whykit import parse_cache  # noqa: E402
from whykit.cli import VAULT_GITIGNORE, main as whykit_main  # noqa: E402
from whykit.vault_index import VaultIndex  # noqa: E402

AS_OF = dt.date(2026, 9, 17)
CACHE = Path(".whykit") / "cache" / parse_cache.CACHE_FILE
LINKS = Path(".whykit") / "cache" / "wikilink_hits.v1"


@contextlib.contextmanager
def no_cache_env(value: str | None) -> object:
    old = os.environ.get(parse_cache.ENV_DISABLE)
    if value is None:
        os.environ.pop(parse_cache.ENV_DISABLE, None)
    else:
        os.environ[parse_cache.ENV_DISABLE] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(parse_cache.ENV_DISABLE, None)
        else:
            os.environ[parse_cache.ENV_DISABLE] = old


@contextlib.contextmanager
def settled_immediately() -> object:
    """Treat files as settled at once, instead of two seconds after a write."""
    with mock.patch.object(parse_cache, "RACY_NS", 0):
        yield


def lint_once(vault: Path) -> tuple[list[lint_mod.Finding], parse_cache.DiskCache]:
    """Lint inside a cache scope; return the findings and the cache it used."""
    with parse_cache.persistent(True):
        with lint_mod.path_cache():
            cache = parse_cache.current(vault.resolve())
            assert cache is not None
            index = VaultIndex.load(vault)
            _, findings = lint_mod.lint(vault, today=AS_OF, vault=index)
    return findings, cache


def lint_uncached(vault: Path) -> list[lint_mod.Finding]:
    return lint_mod.lint(vault, today=AS_OF)[1]


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = whykit_main(list(argv))
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class _VaultCase(unittest.TestCase):
    notes = 60

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = generate(Path(self._tmp.name) / "vault", self.notes).resolve()
        self._env = no_cache_env(None)
        self._env.__enter__()

    def tearDown(self) -> None:
        self._env.__exit__(None, None, None)
        self._tmp.cleanup()

    def note(self, relative: str) -> Path:
        return self.vault / relative

    def assert_same_as_uncached(self, findings: list[lint_mod.Finding]) -> None:
        self.assertEqual(findings, lint_uncached(self.vault))


class OutputIdentityTest(unittest.TestCase):
    """Golden digests: identical with the cache off, cold and warm."""

    @unittest.skipIf(os.name == "nt", "golden digests are recorded with POSIX paths")
    def test_every_pinned_command_prints_the_same_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 400)
            with no_cache_env("1"):
                off = output_digests(vault)
            self.assertFalse((vault / ".whykit" / "cache").exists(), "WHYKIT_NO_CACHE=1 wrote a cache")
            with no_cache_env(None):
                cold = output_digests(vault, DIGEST_COMMANDS[:1])  # writes the cache
                self.assertTrue((vault / CACHE).is_file())
                racy = output_digests(vault)  # entries verified by content hash
                with settled_immediately():
                    output_digests(vault, DIGEST_COMMANDS[:1])  # re-records them as settled
                    warm = output_digests(vault)  # stat-only reuse
        self.assertEqual(cold, {name: off[name] for name in cold})
        self.assertEqual(racy, off)
        self.assertEqual(warm, off)

    def test_digests_match_the_recorded_golden_values_with_a_warm_cache(self) -> None:
        if os.name == "nt":
            self.skipTest("golden digests are recorded with POSIX paths")
        from test_performance import GOLDEN_400, INTENTIONAL_CHANGES

        with tempfile.TemporaryDirectory() as tmp, no_cache_env(None), settled_immediately():
            vault = generate(Path(tmp) / "vault", 400)
            output_digests(vault)
            warm = output_digests(vault)
        for name, original in GOLDEN_400.items():
            expected = INTENTIONAL_CHANGES.get(name, (original, ""))[0]
            self.assertEqual(warm[name], expected, name)


class ReuseTest(_VaultCase):
    def test_unchanged_vault_reuses_every_note_without_reading_it(self) -> None:
        with settled_immediately():
            first, cold = lint_once(self.vault)
            self.assertEqual(cold.hits, 0)
            reads: list[Path] = []
            original = Path.read_bytes

            def counting(path: Path) -> bytes:
                reads.append(path)
                return original(path)

            with mock.patch.object(Path, "read_bytes", counting):
                second, warm = lint_once(self.vault)
        self.assertEqual(warm.misses, 0)
        self.assertEqual(warm.hits, len(cold.digests or {}))
        # Only the whole-file tables lint parses on every run (the registers,
        # the review log and AGENTS.md), read as bytes so a file that is not
        # valid UTF-8 becomes a finding. No note is read for its parse.
        self.assertEqual(
            sorted(path.name for path in reads if self.vault in path.parents and path.suffix == ".md"),
            ["AGENTS.md", "decision-log.md", "evidence-register.md", "review-log.md"],
        )
        self.assertEqual(first, second)
        self.assert_same_as_uncached(second)

    def test_a_warm_run_does_not_rewrite_the_cache(self) -> None:
        with settled_immediately():
            lint_once(self.vault)
            before = os.stat(self.vault / CACHE)
            lint_once(self.vault)
            after = os.stat(self.vault / CACHE)
        self.assertEqual((before.st_mtime_ns, before.st_ino), (after.st_mtime_ns, after.st_ino))

    def test_views_a_later_command_needs_are_added_to_the_cache(self) -> None:
        with settled_immediately(), parse_cache.persistent(True):
            VaultIndex.load(self.vault)  # only front matter, as `query --type` needs
        self.assertTrue((self.vault / CACHE).is_file())
        self.assertFalse((self.vault / LINKS).exists())
        with settled_immediately():
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 0)
        stored = json.loads((self.vault / LINKS).read_text(encoding="utf-8"))["rows"]
        self.assertEqual(len(stored), len(cache.digests or {}))
        self.assert_same_as_uncached(findings)

    def test_a_front_matter_only_command_never_reads_the_views(self) -> None:
        with settled_immediately():
            lint_once(self.vault)
            for name in parse_cache.NOTE_FACTS:
                (self.vault / ".whykit" / "cache" / f"{name}.v1").write_bytes(b"{damaged")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), parse_cache.persistent(True):
                VaultIndex.load(self.vault)
        self.assertEqual(err.getvalue(), "")


class InvalidationTest(_VaultCase):
    target = "01-strategy/note-00000.md"

    def prime(self) -> None:
        lint_once(self.vault)

    def test_edit_with_a_new_size_is_reparsed(self) -> None:
        with settled_immediately():
            self.prime()
            path = self.note(self.target)
            path.write_text(path.read_text(encoding="utf-8") + "\nSee [[does-not-exist]].\n", encoding="utf-8")
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assertIn(("wikilink.missing", "01-strategy/note-00000.md"), {(f.code, f.path) for f in findings})
        self.assert_same_as_uncached(findings)

    def test_same_size_edit_with_the_old_mtime_restored_is_reparsed(self) -> None:
        with settled_immediately():
            self.prime()
            path = self.note(self.target)
            info = os.stat(path)
            text = path.read_text(encoding="utf-8")
            # Same byte length, different content, mtime put back.
            mutated = text[:-2] + ("Z\n" if text[-2:] != "Z\n" else "Y\n")
            self.assertEqual(len(mutated.encode()), len(text.encode()))
            path.write_text(mutated, encoding="utf-8")
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            self.assertEqual(os.stat(path).st_size, info.st_size)
            self.assertEqual(os.stat(path).st_mtime_ns, info.st_mtime_ns)
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assert_same_as_uncached(findings)

    def test_rename_drops_the_old_entry_and_links_follow_the_files(self) -> None:
        with settled_immediately():
            self.prime()
            old = self.note(self.target)
            old.rename(old.with_name("renamed-note.md"))
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assert_same_as_uncached(findings)
        stored = json.loads((self.vault / CACHE).read_text(encoding="utf-8"))["entries"]
        self.assertNotIn(self.target, stored)
        self.assertIn("01-strategy/renamed-note.md", stored)

    def test_delete_drops_the_entry(self) -> None:
        with settled_immediately():
            self.prime()
            self.note(self.target).unlink()
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 0)
        self.assert_same_as_uncached(findings)
        self.assertNotIn(self.target, json.loads((self.vault / CACHE).read_text(encoding="utf-8"))["entries"])

    def test_config_change_discards_the_cache(self) -> None:
        with settled_immediately():
            self.prime()
            config = self.vault / "whykit.toml"
            config.write_text(config.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.hits, 0)
        self.assertEqual(cache.misses, len(cache.digests or {}))
        self.assert_same_as_uncached(findings)

    def test_version_change_discards_the_cache(self) -> None:
        with settled_immediately():
            self.prime()
            with mock.patch.object(parse_cache, "__version__", "99.0.0"):
                _, cache = lint_once(self.vault)
        self.assertEqual(cache.hits, 0)

    def test_source_change_discards_the_cache(self) -> None:
        with settled_immediately():
            self.prime()
            real = parse_cache.fingerprint

            def other_source(root: Path) -> str:
                return real(root) + "-patched"

            with mock.patch.object(parse_cache, "fingerprint", other_source):
                _, cache = lint_once(self.vault)
        self.assertEqual(cache.hits, 0)

    def test_racy_entries_are_verified_by_content(self) -> None:
        # A file system whose signature cannot tell two writes apart (here:
        # ctime ignored, so only mtime, size and inode remain).  The entry was
        # recorded within the racy window, so its content hash is checked.
        def weak(info: os.stat_result) -> tuple[int, int, int, int]:
            return (info.st_mtime_ns, 0, info.st_size, info.st_ino)

        with mock.patch.object(parse_cache, "_signature", weak):
            self.prime()  # files were just written: every entry is racy
            path = self.note(self.target)
            info = os.stat(path)
            data = path.read_bytes()
            path.write_bytes(data.replace(b"note", b"NOTE", 1))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            with settled_immediately():
                findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assert_same_as_uncached(findings)

    def test_clock_behind_the_files_forces_content_checks(self) -> None:
        def weak(info: os.stat_result) -> tuple[int, int, int, int]:
            return (info.st_mtime_ns, 0, info.st_size, info.st_ino)

        behind = time.time_ns() - 3600 * 10**9  # the clock is an hour slow
        with mock.patch.object(parse_cache, "_signature", weak), \
                mock.patch.object(parse_cache.time, "time_ns", lambda: behind):
            self.prime()
            stored = json.loads((self.vault / CACHE).read_text(encoding="utf-8"))["entries"]
            self.assertFalse(any(row[5] for row in stored.values()), "future-dated files recorded as settled")
            path = self.note(self.target)
            info = os.stat(path)
            path.write_bytes(path.read_bytes().replace(b"note", b"NOTE", 1))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assert_same_as_uncached(findings)

    def test_future_mtime_is_never_trusted_by_stat_alone(self) -> None:
        path = self.note(self.target)
        future = time.time_ns() + 86400 * 10**9
        os.utime(path, ns=(future, future))
        self.prime()
        stored = json.loads((self.vault / CACHE).read_text(encoding="utf-8"))["entries"]
        self.assertFalse(stored[self.target][5])

    def test_a_second_load_in_one_command_sees_an_edit_in_between(self) -> None:
        with settled_immediately():
            self.prime()
            with parse_cache.persistent(True), lint_mod.path_cache():
                first = VaultIndex.load(self.vault)
                again = VaultIndex.load(self.vault)
                self.assertEqual([n.front for n in first.notes], [n.front for n in again.notes])
                path = self.note(self.target)
                path.write_text(path.read_text(encoding="utf-8") + "\n[[added-link]]\n", encoding="utf-8")
                after = VaultIndex.load(self.vault)
                note = next(n for n in after.notes if n.path == path)
                self.assertIn("added-link", [target for target, _, _ in note.wikilink_hits])
            findings, _ = lint_once(self.vault)
        self.assert_same_as_uncached(findings)

    def test_concurrent_edit_after_stat_is_not_cached(self) -> None:
        with settled_immediately():
            self.prime()
            with parse_cache.persistent(True), lint_mod.path_cache():
                index = VaultIndex.load(self.vault)
                note = next(n for n in index.notes if n.path.name == "note-00000.md")
                path = self.note(self.target)
                path.write_text(path.read_text(encoding="utf-8") + "\nlate edit\n", encoding="utf-8")
                self.assertIn("late edit", note.text)
                self.assertTrue(note.stale)
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assert_same_as_uncached(findings)


class SecretScanTest(_VaultCase):
    target = "01-strategy/note-00000.md"

    @staticmethod
    def fake_key() -> str:
        # Assembled at run time so the repository never holds the pattern.
        return "AK" + "IA" + "EXAMPLE" + "0" * 9

    def test_secret_added_after_caching_is_found(self) -> None:
        with settled_immediately():
            lint_once(self.vault)
            path = self.note(self.target)
            path.write_text(path.read_text(encoding="utf-8") + f"\nkey {self.fake_key()}\n", encoding="utf-8")
            findings, _ = lint_once(self.vault)
            warm, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 0)
        codes = [(f.code, f.path) for f in warm]
        self.assertIn(("secret.detected", self.target), codes)
        self.assertEqual(findings, warm)
        self.assert_same_as_uncached(warm)

    def test_sensitivity_change_rescans_the_note(self) -> None:
        path = self.note(self.target)
        path.write_text(path.read_text(encoding="utf-8") + f"\nkey {self.fake_key()}\n", encoding="utf-8")
        with settled_immediately():
            lint_once(self.vault)
            text = path.read_text(encoding="utf-8")
            for old, new in (("sensitivity: internal", "sensitivity: restricted"), ("sensitivity: public", "sensitivity: restricted")):
                text = text.replace(old, new)
            path.write_text(text, encoding="utf-8")
            findings, cache = lint_once(self.vault)
        self.assertEqual(cache.misses, 1)
        self.assertIn(("secret.detected", self.target), [(f.code, f.path) for f in findings])
        self.assert_same_as_uncached(findings)

    def test_secret_scan_off_is_not_served_from_the_cache(self) -> None:
        path = self.note(self.target)
        path.write_text(path.read_text(encoding="utf-8") + f"\nkey {self.fake_key()}\n", encoding="utf-8")
        with settled_immediately():
            lint_once(self.vault)
            with parse_cache.persistent(True):
                _, off = lint_mod.lint(self.vault, today=AS_OF, secrets=False)
        self.assertNotIn("secret.detected", {f.code for f in off})


class StringPoolTest(unittest.TestCase):
    def test_link_targets_do_not_fill_the_front_matter_pool(self) -> None:
        text = "---\ntitle: Pool\n---\n" + "".join(f"[[pool-target-{i}]]\n" for i in range(50))
        note = lint_mod.load_note(Path("pool.md"), text=text)
        self.assertEqual(len(note.wikilink_hits), 50)
        self.assertFalse([key for key in lint_mod._SHARED if key.startswith("pool-target-")])
        self.assertIs(note.wikilink_hits[0][0], lint_mod._shared_target("pool-target-0"))


class RobustnessTest(_VaultCase):
    def test_corrupted_cache_is_ignored_with_a_warning_and_rebuilt(self) -> None:
        lint_once(self.vault)
        (self.vault / CACHE).write_bytes(b"{not json")
        code, out, err = run_cli("lint", "--root", str(self.vault), "--json", "--today", AS_OF.isoformat())
        with no_cache_env("1"):
            expected = run_cli("lint", "--root", str(self.vault), "--json", "--today", AS_OF.isoformat())
        self.assertIn("ignoring unreadable parse cache", err)
        self.assertEqual((code, out), expected[:2])
        json.loads((self.vault / CACHE).read_text(encoding="utf-8"))  # rebuilt

    def test_damaged_entries_are_recomputed_not_trusted(self) -> None:
        with settled_immediately():
            lint_once(self.vault)
            data = json.loads((self.vault / CACHE).read_text(encoding="utf-8"))
            links = json.loads((self.vault / LINKS).read_text(encoding="utf-8"))
            for number, (key, row) in enumerate(data["entries"].items()):
                if number % 3 == 0 and links["rows"][key][1]:
                    links["rows"][key][1] = [["x", "not a line", True]]
                elif number % 3 == 1:
                    links["rows"][key][0] = "0" * 16  # stored for other content
                elif row[6][1]:
                    row[6][0] = {"title": 5}
            (self.vault / CACHE).write_text(json.dumps(data), encoding="utf-8")
            (self.vault / LINKS).write_text(json.dumps(links), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                findings, _ = lint_once(self.vault)
        self.assertIn("damaged", err.getvalue())
        self.assert_same_as_uncached(findings)

    def test_wrong_shape_rows_discard_the_whole_cache(self) -> None:
        lint_once(self.vault)
        data = json.loads((self.vault / CACHE).read_text(encoding="utf-8"))
        key = next(iter(data["entries"]))
        data["entries"][key] = ["oops"]
        (self.vault / CACHE).write_text(json.dumps(data), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            findings, cache = lint_once(self.vault)
        self.assertIn("ignoring unreadable parse cache", err.getvalue())
        self.assertEqual(cache.hits, 0)
        self.assert_same_as_uncached(findings)

    def test_no_cache_flag_and_environment_variable(self) -> None:
        argv = ["lint", "--root", str(self.vault), "--json", "--today", AS_OF.isoformat()]
        run_cli("--no-cache", *argv)
        self.assertFalse((self.vault / ".whykit" / "cache").exists())
        for value in ("1", "true", "yes"):
            with no_cache_env(value):
                run_cli(*argv)
            self.assertFalse((self.vault / ".whykit" / "cache").exists(), value)
        with no_cache_env("0"):
            run_cli(*argv)
        self.assertTrue((self.vault / CACHE).is_file())

    def test_writing_commands_and_mcp_never_create_a_cache(self) -> None:
        run_cli("new", "note", "Cache check", "--root", str(self.vault), "--workstream", "01-strategy", "--type", "research")
        self.assertFalse((self.vault / ".whykit" / "cache").exists())
        from whykit.mcp_server import VaultTools

        tools = VaultTools(self.vault)
        tools.call("status", {"today": AS_OF.isoformat()})
        tools.call("query", {"text": "pipeline"})
        self.assertFalse((self.vault / ".whykit" / "cache").exists())

    def test_cache_directory_ignores_itself_and_the_template_ignores_it(self) -> None:
        lint_once(self.vault)
        ignore = (self.vault / ".whykit" / "cache" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*", ignore.splitlines())
        self.assertIn(".whykit/cache/", VAULT_GITIGNORE.splitlines())

    def test_cache_file_is_not_secret_scanned_or_collected(self) -> None:
        lint_once(self.vault)
        paths = [p.relative_to(self.vault).as_posix() for p, _ in lint_mod._text_files_for_secret_scan(self.vault)]
        self.assertFalse([p for p in paths if p.startswith(".whykit/cache")])
        self.assertFalse([p for p in lint_mod.collect_markdown(self.vault, []) if ".whykit" in p.parts])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_cache_directory_is_neither_read_nor_written(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            (self.vault / ".whykit").mkdir(exist_ok=True)
            (self.vault / ".whykit" / "cache").symlink_to(outside, target_is_directory=True)
            findings, _ = lint_once(self.vault)
            self.assertEqual(list(Path(outside).iterdir()), [])
        self.assert_same_as_uncached(findings)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_gitignore_and_lock_are_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            cache_dir = self.vault / ".whykit" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / ".gitignore").symlink_to(Path(outside) / "planted-ignore")
            (cache_dir / parse_cache.LOCK_FILE).symlink_to(Path(outside) / "planted-lock")
            lint_once(self.vault)
            self.assertEqual(list(Path(outside).iterdir()), [])
        self.assertFalse((self.vault / CACHE).exists())  # lock refused, nothing written

    @unittest.skipIf(os.name == "nt", "advisory locks differ on Windows")
    def test_a_held_lock_skips_the_write(self) -> None:
        import fcntl

        for lock in (Path(".whykit") / "cache" / parse_cache.LOCK_FILE, Path(".whykit") / "mutation.lock"):
            with self.subTest(lock=str(lock)):
                path = self.vault / lock
                path.parent.mkdir(parents=True, exist_ok=True)
                (self.vault / CACHE).unlink(missing_ok=True)
                with open(path, "a+b") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        findings, _ = lint_once(self.vault)
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                self.assertFalse((self.vault / CACHE).exists())
                self.assert_same_as_uncached(findings)

    def test_concurrent_runs_never_see_a_partial_cache(self) -> None:
        argv = ["lint", "--root", str(self.vault), "--json", "--today", AS_OF.isoformat()]
        with no_cache_env("1"):
            expected = run_cli(*argv)[:2]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH")]))}
        env.pop(parse_cache.ENV_DISABLE, None)
        results: list[tuple[int, str, str]] = []

        def worker() -> None:
            for _ in range(3):
                done = subprocess.run(
                    [sys.executable, "-m", "whykit.cli", *argv], capture_output=True, text=True,
                    encoding="utf-8", env=env, timeout=300,
                )
                results.append((done.returncode, done.stdout, done.stderr))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 12)
        for code, out, err in results:
            self.assertEqual((code, out), expected)
            self.assertNotIn("parse cache", err)
        json.loads((self.vault / CACHE).read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in (self.vault / CACHE).parent.iterdir() if ".tmp-" in p.name], [])


if __name__ == "__main__":
    unittest.main()
