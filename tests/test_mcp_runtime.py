"""Integration contract for the optional MCP SDK extra."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"

try:
    from mcp import Client, StdioServerParameters
except ModuleNotFoundError as exc:  # The core matrix intentionally omits the extra.
    if exc.name != "mcp":
        raise
    Client = None


@unittest.skipIf(Client is None, "install the optional whykit[mcp] extra")
class McpRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_exposes_only_its_read_only_tools_to_an_mcp_client(self) -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "whykit.mcp_server",
                "--root",
                str(ROOT / "examples" / "tiny"),
            ],
        )

        async with asyncio.timeout(20):
            async with Client(
                parameters, raise_exceptions=True, read_timeout_seconds=5
            ) as client:
                response = await client.list_tools()

        self.assertEqual(
            {tool.name for tool in response.tools},
            {"query", "context", "impact", "status", "pack"},
        )
        for tool in response.tools:
            with self.subTest(tool=tool.name):
                self.assertTrue(tool.title)
                self.assertTrue(tool.description)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertTrue(tool.annotations.idempotent_hint)
                self.assertFalse(tool.annotations.open_world_hint)
                for name, schema in tool.input_schema["properties"].items():
                    self.assertTrue(schema.get("description"), f"{tool.name}.{name} has no description")

    async def test_in_process_errors_are_structured_tool_results(self) -> None:
        from whykit.mcp_server import build_server

        server = build_server(ROOT / "examples" / "northline")
        async with asyncio.timeout(20):
            async with Client(server) as client:
                for tool_name, arguments, code in (
                    ("context", {"target": "../../etc/passwd"}, "invalid_target"),
                    ("impact", {"target": "/etc/passwd"}, "invalid_target"),
                    ("impact", {"target": "C:\\Windows\\win.ini"}, "invalid_target"),
                    ("context", {"target": "file:///etc/passwd"}, "invalid_target"),
                    ("pack", {"targets": ["Home", "~/.ssh/id_ed25519"]}, "invalid_target"),
                    ("pack", {}, "invalid_argument"),
                    ("query", {"source_id": "not-an-id"}, "invalid_argument"),
                    ("status", {"today": "not-a-date"}, "invalid_argument"),
                ):
                    with self.subTest(tool=tool_name, arguments=arguments):
                        result = await client.call_tool(tool_name, arguments)
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.structured_content["error"]["code"], code)
                        self.assertEqual(json.loads(result.content[0].text), result.structured_content)

                # Arguments outside the advertised schema never reach the handler.
                for tool_name, arguments in (
                    ("query", {"limit": -1}),
                    ("context", {"target": "x" * 600}),
                    ("context", {}),
                    ("pack", {"targets": ["Home"] * 21}),
                ):
                    with self.subTest(tool=tool_name, arguments=list(arguments)):
                        result = await client.call_tool(tool_name, arguments)
                        self.assertTrue(result.is_error)

                ok = await client.call_tool("status", {"today": "2026-09-17"})
                self.assertFalse(ok.is_error)
                self.assertNotIn(str(ROOT), ok.content[0].text)
                bundle = await client.call_tool("pack", {"targets": ["D-001"], "max_chars": 500})
                self.assertFalse(bundle.is_error)
                self.assertEqual(bundle.structured_content["resolved"], 1)

    async def test_stdio_tools_enforce_sensitivity_on_real_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            init = await asyncio.create_subprocess_exec(
                sys.executable,
                str(CLI),
                "init",
                str(vault),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await init.communicate()
            self.assertEqual(init.returncode, 0, stderr.decode("utf-8"))
            self.assertIn(b"WhyKit vault created", stdout)

            notes = vault / "notes"
            notes.mkdir(exist_ok=True)
            for filename, title, sensitivity, body in (
                ("public-note.md", "Public note", "public", "public-sentinel E-001"),
                ("restricted-note.md", "Restricted note", "restricted", "restricted-sentinel"),
            ):
                (notes / filename).write_text(
                    f"---\ntitle: {title}\naliases: []\ntype: note\n"
                    f"status: draft\nowner: Test owner\ncreated: 2026-09-24\n"
                    f"last_updated: 2026-09-24\nsource_of_truth: false\n"
                    f"sensitivity: {sensitivity}\n"
                    f'source_ids: ["E-001"]\ntags: []\n---\n\n'
                    f"# {title}\n\n{body}\n",
                    encoding="utf-8",
                )
            evidence_register = vault / "00-context" / "evidence-register.md"
            evidence_register.write_text(
                evidence_register.read_text(encoding="utf-8").replace(
                    "| E-001 |  | interview / report / analytics / vendor doc / internal |  |  |  |  |",
                    "| E-001 | Private interview | interview | 2026-09-24 | 2026-09-24 | notes/private.md | private-evidence-sentinel |",
                ),
                encoding="utf-8",
            )
            (vault / "06-decisions" / "d-001-restricted.md").write_text(
                "---\ntitle: Restricted decision\naliases: []\ntype: decision\n"
                "decision_id: D-001\nstatus: draft\nowner: Test owner\n"
                "created: 2026-09-24\nlast_updated: 2026-09-24\n"
                "source_of_truth: false\nsensitivity: restricted\n"
                "source_ids: []\ntags: []\n---\n\n"
                "# Restricted decision\n\nrestricted-decision-sentinel\n",
                encoding="utf-8",
            )

            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "whykit.mcp_server",
                    "--root",
                    str(vault),
                    "--max-sensitivity",
                    "public",
                ],
            )
            async with asyncio.timeout(30):
                async with Client(parameters, read_timeout_seconds=5) as client:
                    query = await client.call_tool("query", {"text": "restricted-sentinel"})
                    query_payload = json.loads(query.content[0].text)
                    self.assertEqual(query_payload["results"], [])

                    public_context = await client.call_tool(
                        "context", {"target": "notes/public-note", "max_chars": 4000}
                    )
                    self.assertFalse(public_context.is_error)
                    public_payload = json.loads(public_context.content[0].text)
                    self.assertIn("public-sentinel", public_payload["content"])
                    self.assertEqual(public_payload["evidence"], [])
                    self.assertNotIn("private-evidence-sentinel", public_context.content[0].text)

                    hidden_pack = await client.call_tool("pack", {"targets": ["notes/restricted-note", "D-001"]})
                    missing_pack = await client.call_tool("pack", {"targets": ["notes/missing-note", "D-999"]})
                    hidden_bundle = json.loads(hidden_pack.content[0].text)
                    missing_bundle = json.loads(missing_pack.content[0].text)
                    for bundle in (hidden_bundle, missing_bundle):
                        bundle.pop("requested_targets")
                        bundle.pop("selected")
                        for item in bundle["missing"]:
                            item.pop("target")
                    self.assertEqual(hidden_bundle, missing_bundle)
                    self.assertNotIn("sentinel", hidden_pack.content[0].text)

                    public_status = await client.call_tool("status", {})
                    self.assertFalse(public_status.is_error)
                    for sentinel in ("restricted-note", "d-001-restricted", "D-001", str(vault)):
                        self.assertNotIn(sentinel, public_status.content[0].text)

                    for tool_name in ("context", "impact"):
                        for hidden_target, missing_target, sentinel in (
                            ("notes/restricted-note", "notes/missing-note", "restricted-sentinel"),
                            ("D-001", "D-999", "restricted-decision-sentinel"),
                            ("E-001", "E-999", "private-evidence-sentinel"),
                        ):
                            with self.subTest(tool=tool_name, target=hidden_target):
                                result = await client.call_tool(
                                    tool_name, {"target": hidden_target}
                                )
                                self.assertFalse(result.is_error)
                                hidden_payload = json.loads(result.content[0].text)
                                missing = await client.call_tool(
                                    tool_name, {"target": missing_target}
                                )
                                missing_payload = json.loads(missing.content[0].text)
                                hidden_payload["target"] = missing_payload["target"]
                                self.assertEqual(hidden_payload, missing_payload)
                                self.assertNotIn(sentinel, result.content[0].text)


if __name__ == "__main__":
    unittest.main()
