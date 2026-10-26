"""`whykit diff`: what changed in the decisions between two commits.

Every scenario runs against a real Git repository whose vault lives in a
subdirectory (`kb/`), so the prefix conversion between vault-relative and
work-tree-relative paths is exercised everywhere. The pull request branch
supersedes, archives, re-reviews and renames decisions (one with a non-ASCII
file name), retires, re-sources and adds evidence, and both introduces and fixes
lint findings, while the base branch moves on independently.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from _vaults import CLI, fresh_vault, historical_decision  # noqa: E402

from whykit import cli  # noqa: E402
from whykit.contract import OUTPUT_SCHEMAS  # noqa: E402
from whykit.diff import _chain, _md, render_markdown  # noqa: E402

TODAY = "2026-10-01"
SCHEMA = json.loads((ROOT / "schemas" / OUTPUT_SCHEMAS["diff"]).read_text(encoding="utf-8"))
CAFE = "d-004-café-décision.md"
CAFE_RENAMED = "d-004-café-révisée.md"


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Pat Example", "-c", "user.email=pat@example.com",
         "-c", "core.quotePath=true", "-c", "init.defaultBranch=main", *args],
        cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout


def whykit(vault: Path, *argv: str) -> None:
    code, out, err = call(*argv, "--root", str(vault))
    if code != 0:
        raise AssertionError(f"whykit {' '.join(argv)} failed: {out}{err}")


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{old!r} not in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def build_repository(repo: Path) -> dict[str, str]:
    """Create the repository; return the base ref, head ref and a main-only change."""
    git(repo, "init", "-q")
    vault = repo / "kb"
    stamped = fresh_vault(vault)
    whykit(vault, "new", "evidence", "--source", "Example survey", "--location", "https://example.com/survey",
           "--type", "survey", "--claims", "Respondents prefer email", "--date", "2026-09-01", "--accessed", "2026-09-01")
    whykit(vault, "new", "evidence", "--source", "Example interviews", "--location", "https://example.com/interviews",
           "--type", "interview", "--claims", "Ops leads own the budget", "--date", "2026-09-01", "--accessed", "2026-09-01")
    for title, source in (("Use email", "E-001"), ("Sell to ops", "E-002"), ("Keep the ledger in Git", "E-002")):
        historical_decision(vault, title, owner="Pat Example", source_ids=[source],
                            review_by="2027-01-01", today=stamped)
    historical_decision(vault, "Café pricing révision", owner="Pat Example",
                        review_by="2027-01-01", today=stamped)
    decisions = vault / "06-decisions"
    created = next(decisions.glob("d-004-*.md"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    git(repo, "mv", str(created.relative_to(repo)), f"kb/06-decisions/{CAFE}")
    replace(decisions / "decision-log.md", f"[[06-decisions/{created.stem}]]", f"[[06-decisions/{Path(CAFE).stem}]]")
    # A finding the branch will fix.
    (vault / "Home.md").write_text((vault / "Home.md").read_text(encoding="utf-8") + "\n[[old-missing-page]]\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base state")
    base = git(repo, "rev-parse", "HEAD").strip()

    git(repo, "checkout", "-q", "-b", "feature")
    historical_decision(vault, "Use chat", owner="Pat Example", supersedes="D-001",
                        review_by="2027-01-01", today=stamped)
    whykit(vault, "evidence", "retire", "E-001", "--why", "Survey sample was too small", "--today", TODAY)
    register = vault / "00-context" / "evidence-register.md"
    replace(register, "https://example.com/interviews", "https://example.com/interviews-v2")
    whykit(vault, "new", "evidence", "--source", "Example pricing page", "--location", "https://example.com/pricing",
           "--type", "report", "--claims", "Competitors charge per seat", "--date", "2026-09-20", "--accessed", "2026-09-20")
    git_decision = next(decisions.glob("d-003-*.md"))
    replace(git_decision, "review_by: 2027-01-01", "review_by: 2027-06-01")
    ops = next(decisions.glob("d-002-*.md"))
    replace(ops, "status: approved", "status: archived")
    log = decisions / "decision-log.md"
    lines = [
        line.replace("| accepted |", "| archived |") if line.startswith("| D-002 |") else line
        for line in log.read_text(encoding="utf-8").split("\n")
    ]
    log.write_text("\n".join(lines), encoding="utf-8")
    git(repo, "mv", f"kb/06-decisions/{CAFE}", f"kb/06-decisions/{CAFE_RENAMED}")
    replace(log, f"[[06-decisions/{Path(CAFE).stem}]]", f"[[06-decisions/{Path(CAFE_RENAMED).stem}]]")
    home = vault / "Home.md"
    replace(home, "\n[[old-missing-page]]\n", "\n[[new-missing-page]]\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "decision changes")
    head = git(repo, "rev-parse", "HEAD").strip()

    # The base branch moves on after the branch point; none of it may show up.
    git(repo, "checkout", "-q", "main")
    whykit(vault, "new", "decision", "Main-only decision", "--owner", "Pat Example", "--status", "draft")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moves on")
    git(repo, "checkout", "-q", "feature")
    return {"base": base, "head": head, "main": "main"}


class DiffReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name) / "repo"
        cls.repo.mkdir()
        cls.refs = build_repository(cls.repo)
        cls.vault = cls.repo / "kb"
        code, out, err = call("diff", "--base", "main", "--root", str(cls.vault), "--today", TODAY, "--json")
        if code != 0:
            raise AssertionError(err)
        cls.report = json.loads(out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_report_validates_against_the_published_schema(self) -> None:
        self.assertEqual(validate(self.report, SCHEMA), [])
        self.assertEqual(self.report["contract_version"], 1)
        self.assertEqual(self.report["prefix"], "kb/")
        self.assertEqual(self.report["merge_base"], self.refs["base"])
        self.assertEqual(self.report["head_commit"], self.refs["head"])
        self.assertTrue(self.report["changed"])

    def test_compares_with_the_merge_base_not_the_moving_base_branch(self) -> None:
        titles = [item["title"] for item in self.report["decisions"]["added"]]
        self.assertEqual(titles, ["Use chat"])
        self.assertEqual(self.report["decisions"]["removed"], [])

    def test_supersession_comes_with_its_chain(self) -> None:
        decisions = self.report["decisions"]
        self.assertEqual(decisions["added"][0]["id"], "D-005")
        self.assertEqual(decisions["added"][0]["supersedes"], ["D-001"])
        self.assertEqual(decisions["added"][0]["chain"], ["D-005", "D-001"])
        [superseded] = decisions["superseded"]
        self.assertEqual((superseded["id"], superseded["from"], superseded["to"]), ("D-001", "approved", "superseded"))
        self.assertEqual(superseded["superseded_by"], ["D-005"])
        self.assertEqual(superseded["chain"], ["D-005", "D-001"])

    def test_archive_status_and_review_moves(self) -> None:
        decisions = self.report["decisions"]
        [archived] = decisions["archived"]
        self.assertEqual((archived["id"], archived["to"]), ("D-002", "archived"))
        self.assertEqual(decisions["status_changed"], [])
        [moved] = decisions["review_moved"]
        self.assertEqual(
            (moved["id"], moved["from"], moved["to"], moved["direction"]),
            ("D-003", "2027-01-01", "2027-06-01", "later"),
        )

    def test_non_ascii_rename_is_a_move_not_a_delete_and_add(self) -> None:
        [moved] = self.report["decisions"]["moved"]
        self.assertEqual(moved["id"], "D-004")
        self.assertEqual(moved["from"], f"06-decisions/{CAFE}")
        self.assertEqual(moved["to"], f"06-decisions/{CAFE_RENAMED}")
        self.assertEqual(moved["title"], "Café pricing révision")

    def test_evidence_added_retired_and_resourced(self) -> None:
        evidence = self.report["evidence"]
        self.assertEqual([item["id"] for item in evidence["added"]], ["E-003"])
        [retired] = evidence["retired"]
        self.assertEqual((retired["id"], retired["why"]), ("E-001", "Survey sample was too small"))
        [resourced] = evidence["resourced"]
        self.assertEqual(resourced["id"], "E-002")
        self.assertEqual(
            resourced["changes"]["location"],
            {"from": "https://example.com/interviews", "to": "https://example.com/interviews-v2"},
        )
        self.assertEqual(evidence["removed"], [])

    def test_decisions_whose_cited_evidence_changed(self) -> None:
        affected = {item["id"]: item["evidence"] for item in self.report["affected_decisions"]}
        self.assertEqual(affected["D-001"], [{"id": "E-001", "change": "retired"}])
        self.assertEqual(affected["D-002"], [{"id": "E-002", "change": "resourced"}])
        self.assertEqual(affected["D-003"], [{"id": "E-002", "change": "resourced"}])
        self.assertNotIn("D-005", affected)

    def test_lint_findings_introduced_and_fixed_ignore_renames_and_line_moves(self) -> None:
        lint = self.report["lint"]
        # The renamed D-004 keeps its findings under the new name: neither
        # introduced nor fixed. D-005 is new, and so are its placeholder
        # findings; D-001 and D-002 are history now, so theirs are gone.
        introduced = {(item["code"], item["path"]) for item in lint["introduced"]}
        self.assertEqual(introduced, {("wikilink.missing", "Home.md"), ("decision.placeholder", "06-decisions/d-005-use-chat.md")})
        missing = [item for item in lint["introduced"] if item["code"] == "wikilink.missing"]
        self.assertIn("new-missing-page", missing[0]["message"])
        fixed = {(item["code"], item["path"]) for item in lint["fixed"]}
        self.assertIn(("wikilink.missing", "Home.md"), fixed)
        self.assertFalse({path for _, path in introduced | fixed} & {f"06-decisions/{CAFE}", f"06-decisions/{CAFE_RENAMED}"})
        old = [item for item in lint["fixed"] if item["code"] == "wikilink.missing"]
        self.assertIn("old-missing-page", old[0]["message"])

    def test_the_working_tree_index_and_head_are_untouched(self) -> None:
        home = self.vault / "Home.md"
        original = home.read_text(encoding="utf-8")
        try:
            home.write_text(original + "\nuncommitted edit [[also-missing]]\n", encoding="utf-8")
            (self.vault / "notes" / "scratch.md").write_text("draft\n", encoding="utf-8")
            before = git(self.repo, "status", "--porcelain=v1", "-z")
            head = git(self.repo, "rev-parse", "HEAD")
            code, out, _ = call("diff", "--base", "main", "--root", str(self.vault), "--today", TODAY, "--json")
            self.assertEqual(code, 0)
            # The uncommitted edit is not part of the comparison.
            self.assertEqual(json.loads(out)["lint"], self.report["lint"])
            self.assertEqual(git(self.repo, "status", "--porcelain=v1", "-z"), before)
            self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
            self.assertIn("uncommitted edit", home.read_text(encoding="utf-8"))
        finally:
            home.write_text(original, encoding="utf-8")
            (self.vault / "notes" / "scratch.md").unlink()

    def test_runs_from_inside_the_vault_without_root(self) -> None:
        previous = os.getcwd()
        os.chdir(self.vault / "06-decisions")
        try:
            code, out, err = call("diff", "--base", "main", "--today", TODAY, "--json")
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["decisions"], self.report["decisions"])

    def test_c_locale_subprocess_reads_non_ascii_names(self) -> None:
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONIOENCODING": ""}
        result = subprocess.run(
            [sys.executable, "-X", "utf8=0", str(CLI), "diff", "--base", "main", "--root", str(self.vault),
             "--today", TODAY, "--json"],
            capture_output=True, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(report["decisions"]["moved"], self.report["decisions"]["moved"])

    @unittest.skipIf(os.name == "nt", "Windows file names are UTF-16, not bytes")
    def test_ascii_filesystem_encoding_still_writes_non_ascii_names(self) -> None:
        # Linux under LC_ALL=C (no UTF-8 mode) encodes file names as ASCII with
        # surrogateescape. Simulate that interpreter here: only names that
        # round-trip through that codec can be opened, exactly as there.
        from unittest import mock

        from whykit import diff as diff_module
        from whykit.immutability import _git_prefix

        real_open = open

        def ascii_open(target, *args, **kwargs):
            os.fspath(target).encode("ascii", "surrogateescape")  # raises like the C-locale interpreter
            return real_open(target, *args, **kwargs)

        with tempfile.TemporaryDirectory() as scratch, \
                mock.patch.object(diff_module, "_filesystem_encoding", lambda: "ascii", create=True), \
                mock.patch.object(diff_module, "open", ascii_open, create=True):
            dest = Path(scratch)
            where = str(self.vault)
            self.assertTrue(diff_module.materialize(self.refs["head"], _git_prefix(where), where, dest))
            names = os.listdir(os.fsencode(dest / "06-decisions"))
        self.assertIn(CAFE_RENAMED.encode("utf-8"), names)

    @unittest.skipIf(os.name == "nt", "Windows file names are UTF-16, not bytes")
    def test_git_names_map_to_their_bytes_and_back_for_display(self) -> None:
        from unittest import mock

        from whykit import diff as diff_module

        with mock.patch.object(diff_module, "_filesystem_encoding", lambda: "ascii"):
            escaped = diff_module._os_name(CAFE)
        self.assertEqual(escaped.encode("ascii", "surrogateescape"), CAFE.encode("utf-8"))
        with mock.patch.object(diff_module, "_filesystem_encoding", lambda: "utf-8"):
            self.assertEqual(diff_module._os_name(CAFE), CAFE)
        self.assertEqual(diff_module._repo_text(f"06-decisions/{escaped}"), f"06-decisions/{CAFE}")
        self.assertEqual(diff_module._repo_text(f"Café links {escaped}"), f"Café links {CAFE}")
        # Bytes that are not UTF-8 stay escaped rather than collapsing into U+FFFD.
        self.assertEqual(diff_module._repo_text("bad-\udcff.md"), "bad-\udcff.md")

    def test_identical_revisions_report_no_change(self) -> None:
        code, out, _ = call("diff", "--base", "HEAD", "--root", str(self.vault), "--today", TODAY, "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertFalse(report["changed"])
        self.assertEqual(validate(report, SCHEMA), [])
        code, text, _ = call("diff", "--base", "HEAD", "--root", str(self.vault), "--today", TODAY)
        self.assertIn("no decision, evidence or lint changes", text)

    def test_text_output_lists_each_section(self) -> None:
        code, out, _ = call("diff", "--base", "main", "--root", str(self.vault), "--today", TODAY)
        self.assertEqual(code, 0)
        for fragment in ("New decisions", "D-005 Use chat", "chain D-005 → D-001", "Review dates moved",
                         "E-002 re-sourced", "Decisions whose evidence changed", "New lint findings"):
            self.assertIn(fragment, out)

    def test_markdown_is_a_sticky_comment_with_a_stable_marker(self) -> None:
        code, out, _ = call("diff", "--base", "main", "--root", str(self.vault), "--today", TODAY, "--format", "markdown")
        self.assertEqual(code, 0)
        first = out.splitlines()[0]
        self.assertEqual(first, "<!-- whykit-diff root=kb -->")
        self.assertIn("#### Superseded or archived", out)
        self.assertIn("**D-001** Use email: approved → superseded by D-005", out)
        self.assertIn("**D-004** Café pricing révision moved", out)
        self.assertLess(len(out), 65536)

    def test_github_format_annotates_work_tree_paths(self) -> None:
        code, out, _ = call("diff", "--base", "main", "--root", str(self.vault), "--today", TODAY, "--format", "github")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"(?m)^::notice file=kb/06-decisions/d-005-use-chat\.md,title=WhyKit new decision::D-005 added")
        self.assertRegex(out, r"(?m)^::warning file=kb/06-decisions/d-003-[^,]+\.md,title=WhyKit evidence changed::D-003 cites")
        self.assertRegex(out, r"(?m)^::error file=kb/Home\.md,line=\d+,title=WhyKit wikilink\.missing \(new\)::")
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("whykit diff: "))

    def test_json_and_another_format_conflict(self) -> None:
        code, out, _ = call("diff", "--base", "main", "--root", str(self.vault), "--json", "--format", "markdown")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "usage")


class DiffEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def repo_with_vault_added_later(self) -> Path:
        repo = self.tmp / "d\u00e9p\u00f4t"  # a non-ASCII work tree path
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "README.md").write_text("code only\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "before the vault")
        git(repo, "checkout", "-q", "-b", "vault")
        fresh_vault(repo / "kb")
        whykit(repo / "kb", "new", "decision", "First decision", "--owner", "Pat Example", "--status", "draft")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "add the vault")
        return repo

    def test_a_vault_added_by_the_change_reports_everything_as_new(self) -> None:
        repo = self.repo_with_vault_added_later()
        code, out, err = call("diff", "--base", "main", "--root", str(repo / "kb"), "--today", TODAY, "--json")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(validate(report, SCHEMA), [])
        self.assertFalse(report["vault_at_base"])
        self.assertTrue(report["vault_at_head"])
        self.assertEqual([item["id"] for item in report["decisions"]["added"]], ["D-001"])
        self.assertEqual(report["lint"]["base"], {"errors": 0, "warnings": 0})

    def test_no_vault_in_either_revision_warns(self) -> None:
        repo = self.repo_with_vault_added_later()
        code, out, err = call("diff", "--base", "main", "--head", "main", "--root", str(repo), "--json")
        self.assertEqual(code, 0)
        self.assertIn("no WhyKit vault", err)
        self.assertFalse(json.loads(out)["changed"])

    def test_unknown_revision_is_a_git_error(self) -> None:
        repo = self.repo_with_vault_added_later()
        code, out, err = call("diff", "--base", "no-such-ref", "--root", str(repo / "kb"), "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "git_error")
        self.assertIn("unknown Git revision: no-such-ref", err)
        code, out, _ = call("diff", "--base", "--upload-pack=x", "--root", str(repo / "kb"), "--json")
        self.assertEqual(code, 2)

    def test_outside_git_is_a_git_error(self) -> None:
        vault = self.tmp / "loose"
        fresh_vault(vault)
        code, out, err = call("diff", "--base", "HEAD", "--root", str(vault), "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "git_error")
        self.assertIn("not inside a Git work tree", err)

    def test_invalid_today_and_root(self) -> None:
        repo = self.repo_with_vault_added_later()
        code, out, _ = call("diff", "--base", "main", "--root", str(repo / "kb"), "--today", "2026-02-30", "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "invalid_argument"))
        code, out, _ = call("diff", "--base", "main", "--root", str(repo / "missing"), "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "invalid_argument"))

    def test_shallow_clone_without_a_merge_base_names_the_fix(self) -> None:
        repo = self.repo_with_vault_added_later()
        clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", "--depth", "1", "--branch", "vault", "--no-local", repo.as_uri(), str(clone))
        git(clone, "fetch", "-q", "--depth", "1", "origin", "main:main")
        code, out, err = call("diff", "--base", "main", "--root", str(clone / "kb"), "--json")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["code"], "git_error")
        self.assertIn("shallow clone", payload["error"]["message"])
        self.assertIn("fetch-depth: 0", payload["error"]["hint"])
        self.assertIn("hint:", err)

    def test_symlinks_in_the_tree_are_not_followed(self) -> None:
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        repo = self.repo_with_vault_added_later()
        secret = self.tmp / "outside.md"
        secret.write_text("api_key = 'abcdefghijklmnopqrstuvwxyz0123'\n", encoding="utf-8")
        (repo / "kb" / "notes" / "link.md").symlink_to(secret)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "a symlink")
        code, out, err = call("diff", "--base", "main", "--root", str(repo / "kb"), "--today", TODAY, "--json")
        self.assertEqual(code, 0, err)
        codes = {item["code"] for item in json.loads(out)["lint"]["introduced"]}
        self.assertNotIn("secret.detected", codes)


class RenderingTests(unittest.TestCase):
    def test_chain_follows_both_directions_and_survives_cycles(self) -> None:
        def record(did: str, supersedes: list[str] = [], superseded_by: list[str] = []) -> dict:  # noqa: B006
            return {"id": did, "supersedes": supersedes, "superseded_by": superseded_by}

        decisions = {
            "D-001": record("D-001", superseded_by=["D-002"]),
            "D-002": record("D-002"),
            "D-003": record("D-003", supersedes=["D-002"]),
        }
        self.assertEqual(_chain("D-001", decisions), ["D-003", "D-002", "D-001"])
        self.assertEqual(_chain("D-003", decisions), ["D-003", "D-002", "D-001"])
        loop = {"D-001": record("D-001", supersedes=["D-002"]), "D-002": record("D-002", supersedes=["D-001"])}
        self.assertEqual(sorted(_chain("D-001", loop)), ["D-001", "D-002"])

    def test_vault_text_cannot_inject_markup_or_mentions(self) -> None:
        hostile = "<img src=x onerror=alert(1)> @org/team [link](https://example.invalid) | `code`\n# heading"
        escaped = _md(hostile)
        self.assertNotIn("<", escaped)
        self.assertNotIn("@", escaped)
        self.assertNotIn("\n", escaped)
        self.assertNotRegex(re.sub(r"&#?\w+;", "", escaped), r"(?<!\\)[\[\]|`#]")

    def test_markdown_is_capped_below_the_comment_limit(self) -> None:
        item = {"id": "D-001", "title": "x" * 500, "status": "approved", "path": "06-decisions/d-001-x.md",
                "supersedes": [], "chain": ["D-001"]}
        report = {
            "prefix": "", "merge_base": "a" * 40, "head_commit": "b" * 40, "today": TODAY,
            "decisions": {"added": [item] * 30, "removed": [], "superseded": [], "archived": [],
                          "status_changed": [], "review_moved": [], "moved": []},
            "evidence": {"added": [], "retired": [], "resourced": [], "updated": [], "removed": []},
            "affected_decisions": [],
            "lint": {"base": {"errors": 0, "warnings": 0}, "head": {"errors": 0, "warnings": 0},
                     "introduced": [{"path": "Home.md", "line": n, "level": "error", "code": "wikilink.missing",
                                     "message": "m" * 3000} for n in range(40)],
                     "fixed": []},
        }
        text = render_markdown(report)
        self.assertTrue(text.startswith("<!-- whykit-diff root=. -->\n"))
        self.assertLess(len(text), 65536)
        self.assertIn("and 5 more", text)
        self.assertEqual(len(re.findall(r"(?m)^- \*\*D-001\*\*", text)), 25)


if __name__ == "__main__":
    unittest.main()
