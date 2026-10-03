"""`whykit adopt` against the folders people actually have.

Real adoption sources are messy: notes without front matter, two ADR folders
that both claim D-001, a PDF renamed to `.md`, a 10 MB meeting dump, symlinks
into somebody's home directory. Each case here checks one thing the inventory
must get right, and that a failed `--write` leaves nothing half-staged behind.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _vaults import fresh_vault  # noqa: E402

from whykit import adopt as adopt_module  # noqa: E402
from whykit.adopt import Candidate, adopt, scan, score_adoption  # noqa: E402

TODAY = dt.date(2026, 9, 17)
WORDS = "Substantive prose about why the team chose this path and what it costs. " * 3

READY_NOTE = (
    "---\ntitle: Note\ntype: guide\nstatus: draft\nowner: Example owner\n"
    "created: 2026-09-17\nlast_updated: 2026-09-17\n"
    "source_of_truth: false\nsensitivity: internal\n---\n\n# Note\n\n" + WORDS
)


def decision(decision_id: str, title: str) -> str:
    return (
        f"---\ntitle: \"{decision_id} — {title}\"\ntype: decision\ndecision_id: {decision_id}\n"
        "status: approved\nowner: Example owner\ncreated: 2026-01-01\nlast_updated: 2026-01-01\n"
        "review_by: 2027-01-01\nsource_of_truth: false\nsensitivity: internal\n---\n\n"
        f"# {title}\n\n## Context\n\n{WORDS}\n\n## Decision\n\n{WORDS}\n"
    )


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
    }


class AdoptTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.source = self.base / "old-docs"
        self.source.mkdir()
        self.vault = self.base / "vault"
        fresh_vault(self.vault, "--minimal")

    def put(self, relative: str, content: str | bytes) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
        return path

    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = adopt_module.main([str(self.source), "--into", str(self.vault), *argv])
        return code, out.getvalue(), err.getvalue()

    def symlink(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")


class MessyInventoryTests(AdoptTestCase):
    def test_classification_of_a_messy_folder(self) -> None:
        self.put("plain.md", "# Plain\n\n" + WORDS)
        self.put("ready.md", READY_NOTE)
        self.put("crlf.md", ("# Windows\r\n\r\n" + WORDS).replace("\n", "\r\n"))
        self.put("bom.md", "﻿" + READY_NOTE)
        self.put("empty.md", "")
        self.put("whitespace.md", "  \n\t\n")
        self.put("stub.md", "# Only a heading\n\n## And another\n\nTODO\n")
        self.put("z-copy/plain.md", "# Plain\n\n" + WORDS)
        self.put("adr/0007-use-queues.md", "# Use queues\n\n" + WORDS)
        self.put("nygard.md", "# Title\n\n## Status\n\nAccepted\n\n## Context\n\n" + WORDS + "\n\n## Decision\n\n" + WORDS)
        self.put("broken-front.md", "---\ntitle: [unclosed\n---\n\n# Broken\n\n" + WORDS)
        self.put("unclosed-front.md", "---\ntitle: x\n\n# Never closed\n\n" + WORDS)
        self.put("bad-date.md", READY_NOTE.replace("created: 2026-09-17", "created: 2026-02-31"))
        by_name = {c.relative: c for c in scan(self.source)}

        self.assertEqual(by_name["plain.md"].assessment, "useful")
        self.assertFalse(by_name["plain.md"].has_front_matter)
        self.assertTrue(by_name["ready.md"].whykit_ready)
        self.assertTrue(by_name["bom.md"].whykit_ready)
        self.assertTrue(by_name["bom.md"].has_front_matter)
        self.assertEqual(by_name["crlf.md"].assessment, "useful")
        self.assertEqual(by_name["empty.md"].assessment, "empty")
        self.assertEqual(by_name["whitespace.md"].assessment, "empty")
        self.assertEqual(by_name["stub.md"].assessment, "heading-only")
        self.assertEqual(by_name["z-copy/plain.md"].assessment, "duplicate")
        self.assertEqual(by_name["z-copy/plain.md"].duplicate_of, "plain.md")
        self.assertTrue(by_name["adr/0007-use-queues.md"].looks_like_decision)
        self.assertTrue(by_name["nygard.md"].looks_like_decision)
        self.assertFalse(by_name["plain.md"].looks_like_decision)
        for name in ("broken-front.md", "unclosed-front.md", "bad-date.md", "plain.md"):
            with self.subTest(name):
                self.assertFalse(by_name[name].whykit_ready)
        self.assertEqual(by_name["plain.md"].sha256, hashlib.sha256(self.put("plain.md", "# Plain\n\n" + WORDS).read_bytes()).hexdigest())

    def test_short_structured_files_are_staged_not_dropped_as_stubs(self) -> None:
        # Regression: anything under 15 words was "heading-only" and never
        # staged, so terse ADRs and front-matter notes vanished silently.
        self.put("0003-use-utc.md", "# 3. Use UTC\n\n## Status\n\nAccepted\n")
        self.put("short-adr.md", "# Pick a DB\n\n## Context\n\nSlow.\n\n## Decision\n\nPostgres.\n")
        self.put("front.md", "---\ntitle: Tiny\n---\n\nShort note.\n")
        self.put("front-only.md", "---\ntitle: Placeholder\n---\n")
        self.put("stub.md", "# Just a heading\n\nTODO\n")
        by_name = {c.relative: c.assessment for c in scan(self.source)}
        self.assertEqual(by_name, {
            "0003-use-utc.md": "useful", "short-adr.md": "useful", "front.md": "useful",
            "front-only.md": "useful", "stub.md": "heading-only",
        })
        adr_only = {c.relative: c.assessment for c in scan(self.source, profile="adr-only")}
        self.assertEqual(adr_only["front.md"], "unsupported")
        self.assertEqual(adr_only["short-adr.md"], "useful")
        _, _, migration, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        for name in ("0003-use-utc.md", "short-adr.md", "front.md", "front-only.md"):
            self.assertTrue((migration.parent / name).is_file(), name)
        self.assertFalse((migration.parent / "stub.md").exists())

    def test_destinations_follow_the_profile(self) -> None:
        self.put("note.md", "# Note\n\n" + WORDS)
        self.put("0001-adr.md", "# ADR\n\n" + WORDS)
        self.put("stub.md", "# Stub\n")
        generic = {c.relative: c for c in scan(self.source)}
        self.assertEqual(generic["note.md"].destination_for("generic"), "notes/ or a workstream (human choice)")
        self.assertEqual(generic["note.md"].destination_for("obsidian-loose"), "notes/ (normalize front matter)")
        self.assertEqual(generic["note.md"].destination_for("adr-only"), "06-decisions/ (assign D-NNN)")
        self.assertEqual(generic["0001-adr.md"].destination_for("generic"), "06-decisions/ (assign D-NNN)")
        self.assertEqual(generic["stub.md"].destination_for("generic"), "—")
        adr_only = {c.relative: c.assessment for c in scan(self.source, profile="adr-only")}
        self.assertEqual(adr_only, {"0001-adr.md": "useful", "note.md": "unsupported", "stub.md": "heading-only"})

    def test_skip_dirs_and_non_markdown_are_left_out(self) -> None:
        self.put("keep.md", "# Keep\n\n" + WORDS)
        for skipped in (".git", ".obsidian", "node_modules", ".import-staging", "build"):
            self.put(f"{skipped}/inner.md", "# Skip\n\n" + WORDS)
        self.put("diagram.png", b"\x89PNG\r\n\x1a\n")
        self.put("notes.txt", WORDS)
        (self.source / "folder.md").mkdir()  # a directory with a Markdown name
        self.assertEqual([c.relative for c in scan(self.source)], ["keep.md"])
        code, out, _ = self.main("--json")
        self.assertEqual(code, 0)
        note = json.loads(out)["scope_note"]
        self.assertIn("2 other file(s) excluded", note)
        self.assertIn("diagram.png", note)
        self.assertNotIn("inner.md", note)

    def test_binary_files_named_markdown_are_not_inventoried(self) -> None:
        self.put("invalid-utf8.md", b"\xff\xfe\x00\x01")
        # NUL bytes are valid UTF-8 but no Markdown editor writes them; a
        # binary renamed to .md must not be staged as a note.
        self.put("renamed-pdf.md", b"%PDF-1.7\n" + b"\x00" * 64 + ("word " * 40).encode())
        self.put("ok.md", "# Ok\n\n" + WORDS)
        self.assertEqual([c.relative for c in scan(self.source)], ["ok.md"])
        code, out, _ = self.main("--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("2 Markdown file(s) unreadable, binary or non-UTF-8", payload["scope_note"])
        self.assertIn("renamed-pdf.md", payload["scope_note"])

    def test_huge_file_is_hashed_and_scored_in_bounded_time(self) -> None:
        big = self.put("meeting-dump.md", "# Dump\n\n" + "minutes of a very long meeting " * 350_000)
        unclosed = self.put("unclosed.md", "---\n" + "key: value\n" * 200_000)
        started = time.monotonic()
        by_name = {c.relative: c for c in scan(self.source)}
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(by_name["meeting-dump.md"].bytes, big.stat().st_size)
        self.assertEqual(by_name["meeting-dump.md"].sha256, hashlib.sha256(big.read_bytes()).hexdigest())
        self.assertGreater(by_name["meeting-dump.md"].bytes, 10_000_000)
        self.assertFalse(by_name["unclosed.md"].whykit_ready)
        self.assertEqual(by_name["unclosed.md"].bytes, unclosed.stat().st_size)

    def test_symlinks_never_pull_in_material_from_outside(self) -> None:
        outside = self.base / "private"
        outside.mkdir()
        (outside / "secret.md").write_text("# Secret\n\n" + WORDS, encoding="utf-8")
        self.put("inside.md", "# Inside\n\n" + WORDS)
        self.symlink(self.source / "linked-file.md", outside / "secret.md")
        self.symlink(self.source / "linked-dir", outside)
        self.symlink(self.source / "dangling.md", self.base / "missing.md")
        (self.source / "sub").mkdir()
        self.symlink(self.source / "sub" / "loop", self.source)
        self.symlink(self.source / "alias.md", self.source / "inside.md")
        relatives = {c.relative: c for c in scan(self.source)}
        self.assertNotIn("linked-file.md", relatives)
        self.assertFalse(any("secret" in name for name in relatives))
        self.assertNotIn("dangling.md", relatives)
        # A link inside the tree is the same bytes, so one of the pair is a duplicate.
        if "alias.md" in relatives:
            self.assertEqual(
                sorted(relatives[name].assessment for name in ("alias.md", "inside.md")), ["duplicate", "useful"],
            )
        code, out, _ = self.main("--json")
        self.assertEqual(code, 0)
        self.assertNotIn("secret", out)


class IdConflictTests(AdoptTestCase):
    def test_two_sources_claiming_the_same_decision_id(self) -> None:
        self.put("team-a/d-001-queues.md", decision("D-001", "Use queues"))
        self.put("team-b/d-001-cron.md", decision("D-001", "Use cron"))
        self.put("team-b/d-002-other.md", decision("D-002", "Other"))
        score = score_adoption(scan(self.source))
        self.assertEqual(len(score["id_conflicts"]), 1)
        self.assertIn("D-001", score["id_conflicts"][0])
        self.assertIn("team-a/d-001-queues.md", score["id_conflicts"][0])
        self.assertIn("team-b/d-001-cron.md", score["id_conflicts"][0])

    def test_byte_identical_copies_are_duplicates_not_conflicts(self) -> None:
        self.put("a/d-001.md", decision("D-001", "Same"))
        self.put("b/d-001.md", decision("D-001", "Same"))
        self.assertEqual(score_adoption(scan(self.source))["id_conflicts"], [])

    def test_id_already_used_in_the_target_vault(self) -> None:
        existing = self.vault / "06-decisions" / "d-001-existing.md"
        existing.write_text(decision("D-001", "Existing"), encoding="utf-8")
        self.put("d-001-imported.md", decision("D-001", "Imported"))
        self.put("d-009-free.md", decision("D-009", "Free"))
        code, out, _ = self.main("--json")
        self.assertEqual(code, 0)
        conflicts = json.loads(out)["score"]["id_conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("06-decisions/d-001-existing.md", conflicts[0])
        code, out, _ = self.main()
        self.assertIn("ID conflict:", out)

    def test_unreadable_or_linked_vault_records_are_skipped(self) -> None:
        decisions = self.vault / "06-decisions"
        (decisions / "d-002-binary.md").write_bytes(b"\xff\xfe")
        (decisions / "d-003-dir.md").mkdir()
        outside = self.base / "outside.md"
        outside.write_text(decision("D-003", "Outside"), encoding="utf-8")
        self.symlink(decisions / "d-003-link.md", outside)
        self.put("d-003-imported.md", decision("D-003", "Imported"))
        self.assertEqual(score_adoption(scan(self.source), self.vault)["id_conflicts"], [])
        shutil.rmtree(decisions)
        self.assertEqual(score_adoption(scan(self.source), self.vault)["id_conflicts"], [])

    def test_conflicts_are_listed_in_the_migration_report(self) -> None:
        self.put("a/x.md", decision("D-004", "One"))
        self.put("b/y.md", decision("D-004", "Two"))
        _, _, migration, _ = adopt(self.source, self.vault, write=True, owner="Example owner", today=TODAY)
        text = migration.read_text(encoding="utf-8")
        self.assertIn("## ID conflicts", text)
        self.assertIn("D-004", text)


class WriteTests(AdoptTestCase):
    def populate(self) -> None:
        self.put("note.md", "# Note\n\n" + WORDS)
        self.put("deep/nested/adr/0001-use-git.md", "# Use Git\n\n" + WORDS)
        self.put("stub.md", "# Stub\n")
        self.put("z-dup.md", "# Note\n\n" + WORDS)

    def test_dry_run_writes_nothing_at_all(self) -> None:
        self.populate()
        before = tree(self.vault)
        candidates, record, migration, score = adopt(self.source, self.vault, write=False, owner="x", today=TODAY)
        self.assertIsNone(record)
        self.assertIsNone(migration)
        self.assertEqual(score["files"], 4)
        code, out, _ = self.main()
        self.assertEqual(code, 0)
        self.assertIn("Dry run. Nothing was written.", out)
        self.assertEqual(tree(self.vault), before)
        self.assertFalse((self.vault / ".import-staging").exists())
        self.assertFalse((self.vault / ".whykit").exists())

    def test_write_stages_byte_identical_copies(self) -> None:
        self.populate()
        crlf = self.put("windows.md", ("# Windows\n\n" + WORDS).replace("\n", "\r\n"))
        candidates, record, migration, score = adopt(self.source, self.vault, write=True, owner="Example owner", today=TODAY)
        batch = self.vault / ".import-staging" / "2026-09-17"
        staged = sorted(p.relative_to(batch).as_posix() for p in batch.rglob("*") if p.is_file())
        self.assertEqual(staged, ["MIGRATION.md", "deep/nested/adr/0001-use-git.md", "note.md", "windows.md"])
        self.assertEqual((batch / "windows.md").read_bytes(), crlf.read_bytes())
        self.assertEqual(record, self.vault / "notes" / "ingestion-2026-09-17.md")
        text = record.read_text(encoding="utf-8")
        for candidate in candidates:
            self.assertIn(candidate.sha256, text)
        self.assertEqual(migration, batch / "MIGRATION.md")
        self.assertFalse(any(p.name.startswith(".partial") for p in (self.vault / ".import-staging").iterdir()))

    def test_rerun_is_non_destructive(self) -> None:
        self.populate()
        _, first_record, first_migration, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        snapshot = tree(self.vault / ".import-staging" / "2026-09-17")
        first_text = first_record.read_text(encoding="utf-8")
        _, second_record, second_migration, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assertEqual(second_record.name, "ingestion-2026-09-17-2.md")
        self.assertEqual(second_migration.parent.name, "2026-09-17-2")
        self.assertEqual(tree(self.vault / ".import-staging" / "2026-09-17"), snapshot)
        self.assertEqual(first_record.read_text(encoding="utf-8"), first_text)
        _, third_record, _, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assertEqual(third_record.name, "ingestion-2026-09-17-3.md")

    def test_record_name_collision_without_batch_collision(self) -> None:
        self.populate()
        (self.vault / "notes" / "ingestion-2026-09-17.md").write_text("hand-written\n", encoding="utf-8")
        _, record, _, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assertEqual(record.name, "ingestion-2026-09-17-2.md")
        self.assertEqual((self.vault / "notes" / "ingestion-2026-09-17.md").read_text(encoding="utf-8"), "hand-written\n")

    def test_source_migration_file_is_not_overwritten_by_the_report(self) -> None:
        self.put("MIGRATION.md", "# Our own migration notes\n\n" + WORDS)
        _, _, migration, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assertEqual(migration.name, "MIGRATION-2.md")
        self.assertIn("Our own migration notes", (migration.parent / "MIGRATION.md").read_text(encoding="utf-8"))

    def test_legacy_research_layout_receives_the_record(self) -> None:
        self.populate()
        (self.vault / "07-research").mkdir()
        _, record, _, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assertEqual(record.parent, self.vault / "07-research" / "sources")

    def test_symlinked_staging_directory_is_refused(self) -> None:
        self.populate()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        self.symlink(self.vault / ".import-staging", elsewhere)
        code, _, err = self.main("--write")
        self.assertEqual(code, 2)
        self.assertIn("adoption failed", err)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_cli_write_output(self) -> None:
        self.populate()
        code, out, _ = self.main("--write", "--owner", "Example owner")
        self.assertEqual(code, 0)
        self.assertIn("Staged under", out)
        self.assertIn("Ingestion record: notes/ingestion-", out)
        self.assertIn("look like existing decision records", out)
        code, out, _ = self.main("--write", "--json")
        payload = json.loads(out)
        self.assertTrue(payload["write"])
        self.assertTrue(payload["migration"].startswith(".import-staging/"))


class RollbackTests(AdoptTestCase):
    """A failed --write must leave the vault exactly as it found it."""

    def populate(self) -> list[Candidate]:
        for index in range(5):
            self.put(f"note-{index}.md", f"# Note {index}\n\n" + WORDS)
        return scan(self.source)

    def assert_untouched(self, before: dict[str, bytes]) -> None:
        after = {k: v for k, v in tree(self.vault).items() if not k.startswith(".whykit/")}
        self.assertEqual(after, {k: v for k, v in before.items() if not k.startswith(".whykit/")})
        staging = self.vault / ".import-staging"
        self.assertEqual(list(staging.iterdir()) if staging.exists() else [], [])

    def test_source_changing_mid_write_rolls_back_the_batch(self) -> None:
        candidates = self.populate()
        before = tree(self.vault)
        # Change a file that sorts after others have already been staged.
        candidates[3].path.write_text("# Changed\n\n" + WORDS + "edited\n", encoding="utf-8")
        with mock.patch.object(adopt_module, "scan", return_value=candidates):
            with self.assertRaisesRegex(RuntimeError, "source changed during adoption: note-3.md"):
                adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assert_untouched(before)

    def test_source_vanishing_mid_write_rolls_back(self) -> None:
        candidates = self.populate()
        before = tree(self.vault)
        candidates[2].path.unlink()
        with mock.patch.object(adopt_module, "scan", return_value=candidates):
            with self.assertRaises(OSError):
                adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assert_untouched(before)

    def test_failure_writing_the_ingestion_record_rolls_back_the_batch(self) -> None:
        self.populate()
        before = tree(self.vault)
        real = adopt_module.atomic_write_text

        def fail_record(path: Path, text: str, **kwargs) -> None:
            if path.name.startswith("ingestion-"):
                raise OSError("disk full")
            real(path, text, **kwargs)

        with mock.patch.object(adopt_module, "atomic_write_text", side_effect=fail_record):
            with self.assertRaisesRegex(OSError, "disk full"):
                adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assert_untouched(before)

    def test_failure_writing_the_migration_report_rolls_back(self) -> None:
        self.populate()
        before = tree(self.vault)
        real = adopt_module.atomic_write_text

        def fail_migration(path: Path, text: str, **kwargs) -> None:
            if path.name.startswith("MIGRATION"):
                raise OSError("read-only file system")
            real(path, text, **kwargs)

        with mock.patch.object(adopt_module, "atomic_write_text", side_effect=fail_migration):
            code = None
            with mock.patch.object(adopt_module, "scan", wraps=adopt_module.scan):
                code, _, err = self.main("--write")
        self.assertEqual(code, 2)
        self.assertIn("read-only file system", err)
        self.assert_untouched(before)

    def test_leftover_partial_batch_from_a_crash_is_ignored(self) -> None:
        self.populate()
        partial = self.vault / ".import-staging" / ".partial-deadbeef"
        partial.mkdir(parents=True)
        (partial / "note-0.md").write_text("half\n", encoding="utf-8")
        _, record, migration, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        self.assertEqual(migration.parent.name, "2026-09-17")
        self.assertNotIn(".partial", record.read_text(encoding="utf-8"))


class CliGuardTests(AdoptTestCase):
    def test_missing_source_and_vault(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(adopt_module.main([str(self.base / "nope"), "--into", str(self.vault)]), 2)
            self.assertEqual(adopt_module.main([str(self.source), "--into", str(self.base)]), 2)
        self.assertIn("not a directory", err.getvalue())
        self.assertIn("no WhyKit vault found", err.getvalue())

    def test_source_inside_the_vault_is_refused(self) -> None:
        inner = self.vault / "notes" / "inbox"
        inner.mkdir()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(adopt_module.main([str(inner), "--into", str(self.vault)]), 2)
        self.assertIn("overlapping", err.getvalue())

    def test_empty_source(self) -> None:
        code, out, _ = self.main()
        self.assertEqual(code, 0)
        self.assertIn("no Markdown found", out)

    def test_nearest_vault_is_used_without_into(self) -> None:
        self.put("note.md", "# Note\n\n" + WORDS)
        cwd = os.getcwd()
        os.chdir(self.vault / "notes")
        self.addCleanup(os.chdir, cwd)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(adopt_module.main([str(self.source), "--json"]), 0)
        self.assertEqual(Path(json.loads(out.getvalue())["vault"]), self.vault)


class SeededAdoptPropertyTests(AdoptTestCase):
    """Random folders: every inventoried file is accounted for exactly once."""

    def test_inventory_invariants(self) -> None:
        rng = random.Random(4242)
        bodies = ["", "# Stub\n", "# Note\n\n" + WORDS, READY_NOTE, decision("D-010", "Ten"), "# Other\n\n" + WORDS[::-1]]
        for _ in range(40):
            name = "/".join(rng.choice(["a", "b", "c d", "é"]) for _ in range(rng.randint(0, 2)))
            name = (name + "/" if name else "") + f"f{rng.randint(0, 999)}.md"
            self.put(name, rng.choice(bodies))
        candidates = scan(self.source)
        score = score_adoption(candidates)
        self.assertEqual(score["files"], len(candidates))
        self.assertEqual(len({c.relative for c in candidates}), len(candidates))
        self.assertEqual(
            score["useful"] + score["duplicates"] + score["empty_or_stub"] + score["unsupported"], score["files"],
        )
        firsts = {c.sha256: c.relative for c in candidates if c.assessment != "duplicate"}
        for candidate in candidates:
            if candidate.assessment == "duplicate":
                self.assertEqual(candidate.duplicate_of, firsts[candidate.sha256])
        _, record, migration, _ = adopt(self.source, self.vault, write=True, owner="x", today=TODAY)
        batch = migration.parent
        for candidate in candidates:
            staged = batch / candidate.relative
            if candidate.assessment == "useful":
                self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), candidate.sha256)
            else:
                self.assertFalse(staged.exists() and candidate.relative != migration.name)


if __name__ == "__main__":
    unittest.main()
