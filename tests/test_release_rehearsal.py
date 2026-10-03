"""``scripts/release_rehearsal.py``: the local dry run of a package release.

The pure parts (changelog parsing, version arithmetic, release notes, the
printed commands) are tested directly. A rehearsal with ``--skip-build`` runs
against a throwaway Git repository through a ``git`` wrapper that records every
invocation, to prove the script never tags or pushes. The full build, install
and smoke run takes a minute or more and needs warm uv caches, so it is opt-in:
set ``WHYKIT_REHEARSAL_E2E=1``.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_rehearsal", ROOT / "scripts" / "release_rehearsal.py")
assert _spec is not None and _spec.loader is not None
rehearsal = importlib.util.module_from_spec(_spec)
sys.modules["release_rehearsal"] = rehearsal
_spec.loader.exec_module(rehearsal)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Example Maintainer",
    "GIT_AUTHOR_EMAIL": "maintainer@example.com",
    "GIT_COMMITTER_NAME": "Example Maintainer",
    "GIT_COMMITTER_EMAIL": "maintainer@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def changelog(unreleased: str, *released: tuple[str, str, str]) -> str:
    parts = ["# Changelog\n", "## [Unreleased]\n", textwrap.dedent(unreleased)]
    for version, suffix, body in released:
        parts += [f"## [{version}] — {suffix}\n", textwrap.dedent(body)]
    return "\n".join(parts)


OLD = ("0.2.0", "2026-09-17", "### Added\n\n- Something old.\n")


class ChangelogVersionTests(unittest.TestCase):
    def candidate(self, text: str, tags: set[str] | None = None) -> object:
        return rehearsal.next_version(rehearsal.parse_changelog(text), tags or set())

    def test_breaking_change_before_1_0_bumps_the_minor_version(self) -> None:
        text = changelog("### Breaking changes\n\n- Renamed a rule code.\n\n### Fixed\n\n- A bug.\n", OLD)
        candidate = self.candidate(text)
        self.assertEqual(candidate.version, "0.3.0")
        self.assertEqual(candidate.previous, "0.2.0")
        self.assertIn("breaking", candidate.reason)

    def test_breaking_change_after_1_0_bumps_the_major_version(self) -> None:
        text = changelog("### Changed\n\n- **BREAKING:** new exit code.\n", ("1.4.2", "2027-01-01", "- x\n"))
        self.assertEqual(self.candidate(text).version, "2.0.0")

    def test_added_bumps_minor_and_fixes_only_bump_patch(self) -> None:
        self.assertEqual(self.candidate(changelog("### Added\n\n- A command.\n", OLD)).version, "0.3.0")
        self.assertEqual(self.candidate(changelog("### Fixed\n\n- A bug.\n\n### Security\n\n- A hole.\n", OLD)).version, "0.2.1")

    def test_empty_subsection_headings_do_not_count(self) -> None:
        self.assertEqual(self.candidate(changelog("### Breaking changes\n\n### Fixed\n\n- A bug.\n", OLD)).version, "0.2.1")

    def test_development_and_preview_sections_are_not_releases(self) -> None:
        text = changelog(
            "### Fixed\n\n- A bug.\n",
            ("0.3.0.dev0", "source preview, not released", "### Added\n\n- x\n"),
            OLD,
        )
        candidate = self.candidate(text)
        self.assertEqual((candidate.version, candidate.previous), ("0.2.1", "0.2.0"))

    def test_after_the_release_pr_the_newest_untagged_section_is_the_candidate(self) -> None:
        text = changelog("", ("0.3.0", "2026-10-10", "### Fixed\n\n- A bug.\n"), OLD)
        candidate = self.candidate(text, {"v0.2.0"})
        self.assertEqual((candidate.version, candidate.previous), ("0.3.0", "0.2.0"))
        self.assertIn("- A bug.", candidate.section.body)

    def test_nothing_to_release_is_an_error_not_a_guess(self) -> None:
        with self.assertRaisesRegex(rehearsal.RehearsalError, "already tagged v0.2.0"):
            self.candidate(changelog("", OLD), {"v0.2.0"})
        with self.assertRaisesRegex(rehearsal.RehearsalError, "no earlier X.Y.Z section"):
            self.candidate(changelog("### Fixed\n\n- A bug.\n"))

    def test_dated_heading_must_carry_an_iso_date(self) -> None:
        dated = self.candidate(changelog("", ("0.3.0", "2026-10-10", "- x\n"), OLD), {"v0.2.0"})
        self.assertEqual(rehearsal.changelog_dating(dated)[0], "PASS")
        undated = self.candidate(changelog("", ("0.3.0", "soon", "- x\n"), OLD), {"v0.2.0"})
        self.assertEqual(rehearsal.changelog_dating(undated)[0], "FAIL")
        draft = self.candidate(changelog("### Fixed\n\n- x\n", OLD))
        self.assertEqual(rehearsal.changelog_dating(draft)[0], "PENDING")

    def test_link_footers_stay_out_of_the_notes(self) -> None:
        text = changelog("### Fixed\n\n- A bug.\n\n[Unreleased]: https://example.com/compare\n", OLD)
        self.assertNotIn("example.com/compare", rehearsal.parse_changelog(text)[0].body)

    def test_section_structure_is_checked(self) -> None:
        good = rehearsal.parse_changelog(changelog("### Highlights\n\n- a\n\n### Fixed\n\n- b\n\n### Performance\n\n- c\n"))[0]
        self.assertEqual(rehearsal.check_sections(good), [])
        unknown = rehearsal.parse_changelog(changelog("### Fixes\n\n- b\n"))[0]
        self.assertIn("unknown changelog subsection '### Fixes'", rehearsal.check_sections(unknown)[0])
        disorder = rehearsal.parse_changelog(changelog("### Fixed\n\n- b\n\n### Added\n\n- a\n"))[0]
        self.assertIn("out of order", rehearsal.check_sections(disorder)[0])
        # Area groups one level down are free-form.
        grouped = rehearsal.parse_changelog(changelog("### Added\n\n#### Commands\n\n- a\n"))[0]
        self.assertEqual(rehearsal.check_sections(grouped), [])

    def test_the_repository_changelog_is_a_valid_release_draft(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        sections = rehearsal.parse_changelog(text)
        candidate = rehearsal.next_version(sections, set())
        self.assertEqual(rehearsal.check_sections(candidate.section), [])
        package = re.search(r'__version__ = "([^"]+)"', (ROOT / "src" / "whykit" / "__init__.py").read_text(encoding="utf-8"))
        assert package is not None
        status, detail = rehearsal.version_alignment(package.group(1), candidate.version)
        self.assertIn(status, {"PASS", "PENDING"}, detail)


class VersionAlignmentTests(unittest.TestCase):
    def test_final_dev_and_mismatched_versions(self) -> None:
        self.assertEqual(rehearsal.version_alignment("0.3.0", "0.3.0")[0], "PASS")
        self.assertEqual(rehearsal.version_alignment("0.3.0.dev0", "0.3.0")[0], "PENDING")
        self.assertEqual(rehearsal.version_alignment("0.3.0.dev0", "0.4.0")[0], "FAIL")
        self.assertEqual(rehearsal.version_alignment("0.3.1", "0.3.0")[0], "FAIL")


class NotesAndCommandsTests(unittest.TestCase):
    def test_first_release_links_its_own_tag_and_later_ones_compare(self) -> None:
        text = changelog("### Highlights\n\n- Fast.\n\n### Added\n\n- A command.\n", OLD)
        candidate = rehearsal.next_version(rehearsal.parse_changelog(text), set())
        first = rehearsal.release_notes(candidate, set())
        self.assertTrue(first.startswith("# WhyKit 0.3.0\n\n### Highlights\n\n- Fast."))
        self.assertIn("releases/tag/v0.3.0", first)
        self.assertIn("compare/v0.2.0...v0.3.0", rehearsal.release_notes(candidate, {"v0.2.0"}))

    def test_owner_commands_tag_the_rehearsed_commit_and_push_only_that_tag(self) -> None:
        sha = "a" * 40
        ready = rehearsal.owner_commands("0.3.0", sha, pending=False)
        self.assertIn(f'git tag -a v0.3.0 -m "WhyKit 0.3.0" {sha}', ready)
        self.assertIn("git push origin v0.3.0", ready)
        self.assertNotIn("--tags", ready)
        self.assertNotIn("--force", ready)
        pending = rehearsal.owner_commands("0.3.0", sha, pending=True)
        self.assertIn('__version__ = "0.3.0"', pending)
        self.assertIn("git tag -a v0.3.0 -m \"WhyKit 0.3.0\" <merged-main-sha>", pending)

    def test_supported_pythons_come_from_the_classifiers(self) -> None:
        versions = rehearsal.supported_pythons(ROOT)
        lowest = re.search(r'requires-python = ">=(3\.\d+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert lowest is not None
        self.assertEqual(versions[0], lowest.group(1))
        self.assertEqual(versions, sorted(versions, key=lambda v: int(v.split(".")[1])))


class NeverReleasesTests(unittest.TestCase):
    def test_git_wrapper_refuses_anything_but_read_only_subcommands(self) -> None:
        with mock.patch.object(rehearsal.subprocess, "run") as run:
            for argv in (("tag", "-a", "v0.3.0"), ("push", "origin", "v0.3.0"), ("commit",), ("fetch",), ("switch", "main"), ()):
                with self.subTest(argv=argv), self.assertRaisesRegex(rehearsal.RehearsalError, "only reads"):
                    rehearsal._git(ROOT, *argv)
            run.assert_not_called()

    def test_the_script_never_spells_out_a_release_action_it_runs(self) -> None:
        source = (ROOT / "scripts" / "release_rehearsal.py").read_text(encoding="utf-8")
        # Commands it runs are lists; none of them may start a publish.
        for forbidden in ('"publish"', '"upload"', '"workflow", "run"', '"release", "create"', '"tag"', '"push"'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


@unittest.skipIf(os.name == "nt", "the git recording wrapper is a POSIX shell script")
@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class SkipBuildRehearsalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "src" / "whykit").mkdir(parents=True)
        (self.repo / "src" / "whykit" / "__init__.py").write_text('__version__ = "0.3.0.dev0"\n', encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text(
            changelog("### Highlights\n\n- Fast.\n\n### Breaking changes\n\n- New exit code.\n", OLD), encoding="utf-8",
        )
        self.real_git = shutil.which("git")
        self.log = self.tmp / "git-calls.log"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "git"
        shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\nexec "{self.real_git}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        env = {**GIT_ENV, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Initial")
        self.git("tag", "v0.2.0")

    def git(self, *args: str) -> str:
        assert self.real_git is not None
        return subprocess.run([self.real_git, *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def rehearse(self, *argv: str) -> tuple[int, str]:
        args = rehearsal.build_parser().parse_args(["--skip-build", "--work-dir", str(self.tmp / "work"), *argv])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = rehearsal.rehearse(args, root=self.repo)
            except rehearsal.RehearsalError as exc:
                return 2, str(exc)
        return code, out.getvalue()

    def assert_never_released(self) -> None:
        calls = self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []
        self.assertTrue(calls, "the wrapper recorded no git calls; is it on PATH?")
        for call in calls:
            with self.subTest(call=call):
                self.assertIn(call.split()[0], rehearsal.READ_ONLY_GIT)
        self.assertEqual(self.git("tag", "--list").split(), ["v0.2.0"])

    def test_clean_checkout_passes_with_the_release_pr_pending(self) -> None:
        code, out = self.rehearse()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS    next version is 0.3.0", out)
        self.assertIn("PENDING version alignment", out)
        self.assertIn("RELEASE PULL REQUEST STILL PENDING", out)
        self.assertIn("git push origin v0.3.0", out)
        notes = (self.tmp / "work" / "release-notes-v0.3.0.md").read_text(encoding="utf-8")
        self.assertIn("- New exit code.", notes)
        self.assertIn("compare/v0.2.0...v0.3.0", notes)
        self.assert_never_released()

    def test_final_version_still_under_unreleased_is_pending(self) -> None:
        (self.repo / "src" / "whykit" / "__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")
        self.git("commit", "-q", "-am", "Bump only")
        code, out = self.rehearse()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS    version alignment", out)
        self.assertIn("PENDING changelog section is dated", out)
        self.assertNotIn("READY TO TAG", out)

    def test_final_version_is_ready_to_tag_the_exact_commit(self) -> None:
        (self.repo / "src" / "whykit" / "__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text(
            changelog("", ("0.3.0", "2026-10-10", "### Breaking changes\n\n- New exit code.\n"), OLD), encoding="utf-8",
        )
        self.git("commit", "-q", "-am", "Release 0.3.0")
        head = self.git("rev-parse", "HEAD").strip()
        code, out = self.rehearse()
        self.assertEqual(code, 0, out)
        self.assertIn("READY TO TAG", out)
        self.assertIn(f'git tag -a v0.3.0 -m "WhyKit 0.3.0" {head}', out)
        self.assert_never_released()

    def test_dirty_tree_or_other_checkout_fails(self) -> None:
        first = self.git("rev-parse", "HEAD").strip()
        (self.repo / "stray.txt").write_text("x\n", encoding="utf-8")
        code, out = self.rehearse()
        self.assertEqual(code, 1)
        self.assertIn("FAIL    working tree is clean", out)
        self.assertIn("NOT READY", out)
        self.assertNotIn("git push", out)
        (self.repo / "stray.txt").unlink()
        (self.repo / "next.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "next.txt")
        self.git("commit", "-q", "-m", "Next")
        code, out = self.rehearse("--commit", first)
        self.assertEqual(code, 1)
        self.assertIn("FAIL    checkout matches the commit", out)
        self.assert_never_released()

    def test_existing_tag_for_the_candidate_fails(self) -> None:
        self.git("tag", "v0.3.0")
        code, out = self.rehearse()
        self.assertEqual(code, 1)
        self.assertIn("FAIL    tag v0.3.0 does not exist yet", out)

    def test_unknown_commit_and_work_dir_inside_the_repo_cannot_run(self) -> None:
        code, message = self.rehearse("--commit", "does-not-exist")
        self.assertEqual(code, 2)
        self.assertIn("is not a commit", message)
        args = rehearsal.build_parser().parse_args(["--skip-build", "--work-dir", str(self.repo / "out")])
        with self.assertRaisesRegex(rehearsal.RehearsalError, "inside the repository"), contextlib.redirect_stdout(io.StringIO()):
            rehearsal.rehearse(args, root=self.repo)
        self.assertFalse((self.repo / "out").exists())


@unittest.skipUnless(os.environ.get("WHYKIT_REHEARSAL_E2E") == "1", "set WHYKIT_REHEARSAL_E2E=1 to build, install and smoke-test for real")
@unittest.skipIf(shutil.which("uv") is None or shutil.which("git") is None, "needs uv and git")
class FullRehearsalTests(unittest.TestCase):
    def test_full_rehearsal_on_a_clean_copy_of_this_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "whykit"
            listed = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=ROOT, check=True, capture_output=True,
            ).stdout.decode("utf-8").split("\0")
            for name in filter(None, listed):
                source = ROOT / name
                if source.is_file():
                    (copy / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, copy / name)
            env = {**os.environ, **GIT_ENV}
            for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "Rehearsal candidate"]):
                subprocess.run(["git", *argv], cwd=copy, check=True, env=env, capture_output=True)
            python = f"{sys.version_info.major}.{sys.version_info.minor}"
            result = subprocess.run(
                [sys.executable, str(copy / "scripts" / "release_rehearsal.py"), "--work-dir", str(Path(tmp) / "work"), "--python", python],
                cwd=copy, env=env, capture_output=True, text=True, timeout=1800,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for needle in (
                "PASS    check_dist (contents, metadata, byte-identical rebuild)",
                "PASS    twine check --strict",
                f"PASS    smoke wheel-py{python}",
                "PASS    smoke sdist",
            ):
                self.assertIn(needle, result.stdout)


if __name__ == "__main__":
    unittest.main()
