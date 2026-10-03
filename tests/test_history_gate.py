"""The append-only history gate, transition by transition.

`whykit history` is the release gate for decision records, so every way a diff
can reach an accepted record is exercised here against a real Git repository:
edits, lifecycle moves, renames, deletes, type changes, merges, shallow clones,
quoted path names and vaults that live in a subdirectory. The parsers it relies
on are additionally hammered with seeded random inputs.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whykit import immutability
from whykit.immutability import (
    _status,
    allowed_lifecycle_change,
    allowed_review_log_append,
    changed_records,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "whykit.py"

APPROVED = """---
title: "D-001 — Use Git"
type: decision
decision_id: D-001
status: approved
owner: "Pat Example"
created: 2026-01-05
last_updated: 2026-01-05
review_by: 2027-01-05
source_ids: ["E-001"]
---

# D-001 — Use Git

## Context

Records were scattered across three tools.

## Decision

Keep them in Git.
"""

DRAFT = APPROVED.replace("decision_id: D-001", "decision_id: D-002").replace("status: approved", "status: draft")

REVIEW_LOG = """---
title: "Review log"
type: reference
status: approved
owner: TODO
created: 2026-09-22
last_updated: 2026-09-22
---

# Review log

Append-only operational record.

| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |
|---|---|---|---|---|---|---|
| 2026-09-22 | [[Home]] | Pat | confirmed | — | 2027-01-01 | ok |

