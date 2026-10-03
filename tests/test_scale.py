"""Scale work that must not change answers: the secret scan, memory and the MCP note cache.

Every optimisation here is checked against a plain reference implementation:
the secret scan against the original read-every-file-again scanner, the path
helpers against ``pathlib``, the impact view against the full graph, and the
MCP note cache against what is on disk after every kind of edit.

Planted secrets are assembled at run time from fragments, so this file never
contains a credential-shaped string; every value is fake and every domain is
reserved (RFC 2606).
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tests"))

from synthetic_vault import generate  # noqa: E402
from whykit import lint as lint_mod  # noqa: E402
from whykit.vault_index import NoteCache, VaultIndex, reuse_notes  # noqa: E402

TODAY = __import__("datetime").date(2026, 9, 17)


# ---------------------------------------------------------------------------
# Planted secrets (fake, assembled from fragments)
# ---------------------------------------------------------------------------

def _planted() -> dict[str, str]:
    """Relative path -> content with fake credential-shaped strings."""
    dash5 = "-" * 5
    private = dash5 + "BEGIN " + "RSA PRIV" + "ATE KEY" + dash5
    openai = "s" + "k-" + "exampleEXAMPLE0000example00"
    aws = "AK" + "IA" + "EXAMPLE0EXAMPLE0"
    github = "gh" + "p_" + "example" * 6
    slack = "xo" + "xb-" + "0000-example-invalid"
    value = "example.invalid-not-a-real-value-0000"
    return {
        # A note with a byte-order mark: the vault index strips it, the raw file keeps it.
        "01-strategy/bom-note.md": "\ufeff---\ntitle: \"BOM\"\n---\n" + f"api_key = {value}\n\nline four {openai}\n",
        "01-strategy/crlf-note.md": "---\r\ntitle: \"CRLF\"\r\n---\r\n" + f"token\r\nAccess-Token: {value}\r\n{aws}\r\n",
        "01-strategy/every-kind.md": (
            "# Every kind\n\nSee https://example.com/runbook.\n\n"
            f"{private}\n{openai}\n{aws}\n{github}\n{slack}\n"
            f"PASSWORD={value}\nclient-secret: '{value}'\n"
            f"twice on one line: {aws} {aws}\n"
        ),
        # Case folding outside ASCII: the Kelvin sign and the long s.
        "01-strategy/unicode-case.md": f"Notes for owner@example.com\napi_\u212aey: {value}\npa\u017f\u017fword = {value}\n",
        "01-strategy/inside-code.md": f"```\n{github}\n```\n`{slack}`\n",
        # Near misses: no finding, and no prefilter shortcut may invent one.
        "01-strategy/near-misses.md": "sk-short\nAKIAlowercase000000\nghp_\nxoxz-0000000000000000000\n-----BEGIN PUBLIC KEY-----\napi_key = short\n",
        "notes.txt": f"{github}\n",
        "config/.env.local": f"API_KEY={value}\n",
        "data/payload.json": '{"client_secret": "' + value + '"}',
        # Scanned by the secret scan although the note index skips the folder.
        "examples/demo/leak.md": f"{aws}\n",
        # Skipped by both.
        "tests/fixture.md": f"{aws}\n",
        "assets/picture.bin": f"{aws}\n",
    }


def _plant(root: Path) -> None:
    for relative, content in _planted().items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    (root / "data" / "latin1.txt").write_bytes(b"api_key = caf\xe9\n")


def _reference_secret_findings(root: Path) -> list[tuple]:
    """The scanner as it was before it reused note text and prefilters."""
    root = root.resolve()
    depth = len(root.parts)
    found: list[tuple] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if any(part in lint_mod.SECRET_SKIP_DIRS for part in path.parts[depth:]):
            continue
        if not (path.name.startswith(".env") or path.suffix.lower() in lint_mod.TEXT_SECRET_EXTENSIONS):
            continue
        relative = path.relative_to(root).as_posix()
        if path.stat().st_size > 5_000_000:
            found.append((relative, None, "secret.scan_skipped_large_file"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            found.append((relative, None, "secret.scan_non_utf8"))
            continue
        for _label, pattern in lint_mod.SECRET_PATTERNS:
            for match in pattern.finditer(text):
                found.append((relative, text[: match.start()].count("\n") + 1, "secret.detected"))
    return found


def _secret_findings(findings: list) -> list[tuple]:
    return [(item.path, item.line, item.code) for item in findings if item.code.startswith("secret.")]


class SecretScanEquivalenceTest(unittest.TestCase):
    """Reusing loaded note text and skipping by prefilter finds exactly the same secrets."""

    def assert_same_as_reference(self, root: Path, *, minimum: int = 0) -> None:
        expected = _reference_secret_findings(root)
        with lint_mod.path_cache():
            index = VaultIndex.load(root)
            _, findings = lint_mod.lint(root, today=TODAY, vault=index)
            direct: list = []
            lint_mod.check_secrets(root.resolve(), direct)
        self.assertEqual(_secret_findings(findings), expected)
        self.assertEqual(_secret_findings(direct), expected)
        self.assertGreaterEqual(len(expected), minimum)

    def test_example_vaults(self) -> None:
        for name in ("northline", "tiny"):
            with self.subTest(vault=name):
                self.assert_same_as_reference(ROOT / "examples" / name)

    def test_synthetic_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_same_as_reference(generate(Path(tmp) / "vault", 300))

    def test_planted_secrets_in_a_synthetic_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 300)
            _plant(vault)
            # Every planted kind, the BOM and CRLF notes, the Unicode case
            # variants and the examples/ copy; not the tests/ or .bin copies.
            self.assert_same_as_reference(vault, minimum=20)
            found = {(path, code) for path, _, code in _reference_secret_findings(vault)}
            self.assertIn(("01-strategy/unicode-case.md", "secret.detected"), found)
            self.assertIn(("examples/demo/leak.md", "secret.detected"), found)
            self.assertIn(("data/latin1.txt", "secret.scan_non_utf8"), found)
            self.assertNotIn(("tests/fixture.md", "secret.detected"), found)

    def test_secret_scan_reads_only_files_the_index_did_not_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 60).resolve()
            _plant(vault)
            reads: list[Path] = []
            original = Path.read_text

            def counting(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
                reads.append(self)
                return original(self, *args, **kwargs)

            with lint_mod.path_cache():
                index = VaultIndex.load(vault)
                Path.read_text = counting  # type: ignore[method-assign]
                try:
                    lint_mod.check_secrets(vault, [], {note.path: note.text for note in index.notes})
                finally:
                    Path.read_text = original  # type: ignore[method-assign]
            notes = {note.path for note in index.notes}
            self.assertEqual([path for path in reads if path in notes], [])
            # Files outside the note index are still read and scanned.
            self.assertIn(vault / "examples" / "demo" / "leak.md", reads)
            self.assertIn(vault / "data" / "payload.json", reads)


class SecretPrefilterSoundnessTest(unittest.TestCase):
    """A prefilter may only skip a file the pattern cannot match."""

    def test_one_prefilter_per_pattern(self) -> None:
        self.assertEqual(len(lint_mod.SECRET_PREFILTERS), len(lint_mod.SECRET_PATTERNS))

    def test_every_match_passes_its_prefilter(self) -> None:
        rng = random.Random(20261003)
        fragments = [
            "-----BEGIN ", "PRIVATE KEY-----", "RSA ", "sk-", "AKIA", "gh", "p_", "o_", "s_", "xox", "b-", "p-",
            "api", "_", "-", "key", "KEY", "Key", "\u212aey", "access", "token", "TOKEN", "client", "secret",
            "SECRET", "password", "pa\u017f\u017fword", "PASSWORD", "\u0130", "=", ":", "\"", "'", " ", "\n",
            "example", "EXAMPLE0", "0000", ".invalid", "/", "+", "\u00e9",
        ]
        prefixes = [
            "-----BEGIN ", "-----BEGIN RSA PRIV" + "ATE KEY-----", "-----BEGIN PRIV" + "ATE KEY-----", "s" + "k-", "AK" + "IA", "gh" + "p_", "gh" + "s_", "gh" + "x_", "xo" + "xb-", "xo" + "xs-",
            "xo" + "xz-", "api_key=", "API-KEY: ", "access_token = '", "client-secret:", "password=", "\u212aey",
        ]
        matched = [0] * len(lint_mod.SECRET_PATTERNS)
        tails = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "abcdefghijklmnopqrstuvwxyz0123456789_-", "0123456789/+=."]
        for _ in range(20_000):
            if rng.random() < 0.5:
                text = "".join(rng.choice(fragments) for _ in range(rng.randint(1, 12)))
            else:
                tail = rng.choice(tails)
                text = (
                    rng.choice(["", " ", "x", "\n", "\u00e9"])
                    + rng.choice(prefixes)
                    + "".join(rng.choice(tail) for _ in range(rng.choice([16, 16, 24, 36, rng.randint(0, 45)])))
                    + rng.choice(["", " ", " ", "x", "\n"])
                )
            for number, ((label, pattern), prefilter) in enumerate(
                zip(lint_mod.SECRET_PATTERNS, lint_mod.SECRET_PREFILTERS, strict=True)
            ):
                if pattern.search(text):
                    matched[number] += 1
                    self.assertTrue(prefilter(text), f"{label}: prefilter rejected a match in {text!r}")
        # The generator does reach every pattern, or the test proves nothing.
        self.assertTrue(all(count >= 20 for count in matched), matched)

    def test_planted_values_pass_their_prefilters(self) -> None:
        for relative, content in _planted().items():
            for (label, pattern), prefilter in zip(lint_mod.SECRET_PATTERNS, lint_mod.SECRET_PREFILTERS, strict=True):
                if pattern.search(content):
                    with self.subTest(file=relative, pattern=label):
                        self.assertTrue(prefilter(content))

    def test_non_ascii_text_always_gets_the_credential_scan(self) -> None:
        value = "example.invalid-not-a-real-value-0000"
        text = f"api_\u212aey = {value}"
        self.assertTrue(lint_mod.SECRET_PATTERNS[-1][1].search(text))
        self.assertTrue(lint_mod.SECRET_PREFILTERS[-1](text))


# ---------------------------------------------------------------------------
# Path helpers that replace pathlib calls on the hot path
# ---------------------------------------------------------------------------

@unittest.skipIf(os.name == "nt", "the string fast path is POSIX-only")
class PathHelperTest(unittest.TestCase):
    def test_relative_text_matches_relative_to(self) -> None:
        pairs = [
            ("/a/b/c.md", "/a/b"), ("/a/b", "/a/b"), ("/a/bc/d.md", "/a/b"), ("/a/b/c.md", "/"),
            ("/", "/"), ("/a", "/a/b"), ("/a/b-c/d.md", "/a/b"), ("/x/y.md", "/a"), ("/a/b/.hidden", "/a/b"),
            ("/a/b/c d/e.md", "/a/b"), ("/a/b/\u00e9.md", "/a/b"), ("/A/b.md", "/a"),
        ]
        for inner, outer in pairs:
            with self.subTest(path=inner, root=outer):
                try:
                    expected: str | None = Path(inner).relative_to(Path(outer)).as_posix()
                except ValueError:
                    expected = None
                self.assertEqual(lint_mod._relative_text(Path(inner), Path(outer)), expected)

    def test_path_order_matches_path_comparison(self) -> None:
        names = ["a/b", "a-b/x", "a.b", "A/b", "a/b/c", "a", "a/B", "\u00e9/x", "e/x", "a b/c", "a/b-c", "a/b/c-d", "a/b0"]
        paths = [Path("/root") / name for name in names]
        self.assertEqual(sorted(paths, key=lint_mod._path_order), sorted(paths))

    def test_cached_realpath_keeps_the_callers_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            (base / "note.md").write_text("x", encoding="utf-8")
            path = base / "note.md"
            with lint_mod.path_cache():
                self.assertIs(lint_mod._real(path), path)
                self.assertEqual(lint_mod._real(Path(str(path))), path.resolve())


class NoteBodyTest(unittest.TestCase):
    def test_body_matches_splitting_on_newlines(self) -> None:
        texts = [
            "---\ntitle: x\n---\nbody\n", "---\ntitle: x\n---", "---\n---\n\n\nbody", "---\na: 1\n---\nno newline",
            "---\na: 1\n---\n", "plain text\nno front\n", "---\na: 1\n---\n\n# H\n\ntext\n\n",
        ]
        for text in texts:
            note = lint_mod.load_note(Path("x.md"), text=text)
            with self.subTest(text=text):
                expected = "\n".join(note.text.split("\n")[note.body_offset:]) if note.has_front else note.text
                self.assertEqual(note.body, expected)
        # An offset past the end of the text gives an empty body, as split did.
        note = lint_mod.load_note(Path("x.md"), text="---\na: 1\n---")
        note.body_offset = 10
        self.assertEqual(note.body, "")


class SharedStringPoolTest(unittest.TestCase):
    def test_front_matter_strings_are_shared_and_unchanged(self) -> None:
        first = lint_mod.load_note(Path("a.md"), text='---\nstatus: approved\nowner: "Ops Lead"\ntags: [x, y]\n---\n')
        second = lint_mod.load_note(Path("b.md"), text='---\nstatus: approved\nowner: "Ops Lead"\ntags: [x, y]\n---\n')
        self.assertEqual(first.front, {"status": "approved", "owner": "Ops Lead", "tags": ["x", "y"]})
        self.assertIs(first.front["status"], second.front["status"])
        self.assertIs(first.front["owner"], second.front["owner"])
        self.assertIs(next(iter(first.front)), next(iter(second.front)))

    def test_pool_is_bounded(self) -> None:
        saved = dict(lint_mod._SHARED)
        try:
            for number in range(lint_mod._SHARED_MAX_ENTRIES + 100):
                self.assertEqual(lint_mod._shared(f"value-{number}"), f"value-{number}")
            self.assertLessEqual(len(lint_mod._SHARED), lint_mod._SHARED_MAX_ENTRIES)
            long = "x" * (lint_mod._SHARED_MAX_CHARS + 1)
            lint_mod._shared(long)
            self.assertNotIn(long, lint_mod._SHARED)
        finally:
            lint_mod._SHARED.clear()
            lint_mod._SHARED.update(saved)


# ---------------------------------------------------------------------------
# Impact view: neighbours without materialising the whole graph
# ---------------------------------------------------------------------------

def _reference_neighbours(root: Path, index: VaultIndex, node_id: str) -> tuple[list[dict], list[dict]]:
    from whykit.graph import build_graph

    graph = build_graph(root, vault=index)
    by_node = {item["id"]: item for item in graph["nodes"]}
    incoming = sorted({edge["from"] for edge in graph["edges"] if edge["type"] == "wikilink" and edge["to"] == node_id})
    outgoing = sorted({edge["to"] for edge in graph["edges"] if edge["type"] == "wikilink" and edge["from"] == node_id})
    return [by_node[v] for v in incoming if v in by_node], [by_node[v] for v in outgoing if v in by_node]


class ImpactViewTest(unittest.TestCase):
    def test_neighbours_match_the_full_graph(self) -> None:
        from whykit.impact import analyze_impact

        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 200).resolve()
            with lint_mod.path_cache():
                index = VaultIndex.load(vault)
                for note in index.notes[::7]:
                    node_id = index.relative(note.path).removesuffix(".md")
                    report = analyze_impact(vault, index.relative(note.path), vault=index)
                    with self.subTest(note=node_id):
                        incoming, outgoing = _reference_neighbours(vault, index, node_id)
                        self.assertEqual(report["incoming"], incoming)
                        self.assertEqual(report["outgoing"], outgoing)

    @unittest.skipIf(os.name == "nt", "':' is not allowed in Windows file names")
    def test_document_named_like_an_evidence_node_answers_like_the_graph(self) -> None:
        from _vaults import fresh_vault
        from whykit.impact import analyze_impact

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            fresh_vault(vault)
            vault = vault.resolve()
            (vault / "evidence:E-001.md").write_text("# odd name\n\nCites E-001.\n", encoding="utf-8")
            (vault / "linker.md").write_text("# linker\n\n[[evidence:E-001]] and E-001\n", encoding="utf-8")
            with lint_mod.path_cache():
                index = VaultIndex.load(vault)
                report = analyze_impact(vault, "linker", vault=index)
                _, outgoing = _reference_neighbours(vault, index, "linker")
            self.assertEqual(report["outgoing"], outgoing)


# ---------------------------------------------------------------------------
# Peak memory budget
# ---------------------------------------------------------------------------

@unittest.skipIf(os.environ.get("WHYKIT_SKIP_PERF"), "WHYKIT_SKIP_PERF is set")
class PeakMemoryTest(unittest.TestCase):
    """Peak traced allocation of one request stays well below where it was.

    Measured in a fresh interpreter at 1,000 notes: lint 5.2-5.5 MB (6.7-8.0
    MB before the streaming work), pack 7.1-7.4 MB (10.8-12.4 MB before),
    across CPython 3.11, 3.12 and 3.14. Each budget sits between the two
    ranges, so it fails on a return of the whole-graph copy in `pack` or of a
    second copy of every path, but not on a Python version's object sizes.

    The measurement runs in its own interpreter. Inside the full suite the
    peak drifted with whatever earlier tests had left behind: a full `re`
    cache recompiles patterns inside the traced window, and a large
    long-lived heap delays cyclic collection, which pushed lint to 6.5 MB on
    a loaded machine without any change to WhyKit. The best of three runs is
    taken, since noise can only add to a peak.
    """

    PROBE = (
        "import datetime as dt, gc, json, sys, tracemalloc\n"
        "from pathlib import Path\n"
        "from whykit import lint\n"
        "from whykit.pack import build_pack\n"
        "vault = Path(sys.argv[1])\n"
        "runs = {\n"
        "    'lint': lambda: lint.lint(vault, today=dt.date(2026, 9, 17)),\n"
        "    'pack': lambda: build_pack(vault, targets=['D-010'], query='pipeline'),\n"
        "}\n"
        "peaks = {}\n"
        "for name, run in runs.items():\n"
        "    run()  # imports and process-wide caches are not the request's cost\n"
        "    best = None\n"
        "    for _ in range(3):\n"
        "        gc.collect()\n"
        "        tracemalloc.start()\n"
        "        try:\n"
        "            run()\n"
        "            peak = tracemalloc.get_traced_memory()[1] / 2**20\n"
        "        finally:\n"
        "            tracemalloc.stop()\n"
        "        best = peak if best is None else min(best, peak)\n"
        "    peaks[name] = best\n"
        "print(json.dumps(peaks))\n"
    )

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.vault = generate(Path(cls._tmp.name) / "vault", 1000).resolve()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_lint_and_pack_peaks(self) -> None:
        import json
        import subprocess

        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
        result = subprocess.run(
            [sys.executable, "-c", self.PROBE, str(self.vault)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        peaks = json.loads(result.stdout)
        self.assertLess(peaks["lint"], 6.0, f"lint peaked at {peaks['lint']:.1f} MB on 1,000 notes")
        self.assertLess(peaks["pack"], 8.5, f"pack peaked at {peaks['pack']:.1f} MB on 1,000 notes")


# ---------------------------------------------------------------------------
# MCP: parsed notes reused across calls, never stale
# ---------------------------------------------------------------------------

class McpNoteCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        from _vaults import fresh_vault
        from whykit.mcp_server import VaultTools

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        fresh_vault(self.vault)
        self.note = self.vault / "notes" / "cache-probe.md"
        self.note.parent.mkdir(exist_ok=True)
        self.note.write_text(self.body("alpha"), encoding="utf-8")
        self.tools = VaultTools(self.vault, max_sensitivity="internal")

    @staticmethod
    def body(word: str) -> str:
        return f'---\ntitle: "Cache probe"\nsensitivity: internal\n---\n\n# Cache probe\n\nThe word is {word}.\n'

    def content(self) -> str:
        payload, failed = self.tools.call("context", {"target": "notes/cache-probe"})
        self.assertFalse(failed, payload)
        return str(payload.get("content") or "") if payload.get("exists") else "<missing>"

    def test_repeated_calls_reuse_parsed_notes(self) -> None:
        self.tools.notes.racy_ns = 0
        self.assertIn("alpha", self.content())
        misses = self.tools.notes.misses
        self.assertGreater(misses, 0)
        loads: list[Path] = []
        original = lint_mod.load_note

        def counting(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            loads.append(path)
            return original(path, *args, **kwargs)

        import whykit.vault_index as vault_index_mod

        vault_index_mod.load_note = counting  # type: ignore[assignment]
        try:
            for tool, arguments in (("context", {"target": "notes/cache-probe"}), ("status", {"today": "2026-10-01"}),
                                    ("query", {"text": "probe"}), ("trace", {}), ("backlinks", {"target": "Home"})):
                payload, failed = self.tools.call(tool, arguments)
                self.assertFalse(failed, (tool, payload))
        finally:
            vault_index_mod.load_note = original  # type: ignore[assignment]
        self.assertEqual(loads, [], "an unchanged note was parsed again")
        self.assertEqual(self.tools.notes.misses, misses)

    def edit_and_check(self, change, expected: str) -> None:  # type: ignore[no-untyped-def]
        for racy_ns in (0, NoteCache.racy_ns):
            with self.subTest(racy_ns=racy_ns):
                self.note.write_text(self.body("alpha"), encoding="utf-8")
                self.tools.notes.racy_ns = racy_ns
                self.assertIn("alpha", self.content())
                self.assertIn("alpha", self.content())  # cached (or deliberately not)
                change()
                self.assertIn(expected, self.content())

    def test_edit_with_a_new_size_is_seen(self) -> None:
        self.edit_and_check(lambda: self.note.write_text(self.body("gamma-longer"), encoding="utf-8"), "gamma-longer")

    def test_same_size_edit_with_restored_mtime_is_seen(self) -> None:
        def change() -> None:
            before = self.note.stat()
            replacement = self.note.with_name(".cache-probe.tmp")
            replacement.write_text(self.body("omega"), encoding="utf-8")
            self.assertEqual(replacement.stat().st_size, before.st_size)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, self.note)  # atomic save: a new inode, same size and mtime

        self.edit_and_check(change, "omega")

    def test_in_place_edit_with_a_new_mtime_is_seen(self) -> None:
        def change() -> None:
            before = self.note.stat()
            self.note.write_text(self.body("delta"), encoding="utf-8")
            os.utime(self.note, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

        self.edit_and_check(change, "delta")

    def test_deleted_and_added_notes_are_seen(self) -> None:
        self.tools.notes.racy_ns = 0
        self.assertIn("alpha", self.content())
        self.note.unlink()
        self.assertEqual(self.content(), "<missing>")
        self.note.write_text(self.body("beta"), encoding="utf-8")
        self.assertIn("beta", self.content())

    def test_freshly_written_files_are_not_cached(self) -> None:
        cache = NoteCache()
        with lint_mod.path_cache(), reuse_notes(cache):
            VaultIndex.load(self.vault)
        # Every file was written moments ago, inside the racy window.
        self.assertEqual(cache._entries, {})
        cache.racy_ns = 0
        with lint_mod.path_cache(), reuse_notes(cache):
            VaultIndex.load(self.vault)
        self.assertTrue(cache._entries)

    def test_cache_is_scoped_to_the_call(self) -> None:
        from whykit.vault_index import _NOTE_CACHE

        self.tools.call("query", {"text": "probe"})
        self.assertIsNone(_NOTE_CACHE.get())

    def test_moved_vault_is_reported_not_served_from_cache(self) -> None:
        self.tools.notes.racy_ns = 0
        self.assertIn("alpha", self.content())
        shutil.rmtree(self.vault)
        payload, failed = self.tools.call("context", {"target": "notes/cache-probe"})
        self.assertTrue(failed)
        self.assertEqual(payload["error"]["code"], "vault_unavailable")


class BenchScriptTest(unittest.TestCase):
    """`scripts/bench.py --mcp` and `--memory`, the sources of the documented numbers."""

    def bench(self, *argv: str) -> dict:
        import json
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bench.py"), "--vault", str(ROOT / "examples" / "tiny"), "--json", *argv],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_mcp_mode_times_every_tool(self) -> None:
        from whykit.mcp_server import TOOL_NAMES

        report = self.bench("--mcp", "--repeat", "2")["mcp"]
        self.assertEqual(sorted(report), sorted(TOOL_NAMES))
        for row in report.values():
            self.assertGreaterEqual(row["cold"], 0)
            self.assertGreaterEqual(row["repeat"], 0)

    def test_memory_mode_reports_a_peak_per_command(self) -> None:
        report = self.bench("--memory", "--only", "lint", "--only", "pack")["peak_mb"]
        self.assertEqual(sorted(report), ["lint", "pack"])
        self.assertTrue(all(value > 0 for value in report.values()), report)


if __name__ == "__main__":
    unittest.main()
