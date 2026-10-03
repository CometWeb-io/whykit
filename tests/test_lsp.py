"""`whykit lsp`: the read-only language server, driven over JSON-RPC.

Two drivers share one set of expectations. ``InProcess`` feeds framed messages
through the same reader the server uses and collects what it writes, so the
protocol details (framing, lifecycle, errors) are exercised without a process
boundary. ``Subprocess`` starts the real CLI and talks to it over pipes, which
is what an editor does.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
NORTHLINE = ROOT / "examples" / "northline"
TODAY = "2026-09-17"
sys.path.insert(0, str(ROOT / "src"))

from whykit import lsp  # noqa: E402


def _frame(message: dict) -> bytes:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


class InProcess:
    """A client wired straight to a LanguageServer instance."""

    def __init__(self, **options) -> None:
        self.sent: list[dict] = []
        self.server = lsp.LanguageServer(self.sent.append, **options)
        self._next = 0

    def notify(self, method: str, params: dict | None = None) -> None:
        self.server.handle({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next += 1
        ident = self._next
        self.server.handle({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
        replies = [m for m in self.sent if m.get("id") == ident and "method" not in m]
        assert len(replies) == 1, replies
        return replies[0]

    def initialize(self, root: Path | None = None, **capabilities) -> dict:
        params: dict = {"processId": None, "capabilities": capabilities}
        if root is not None:
            params["rootUri"] = lsp.path_to_uri(root)
        reply = self.request("initialize", params)
        self.notify("initialized")
        return reply

    def diagnostics(self, uri: str) -> list[dict] | None:
        found = [m["params"]["diagnostics"] for m in self.sent
                 if m.get("method") == "textDocument/publishDiagnostics" and m["params"]["uri"] == uri]
        return found[-1] if found else None


def _open(client: InProcess, path: Path, text: str | None = None, version: int = 1) -> str:
    uri = lsp.path_to_uri(path)
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": uri, "languageId": "markdown", "version": version,
        "text": path.read_text(encoding="utf-8") if text is None else text,
    }})
    return uri


def _position_of(text: str, needle: str, offset: int = 0) -> dict:
    index = text.index(needle) + offset
    line = text.count("\n", 0, index)
    column = index - (text.rfind("\n", 0, index) + 1)
    return {"line": line, "character": column}


class VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        shutil.copytree(NORTHLINE, self.vault)
        self.vault = self.vault.resolve()
        self.client = InProcess(debounce=0, today=dt.date.fromisoformat(TODAY))

    def start(self, **capabilities) -> dict:
        return self.client.initialize(self.vault, **capabilities)


class FramingTests(unittest.TestCase):
    def test_reads_a_frame_with_extra_headers_and_a_multibyte_body(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": "x", "params": {"t": "Zażółć 😀"}}, ensure_ascii=False).encode()
        stream = io.BytesIO(
            b"Content-Length: " + str(len(body)).encode()
            + b"\r\nContent-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n" + body
        )
        self.assertEqual(lsp.read_message(stream), {"jsonrpc": "2.0", "method": "x", "params": {"t": "Zażółć 😀"}})
        self.assertIsNone(lsp.read_message(stream))

    def test_a_frame_without_a_length_is_a_protocol_error(self) -> None:
        with self.assertRaises(lsp.ProtocolError):
            lsp.read_message(io.BytesIO(b"Content-Type: x\r\n\r\n{}"))

    def test_a_truncated_body_is_end_of_stream(self) -> None:
        self.assertIsNone(lsp.read_message(io.BytesIO(b"Content-Length: 50\r\n\r\n{}")))

    def test_write_counts_bytes_not_characters(self) -> None:
        out = io.BytesIO()
        lsp.write_message(out, {"t": "ż😀"})
        header, body = out.getvalue().split(b"\r\n\r\n", 1)
        self.assertEqual(header, b"Content-Length: " + str(len(body)).encode())
        self.assertEqual(json.loads(body), {"t": "ż😀"})

    def test_invalid_json_gets_a_parse_error_and_the_server_keeps_going(self) -> None:
        stdin = io.BytesIO(
            _frame({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"capabilities": {}}})
            + b"Content-Length: 5\r\n\r\n{nope"
            + _frame({"jsonrpc": "2.0", "id": 1, "method": "shutdown"})
            + _frame({"jsonrpc": "2.0", "method": "exit"})
        )
        stdout = io.BytesIO()
        code = lsp.serve(stdin, stdout, debounce=0)
        replies = []
        data = io.BytesIO(stdout.getvalue())
        while (message := lsp.read_message(data)) is not None:
            replies.append(message)
        self.assertEqual(replies[0]["id"], 0)
        self.assertEqual(replies[1]["error"]["code"], -32700)
        self.assertIsNone(replies[1]["id"])
        self.assertEqual(replies[2], {"jsonrpc": "2.0", "id": 1, "result": None})
        self.assertEqual(code, 0)

    def test_end_of_input_without_shutdown_exits_1(self) -> None:
        self.assertEqual(lsp.serve(io.BytesIO(b""), io.BytesIO(), debounce=0), 1)


class UriTests(unittest.TestCase):
    def test_posix_round_trip_with_spaces_and_non_ascii(self) -> None:
        path = "/tmp/vault dir/07-research/Zażółć notatka #1.md"
        uri = lsp.path_to_uri(path, windows=False)
        self.assertEqual(uri, "file:///tmp/vault%20dir/07-research/Za%C5%BC%C3%B3%C5%82%C4%87%20notatka%20%231.md")
        self.assertEqual(lsp.uri_to_path(uri, windows=False), path)

    def test_windows_drive_letters_in_both_spellings(self) -> None:
        expected = str(PureWindowsPath("c:/Users/A B/vault/ż.md"))
        self.assertEqual(lsp.uri_to_path("file:///c%3A/Users/A%20B/vault/%C5%BC.md", windows=True), expected)
        self.assertEqual(lsp.uri_to_path("file:///c:/Users/A%20B/vault/%C5%BC.md", windows=True), expected)
        self.assertEqual(
            lsp.path_to_uri(PureWindowsPath("C:/Users/A B/vault/ż.md"), windows=True),
            "file:///C:/Users/A%20B/vault/%C5%BC.md",
        )

    def test_windows_unc_share(self) -> None:
        self.assertEqual(lsp.uri_to_path("file://server/share/vault/a.md", windows=True), r"\\server\share\vault\a.md")
        self.assertEqual(lsp.path_to_uri(PureWindowsPath(r"\\server\share\vault\a.md"), windows=True),
                         "file://server/share/vault/a.md")

    def test_non_file_uris_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            lsp.uri_to_path("untitled:Untitled-1")


class PositionTests(unittest.TestCase):
    def test_character_offsets_per_encoding(self) -> None:
        line = "ż😀x"
        self.assertEqual(lsp.to_character(line, 3, "utf-16"), 4)
        self.assertEqual(lsp.to_character(line, 3, "utf-8"), 7)
        self.assertEqual(lsp.to_character(line, 3, "utf-32"), 3)
        self.assertEqual(lsp.from_character(line, 4, "utf-16"), 3)
        self.assertEqual(lsp.from_character(line, 7, "utf-8"), 3)
        self.assertEqual(lsp.from_character(line, 2, "utf-16"), 2)  # inside a surrogate pair: snaps past it
        self.assertEqual(lsp.from_character(line, 99, "utf-16"), 3)  # clamped to the line

    def test_incremental_edits_apply_in_utf16_units(self) -> None:
        text = "a😀b\r\nsecond\n"
        edited = lsp.apply_change(text, {"range": {"start": {"line": 0, "character": 3}, "end": {"line": 1, "character": 3}},
                                         "text": "X"}, "utf-16")
        self.assertEqual(edited, "a😀Xond\n")
        self.assertEqual(lsp.apply_change(text, {"text": "whole"}, "utf-16"), "whole")


class LifecycleTests(VaultCase):
    def test_requests_before_initialize_are_refused(self) -> None:
        reply = self.client.request("textDocument/hover", {})
        self.assertEqual(reply["error"]["code"], -32002)

    def test_initialize_advertises_the_read_only_feature_set(self) -> None:
        reply = self.start(general={"positionEncodings": ["utf-8", "utf-16"]})
        capabilities = reply["result"]["capabilities"]
        self.assertEqual(capabilities["positionEncoding"], "utf-8")
        self.assertEqual(capabilities["textDocumentSync"]["openClose"], True)
        self.assertIn(capabilities["textDocumentSync"]["change"], (1, 2))
        self.assertTrue(capabilities["hoverProvider"])
        self.assertTrue(capabilities["definitionProvider"])
        self.assertIn("[", capabilities["completionProvider"]["triggerCharacters"])
        self.assertIn("documentLinkProvider", capabilities)
        self.assertNotIn("documentFormattingProvider", capabilities)
        self.assertNotIn("renameProvider", capabilities)
        self.assertNotIn("codeActionProvider", capabilities)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "whykit")

    def test_unknown_requests_and_bad_params(self) -> None:
        self.start()
        self.assertEqual(self.client.request("textDocument/rename", {})["error"]["code"], -32601)
        self.assertEqual(self.client.request("textDocument/hover", {"textDocument": 3})["error"]["code"], -32602)
        self.client.notify("$/someExtension", {})  # ignored, no reply
        self.client.notify("$/cancelRequest", {"id": 99})

    def test_shutdown_then_exit(self) -> None:
        self.start()
        self.assertEqual(self.client.request("shutdown")["result"], None)
        self.assertEqual(self.client.request("textDocument/hover", {})["error"]["code"], -32600)
        self.client.notify("exit")
        self.assertFalse(self.client.server.running)
        self.assertEqual(self.client.server.exit_code, 0)


class DiagnosticTests(VaultCase):
    def test_a_clean_vault_publishes_empty_diagnostics_for_the_open_document(self) -> None:
        self.start()
        uri = _open(self.client, self.vault / "Home.md")
        self.assertEqual(self.client.diagnostics(uri), [])

    def test_unsaved_buffer_is_linted_and_mapped_to_a_range(self) -> None:
        self.start()
        path = self.vault / "01-strategy" / "positioning.md"
        if not path.exists():
            path = sorted((self.vault / "01-strategy").glob("*.md"))[0]
        before = _tree_digest(self.vault)
        text = path.read_text(encoding="utf-8") + "\nZażółć 😀 see [[no-such-note]] here.\n"
        uri = _open(self.client, path, text)
        diagnostics = self.client.diagnostics(uri)
        missing = [d for d in diagnostics if d["code"] == "wikilink.missing"]
        self.assertEqual(len(missing), 1, diagnostics)
        line = text.split("\n").index("Zażółć 😀 see [[no-such-note]] here.")
        # utf-16: "Zażółć " is 7 units, the emoji 2, then " see " -> 14
        self.assertEqual(missing[0]["range"], {"start": {"line": line, "character": 14},
                                               "end": {"line": line, "character": 14 + len("[[no-such-note]]")}})
        self.assertEqual(missing[0]["severity"], 1)
        published = [m for m in self.client.sent if m.get("method") == "textDocument/publishDiagnostics" and m["params"]["uri"] == uri]
        self.assertEqual(published[-1]["params"]["version"], 1)
        self.assertEqual(missing[0]["source"], "whykit")
        # Fix it in the buffer: the diagnostic goes away, still without a save.
        self.client.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": path.read_text(encoding="utf-8")}],
        })
        self.assertEqual(self.client.diagnostics(uri), [])
        self.assertEqual(_tree_digest(self.vault), before, "the server must never write to the vault")

    def test_findings_in_other_files_are_published_and_later_cleared(self) -> None:
        self.start()
        target = self.vault / "06-decisions" / "d-002-icp-ops.md"
        linking = [p for p in self.vault.rglob("*.md") if "[[06-decisions/d-002-icp-ops" in p.read_text(encoding="utf-8")]
        self.assertTrue(linking)
        # Renaming the note in the editor (unsaved) is not visible on disk;
        # deleting it on disk is, after a watched-files notification.
        moved = target.with_name("d-002-moved.md")
        target.rename(moved)
        self.client.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": lsp.path_to_uri(target), "type": 3}]})
        published = {m["params"]["uri"] for m in self.client.sent if m.get("method") == "textDocument/publishDiagnostics" and m["params"]["diagnostics"]}
        self.assertIn(lsp.path_to_uri(linking[0]), published)
        moved.rename(target)
        self.client.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": lsp.path_to_uri(target), "type": 1}]})
        self.assertEqual(self.client.diagnostics(lsp.path_to_uri(linking[0])), [])

    def test_whole_vault_is_linted_after_initialized(self) -> None:
        (self.vault / "07-research" / "broken.md").write_text(
            (self.vault / "Home.md").read_text(encoding="utf-8") + "\n[[nowhere-at-all]]\n", encoding="utf-8")
        self.start()
        diagnostics = self.client.diagnostics(lsp.path_to_uri(self.vault / "07-research" / "broken.md"))
        self.assertTrue(diagnostics and any(d["code"] == "wikilink.missing" for d in diagnostics))

    def test_non_ascii_file_name_round_trips_through_its_uri(self) -> None:
        self.start()
        name = unicodedata.normalize("NFC", "Zażółć notatka.md")
        path = self.vault / "07-research" / name
        path.write_text((self.vault / "Home.md").read_text(encoding="utf-8"), encoding="utf-8")
        uri = _open(self.client, path, path.read_text(encoding="utf-8") + "\n[[missing-target]]\n")
        self.assertIn("%C5%BC", uri)
        codes = {d["code"] for d in self.client.diagnostics(uri)}
        self.assertIn("wikilink.missing", codes)

    def test_typing_keeps_cross_file_findings_from_the_last_vault_lint(self) -> None:
        orphan = self.vault / "07-research" / "orphan.md"
        orphan.write_text((self.vault / "Home.md").read_text(encoding="utf-8"), encoding="utf-8")
        self.start()
        uri = _open(self.client, orphan)
        self.assertIn("note.orphan", {d["code"] for d in self.client.diagnostics(uri)})
        text = orphan.read_text(encoding="utf-8") + "\n[[still-missing]]\n"
        self.client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                                      "contentChanges": [{"text": text}]})
        codes = {d["code"] for d in self.client.diagnostics(uri)}
        self.assertEqual(codes, {"note.orphan", "wikilink.missing"})

    def test_cross_file_codes_complete_a_lint_of_one_file(self) -> None:
        """Lint of one file + carried-over cross-file findings == whole-vault lint, per file.

        If a check that compares files is added to `lint()`, this fails until
        its codes join CROSS_FILE_CODES.
        """
        from whykit.lint import lint
        from whykit.rules import RULE_BY_CODE

        self.assertLessEqual(lsp.CROSS_FILE_CODES, set(RULE_BY_CODE))
        home = (self.vault / "Home.md").read_text(encoding="utf-8")
        (self.vault / "07-research" / "orphan.md").write_text(
            home.replace("[[", "[[x-") + "\nAKIAABCDEFGHIJKLMNOP\nE-999\n", encoding="utf-8")
        decision = (self.vault / "06-decisions" / "d-002-icp-ops.md").read_text(encoding="utf-8")
        (self.vault / "06-decisions" / "d-011-dup.md").write_text(decision, encoding="utf-8")
        today = dt.date.fromisoformat(TODAY)
        _, full = lint(self.vault, today=today)
        self.assertGreaterEqual(len({f.code for f in full}), 5)
        for name in sorted({f.path for f in full if f.path.endswith(".md")}):
            with self.subTest(path=name):
                _, scoped = lint(self.vault, [name], today=today)
                expected = {(f.line, f.code, f.message) for f in full if f.path == name}
                merged = {(f.line, f.code, f.message) for f in scoped if f.path == name}
                merged |= {(f.line, f.code, f.message) for f in full if f.path == name and f.code in lsp.CROSS_FILE_CODES}
                self.assertEqual(merged, expected)

    def test_rapid_changes_are_debounced_into_one_lint(self) -> None:
        client = InProcess(debounce=0.15, today=dt.date.fromisoformat(TODAY))
        client.initialize(self.vault)
        deadline = time.monotonic() + 10
        while client.server.lint_runs < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        runs = client.server.lint_runs
        path = self.vault / "Home.md"
        base = path.read_text(encoding="utf-8")
        uri = _open(client, path, base)
        for version in range(2, 12):
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": base + "x" * version}],
            })
        deadline = time.monotonic() + 10
        while client.server.lint_runs == runs and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.4)
        self.assertEqual(client.server.lint_runs, runs + 1)
        client.request("shutdown")
        client.notify("exit")


class NavigationTests(VaultCase):
    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.path = self.vault / "07-research" / "scratch.md"
        self.text = (
            "---\ntitle: Scratch\n---\n\n"
            "See [[06-decisions/d-002-icp-ops]] and [[d-00\n"
            "Cites E-003 and D-002. `E-001 in code`\n"
            "Also [[D-00\n"
            "Ev E-00\n"
        )
        self.uri = _open(self.client, self.path, self.text)

    def _at(self, needle: str, offset: int = 0) -> dict:
        return {"textDocument": {"uri": self.uri}, "position": _position_of(self.text, needle, offset)}

    def test_wikilink_completion_offers_paths_and_closes_the_link(self) -> None:
        reply = self.client.request("textDocument/completion", self._at("[[d-00", 6))["result"]
        items = {item["label"]: item for item in reply["items"]}
        self.assertIn("06-decisions/d-002-icp-ops", items)
        edit = items["06-decisions/d-002-icp-ops"]["textEdit"]
        start = _position_of(self.text, "[[d-00", 2)
        self.assertEqual(edit["range"]["start"], start)
        self.assertEqual(edit["range"]["end"]["character"], start["character"] + 4)
        self.assertTrue(edit["newText"].endswith("]]"))
        self.assertIn(edit["newText"][:-2], ("d-002-icp-ops", "06-decisions/d-002-icp-ops"))

    def test_wikilink_completion_offers_decision_ids_that_resolve(self) -> None:
        items = self.client.request("textDocument/completion", self._at("[[D-00", 6))["result"]["items"]
        by_label = {item["label"]: item for item in items}
        self.assertIn("D-002", by_label)
        self.assertIn("ICP is ops", by_label["D-002"]["detail"])

    def test_id_completion_outside_links(self) -> None:
        items = self.client.request("textDocument/completion", self._at("Ev E-00", 7))["result"]["items"]
        by_label = {item["label"]: item for item in items}
        self.assertIn("E-003", by_label)
        self.assertIn("Plausible", by_label["E-003"]["detail"])
        self.assertEqual(by_label["E-003"]["textEdit"]["newText"], "E-003")

    def test_hover_on_evidence_and_decision_ids(self) -> None:
        evidence = self.client.request("textDocument/hover", self._at("E-003", 2))["result"]["contents"]["value"]
        self.assertIn("E-003", evidence)
        self.assertIn("Plausible monthly unique visitors", evidence)
        self.assertIn("active", evidence)
        decision = self.client.request("textDocument/hover", self._at("D-002.", 1))["result"]["contents"]["value"]
        for expected in ("ICP is ops, not IT", "approved", "Maya Chen", "2027-01-06"):
            self.assertIn(expected, decision)

    def test_hover_on_a_wikilink_and_nothing_inside_code(self) -> None:
        value = self.client.request("textDocument/hover", self._at("[[06-decisions", 5))["result"]["contents"]["value"]
        self.assertIn("Maya Chen", value)
        self.assertIsNone(self.client.request("textDocument/hover", self._at("E-001 in code", 1))["result"])

    def test_definition_of_wikilink_and_ids(self) -> None:
        link = self.client.request("textDocument/definition", self._at("[[06-decisions", 5))["result"]
        self.assertEqual(link["uri"], lsp.path_to_uri(self.vault / "06-decisions" / "d-002-icp-ops.md"))
        decision = self.client.request("textDocument/definition", self._at("D-002.", 1))["result"]
        self.assertEqual(decision["uri"], link["uri"])
        evidence = self.client.request("textDocument/definition", self._at("E-003", 1))["result"]
        register = self.vault / "00-context" / "evidence-register.md"
        self.assertEqual(evidence["uri"], lsp.path_to_uri(register))
        row = next(i for i, line in enumerate(register.read_text(encoding="utf-8").split("\n")) if line.startswith("| E-003 "))
        self.assertEqual(evidence["range"]["start"]["line"], row)

    def test_definition_of_an_unknown_id_is_null(self) -> None:
        text = self.text + "E-999\n"
        self.client.notify("textDocument/didChange", {"textDocument": {"uri": self.uri, "version": 2},
                                                      "contentChanges": [{"text": text}]})
        position = {"line": text.count("\n") - 1, "character": 1}
        reply = self.client.request("textDocument/definition", {"textDocument": {"uri": self.uri}, "position": position})
        self.assertIsNone(reply["result"])

    def test_document_links(self) -> None:
        links = self.client.request("textDocument/documentLink", {"textDocument": {"uri": self.uri}})["result"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["target"], lsp.path_to_uri(self.vault / "06-decisions" / "d-002-icp-ops.md"))
        start = _position_of(self.text, "06-decisions/d-002")
        self.assertEqual(links[0]["range"]["start"], start)

    def test_positions_past_the_end_do_not_fail(self) -> None:
        params = {"textDocument": {"uri": self.uri}, "position": {"line": 500, "character": 900}}
        for method in ("textDocument/hover", "textDocument/definition", "textDocument/completion"):
            with self.subTest(method=method):
                self.assertNotIn("error", self.client.request(method, params))

    def test_unopened_and_outside_documents(self) -> None:
        outside = Path(self._tmp.name) / "elsewhere.md"
        outside.write_text("E-003\n", encoding="utf-8")
        params = {"textDocument": {"uri": lsp.path_to_uri(outside)}, "position": {"line": 0, "character": 1}}
        self.assertIsNone(self.client.request("textDocument/hover", params)["result"])
        untitled = {"textDocument": {"uri": "untitled:Untitled-1"}, "position": {"line": 0, "character": 0}}
        self.assertIsNone(self.client.request("textDocument/hover", untitled)["result"])


class EditorDocsTests(unittest.TestCase):
    TEXT = (ROOT / "docs" / "editors.md").read_text(encoding="utf-8")

    def test_options_table_lists_every_server_option(self) -> None:
        from whykit.cli import build_parser

        parser = next(a for a in build_parser()._actions if a.dest == "command").choices["lsp"]
        options = {o for a in parser._actions for o in a.option_strings if o.startswith("--") and o != "--help"}
        documented = set(re.findall(r"^\| `(--[a-z-]+)", self.TEXT, re.M))
        self.assertEqual(documented, options)

    def test_configuration_snippets_parse(self) -> None:
        import tomllib

        blocks = re.findall(r"```(json|toml)\n(.*?)```", self.TEXT, re.S)
        self.assertEqual({kind for kind, _ in blocks}, {"json", "toml"})
        for kind, body in blocks:
            with self.subTest(kind=kind):
                parsed = json.loads(body) if kind == "json" else tomllib.loads(body)
                self.assertIn("lsp", json.dumps(parsed))

    def test_snippets_are_marked_as_examples(self) -> None:
        self.assertIn("examples, not maintained integrations", self.TEXT)


class Subprocess:
    """The real CLI over pipes, as an editor runs it."""

    def __init__(self, *args: str) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(CLI), "lsp", *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.inbox: queue.Queue[dict] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._next = 0

    def _read(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                self.inbox.put({"_eof": True})
                return
            length = int(line.split(b":")[1])
            while self.proc.stdout.readline() not in (b"\r\n", b""):
                pass
            self.inbox.put(json.loads(self.proc.stdout.read(length)))

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(_frame(message))
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            message = self.inbox.get(timeout=max(0.01, deadline - time.monotonic()))
            if message.get("_eof"):
                raise AssertionError(f"server exited: {self.proc.stderr.read().decode() if self.proc.stderr else ''}")
            if predicate(message):
                return message

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next += 1
        ident = self._next
        self.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
        return self.wait_for(lambda m: m.get("id") == ident and "method" not in m)


class SubprocessTests(VaultCase):
    def test_editor_session_over_stdio(self) -> None:
        before = _tree_digest(self.vault)
        client = Subprocess("--stdio", "--debounce", "50", "--today", TODAY)
        try:
            reply = client.request("initialize", {"processId": os.getpid(), "rootUri": lsp.path_to_uri(self.vault),
                                                  "capabilities": {}})
            self.assertEqual(reply["result"]["capabilities"]["positionEncoding"], "utf-16")
            client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            path = self.vault / "Home.md"
            text = path.read_text(encoding="utf-8") + "\nŻ [[gone-note]] E-003\n"
            uri = lsp.path_to_uri(path)
            client.send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {
                "uri": uri, "languageId": "markdown", "version": 1, "text": text}}})
            published = client.wait_for(lambda m: m.get("method") == "textDocument/publishDiagnostics"
                                        and m["params"]["uri"] == uri
                                        and any(d["code"] == "wikilink.missing" for d in m["params"]["diagnostics"]))
            self.assertEqual(published["params"]["diagnostics"][0]["range"]["start"]["line"], text.count("\n") - 1)
            hover = client.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                          "position": {"line": text.count("\n") - 1, "character": 18}})
            self.assertIn("Plausible", hover["result"]["contents"]["value"])
            self.assertEqual(client.request("shutdown")["result"], None)
            client.send({"jsonrpc": "2.0", "method": "exit"})
            self.assertEqual(client.proc.wait(timeout=30), 0)
        finally:
            if client.proc.poll() is None:
                client.proc.kill()
                client.proc.wait()
            for stream in (client.proc.stdin, client.proc.stdout, client.proc.stderr):
                if stream is not None:
                    stream.close()
        self.assertEqual(_tree_digest(self.vault), before)

    def test_root_that_is_not_a_vault_exits_2(self) -> None:
        result = subprocess.run([sys.executable, str(CLI), "lsp", "--root", self._tmp.name],
                                input=b"", capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
