"""Probe the pinned SDK boundary before any transport starts."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import types
import unittest
from unittest.mock import patch

from whykit.mcp_server import _require_mcp


@unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP extra")
class SDKContractTests(unittest.TestCase):
    def test_missing_transport_module_is_an_actionable_startup_error(self):
        server = types.ModuleType('mcp.server')
        server.__path__ = []
        server.MCPServer = lambda: None
        stderr = io.StringIO()
        with patch.dict(sys.modules, {'mcp.server': server, 'mcp.server.runner': None, 'mcp.server.stdio': None}), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                _require_mcp()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('Unsupported MCP SDK', stderr.getvalue())

    def test_supported_sdk_exposes_the_transport_contract(self):
        self.assertTrue(callable(_require_mcp()))

    def test_missing_private_capability_is_an_actionable_startup_error(self):
        from mcp.server import runner, stdio
        for module, name in ((stdio, "_claim_fd"), (stdio, "_open_stdin_diversion"),
                             (stdio, "_open_stdout_diversion"), (runner, "serve_dual_era_loop")):
            with self.subTest(capability=name), patch.object(module, name, None):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    _require_mcp()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("Unsupported MCP SDK", stderr.getvalue())
