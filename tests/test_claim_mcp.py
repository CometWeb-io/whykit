from __future__ import annotations
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from _claims import TODAY, approved_claim_vault
from _jsonschema import validate
from test_claim_migration import inventory
from whykit.mcp_server import VaultTools, ToolFailure, output_schema
from whykit.scaffold import _frontmatter_replace


class ClaimMcpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.cid, self.cp, self.did, self.dp = approved_claim_vault(self.root)

    def test_readonly_claim_tools_schema_and_state(self):
        before = inventory(self.root)
        tools = VaultTools(self.root, max_sensitivity="restricted")
        reports = {
            "trace": tools.trace(today=TODAY.isoformat()),
            "context": tools.context(self.cid),
            "impact": tools.impact("E-001"),
            "query": tools.query(doc_type="claim"),
            "pack": tools.pack(targets=[self.did]),
            "status": tools.status(today=TODAY.isoformat()),
            "backlinks": tools.backlinks(self.cid),
        }
        for name, result in reports.items():
            with self.subTest(tool=name):
                self.assertEqual(result["contract_version"], 2)
                self.assertEqual(
                    validate(result, output_schema(name, contract_version=2)), []
                )
        self.assertEqual(
            reports["trace"]["decisions"][0]["claims"][0]["verification_status"],
            "disputed",
        )
        self.assertEqual(inventory(self.root), before)

    def test_hidden_claim_twin_and_private_artifacts_do_not_leak(self):
        missing = self.root.parent / "missing"
        shutil.copytree(self.root, missing)
        (missing / self.cp.relative_to(self.root)).unlink()
        (missing / self.dp.relative_to(self.root)).unlink()
        self.cp.write_text(
            _frontmatter_replace(
                self.cp.read_text(encoding="utf-8"), "sensitivity", "restricted"
            ),
            encoding="utf-8",
        )
        hidden = VaultTools(self.root, max_sensitivity="public")
        twin = VaultTools(missing, max_sensitivity="public")
        self.assertEqual(hidden.context(self.cid), twin.context(self.cid))
        receipts = (self.root / "00-context/review-log.md").read_text(encoding="utf-8")
        digests = [
            p.stem for p in (self.root / "00-context/claim-snapshots").glob("*.txt")
        ]
        payload = json.dumps(
            [
                hidden.query(),
                hidden.trace(today=TODAY.isoformat()),
                hidden.status(today=TODAY.isoformat()),
                hidden.pack(targets=[self.cid]),
            ]
        )
        for sentinel in [
            self.cp.name,
            "Cached decisions are readable offline.",
            *digests,
            "claim-receipt/v1:",
            str(self.root),
        ]:
            self.assertNotIn(sentinel, payload)
        self.assertIn("claim-receipt/v1:", receipts)

    def test_config_and_snapshot_change_invalidates_cursor(self):
        tools = VaultTools(self.root, max_sensitivity="restricted")
        first = tools.query(limit=1)
        self.assertTrue(first["next_cursor"])
        p = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaises(ToolFailure):
            tools.query(limit=1, cursor=first["next_cursor"])
        first = tools.query(limit=1)
        config = self.root / "whykit.toml"
        config.write_text(
            config.read_text(encoding="utf-8") + "\n# changed config\n",
            encoding="utf-8",
        )
        with self.assertRaises(ToolFailure):
            tools.query(limit=1, cursor=first["next_cursor"])

    def test_schema_versions_are_explicit_and_closed(self):
        for tool in ("trace", "context", "query"):
            schema = output_schema(tool, contract_version=2)
            self.assertEqual(schema["properties"]["contract_version"]["const"], 2)
            self.assertNotIn('"$ref"', json.dumps(schema))
        with self.assertRaises(ValueError):
            output_schema("trace", contract_version=99)


    def test_status_reuses_the_confined_captured_claim_view(self):
        from unittest.mock import patch
        from whykit.claims import capture_claims
        def guard(index, config):
            if index.confined:
                raise AssertionError("filtered readers must not reopen private raw inputs")
            return capture_claims(index, config)
        with patch("whykit.claims.capture_claims", side_effect=guard):
            self.assertEqual(VaultTools(self.root, max_sensitivity="public").status(today=TODAY.isoformat())["contract_version"], 2)


class ClaimSdkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ClaimMcpTests.setUp(self)

    async def test_sdk_publishes_both_versions_and_validates_structured_claims(self):
        try:
            from mcp import Client
        except ImportError:
            self.skipTest("optional MCP extra is not installed")
        from whykit.mcp_server import build_server

        async with Client(
            build_server(self.root, max_sensitivity="restricted")
        ) as client:
            listing = await client.list_tools()
            for tool in listing.tools:
                self.assertEqual(len(tool.output_schema["anyOf"]), 2)
            for name, args in (
                ("trace", {"today": TODAY.isoformat()}),
                ("context", {"target": self.cid}),
                ("impact", {"target": "E-001"}),
            ):
                result = await client.call_tool(name, args)
                self.assertFalse(result.is_error)
                self.assertEqual(
                    validate(
                        result.structured_content,
                        output_schema(name, contract_version=2),
                    ),
                    [],
                )

    async def test_claim_metadata_respects_whole_result_budget(self):
        try:
            from mcp import Client
        except ImportError:
            self.skipTest("optional MCP extra is not installed")
        from whykit.mcp_server import build_server, MAX_MCP_RESULT_BYTES

        self.cp.write_text(
            _frontmatter_replace(
                self.cp.read_text(encoding="utf-8"), "title", "Ż" * 400_000
            ),
            encoding="utf-8",
        )
        async with Client(
            build_server(self.root, max_sensitivity="restricted")
        ) as client:
            result = await client.call_tool("query", {"type": "claim"})
            self.assertTrue(result.is_error)
            self.assertEqual(
                result.structured_content["error"]["code"], "response_too_large"
            )
            self.assertLessEqual(
                len(result.model_dump_json(by_alias=True).encode("utf-8")),
                MAX_MCP_RESULT_BYTES,
            )
            self.assertNotIn(str(self.root), result.content[0].text)
