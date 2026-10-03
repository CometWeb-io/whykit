"""Regressions for friction a team hits adopting WhyKit in an existing repository.

Each case comes from following the documentation on a realistic repository: the
vault in a subdirectory of a monorepo, titles in other scripts, output piped to
`head`, and an import from a working copy that will be committed.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _vaults import fresh_vault

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
EXAMPLE = ROOT / "examples" / "northline"

sys.path.insert(0, str(ROOT / "src"))

from whykit.scaffold import _slugify  # noqa: E402


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args], cwd=cwd, text=True, encoding="utf-8", errors="replace",
        capture_output=True, env={**os.environ, **(env or {})},
    )


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=check, text=True, encoding="utf-8", errors="replace", capture_output=True,
    )


@unittest.skipIf(os.name == "nt", "the installed hook is a POSIX shell script")
@unittest.skipUnless(shutil.which("git"), "git is required")
class HooksForAVaultInsideARepositoryTests(unittest.TestCase):
    """A vault is often `docs/decisions/` in a product repository, not its own repo."""

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = Path(td.name) / "product"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        self.vault = self.repo / "knowledge"
        fresh_vault(self.vault)
        (self.repo / "README.md").write_text("# Product\n", encoding="utf-8")
        git("add", ".", cwd=self.repo)
        git("commit", "-q", "-m", "init", cwd=self.repo)
        # The hook calls `whykit` on PATH; point it at this checkout.
        bin_dir = Path(td.name) / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "whykit"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{CLI}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    def test_install_hooks_finds_the_enclosing_repository(self) -> None:
        result = run("install-hooks", "--root", str(self.vault))
        self.assertEqual(result.returncode, 0, result.stderr)
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        self.assertTrue(hook.is_file(), result.stdout)

    def test_installed_hook_checks_the_vault_not_the_repository_root(self) -> None:
        self.assertEqual(run("install-hooks", "--root", str(self.vault)).returncode, 0)
        env = {**os.environ, **self.env}
        (self.repo / "app.txt").write_text("unrelated change\n", encoding="utf-8")
        git("add", "app.txt", cwd=self.repo)
        clean = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "app"],
            cwd=self.repo, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertNotIn("no WhyKit vault found", clean.stderr)

        broken = self.vault / "notes" / "broken.md"
        broken.write_text((self.vault / "Home.md").read_text(encoding="utf-8") + "\n[[No Such Note]]\n", encoding="utf-8")
        git("add", ".", cwd=self.repo)
        refused = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "broken"],
            cwd=self.repo, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("wikilink.missing", refused.stdout + refused.stderr)

    def test_install_hooks_in_a_linked_worktree(self) -> None:
        worktree = self.repo.parent / "product-wt"
        git("worktree", "add", "-q", str(worktree), cwd=self.repo)
        result = run("install-hooks", "--root", str(worktree / "knowledge"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / ".git" / "hooks" / "pre-commit").is_file())

    def test_doctor_sees_the_hook_install_hooks_wrote(self) -> None:
        self.assertEqual(run("install-hooks", "--root", str(self.vault)).returncode, 0)
        report = json.loads(run("doctor", "--root", str(self.vault), "--json").stdout)
        checks = {item["name"]: item for item in report["checks"]}
        self.assertTrue(checks["pre_commit_hook"]["passed"], checks["pre_commit_hook"])

    def test_outside_a_repository_the_error_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "loose"
            fresh_vault(vault)
            result = run("install-hooks", "--root", str(vault), env={"GIT_CEILING_DIRECTORIES": td})
            self.assertEqual(result.returncode, 2)
            self.assertIn("not a git repository", result.stderr)


class DoctorAgreesWithLintTests(unittest.TestCase):
    def test_doctor_counts_the_same_findings_as_lint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "ledger"
            fresh_vault(vault)
            self.assertEqual(run("new", "note", "Loose ends", "--workstream", "notes", "--root", str(vault)).returncode, 0)
            lint = json.loads(run("lint", "--root", str(vault), "--json").stdout)
            doctor = json.loads(run("doctor", "--root", str(vault), "--json").stdout)
            detail = {item["name"]: item for item in doctor["checks"]}["vault_lint"]["detail"]
            self.assertIn(f"{lint['errors']} errors, {lint['warnings']} warnings", detail)


class ClosedPipeTests(unittest.TestCase):
    """`whykit lint --json | head -1` must not end in a traceback or exit 120."""

    def test_small_output_to_a_closed_pipe_exits_cleanly(self) -> None:
        for argv in (
            ["status", "--today", "2026-09-17"],
            ["lint", "--format", "sarif", "--today", "2026-09-17"],
            ["lint", "--json", "--today", "2026-09-17"],
        ):
            with self.subTest(argv=argv):
                proc = subprocess.Popen(
                    [sys.executable, str(CLI), *argv, "--root", str(EXAMPLE)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                assert proc.stdout is not None and proc.stderr is not None
                proc.stdout.close()
                stderr = proc.stderr.read().decode("utf-8", "replace")
                proc.stderr.close()
                code = proc.wait(timeout=120)
                self.assertNotIn("Exception ignored", stderr)
                self.assertNotIn("BrokenPipeError", stderr)
                self.assertIn(code, (0, 1), stderr)


class TitlesInOtherScriptsTests(unittest.TestCase):
    def test_letters_without_a_decomposition_keep_their_base_letter(self) -> None:
        self.assertEqual(_slugify("Łódź łąka"), "lodz-laka")
        self.assertEqual(_slugify("Zażółć gęślą jaźń"), "zazolc-gesla-jazn")
        self.assertEqual(_slugify("Straße Øresund Æble"), "strasse-oresund-aeble")

    def test_titles_with_no_latin_letters_get_distinct_stable_slugs(self) -> None:
        first, second = _slugify("会议记录"), _slugify("数据库评估")
        self.assertNotEqual(first, second)
        self.assertEqual(first, _slugify("会议记录"))
        self.assertRegex(first, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    def test_two_notes_with_non_latin_titles_both_get_created(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "ledger"
            fresh_vault(vault)
            paths = []
            for title in ("会议记录", "Решение по базе данных"):
                result = run("new", "note", title, "--workstream", "notes", "--root", str(vault), "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                paths.append(json.loads(result.stdout)["path"])
            self.assertEqual(len(set(paths)), 2)

    def test_an_existing_note_is_named_with_a_way_forward(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "ledger"
            fresh_vault(vault)
            self.assertEqual(run("new", "note", "Retro", "--workstream", "notes", "--root", str(vault)).returncode, 0)
            again = run("new", "note", "Retro", "--workstream", "notes", "--root", str(vault))
            self.assertEqual(again.returncode, 2)
            self.assertIn("notes/retro.md already exists", again.stderr)
            self.assertIn("hint:", again.stderr)
            self.assertNotIn(str(vault), again.stderr)


class AdoptFromAWorkingCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.base = Path(td.name)
        self.source = self.base / "old-docs"
        (self.source / "notes").mkdir(parents=True)
        self.vault = self.base / "ledger"
        fresh_vault(self.vault)

    def test_the_committed_ingestion_record_does_not_embed_the_local_absolute_path(self) -> None:
        (self.source / "notes" / "a.md").write_text("# A\n\n" + "word " * 30 + "\n", encoding="utf-8")
        result = run("adopt", str(self.source), "--into", str(self.vault), "--write", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.vault / json.loads(result.stdout)["ingestion_record"]
        text = record.read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), text)
        self.assertIn("`old-docs`", text)

    @unittest.skipIf(os.name == "nt", "creating symlinks needs privileges on Windows")
    def test_a_symlink_is_reported_as_the_duplicate_not_the_file_it_points_to(self) -> None:
        (self.source / "notes" / "real.md").write_text("# Real\n\n" + "word " * 30 + "\n", encoding="utf-8")
        (self.source / "alias.md").symlink_to(Path("notes") / "real.md")
        result = run("adopt", str(self.source), "--into", str(self.vault), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        files = {item["path"]: item for item in json.loads(result.stdout)["candidates"]}
        self.assertEqual(files["notes/real.md"]["assessment"], "useful")
        self.assertEqual(files["alias.md"]["assessment"], "duplicate")
        self.assertEqual(files["alias.md"]["duplicate_of"], "notes/real.md")

    def test_one_very_long_path_does_not_pad_every_row(self) -> None:
        deep = self.source / "notes" / ("x" * 150 + ".md")
        deep.write_text("# Deep\n", encoding="utf-8")
        (self.source / "notes" / "short.md").write_text("# Short\n", encoding="utf-8")
        result = run("adopt", str(self.source), "--into", str(self.vault))
        self.assertEqual(result.returncode, 0, result.stderr)
        short_row = next(line for line in result.stdout.splitlines() if "notes/short.md" in line)
        self.assertLess(len(short_row), 120, short_row)


    def test_wide_characters_keep_the_assessment_column_aligned(self) -> None:
        # East Asian wide characters take two terminal cells each; padding by
        # code points would push their row's assessment two cells per character.
        (self.source / "notes" / "決定事項.md").write_text("# 決定\n\n" + "word " * 30 + "\n", encoding="utf-8")
        (self.source / "notes" / "plain-ascii.md").write_text("# Plain\n\n" + "word " * 30 + "\n", encoding="utf-8")
        result = run("adopt", str(self.source), "--into", str(self.vault))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line for line in result.stdout.splitlines() if line.startswith("  notes/")]
        self.assertEqual(len(rows), 2, result.stdout)
        columns = {display_width(row[: row.index("useful")]) for row in rows}
        self.assertEqual(len(columns), 1, rows)


class LintTextColumnsTests(unittest.TestCase):
    def test_level_and_line_columns_line_up_for_errors_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            fresh_vault(vault)
            note = vault / "notes" / "mixed.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "---\ntitle: Mixed\ntype: research\nstatus: draft\nowner: Product Lead\ncreated: 2026-01-05\n"
                "last_updated: 2026-09-01\nsource_of_truth: false\nsensitivity: internal\nsource_ids: []\ntags: []\n"
                "---\n\n# Mixed\n\n" + "\n" * 10 + "[[does-not-exist]]\n",
                encoding="utf-8",
            )
            result = run("lint", "--root", str(vault), "--today", "2026-09-17")
        rows = [line for line in result.stdout.splitlines() if line.startswith("  error") or line.startswith("  warning")]
        levels = {line.split()[0] for line in rows}
        self.assertEqual(levels, {"error", "warning"}, result.stdout)
        # The rule code starts in the same column on every row, and a line
        # number is never glued to the level name.
        self.assertEqual(len({line.index("[") for line in rows}), 1, rows)
        for line in rows:
            self.assertNotRegex(line, r"(error|warning) *:\d", line)


def display_width(text: str) -> int:
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


if __name__ == "__main__":
    unittest.main()
