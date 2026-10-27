"""Native global ledgers never become raw MCP records, at any ceiling.

Fixtures use real writers on synthetic data. No model, network or host settings.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from whykit import mcp_server
from whykit.mcp_server import RECORD_TEMPLATE, VaultTools, VaultWatcher
from whykit.review import record_review
from whykit.scaffold import _frontmatter_replace, create_decision

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 9, 17)
TITLE = "MCP_RESTRICTED_DECISION_TITLE_CANARY"
REVIEWER = "MCP_RESTRICTED_REVIEWER_CANARY"
REVIEW_NOTE = "MCP_RESTRICTED_REVIEW_NOTE_CANARY"
LEDGERS = ("06-decisions/decision-log.md", "00-context/review-log.md")
CEILINGS = ("public", "internal", "confidential", "restricted")

try:
    from mcp import Client
    from mcp.shared.exceptions import MCPError
except ModuleNotFoundError as exc:
    if exc.name != "mcp":
        raise
    Client = None


def ledger_path(name: str) -> bool:
    return name.replace("\\", "/").casefold() in LEDGERS


def tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def fixture(root: Path) -> str:
    shutil.copytree(ROOT / "examples/approval", root)
    identity, _ = create_decision(root, TITLE, owner="Synthetic Reviewer", sensitivity="restricted",
                                   source_ids=["E-001"], today=DAY)
    record_review(root, identity, reviewer=REVIEWER, outcome="update-required", note_text=REVIEW_NOTE, today=DAY)
    return identity


class McpLedgerScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "vault"
        self.hidden = fixture(self.root)

    def test_native_ledger_context_resource_query_and_pack_are_withheld_at_every_ceiling(self) -> None:
        before = tree(self.root)
        for ceiling in CEILINGS:
            tools = VaultTools(self.root, max_sensitivity=ceiling)
            with self.subTest(ceiling=ceiling):
                for path in LEDGERS:
                    self.assertFalse(tools.context(path)["exists"])
                    self.assertIsNone(tools.read_record(path))
                    self.assertFalse(tools.impact(path)["exists"])
                    self.assertFalse(tools.backlinks(path)["exists"])
                    self.assertEqual(tools.pack(targets=[path])["contexts"], [])
                    values = tools.complete("ref/resource", RECORD_TEMPLATE, "target", path.rsplit(".", 1)[0])["values"]
                    self.assertEqual(values, [])
                for marker in (TITLE, REVIEWER, REVIEW_NOTE):
                    query = tools.query(marker)
                    self.assertFalse(any(ledger_path(row["path"]) for row in query["results"]))
                    bundle = tools.pack(query=marker)
                    self.assertFalse(any(ledger_path(row["record"].get("path", "")) for row in bundle["contexts"]))
                    if ceiling != "restricted":
                        self.assertEqual(query["total"], 0)
                        self.assertEqual(bundle["contexts"], [])
                index = tools.visible_index()
                for captured in (index, index.derived["full_index"]):
                    self.assertFalse(any(ledger_path(captured.relative(note.path)) for note in captured.notes))
        self.assertEqual(tree(self.root), before)

    def test_uppercase_extension_component_aliases_and_metadata_aliases_cannot_reopen_ledgers(self) -> None:
        targets = list(LEDGERS)
        for relative in LEDGERS:
            original = self.root / relative
            alias = "GLOBAL_LEDGER_" + original.stem.replace("-", "_").upper()
            original.write_text(_frontmatter_replace(original.read_text(encoding="utf-8"), "aliases", json.dumps([alias])), encoding="utf-8")
            alias_path = self.root / ("06-Decisions/Decision-Log.MD" if relative.startswith("06-") else "00-Context/Review-Log.MD")
            alias_path.parent.mkdir(parents=True, exist_ok=True)
            if alias_path.exists() and os.path.samefile(original, alias_path):
                original.rename(alias_path)
            else:
                shutil.copyfile(original, alias_path)
            targets += [alias, alias_path.relative_to(self.root).as_posix(), alias_path.relative_to(self.root).with_suffix("").as_posix()]
        before = tree(self.root)
        for ceiling in CEILINGS:
            tools = VaultTools(self.root, max_sensitivity=ceiling)
            with self.subTest(ceiling=ceiling):
                for target in targets:
                    self.assertFalse(tools.context(target)["exists"], target)
                    self.assertIsNone(tools.read_record(target), target)
                    self.assertEqual(tools.pack(targets=[target])["contexts"], [], target)
                for marker in (TITLE, REVIEWER, REVIEW_NOTE):
                    result = tools.query(marker)
                    self.assertFalse(any(ledger_path(row["path"]) for row in result["results"]))
                    if ceiling != "restricted":
                        self.assertEqual(result["total"], 0)
                status = tools.status(today=DAY.isoformat())
                self.assertFalse(any(ledger_path(row["path"]) for row in status["findings"]))
        self.assertEqual(tree(self.root), before)

    def test_public_nonledger_context_resources_prompts_and_evidence_still_work(self) -> None:
        tools = VaultTools(self.root, max_sensitivity="public")
        before = tree(self.root)
        record = tools.context("D-001")
        self.assertTrue(record["exists"])
        self.assertTrue(record["record"]["path"].startswith("06-decisions/d-001-"))
        self.assertEqual(record["record"]["status"], "approved")
        self.assertTrue(tools.read_record("D-001")["exists"])
        self.assertTrue(tools.context("E-001")["exists"])
        self.assertTrue(tools.query("reviewed record")["results"])
        self.assertTrue(tools.pack(targets=["D-001"])["contexts"])
        self.assertNotIn("decision_log.unindexed", {row["code"] for row in record.get("findings", [])})
        prompt = tools.summarize_decision_prompt("D-001")
        self.assertIn("D-001", prompt)
        self.assertNotIn(TITLE, prompt)
        self.assertFalse(tools.context(self.hidden)["exists"])
        self.assertIsNone(tools.read_record(self.hidden))
        self.assertEqual(tree(self.root), before)

    def test_hidden_native_writer_changes_leave_public_queries_cursors_status_and_watcher_unchanged(self) -> None:
        tools = VaultTools(self.root, max_sensitivity="public")
        query = tools.query("", limit=1)
        status = tools.status(today=DAY.isoformat())
        context = tools.context("D-001")
        watcher = VaultWatcher(tools)
        snapshot = watcher._snapshot()
        self.assertEqual(watcher.poll(), [])
        identity, _ = create_decision(self.root, "ANOTHER_PRIVATE_LEDGER_CANARY", owner="Synthetic Reviewer",
                                       sensitivity="restricted", source_ids=["E-001"], today=DAY)
        record_review(self.root, identity, reviewer="Another private reviewer", outcome="update-required",
                       note_text="Another private review note", today=DAY)
        self.assertEqual(tools.query("", limit=1), query)
        if query["next_cursor"]:
            tools.query("", limit=1, cursor=query["next_cursor"])
        self.assertEqual(tools.status(today=DAY.isoformat()), status)
        self.assertEqual(tools.context("D-001"), context)
        self.assertEqual(watcher._snapshot(), snapshot)
        self.assertEqual(watcher.poll(), [])

    def test_public_handlers_do_not_hash_raw_ledger_bodies_or_read_them_via_confined_lint(self) -> None:
        tools = VaultTools(self.root, max_sensitivity="public")
        original_sha = hashlib.sha256
        def visible_hash(raw=b"", *args, **kwargs):
            for marker in (TITLE, REVIEWER, REVIEW_NOTE):
                self.assertNotIn(marker.encode(), raw)
            return original_sha(raw, *args, **kwargs)
        # Loading the initial native index is expected. Only legacy lint must not reopen omitted logs.
        import whykit.lint as lint_module
        original_read = lint_module.read_vault_text
        with patch.object(hashlib, "sha256", side_effect=visible_hash):
            tools.query(TITLE)
            tools.context("D-001")
            tools.status(today=DAY.isoformat())
            VaultWatcher(tools)._snapshot()
        loading = False
        original_load = tools.notes.load_all
        def captured_index(paths):
            nonlocal loading
            loading = True
            try:
                return original_load(paths)
            finally:
                loading = False
        def confined_read(path):
            if not loading:
                self.assertFalse(ledger_path(path.relative_to(self.root).as_posix()))
            return original_read(path)
        with patch.object(tools.notes, "load_all", side_effect=captured_index), patch.object(lint_module, "read_vault_text", side_effect=confined_read):
            tools.status(today=DAY.isoformat())


@unittest.skipIf(Client is None, "install optional whykit[mcp] extra")
class McpLedgerSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_in_process_mcp_tools_and_record_resources_cannot_bypass_ledger_scope(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / "vault"
        fixture(root)
        async with Client(mcp_server.build_server(root, max_sensitivity="public")) as client:
            for path in LEDGERS:
                response = await client.call_tool("context", {"target": path})
                self.assertFalse(response.is_error)
                self.assertFalse(response.structured_content["exists"])
                with self.assertRaises(MCPError):
                    await client.read_resource("whykit://record/" + path)
            query = await client.call_tool("query", {"text": TITLE})
            self.assertFalse(query.is_error)
            self.assertEqual(query.structured_content["total"], 0)
            public = await client.read_resource("whykit://record/D-001")
            self.assertTrue(public.contents)
