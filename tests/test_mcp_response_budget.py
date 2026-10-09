"""Whole-result byte limits, separate from document body character limits."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _vaults import fresh_vault
from whykit.mcp_server import MAX_MCP_RESULT_BYTES, _result_fits_budget, build_server

try:
    from mcp import Client, StdioServerParameters
    from mcp.shared.exceptions import MCPError
except ModuleNotFoundError as exc:
    if exc.name != "mcp":
        raise
    Client = None


class JsonBudgetTests(unittest.TestCase):
    def test_exact_utf8_boundary_including_json_escaping(self) -> None:
        for value in ("plain", "Ż🙂", '"\\\n\t\x00', {"text": "Ż🙂", "rows": [1, True, None]}):
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            with self.subTest(value=value):
                with patch("whykit.mcp_server.MAX_MCP_RESULT_BYTES", len(encoded)):
                    self.assertTrue(_result_fits_budget(value))
                with patch("whykit.mcp_server.MAX_MCP_RESULT_BYTES", len(encoded) - 1):
                    self.assertFalse(_result_fits_budget(value))


@unittest.skipIf(Client is None, "install the optional whykit[mcp] extra")
class McpResponseBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        fresh_vault(self.vault, "--minimal")
        # Each payload fits on its own; its text + structured copies do not.
        self.write_note("duplicated", "public", "x" * 600_000)
        # Resources and prompts have only a text copy, so test a larger field.
        self.write_note("oversized", "public", "Ż" * 600_000, decision=True)
        self.write_note("hidden", "restricted", "hidden-sentinel" * 100_000)

    def write_note(self, name: str, sensitivity: str, title: str, *, decision: bool = False) -> None:
        path = self.vault / "notes" / f"{name}.md"
        path.parent.mkdir(exist_ok=True)
        identifier = "decision_id: D-001\n" if decision else ""
        path.write_text(
            f"---\ntitle: {title}\ntype: {'decision' if decision else 'note'}\n"
            f"{identifier}status: draft\nowner: Ada Example\ncreated: 2026-10-09\n"
            f"last_updated: 2026-10-09\nsource_of_truth: false\n"
            f"sensitivity: {sensitivity}\nsource_ids: []\ntags: []\n---\n\nBody.\n",
            encoding="utf-8",
        )

    async def check_tools(self, client) -> None:
        for name, arguments in (
            ("query", {"text": "duplicated", "limit": 1}),
            ("context", {"target": "notes/duplicated", "max_chars": 0}),
            ("pack", {"targets": ["notes/duplicated"], "max_chars": 0}),
        ):
            with self.subTest(tool=name):
                result = await client.call_tool(name, arguments)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "response_too_large")
                self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                encoded = result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
                self.assertLessEqual(len(encoded), MAX_MCP_RESULT_BYTES)
                self.assertNotIn("hidden-sentinel", result.content[0].text)
                self.assertNotIn(str(self.vault), result.content[0].text)
        hidden = await client.call_tool("context", {"target": "notes/hidden", "max_chars": 0})
        missing = await client.call_tool("context", {"target": "notes/missing", "max_chars": 0})
        self.assertFalse(hidden.is_error)
        self.assertEqual(
            {key: value for key, value in hidden.structured_content.items() if key != "target"},
            {key: value for key, value in missing.structured_content.items() if key != "target"},
        )
        small = await client.call_tool("query", {"text": "Body", "limit": 0})
        self.assertFalse(small.is_error)

    async def check_resources_and_prompts(self, client) -> None:
        for read in (
            lambda: client.read_resource("whykit://record/notes/oversized"),
            lambda: client.read_resource("whykit://decisions"),
            lambda: client.list_resources(),
            lambda: client.get_prompt("summarize_decision", {"decision_id": "D-001"}),
        ):
            with self.assertRaises(MCPError) as caught:
                await read()
            error = caught.exception.error
            self.assertEqual(error.code, -32603)
            self.assertEqual(error.data["error"]["code"], "response_too_large")
            self.assertNotIn("hidden-sentinel", error.message)

    async def test_sdk_in_process_bounds_tools_resources_and_prompts(self) -> None:
        async with asyncio.timeout(30), Client(build_server(self.vault, max_sensitivity="public")) as client:
            await self.check_tools(client)
            await self.check_resources_and_prompts(client)

    async def test_stdio_enforces_the_same_budget(self) -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "whykit.mcp_server", "--root", str(self.vault), "--max-sensitivity", "public"],
        )
        async with asyncio.timeout(30), Client(parameters, read_timeout_seconds=10) as client:
            await self.check_tools(client)
            await self.check_resources_and_prompts(client)

    async def test_http_enforces_the_same_budget(self) -> None:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "whykit.mcp_server", "--root", str(self.vault),
            "--max-sensitivity", "public", "--http", "--port", str(port),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            async with asyncio.timeout(40), httpx2.AsyncClient() as http:
                while True:
                    try:
                        await http.post(url, json={})
                        break
                    except httpx2.TransportError:
                        await asyncio.sleep(0.1)
                async with Client(streamable_http_client(url, http_client=http)) as client:
                    await self.check_tools(client)
                    await self.check_resources_and_prompts(client)
        finally:
            server.terminate()
            _, stderr = await server.communicate()
            self.assertNotIn(b"hidden-sentinel", stderr)


if __name__ == "__main__":
    unittest.main()
