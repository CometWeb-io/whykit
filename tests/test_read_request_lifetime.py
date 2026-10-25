"""Request caches cannot carry stale filesystem paths into a later read."""
from __future__ import annotations
import contextvars
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from _vaults import fresh_vault
from whykit import lint, lsp
from whykit.mcp_server import VaultTools

class ReadRequestLifetimeTests(unittest.TestCase):
    def test_note_cache_checks_content_when_change_time_is_untrusted(self):
        from whykit import parse_cache
        from whykit.vault_index import NoteCache

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe.md"
            path.write_text("# Before\n", encoding="utf-8")
            cache = NoteCache()
            cache.racy_ns = 0
            cache.load_all([path])
            before = path.stat()
            stat, lstat = os.stat, os.lstat
            path.write_text("# After!\n", encoding="utf-8")
            def fixed_stat(target, *args, **kwargs):
                return before if os.fspath(target) == os.fspath(path) else stat(target, *args, **kwargs)
            def fixed_lstat(target, *args, **kwargs):
                return before if os.fspath(target) == os.fspath(path) else lstat(target, *args, **kwargs)
            with patch.object(parse_cache, "CHANGE_TIME_TRUSTED", False), patch.object(os, "stat", fixed_stat), patch.object(os, "lstat", fixed_lstat):
                self.assertEqual(cache.load_all([path])[0].text, "# After!\n")

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
