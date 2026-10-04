"""Regressions for hostile vault content, policy patterns and Git paths.

Each test pins one crash, hang or injection the fuzz harness
(``tests/fuzz_parsers.py``) or the security review found. See
``docs/security-model.md`` for what WhyKit guarantees and what it does not.
"""
from __future__ import annotations

import io
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fuzz_parsers  # noqa: E402
from whykit import lint as lint_mod  # noqa: E402
from whykit import rule_policy  # noqa: E402
from whykit.lint import _parse_front_matter, _split_table_row, lint, load_note  # noqa: E402
from whykit.lint import main as lint_main  # noqa: E402
from whykit.rule_policy import build_policy, check_custom_rules, regex_problem  # noqa: E402
from _vaults import fresh_vault  # noqa: E402

NOTE_HEAD = (
    "---\ntitle: Hostile\ntype: guide\nstatus: draft\nowner: Tester\ncreated: 2026-09-01\n"
    "last_updated: 2026-09-01\nsource_of_truth: false\nsensitivity: internal\n---\n\n"
)


class VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.today = fresh_vault(self.vault, "--minimal")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def note(self, relative: str, body: str) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NOTE_HEAD + body + "\n", encoding="utf-8")
        return path


class LinkTargetTests(VaultCase):
    def test_a_link_name_longer_than_the_filesystem_allows_is_a_finding_not_a_crash(self) -> None:
        long = "a" * 300
        self.note("notes/long.md", f"[[{long}]] ![[x/{long}.png]] [y]({long}.md) [z](notes/{long}/{long}.md)")
        _, findings = lint(self.vault, today=self.today)
        codes = {(f.path, f.code) for f in findings}
        self.assertIn(("notes/long.md", "wikilink.missing"), codes)
        self.assertIn(("notes/long.md", "embed.missing"), codes)
        self.assertIn(("notes/long.md", "markdown_link.missing"), codes)

    def test_a_nul_byte_in_a_link_target_is_a_finding_not_a_crash(self) -> None:
        self.note("notes/nul.md", "[[a\x00b]] [y](a\x00b.md)")
        _, findings = lint(self.vault, today=self.today)
        self.assertTrue(any(f.path == "notes/nul.md" and f.code == "wikilink.missing" for f in findings))


    def test_a_nul_byte_is_a_finding_on_the_windows_resolve_route_too(self) -> None:
        # Windows takes the full ``Path.resolve()`` route, whose ``stat`` raises
        # ValueError for an embedded NUL; POSIX never reaches it.
        self.note("notes/nul.md", "[[a\x00b]] [y](a\x00b.md) [z](sub/a\x00b/../c.md) ![[x/a\x00b.png]]")
        with patch.object(lint_mod, "_POSIX", False):
            _, findings = lint(self.vault, today=self.today)
        codes = {(f.path, f.code) for f in findings}
        self.assertIn(("notes/nul.md", "wikilink.missing"), codes)
        self.assertIn(("notes/nul.md", "markdown_link.missing"), codes)

    def test_containment_treats_a_path_the_os_rejects_as_outside(self) -> None:
        root = self.vault.resolve()
        with patch.object(lint_mod, "_real", side_effect=ValueError("embedded null character in path")):
            self.assertFalse(lint_mod._within(root, root / "notes" / "x.md"))

    def test_a_link_to_another_share_or_drive_never_reaches_the_file_system(self) -> None:
        # `[x](\\host\share\x)` is a UNC path on Windows. Resolving it asks the
        # network for `host` (seconds, and credentials offered to whoever
        # answers). POSIX has the same shape: `//host/share` is its own root.
        root = self.vault.resolve()
        with patch.object(lint_mod, "_real", side_effect=AssertionError("file system touched")):
            self.assertFalse(lint_mod._within(root, Path("//host/share/x.md")))
        touched: list[str] = []
        real_is_file, real_exists, real_uncached = Path.is_file, Path.exists, lint_mod._real_uncached

        def record(original):  # type: ignore[no-untyped-def]
            def probe(path, *args, **kwargs):  # type: ignore[no-untyped-def]
                touched.append(str(path))
                return original(path, *args, **kwargs)
            return probe

        self.note("notes/unc.md", "![[//host/share/x.png]] ![[//host/share/y]]")
        with patch.object(Path, "is_file", record(real_is_file)), patch.object(Path, "exists", record(real_exists)), \
                patch.object(lint_mod, "_real_uncached", record(real_uncached)):
            _, findings = lint(self.vault, today=self.today)
        self.assertEqual([path for path in touched if path.startswith("//")], [])
        self.assertIn(("notes/unc.md", "embed.missing"), {(f.path, f.code) for f in findings})

    def test_windows_anchors_are_compared_without_the_file_system(self) -> None:
        root = PureWindowsPath("D:/a/vault")
        for path, foreign in (
            (PureWindowsPath("\\\\host\\share\\x.md"), True),
            (PureWindowsPath("//host/share/x.md"), True),
            (PureWindowsPath("C:/Windows/win.ini"), True),
            (root / "C:x", True),  # drive-relative on another drive
            (PureWindowsPath("d:/a/vault/notes/x.md"), False),  # drive letters ignore case
            (root / "notes" / "x.md", False),
        ):
            with self.subTest(path=str(path)):
                self.assertEqual(lint_mod._foreign_anchor(root, path), foreign)

    def test_names_windows_cannot_store_are_rejected_from_the_string(self) -> None:
        for name in ("a<b", "a>b", 'a"b', "a|b", "a?b", "a*b", "a:b", "a\tb", "CON", "nul.md", "Com1 .txt", "lpt9"):
            with self.subTest(name=name):
                self.assertTrue(lint_mod._impossible_name(name, windows=True))
                self.assertFalse(lint_mod._impossible_name(name, windows=False))
        for name in ("console.md", "a\u202eb", "com0", "\ud800", "notes"):
            with self.subTest(name=name):
                self.assertFalse(lint_mod._impossible_name(name, windows=True))
        self.assertTrue(lint_mod._impossible_name("a\x00b", windows=False))
        path = PureWindowsPath("D:/v/notes/a<b/c.md")
        self.assertEqual(lint_mod._first_impossible_part(path, windows=True), 3)
        self.assertIsNone(lint_mod._first_impossible_part(PureWindowsPath("C:/v/x.md"), windows=True))


