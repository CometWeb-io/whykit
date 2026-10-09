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
    from mcp.shared.exceptions import MCPError
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
                server_info = client.server_info

        from whykit import __version__
        from whykit.mcp_server import output_schema

        self.assertIsNotNone(server_info)
        self.assertEqual((server_info.name, server_info.version), ("whykit", __version__))
        self.assertEqual(
            {tool.name for tool in response.tools},
            {"query", "context", "impact", "status", "pack", "trace", "backlinks"},
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
                self.assertEqual(tool.output_schema, {"type": "object", "anyOf": [output_schema(tool.name), output_schema(tool.name, contract_version=2)]})

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


def _write_note(vault: Path, relative: str, *, title: str, sensitivity: str, body: str, extra: str = "") -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\naliases: []\ntype: note\nstatus: approved\n"
        f"owner: Test owner\ncreated: 2026-09-24\nlast_updated: 2026-09-24\n"
        f"source_of_truth: false\nsensitivity: {sensitivity}\nsource_ids: []\ntags: []\n{extra}---\n\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )


# Values a hostile or confused client may send in place of a target.
MALICIOUS_TARGETS = (
    "../../etc/passwd",
    "/etc/passwd",
    "C:\\Windows\\win.ini",
    "file:///etc/passwd",
    "https://example.com/x",
    "~/.ssh/id_ed25519",
    "notes/./x",
    "notes/../../x",
    "a\x00b",
    "line\nbreak",
    "",
    "   ",
)


@unittest.skipIf(Client is None, "install the optional whykit[mcp] extra")
class McpStdioConformanceTests(unittest.IsolatedAsyncioTestCase):
    """Every tool, resource and prompt through the official SDK client over stdio."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        init = await asyncio.create_subprocess_exec(
            sys.executable, str(CLI), "init", "--minimal", str(self.vault),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await init.communicate()
        self.assertEqual(init.returncode, 0, stderr.decode("utf-8"))
        _write_note(self.vault, "notes/shared.md", title="Shared visible", sensitivity="internal",
                    body="visible-body [[notes/hub]]")
        _write_note(self.vault, "notes/hub.md", title="Hub", sensitivity="internal",
                    body="[[shared]] [[notes/shared]]")
        # A hidden twin of the visible stem, a hidden note and a hidden decision.
        _write_note(self.vault, "archive/shared.md", title="Shared hidden", sensitivity="restricted",
                    body="hidden-sentinel [[notes/hub]]")
        _write_note(self.vault, "archive/classified.md", title="Classified", sensitivity="restricted",
                    body="hidden-sentinel [[notes/hub]]")
        (self.vault / "06-decisions" / "d-001-hidden.md").write_text(
            "---\ntitle: Hidden decision\naliases: []\ntype: decision\ndecision_id: D-001\n"
            "status: approved\nowner: Test owner\ncreated: 2026-09-24\nlast_updated: 2026-09-24\n"
            "source_of_truth: false\nsensitivity: restricted\nsource_ids: []\ntags: []\n---\n\n"
            "# Hidden decision\n\nhidden-sentinel\n",
            encoding="utf-8",
        )
        (self.vault / "06-decisions" / "d-002-visible.md").write_text(
            "---\ntitle: Visible decision\naliases: []\ntype: decision\ndecision_id: D-002\n"
            "status: approved\nowner: Test owner\ncreated: 2026-09-24\nlast_updated: 2026-09-24\n"
            "source_of_truth: false\nsensitivity: internal\nsource_ids: []\ntags: []\n---\n\n"
            "# Visible decision\n\nvisible-decision-body [[notes/shared]]\n",
            encoding="utf-8",
        )
        self.before = {
            str(path.relative_to(self.vault)): path.read_bytes()
            for path in sorted(self.vault.rglob("*")) if path.is_file()
        }

    async def connected(self, check, *extra: str, mode: str = "auto") -> None:
        # The SDK client's task group must be entered and left in one task, so
        # each test opens its own stdio session instead of sharing one from
        # asyncSetUp.
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "whykit.mcp_server", "--root", str(self.vault), *extra],
        )
        async with asyncio.timeout(60):
            async with Client(parameters, read_timeout_seconds=10, mode=mode) as client:
                self.client = client
                await check()

    async def asyncTearDown(self) -> None:
        after = {
            str(path.relative_to(self.vault)): path.read_bytes()
            for path in sorted(self.vault.rglob("*")) if path.is_file()
        }
        self._tmp.cleanup()
        self.assertEqual(after, self.before, "an MCP call modified the vault")

    async def call(self, name: str, arguments: dict) -> tuple[dict, bool]:
        result = await self.client.call_tool(name, arguments)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload, result.structured_content)
        return payload, result.is_error

    async def _check_every_tool_answers_a_valid_call(self) -> None:
        for name, arguments in (
            ("query", {"text": "visible"}),
            ("context", {"target": "shared"}),
            ("impact", {"target": "notes/shared"}),
            ("status", {"today": "2026-09-24"}),
            ("pack", {"targets": ["D-002"], "query": "visible"}),
            ("trace", {"today": "2026-09-24"}),
            ("backlinks", {"target": "notes/hub"}),
        ):
            with self.subTest(tool=name):
                payload, is_error = await self.call(name, arguments)
                self.assertFalse(is_error, payload)
                self.assertNotIn("hidden-sentinel", json.dumps(payload))
                self.assertNotIn(str(self.vault), json.dumps(payload))
        context, _ = await self.call("context", {"target": "shared"})
        self.assertEqual(context["record"]["path"], "notes/shared.md")
        trace, _ = await self.call("trace", {"today": "2026-09-24"})
        self.assertEqual([record["decision_id"] for record in trace["decisions"]], ["D-002"])

    async def _check_malicious_targets_get_the_json_error_body_on_every_target_tool(self) -> None:
        for name in ("context", "impact", "backlinks"):
            for target in MALICIOUS_TARGETS:
                with self.subTest(tool=name, target=target):
                    payload, is_error = await self.call(name, {"target": target})
                    self.assertTrue(is_error)
                    self.assertEqual(payload["error"]["code"], "invalid_target")
                    self.assertNotIn("passwd", payload["error"]["message"])
        for target in MALICIOUS_TARGETS:
            with self.subTest(tool="pack", target=target):
                payload, is_error = await self.call("pack", {"targets": ["Home", target]})
                self.assertTrue(is_error)
                self.assertEqual(payload["error"]["code"], "invalid_target")

    async def _check_schema_violations_get_the_same_json_error_body(self) -> None:
        for name, arguments, code in (
            ("query", {"limit": -1}, "invalid_argument"),
            ("query", {"limit": "many"}, "invalid_argument"),
            ("query", {"text": "x" * 5000}, "invalid_argument"),
            ("query", {"canonical_only": "maybe"}, "invalid_argument"),
            ("context", {}, "invalid_target"),
            ("context", {"target": None}, "invalid_target"),
            ("context", {"target": ["Home"]}, "invalid_target"),
            ("context", {"target": "x" * 600}, "invalid_target"),
            ("context", {"target": "Home", "max_chars": -5}, "invalid_argument"),
            ("impact", {"target": 42}, "invalid_target"),
            ("status", {"due_days": -1}, "invalid_argument"),
            ("status", {"today": "x" * 100}, "invalid_argument"),
            ("pack", {"targets": ["Home"] * 21}, "invalid_argument"),
            ("pack", {"targets": "Home"}, "invalid_argument"),
            ("pack", {"targets": ["Home", 7]}, "invalid_target"),
            ("pack", {"targets": ["x" * 600]}, "invalid_target"),
            ("pack", {"max_docs": 0, "query": "x"}, "invalid_argument"),
            ("trace", {"limit": -1}, "invalid_argument"),
            ("trace", {"gaps_only": "sometimes"}, "invalid_argument"),
            ("trace", {"decision": "x" * 500}, "invalid_argument"),
            ("backlinks", {}, "invalid_target"),
            ("backlinks", {"target": "Home", "limit": -1}, "invalid_argument"),
        ):
            with self.subTest(tool=name, arguments=sorted(arguments)):
                payload, is_error = await self.call(name, arguments)
                self.assertTrue(is_error)
                self.assertEqual(payload["error"]["code"], code)
                self.assertEqual(set(payload), {"error"})
                self.assertNotIn("xxxx", payload["error"]["message"])
        # Handler-level validation has the same shape.
        for name, arguments in (
            ("query", {"source_id": "not-an-id"}),
            ("trace", {"decision": "E-001"}),
            ("trace", {"today": "2026-02-30"}),
        ):
            with self.subTest(tool=name, arguments=sorted(arguments)):
                payload, is_error = await self.call(name, arguments)
                self.assertTrue(is_error)
                self.assertEqual(payload["error"]["code"], "invalid_argument")
        payload, is_error = await self.call("delete_everything", {"target": "Home"})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "unknown_tool")

    async def _check_hidden_records_match_missing_ones_on_every_target_tool(self) -> None:
        for name in ("context", "impact", "backlinks"):
            for hidden, missing in (("archive/classified", "archive/never"), ("D-001", "D-999")):
                with self.subTest(tool=name, target=hidden):
                    hidden_payload, _ = await self.call(name, {"target": hidden})
                    missing_payload, _ = await self.call(name, {"target": missing})
                    for payload in (hidden_payload, missing_payload):
                        payload.pop("target")
                        payload.pop("id", None)
                    self.assertEqual(hidden_payload, missing_payload)
        hidden_trace, _ = await self.call("trace", {"decision": "D-001", "today": "2026-09-24"})
        missing_trace, _ = await self.call("trace", {"decision": "D-999", "today": "2026-09-24"})
        hidden_trace.pop("decision")
        missing_trace.pop("decision")
        self.assertEqual(hidden_trace, missing_trace)

    async def _check_resources_are_read_only_and_ceiling_filtered(self) -> None:
        listed = await self.client.list_resources()
        self.assertEqual([str(item.uri) for item in listed.resources], ["whykit://decisions", "whykit://record/D-002"])
        self.assertIsNone(listed.next_cursor)
        with self.assertRaises(MCPError) as caught:
            await self.client.list_resources(cursor="not-a-cursor", cache_mode="bypass")
        self.assertEqual(caught.exception.error.code, -32602)
        templates = await self.client.list_resource_templates()
        self.assertEqual([item.uri_template for item in templates.resource_templates], ["whykit://record/{+target}"])

        index = await self.client.read_resource("whykit://decisions")
        rows = json.loads(index.contents[0].text)["decisions"]
        self.assertEqual([row["decision_id"] for row in rows], ["D-002"])

        record = await self.client.read_resource("whykit://record/D-002")
        self.assertIn("visible-decision-body", json.loads(record.contents[0].text)["content"])
        nested = await self.client.read_resource("whykit://record/notes/shared")
        self.assertEqual(json.loads(nested.contents[0].text)["record"]["path"], "notes/shared.md")

        messages = {}
        for uri in ("whykit://record/D-001", "whykit://record/D-999", "whykit://record/archive/classified",
                    "whykit://record/archive/never"):
            with self.subTest(uri=uri):
                with self.assertRaises(MCPError) as caught:
                    await self.client.read_resource(uri)
                messages[uri] = caught.exception.error
                self.assertNotIn("hidden-sentinel", str(caught.exception))
        self.assertEqual(messages["whykit://record/D-001"].message, messages["whykit://record/D-999"].message)
        self.assertEqual(
            messages["whykit://record/archive/classified"].message, messages["whykit://record/archive/never"].message
        )
        for uri in ("whykit://record/..%2F..%2Fetc%2Fpasswd", "whykit://record/%2Fetc%2Fpasswd",
                    "whykit://record/~%2F.ssh"):
            with self.subTest(uri=uri):
                with self.assertRaises(MCPError) as caught:
                    await self.client.read_resource(uri)
                self.assertNotIn("root:", str(caught.exception))

    async def _check_prompts_embed_only_visible_data(self) -> None:
        listed = await self.client.list_prompts()
        self.assertEqual({prompt.name for prompt in listed.prompts}, {"summarize_decision", "review_evidence_gaps"})
        summary = await self.client.get_prompt("summarize_decision", {"decision_id": "D-002"})
        text = summary.messages[0].content.text
        self.assertIn("visible-decision-body", text)
        self.assertIn("untrusted_data", text)
        gaps = await self.client.get_prompt("review_evidence_gaps", {"today": "2026-09-24"})
        self.assertIn("D-002", gaps.messages[0].content.text)
        self.assertNotIn("hidden-sentinel", gaps.messages[0].content.text)
        errors = {}
        for decision_id in ("D-001", "D-999"):
            with self.assertRaises(MCPError) as caught:
                await self.client.get_prompt("summarize_decision", {"decision_id": decision_id})
            errors[decision_id] = str(caught.exception.error.message)
        self.assertEqual(errors["D-001"], errors["D-999"])
        with self.assertRaises(MCPError):
            await self.client.get_prompt("summarize_decision", {"decision_id": "../../etc/passwd"})

    async def _check_structured_results_follow_the_declared_output_schemas(self) -> None:
        from whykit.mcp_server import TOOL_NAMES, output_schema

        listed = await self.client.list_tools()
        self.assertEqual({tool.name: tool.output_schema for tool in listed.tools},
                         {name: {"type": "object", "anyOf": [output_schema(name), output_schema(name, contract_version=2)]} for name in TOOL_NAMES})
        # The SDK client validates every successful structuredContent against
        # the declared schema and raises on a mismatch.
        for name, arguments in (
            ("query", {"limit": 1}),
            ("context", {"target": "E-001"}),
            ("context", {"target": "archive/classified"}),
            ("impact", {"target": "E-001"}),
            ("status", {}),
            ("pack", {"query": "visible"}),
            ("trace", {"limit": 1}),
            ("backlinks", {"target": "notes/hub", "limit": 1}),
        ):
            with self.subTest(tool=name, arguments=arguments):
                result = await self.client.call_tool(name, arguments)
                self.assertFalse(result.is_error, result.structured_content)

    async def _check_tool_pages_follow_cursors(self) -> None:
        seen, cursor = [], ""
        for _ in range(200):
            payload, is_error = await self.call("query", {"limit": 1, "cursor": cursor})
            self.assertFalse(is_error, payload)
            seen += [item["path"] for item in payload["results"]]
            cursor = payload["next_cursor"]
            if cursor is None:
                break
        whole, _ = await self.call("query", {"limit": 100})
        self.assertEqual(seen, [item["path"] for item in whole["results"]])
        self.assertFalse([path for path in seen if path.startswith("archive/")])
        payload, is_error = await self.call("trace", {"limit": 1, "cursor": "AAAA"})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "invalid_cursor")

    async def _check_completions_offer_only_visible_records(self) -> None:
        from mcp_types import PromptReference, ResourceTemplateReference

        self.assertIsNotNone(self.client.server_capabilities.completions)
        template = ResourceTemplateReference(uri="whykit://record/{+target}")
        decisions = await self.client.complete(template, {"name": "target", "value": "D-"})
        self.assertEqual(decisions.completion.values, ["D-002"])
        paths = await self.client.complete(template, {"name": "target", "value": ""})
        self.assertIn("notes/shared.md", paths.completion.values)
        self.assertFalse([value for value in paths.completion.values if value.startswith("archive/")])
        prompt = await self.client.complete(PromptReference(name="summarize_decision"),
                                            {"name": "decision_id", "value": ""})
        self.assertEqual(prompt.completion.values, ["D-002"])
        hidden = await self.client.complete(template, {"name": "target", "value": "archive/"})
        self.assertEqual((hidden.completion.values, hidden.completion.total), ([], 0))

    async def _check_change_events_cover_visible_records_only(self) -> None:
        hidden = self.vault / "06-decisions" / "d-001-hidden.md"
        visible = self.vault / "06-decisions" / "d-002-visible.md"
        uris = ["whykit://decisions", "whykit://record/D-001", "whykit://record/D-002"]
        async with self.client.listen(resources_list_changed=True, resource_subscriptions=uris) as subscription:
            await asyncio.sleep(1.0)  # let the watcher record its baseline
            events: list = []

            async def collect() -> None:
                async for event in subscription:
                    events.append(event)

            async with asyncio.TaskGroup() as group:
                task = group.create_task(collect())
                try:
                    hidden.write_text(hidden.read_text(encoding="utf-8") + "\nhidden edit\n", encoding="utf-8")
                    await asyncio.sleep(1.5)
                    self.assertEqual(events, [], "a hidden edit produced a change event")
                    visible.write_text(visible.read_text(encoding="utf-8") + "\nvisible edit\n", encoding="utf-8")
                    for _ in range(50):
                        if events:
                            break
                        await asyncio.sleep(0.1)
                finally:
                    task.cancel()
                    hidden.write_bytes(self.before["06-decisions/d-001-hidden.md"])
                    visible.write_bytes(self.before["06-decisions/d-002-visible.md"])
        self.assertEqual([getattr(event, "uri", None) for event in events], ["whykit://record/D-002"])

    async def _check_handshake_clients_are_not_promised_subscriptions(self) -> None:
        capabilities = self.client.server_capabilities
        self.assertFalse(capabilities.resources.subscribe)
        self.assertIsNotNone(capabilities.completions)
        listed = await self.client.list_resources()
        self.assertEqual(len(listed.resources), 2)

    async def test_structured_results_follow_the_declared_output_schemas(self) -> None:
        await self.connected(self._check_structured_results_follow_the_declared_output_schemas)

    async def test_tool_pages_follow_cursors(self) -> None:
        await self.connected(self._check_tool_pages_follow_cursors)

    async def test_completions_offer_only_visible_records(self) -> None:
        await self.connected(self._check_completions_offer_only_visible_records)

    async def test_change_events_cover_visible_records_only(self) -> None:
        await self.connected(self._check_change_events_cover_visible_records_only, "--watch-interval", "0.2")

    async def test_handshake_clients_are_not_promised_subscriptions(self) -> None:
        await self.connected(self._check_handshake_clients_are_not_promised_subscriptions, mode="legacy")

    async def test_every_tool_answers_a_valid_call(self) -> None:
        await self.connected(self._check_every_tool_answers_a_valid_call)

    async def test_malicious_targets_get_the_json_error_body_on_every_target_tool(self) -> None:
        await self.connected(self._check_malicious_targets_get_the_json_error_body_on_every_target_tool)

    async def test_schema_violations_get_the_same_json_error_body(self) -> None:
        await self.connected(self._check_schema_violations_get_the_same_json_error_body)

    async def test_hidden_records_match_missing_ones_on_every_target_tool(self) -> None:
        await self.connected(self._check_hidden_records_match_missing_ones_on_every_target_tool)

    async def test_resources_are_read_only_and_ceiling_filtered(self) -> None:
        await self.connected(self._check_resources_are_read_only_and_ceiling_filtered)

    async def test_prompts_embed_only_visible_data(self) -> None:
        await self.connected(self._check_prompts_embed_only_visible_data)


@unittest.skipIf(Client is None, "install the optional whykit[mcp] extra")
class McpHttpTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_transport_requires_the_bearer_token(self) -> None:
        import socket

        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            token = "t0ken-" + "x" * 40
            token_file = Path(tmp) / "token"
            token_file.write_text(token + "\n", encoding="utf-8")
            server = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "whykit.mcp_server", "--root", str(ROOT / "examples" / "tiny"),
                "--http", "--port", str(port), "--token-file", str(token_file),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            url = f"http://127.0.0.1:{port}/mcp"
            try:
                async with asyncio.timeout(60), httpx2.AsyncClient() as http:
                    while True:
                        try:
                            denied = await http.post(url, json={})
                            break
                        except httpx2.TransportError:
                            await asyncio.sleep(0.1)
                    self.assertEqual(denied.status_code, 401)
                    self.assertEqual(denied.json()["error"]["code"], "unauthorized")
                    wrong = await http.post(url, json={}, headers={"Authorization": "Bearer " + "y" * 46})
                    self.assertEqual(wrong.status_code, 401)
                    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as authorized:
                        async with Client(streamable_http_client(url, http_client=authorized)) as client:
                            listed = await client.list_tools()
                            self.assertEqual(len(listed.tools), 7)
                            result = await client.call_tool("status", {})
                            self.assertFalse(result.is_error)
                            self.assertNotIn(tmp, result.content[0].text)
            finally:
                server.terminate()
                _, stderr = await server.communicate()
            self.assertIn(f"http://127.0.0.1:{port}/mcp", stderr.decode("utf-8"))
            self.assertNotIn(token, stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
