"""Migration loss is measured against an explicit source, never guessed from code."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _jsonschema import validate
from _vaults import fresh_vault
from test_machine_contract import call
from whykit.adopt import compare_migration, scan

ROOT = Path(__file__).resolve().parents[1]


class PreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.source, self.target, self.vault = (self.base / name for name in ("source", "target", "vault"))
        self.source.mkdir()
        self.target.mkdir()
        fresh_vault(self.vault, "--minimal")

    def put(self, name: str, before: bytes, after: bytes | None = None) -> None:
        for root, data in ((self.source, before), (self.target, before if after is None else after)):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def report(self) -> dict:
        return compare_migration(self.source, self.target, scan(self.source))

    def test_byte_preservation_bom_crlf_unicode_and_code_examples(self) -> None:
        self.put("AGENTS.md", '\ufeff# Rules\r\n\r\nUse [[notes/Żółć|Label]] and E-001, D-001.\r\n'.encode())
        self.put("examples.MD", b"Code example: `[[not-a-real-link]]`.\n\n```md\n[[another-example]] E-999\n```\n",
                 b"Code example: `not-a-real-link`.\n\n```md\nanother-example\n```\n")
        report = self.report()
        self.assertTrue(report["passed"])
        self.assertTrue(report["files"][0]["unchanged"])
        self.assertEqual(report["files"][1]["missing_wikilinks"], [])

    def test_rewritten_backtick_link_alias_anchor_and_duplicate_occurrence_are_losses(self) -> None:
        before = b"Read [[notes/a#Detail|Label]] twice: [[notes/a#Detail|Label]]. E-001 D-001.\n"
        self.put("AGENTS.md", before, b"Read `notes/a` twice: [[notes/a#Detail|Label]]. D-001.\n")
        entry = self.report()["files"][0]
        self.assertEqual(entry["missing_wikilinks"], ["[[notes/a#Detail|Label]]"])
        self.assertEqual(entry["missing_ids"], ["E-001"])
        self.assertEqual(entry["issues"], ["wikilinks_lost", "ids_lost"])

    def test_new_frontmatter_and_escaped_table_pipe_do_not_lose_links(self) -> None:
        self.put("note.md", b"Read [[notes/a|Label]] and E-001.\n",
                 b"---\ntitle: Normalized\nsource_ids: [E-001]\n---\nRead [[notes/a\\|Label]] and E-001.\n")
        self.assertTrue(self.report()["passed"])
        self.put("unicode.md", "Read [[notes/Z\u0307|Label]].\n".encode(), "Read [[notes/Ż|Label]].\n".encode())
        self.assertTrue(self.report()["passed"])

    def test_non_utf8_and_binary_targets_are_failures(self) -> None:
        self.put("note.md", b"Original note words.\n", b"\xff")
        self.assertEqual(self.report()["files"][0]["issues"], ["unreadable_file"])
        (self.target / "note.md").write_bytes(b"\0")
        self.assertEqual(self.report()["files"][0]["issues"], ["unreadable_file"])

    def test_native_declarations_cannot_be_replaced_by_body_mentions(self) -> None:
        self.put("decision.md", b"---\ndecision_id: D-001\nsource_ids: [E-001]\n---\nD-001 uses E-001.\n",
                 b"---\ndecision_id: D-002\nsource_ids: []\n---\nOld D-001 mentioned E-001.\n")
        entry = self.report()["files"][0]
        self.assertEqual(entry["missing_ids"], [])
        self.assertEqual(entry["issues"], ["decision_id_changed", "source_ids_lost"])

    def test_review_rows_allow_append_and_padding_but_reject_rewrite(self) -> None:
        text = (ROOT / "examples/northline/00-context/review-log.md").read_bytes()
        padded = text.replace(b"| Date | Target |", b"| Date    | Target    |")
        self.put("00-context/review-log.md", text, padded)
        self.assertTrue(self.report()["passed"])
        self.assertEqual(self.report()["files"][0]["review_history"], "preserved")
        path = self.target / "00-context/review-log.md"
        path.write_bytes(padded + b"| 2026-09-17 | [[06-decisions/d-003-no-paid]] | Example reviewer | confirmed | 2026-10-06 | 2026-11-06 | Rechecked. |\n")
        self.assertTrue(self.report()["passed"])
        path.write_bytes(padded.replace(b"confirmed", b"needs-change"))
        self.assertIn("history_changed", self.report()["files"][0]["issues"])
        path.write_bytes(b"History moved somewhere else.\n")
        self.assertIn("history_unrecognized", self.report()["files"][0]["issues"])

    def test_missing_invalid_and_changed_inputs_fail_closed(self) -> None:
        self.assertFalse(self.report()["passed"])
        self.put("gone.md", b"Original note words.\n")
        (self.target / "gone.md").unlink()
        self.assertEqual(self.report()["files"][0]["issues"], ["missing_file"])
        self.put("invalid.md", b"\xff", b"\xff")
        self.assertTrue(any("unreadable_source" in f["issues"] for f in self.report()["files"]))
        candidates = scan(self.source)
        (self.source / "gone.md").unlink()
        report = compare_migration(self.source, self.target, candidates)
        self.assertEqual(report["files"][0]["issues"], ["unreadable_file"])
        self.put("gone.md", b"Original note words.\n")
        candidates = scan(self.source)
        (self.source / "gone.md").write_bytes(b"Changed after inventory.\n")
        self.assertIn("source_changed", compare_migration(self.source, self.target, candidates)["files"][0]["issues"])

    def test_target_symlink_cannot_escape_comparison_directory(self) -> None:
        self.put("note.md", b"Original note words.\n")
        outside = self.base / "outside.md"
        outside.write_bytes(b"private-sentinel")
        link = self.target / "note.md"
        link.unlink()
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.report()["files"][0]["issues"], ["unsafe_target"])

    def test_cli_compare_is_read_only_and_cannot_be_combined_with_write(self) -> None:
        self.put("note.md", b"Read [[notes/a|Label]] and E-001.\n", b"Read `notes/a`.\n")
        def hashes():
            return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.base.rglob("*") if p.is_file()}
        before = hashes()
        args = ("adopt", str(self.source), "--into", str(self.vault), "--compare", str(self.target))
        code, output, _ = call(*args, "--json")
        self.assertEqual(code, 1)
        report = json.loads(output)
        self.assertFalse(report["write"])
        schema = json.loads((ROOT / "schemas/adopt-report.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])
        report["preservation"]["files"][0]["issues"] = ["unknown_issue"]
        self.assertTrue(validate(report, schema))
        code, output, _ = call(*args, "--write", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["error"]["code"], "usage")
        self.assertEqual(hashes(), before)
        code, output, _ = call(*args)
        self.assertEqual(code, 1)
        self.assertIn("wikilinks_lost", output)

    def test_unreadable_target_has_an_explicit_failure(self) -> None:
        self.put("note.md", b"Original words.\n")
        original = Path.read_bytes
        def denied(path):
            if path == self.target / "note.md":
                raise PermissionError
            return original(path)
        candidates = scan(self.source)
        with patch.object(Path, "read_bytes", denied):
            self.assertEqual(compare_migration(self.source, self.target, candidates)["files"][0]["issues"], ["unreadable_file"])

    def test_staged_baseline_inside_vault_is_allowed_only_for_read_only_comparison(self) -> None:
        self.put("note.md", b"Read [[notes/a|Label]].\n")
        staged = self.vault / ".import-staging/baseline"
        shutil.copytree(self.source, staged)
        code, output, errors = call("adopt", str(staged), "--into", str(self.vault), "--compare", str(self.target), "--json")
        self.assertEqual(code, 0, errors)
        self.assertTrue(json.loads(output)["preservation"]["passed"])
        code, output, _ = call("adopt", str(staged), "--into", str(self.vault), "--write", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["error"]["code"], "invalid_target")
        code, output, _ = call("adopt", str(staged), "--into", str(self.vault), "--compare", str(staged), "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["error"]["code"], "invalid_target")
