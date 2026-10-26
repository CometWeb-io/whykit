"""Row classification is preserved by writers and every filtered reader."""
from __future__ import annotations

import datetime as dt
import asyncio
import contextlib
import io
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
from whykit.lint import Note, evidence_register, lint, load_note
from whykit.mcp_server import RECORD_TEMPLATE, VaultTools, VaultWatcher
from whykit.scaffold import create_evidence, create_note
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
                "---\ntitle: " + name + "\ntype: reference\nstatus: draft\nowner: Ada Example\n"
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

    def test_extending_legacy_rows_without_trailing_pipes_preserves_cells(self) -> None:
        original = self.register.read_text(encoding="utf-8")
        legacy = "\n".join(line.rsplit("|", 2)[0].rstrip() if line.startswith("|") and line.count("|") == 9 else line
                           for line in original.splitlines()) + "\n"
        self.register.write_text(legacy, encoding="utf-8")
        self.create("Added classified row", "restricted")
        row = evidence_register(self.root)[0][self.public]
        self.assertEqual(row["claims"], "Synthetic claim")
        self.assertEqual(row["sensitivity"], "public")

    def test_unknown_evidence_references_are_not_publication_inputs(self) -> None:
        public = self.root / "notes/public.md"
        public.write_text(public.read_text(encoding="utf-8") + "\n`E-999`\n", encoding="utf-8")
        payload = build_explorer_index(self.root, today=TODAY)
        self.assertNotIn("notes/public", {doc["id"] for doc in payload["docs"]})

    def test_register_floor_cannot_be_lowered_by_public_row(self) -> None:
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("sensitivity: public", "sensitivity: restricted"), encoding="utf-8")
        self.assertFalse(self.tools.context(self.public)["exists"])
        active, _, _, _ = classified_evidence(VaultIndex.load(self.root))
        self.assertEqual(active[self.public]["sensitivity"], "restricted")

    def test_public_row_with_private_references_withholds_its_citing_notes(self) -> None:
        hidden = self.root / "notes/secret.md"
        hidden.write_text("---\ntitle: Secret title canary\nsensitivity: restricted\n---\nSECRET-BODY\n", encoding="utf-8")
        original = self.register.read_text(encoding="utf-8")
        for reference in ("[[notes/secret]]", "Secret title canary", self.hidden):
            with self.subTest(reference=reference):
                self.register.write_text(original.replace("Public fixture", reference), encoding="utf-8")
                payload = build_explorer_index(self.root, today=TODAY)
                self.assertNotIn(self.public, [row["id"] for row in payload["evidence"]])
                self.assertNotIn("notes/public", {doc["id"] for doc in payload["docs"]})

    def test_malformed_headers_and_separators_do_not_remove_classification(self) -> None:
        from whykit.tables import evidence_table_bounds

        original = self.register.read_text(encoding="utf-8")
        lines = original.splitlines()
        header, _, _ = evidence_table_bounds(lines, "active")
        for offset, replacement in ((0, lines[header].replace("Sensitivity", "Sensitivity <!-- hidden -->")),
                                    (1, lines[header + 1].replace("---", "<!-- --- -->", 1)),
                                    (1, "<!--\n" + lines[header + 1] + "\n-->")):
            with self.subTest(offset=offset, replacement=replacement):
                damaged = lines.copy()
                damaged[header + offset] = replacement
                self.register.write_text("\n".join(damaged) + "\n", encoding="utf-8")
                self.assertFalse(self.tools.context(self.public)["exists"])
                before = self.register.read_bytes()
                with self.assertRaises(ValueError):
                    self.create("Must not write", "public")
                self.assertEqual(self.register.read_bytes(), before)
        self.register.write_text(original, encoding="utf-8")

    def test_comment_tables_are_not_write_targets(self) -> None:
        original = self.register.read_text(encoding="utf-8")
        fake = ("| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |\n"
                "|---|---|---|---|---|---|---|---|\n"
                "| E-900 | Hidden example | fixture | 2026-09-17 | 2026-09-17 | example | example | restricted |\n")
        self.register.write_text(original.replace("# Evidence register", "# Evidence register\n\n<!--\n" + fake + "-->"), encoding="utf-8")
        self.assertNotIn("E-900", evidence_register(self.root)[0])
        self.assertEqual(self.create("Real row", "public"), "E-004")
        self.assertIn(fake, self.register.read_text(encoding="utf-8"))

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
        from whykit.cli import _non_public_docs

        # The private local viewer still serves the raw table, including incomplete rows.
        for note in self.root.rglob("*.md"):
            note.write_text(note.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: public"), encoding="utf-8")
        self.assertIn("00-context/evidence-register.md", _non_public_docs(self.root))

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


class EvidenceRegisterAliasTests(unittest.TestCase):
    aliases = ("evidence-register.md", "evidence-register.MD", "EVIDENCE-REGISTER.md",
               "Evidence-Register.Md", "00-CONTEXT/EVIDENCE-REGISTER.MD")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "vault"
        self.day = fresh_vault(self.root, "--minimal")
        self.register = self.root / "00-context/evidence-register.md"
        home = self.root / "Home.md"
        note = load_note(home)
        home.write_text(note.text[:len(note.text) - len(note.body)] + "\n# Public home\n", encoding="utf-8")
        for path in self.root.rglob("*.md"):
            if path not in (home, self.register):
                path.unlink()
        for path in (home, self.register):
            path.write_text(path.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: public"), encoding="utf-8")
        for label, source in (("restricted", "PRIVATE-ALIAS-CANARY"), ("public", "PUBLIC-ALIAS-CANARY")):
            create_evidence(self.root, source=source, location="https://example.com/source", kind="fixture",
                            claims="Synthetic fixture", sensitivity=label, today=self.day)
        public = create_note(self.root, "Public note", workstream="notes", owner="Ada Example",
                             doc_type="reference", sensitivity="public", today=self.day)
        public.write_text(public.read_text(encoding="utf-8") + "\nCites E-002.\n", encoding="utf-8")

    @contextlib.contextmanager
    def alias(self, name):
        path = self.root / (name if "/" in name else "00-context/" + name)
        path.parent.mkdir(exist_ok=True)
        self.register.rename(path)
        try:
            yield path.relative_to(self.root).with_suffix("").as_posix()
        finally:
            path.rename(self.register)

    def test_public_export_filters_rows_and_raw_register_for_every_alias(self):
        from whykit.cli import main

        for name in self.aliases:
            with self.subTest(name=name), self.alias(name):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = main(["explorer-index", "--root", str(self.root)])
                self.assertEqual(code, 0, out.getvalue())
                payload = json.loads(out.getvalue())
                self.assertEqual([row["id"] for row in payload["evidence"]], ["E-002"])
                self.assertIn("notes/public-note", {doc["id"] for doc in payload["docs"]})
                self.assertNotIn("PRIVATE-ALIAS-CANARY", out.getvalue())
                self.assertIn("PUBLIC-ALIAS-CANARY", out.getvalue())
                self.assertIn("PRIVATE-ALIAS-CANARY", json.dumps(build_explorer_index(self.root, private=True)))

    def test_public_mcp_keeps_public_rows_and_withholds_raw_aliases(self):
        tools = VaultTools(self.root, max_sensitivity="public")
        for name in self.aliases:
            with self.subTest(name=name), self.alias(name) as target:
                self.assertIsNone(tools.read_record(target))
                self.assertFalse(tools.context("E-001")["exists"])
                self.assertEqual(tools.context("E-002")["evidence"]["record"]["source"], "PUBLIC-ALIAS-CANARY")
                self.assertEqual(tools.status()["evidence_active"], 1)
                self.assertNotIn("PRIVATE-ALIAS-CANARY", json.dumps(tools.resource_rows()))

    def test_serve_refuses_aliases_without_network_opt_in(self):
        from whykit.cli import main, _non_public_docs

        for name in self.aliases:
            with self.subTest(name=name), self.alias(name):
                hidden = _non_public_docs(self.root)
                self.assertIn("evidence:E-001", hidden)
                self.assertTrue(any(item.casefold() == "00-context/evidence-register.md" for item in hidden))
                with patch("whykit.cli.shutil.which", return_value="npm"), patch("whykit.cli.subprocess.run") as run:
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(["serve", str(self.root), "--host", "0.0.0.0"]), 2)
                    run.assert_not_called()

    def test_all_public_legacy_aliases_still_export_and_can_serve(self):
        from whykit.cli import main, _non_public_docs
        from whykit.tables import split_table_row

        lines = [line for line in self.register.read_text(encoding="utf-8").splitlines() if "PRIVATE-ALIAS-CANARY" not in line]
        self.register.write_text("\n".join("| " + " | ".join(split_table_row(line)[:7]) + " |"
                                           if line.startswith("|") and len(split_table_row(line)) == 8 else line
                                           for line in lines) + "\n", encoding="utf-8")
        checkout = Path(self.tmp.name) / "checkout"
        explorer = checkout / "apps" / "explorer"
        explorer.mkdir(parents=True)
        (explorer / "package.json").write_text("{}", encoding="utf-8")
        (explorer / "node_modules").mkdir()
        for name in self.aliases:
            with self.subTest(name=name), self.alias(name) as target:
                payload = build_explorer_index(self.root)
                self.assertEqual([row["id"] for row in payload["evidence"]], ["E-002"])
                self.assertTrue(VaultTools(self.root, max_sensitivity="public").context(target)["exists"])
                self.assertEqual(_non_public_docs(self.root), [])
                with patch("whykit.cli.SOURCE_ROOT", checkout), patch("whykit.cli.shutil.which", return_value="npm"), patch("whykit.cli.subprocess.run") as run:
                    run.return_value.returncode = 0
                    self.assertEqual(main(["serve", str(self.root), "--host", "0.0.0.0"]), 0)
                    run.assert_called_once()

    def test_ambiguous_captured_registers_are_withheld(self):
        # A captured pair also exercises case-sensitive filesystems on a case-folding host.
        index = VaultIndex.load(self.root)
        alias = self.register.with_name("EVIDENCE-REGISTER.MD")
        other = Note(alias, self.register.read_text(encoding="utf-8"))
        index.notes.append(other)
        index.by_path[alias] = other
        with patch.object(VaultIndex, "load", return_value=index):
            self.assertEqual(classified_evidence(index), ({}, {}, [], True))
            payload = build_explorer_index(self.root)
            self.assertEqual(payload["evidence"], [])
            self.assertNotIn("PRIVATE-ALIAS-CANARY", json.dumps(payload))
            tools = VaultTools(self.root, max_sensitivity="restricted")
            self.assertFalse(tools.context("E-002")["exists"])
            self.assertFalse(tools.context("00-context/evidence-register")["exists"])
            self.assertFalse(tools.context("00-context/EVIDENCE-REGISTER")["exists"])
            self.assertIsNone(tools.status()["evidence_active"])
            from whykit.cli import _non_public_docs
            self.assertTrue(_non_public_docs(self.root))


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
            register.rename(register.with_name("EVIDENCE-REGISTER.MD"))

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