class TargetTests(VaultCase):
    def test_a_target_naming_an_unknown_home_directory_is_just_missing(self) -> None:
        from whykit.backlinks import build_backlinks
        from whykit.context import build_context
        from whykit.impact import analyze_impact

        for target in ("~no-such-user-whykit/x", "~no-such-user-whykit", "/" + "a" * 300 + ".md"):
            with self.subTest(target=target):
                self.assertEqual(build_backlinks(self.vault, target)["backlinks"], [])
                self.assertFalse(build_context(self.vault, target)["exists"])
                analyze_impact(self.vault, target)


class ConfusableIdentifierTests(VaultCase):
    def test_identifiers_are_ascii_digits_only(self) -> None:
        from whykit.evidence import list_evidence

        fullwidth = "E-\uff10\uff10\uff11"
        note = load_note(Path("n.md"), text=f"Cites {fullwidth} and D-\uff10\uff10\uff12.")
        self.assertEqual(note.cited_evidence, ())
        register = self.vault / "00-context" / "evidence-register.md"
        register.write_text(register.read_text(encoding="utf-8").replace(
            "## Retired sources",
            f"| {fullwidth} | Look-alike | report | 2026-09-01 | 2026-09-01 | https://example.com/x | c |\n\n## Retired sources",
        ), encoding="utf-8")
        self.assertNotIn(fullwidth, [item["id"] for item in list_evidence(self.vault)["evidence"]])


