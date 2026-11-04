"""Real raw-byte migrations, legacy consumers and scoped knowledge projections."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import unittest
from unittest.mock import patch

from _jsonschema import validate
import test_agent_preflight as approval_fixture
from whykit import knowledge_v2 as knowledge
from whykit.claim_ledger import verify_claim
from whykit.artifact_store import capture_artifact
from whykit.outcome_ledger import create_outcome
from whykit.parse_cache import persistent
from whykit.query import query_vault
from whykit.scaffold import create_decision
from whykit.tables import review_table_header, split_table_row
from whykit.vault_index import NoteCache, VaultIndex, reuse_notes
from whykit.sensitivity import classified_evidence

ROOT = Path(__file__).resolve().parents[1]
DAY = approval_fixture.DAY


def tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def rehash(bundle: dict) -> dict:
    bundle["manifest_sha256"] = knowledge._hash(knowledge._wire(bundle["manifest"]))
    bundle["bundle_sha256"] = knowledge._hash(knowledge._wire({key: child for key, child in bundle.items() if key != "bundle_sha256"}))
    return bundle


class KnowledgeV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = approval_fixture.PreflightTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.vault.resolve()
        self.parent = self.root.parent
        self.claim, self.claim_path, self.capture = self.fixture.claimed_decision()
        self.claim_path = self.claim_path.resolve()
        digest = hashlib.sha256(self.claim_path.read_bytes()).hexdigest()
        data = {"decision_id": self.fixture.manifest["decision_ids"][0],
                "metric": {"name": "Queue delay", "unit": "seconds", "definition": "Synthetic fixed cohort."},
                "baseline": {"value": "5.00", "as_of": DAY.isoformat(), "claim": {"claim_id": self.claim["claim_id"], "sha256": digest}},
                "target": {"value": "3.00", "by": None}, "observed": None, "implementation_refs": ["issue:example-7"]}
        plan = create_outcome(self.root, data, owner="Ada Example", today=DAY)
        self.outcome = create_outcome(self.root, data, owner="Ada Example", today=DAY,
                                       write=True, expected_sha256=plan["expected_sha256"])
        # Byte preservation cannot be inferred from normalized notes or receipt digests.
        self.unknown = b"\xef\xbb\xbf---\r\ntitle: Unknown fields\r\ntype: reference\r\nsensitivity: public\r\ncustom_future:\r\n  nested: retained\r\n---\r\nZa\xc5\xbc\xc3\xb3\xc5\x82\xc4\x87 [[Home|Label]]\r\n"
        (self.root / "unknown.md").write_bytes(self.unknown)
        (self.root / "unknown.bin").write_bytes(bytes(range(256)) + b"\x00\xff\r\n")
        (self.root / "no-eof.txt").write_bytes(b"No terminal newline")
        binary = self.parent / "binary-capture.bin"
        binary.write_bytes(b"\x00\xff\xef\xbb\xbf\r\n")
        preview = capture_artifact(self.root, "E-003", binary, recorded_at="2026-09-17T11:00:00Z")
        capture_artifact(self.root, "E-003", binary, recorded_at="2026-09-17T11:00:00Z",
                         write=True, expected_sha256=preview["expected_sha256"])
        _, draft = create_decision(self.root, "Future source fields", owner="Ada Example", today=DAY)
        original = draft.read_bytes()
        draft.write_bytes(original.replace(b"---\n", b'---\nfuture_data: {"anything":{"nested":"retained"}}\n', 1))
        self.target = self.parent / "restored"

    def bundle(self) -> dict:
        return knowledge.build_migration_bundle(self.root, full_local_scope=True)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_actual_bundle_restore_preserves_raw_bytes_and_legacy_consumers(self) -> None:
        before = tree(self.root)
        old_query = query_vault(self.root, doc_type="decision", limit=100)
        old_anchor = verify_claim(self.root, self.claim["claim_id"], today=DAY)
        old_preflight = self.fixture.check()
        with persistent(), patch.object(socket, "socket", side_effect=AssertionError("no network")), patch.object(subprocess, "run", side_effect=AssertionError("no runtime subprocess")):
            bundle = self.bundle()
            self.assertEqual(knowledge.validate_migration_bundle(json.dumps(bundle)), bundle)
            preview = knowledge.restore_migration_bundle(bundle, self.target)
            self.assertFalse(preview["applied"])
            self.assertFalse(preview["exact_bytes_preserved"])
            self.assertFalse(self.target.exists())
            applied = knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=preview["expected_sha256"])
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["exact_bytes_preserved"])
        self.assertFalse(applied["git_history_transferred"])
        self.assertFalse(applied["execution_authorized"])
        self.assertEqual(tree(self.root), before)
        for row in bundle["files"]:
            self.assertEqual((self.target / row["path"]).read_bytes(), before[row["path"]])
        self.assertEqual((self.target / "unknown.md").read_bytes(), self.unknown)
        self.assertEqual(query_vault(self.target, doc_type="decision", limit=100), old_query)
        self.assertEqual(verify_claim(self.target, self.claim["claim_id"], today=DAY), old_anchor)
        original_root = self.fixture.vault
        self.fixture.vault = self.target
        try:
            self.assertEqual(self.fixture.check(), old_preflight)
        finally:
            self.fixture.vault = original_root
        self.assertEqual(self.bundle(), bundle)
        self.assertEqual(knowledge.build_migration_bundle(self.target, full_local_scope=True), bundle)
        self.assertEqual({row["kind"] for row in bundle["manifest"]["objects"]}, {"Claim", "EvidenceArtifact", "Decision", "ReviewEvent", "Outcome"})
        artifact = next(row for row in bundle["manifest"]["objects"] if row["kind"] == "EvidenceArtifact")
        self.assertNotIn("content_base64", json.dumps(artifact))

    def test_full_scope_is_explicit_and_partial_grants_cannot_be_called_lossless(self) -> None:
        for options in ({}, {"full_local_scope": 1}, {"full_local_scope": True, "allowed_sensitivities": {"public"}},
                        {"full_local_scope": True, "allowed_sensitivities": set(knowledge._LABELS)}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "explicit full"):
                knowledge.build_migration_bundle(self.root, **options)
        with reuse_notes(NoteCache()):
            grant = VaultIndex.load(self.root)
        with self.assertRaisesRegex(ValueError, "restricted grant"):
            knowledge.build_migration_bundle(self.root, full_local_scope=True, vault=grant)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_typed_forgery_fails_even_after_recomputing_all_public_hashes(self) -> None:
        bundle = self.bundle()
        for kind in ("Decision", "Outcome", "ReviewEvent", "Claim", "EvidenceArtifact"):
            forged = copy.deepcopy(bundle)
            row = next(row for row in forged["manifest"]["objects"] if row["kind"] == kind)
            row["data"]["forged"] = "Looks internally consistent"
            rehash(forged)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "typed knowledge"):
                knowledge.validate_migration_bundle(forged)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_untrusted_paths_aliases_nonfinite_and_structure_are_refused_before_staging(self) -> None:
        bundle = self.bundle()
        for name in ("../escape", "/absolute", "https://example.test/x", "a\\b", ".git/config", ".whykit/config", "a/../b", "a./b", "a\x00b"):
            forged = copy.deepcopy(bundle)
            forged["files"][0]["path"] = name
            rehash(forged)
            with self.subTest(name=name), patch("whykit.knowledge_v2.tempfile.TemporaryDirectory", side_effect=AssertionError("no staging before path validation")), self.assertRaises(ValueError):
                knowledge.validate_migration_bundle(forged)
        for altered in ({**bundle, "extra": True}, {**bundle, "contract_version": True}, {**bundle, "manifest": float("nan")}):
            with self.assertRaises(ValueError):
                knowledge.validate_migration_bundle(altered)
        raw = '{"format":"x","format":"y"}'
        with self.assertRaisesRegex(ValueError, "duplicate"):
            knowledge.validate_migration_bundle(raw)
        forged = copy.deepcopy(bundle)
        duplicate = copy.deepcopy(forged["files"][0])
        duplicate["path"] = duplicate["path"].upper()
        forged["files"].append(duplicate)
        forged["files"].sort(key=lambda row: row["path"])
        rehash(forged)
        with self.assertRaises(ValueError):
            knowledge.validate_migration_bundle(forged)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_raw_forgery_size_and_patched_count_limits_are_refused(self) -> None:
        bundle = self.bundle()
        for field, value in (("size_bytes", True), ("sha256", "0" * 64), ("content_base64", "!invalid!")):
            forged = copy.deepcopy(bundle)
            forged["files"][0][field] = value
            rehash(forged)
            with self.subTest(field=field), self.assertRaises(ValueError):
                knowledge.validate_migration_bundle(forged)
        with patch.object(knowledge, "MAX_FILES", 1), self.assertRaises(ValueError):
            self.bundle()
        with patch.object(knowledge, "MAX_OBJECTS", 1), self.assertRaises(ValueError):
            self.bundle()
        with patch.object(knowledge, "MAX_RAW_BYTES", 1), self.assertRaises(ValueError):
            knowledge.validate_migration_bundle(bundle)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_restore_requires_reviewed_hash_and_refuses_existing_or_symlink_target(self) -> None:
        bundle = self.bundle()
        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            knowledge.restore_migration_bundle(bundle, self.target, write=True)
        self.assertFalse(self.target.exists())
        self.target.mkdir()
        (self.target / "owned.txt").write_bytes(b"Other owner's bytes")
        before = tree(self.target)
        with self.assertRaisesRegex(ValueError, "must not exist"):
            knowledge.restore_migration_bundle(bundle, self.target)
        self.assertEqual(tree(self.target), before)
        link = self.parent / "alias"
        link.symlink_to(self.target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            knowledge.restore_migration_bundle(bundle, link / "new")
        with self.assertRaisesRegex(ValueError, "existing vault"):
            knowledge.restore_migration_bundle(bundle, self.root / "nested-restore")

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_native_raw_input_symlinks_fifo_and_unknown_machine_state_fail_closed(self) -> None:
        extra = self.root / "extra-source"
        for form in ("symlink", "fifo"):
            if form == "symlink":
                extra.symlink_to(self.root / "unknown.bin")
            else:
                os.mkfifo(extra)
            try:
                with self.subTest(form=form), self.assertRaisesRegex(ValueError, "symlink|nonregular"):
                    self.bundle()
            finally:
                extra.unlink()
        machine = self.root / ".whykit/unrecognized.json"
        machine.parent.mkdir(exist_ok=True)
        machine.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "unknown migration machine"):
            self.bundle()

    def test_default_public_view_hides_native_private_data_and_mixed_log_fingerprints(self) -> None:
        before = knowledge.project_knowledge_v2(self.root)
        self.assertNotIn("Claim", {row["kind"] for row in before["objects"]})
        self.assertNotIn("Outcome", {row["kind"] for row in before["objects"]})
        log_path = self.root / "00-context/review-log.md"
        log = log_path.read_text(encoding="utf-8")
        # Existing public review rows stay stable when a non-granted target gains a row.
        private = self.fixture.record
        private.write_text(private.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: restricted").replace("sensitivity: public", "sensitivity: restricted"), encoding="utf-8")
        filtered = knowledge.project_knowledge_v2(self.root)
        self.assertTrue(any(item["kind"] == "ReviewEvent" for item in filtered["objects"]))
        row = f"| 2026-09-17 | [[06-decisions/{private.stem}]] | Hidden reviewer | confirmed | — | — | Hidden canary. |\n"
        lines = log.splitlines(keepends=True)
        at = review_table_header(lines)
        self.assertIsNotNone(at)
        lines.insert(at + 2, row)
        log_path.write_text("".join(lines), encoding="utf-8")
        self.assertEqual(knowledge.project_knowledge_v2(self.root), filtered)
        self.claim_path.write_bytes(b"malformed hidden claim")
        self.assertEqual(knowledge.project_knowledge_v2(self.root), filtered)
        self.assertNotIn("Hidden canary", json.dumps(filtered))
        for event in (row for row in filtered["objects"] if row["kind"] == "ReviewEvent"):
            self.assertIsNone(event["source"]["sha256"])

    def test_supplied_empty_grants_never_reload_full_index_or_rows(self) -> None:
        with reuse_notes(NoteCache()):
            index = VaultIndex.load(self.root)
        restricted = index.subset(lambda note: note.path == self.claim_path)
        restricted.derived["evidence_register"] = ({}, {}, [])
        with patch.object(VaultIndex, "load", side_effect=AssertionError("grant expansion")):
            report = knowledge.project_knowledge_v2(self.root, allowed_sensitivities=set(knowledge._LABELS), vault=restricted)
        self.assertEqual(report["objects"], [])

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_schemas_cover_real_bundle_and_five_kind_manifest(self) -> None:
        bundle = self.bundle()
        for file, data in (("knowledge-v2.schema.json", bundle["manifest"]), ("knowledge-migration.schema.json", bundle)):
            schema = json.loads((ROOT / "schemas" / file).read_text(encoding="utf-8"))
            self.assertEqual(validate(data, schema), [])
        invalid = copy.deepcopy(bundle["manifest"])
        invalid["objects"][0]["kind"] = "InventedKind"
        schema = json.loads((ROOT / "schemas/knowledge-v2.schema.json").read_text(encoding="utf-8"))
        self.assertNotEqual(validate(invalid, schema), [])

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_source_git_refs_index_objects_and_dirty_worktree_remain_byte_identical(self) -> None:
        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Synthetic Reviewer", "-c", "user.email=fixture@example.test", "commit", "-qm", "Synthetic baseline")
        (self.root / "unknown.bin").write_bytes(b"Staged bytes")
        git("add", "unknown.bin")
        (self.root / "unknown.md").write_bytes(self.unknown + b"Unstaged bytes")
        before = tree(self.root)
        with persistent(), patch.object(subprocess, "run", side_effect=AssertionError("runtime Git forbidden")):
            bundle = self.bundle()
            plan = knowledge.restore_migration_bundle(bundle, self.target)
            knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.target / ".git").exists())
        self.assertFalse(bundle["git_history_transferred"])

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_native_orphan_or_garbage_cannot_silently_become_untyped_raw_inventory(self) -> None:
        bundle = self.bundle()
        for name, raw in (("00-context/claims/garbage.bin", b"unknown"),
                          ("00-context/outcomes/o-999.txt", b"unknown"),
                          ("00-context/artifacts/E-999/unknown.json", b"{}")):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            try:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "native knowledge"):
                    self.bundle()
            finally:
                path.unlink()
                if path.parent.name == "E-999":
                    path.parent.rmdir()
        self.assertEqual(self.bundle(), bundle)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_canonical_decision_file_without_an_identity_cannot_silently_disappear(self) -> None:
        path = self.root / "06-decisions/d-999-unresolved.md"
        path.write_bytes(b"---\ntitle: Unresolved record\ntype: decision\nsensitivity: public\n---\nNo identity.\n")
        with self.assertRaisesRegex(ValueError, "native knowledge"):
            self.bundle()

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_validated_derived_manifest_is_excluded_but_stale_or_forged_one_refuses(self) -> None:
        bundle = self.bundle()
        plan = knowledge.restore_migration_bundle(bundle, self.target)
        knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=plan["expected_sha256"])
        manifest = self.target / knowledge.MANIFEST_PATH
        manifest.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "derived knowledge"):
            knowledge.build_migration_bundle(self.target, full_local_scope=True)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_duplicate_identical_review_rows_keep_distinct_local_occurrences(self) -> None:
        path = self.root / "00-context/review-log.md"
        text = path.read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| 2026-"))
        path.write_text(text + row + "\n", encoding="utf-8")
        report = self.bundle()["manifest"]
        events = [event for event in report["objects"] if event["kind"] == "ReviewEvent"]
        digest = knowledge._hash(knowledge._wire(split_table_row(row)))
        twins = [event for event in events if event["source"]["row_sha256"] == digest]
        self.assertEqual([event["source"]["occurrence"] for event in twins], [1, 2])

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_restore_failure_removes_only_owned_entries_and_keeps_unexpected_files(self) -> None:
        bundle = self.bundle()
        plan = knowledge.restore_migration_bundle(bundle, self.target)
        original = knowledge._write_entry
        def fail(root_fd, name, raw, directories, files):
            original(root_fd, name, raw, directories, files)
            (self.target / "other-writer.txt").write_bytes(b"Keep me")
            raise OSError("injected disk failure")
        with patch.object(knowledge, "_write_entry", side_effect=fail), self.assertRaisesRegex(OSError, "injected"):
            knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(tree(self.target), {"other-writer.txt": b"Keep me"})

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_restore_directory_swap_cannot_create_a_file_outside_the_target(self) -> None:
        bundle = self.bundle()
        plan = knowledge.restore_migration_bundle(bundle, self.target)
        outside = self.parent / "outside"
        outside.mkdir()
        original = os.open
        swapped = False
        def swap(name, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if not swapped and isinstance(name, str) and "/" not in name and flags & os.O_EXCL and (self.target / "00-context").is_dir():
                directory = self.target / "00-context"
                directory.rename(self.target / "moved-owned-directory")
                directory.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original(name, flags, mode, dir_fd=dir_fd)
        with patch("whykit.knowledge_v2.os.open", side_effect=swap), self.assertRaises((ValueError, OSError)):
            knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=plan["expected_sha256"])
        self.assertTrue(swapped)
        self.assertEqual(tree(outside), {})

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_legacy_alias_and_body_decision_ids_are_derived_without_rewriting_sources(self) -> None:
        path = next(path for path in (self.root / "06-decisions").glob("d-*-future-source-fields.md"))
        original = path.read_text(encoding="utf-8")
        identity = next(line.split(":", 1)[1].strip() for line in original.splitlines() if line.startswith("decision_id:"))
        for legacy in (original.replace("decision_id: " + identity + "\n", "").replace("aliases: []", "aliases: [" + identity + "]"),
                       original.replace("decision_id: " + identity + "\n", "") + "\n## Decision " + identity + "\n"):
            path.write_text(legacy, encoding="utf-8")
            before = path.read_bytes()
            bundle = self.bundle()
            row = next(item for item in bundle["manifest"]["objects"] if item["kind"] == "Decision" and item["id"] == identity)
            self.assertEqual(row["data"]["record"]["decision_id"], identity)
            self.assertEqual(row["data"]["record"]["future_data"], '{"anything":{"nested":"retained"}}')
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_review_dates_and_nested_manifest_types_match_source_contract(self) -> None:
        path = self.root / "00-context/review-log.md"
        original = path.read_text(encoding="utf-8")
        for day in ("20260917", "2026-W38-4", "2026-02-30"):
            path.write_text(original.replace("| 2026-09-17 |", "| " + day + " |"), encoding="utf-8")
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, "review event"):
                self.bundle()
        path.write_text(original, encoding="utf-8")
        forged = self.bundle()
        forged["manifest"]["contract_version"] = True
        # Deliberately keep the derived-manifest digest; Python bool == 1 must not bypass rederivation.
        forged["bundle_sha256"] = knowledge._hash(knowledge._wire({key: child for key, child in forged.items() if key != "bundle_sha256"}))
        with self.assertRaisesRegex(ValueError, "typed knowledge"):
            knowledge.validate_migration_bundle(forged)

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_destination_appearance_is_never_overwritten_between_preview_and_mkdir(self) -> None:
        bundle = self.bundle()
        plan = knowledge.restore_migration_bundle(bundle, self.target)
        original = os.mkdir
        def appear(name, mode=0o777, *, dir_fd=None):
            if name == self.target.name and dir_fd is not None:
                original(name, mode, dir_fd=dir_fd)
                (self.target / "foreign.txt").write_bytes(b"Other writer")
            return original(name, mode, dir_fd=dir_fd)
        with patch("whykit.knowledge_v2.os.mkdir", side_effect=appear), self.assertRaises(FileExistsError):
            knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(tree(self.target), {"foreign.txt": b"Other writer"})

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_existing_example_formats_restore_without_metadata_or_body_rewrites(self) -> None:
        for example in ("tiny", "northline", "approval"):
            with self.subTest(example=example):
                source = self.parent / ("legacy-" + example)
                shutil.copytree(ROOT / "examples" / example, source)
                before = tree(source)
                bundle = knowledge.build_migration_bundle(source, full_local_scope=True)
                target = self.parent / ("restored-" + example)
                plan = knowledge.restore_migration_bundle(bundle, target)
                knowledge.restore_migration_bundle(bundle, target, write=True, expected_sha256=plan["expected_sha256"])
                for row in bundle["files"]:
                    self.assertEqual((target / row["path"]).read_bytes(), before[row["path"]])
                self.assertEqual(tree(source), before)
                self.assertEqual(query_vault(source, doc_type="decision"), query_vault(target, doc_type="decision"))

    def test_explicit_row_grant_enables_only_its_native_claim_outcome_dependency_closure(self) -> None:
        with reuse_notes(NoteCache()):
            index = VaultIndex.load(self.root)
        active, retired, _, _ = classified_evidence(index)
        paths = {self.claim_path, self.root / self.outcome["path"], self.fixture.record}
        grant = index.subset(lambda note: note.path in paths)
        grant.derived["evidence_register"] = ({"E-001": active["E-001"]}, {}, [("E-001", 0)])
        with patch.object(VaultIndex, "load", side_effect=AssertionError("grant expansion")):
            view = knowledge.project_knowledge_v2(self.root, allowed_sensitivities=set(knowledge._LABELS), vault=grant)
        self.assertEqual({row["kind"] for row in view["objects"]}, {"Decision", "Claim", "Outcome", "EvidenceArtifact"})
        grant.derived["evidence_register"] = ({}, {}, [])
        denied = knowledge.project_knowledge_v2(self.root, allowed_sensitivities=set(knowledge._LABELS), vault=grant)
        self.assertEqual({row["kind"] for row in denied["objects"]}, {"Decision"})

    def test_denied_title_alias_body_errors_and_source_hashes_never_change_public_view(self) -> None:
        hidden = self.root / "hidden.md"
        hidden.write_bytes(b"---\ntitle: Hidden title canary\naliases: [Hidden alias canary]\ntype: reference\nsensitivity: restricted\n---\nHidden body canary\n")
        before = knowledge.project_knowledge_v2(self.root)
        for raw in (b"---\nsensitivity: restricted\ntitle: Different secret\n---\nOther secret",
                    b"---\nsensitivity: restricted\n---\n\xffbad secret bytes"):
            hidden.write_bytes(raw)
            self.assertEqual(knowledge.project_knowledge_v2(self.root), before)
        self.assertNotIn("canary", json.dumps(before))

    def test_deep_and_oversized_untrusted_input_never_reaches_staging(self) -> None:
        deep: object = {}
        for _ in range(knowledge.MAX_JSON_DEPTH + 2):
            deep = [deep]
        with patch("whykit.knowledge_v2.tempfile.TemporaryDirectory", side_effect=AssertionError("untrusted staging")):
            with self.assertRaisesRegex(ValueError, "structure limit"):
                knowledge.validate_migration_bundle({"deep": deep})
            with patch.object(knowledge, "MAX_BUNDLE_BYTES", 8), self.assertRaisesRegex(ValueError, "byte limit"):
                knowledge.validate_migration_bundle(b"{}" * 9)

    def test_scoped_view_never_hashes_raw_mixed_register_or_review_log(self) -> None:
        forbidden = {(self.root / name).read_bytes() for name in ("00-context/evidence-register.md", "00-context/review-log.md")}
        original = knowledge._hash
        def check(raw):
            self.assertNotIn(raw, forbidden)
            return original(raw)
        with patch.object(knowledge, "_hash", side_effect=check):
            report = knowledge.project_knowledge_v2(self.root)
        self.assertTrue(report["objects"])

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_empty_source_directories_roundtrip_and_unexpected_empty_directory_refuses(self) -> None:
        (self.root / "empty/nested").mkdir(parents=True)
        bundle = self.bundle()
        self.assertIn("empty/nested", bundle["directories"])
        plan = knowledge.restore_migration_bundle(bundle, self.target)
        knowledge.restore_migration_bundle(bundle, self.target, write=True, expected_sha256=plan["expected_sha256"])
        self.assertTrue((self.target / "empty/nested").is_dir())
        self.assertEqual(knowledge.build_migration_bundle(self.target, full_local_scope=True), bundle)
        other = self.parent / "restore-with-race"
        plan = knowledge.restore_migration_bundle(bundle, other)
        original = knowledge._write_entry
        def inject(*args):
            original(*args)
            (other / "foreign-empty").mkdir(exist_ok=True)
        with patch.object(knowledge, "_write_entry", side_effect=inject), self.assertRaisesRegex(ValueError, "verification"):
            knowledge.restore_migration_bundle(bundle, other, write=True, expected_sha256=plan["expected_sha256"])
        self.assertTrue((other / "foreign-empty").is_dir())

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_export_is_reviewed_create_only_private_and_outside_source(self) -> None:
        output = self.parent / "portable-żółw.json"
        before = tree(self.root)
        preview = knowledge.export_migration_bundle(self.root, output, full_local_scope=True)
        self.assertFalse(preview["applied"])
        self.assertFalse(output.exists())
        with persistent():
            report = knowledge.export_migration_bundle(self.root, output, full_local_scope=True,
                                                       write=True, expected_sha256=preview["expected_sha256"])
        self.assertTrue(report["applied"])
        self.assertFalse(report["git_history_transferred"])
        self.assertFalse(report["source_git_modified"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(knowledge.validate_migration_bundle(output.read_bytes()), self.bundle())
        self.assertEqual(tree(self.root), before)
        self.assertEqual(list(self.parent.glob(".whykit-export-*.tmp")), [])
        for target in (self.root / "inside.json", output):
            with self.subTest(target=target), self.assertRaises(ValueError):
                knowledge.export_migration_bundle(self.root, target, full_local_scope=True)
        denied = self.parent / "denied.json"
        with self.assertRaisesRegex(ValueError, "explicit full"):
            knowledge.export_migration_bundle(self.root, denied)
        self.assertFalse(denied.exists())

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_export_stale_source_and_destination_race_never_publish_or_replace(self) -> None:
        output = self.parent / "export-race.json"
        plan = knowledge.export_migration_bundle(self.root, output, full_local_scope=True)
        (self.root / "no-eof.txt").write_bytes(b"Changed after preview")
        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            knowledge.export_migration_bundle(self.root, output, full_local_scope=True, write=True,
                                             expected_sha256=plan["expected_sha256"])
        self.assertFalse(output.exists())
        plan = knowledge.export_migration_bundle(self.root, output, full_local_scope=True)
        original = os.link
        def race(*args, **kwargs):
            output.write_bytes(b"Concurrent owner's bytes")
            return original(*args, **kwargs)
        with patch("whykit.knowledge_v2.os.link", side_effect=race), self.assertRaises(FileExistsError):
            knowledge.export_migration_bundle(self.root, output, full_local_scope=True, write=True,
                                             expected_sha256=plan["expected_sha256"])
        self.assertEqual(output.read_bytes(), b"Concurrent owner's bytes")
        self.assertEqual(list(self.parent.glob(".whykit-export-*.tmp")), [])

    def test_unsupported_native_primitives_refuse_before_staging_or_writes(self) -> None:
        before = tree(self.root)
        with patch.object(knowledge, "_NATIVE_IO_AVAILABLE", False), patch.object(knowledge, "_validated", side_effect=AssertionError("unsupported platform staging")):
            for operation in (
                lambda: knowledge.build_migration_bundle(self.root, full_local_scope=True),
                lambda: knowledge.restore_migration_bundle({}, self.target),
                lambda: knowledge.export_migration_bundle(self.root, self.parent / "unsupported.json", full_local_scope=True),
            ):
                with self.assertRaisesRegex(ValueError, "POSIX descriptor-relative"):
                    operation()
            # The read-only typed projection uses existing portable source readers.
            self.assertEqual(knowledge.project_knowledge_v2(self.root)["scope"], "filtered")
        self.assertEqual(tree(self.root), before)
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(knowledge._NATIVE_IO_AVAILABLE, "native migration I/O currently requires POSIX; refusal/projection tests remain active")
    def test_export_and_restore_refuse_reserved_git_namespace_before_staging(self) -> None:
        bundle = self.bundle()
        for alias in (".git", ".GIT", ".git.", ".git ", ".Git... ", ".git:stream"):
            directory = self.parent / alias
            (directory / "refs/heads").mkdir(parents=True, exist_ok=True)
            (directory / "objects").mkdir(exist_ok=True)
            (directory / "index").write_bytes(b"Retain original index bytes")
            (directory / "refs/heads/main").write_bytes(b"1" * 40 + b"\n")
            before = tree(self.parent)
            for write in (False, True):
                with self.subTest(alias=alias, write=write):
                    with patch.object(knowledge, "build_migration_bundle", side_effect=AssertionError("namespace must refuse before source reads")):
                        with self.assertRaisesRegex(ValueError, "Git metadata namespace"):
                            knowledge.export_migration_bundle(self.root, directory / "refs/heads/bundle", full_local_scope=True,
                                                               write=write, expected_sha256="0" * 64)
                    with patch.object(knowledge, "_validated", side_effect=AssertionError("namespace must refuse before staging")):
                        with self.assertRaisesRegex(ValueError, "Git metadata namespace"):
                            knowledge.restore_migration_bundle(bundle, directory / "objects/restored-vault",
                                                                write=write, expected_sha256="0" * 64)
            self.assertEqual(tree(self.parent), before)
