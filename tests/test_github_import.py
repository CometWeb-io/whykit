"""Saved GitHub payloads are synthetic declarations, never authenticated sources."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from test_artifact_store import register
from whykit import artifact_store as store, io
from whykit.github_import import import_github
from whykit.parse_cache import persistent

STAMP = "2026-10-27T10:00:00Z"
REPO = "fixture-org/ledger"


def payload(kind: str) -> dict:
    common = {"id": 101, "number": 7, "state": "open", "created_at": STAMP,
              "updated_at": STAMP, "closed_at": None, "body": "Synthetic żółw\r\nUntrusted declaration."}
    if kind == "issue":
        return {**common, "html_url": f"https://github.com/{REPO}/issues/7",
                "url": f"https://api.github.com/repos/{REPO}/issues/7", "repository_url": f"https://api.github.com/repos/{REPO}"}
    if kind == "pull-request":
        return {**common, "html_url": f"https://github.com/{REPO}/pull/7", "url": f"https://api.github.com/repos/{REPO}/pulls/7",
                "base": {"sha": "a" * 40, "repo": {"full_name": REPO}},
                "head": {"sha": "b" * 40, "repo": None}, "merge_commit_sha": None, "merged_at": None}
    return {"id": 303, "html_url": f"https://github.com/{REPO}/actions/runs/303",
            "url": f"https://api.github.com/repos/{REPO}/actions/runs/303", "repository": {"full_name": REPO},
            "head_repository": {"full_name": "fixture-fork/ledger"}, "run_attempt": 1, "head_sha": "a" * 40,
            "status": "queued", "conclusion": None, "created_at": STAMP, "updated_at": STAMP}


def snapshot(root: Path) -> dict:
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None for path in root.rglob("*")}


class GithubImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "vault"
        self.root.mkdir()
        self.register = register(self.root, "restricted")
        self.source = self.root.parent / "saved.json"
        self.kind = "issue"
        self.prepare(payload(self.kind))

    def prepare(self, data: dict) -> None:
        self.source.write_bytes(b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\r\n")
        text = self.register.read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| E-001 |"))
        cells = row.split("|")
        cells[6] = " " + data["html_url"] + " "
        self.register.write_text(text.replace(row, "|".join(cells)), encoding="utf-8")

    def run_import(self, **kwargs) -> dict:
        return import_github(self.root, "E-001", self.source, kind=self.kind, repository=REPO, recorded_at=kwargs.pop("recorded_at", STAMP), **kwargs)

    def apply(self, **kwargs) -> dict:
        plan = self.run_import(**kwargs)
        return self.run_import(write=True, expected_sha256=plan["expected_sha256"], **kwargs)

    def test_three_kinds_one_transaction_exact_raw_and_no_writes_on_preview(self) -> None:
        for kind in ("issue", "pull-request", "workflow-run"):
            with self.subTest(kind=kind):
                self.kind = kind
                self.prepare(payload(kind))
                before = snapshot(self.root)
                with persistent(True):
                    preview = self.run_import()
                self.assertEqual(snapshot(self.root), before)
                with patch("whykit.github_import.apply_transaction", wraps=io.apply_transaction) as transaction:
                    result = self.run_import(write=True, expected_sha256=preview["expected_sha256"])
                    self.assertEqual(transaction.call_count, 1)
                    self.assertEqual(len(transaction.call_args.args[1]), 3)
                self.assertEqual(result["size_bytes"], len(self.source.read_bytes()))
                self.assertEqual(result["observation_recorded_at"], STAMP)
                self.assertEqual(result["source_version"], "sha256:" + hashlib.sha256(self.source.read_bytes()).hexdigest())
                self.assertEqual(store.read_artifact(self.root, "E-001", result["source_version"]), self.source.read_bytes())
                receipt = json.loads((self.root / result["receipt_path"]).read_text(encoding="utf-8"))
                schema = json.loads((Path(__file__).parents[1] / "schemas/github-import-receipt.schema.json").read_text(encoding="utf-8"))
                self.assertEqual(validate(receipt, schema), [])
                self.assertEqual(receipt["metadata"], result["metadata"])
                self.assertNotIn("body", receipt["metadata"])
                for flag in ("source_identity_authenticated", "remote_state_checked", "semantics_checked", "execution_authorized"):
                    self.assertIs(result[flag], False)
                # Different resources belong to different synthetic E rows; reset between profiles.
                for directory in ("artifacts", "observations", "imports"):
                    shutil.rmtree(self.root / "00-context" / directory, ignore_errors=True)

    def test_retry_and_a_b_a_never_rewrite_old_versions(self) -> None:
        plan = self.run_import()
        first = self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        original = {key: (self.root / first[key]).read_bytes() for key in ("artifact_path", "receipt_path")}
        with patch("whykit.github_import.apply_transaction") as transaction:
            retry = self.run_import(write=True, expected_sha256=plan["expected_sha256"])
            transaction.assert_not_called()
        self.assertTrue(retry["already_exists"])
        raw_a = self.source.read_bytes()
        data = payload("issue")
        data["body"] = "B"
        self.prepare(data)
        self.apply(recorded_at="2026-10-27T11:00:00Z")
        self.source.write_bytes(raw_a)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        third = self.apply(recorded_at="2026-10-27T12:00:00Z")
        self.assertTrue(third["already_exists"])
        self.assertTrue(third["observation_added"])
        self.assertEqual({key: (self.root / first[key]).read_bytes() for key in original}, original)
        observed = store.read_source_observation(self.root, "E-001", first["source_version"], today=dt.date(2026, 10, 27))
        self.assertEqual(observed["state"], "current")
        self.assertEqual(observed["observation_count"], 3)

    def test_same_read_snapshot_drives_validation_and_capture(self) -> None:
        original = self.source.read_bytes()
        real = store._read_regular
        reads = []
        def read(path, **kwargs):
            result = real(path, **kwargs)
            if Path(path) == self.source:
                reads.append(result)
                self.source.write_bytes(b'not valid json anymore')
            return result
        with patch("whykit.github_import._read_regular", side_effect=read):
            plan = self.run_import()
        self.assertEqual(reads, [original])
        self.assertEqual(plan["source_version"], "sha256:" + hashlib.sha256(original).hexdigest())
        self.source.write_bytes(original)
        plan = self.run_import()
        with patch("whykit.github_import._read_regular", side_effect=read):
            result = self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(store.read_artifact(self.root, "E-001", result["source_version"]), original)

    def test_stale_input_receipt_and_failure_never_claim_success(self) -> None:
        plan = self.run_import()
        data = payload("issue")
        data["body"] = "changed"
        self.prepare(data)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        self.assertFalse((self.root / "00-context/imports").exists())
        plan = self.run_import()
        before = snapshot(self.root)
        with patch("whykit.github_import.apply_transaction", side_effect=OSError("synthetic transaction refusal")):
            with self.assertRaises(OSError):
                self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        def files(state):
            return {p:v for p,v in state.items() if v is not None and not p.startswith('.whykit')}
        self.assertEqual(files(snapshot(self.root)), files(before))
        result = self.apply()
        receipt = self.root / result["receipt_path"]
        receipt.write_text('{}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "receipt"):
            self.run_import()

    def test_public_prior_capture_is_refused_without_rewriting_it(self) -> None:
        text = self.register.read_text(encoding="utf-8")
        self.register.write_text(text.replace(" | restricted |", " | public |"), encoding="utf-8")
        plan = store.capture_artifact(self.root, "E-001", self.source, recorded_at=STAMP)
        old = store.capture_artifact(self.root, "E-001", self.source, recorded_at=STAMP, write=True, expected_sha256=plan["expected_sha256"])
        before = (self.root / old["path"]).read_bytes()
        self.register.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "restricted"):
            self.run_import()
        self.assertEqual((self.root / old["path"]).read_bytes(), before)
        self.assertFalse((self.root / "00-context/imports").exists())

    def test_public_capture_planner_snapshot_preserves_legacy_hash(self) -> None:
        before = store._capture_plan(self.root, "E-001", self.source, STAMP, store.MAX_SOURCE_BYTES)
        after = store._capture_plan(self.root, "E-001", self.source, STAMP, store.MAX_SOURCE_BYTES, source_bytes=self.source.read_bytes())
        self.assertEqual(before, after)
        for raw in (bytearray(b"mutable"), b"over the bound"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                store._capture_plan(self.root, "E-001", self.source, STAMP, 4, source_bytes=raw)

    def test_transaction_recovery_completes_capture_observation_and_receipt(self) -> None:
        plan = self.run_import()
        target = self.root / plan["receipt_path"]
        write_bytes = io.atomic_write_bytes
        def crash(path, raw):
            if path == target:
                raise OSError("synthetic crash before receipt target")
            write_bytes(path, raw)
        with patch.object(io, "atomic_write_bytes", side_effect=crash), self.assertRaises(OSError):
            self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        with self.assertRaisesRegex(OSError, "recovery required"):
            self.run_import()
        with io.vault_mutation_lock(self.root):
            pass
        retry = self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        self.assertTrue(target.is_file())
        self.assertTrue(retry["already_exists"])
        self.assertFalse(retry["observation_added"])
        ledger = json.loads(store.observation_path(self.root, "E-001").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["observations"]), 1)

    def test_receipt_only_old_event_cannot_replay_a_receipt_absent_token(self) -> None:
        raw_a = self.source.read_bytes()
        for raw, when in ((raw_a, STAMP), (raw_a + b" ", "2026-10-27T11:00:00Z")):
            self.source.write_bytes(raw)
            plan = store.capture_artifact(self.root, "E-001", self.source, recorded_at=when)
            store.capture_artifact(self.root, "E-001", self.source, recorded_at=when,
                                   write=True, expected_sha256=plan["expected_sha256"])
        self.source.write_bytes(raw_a)
        plan = self.run_import()
        self.assertFalse(plan["observation_added"])
        self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        with self.assertRaisesRegex(ValueError, "stale"):
            self.run_import(write=True, expected_sha256=plan["expected_sha256"])
        fresh = self.run_import()
        self.run_import(write=True, expected_sha256=fresh["expected_sha256"])