class InvalidUtf8Tests(VaultCase):
    def test_a_file_that_is_not_utf8_is_a_finding_not_a_crash(self) -> None:
        from whykit.evidence import list_evidence
        from whykit.graph import build_graph
        from whykit.status import build_status
        from whykit.trace import build_trace

        (self.vault / "notes").mkdir(exist_ok=True)
        (self.vault / "notes" / "bad.md").write_bytes(NOTE_HEAD.encode("utf-8") + b"broken \xed\xa0\x80 bytes\n")
        register = self.vault / "00-context" / "evidence-register.md"
        register.write_bytes(register.read_bytes() + b"\n| E-009 | s\xed | x | y | z | https://example.com/a | c |\n")
        _, findings = lint(self.vault, today=self.today)
        flagged = {f.path for f in findings if f.code == "frontmatter.invalid" and "UTF-8" in f.message}
        self.assertEqual(flagged, {"notes/bad.md", "00-context/evidence-register.md"})
        build_status(self.vault, today=self.today)
        build_graph(self.vault)
        build_trace(self.vault, today=self.today)
        list_evidence(self.vault)


class ParserScalingTests(unittest.TestCase):
    def test_pathological_repetition_stays_near_linear(self) -> None:
        failures = fuzz_parsers.run_scaling(size=20_000)
        self.assertEqual([str(failure) for failure in failures], [])

    def test_link_patterns_still_read_ordinary_links(self) -> None:
        note = load_note(Path("n.md"), text="[[a/b#Head|Alias]] and [text](c.md 'title') and ![i](<d e.png>)")
        from whykit.lint import MARKDOWN_LINK_RE, WIKILINK_RE

        self.assertEqual([m.group(1) for m in WIKILINK_RE.finditer(note.masked)], ["a/b"])
        self.assertEqual([m.group(2) for m in MARKDOWN_LINK_RE.finditer(note.masked)], ["c.md 'title'", "<d e.png>"])

    def test_table_rows_keep_their_cells(self) -> None:
        self.assertEqual(_split_table_row("| [[a|b]] | c [[ | d ]] e | f |"), ["[[a|b]]", "c [[ | d ]] e", "f"])
        self.assertEqual(_split_table_row("| a [[ | b |"), ["a [[", "b"])


class FrontMatterDepthTests(unittest.TestCase):
    def test_nested_inline_lists_are_rejected_without_recursion(self) -> None:
        for depth in (2, 50, 5000):
            raw = "tags: " + "[" * depth + "a" + "]" * depth
            with self.subTest(depth=depth):
                started = time.perf_counter()
                with self.assertRaisesRegex(ValueError, "nested"):
                    _parse_front_matter(raw)
                self.assertLess(time.perf_counter() - started, 1.0)
        note = load_note(Path("n.md"), text="---\ntags: " + "[" * 5000 + "]" * 5000 + "\n---\n")
        self.assertIn("nested", note.front_error or "")

    def test_flat_inline_lists_still_parse(self) -> None:
        self.assertEqual(_parse_front_matter("tags: [a, 'b, c', \"[d]\"]"), {"tags": ["a", "b, c", "[d]"]})


