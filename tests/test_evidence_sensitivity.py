"""Row classification is preserved by writers and every filtered reader."""
from __future__ import annotations

import datetime as dt
import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _vaults import fresh_vault
from whykit.evidence import retire_evidence
from whykit.explorer_index import build_explorer_index
from whykit.lint import evidence_register, lint
from whykit.mcp_server import RECORD_TEMPLATE, VaultTools, VaultWatcher
from whykit.scaffold import create_evidence
from whykit.sensitivity import classified_evidence
from whykit.vault_index import VaultIndex

try:
    from mcp import Client, StdioServerParameters
    from mcp.shared.exceptions import MCPError
except ModuleNotFoundError as exc:
    if exc.name != "mcp":
        raise
    Client = None

TODAY = dt.date(2026, 9, 17)


class EvidenceSensitivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "vault"
        fresh_vault(self.root, "--minimal")
        self.register = self.root / "00-context/evidence-register.md"
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: public"), encoding="utf-8")
        self.hidden = self.create("Restricted sentinel", "restricted")
        self.public = self.create("Public fixture", "public")
        self.internal = self.create("Internal sentinel", "internal")
        notes = self.root / "notes"
        notes.mkdir(exist_ok=True)
        for name, eid in (("public", self.public), ("hidden-citation", self.hidden)):
            (notes / f"{name}.md").write_text(
                "---\ntitle: " + name + "\ntype: note\nstatus: draft\nowner: Ada Example\n"
                "created: 2026-09-17\nlast_updated: 2026-09-17\nsource_of_truth: false\n"
                f"sensitivity: public\nsource_ids: [{eid}]\ntags: []\n---\n\nCites {eid}.\n"
            , encoding="utf-8")
        self.tools = VaultTools(self.root, max_sensitivity="public")

    def create(self, source: str, sensitivity: str | None) -> str:
        return create_evidence(self.root, source=source, kind="fixture", location="https://example.com/source",
                               claims="Synthetic claim", sensitivity=sensitivity, today=TODAY)

    def test_create_extend_append_and_retire_preserve_classification(self) -> None:
        active, _, _ = evidence_register(self.root)
        self.assertEqual({key: row["sensitivity"] for key, row in active.items()},
                         {self.hidden: "restricted", self.public: "public", self.internal: "internal"})
        inherited = self.create("Inherited fixture", None)
        self.assertEqual(evidence_register(self.root)[0][inherited]["sensitivity"], "public")
        retire_evidence(self.root, self.hidden, reason="Synthetic retirement", replaced_by=self.public, today=TODAY)
        active, retired, _ = evidence_register(self.root)
        self.assertNotIn(self.hidden, active)
        self.assertEqual(retired[self.hidden]["sensitivity"], "restricted")
        self.assertEqual(self.tools.context(self.hidden)["exists"], False)
        self.assertEqual(self.tools.context(self.public)["evidence"]["record"]["sensitivity"], "public")

    def test_register_floor_cannot_be_lowered_by_public_row(self) -> None:
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("sensitivity: public", "sensitivity: restricted"), encoding="utf-8")
        self.assertFalse(self.tools.context(self.public)["exists"])
        active, _, _, _ = classified_evidence(VaultIndex.load(self.root))
        self.assertEqual(active[self.public]["sensitivity"], "restricted")

    def test_explicit_invalid_register_labels_never_fall_back_to_internal(self) -> None:
        original = self.register.read_text(encoding="utf-8")
        highest = VaultTools(self.root, max_sensitivity="restricted")
        for value in ("", "false", "0", "[]", "{}"):
            with self.subTest(value=value):
                self.register.write_text(original.replace("sensitivity: public", f"sensitivity: {value}"), encoding="utf-8")
                self.assertFalse(highest.context(self.public)["exists"])
        self.register.write_text(original.replace("sensitivity: public", "Sensitivity: public"), encoding="utf-8")
        self.assertFalse(highest.context(self.public)["exists"])

    def test_code_examples_are_not_register_rows_or_write_targets(self) -> None:
        original = self.register.read_text(encoding="utf-8")
        fake = "| ID | Source | Type | Date | Accessed | Location | Claims it supports |\n|---|---|---|---|---|---|---|\n| E-900 | Code example | fixture | 2026-09-17 | 2026-09-17 | https://example.com/example | Code only |\n"
        self.register.write_text(original.replace("# Evidence register", "# Evidence register\n\n```md\n" + fake + "```"), encoding="utf-8")
        created = self.create("Real row", "public")
        self.assertEqual(created, "E-004")
        self.assertNotIn("E-900", evidence_register(self.root)[0])
        self.assertIn(fake, self.register.read_text(encoding="utf-8"))
        retire_evidence(self.root, created, reason="Retire the real row", today=TODAY)
        self.assertIn(fake, self.register.read_text(encoding="utf-8"))

    def test_label_on_an_unpopulated_placeholder_is_preserved_on_creation(self) -> None:
        text = self.register.read_text(encoding="utf-8")
        lines = text.splitlines()
        lines = [line for line in lines if not line.startswith("| E-")]
        from whykit.tables import evidence_table_bounds

        header, _, _ = evidence_table_bounds(lines, "active")
        lines.insert(header + 2, "| E-001 | | | | | | | restricted |")
        text = "\n".join(lines) + "\n"
        self.register.write_text(text, encoding="utf-8")
        created = self.create("Placeholder source", None)
        self.assertEqual(created, "E-001")
        self.assertEqual(evidence_register(self.root)[0][created]["sensitivity"], "restricted")

    def test_labeled_incomplete_rows_cannot_escape_through_the_raw_register(self) -> None:
        lines = self.register.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if not line.startswith("| E-") or line.startswith(f"| {self.hidden} |")]
        text = "\n".join(lines).replace("https://example.com/source", "") + "\n"
        self.register.write_text(text, encoding="utf-8")
        self.assertEqual(evidence_register(self.root)[0], {})
        self.assertFalse(self.tools.context("00-context/evidence-register")["exists"])
        self.assertNotIn("Restricted sentinel", json.dumps(build_explorer_index(self.root, today=TODAY)))

    def test_invalid_explicit_label_is_withheld_and_warned(self) -> None:
        for label in ("", "publik", "PUBLIC"):
            with self.subTest(label=label):
                original = self.register.read_text(encoding="utf-8")
                self.register.write_text(original.replace("| public |", f"| {label} |"), encoding="utf-8")
                self.assertFalse(self.tools.context(self.public)["exists"])
                _, findings = lint(self.root, today=TODAY)
                found = [item for item in findings if item.code == "evidence.sensitivity"]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].level, "warning")
                self.register.write_text(original, encoding="utf-8")
        _, findings = lint(self.root, today=TODAY)
        self.assertFalse(any(item.code == "evidence.sensitivity" for item in findings))

    def test_all_target_surfaces_make_hidden_evidence_look_missing(self) -> None:
        for method in (self.tools.context, self.tools.impact, self.tools.backlinks):
            hidden = method(self.hidden)
            missing = method("E-999")
            hidden["target"] = missing["target"]
            if "id" in hidden:
                hidden["id"] = missing["id"]
            self.assertEqual(hidden, missing)
        hidden = self.tools.pack([self.hidden])
        missing = self.tools.pack(["E-999"])
        for report in (hidden, missing):
            report.pop("requested_targets")
            report.pop("selected")
            for item in report["missing"]:
                item.pop("target")
        self.assertEqual(hidden, missing)
        public = self.tools.pack([self.public])
        self.assertEqual(public["resolved"], 1)
        self.assertNotIn("sentinel", json.dumps(public))

    def test_counts_completions_resources_and_raw_register_do_not_leak_rows(self) -> None:
        self.assertEqual(self.tools.status(today=TODAY.isoformat())["evidence_active"], 1)
        self.assertFalse(self.tools.context("00-context/evidence-register")["exists"])
        values = self.tools.complete("ref/resource", RECORD_TEMPLATE, "target", "E-")["values"]
        self.assertEqual(values, [self.public])
        self.assertIsNone(self.tools.read_record(self.hidden))
        self.assertIsNotNone(self.tools.read_record(self.public))
        for method in (self.tools.query, self.tools.status, self.tools.trace, self.tools.resource_rows):
            self.assertNotIn("sentinel", json.dumps(method()))

    def test_public_export_keeps_public_rows_and_withholds_raw_table_and_dependents(self) -> None:
        public = build_explorer_index(self.root, today=TODAY)
        self.assertEqual([row["id"] for row in public["evidence"]], [self.public])
        docs = {doc["id"] for doc in public["docs"]}
        self.assertIn("notes/public", docs)
        self.assertNotIn("notes/hidden-citation", docs)
        self.assertNotIn("00-context/evidence-register", docs)
        self.assertNotIn("sentinel", json.dumps(public))
        private = build_explorer_index(self.root, today=TODAY, private=True)
        self.assertIn("Restricted sentinel", json.dumps(private))

    def test_hidden_row_edits_do_not_emit_events_and_snapshot_binds_labels(self) -> None:
        watcher = VaultWatcher(self.tools)
        self.assertEqual(watcher.poll(), [])
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("Restricted sentinel", "Changed private sentinel"), encoding="utf-8")
        self.assertEqual(watcher.poll(), [])
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("| E-002", "\n| E-002"), encoding="utf-8")
        self.assertEqual(watcher.poll(), [])
        original = classified_evidence
        def edit_after_capture(index):
            rows = original(index)
            self.register.write_text(self.register.read_text(encoding="utf-8").replace("| public |", "| restricted |"), encoding="utf-8")
            return rows
        with patch("whykit.mcp_server.classified_evidence", side_effect=edit_after_capture):
            self.assertTrue(self.tools.context(self.public)["exists"])
        self.assertFalse(self.tools.context(self.public)["exists"])

    def test_duplicate_id_and_truncated_labeled_row_fail_closed(self) -> None:
        original = self.register.read_text(encoding="utf-8")
        private = next(line for line in original.splitlines() if line.startswith(f"| {self.hidden} |"))
        self.register.write_text(original.replace("## Retired sources", private.replace(self.hidden, self.public) + "\n\n## Retired sources"), encoding="utf-8")
        self.assertFalse(self.tools.context(self.public)["exists"])

        before = self.register.read_bytes()
        with self.assertRaises(ValueError):
            retire_evidence(self.root, self.public, reason="Cannot retire an ambiguous ID", today=TODAY)
        self.assertEqual(self.register.read_bytes(), before)
        public = next(line for line in original.splitlines() if line.startswith(f"| {self.public} |"))
        self.register.write_text(original.replace(public, public.removesuffix(" public |")), encoding="utf-8")
        self.assertFalse(self.tools.context(self.public)["exists"])

    def test_retirement_inherits_replacement_ceiling(self) -> None:
        retire_evidence(self.root, self.public, reason="Replaced by a private source", replaced_by=self.hidden, today=TODAY)
        self.assertFalse(self.tools.context(self.public)["exists"])
        active, retired, _, _ = classified_evidence(VaultIndex.load(self.root))
        self.assertEqual(retired[self.public]["sensitivity"], active[self.hidden]["sensitivity"])
        self.assertNotIn(self.public, [row["id"] for row in build_explorer_index(self.root, today=TODAY)["evidence"]])

    def test_public_evidence_changes_emit_citing_record_events_and_network_guard_sees_rows(self) -> None:
        from whykit.cli import _non_public_docs

        self.assertIn(f"evidence:{self.hidden}", _non_public_docs(self.root))
        watcher = VaultWatcher(self.tools)
        watcher.poll()
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("Public fixture", "Updated public fixture"), encoding="utf-8")
        uris = {uri for _, uri in watcher.poll()}
        self.assertIn(f"whykit://record/{self.public}", uris)
        self.assertIn("whykit://record/notes/public", uris)
        self.assertNotIn(f"whykit://record/{self.hidden}", uris)