Trailing paragraph.
"""

ROW = "| 2026-10-01 | [[Home]] | Lee | confirmed | 2026-09-22 | 2027-04-01 | again |\n"


def _replace_key(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            lines[index] = f"{key}: {value}\n"
            return "".join(lines)
    # Insert before the closing fence.
    close = lines.index("---\n", 1)
    lines.insert(close, f"{key}: {value}\n")
    return "".join(lines)


class Repo:
    """A throwaway Git repository with an optional vault subdirectory."""

    def __init__(self, vault: str = "") -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.top = Path(self._tmp.name).resolve()
        self.vault = self.top / vault if vault else self.top
        self.vault.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "WhyKit test")
        self.git("config", "commit.gpgsign", "false")

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd or self.top, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout

    def write(self, relative: str, text: str) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def commit(self, message: str = "change") -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message, "--allow-empty")
        return self.git("rev-parse", "HEAD").strip()

    def blocked(self, base: str, head: str = "HEAD", **kwargs) -> list[tuple[str, str]]:
        return changed_records(base, head, str(self.vault), **kwargs)


class RepoTestCase(unittest.TestCase):
    vault_dir = ""

    def setUp(self) -> None:
        self.repo = Repo(self.vault_dir)
        self.addCleanup(self.repo.cleanup)
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED)
        self.repo.write("06-decisions/d-002-draft.md", DRAFT)
        self.repo.write("00-context/review-log.md", REVIEW_LOG)
        self.base = self.repo.commit("base")

    def record(self) -> Path:
        return self.repo.vault / "06-decisions/d-001-use-git.md"


class StatusParsingTests(unittest.TestCase):
    def test_plain_quoted_and_commented_values(self) -> None:
        for line in ("status: approved", 'status: "approved"', "status: 'approved'", "status: approved  # council"):
            with self.subTest(line=line):
                self.assertEqual(_status(f"---\n{line}\n---\nbody\n"), "approved")

    def test_status_is_read_from_front_matter_only(self) -> None:
        # Regression: a YAML snippet in the body used to make the status
        # ambiguous, which demoted an approved record to "mutable".
        text = "---\nstatus: approved\n---\n# D\n\n```yaml\nstatus: draft\n```\n"
        self.assertEqual(_status(text), "approved")

    def test_body_only_status_is_not_a_lifecycle_status(self) -> None:
        self.assertIsNone(_status("# Title\n\nstatus: approved\n"))
        self.assertIsNone(_status("---\ntitle: x\n---\nstatus: approved\n"))

    def test_status_value_is_case_normalised(self) -> None:
        # Regression: `Approved` was returned verbatim and then compared with
        # the lowercase set, so the record was treated as mutable.
        self.assertEqual(_status("---\nstatus: Approved\n---\n"), "approved")
        self.assertEqual(_status("---\nstatus: ARCHIVED\n---\n"), "archived")

    def test_byte_order_mark_and_crlf_are_tolerated(self) -> None:
        self.assertEqual(_status("﻿---\nstatus: approved\n---\n"), "approved")
        self.assertEqual(_status("---\r\nstatus: approved\r\n---\r\n"), "approved")

    def test_ambiguous_or_unknown_status_is_none(self) -> None:
        self.assertIsNone(_status("---\nstatus: approved\nstatus: draft\n---\n"))
        self.assertIsNone(_status("---\nstatus: draft\n---\n"))
        self.assertIsNone(_status("---\nstatus: approved\n"))  # never closed
        self.assertIsNone(_status("---\nmeta:\n  status: approved\n---\n"))
        self.assertIsNone(_status(""))


class LifecycleMatrixTests(unittest.TestCase):
    def test_allowed_transitions(self) -> None:
        cases = {
            "review date refresh": _replace_key(_replace_key(APPROVED, "review_by", "2027-07-01"), "last_updated", "2026-07-01"),
            "unchanged": APPROVED,
            "superseded": _replace_key(_replace_key(APPROVED, "status", "superseded"), "superseded_by", "D-009"),
            "archived": _replace_key(APPROVED, "status", "archived"),
            "quoted superseded": _replace_key(APPROVED, "status", '"superseded"'),
        }
        for name, head in cases.items():
            with self.subTest(name):
                self.assertTrue(allowed_lifecycle_change(APPROVED, head))

    def test_blocked_transitions(self) -> None:
        superseded = _replace_key(APPROVED, "status", "superseded")
        archived = _replace_key(APPROVED, "status", "archived")
        cases = {
            "approved -> draft": (APPROVED, _replace_key(APPROVED, "status", "draft")),
            "approved -> in_review": (APPROVED, _replace_key(APPROVED, "status", "in_review")),
            "owner change": (APPROVED, _replace_key(APPROVED, "owner", '"Someone Else"')),
            "evidence change": (APPROVED, _replace_key(APPROVED, "source_ids", '["E-002"]')),
            "new key": (APPROVED, _replace_key(APPROVED, "notes", '"added"')),
            "superseded_by while approved": (APPROVED, _replace_key(APPROVED, "superseded_by", "D-009")),
            "review_by moved while superseding with title edit": (
                APPROVED, _replace_key(_replace_key(APPROVED, "status", "superseded"), "title", '"Renamed"'),
            ),
            "body edit": (APPROVED, APPROVED + "\nAddendum.\n"),
            "body edit on supersede": (APPROVED, superseded + "\nAddendum.\n"),
            "front matter removed": (APPROVED, APPROVED.split("---\n", 2)[2]),
            "status removed": (APPROVED, APPROVED.replace("status: approved\n", "")),
            "superseded -> archived": (superseded, _replace_key(superseded, "status", "archived")),
            "archived -> approved": (archived, APPROVED),
            "superseded edit": (superseded, superseded + "x\n"),
            "block list superseded_by": (
                APPROVED, _replace_key(APPROVED, "status", "superseded").replace("---\n\n#", "superseded_by:\n  - D-009\n---\n\n#", 1),
            ),
        }
        for name, (base, head) in cases.items():
            with self.subTest(name):
                self.assertFalse(allowed_lifecycle_change(base, head))


class ReviewLogParserTests(unittest.TestCase):
    def test_append_and_refresh_are_allowed(self) -> None:
        appended = REVIEW_LOG.replace("\n\nTrailing", "\n" + ROW.rstrip("\n") + "\n\nTrailing")
        self.assertTrue(allowed_review_log_append(REVIEW_LOG, appended))
        refreshed = appended.replace("last_updated: 2026-09-22", "last_updated: 2026-10-01")
        self.assertTrue(allowed_review_log_append(REVIEW_LOG, refreshed))
        self.assertTrue(allowed_review_log_append(REVIEW_LOG, REVIEW_LOG))

    def test_append_to_an_empty_table(self) -> None:
        empty = REVIEW_LOG.replace("| 2026-09-22 | [[Home]] | Pat | confirmed | — | 2027-01-01 | ok |\n", "")
        appended = empty.replace("|---|---|---|---|---|---|---|\n", "|---|---|---|---|---|---|---|\n" + ROW)
        self.assertTrue(allowed_review_log_append(empty, appended))

    def test_rewrites_are_blocked(self) -> None:
        cases = {
            "row edited": REVIEW_LOG.replace("confirmed", "changed"),
            "row deleted": REVIEW_LOG.replace("| 2026-09-22 | [[Home]] | Pat | confirmed | — | 2027-01-01 | ok |\n", ""),
            "row inserted before": REVIEW_LOG.replace("|---|---|---|---|---|---|---|\n", "|---|---|---|---|---|---|---|\n" + ROW),
            "intro edited": REVIEW_LOG.replace("Append-only operational", "Editable"),
            "trailer edited": REVIEW_LOG.replace("Trailing paragraph.", "Other paragraph."),
            "title edited": REVIEW_LOG.replace('title: "Review log"', 'title: "Log"'),
            "header renamed": REVIEW_LOG.replace("| Note |", "| Notes |"),
            "front matter removed": REVIEW_LOG.split("---\n", 2)[2],
        }
        for name, head in cases.items():
            with self.subTest(name):
                self.assertFalse(allowed_review_log_append(REVIEW_LOG, head))

    def test_header_must_be_followed_by_a_separator_line(self) -> None:
        truncated = REVIEW_LOG.split("|---|")[0].rstrip("\n")
        self.assertFalse(allowed_review_log_append(truncated, truncated))


class SeededPropertyTests(unittest.TestCase):
    """Random but reproducible inputs: append is allowed, any rewrite is not."""

    def _log(self, rows: list[str]) -> str:
        head, tail = REVIEW_LOG.split("| 2026-09-22 | [[Home]]", 1)
        tail = tail.split("\n", 1)[1]
        return head + "".join(rows) + tail

    def _row(self, rng: random.Random) -> str:
        day = rng.randint(1, 28)
        outcome = rng.choice(["confirmed", "changed", "retired", "deferred"])
        note = "".join(rng.choice("abcdefgh ") for _ in range(rng.randint(0, 12))).strip()
        return f"| 2026-10-{day:02d} | [[Note {rng.randint(1, 50)}]] | R{rng.randint(1, 9)} | {outcome} | — | 2027-01-01 | {note} |\n"

    def test_review_log_properties(self) -> None:
        rng = random.Random(20261003)
        for iteration in range(300):
            base_rows = [self._row(rng) for _ in range(rng.randint(0, 6))]
            base = self._log(base_rows)
            appended = base_rows + [self._row(rng) for _ in range(rng.randint(0, 4))]
            with self.subTest(iteration=iteration, op="append"):
                self.assertTrue(allowed_review_log_append(base, self._log(appended)))
            if not base_rows:
                continue
            mutated = list(base_rows)
            op = rng.choice(["edit", "delete", "swap", "insert"])
            index = rng.randrange(len(mutated))
            if op == "edit":
                mutated[index] = mutated[index].replace("|", "| x", 2)
            elif op == "delete":
                del mutated[index]
            elif op == "swap" and len(mutated) > 1 and mutated[0] != mutated[-1]:
                mutated[0], mutated[-1] = mutated[-1], mutated[0]
            else:
                mutated.insert(index, self._row(rng))
                if mutated[index] == mutated[index + 1]:
                    continue
            if mutated == base_rows:
                continue
            with self.subTest(iteration=iteration, op=op):
                self.assertFalse(allowed_review_log_append(base, self._log(mutated)))

    def test_lifecycle_properties(self) -> None:
        rng = random.Random(1709)
        frozen = ["title", "owner", "decision_id", "created", "source_ids", "type"]
        for iteration in range(300):
            head = APPROVED
            target = rng.choice(["approved", "superseded", "archived"])
            head = _replace_key(head, "status", target)
            allowed = ["review_by", "last_updated"] if target == "approved" else ["last_updated", "superseded_by"]
            for key in rng.sample(allowed, rng.randint(0, len(allowed))):
                head = _replace_key(head, key, f"2027-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}" if key != "superseded_by" else f"D-{rng.randint(2, 999):03d}")
            with self.subTest(iteration=iteration, target=target):
                self.assertTrue(allowed_lifecycle_change(APPROVED, head))
            key = rng.choice(frozen)
            tampered = _replace_key(head, key, f'"tampered {iteration}"')
            with self.subTest(iteration=iteration, tampered=key):
                self.assertFalse(allowed_lifecycle_change(APPROVED, tampered))


class GitTransitionTests(RepoTestCase):
    def test_unchanged_history_passes(self) -> None:
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_new_records_and_draft_edits_pass(self) -> None:
        self.repo.write("06-decisions/d-003-new.md", APPROVED.replace("D-001", "D-003"))
        self.repo.write("06-decisions/d-002-draft.md", DRAFT + "More drafting.\n")
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_semantic_edit_is_blocked(self) -> None:
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED.replace("Keep them in Git.", "Keep them in a wiki."))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [("M", "06-decisions/d-001-use-git.md")])

    def test_metadata_only_lifecycle_moves_pass(self) -> None:
        for status in ("superseded", "archived"):
            with self.subTest(status):
                self.repo.write("06-decisions/d-001-use-git.md", _replace_key(APPROVED, "status", status))
                self.repo.commit(status)
                self.assertEqual(self.repo.blocked(self.base), [])

    def test_superseded_record_is_frozen(self) -> None:
        self.repo.write("06-decisions/d-001-use-git.md", _replace_key(APPROVED, "status", "superseded"))
        base = self.repo.commit("supersede")
        self.repo.write("06-decisions/d-001-use-git.md", _replace_key(APPROVED, "status", "archived"))
        self.repo.commit("archive")
        self.assertEqual(self.repo.blocked(base), [("M", "06-decisions/d-001-use-git.md")])

    def test_rename_of_accepted_record_is_blocked(self) -> None:
        self.repo.git("mv", str(self.record()), str(self.repo.vault / "06-decisions/d-001-renamed.md"))
        self.repo.commit()
        blocked = self.repo.blocked(self.base)
        self.assertEqual(len(blocked), 1)
        self.assertTrue(blocked[0][0].startswith("R"))
        self.assertEqual(blocked[0][1], "06-decisions/d-001-use-git.md -> 06-decisions/d-001-renamed.md")

    def test_rename_of_draft_passes(self) -> None:
        self.repo.git("mv", str(self.repo.vault / "06-decisions/d-002-draft.md"), str(self.repo.vault / "06-decisions/d-002-better.md"))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_move_out_of_the_decisions_folder_is_blocked(self) -> None:
        (self.repo.vault / "archive").mkdir()
        self.repo.git("mv", str(self.record()), str(self.repo.vault / "archive/d-001-use-git.md"))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [("D", "06-decisions/d-001-use-git.md")])

    def test_delete_of_accepted_record_is_blocked_but_draft_delete_passes(self) -> None:
        (self.repo.vault / "06-decisions/d-002-draft.md").unlink()
        self.repo.commit("drop draft")
        self.assertEqual(self.repo.blocked(self.base), [])
        self.record().unlink()
        self.repo.commit("drop accepted")
        self.assertEqual(self.repo.blocked(self.base), [("D", "06-decisions/d-001-use-git.md")])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_type_change_to_symlink_is_blocked(self) -> None:
        self.repo.write("notes/elsewhere.md", APPROVED.replace("Git", "a wiki"))
        self.record().unlink()
        os.symlink("../notes/elsewhere.md", self.record())
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [("T", "06-decisions/d-001-use-git.md")])

    def test_copy_detection_does_not_unlock_or_block(self) -> None:
        self.repo.git("config", "diff.renames", "copies")
        self.repo.write("06-decisions/d-002-draft.md", DRAFT + "edited\n")
        self.repo.write("06-decisions/d-003-copy.md", DRAFT)
        self.repo.commit()
        out = self.repo.git("diff", "--name-status", f"{self.base}...HEAD", cwd=self.repo.vault)
        self.assertRegex(out, r"(?m)^C\d+\t")
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_non_record_files_in_the_folder_are_ignored(self) -> None:
        self.repo.write("06-decisions/README.md", "---\nstatus: approved\n---\nx\n")
        base = self.repo.commit()
        self.repo.write("06-decisions/README.md", "---\nstatus: approved\n---\nchanged\n")
        self.repo.commit()
        self.assertEqual(self.repo.blocked(base), [])

    def test_body_status_line_does_not_unlock_an_accepted_record(self) -> None:
        # Regression: a `status:` line inside a fenced YAML example made the
        # base status ambiguous, so the record was treated as a draft.
        text = APPROVED + "\n```yaml\nstatus: draft\n```\n"
        self.repo.write("06-decisions/d-001-use-git.md", text)
        base = self.repo.commit()
        self.repo.write("06-decisions/d-001-use-git.md", text.replace("Keep them in Git.", "Keep them in a wiki."))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(base), [("M", "06-decisions/d-001-use-git.md")])

    def test_non_ascii_and_quoted_file_names_are_checked(self) -> None:
        # Regression: without -z, Git C-quotes such names ("d-004-caf\303\251.md"),
        # the record pattern no longer matched and the rewrite passed.
        names = ["06-decisions/d-004-café.md", '06-decisions/d-005-say "hi".md', "06-decisions/d-006-tab\there.md"]
        if os.name == "nt":
            names = names[:1]
        for name in names:
            self.repo.write(name, APPROVED)
        base = self.repo.commit()
        for name in names:
            self.repo.write(name, APPROVED + "rewritten\n")
        self.repo.commit()
        self.assertEqual(sorted(self.repo.blocked(base)), sorted(("M", name) for name in names))

    def test_utf8_content_is_compared_independent_of_the_locale(self) -> None:
        self.repo.write("06-decisions/d-007-utf8.md", APPROVED.replace("Use Git", "Użyj Gita — ☃"))
        base = self.repo.commit()
        self.repo.write("06-decisions/d-007-utf8.md", _replace_key(APPROVED.replace("Use Git", "Użyj Gita — ☃"), "status", "superseded"))
        self.repo.commit()
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8=0", str(SCRIPT), "history", "--base", base, "--root", str(self.repo.vault)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ReviewLogGitTests(RepoTestCase):
    def log(self) -> Path:
        return self.repo.vault / "00-context/review-log.md"

    def test_append_passes(self) -> None:
        self.repo.write("00-context/review-log.md", REVIEW_LOG.replace("\n\nTrailing", "\n" + ROW.rstrip("\n") + "\n\nTrailing"))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_rewrite_delete_and_rename_are_blocked(self) -> None:
        self.repo.write("00-context/review-log.md", REVIEW_LOG.replace("confirmed", "retracted"))
        self.repo.commit("rewrite")
        self.assertEqual(self.repo.blocked(self.base), [("M", "00-context/review-log.md")])
        self.log().unlink()
        self.repo.commit("delete")
        self.assertEqual(self.repo.blocked(self.base), [("D", "00-context/review-log.md")])

    def test_newly_created_log_passes(self) -> None:
        self.log().unlink()
        base = self.repo.commit("no log yet")
        self.repo.write("00-context/review-log.md", REVIEW_LOG)
        self.repo.commit("log created")
        self.assertEqual(self.repo.blocked(base), [])


class MergeAndRevisionTests(RepoTestCase):
    def test_merge_commit_that_carries_a_rewrite_is_blocked(self) -> None:
        self.repo.git("checkout", "-qb", "feature")
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED + "sneaky\n")
        self.repo.commit("rewrite on branch")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("notes/unrelated.md", "x\n")
        self.repo.commit("main moves on")
        self.repo.git("merge", "-q", "--no-ff", "feature", "-m", "merge feature")
        self.assertEqual(self.repo.blocked("main~1"), [("M", "06-decisions/d-001-use-git.md")])

    def test_changes_already_on_the_base_branch_are_not_attributed_to_the_pr(self) -> None:
        self.repo.git("checkout", "-qb", "feature")
        self.repo.write("notes/feature.md", "x\n")
        self.repo.commit("feature work")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED + "fixed upstream\n")
        self.repo.commit("upstream change")
        self.repo.git("checkout", "-q", "feature")
        self.repo.git("merge", "-q", "--no-ff", "main", "-m", "sync main")
        # Three-dot semantics: the PR is judged against the merge base only.
        self.assertEqual(self.repo.blocked("main", "feature"), [])

    def test_unknown_base_is_an_error_not_a_pass(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            self.repo.blocked("does-not-exist")
        self.assertIn("unknown Git revision: does-not-exist", ctx.exception.stderr)

    def test_option_shaped_revision_is_refused(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            self.repo.blocked("--output=/tmp/x")
        self.assertIn("unknown Git revision", ctx.exception.stderr)

    def test_outside_a_work_tree_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(subprocess.CalledProcessError) as ctx:
                changed_records("HEAD", "HEAD", td)
        self.assertIn("not inside a Git work tree", ctx.exception.stderr)

    def test_shallow_clone_without_merge_base_explains_the_fix(self) -> None:
        self.repo.git("checkout", "-qb", "feature")
        self.repo.write("notes/feature.md", "x\n")
        self.repo.commit("feature")
        self.repo.git("checkout", "-q", "main")
        self.repo.write("notes/main.md", "y\n")
        self.repo.commit("main")
        with tempfile.TemporaryDirectory() as td:
            clone = Path(td) / "clone"
            subprocess.run(
                ["git", "clone", "-q", "--depth", "1", "--no-single-branch", self.repo.top.as_uri(), str(clone)],
                check=True, capture_output=True,
            )
            with self.assertRaises(subprocess.CalledProcessError) as ctx:
                changed_records("origin/main", "origin/feature", str(clone))
        self.assertIn("shallow", ctx.exception.stderr)
        self.assertIn("fetch-depth: 0", ctx.exception.stderr)


class NestedVaultTests(RepoTestCase):
    vault_dir = "docs/ledger"

    def test_root_flag_checks_the_nested_vault(self) -> None:
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED + "rewritten\n")
        self.repo.write("00-context/review-log.md", REVIEW_LOG.replace("confirmed", "retracted"))
        self.repo.commit()
        self.assertEqual(
            sorted(self.repo.blocked(self.base)),
            [("M", "00-context/review-log.md"), ("M", "06-decisions/d-001-use-git.md")],
        )

    def test_nested_lifecycle_change_reads_the_right_blob(self) -> None:
        self.repo.write("06-decisions/d-001-use-git.md", _replace_key(APPROVED, "status", "superseded"))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_running_inside_the_vault_without_root(self) -> None:
        # Regression: without --root the Git object paths lacked the vault
        # prefix, `git show` failed, and every record looked mutable.
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED + "rewritten\n")
        self.repo.commit()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "history", "--base", self.base],
            cwd=self.repo.vault, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("d-001-use-git.md", proc.stderr)

    def test_wrong_root_warns_instead_of_silently_passing(self) -> None:
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED + "rewritten\n")
        self.repo.commit()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = immutability.main(["--base", self.base, "--root", str(self.repo.top)])
        self.assertEqual(code, 0)
        self.assertIn("no decision records or review log", err.getvalue())


class MainEntryTests(RepoTestCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = immutability.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_json_pass_and_fail(self) -> None:
        code, out, _ = self.run_main("--base", self.base, "--root", str(self.repo.vault), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["passed"], True)
        self.repo.write("06-decisions/d-001-use-git.md", APPROVED + "x\n")
        self.repo.commit()
        code, out, _ = self.run_main("--base", self.base, "--root", str(self.repo.vault), "--json")
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(payload["contract_version"], 1)
        self.assertEqual(payload["blocked"], [{"status": "M", "path": "06-decisions/d-001-use-git.md"}])

    def test_human_output(self) -> None:
        code, out, _ = self.run_main("--base", self.base, "--root", str(self.repo.vault))
        self.assertEqual((code, out.strip()), (0, "history: immutable reasoning unchanged; review log append-only"))
        self.record().unlink()
        self.repo.commit()
        code, _, err = self.run_main("--base", self.base, "--root", str(self.repo.vault))
        self.assertEqual(code, 1)
        self.assertIn("Supersede; do not rewrite", err)
        self.assertIn("D\t06-decisions/d-001-use-git.md", err)

    def test_git_errors_exit_2(self) -> None:
        code, _, err = self.run_main("--base", "nope", "--root", str(self.repo.vault))
        self.assertEqual(code, 2)
        self.assertIn("unknown Git revision: nope", err)

    def test_missing_git_exits_2(self) -> None:
        with mock.patch.object(immutability.subprocess, "run", side_effect=FileNotFoundError("git")):
            code, _, err = self.run_main("--base", self.base, "--root", str(self.repo.vault))
        self.assertEqual(code, 2)
        self.assertIn("git is not installed", err)

    def test_blob_that_vanishes_between_diff_and_show_fails_closed(self) -> None:
        self.repo.write("06-decisions/d-001-use-git.md", _replace_key(APPROVED, "status", "superseded"))
        self.repo.write("00-context/review-log.md", REVIEW_LOG.replace("\n\nTrailing", "\n" + ROW.rstrip("\n") + "\n\nTrailing"))
        self.repo.commit()
        real = immutability.git

        def flaky(*args: str, root: str | None = None) -> str:
            if args[0] == "show" and not args[1].startswith(self.base):
                raise subprocess.CalledProcessError(128, ["git", *args])
            return real(*args, root=root)

        with mock.patch.object(immutability, "git", side_effect=flaky):
            blocked = self.repo.blocked(self.base)
        self.assertEqual(sorted(blocked), [("M", "00-context/review-log.md"), ("M", "06-decisions/d-001-use-git.md")])


@unittest.skipUnless(shutil.which("git"), "git required")
class DiffParserTests(unittest.TestCase):
    def test_nul_separated_entries(self) -> None:
        out = "M\x0006-decisions/a b.md\x00R087\x00old.md\x00new.md\x00D\x00x.md\x00"
        self.assertEqual(
            immutability._diff_entries(out),
            [("M", "06-decisions/a b.md", "06-decisions/a b.md"), ("R087", "old.md", "new.md"), ("D", "x.md", "x.md")],
        )

    def test_truncated_output_fails_closed(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            immutability._diff_entries("R100\x00old.md\x00")


if __name__ == "__main__":
    unittest.main()