class PolicyPatternTests(unittest.TestCase):
    def test_shapes_that_hide_from_a_token_scan_are_rejected(self) -> None:
        cases = {
            r"(?x)(a + ) + $": "nested",          # verbose mode moves the quantifier
            r"(?:a(?#c)+)+$": "nested",           # a comment between atom and quantifier
            r"(a?){25}a{25}": "nested",           # an optional body repeated a fixed number of times
            r"a*a*a*a*b": "overlapping",          # polynomial: adjacent quantifiers that match the same text
            r"\s*\s*x": "overlapping",
            r".{2,}\s{30}": "overlapping",
            r"\s+\S*\s+$": "overlapping",
            r"[ab]+b*a*$": "overlapping",
        }
        for pattern, expected in cases.items():
            with self.subTest(pattern=pattern):
                self.assertIn(expected, regex_problem(pattern) or "accepted")

    def test_ordinary_patterns_stay_accepted(self) -> None:
        for pattern in (r"\bTBD\b", r"(?i)^owner sign-off:", r"[A-Z]{2,4}-\d{3}", r"(?:foo|bar) baz",
                        r"(ab)?c+", r"https?://[^ ]+", r"(?P<id>E-\d{3,})", r"a{2}(bc){3}",
                        r"^status:\s*approved\s*$", r"\w+@\w+\.\w+", r"^## Decision\b", r"\[\[[^\]]+\]\]",
                        r"(?i)confidential", r"D-\d{3,}", r"^\s*- \[ \]", r"\d+-\d+", r"TODO.*"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(regex_problem(pattern))

    def test_pattern_checks_stop_at_the_time_budget_and_say_so(self) -> None:
        policy = build_policy({"custom": [{"id": "custom.slow", "summary": "s", "forbidden_patterns": [r"\w+@"]}]})
        text = NOTE_HEAD + "\n".join("a" * 9_000 for _ in range(400))
        note = load_note(Path("notes/slow.md"), text=text)
        findings: list = []
        started = time.perf_counter()
        with patch.object(rule_policy, "PATTERN_BUDGET_SECONDS", 0.2):
            check_custom_rules(policy, [note], findings, self.today(), lambda path: path.as_posix())
        self.assertLess(time.perf_counter() - started, 5.0)
        self.assertTrue(any("time budget" in f.message for f in findings), [f.message for f in findings])

    @staticmethod
    def today():
        import datetime as dt

        return dt.date(2026, 10, 3)


class TerminalAndWorkflowOutputTests(VaultCase):
    """Text output must not let vault content start a new log line."""

    def test_lint_text_output_keeps_each_finding_on_one_line(self) -> None:
        self.note("notes/inject.md", "[[nope\n::warning::WIKI]] [[x\u2028::error::LS]] \x1b[31mred")
        evil = self.vault / "notes" / "a\n::error::FNAME.md"
        try:
            evil.write_text(NOTE_HEAD, encoding="utf-8")
        except OSError:
            pass
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            lint_main(["--root", str(self.vault), "--today", self.today.isoformat()])
        out = buffer.getvalue()
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)
        self.assertNotIn("\u2028", out)
        self.assertNotIn("\x1b", out)

    def test_check_text_summary_cannot_forge_workflow_commands(self) -> None:
        import subprocess

        self.note("notes/inject.md", "[[nope\n::warning::WIKI]] [[x\u2028::error::LS]]")
        evil = self.vault / "notes" / "a\n::error::FNAME.md"
        try:
            evil.write_text(NOTE_HEAD, encoding="utf-8")
        except OSError:
            pass
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "whykit.py"), "check", "--root", str(self.vault),
             "--profile", "local", "--format", "github", "--today", self.today.isoformat()],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        self.assertIn("WhyKit local gate", result.stdout)
        for line in (result.stdout + result.stderr).splitlines():
            for forged in ("::warning::WIKI", "::error::LS", "::error::FNAME"):
                self.assertFalse(line.lstrip().startswith(forged), line)


class PullRequestMarkdownTests(unittest.TestCase):
    def test_urls_in_vault_text_do_not_autolink(self) -> None:
        from whykit.diff import _md

        for text in ("see https://phish.example/login", "www.phish.example", "mailto:a@b.example"):
            with self.subTest(text=text):
                rendered = _md(text)
                self.assertNotIn("://", rendered)
                self.assertNotIn("www.", rendered)
                self.assertNotIn("mailto:", rendered)

    def test_the_comment_marker_cannot_be_closed_early(self) -> None:
        from whykit.diff import marker

        for prefix in ("v--->", "a---->x", "-->", "<!--"):
            with self.subTest(prefix=prefix):
                line = marker({"prefix": prefix})
                self.assertEqual(line.count("-->"), 1, line)
                self.assertTrue(line.endswith("-->"))


class FuzzSmokeTests(unittest.TestCase):
    """A short fixed-seed round of every target; the full run is `python tests/fuzz_parsers.py`."""

    def test_fixed_seed_round_finds_nothing(self) -> None:
        failures = fuzz_parsers.run_all(seed=fuzz_parsers.DEFAULT_SEED, cases=60, scaling=False)
        self.assertEqual([str(failure) for failure in failures], [])


if __name__ == "__main__":
    unittest.main()
