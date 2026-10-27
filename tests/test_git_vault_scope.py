"""Git's line protocol must preserve exact vault scope, including whitespace."""
from __future__ import annotations

import contextlib
import json
import os
import errno
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_history_gate import APPROVED
from whykit.ci_formats import github_annotations, source_root
from whykit.immutability import _git_prefix, changed_records
from whykit.diff import build_diff


def write_note(root: Path, path: str, body: str = "", **metadata) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    front = {"title": path, "sensitivity": "public", **metadata}
    target.write_text("---\n" + "\n".join(f"{key}: {json.dumps(value)}" for key, value in front.items())
                      + "\n---\n" + body, encoding="utf-8")
    return target


def write_register(root: Path) -> Path:
    return write_note(root, "00-context/evidence-register.md", """
| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-001 | Synthetic source | report | 2026-10-01 | 2026-10-01 | https://example.com/report | Synthetic claim |
""")


class GitVaultScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.repo = self.parent / "repository"
        self.repo.mkdir()
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=Pat Example", "-c", "user.email=pat@example.com", *args],
            cwd=self.repo, check=True, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        ).stdout.removesuffix("\n")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def vault(self, name: str, title: str) -> Path:
        root = self.repo / name
        write_note(root, "Home.md")
        write_register(root)
        write_note(root, "decision.md", decision_id="D-001", title=title, valid_from="2026-01-01")
        return root

    @unittest.skipIf(os.name == "nt", "generated POSIX hook execution requires a POSIX shell")
    def test_installed_hook_runs_in_the_selected_vault(self) -> None:
        import io
        from whykit.cli import main

        stub = self.parent / "bin"
        stub.mkdir()
        executable = stub / "whykit"
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" > "$WHYKIT_HOOK_PROBE"\n', encoding="utf-8")
        executable.chmod(0o755)
        probe = self.parent / "hook-pwd.txt"
        for name in (" kb", "nested/ space /資料", "\tleading", "back\\slash"):
            with self.subTest(name=name):
                root = self.vault(name, "Selected hook vault")
                if name.strip() != name:
                    self.vault(name.strip(), "Other hook vault")
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = main(["install-hooks", "--root", str(root), "--force"])
                self.assertEqual(code, 0)
                hook = self.repo / ".git/hooks/pre-commit"
                subprocess.run(["sh", str(hook)], cwd=self.repo, check=True, capture_output=True,
                               env={**os.environ, "PATH": str(stub) + os.pathsep + os.environ["PATH"],
                                    "WHYKIT_HOOK_PROBE": str(probe)})
                self.assertEqual(Path(probe.read_text(encoding="utf-8").removesuffix("\n")), root)

    def test_diff_never_selects_a_sibling_scope(self) -> None:
        selected = self.vault(" kb", "SELECTED_VAULT")
        self.vault("kb", "OTHER_VAULT_CANARY")
        base = self.commit("synthetic whitespace namespace")
        write_note(selected, "new.md", decision_id="D-002", title="Selected new record")
        head = self.commit("synthetic selected-vault change")
        report = build_diff(selected, base, head)
        self.assertEqual(report["prefix"], " kb/")
        self.assertEqual([item["id"] for item in report["decisions"]["added"]], ["D-002"])
        self.assertEqual(_git_prefix(str(selected)), " kb/")

    @unittest.skipIf(os.name == "nt", "Windows forbids newline paths")
    def test_ambiguous_hook_path_is_refused_before_writes(self) -> None:
        import io
        from whykit.cli import main

        root = self.vault("selected", "Selected")
        self.git("config", "core.hooksPath", "bad\nhooks")
        before = {str(path): path.read_bytes() for path in self.repo.rglob("*") if path.is_file()}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["install-hooks", "--root", str(root)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Git hook location could not be verified", stderr.getvalue())
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.repo.rglob("*") if path.is_file()})

    def test_history_gate_cannot_compare_unchanged_sibling_instead_of_rewritten_record(self) -> None:
        selected = self.vault(" kb", "Selected")
        sibling = self.vault("kb", "Other")
        relative = "06-decisions/d-001-use-git.md"
        for root in (selected, sibling):
            record = root / relative
            record.parent.mkdir()
            record.write_text(APPROVED, encoding="utf-8")
        base = self.commit("synthetic accepted history")
        record = selected / relative
        record.write_text(record.read_text(encoding="utf-8") + "\nRewritten accepted reasoning.\n", encoding="utf-8")
        head = self.commit("synthetic rewrite under leading-space scope")
        self.assertEqual(changed_records(base, head, str(selected)), [("M", relative)])

    def test_prefix_preserves_spaces_tabs_nested_and_unicode_directories(self) -> None:
        names = [" leading", "nested/ space /żółw-資料", "quote\"name"]
        if os.name != "nt":
            names += ["trailing ", "\tleading", "trailing\t", "back\\slash"]
        for name in names:
            with self.subTest(name=name):
                root = self.repo / name
                root.mkdir(parents=True)
                self.assertEqual(_git_prefix(str(root)), name + "/")
                with contextlib.chdir(root):
                    self.assertEqual(_git_prefix(None), name + "/")

    @unittest.skipIf(os.name == "nt", "Windows does not preserve trailing directory whitespace")
    def test_repository_root_trailing_whitespace_is_not_stripped(self) -> None:
        renamed = self.parent / "repository \t"
        self.repo.rename(renamed)
        self.repo = renamed
        self.assertEqual(_git_prefix(str(renamed)), "")
        root = renamed / "nested"
        root.mkdir()
        self.assertEqual(_git_prefix(str(root)), "nested/")

    def test_ci_locations_and_annotations_keep_the_same_exact_scope(self) -> None:
        root = self.repo / " leading"
        root.mkdir()
        self.assertEqual(source_root(root), (self.repo, " leading/"))
        findings = [{"level": "error", "path": "Home.md", "line": 1,
                     "code": "frontmatter.missing", "message": "Synthetic finding"}]
        self.assertIn("file= leading/Home.md,", github_annotations(findings, root)[0])
        if os.name != "nt":
            renamed = self.parent / "repository \t"
            self.repo.rename(renamed)
            self.repo = renamed
            self.assertEqual(source_root(renamed / " leading"), (renamed, " leading/"))
        self.assertEqual(source_root(self.parent), (self.parent, ""))

    def test_incorrect_quoted_or_ambiguous_git_location_fails_closed(self) -> None:
        root = self.repo / " intended"
        root.mkdir()
        (self.repo / "intended").mkdir()
        for prefix, top in (("intended/\n", str(self.repo) + "\n"),
                            (" intended/", str(self.repo) + "\n"),
                            (" intended/\n", '"' + str(self.repo) + '"\n'),
                            (" intended/\n", str(self.repo) + "\nextra\n"),
                            ("../ intended/\n", str(self.repo) + "\n")):
            with self.subTest(prefix=prefix, top=top), patch("whykit.immutability.git", side_effect=[prefix, top]):
                with self.assertRaises(subprocess.CalledProcessError):
                    _git_prefix(str(root))

    def test_git_prefix_failure_never_falls_back_to_repository_root(self) -> None:
        failure = subprocess.CalledProcessError(128, ["git", "rev-parse", "--show-prefix"])
        with patch("whykit.immutability.git", side_effect=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                _git_prefix(str(self.repo))

    @unittest.skipIf(os.name == "nt", "Windows forbids newline filenames")
    def test_real_newline_scope_is_refused_without_selecting_another_directory(self) -> None:
        root = self.repo / "line\nvault"
        root.mkdir()
        with self.assertRaises(subprocess.CalledProcessError):
            _git_prefix(str(root))

    @unittest.skipIf(os.name == "nt", "Windows forbids newline filenames")
    def test_ambiguous_git_scope_never_falls_back_to_an_unprefixed_annotation(self) -> None:
        root = self.repo / "line\nvault"
        root.mkdir()
        (self.repo / "Home.md").write_text("Unrelated sibling", encoding="utf-8")
        (root / "Home.md").write_text("Selected vault", encoding="utf-8")
        findings = [{"level": "error", "path": "Home.md", "line": 1,
                     "code": "frontmatter.missing", "message": "Synthetic finding"}]
        with self.assertRaises(OSError) as refused:
            source_root(root)
        self.assertEqual(refused.exception.errno, errno.EINVAL)
        with self.assertRaises(OSError):
            github_annotations(findings, root)

    @unittest.skipIf(os.name == "nt", "Windows forbids newline filenames")
    def test_cli_formatters_refuse_ambiguous_scope_without_partial_annotations(self) -> None:
        root = self.vault("line\nvault", "Selected vault")
        (self.repo / "Home.md").write_text("Unrelated sibling", encoding="utf-8")
        for arguments in (("lint", "--format", "github"), ("lint", "--format", "sarif"),
                          ("check", "--format", "github"), ("install-hooks",)):
            with self.subTest(arguments=arguments):
                child = subprocess.run(
                    [sys.executable, "-m", "whykit.cli", *arguments, "--root", str(root)],
                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                    env={**os.environ, "WHYKIT_NO_CACHE": "1"},
                )
                self.assertEqual(child.returncode, 2, child.stderr)
                self.assertEqual(child.stdout, "")
                self.assertIn("Git vault scope could not be verified", child.stderr)
                self.assertNotIn("Traceback", child.stderr)
        self.assertFalse((self.repo / ".git/hooks/pre-commit").exists())


if __name__ == "__main__":
    unittest.main()