@unittest.skipIf(Client is None, "install the optional whykit[mcp] extra")
class McpEvidenceEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_row_privacy_through_legacy_modern_stdio_and_http(self) -> None:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "vault"
            fresh_vault(root, "--minimal")
            register = root / "00-context/evidence-register.md"
            register.write_text(register.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: public"), encoding="utf-8")
            for label in ("restricted", "public"):
                create_evidence(root, source=f"{label}-sentinel", location="https://example.com/source", kind="fixture",
                                claims="Synthetic claim", sensitivity=label, today=TODAY)

            async def verify(client):
                public = await client.call_tool("context", {"target": "E-002"})
                self.assertTrue(public.structured_content["exists"])
                self.assertEqual(public.structured_content["evidence"]["record"]["sensitivity"], "public")
                for name in ("context", "impact", "backlinks"):
                    result = await client.call_tool(name, {"target": "E-001"})
                    self.assertFalse(result.structured_content["exists"])
                    self.assertNotIn("restricted-sentinel", result.content[0].text)
                status = await client.call_tool("status", {"today": "2026-09-17"})
                self.assertEqual(status.structured_content["evidence_active"], 1)
                with self.assertRaises(MCPError):
                    await client.read_resource("whykit://record/E-001")
                with self.assertRaises(MCPError):
                    await client.read_resource("whykit://record/00-context/evidence-register")
                resource = await client.read_resource("whykit://record/E-002")
                self.assertNotIn("restricted-sentinel", resource.contents[0].text)

            args = ["-m", "whykit.mcp_server", "--root", str(root), "--max-sensitivity", "public"]
            for mode in ("auto", "legacy"):
                async with asyncio.timeout(30), Client(StdioServerParameters(command=sys.executable, args=args), mode=mode) as client:
                    await verify(client)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            server = await asyncio.create_subprocess_exec(sys.executable, *args, "--http", "--port", str(port),
                                                         stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
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
                        await verify(client)
            finally:
                server.terminate()
                _, stderr = await server.communicate()
                self.assertNotIn(b"restricted-sentinel", stderr)


if __name__ == "__main__":
    unittest.main()
