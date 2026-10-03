"""Cross-platform robustness: console encodings, line endings, file names, permissions.

Each test reproduces a condition a Windows, macOS or minimal-container user
meets without asking for it: an ASCII or legacy code-page console, a vault
checked out with CRLF line endings, a file name in NFD, a link typed in the
wrong case, a read-only file, a decision record whose name is not ASCII.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
TINY = ROOT / "examples" / "tiny"
NORTHLINE = ROOT / "examples" / "northline"
TODAY = "2026-09-17"

sys.path.insert(0, str(ROOT / "src"))

from whykit.console import emit_machine, harden_stream  # noqa: E402
from whykit.io import atomic_write_text, ensure_writable, match_line_endings  # noqa: E402
from whykit.lint import Finding, Note, check_path_collisions  # noqa: E402

# The interpreter's own defaults, not the test runner's: a developer who set
# PYTHONUTF8=1 globally would otherwise never see the ASCII code path.
ASCII_CONSOLE = {"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0", "LC_ALL": "C", "LANG": "C"}
CP1252_CONSOLE = {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
UTF8_CONSOLE = {"PYTHONIOENCODING": "utf-8"}


def run(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    merged = {key: value for key, value in os.environ.items() if key not in {"PYTHONIOENCODING", "PYTHONUTF8", "LC_ALL"}}
    merged.update(env or {})
    return subprocess.run(
        [sys.executable, str(CLI), *args], capture_output=True, env=merged, cwd=cwd, timeout=120,
    )


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout


def to_crlf(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".toml"}:
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))


def line_endings(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    crlf = data.count(b"\r\n")
    return crlf, data.count(b"\n") - crlf


class VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="whykit-portability-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        def unlock(func, path, _exc):  # read-only files block rmtree on Windows
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
        if sys.version_info >= (3, 12):
            shutil.rmtree(self.tmp, onexc=unlock)
        else:  # pragma: no cover - Python 3.11
            shutil.rmtree(self.tmp, onerror=unlock)

    def vault(self, name: str = "vault", source: Path = TINY) -> Path:
        target = self.tmp / name
        shutil.copytree(source, target)
        return target

    def append(self, path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)


class ConsoleEncodingTests(VaultCase):
    """Human output degrades on a console that cannot encode it; JSON never does."""

    def broken_vault(self) -> Path:
        vault = self.vault()
        # A non-ASCII target makes the finding message itself non-ASCII.
        self.append(vault / "Home.md", "\n- [[Łódź → café]]\n")
        return vault

    def test_human_lint_report_survives_an_ascii_console(self) -> None:
        vault = self.broken_vault()
        result = run("lint", "--root", str(vault), "--today", TODAY, env=ASCII_CONSOLE)
        self.assertEqual(result.returncode, 1, result.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", result.stderr)
        out = result.stdout.decode("ascii")  # every byte is ASCII
        self.assertIn("files - 1 error(s)", out)
        # Known glyphs become ASCII; anything else stays unambiguous.
        self.assertIn("[[\\u0141\\xf3d\\u017a -> caf\\xe9]]", out)

    def test_clean_vault_summary_survives_an_ascii_console(self) -> None:
        result = run("lint", "--root", str(NORTHLINE), "--today", TODAY, env=ASCII_CONSOLE)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertTrue(result.stdout.decode("ascii").rstrip().endswith("files - clean"))

    def test_human_output_survives_a_windows_code_page(self) -> None:
        vault = self.broken_vault()
        result = run("lint", "--root", str(vault), "--today", TODAY, env=CP1252_CONSOLE)
        self.assertEqual(result.returncode, 1, result.stderr.decode("cp1252", "replace"))
        out = result.stdout.decode("cp1252")
        # cp1252 has an em dash and é, so those stay; Ł and → do not.
        self.assertIn("files — 1 error(s)", out)
        self.assertIn("[[\\u0141\xf3d\\u017a -> caf\xe9]]", out)

    @unittest.skipIf(os.name == "nt", "the C locale is a POSIX concept")
    def test_c_locale_without_an_explicit_encoding(self) -> None:
        vault = self.broken_vault()
        env = {"PYTHONUTF8": "0", "LC_ALL": "C", "LANG": "C"}
        result = run("lint", "--root", str(vault), "--today", TODAY, env=env)
        self.assertEqual(result.returncode, 1, result.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", result.stderr)

    def test_errors_on_stderr_survive_an_ascii_console(self) -> None:
        missing = self.tmp / "nowhere — here"
        result = run("lint", "--root", str(missing), env=ASCII_CONSOLE)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"Traceback", result.stderr)
        result.stderr.decode("ascii")

    def test_json_bytes_do_not_depend_on_the_console(self) -> None:
        vault = self.broken_vault()
        commands = (
            ("lint", "--root", str(vault), "--today", TODAY, "--json"),
            ("query", "HubSpot", "--root", str(NORTHLINE), "--json"),
            ("graph", "--root", str(vault), "--format", "json"),
            ("graph", "--root", str(vault), "--format", "mermaid"),
            ("snapshot", "--root", str(vault), "--today", TODAY),
            ("rules", "--json"),
        )
        for command in commands:
            with self.subTest(command=command[:2]):
                expected = run(*command, env=UTF8_CONSOLE)
                self.assertIn(expected.returncode, (0, 1), expected.stderr)
                for env in (ASCII_CONSOLE, CP1252_CONSOLE):
                    actual = run(*command, env=env)
                    self.assertEqual(actual.returncode, expected.returncode, actual.stderr)
                    self.assertEqual(actual.stdout, expected.stdout)
        report = json.loads(run(*commands[0], env=ASCII_CONSOLE).stdout.decode("utf-8"))
        self.assertIn("[[Łódź → café]]", report["findings"][0]["message"])

    def test_json_error_objects_are_utf8_on_any_console(self) -> None:
        # Error objects go through the same UTF-8 path as reports: a message
        # naming a non-ASCII path must reach a cp1252 or ASCII console intact.
        missing = self.tmp / "Łódź → vault"
        commands = (
            ("status", "--root", str(missing), "--json"),
            ("explorer-index", "--root", str(missing)),
            ("rules", "no.such_rule", "--json"),
        )
        for command in commands:
            with self.subTest(command=command[0]):
                expected = run(*command, env=UTF8_CONSOLE)
                for env in (ASCII_CONSOLE, CP1252_CONSOLE):
                    actual = run(*command, env=env)
                    self.assertEqual(actual.returncode, expected.returncode)
                    self.assertNotIn(b"Traceback", actual.stderr)
                    self.assertEqual(actual.stdout, expected.stdout)
                    payload = json.loads(actual.stdout.decode("utf-8"))
                    self.assertIn("code", payload["error"])
        payload = json.loads(run(*commands[0], env=CP1252_CONSOLE).stdout.decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "vault_not_found")
        self.assertIn("Łódź → vault", payload["error"]["message"])

    def test_harden_stream_replaces_instead_of_raising(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
        harden_stream(stream)
        stream.write("12 files — clean … → Ł\U0001f600\n")
        stream.flush()
        self.assertEqual(raw.getvalue(), b"12 files - clean ... -> \\u0141\\U0001f600\n")

    def test_harden_stream_keeps_a_users_explicit_choice(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="replace")
        harden_stream(stream)
        self.assertEqual(stream.errors, "replace")
        utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
        harden_stream(utf8)
        self.assertEqual(utf8.errors, "strict")

    def test_harden_stream_keeps_undecodable_file_names_byte_exact(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="surrogateescape")
        harden_stream(stream)
        name = b"caf\xe9.md".decode("ascii", "surrogateescape")
        stream.write(f"{name} — ok\n")
        stream.flush()
        self.assertEqual(raw.getvalue(), b"caf\xe9.md - ok\n")

    def test_emit_machine_writes_utf8_and_restores_the_stream(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict", newline="\n")
        stream.write("before\n")
        emit_machine(json.dumps({"title": "Łódź — café"}, ensure_ascii=False), file=stream)
        stream.write("after\n")
        stream.flush()
        self.assertEqual(raw.getvalue(), 'before\n{"title": "Łódź — café"}\nafter\n'.encode())
        self.assertEqual((stream.encoding, stream.errors), ("ascii", "strict"))


class LineEndingTests(VaultCase):
    """A Windows checkout (core.autocrlf=true) has CRLF in every Markdown file."""

    READ_COMMANDS = (
        ("lint", "--today", TODAY, "--json"),
        ("status", "--today", TODAY, "--json"),
        ("trace", "--today", TODAY, "--json"),
        ("query", "minimal", "--json"),
        ("graph", "--format", "json"),
        ("backlinks", "D-001", "--json"),
        ("impact", "D-001", "--json"),
        ("evidence", "list", "--json"),
        ("check", "--profile", "ci", "--today", TODAY, "--json"),
    )

    def normalized(self, root: Path, command: tuple[str, ...]) -> str:
        result = run(command[0], "--root", str(root), *command[1:], env=UTF8_CONSOLE)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        text = result.stdout.decode("utf-8")
        spellings = {str(root), str(root.resolve()), json.dumps(str(root.resolve()))[1:-1], json.dumps(str(root))[1:-1]}
        for spelling in sorted(spellings, key=len, reverse=True):
            text = text.replace(spelling, "<root>")
        return text

    def test_read_commands_agree_on_lf_and_crlf_checkouts(self) -> None:
        lf = self.vault("lf")
        crlf = self.vault("crlf")
        to_crlf(crlf)
        for command in self.READ_COMMANDS:
            with self.subTest(command=command[:2]):
                self.assertEqual(self.normalized(crlf, command), self.normalized(lf, command))

    def test_mutations_keep_crlf_files_crlf(self) -> None:
        vault = self.vault()
        to_crlf(vault)
        steps = (
            ("new", "--root", str(vault), "decision", "Pick a café", "--source", "E-001"),
            ("review", "--root", str(vault), "record", "D-001", "--reviewer", "A Person", "--today", TODAY),
            ("new", "--root", str(vault), "evidence", "--source", "Survey", "--location", "https://example.com/s",
             "--type", "web", "--claims", "a claim"),
            ("evidence", "--root", str(vault), "retire", "E-003", "--why", "withdrawn"),
            ("new", "--root", str(vault), "note", "Une note", "--workstream", "notes", "--link-from", "Home.md"),
        )
        for step in steps:
            result = run(*step, env=UTF8_CONSOLE)
            self.assertEqual(result.returncode, 0, (step, result.stderr))
        for relative in (
            "Home.md",
            "00-context/evidence-register.md",
            "00-context/review-log.md",
            "06-decisions/decision-log.md",
            "06-decisions/d-001-start-with-a-minimal-whykit-vault.md",
        ):
            with self.subTest(file=relative):
                crlf, lf = line_endings(vault / relative)
                self.assertGreater(crlf, 0)
                self.assertEqual(lf, 0, "a rewrite mixed or converted line endings")
        lint = run("lint", "--root", str(vault), "--today", TODAY, env=UTF8_CONSOLE)
        self.assertEqual(lint.returncode, 0, lint.stdout.decode("utf-8"))

    def test_match_line_endings_follows_the_existing_file(self) -> None:
        crlf = self.tmp / "crlf.md"
        crlf.write_bytes(b"a\r\nb\r\n")
        lf = self.tmp / "lf.md"
        lf.write_bytes(b"a\nb\r\n")
        self.assertEqual(match_line_endings(crlf, "x\ny\r\n"), "x\r\ny\r\n")
        self.assertEqual(match_line_endings(lf, "x\ny\n"), "x\ny\n")
        self.assertEqual(match_line_endings(self.tmp / "new.md", "x\n"), "x\n")


class FileNameTests(VaultCase):
    def test_vault_path_with_spaces_and_non_ascii_characters(self) -> None:
        vault = self.vault("my vault — Łódź 日本")
        for env in (UTF8_CONSOLE, ASCII_CONSOLE):
            result = run("lint", "--root", str(vault), "--today", TODAY, "--strict", env=env)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        created = run("new", "--root", str(vault), "decision", "Zażółć gęślą jaźń", env=ASCII_CONSOLE)
        self.assertEqual(created.returncode, 0, created.stderr.decode("utf-8", "replace"))
        self.assertEqual(run("lint", "--root", str(vault), "--today", TODAY).returncode, 0)

    def test_init_into_a_non_ascii_path_lints_without_errors(self) -> None:
        target = self.tmp / "nowy skarbiec — ąę"
        created = run("init", str(target), env=ASCII_CONSOLE)
        self.assertEqual(created.returncode, 0, created.stderr.decode("utf-8", "replace"))
        report = json.loads(run("lint", "--root", str(target), "--json").stdout.decode("utf-8"))
        self.assertEqual(report["errors"], 0)

    def test_long_vault_path(self) -> None:
        deep = self.tmp
        for index in range(10):
            deep = deep / f"segment-{index:02d}-{'x' * 20}"
        try:
            vault = self.vault(str(deep.relative_to(self.tmp) / "vault"))
        except OSError as exc:  # pragma: no cover - Windows without long paths
            self.skipTest(f"filesystem refuses long paths: {exc}")
        self.assertGreater(len(str(vault)), 260)
        result = run("lint", "--root", str(vault), "--today", TODAY, "--strict")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_nfd_file_name_resolves_an_nfc_link(self) -> None:
        vault = self.vault()
        nfd = unicodedata.normalize("NFD", "zürich-café")
        source = (vault / "notes" / "README.md").read_text(encoding="utf-8")
        (vault / "notes" / f"{nfd}.md").write_text(source.replace("title: Notes", "title: Zurich"), encoding="utf-8")
        nfc = unicodedata.normalize("NFC", "zürich-café")
        self.append(vault / "Home.md", f"\n- [[{nfc}]]\n- [[notes/{nfc}]]\n")
        report = json.loads(run("lint", "--root", str(vault), "--today", TODAY, "--json").stdout.decode("utf-8"))
        self.assertEqual([f["code"] for f in report["findings"] if f["level"] == "error"], [])
        graph = json.loads(run("graph", "--root", str(vault), "--format", "json").stdout.decode("utf-8"))
        self.assertEqual(graph["unresolved"], [])

    def test_wrong_case_path_link_resolves_to_the_real_note_everywhere(self) -> None:
        vault = self.vault()
        self.append(vault / "Home.md", "\n- [[NOTES/readme]]\n- [[home]]\n")
        report = json.loads(run("lint", "--root", str(vault), "--today", TODAY, "--json").stdout.decode("utf-8"))
        self.assertEqual(report["errors"], 0, report["findings"])
        graph = json.loads(run("graph", "--root", str(vault), "--format", "json").stdout.decode("utf-8"))
        nodes = {node["id"] for node in graph["nodes"]}
        dangling = [edge for edge in graph["edges"] if edge["type"] == "wikilink" and edge["to"] not in nodes]
        # Before: macOS and Windows returned the typed spelling, a node that does not exist.
        self.assertEqual(dangling, [])
        self.assertIn({"from": "Home", "to": "notes/README", "type": "wikilink"}, graph["edges"])

    def test_paths_differing_only_by_case_or_normalization_are_reported(self) -> None:
        root = self.tmp.resolve()
        notes = [
            Note(path=root / "notes" / "Plan.md", text=""),
            Note(path=root / "notes" / "plan.md", text=""),
            Note(path=root / "notes" / unicodedata.normalize("NFC", "café.md"), text=""),
            Note(path=root / "notes" / unicodedata.normalize("NFD", "café.md"), text=""),
            Note(path=root / "other" / "plan.md", text=""),
        ]
        findings: list[Finding] = []
        check_path_collisions(root, notes, findings)
        self.assertEqual([f.code for f in findings], ["path.case_collision", "path.case_collision"])
        self.assertTrue(all(f.level == "warning" for f in findings))

    def test_case_collision_on_a_case_sensitive_filesystem(self) -> None:
        vault = self.vault()
        notes = vault / "notes"
        body = (notes / "README.md").read_text(encoding="utf-8")
        (notes / "plan.md").write_text(body, encoding="utf-8")
        (notes / "Plan.md").write_text(body.replace("title: Notes", "title: Plan"), encoding="utf-8")
        if len(list(notes.glob("*.md"))) < 3:
            self.skipTest("this filesystem folds case, so the collision cannot exist here")
        report = json.loads(run("lint", "--root", str(vault), "--today", TODAY, "--json").stdout.decode("utf-8"))
        self.assertIn("path.case_collision", {f["code"] for f in report["findings"]})

    def test_distinct_names_are_not_a_collision(self) -> None:
        report = json.loads(run("lint", "--root", str(NORTHLINE), "--today", TODAY, "--json").stdout.decode("utf-8"))
        self.assertNotIn("path.case_collision", {f["code"] for f in report["findings"]})

    @unittest.skipIf(os.name == "nt", "creating symlinks needs Developer Mode on Windows")
    def test_symlinked_vault_root(self) -> None:
        real = self.vault("real")
        link = self.tmp / "link"
        link.symlink_to(real, target_is_directory=True)
        for command in (("lint", "--today", TODAY, "--strict"), ("status", "--today", TODAY), ("graph",)):
            with self.subTest(command=command[0]):
                result = run(command[0], "--root", str(link), *command[1:])
                self.assertEqual(result.returncode, 0, result.stderr)
        created = run("new", "--root", str(link), "decision", "Through a link")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertTrue(list((real / "06-decisions").glob("d-002-*.md")))


class ReadOnlyFileTests(VaultCase):
    def make_read_only(self, path: Path) -> None:
        os.chmod(path, stat.S_IREAD)
        if os.name != "nt" and os.access(path, os.W_OK) and os.geteuid() == 0:  # pragma: no cover
            self.skipTest("root ignores file permissions")

    def test_read_only_index_stops_a_mutation_before_any_write(self) -> None:
        vault = self.vault()
        log = vault / "06-decisions" / "decision-log.md"
        before = log.read_bytes()
        self.make_read_only(log)
        result = run("new", "--root", str(vault), "decision", "Blocked", env=ASCII_CONSOLE)
        self.assertEqual(result.returncode, 2)
        err = result.stderr.decode("ascii")
        self.assertIn("read-only", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in (vault / "06-decisions").glob("d-*.md")),
                         ["d-001-start-with-a-minimal-whykit-vault.md"])
        self.assertEqual(list(vault.rglob("*.whykit-tmp-*")), [])
        self.assertFalse((vault / ".whykit" / "transactions").exists())
        # The vault is not wedged: once writable, the next mutation succeeds.
        os.chmod(log, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(run("new", "--root", str(vault), "decision", "Unblocked").returncode, 0)

    def test_read_only_link_source_stops_note_creation(self) -> None:
        vault = self.vault()
        self.make_read_only(vault / "Home.md")
        result = run("new", "--root", str(vault), "note", "Draft", "--workstream", "notes", "--link-from", "Home.md")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((vault / "notes" / "draft.md").exists())

    def test_atomic_write_refuses_a_read_only_target(self) -> None:
        target = self.tmp / "locked.md"
        target.write_text("keep\n", encoding="utf-8")
        self.make_read_only(target)
        with self.assertRaises(PermissionError):
            ensure_writable(target)
        with self.assertRaises(PermissionError):
            atomic_write_text(target, "replace\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(list(self.tmp.glob(".locked.md.whykit-tmp-*")), [])

    def test_read_only_files_do_not_affect_read_commands(self) -> None:
        vault = self.vault()
        for path in vault.rglob("*.md"):
            self.make_read_only(path)
        result = run("lint", "--root", str(vault), "--today", TODAY, "--strict")
        self.assertEqual(result.returncode, 0, result.stderr)


class EvidenceRetireTests(VaultCase):
    def test_retiring_without_a_replacement_lints_clean(self) -> None:
        vault = self.vault()
        added = run("new", "--root", str(vault), "evidence", "--source", "Survey", "--location",
                    "https://example.com/s", "--type", "web", "--claims", "a claim")
        self.assertEqual(added.returncode, 0, added.stderr)
        retired = run("evidence", "--root", str(vault), "retire", "E-003", "--why", "withdrawn")
        self.assertEqual(retired.returncode, 0, retired.stderr)
        report = json.loads(run("lint", "--root", str(vault), "--today", TODAY, "--json").stdout.decode("utf-8"))
        self.assertNotIn("evidence.replaced_by_format", {f["code"] for f in report["findings"]})


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitHistoryTests(VaultCase):
    def repo(self, *, autocrlf: bool) -> Path:
        vault = self.vault()
        git("init", "-q", cwd=vault)
        git("config", "core.autocrlf", "true" if autocrlf else "false", cwd=vault)
        git("add", "-A", cwd=vault)
        git("commit", "-qm", "base", cwd=vault)
        if autocrlf:
            # Check the files out again, as a Windows clone would: CRLF on
            # disk, LF in the repository, and a clean index.
            for path in vault.rglob("*"):
                if path.is_file() and ".git" not in path.relative_to(vault).parts:
                    path.unlink()
            git("checkout", "--", ".", cwd=vault)
            crlf, lf = line_endings(vault / "Home.md")
            if lf:  # pragma: no cover - a global gitattributes overriding eol
                self.skipTest("this Git configuration does not check out CRLF")
        return vault

    def test_rewrite_of_a_non_ascii_decision_name_is_blocked(self) -> None:
        vault = self.repo(autocrlf=False)
        old = "06-decisions/d-001-start-with-a-minimal-whykit-vault.md"
        new = "06-decisions/d-001-start-with-a-minimal-whykit-vault-café.md"
        git("mv", old, new, cwd=vault)
        git("commit", "-qm", "rename", cwd=vault)
        base = git("rev-parse", "HEAD", cwd=vault).strip()
        self.append(vault / new, "\nRewritten reasoning.\n")
        git("commit", "-qam", "rewrite", cwd=vault)
        for env in (UTF8_CONSOLE, ASCII_CONSOLE):
            result = run("history", "--base", base, "--head", "HEAD", "--root", str(vault), env=env)
            # Git used to print the name octal-quoted; `git show` then found
            # nothing and the rewrite passed as unchanged.
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("café".encode() if env is UTF8_CONSOLE else b"caf\\xe9", result.stderr)

    def test_autocrlf_checkout_passes_history_and_check(self) -> None:
        vault = self.repo(autocrlf=True)
        base = git("rev-parse", "HEAD", cwd=vault).strip()
        self.assertEqual(git("status", "--porcelain", cwd=vault).strip(), "")
        history = run("history", "--base", base, "--head", "HEAD", "--root", str(vault))
        self.assertEqual(history.returncode, 0, history.stdout)
        check = run("check", "--root", str(vault), "--profile", "release", "--base", base, "--today", TODAY, "--json")
        report = json.loads(check.stdout.decode("utf-8"))
        failed = {item["name"] for item in report["checks"] if not item["passed"]}
        self.assertNotIn("history", failed)

    def test_autocrlf_rewrite_is_still_blocked(self) -> None:
        vault = self.repo(autocrlf=True)
        base = git("rev-parse", "HEAD", cwd=vault).strip()
        record = vault / "06-decisions" / "d-001-start-with-a-minimal-whykit-vault.md"
        self.append(record, "Rewritten reasoning.\r\n")
        git("commit", "-qam", "rewrite", cwd=vault)
        result = run("history", "--base", base, "--head", "HEAD", "--root", str(vault))
        self.assertEqual(result.returncode, 1, result.stdout)


if __name__ == "__main__":
    unittest.main()
