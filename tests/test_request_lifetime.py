"""Copied contexts cannot extend a read request's paths or sensitivity projection."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _vaults import fresh_vault
from whykit import io, lint, lsp
from whykit.mcp_server import VaultTools


class RequestLifetimeTests(unittest.TestCase):
    def test_expired_mcp_context_rechecks_a_changed_sensitivity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root, "--minimal")
            note = root / "notes/probe.md"
            note.write_text("---\ntitle: Scoped probe\nsensitivity: public\n---\n", encoding="utf-8")
            tools = VaultTools(root, max_sensitivity="public")
            def capture():
                self.assertEqual(tools.query(text="Scoped probe")["total"], 1)
                return contextvars.copy_context()
            context = tools.scoped(capture)
            with io.vault_mutation_lock(root):
                io.apply_transaction(root, {note: note.read_text(encoding="utf-8").replace("public", "restricted")})
            self.assertEqual(tools.query(text="Scoped probe")["total"], 0)
            self.assertEqual(context.run(tools.query, text="Scoped probe")["total"], 0)

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges on Windows")
    def test_expired_path_context_cannot_admit_a_retargeted_outside_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "vault"
            root.mkdir()
            inside, outside, link = root / "inside.md", base / "outside.md", root / "alias.md"
            inside.write_text("# inside\n", encoding="utf-8")
            outside.write_text("# outside\n", encoding="utf-8")
            link.symlink_to(inside)
            with lint.path_cache():
                self.assertIn(link, lint.collect_markdown(root, []))
                context = contextvars.copy_context()
            link.unlink()
            link.symlink_to(outside)
            self.assertNotIn(link, context.run(lint.collect_markdown, root, []))
            self.assertEqual(context.run(lint._real, link), outside)

    def test_live_copied_path_and_evidence_views_belong_to_their_thread(self):
        root = Path(tempfile.gettempdir()).resolve() / "whykit-view-fixture"
        with lint.evidence_view(root, ({"E-001": {"source": "parent"}}, {}, [])), lint.path_cache():
            parent = lint._REQUEST.get()
            context = contextvars.copy_context()
            def read():
                with lint.path_cache():
                    return lint._REQUEST.get(), lint.evidence_register(root)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                child, rows = pool.submit(context.run, read).result(timeout=5)
            self.assertIsNot(child, parent)
            self.assertEqual(rows, ({}, {}, []))

    def test_live_copied_path_and_evidence_views_belong_to_their_async_task(self):
        async def check():
            root = Path(tempfile.gettempdir()).resolve() / "whykit-view-fixture"
            with lint.path_cache(), lint.evidence_view(root, ({"E-001": {}}, {}, [])):
                parent = lint._REQUEST.get()
                async def read():
                    with lint.path_cache():
                        return lint._REQUEST.get(), lint.evidence_register(root)
                child, rows = await asyncio.create_task(read())
                self.assertIsNot(child, parent)
                self.assertEqual(rows, ({}, {}, []))
        asyncio.run(check())

    def test_expired_evidence_view_is_not_a_persistent_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with lint.evidence_view(root, ({"E-001": {"source": "old"}}, {}, [])):
                self.assertIn("E-001", lint.evidence_register(root)[0])
                context = contextvars.copy_context()
            self.assertEqual(context.run(lint.evidence_register, root), ({}, {}, []))

    def test_nested_requests_keep_one_visible_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root, "--minimal")
            tools = VaultTools(root)
            with patch.object(tools.notes, "load_all", wraps=tools.notes.load_all) as load:
                def read():
                    parent = lint._REQUEST.get()
                    first = tools.visible_index()
                    self.assertIs(tools.scoped(tools.visible_index), first)
                    self.assertIs(lint._REQUEST.get(), parent)
                tools.scoped(read)
                self.assertEqual(load.call_count, 1)

    def test_expired_inner_evidence_view_does_not_survive_an_outer_path_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root, "--minimal")
            with lint.path_cache():
                with lint.evidence_view(root, ({"E-999": {}}, {}, [])):
                    context = contextvars.copy_context()
                self.assertNotIn("E-999", context.run(lint.evidence_register, root)[0])

    def test_expired_inner_mcp_view_does_not_survive_an_outer_path_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root, "--minimal")
            tools = VaultTools(root)
            with lint.path_cache():
                def capture():
                    tools.visible_index()
                    return contextvars.copy_context()
                context = tools.scoped(capture)
                with patch.object(tools.notes, "load_all", wraps=tools.notes.load_all) as load:
                    context.run(tools.query)
                    self.assertEqual(load.call_count, 1)

    def test_live_mcp_context_in_another_thread_rebuilds_the_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root, "--minimal")
            tools = VaultTools(root)
            def read():
                tools.visible_index()
                context = contextvars.copy_context()
                with patch.object(tools.notes, "load_all", wraps=tools.notes.load_all) as load:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(context.run, tools.query).result(timeout=5)
                    self.assertEqual(load.call_count, 1)
            tools.scoped(read)

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges on Windows")
    def test_separate_mcp_calls_refresh_even_inside_an_outer_path_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "vault"
            fresh_vault(root, "--minimal")
            inside, outside, link = root / "notes/inside.md", base / "outside.md", root / "notes/alias.md"
            inside.write_text("---\ntitle: Inside original\nsensitivity: public\n---\n", encoding="utf-8")
            outside.write_text("---\ntitle: Outside secret canary\nsensitivity: public\n---\n", encoding="utf-8")
            link.symlink_to(inside)
            tools = VaultTools(root, max_sensitivity="public")
            with lint.path_cache():
                self.assertGreater(tools.query(text="Inside original")["total"], 0)
                link.unlink()
                link.symlink_to(outside)
                self.assertEqual(tools.query(text="Outside secret canary")["total"], 0)

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges on Windows")
    def test_lsp_definition_and_document_links_recheck_the_current_target(self):
        from test_lsp import InProcess, _open
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "vault"
            fresh_vault(root, "--minimal")
            inside, outside, link = root / "notes/inside.md", base / "outside.md", root / "notes/alias.md"
            inside.write_text("# inside\n", encoding="utf-8")
            outside.write_text("# outside\n", encoding="utf-8")
            link.symlink_to(inside)
            client = InProcess(debounce=0)
            client.initialize(root)
            uri = _open(client, root / "Home.md", "[[notes/alias]]\n")
            params = {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}}
            self.assertIsNotNone(client.request("textDocument/definition", params)["result"])
            link.unlink()
            link.symlink_to(outside)
            self.assertIsNone(client.request("textDocument/definition", params)["result"])
            self.assertEqual(client.request("textDocument/documentLink", {"textDocument": {"uri": uri}})["result"], [])

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges on Windows")
    def test_evidence_register_cannot_read_an_outside_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "vault"
            (root / "00-context").mkdir(parents=True)
            outside = base / "outside.md"
            outside.write_text("| ID | Source | Type | Date | Accessed | Location | Claims |\n"
                               "|---|---|---|---|---|---|---|\n"
                               "| E-999 | Outside secret canary | internal | 2026-01-01 | 2026-01-01 | here | claim |\n", encoding="utf-8")
            (root / "00-context/evidence-register.md").symlink_to(outside)
            self.assertEqual(lint.evidence_register(root), ({}, {}, []))

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges on Windows")
    def test_lsp_auto_discovery_and_open_buffers_cannot_follow_outside_sources(self):
        from test_lsp import InProcess, _open
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "vault"
            fresh_vault(root, "--minimal")
            inside, outside, link = root / "notes/inside.md", base / "outside.md", root / "notes/alias.md"
            inside.write_text("[[INSIDE_CANARY]]\n", encoding="utf-8")
            outside.write_text("[[OUTSIDE_SECRET_CANARY]]\n", encoding="utf-8")
            link.symlink_to(inside)
            opened = InProcess(debounce=0)
            opened.initialize(root)
            uri = _open(opened, link)
            link.unlink()
            link.symlink_to(outside)
            unopened = InProcess(debounce=0)
            unopened.initialize()
            params = {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}}
            for client in (opened, unopened):
                with self.subTest(open_buffer=client is opened):
                    self.assertIsNone(client.request("textDocument/hover", params)["result"])

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges on Windows")
    def test_lsp_requests_do_not_share_a_stale_resolution_cache(self):
        from test_lsp import InProcess, _open
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "vault"
            fresh_vault(root, "--minimal")
            inside, outside, link = root / "Home.md", base / "outside.md", root / "alias.md"
            outside.write_text("# outside\n", encoding="utf-8")
            link.symlink_to(inside)
            client = InProcess(debounce=0)
            client.initialize(root)
            uri = _open(client, inside)
            params = {"textDocument": {"uri": uri}}
            with patch.dict(lsp._HANDLERS, {"test/resolve": lambda server, args: os.fspath(lint._real(link))}):
                self.assertEqual(client.request("test/resolve", params)["result"], os.fspath(inside))
                link.unlink()
                link.symlink_to(outside)
                self.assertEqual(client.request("test/resolve", params)["result"], os.fspath(outside))
