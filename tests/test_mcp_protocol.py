"""MCP protocol surfaces beyond the tool handlers — no SDK needed.

Output schemas, page cursors, completions, change events and the HTTP guard
are all computed by SDK-free code, so their confinement rules run in every CI
job: a record above the ceiling must stay indistinguishable from one that does
not exist on each of these surfaces too.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import unsupported_keywords, validate  # noqa: E402
from test_mcp_tools import VaultToolsTestCase, write_decision, write_doc  # noqa: E402
from whykit import mcp_server  # noqa: E402
from whykit.mcp_server import (  # noqa: E402
    TOOL_BASE_SCHEMAS,
    TOOL_NAMES,
    CursorCodec,
    ToolFailure,
    VaultTools,
    VaultWatcher,
    is_loopback_host,
    output_schema,
    read_token,
    require_bearer,
)

NORTHLINE = ROOT / "examples" / "northline"
TODAY = "2026-09-24"


def valid_calls(today: str) -> list[tuple[str, dict]]:
    return [
        ("query", {"text": "", "limit": 100}),
        ("query", {"text": "decision", "limit": 2}),
        ("context", {"target": "D-002"}),
        ("context", {"target": "E-001"}),
        ("context", {"target": "notes/never-written"}),
        ("impact", {"target": "E-001"}),
        ("impact", {"target": "D-002"}),
        ("impact", {"target": "notes/internal-note"}),
        ("impact", {"target": "notes/never-written"}),
        ("status", {"today": today}),
        ("pack", {"targets": ["D-002", "never-written"], "query": "needle"}),
        ("trace", {"today": today}),
        ("trace", {"today": today, "limit": 1}),
        ("trace", {"decision": "D-999", "today": today}),
        ("backlinks", {"target": "notes/internal-note", "limit": 1}),
        ("backlinks", {"target": "E-001"}),
        ("backlinks", {"target": "never-written"}),
    ]


class OutputSchemaTests(VaultToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_decision(self.vault, "06-decisions/d-002-cites.md", decision_id="D-002", sensitivity="internal",
                       body="Based on E-001 and [[notes/internal-note]].")
        write_decision(self.vault, "06-decisions/d-003-public.md", decision_id="D-003", sensitivity="public",
                       body="Cites E-001 publicly.")

    def test_package_copies_of_the_contract_schemas_match_schemas_dir(self) -> None:
        packaged = ROOT / "src" / "whykit" / "contract_schemas"
        self.assertEqual(sorted(path.name for path in packaged.glob("*.json")), sorted(set(TOOL_BASE_SCHEMAS.values())))
        for name in TOOL_BASE_SCHEMAS.values():
            with self.subTest(schema=name):
                self.assertEqual((packaged / name).read_bytes(), (ROOT / "schemas" / name).read_bytes())

    def test_every_tool_has_an_object_output_schema_the_test_validator_understands(self) -> None:
        self.assertEqual(set(TOOL_BASE_SCHEMAS), set(TOOL_NAMES))
        for tool in TOOL_NAMES:
            with self.subTest(tool=tool):
                schema = output_schema(tool)
                self.assertEqual(schema["type"], "object")
                self.assertEqual(unsupported_keywords(schema), [])
                self.assertNotIn("$id", schema)
                self.assertIn(tool, schema["description"])
                # Copies, so a caller cannot corrupt the next listing.
                schema["properties"].clear()
                self.assertTrue(output_schema(tool)["properties"])

    def test_successful_results_follow_the_output_schema_under_every_ceiling(self) -> None:
        for policy in ("public", "internal", "restricted"):
            tools = VaultTools(self.vault, max_sensitivity=policy)
            for tool, arguments in valid_calls(TODAY):
                with self.subTest(policy=policy, tool=tool, arguments=arguments):
                    payload, is_error = tools.call(tool, arguments)
                    self.assertFalse(is_error, payload)
                    self.assertEqual(validate(payload, output_schema(tool)), [])

    def test_results_under_a_team_rule_policy_follow_the_output_schema(self) -> None:
        # Custom-rule codes and override-adjusted levels flow into `status`
        # and `context` findings; the structured results must still validate.
        config = self.vault / "whykit.toml"
        config.write_text(
            config.read_text(encoding="utf-8")
            + '\n[[rules.custom]]\nid = "custom.method"\nsummary = "Notes state their method."\n'
            'required_sections = ["## Method"]\n\n[rules.custom.applies_to]\npaths = ["notes/**"]\n\n'
            '[rules.overrides."wikilink.missing"]\nlevel = "warning"\n\n'
            '[rules.overrides."secret.detected"]\nlevel = "off"\npaths = ["fixtures/**"]\n'
            'reason = "Synthetic credentials used by fixtures."\n',
            encoding="utf-8",
        )
        for policy in ("public", "internal", "restricted"):
            tools = VaultTools(self.vault, max_sensitivity=policy)
            for tool, arguments in (("status", {"today": TODAY}), ("context", {"target": "notes/public-note"})):
                with self.subTest(policy=policy, tool=tool):
                    payload, is_error = tools.call(tool, arguments)
                    self.assertFalse(is_error, payload)
                    self.assertEqual(validate(payload, output_schema(tool)), [])
        payload, _ = VaultTools(self.vault, max_sensitivity="internal").call("status", {"today": TODAY})
        self.assertIn("custom.method", payload["finding_codes"])

        # An unreasoned security override is a configuration error, not a crash.
        config.write_text(config.read_text(encoding="utf-8")
                          + '\n[rules.overrides."secret.scan_unreadable"]\nlevel = "off"\n', encoding="utf-8")
        payload, is_error = VaultTools(self.vault, max_sensitivity="internal").call("status", {"today": TODAY})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "invalid_config")

    def test_example_vault_results_follow_the_output_schema(self) -> None:
        tools = VaultTools(NORTHLINE, max_sensitivity="restricted")
        for tool, arguments in (
            ("query", {"text": "", "limit": 100}),
            ("context", {"target": "D-010"}),
            ("impact", {"target": "E-000"}),
            ("status", {"today": "2026-09-17"}),
            ("pack", {"query": "pipeline", "max_docs": 20}),
            ("trace", {"today": "2026-09-17", "limit": 100}),
            ("backlinks", {"target": "D-009"}),
        ):
            with self.subTest(tool=tool):
                payload, is_error = tools.call(tool, arguments)
                self.assertFalse(is_error, payload)
                self.assertEqual(validate(payload, output_schema(tool)), [])

    def test_error_bodies_are_not_described_by_the_output_schema(self) -> None:
        # Clients validate only successful results; the error body is separate.
        payload, is_error = self.tools.call("context", {"target": "../x"})
        self.assertTrue(is_error)
        self.assertTrue(validate(payload, output_schema("context")))


class PaginationTests(VaultToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        for index in range(5):
            write_doc(self.vault, f"notes/page-{index}.md", title=f"Page {index}", sensitivity="internal",
                      body="needle-page [[notes/internal-note]]")
            # Hidden records interleaved with the visible ones in every ordering.
            write_doc(self.vault, f"notes/page-{index}-hidden.md", title=f"Page {index} hidden",
                      sensitivity="restricted", body="needle-page hidden-sentinel [[notes/internal-note]]")
            write_decision(self.vault, f"06-decisions/d-1{index}0-visible.md", decision_id=f"D-1{index}0",
                           sensitivity="internal", body="visible")
            write_decision(self.vault, f"06-decisions/d-1{index}1-hidden.md", decision_id=f"D-1{index}1",
                           sensitivity="restricted", body="hidden-sentinel")

    def walk(self, tools: VaultTools, tool: str, arguments: dict, items: str) -> tuple[list, list]:
        collected, cursors = [], []
        cursor = None
        for _ in range(100):
            payload, is_error = tools.call(tool, {**arguments, **({"cursor": cursor} if cursor else {})})
            self.assertFalse(is_error, payload)
            collected += payload[items]
            cursor = payload["next_cursor"]
            if cursor is None:
                return collected, cursors
            cursors.append(cursor)
        self.fail("pagination did not terminate")

    def test_pages_reassemble_the_full_visible_result(self) -> None:
        for tool, arguments, items, full in (
            ("query", {"text": "needle-page"}, "results", {"text": "needle-page", "limit": 100}),
            ("trace", {"today": TODAY}, "decisions", {"today": TODAY, "limit": 100}),
            ("backlinks", {"target": "notes/internal-note"}, "backlinks", {"target": "notes/internal-note", "limit": 500}),
        ):
            for limit in (1, 2, 3):
                with self.subTest(tool=tool, limit=limit):
                    pages, cursors = self.walk(self.tools, tool, {**arguments, "limit": limit}, items)
                    whole, _ = self.tools.call(tool, full)
                    self.assertEqual(pages, whole[items])
                    self.assertEqual(len(cursors), -(-len(pages) // limit) - 1)
                    self.assertNotIn("hidden-sentinel", json.dumps(pages))
                    self.assertNotIn("hidden", json.dumps(pages))

    def test_page_fields_are_consistent(self) -> None:
        first, _ = self.tools.call("trace", {"today": TODAY, "limit": 2})
        self.assertEqual(first["matched"], 5)
        self.assertTrue(first["truncated"])
        last, _ = self.tools.call("trace", {"today": TODAY, "limit": 4, "cursor": None})
        rest, _ = self.tools.call("trace", {"today": TODAY, "limit": 4, "cursor": last["next_cursor"]})
        self.assertEqual(len(rest["decisions"]), 1)
        self.assertFalse(rest["truncated"])
        self.assertIsNone(rest["next_cursor"])
        query, _ = self.tools.call("query", {"text": "needle-page", "limit": 2})
        self.assertEqual((query["total"], query["returned"], query["query"]["limit"]), (5, 2, 2))
        # limit=0 reports what exists but never pages.
        zero, _ = self.tools.call("backlinks", {"target": "notes/internal-note", "limit": 0})
        self.assertEqual((zero["backlinks"], zero["count"], zero["truncated"], zero["next_cursor"]), ([], 5, True, None))

    def test_cursors_do_not_depend_on_hidden_records(self) -> None:
        calls = (
            ("query", {"text": "needle-page", "limit": 2}),
            ("trace", {"today": TODAY, "limit": 2}),
            ("backlinks", {"target": "notes/internal-note", "limit": 2}),
        )
        with_hidden = {tool: self.tools.call(tool, arguments)[0] for tool, arguments in calls}
        resources = self.tools.resource_rows()
        hidden = [path for path in self.vault.rglob("*.md") if "hidden" in path.name or "restricted" in path.name]
        for path in hidden:
            path.unlink()
        without_hidden = {tool: self.tools.call(tool, arguments)[0] for tool, arguments in calls}
        self.assertEqual(with_hidden, without_hidden)
        self.assertEqual(resources, self.tools.resource_rows())
        # The cursor issued before the hidden records vanished still works.
        follow, is_error = self.tools.call("query", {"text": "needle-page", "limit": 2,
                                                     "cursor": with_hidden["query"]["next_cursor"]})
        self.assertFalse(is_error, follow)

    def test_a_hidden_change_keeps_cursors_valid_and_a_visible_change_invalidates_them(self) -> None:
        first, _ = self.tools.call("query", {"text": "needle-page", "limit": 2})
        write_doc(self.vault, "notes/page-9-hidden.md", title="Late hidden", sensitivity="restricted", body="needle-page")
        _, is_error = self.tools.call("query", {"text": "needle-page", "limit": 2, "cursor": first["next_cursor"]})
        self.assertFalse(is_error)
        write_doc(self.vault, "notes/page-9.md", title="Late visible", sensitivity="internal", body="needle-page")
        payload, is_error = self.tools.call("query", {"text": "needle-page", "limit": 2, "cursor": first["next_cursor"]})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "invalid_cursor")

    def test_cursors_are_bound_to_their_call_ceiling_and_process(self) -> None:
        page, _ = self.tools.call("trace", {"today": TODAY, "limit": 1})
        cursor = page["next_cursor"]
        restricted = VaultTools(self.vault, max_sensitivity="restricted")
        for tools, tool, arguments in (
            (self.tools, "trace", {"today": TODAY, "limit": 2}),
            (self.tools, "trace", {"today": "2026-09-25", "limit": 1}),
            (self.tools, "trace", {"today": TODAY, "limit": 1, "gaps_only": True}),
            (self.tools, "query", {"limit": 1}),
            (self.tools, "backlinks", {"target": "notes/internal-note", "limit": 1}),
            (restricted, "trace", {"today": TODAY, "limit": 1}),
            (VaultTools(self.vault, max_sensitivity="internal"), "trace", {"today": TODAY, "limit": 1}),
        ):
            with self.subTest(policy=tools.policy, tool=tool, arguments=arguments):
                payload, is_error = tools.call(tool, {**arguments, "cursor": cursor})
                self.assertTrue(is_error)
                self.assertEqual(payload, {"error": {
                    "code": "invalid_cursor",
                    "message": "cursor is not valid for this call or the results changed; "
                    "repeat the call without a cursor",
                }})

    def test_forged_and_malformed_cursors_are_rejected_alike(self) -> None:
        page, _ = self.tools.call("query", {"text": "needle-page", "limit": 1})
        cursor = page["next_cursor"]
        codec = CursorCodec("internal")
        forged_offset = codec.encode("query", None, "x", 3)
        flipped = cursor[:-2] + ("A" if cursor[-2] != "A" else "B") + cursor[-1]
        for bad in (flipped, forged_offset, "", "x", "=" * 10, "A" * 200, "a\x00b", "../../etc/passwd", 7, ["x"]):
            with self.subTest(cursor=repr(bad)):
                payload, is_error = self.tools.call("query", {"text": "needle-page", "limit": 1, "cursor": bad})
                if bad == "":
                    self.assertFalse(is_error)  # empty means "first page"
                    continue
                self.assertTrue(is_error)
                self.assertEqual(payload["error"]["code"], "invalid_cursor")

    def test_resource_listing_pages_visible_decisions_only(self) -> None:
        with patch.object(mcp_server, "RESOURCE_PAGE_SIZE", 2):
            rows, cursor = self.tools.resource_page()
            pages = [rows]
            while cursor is not None:
                rows, cursor = self.tools.resource_page(cursor)
                pages.append(rows)
            with self.assertRaises(ToolFailure) as caught:
                self.tools.resource_page("bogus")
        self.assertEqual(caught.exception.code, "invalid_cursor")
        uris = [row["uri"] for page in pages for row in page]
        self.assertEqual(uris[0], "whykit://decisions")
        self.assertEqual(uris[1:], [f"whykit://record/D-1{index}0" for index in range(5)])
        self.assertEqual(len(pages), 3)
        self.assertNotIn("hidden", json.dumps(pages))


class CompletionTests(VaultToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_decision(self.vault, "06-decisions/d-002-visible.md", decision_id="D-002", sensitivity="internal")
        write_decision(self.vault, "06-decisions/d-003-public.md", decision_id="D-003", sensitivity="public")

    def values(self, tools: VaultTools, ref_type: str, name: str, argument: str, value: object) -> list[str]:
        result = tools.complete(ref_type, name, argument, value)
        self.assertEqual(result["total"], len(result["values"]))
        self.assertFalse(result["has_more"])
        return result["values"]

    def test_decision_ids_offer_only_visible_decisions(self) -> None:
        self.assertEqual(self.values(self.tools, "ref/prompt", "summarize_decision", "decision_id", "D-"),
                         ["D-002", "D-003"])
        self.assertEqual(self.values(self.public, "ref/prompt", "summarize_decision", "decision_id", ""), ["D-003"])
        # A prefix only a hidden decision matches looks like one nothing matches.
        self.assertEqual(self.values(self.tools, "ref/prompt", "summarize_decision", "decision_id", "D-001"), [])

    def test_record_targets_honor_the_ceiling(self) -> None:
        internal = self.values(self.tools, "ref/resource", "whykit://record/{+target}", "target", "")
        public = self.values(self.public, "ref/resource", "whykit://record/{+target}", "target", "")
        self.assertIn("E-001", internal)
        self.assertIn("notes/internal-note.md", internal)
        self.assertNotIn("E-001", public)  # the register is internal
        self.assertEqual(public[0], "D-003")
        self.assertIn("notes/public-note.md", public)
        self.assertNotIn("notes/internal-note.md", public)
        for values in (internal, public):
            self.assertFalse([value for value in values if "restricted" in value or value == "D-001"])
        self.assertEqual(self.values(self.tools, "ref/resource", "whykit://record/{+target}", "target", "NOTES/int"),
                         ["notes/internal-note.md"])

    def test_completion_never_fails_and_caps_its_answer(self) -> None:
        for value in ("a\x00b", "x" * 600, None, 7):
            with self.subTest(value=repr(value)[:20]):
                self.assertEqual(self.tools.complete("ref/prompt", "summarize_decision", "decision_id", value),
                                 {"values": [], "total": 0, "has_more": False})
        for ref_type, name, argument in (
            ("ref/prompt", "unknown", "decision_id"),
            ("ref/prompt", "summarize_decision", "other"),
            ("ref/resource", "whykit://decisions", "target"),
        ):
            self.assertEqual(self.tools.complete(ref_type, name, argument, "")["values"], [])
        self.assertEqual(len(self.values(self.tools, "ref/prompt", "review_evidence_gaps", "today", "")), 1)
        for index in range(120):
            write_doc(self.vault, f"bulk/n-{index:03}.md", title=f"Bulk {index}", sensitivity="internal")
        capped = self.tools.complete("ref/resource", "whykit://record/{+target}", "target", "bulk/")
        self.assertEqual((len(capped["values"]), capped["total"], capped["has_more"]), (100, 120, True))


class WatcherTests(VaultToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_decision(self.vault, "06-decisions/d-002-visible.md", decision_id="D-002", sensitivity="internal")
        self.watcher = VaultWatcher(self.tools)
        self.assertEqual(self.watcher.poll(), [])  # baseline

    def test_nothing_changed_means_no_events(self) -> None:
        self.assertEqual(self.watcher.poll(), [])

    def test_visible_edit_notifies_the_record_uris(self) -> None:
        path = self.vault / "notes" / "internal-note.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        self.assertEqual(self.watcher.poll(), [
            ("updated", "whykit://record/notes/internal-note"),
            ("updated", "whykit://record/notes/internal-note.md"),
        ])

    def test_hidden_edits_additions_and_deletions_are_silent(self) -> None:
        path = self.vault / "notes" / "restricted-secret.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        write_doc(self.vault, "notes/new-secret.md", title="New secret", sensitivity="confidential")
        write_decision(self.vault, "06-decisions/d-009-hidden.md", decision_id="D-009", sensitivity="restricted")
        (self.vault / "06-decisions" / "d-001-restricted.md").unlink()
        self.assertEqual(self.watcher.poll(), [])

    def test_a_record_crossing_the_ceiling_looks_like_a_creation(self) -> None:
        path = self.vault / "06-decisions" / "d-001-restricted.md"
        path.write_text(path.read_text(encoding="utf-8").replace("sensitivity: restricted", "sensitivity: internal"),
                        encoding="utf-8")
        events = self.watcher.poll()
        self.assertIn(("updated", "whykit://record/D-001"), events)
        self.assertIn(("updated", "whykit://decisions"), events)
        self.assertIn(("list_changed", None), events)

    def test_register_rows_notify_only_within_the_ceiling(self) -> None:
        public = VaultWatcher(self.public)
        self.assertEqual(public.poll(), [])
        register = self.vault / "00-context" / "evidence-register.md"
        register.write_text(register.read_text(encoding="utf-8").replace("evidence-sentinel", "changed claim"),
                            encoding="utf-8")
        self.assertIn(("updated", "whykit://record/E-001"), self.watcher.poll())
        self.assertNotIn(("updated", "whykit://record/E-001"), public.poll())


    def test_reset_starts_a_new_baseline(self) -> None:
        path = self.vault / "notes" / "internal-note.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        self.watcher.reset()
        self.assertEqual(self.watcher.poll(), [])

    def test_an_unchanged_vault_is_not_snapshotted_again(self) -> None:
        import time

        real = time.time_ns
        watcher = VaultWatcher(self.tools)
        # Files written in the last two seconds always force a snapshot (a
        # same-tick, same-size rewrite would not change their signature).
        with patch.object(mcp_server.time, "time_ns", lambda: real() + 10 * 10**9):
            self.assertEqual(watcher.poll(), [])
            with patch.object(VaultWatcher, "_snapshot", side_effect=AssertionError("snapshot taken")):
                self.assertEqual(watcher.poll(), [])
            path = self.vault / "notes" / "internal-note.md"
            path.write_text(path.read_text(encoding="utf-8") + "x", encoding="utf-8")
            self.assertIn(("updated", "whykit://record/notes/internal-note.md"), watcher.poll())
        with patch.object(VaultWatcher, "_snapshot", side_effect=AssertionError("snapshot taken")):
            with self.assertRaises(AssertionError):
                watcher.poll()  # just written: inside the racy window


class HttpGuardTests(unittest.TestCase):
    def test_loopback_detection(self) -> None:
        for host in ("127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost", "LOCALHOST"):
            self.assertTrue(is_loopback_host(host), host)
        for host in ("0.0.0.0", "::", "192.0.2.1", "example.com", "localhost.example.com", ""):
            self.assertFalse(is_loopback_host(host), host)

    def test_token_sources_and_strength(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "token"
            good.write_text("t" * 40 + "\n", encoding="utf-8")
            self.assertEqual(read_token(str(good)), "t" * 40)
            weak = Path(tmp) / "weak"
            weak.write_text("short", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_token(str(weak))
            with self.assertRaises(ValueError):
                read_token(str(Path(tmp) / "absent"))
        with patch.dict(os.environ, {"WHYKIT_MCP_TOKEN": "e" * 32}):
            self.assertEqual(read_token(None), "e" * 32)
        with patch.dict(os.environ, {"WHYKIT_MCP_TOKEN": ""}):
            self.assertIsNone(read_token(None))

    def test_bearer_guard_rejects_missing_wrong_and_duplicate_tokens(self) -> None:
        token = "k" * 40
        reached: list[dict] = []

        async def app(scope, receive, send) -> None:
            reached.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})

        guarded = require_bearer(app, token)

        def request(*headers: tuple[bytes, bytes], kind: str = "http") -> int:
            sent: list[dict] = []

            async def send(message: dict) -> None:
                sent.append(message)

            async def receive() -> dict:
                return {"type": "http.request"}

            asyncio.run(guarded({"type": kind, "headers": list(headers)}, receive, send))
            return sent[0]["status"] if sent else 0

        good = (b"authorization", f"Bearer {token}".encode())
        self.assertEqual(request(), 401)
        self.assertEqual(request((b"authorization", b"Bearer wrong")), 401)
        self.assertEqual(request((b"authorization", f"bearer {token}".encode())), 401)
        self.assertEqual(request(good, good), 401)
        self.assertEqual(reached, [])
        self.assertEqual(request(good), 200)
        request(kind="lifespan")  # not an HTTP request: passed through untouched
        self.assertEqual([scope["type"] for scope in reached], ["http", "lifespan"])

    def test_a_reachable_host_without_a_token_is_a_usage_error(self) -> None:
        cases = (
            (["--http", "--host", "0.0.0.0"], "bearer token"),
            (["--host", "127.0.0.1"], "need --http"),
            (["--token-file", "x"], "need --http"),
            (["--http", "--port", "0"], "--port"),
            (["--watch-interval", "-1"], "--watch-interval"),
            (["--http", "--token-file", str(NORTHLINE / "Home.md"), "--host", "0.0.0.0"], None),
            (["--http", "--token-file", str(NORTHLINE / "missing-token")], "cannot read --token-file"),
        )
        for argv, message in cases:
            with self.subTest(argv=argv), patch.dict(os.environ, {"WHYKIT_MCP_TOKEN": ""}):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                    mcp_server.main(["--root", str(NORTHLINE), *argv])
                self.assertEqual(caught.exception.code, 2)
                if message is None:  # a file that is not a token: refused without echoing it
                    self.assertIn("printable ASCII", stderr.getvalue())
                    self.assertNotIn("Map of content", stderr.getvalue())
                else:
                    self.assertIn(message, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
