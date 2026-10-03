"""MCP tool handlers: validation, vault confinement and sensitivity — no SDK needed."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))

from whykit.config import ConfigError  # noqa: E402
from whykit.mcp_server import (  # noqa: E402
    MAX_MCP_CONTEXT,
    MAX_MCP_PACK_DOCS,
    MAX_MCP_RESULTS,
    TOOL_NAMES,
    ToolFailure,
    VaultTools,
    validate_target,
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], text=True, capture_output=True)


def write_doc(
    vault: Path,
    relative: str,
    *,
    title: str,
    sensitivity: str,
    body: str = "",
    extra: str = "",
) -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\naliases: []\ntype: guide\nstatus: approved\n"
        f"owner: Tester\ncreated: 2026-09-23\nlast_updated: 2026-09-23\n"
        f"source_of_truth: false\nsensitivity: {sensitivity}\nsource_ids: []\n"
        f"tags: []\n{extra}---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


class VaultToolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        self.assertEqual(run("init", "--minimal", str(self.vault)).returncode, 0)
        register = self.vault / "00-context" / "evidence-register.md"
        register.write_text(
            register.read_text(encoding="utf-8").replace(
                "| E-001 |  | interview / report / analytics / vendor doc / internal |  |  |  |  |",
                "| E-001 | Private interview | interview | 2026-09-24 | 2026-09-24 | notes/private.md | evidence-sentinel |",
            ),
            encoding="utf-8",
        )
        write_doc(self.vault, "notes/public-note.md", title="Public Alpha", sensitivity="public",
                  body="needle-alpha public-sentinel E-001")
        write_doc(self.vault, "notes/internal-note.md", title="Internal Beta", sensitivity="internal",
                  body="needle-alpha internal-sentinel")
        write_doc(self.vault, "notes/restricted-secret.md", title="Restricted Gamma", sensitivity="restricted",
                  body="needle-alpha restricted-sentinel " + "x" * 5000 + "\n\n[[notes/does-not-exist]]",
                  extra="review_by: 2026-09-01\n")
        (self.vault / "06-decisions" / "d-001-restricted.md").write_text(
            "---\ntitle: Restricted decision\naliases: []\ntype: decision\n"
            "decision_id: D-001\nstatus: draft\nowner: Tester\n"
            "created: 2026-09-24\nlast_updated: 2026-09-24\n"
            "source_of_truth: false\nsensitivity: restricted\n"
            "source_ids: []\ntags: []\n---\n\n"
            "# Restricted decision\n\nrestricted-decision-sentinel\n",
            encoding="utf-8",
        )
        self.tools = VaultTools(self.vault, max_sensitivity="internal")
        self.public = VaultTools(self.vault, max_sensitivity="public")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assertFailure(self, code: str, fn, *args, **kwargs) -> ToolFailure:
        with self.assertRaises(ToolFailure) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class TargetValidationTests(unittest.TestCase):
    def test_accepts_ids_paths_stems_and_aliases(self) -> None:
        for value in ("E-001", "D-042", "notes/public-note", "notes/public-note.md", "Home", "  padded  ",
                      "notes\\windows-style", "a name with spaces", "ünïcode/nöte"):
            with self.subTest(value=value):
                self.assertEqual(validate_target(value), value.strip())

    def test_rejects_anything_that_could_leave_the_vault(self) -> None:
        for value in (
            "../outside", "notes/../../etc/passwd", "..", "./Home", "notes/./x",
            "..\\..\\windows\\system32", "/etc/passwd", "\\\\server\\share\\x",
            "C:\\Windows\\win.ini", "C:/Windows/win.ini", "~/.ssh/id_ed25519", "~root",
            "file:///etc/passwd", "https://example.com/x", "x\x00y", "line\nbreak",
            "tab\there", "bell\x07", "del\x7f", "", "   ", "a" * 513, "n/" + "b" * 256,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ToolFailure) as caught:
                    validate_target(value)
                self.assertEqual(caught.exception.code, "invalid_target")
                # The message never echoes the hostile input back.
                if len(value) > 3:
                    self.assertNotIn(value, caught.exception.message)

    def test_rejects_non_string_targets(self) -> None:
        for value in (None, 1, ["Home"], {"path": "Home"}, b"Home"):
            with self.subTest(value=value):
                with self.assertRaises(ToolFailure):
                    validate_target(value)


class QueryToolTests(VaultToolsTestCase):
    def test_query_returns_only_records_within_the_ceiling(self) -> None:
        report = self.tools.query("needle-alpha", 50)
        paths = {item["path"] for item in report["results"]}
        self.assertEqual(paths, {"notes/public-note.md", "notes/internal-note.md"})
        self.assertEqual(report["max_sensitivity"], "internal")
        public_paths = {item["path"] for item in self.public.query("needle-alpha", 50)["results"]}
        self.assertEqual(public_paths, {"notes/public-note.md"})
        self.assertEqual(self.tools.query("Restricted Gamma")["results"], [])

    def test_query_filters_are_forwarded(self) -> None:
        report = self.tools.query(None, 50, source_id="E-001")
        self.assertEqual([item["path"] for item in report["results"]], ["notes/public-note.md"])
        report = self.tools.query("", 50, doc_type="decision")
        self.assertNotIn("06-decisions/d-001-restricted.md", {item["path"] for item in report["results"]})

    def test_query_limit_is_validated_and_clamped(self) -> None:
        self.assertEqual(self.tools.query("", 10_000)["query"]["limit"], MAX_MCP_RESULTS)
        self.assertEqual(self.tools.query("", 0)["returned"], 0)
        for bad in (-1, True, "5", 2.5, None):
            with self.subTest(limit=bad):
                self.assertFailure("invalid_argument", self.tools.query, "", bad)

    def test_query_rejects_hostile_text_and_filters(self) -> None:
        self.assertFailure("invalid_argument", self.tools.query, "x" * 1001)
        self.assertFailure("invalid_argument", self.tools.query, "a\x00b")
        self.assertFailure("invalid_argument", self.tools.query, 42)
        self.assertFailure("invalid_argument", self.tools.query, "", 5, source_id="../E-001")
        self.assertFailure("invalid_argument", self.tools.query, "", 5, owner="o" * 201)
        self.assertFailure("invalid_argument", self.tools.query, "", 5, canonical_only="yes")


class ContextToolTests(VaultToolsTestCase):
    def test_context_returns_body_marked_untrusted(self) -> None:
        report = self.tools.context("notes/internal-note")
        self.assertTrue(report["exists"])
        self.assertIn("internal-sentinel", report["content"])
        self.assertEqual(report["content_trust"], "untrusted_data")

    def test_hidden_records_are_indistinguishable_from_missing_ones(self) -> None:
        for tools in (self.tools, self.public):
            for hidden, missing in (
                ("notes/restricted-secret", "notes/never-written"),
                ("restricted-secret", "never-written"),
                ("D-001", "D-999"),
            ):
                with self.subTest(policy=tools.policy, target=hidden):
                    hidden_report = tools.context(hidden)
                    missing_report = tools.context(missing)
                    hidden_report["target"] = missing_report["target"]
                    self.assertEqual(hidden_report, missing_report)
                    self.assertNotIn("sentinel", json.dumps(hidden_report))

    def test_public_ceiling_hides_evidence_register_rows(self) -> None:
        report = self.public.context("notes/public-note")
        self.assertEqual(report["evidence"], [])
        self.assertNotIn("evidence-sentinel", json.dumps(report))
        self.assertFalse(self.public.context("E-001")["exists"])
        self.assertTrue(self.tools.context("E-001")["exists"])

    def test_max_chars_is_validated_and_clamped(self) -> None:
        self.assertEqual(self.tools.context("notes/internal-note", 0)["content"], "")
        self.assertTrue(self.tools.context("notes/internal-note", 10_000_000)["exists"])
        for bad in (-1, False, "100"):
            with self.subTest(max_chars=bad):
                self.assertFailure("invalid_argument", self.tools.context, "notes/internal-note", bad)
        self.assertGreaterEqual(MAX_MCP_CONTEXT, 4000)

    def test_symlink_out_of_the_vault_is_not_followed(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("---\ntitle: Outside\nsensitivity: public\n---\n\noutside-sentinel\n", encoding="utf-8")
        try:
            (self.vault / "notes" / "escape.md").symlink_to(outside)
            (self.vault / "linked-dir").symlink_to(self.base, target_is_directory=True)
        except OSError:  # pragma: no cover - platforms without symlink rights
            self.skipTest("symlinks unavailable")
        for tools in (self.tools, self.public):
            for target in ("notes/escape", "escape", "linked-dir/outside", "outside"):
                with self.subTest(target=target):
                    for payload in (tools.context(target), tools.impact(target)):
                        self.assertFalse(payload["exists"])
                        self.assertNotIn("outside-sentinel", json.dumps(payload))
            self.assertEqual(tools.query("outside-sentinel")["results"], [])

    def test_files_outside_the_note_index_are_not_confirmed(self) -> None:
        skipped = self.vault / ".obsidian" / "workspace-note.md"
        skipped.parent.mkdir(exist_ok=True)
        skipped.write_text("skipped-sentinel\n", encoding="utf-8")
        for name in ("context", "impact"):
            with self.subTest(tool=name):
                hidden = getattr(self.tools, name)(".obsidian/workspace-note")
                missing = getattr(self.tools, name)(".obsidian/never-written")
                hidden["target"] = missing["target"]
                self.assertEqual(hidden, missing)


class ImpactToolTests(VaultToolsTestCase):
    def test_impact_reports_visible_references(self) -> None:
        report = self.tools.impact("E-001")
        self.assertTrue(report["exists"])
        self.assertEqual([item["path"] for item in report["references"]], ["notes/public-note.md"])

    def test_impact_hides_restricted_targets_in_every_shape(self) -> None:
        for tools in (self.tools, self.public):
            for hidden, missing in (
                ("notes/restricted-secret", "notes/never-written"),
                ("D-001", "D-999"),
            ):
                with self.subTest(policy=tools.policy, target=hidden):
                    hidden_report = tools.impact(hidden)
                    missing_report = tools.impact(missing)
                    hidden_report["target"] = missing_report["target"]
                    self.assertEqual(hidden_report, missing_report)
        public_hidden = self.public.impact("E-001")
        public_missing = self.public.impact("E-999")
        public_hidden["target"] = public_missing["target"]
        self.assertEqual(public_hidden, public_missing)

    def test_impact_rejects_traversal_before_touching_the_filesystem(self) -> None:
        with patch("whykit.impact.analyze_impact") as analyze:
            self.assertFailure("invalid_target", self.tools.impact, "../../etc/passwd")
            analyze.assert_not_called()


class StatusToolTests(VaultToolsTestCase):
    def test_status_counts_only_visible_records_and_hides_the_host_path(self) -> None:
        report = self.tools.status("2026-09-24")
        text = json.dumps(report)
        self.assertNotIn("root", report)
        self.assertNotIn(str(self.vault), text)
        self.assertNotIn(self.tmp.name, text)
        self.assertNotIn("restricted-secret", text)
        self.assertNotIn("does-not-exist", text)
        self.assertNotIn("restricted", report["sensitivity"])
        self.assertEqual(report["review_queue"], [])
        self.assertEqual(report["evidence_active"], 1)

        everything = VaultTools(self.vault, max_sensitivity="restricted").status("2026-09-24")
        self.assertEqual(everything["documents"], report["documents"] + 2)
        self.assertEqual([item["path"] for item in everything["review_queue"]], ["notes/restricted-secret.md"])
        self.assertTrue(any("does-not-exist" in item["message"] for item in everything["findings"]))
        self.assertGreater(everything["errors"], report["errors"])

    def test_public_status_withholds_internal_register_counts(self) -> None:
        report = self.public.status("2026-09-24")
        self.assertIsNone(report["evidence_active"])
        self.assertIsNone(report["evidence_retired"])
        self.assertEqual(set(report["sensitivity"]), {"public"})
        # Findings come from the confined view: only public files, and a link
        # to an internal note reads as unresolved, exactly like a missing one.
        self.assertLessEqual({item["path"] for item in report["findings"]}, {"Home.md", "notes/public-note.md"})
        self.assertNotIn("sentinel", json.dumps(report))

    def test_status_validates_arguments(self) -> None:
        for bad in ("2026-13-01", "yesterday", "2026-09-24T00:00:00\x00"):
            with self.subTest(today=bad):
                self.assertFailure("invalid_argument", self.tools.status, bad)
        self.assertFailure("invalid_argument", self.tools.status, None, -1)
        self.assertFailure("invalid_argument", self.tools.status, None, True)
        self.assertEqual(self.tools.status("2026-09-24", 10**9)["review_due_days"], 3650)

    def test_status_reports_an_invalid_config_without_its_contents(self) -> None:
        with patch("whykit.config.load_config", side_effect=ConfigError("secret-path")):
            failure = self.assertFailure("invalid_config", self.tools.status, "2026-09-24")
        self.assertNotIn("secret-path", failure.message)


class PackToolTests(VaultToolsTestCase):
    def test_pack_reports_hidden_targets_like_missing_ones(self) -> None:
        report = self.tools.pack(["notes/internal-note", "notes/restricted-secret", "notes/never-written"])
        self.assertEqual([item["target"] for item in report["contexts"]], ["notes/internal-note"])
        self.assertEqual(
            report["missing"],
            [
                {"target": "notes/restricted-secret", "origin": "explicit", "reason": "missing"},
                {"target": "notes/never-written", "origin": "explicit", "reason": "missing"},
            ],
        )
        self.assertNotIn("restricted-sentinel", json.dumps(report))

    def test_hidden_targets_do_not_consume_the_body_budget(self) -> None:
        with_hidden = self.tools.pack(["notes/restricted-secret", "notes/internal-note"], max_chars=100_000)
        without = self.tools.pack(["notes/never-written", "notes/internal-note"], max_chars=100_000)
        self.assertEqual(with_hidden["budget"], without["budget"])

    def test_pack_query_respects_the_ceiling(self) -> None:
        report = self.public.pack([], "needle-alpha")
        self.assertEqual([item["target"] for item in report["contexts"]], ["notes/public-note.md"])
        self.assertEqual(report["evidence"], [])
        self.assertNotIn("evidence-sentinel", json.dumps(report))

    def test_pack_validates_arguments(self) -> None:
        self.assertFailure("invalid_argument", self.tools.pack)
        self.assertFailure("invalid_argument", self.tools.pack, [], "   ")
        self.assertFailure("invalid_argument", self.tools.pack, "Home")
        self.assertFailure("invalid_argument", self.tools.pack, ["Home"] * (MAX_MCP_PACK_DOCS + 1))
        self.assertFailure("invalid_target", self.tools.pack, ["Home", "../etc/passwd"])
        self.assertFailure("invalid_argument", self.tools.pack, ["Home"], None, 0)
        self.assertFailure("invalid_argument", self.tools.pack, ["Home"], None, 8, -1)
        self.assertFailure("invalid_argument", self.tools.pack, ["Home"], agent="root-shell")
        self.assertEqual(self.tools.pack(["Home"], max_docs=500)["budget"]["max_docs"], MAX_MCP_PACK_DOCS)


def write_decision(vault: Path, relative: str, *, decision_id: str, sensitivity: str, body: str = "",
                   status: str = "approved", aliases: str = "[]") -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: Decision {decision_id}\naliases: {aliases}\ntype: decision\n"
        f"decision_id: {decision_id}\nstatus: {status}\nowner: Tester\n"
        f"created: 2026-09-24\nlast_updated: 2026-09-24\nreview_by: 2027-09-24\n"
        f"source_of_truth: false\nsensitivity: {sensitivity}\nsource_ids: []\ntags: []\n---\n\n"
        f"# Decision {decision_id}\n\n{body}\n",
        encoding="utf-8",
    )


class HiddenTwinTests(VaultToolsTestCase):
    """A hidden record that shares a stem, alias or decision ID with a visible
    one must not turn resolution into `ambiguous`: every response has to be
    byte-for-byte what it would be if the hidden record did not exist."""

    def setUp(self) -> None:
        super().setUp()
        # Visible originals.
        write_doc(self.vault, "notes/shared.md", title="Shared visible", sensitivity="internal",
                  body="shared-visible E-001", extra="")
        write_doc(self.vault, "notes/aliased.md", title="Aliased visible", sensitivity="internal",
                  body="aliased-visible")
        (self.vault / "notes" / "aliased.md").write_text(
            (self.vault / "notes" / "aliased.md").read_text(encoding="utf-8").replace("aliases: []", "aliases: [Roadmap]"),
            encoding="utf-8",
        )
        write_doc(self.vault, "notes/toplevel.md", title="Top visible", sensitivity="internal", body="top-visible")
        write_decision(self.vault, "06-decisions/d-002-visible.md", decision_id="D-002", sensitivity="internal",
                       body="Rests on [[notes/shared]] and E-001.")
        write_doc(self.vault, "notes/twin-hub.md", title="Twin hub", sensitivity="internal",
                  body="See [[shared]], [[Roadmap]], [[toplevel]], [[archive/shared]], "
                  "[md](../archive/shared.md) and [[D-002]].")
        # Hidden twins: same stem, same alias, same decision ID, and a root-level
        # file whose path is exactly the bare stem a caller might type.
        self.hidden = [
            "archive/shared.md",
            "archive/roadmap-secret.md",
            "archive/d-002-hidden.md",
            "toplevel.md",
        ]
        write_doc(self.vault, "archive/shared.md", title="Shared hidden", sensitivity="restricted",
                  body="twin-sentinel [[notes/shared]] E-001")
        write_doc(self.vault, "archive/roadmap-secret.md", title="Roadmap hidden", sensitivity="restricted",
                  body="twin-sentinel [[notes/aliased]]")
        (self.vault / "archive" / "roadmap-secret.md").write_text(
            (self.vault / "archive" / "roadmap-secret.md").read_text(encoding="utf-8").replace("aliases: []", "aliases: [Roadmap]"),
            encoding="utf-8",
        )
        write_decision(self.vault, "archive/d-002-hidden.md", decision_id="D-002", sensitivity="restricted",
                       body="twin-sentinel supersedes nothing [[06-decisions/d-002-visible]] E-001")
        write_doc(self.vault, "toplevel.md", title="Top hidden", sensitivity="confidential",
                  body="twin-sentinel [[notes/toplevel]]")

    def snapshot(self, tools: VaultTools) -> dict:
        out: dict = {}
        for target in ("shared", "Roadmap", "toplevel", "D-002", "notes/twin-hub", "notes/shared",
                       "archive/shared", "E-001"):
            out[f"context:{target}"] = tools.call("context", {"target": target})
            out[f"impact:{target}"] = tools.call("impact", {"target": target})
            out[f"backlinks:{target}"] = tools.call("backlinks", {"target": target})
        out["pack"] = tools.call("pack", {"targets": ["shared", "Roadmap", "toplevel", "D-002"], "query": "visible"})
        out["query"] = tools.call("query", {"text": "visible", "limit": 100})
        out["trace"] = tools.call("trace", {"today": "2026-09-24"})
        out["trace:D-002"] = tools.call("trace", {"decision": "D-002", "today": "2026-09-24"})
        out["status"] = tools.call("status", {"today": "2026-09-24"})
        out["decisions"] = tools.decision_index()
        try:
            out["prompt"] = tools.summarize_decision_prompt("D-002")
        except ToolFailure as exc:
            out["prompt"] = exc.payload()
        return out

    def test_hidden_twins_do_not_change_any_response(self) -> None:
        for policy in ("internal", "public"):
            with self.subTest(policy=policy):
                tools = VaultTools(self.vault, max_sensitivity=policy)
                with_twins = self.snapshot(tools)
                self.assertNotIn("twin-sentinel", json.dumps(with_twins))
                if policy == "internal":
                    for target in ("shared", "Roadmap", "toplevel", "D-002"):
                        payload, is_error = with_twins[f"context:{target}"]
                        self.assertFalse(is_error)
                        self.assertTrue(payload["exists"], target)
                        self.assertFalse(payload.get("ambiguous"), target)
                saved = {relative: (self.vault / relative).read_bytes() for relative in self.hidden}
                for relative in self.hidden:
                    (self.vault / relative).unlink()
                try:
                    self.assertEqual(with_twins, self.snapshot(tools))
                finally:
                    for relative, data in saved.items():
                        (self.vault / relative).write_bytes(data)

    def test_visible_duplicates_are_still_reported_as_ambiguous(self) -> None:
        write_doc(self.vault, "other/shared.md", title="Shared second", sensitivity="internal")
        report = self.tools.context("shared")
        self.assertFalse(report["exists"])
        self.assertTrue(report["ambiguous"])


class ConfinedIndexTests(VaultToolsTestCase):
    def test_full_lint_is_unchanged_and_confined_lint_treats_dropped_notes_as_missing(self) -> None:
        from whykit.lint import lint
        from whykit.vault_index import VaultIndex

        write_doc(self.vault, "notes/linker.md", title="Linker", sensitivity="internal",
                  body="[[notes/restricted-secret]] and [md](restricted-secret.md)")
        full = VaultIndex.load(self.vault)
        confined = full.subset(lambda note: note.path.stem != "restricted-secret")

        def codes(index: VaultIndex) -> set[str]:
            _, findings = lint(self.vault, ["notes/linker.md"], orphans=False, secrets=False, vault=index)
            return {item.code for item in findings if item.path == "notes/linker.md"}

        self.assertNotIn("wikilink.missing", codes(full))
        self.assertNotIn("markdown_link.missing", codes(full))
        self.assertIn("wikilink.missing", codes(confined))
        self.assertIn("markdown_link.missing", codes(confined))
        self.assertEqual(full.resolve_link("restricted-secret")[0], (self.vault / "notes" / "restricted-secret.md").resolve())
        self.assertEqual(confined.resolve_link("restricted-secret"), (None, False))
        self.assertEqual(confined.resolve_link("notes/restricted-secret"), (None, False))


class TraceToolTests(VaultToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_decision(self.vault, "06-decisions/d-002-cites.md", decision_id="D-002", sensitivity="internal",
                       body="Based on E-001.")
        write_decision(self.vault, "06-decisions/d-003-bare.md", decision_id="D-003", sensitivity="public",
                       body="No sources yet.")
        write_decision(self.vault, "06-decisions/d-004-via-hidden.md", decision_id="D-004", sensitivity="internal",
                       body="See [[notes/restricted-secret]].")

    def test_trace_reports_evidence_and_gaps_for_visible_decisions(self) -> None:
        report = self.tools.trace(today="2026-09-24")
        by_id = {record["decision_id"]: record for record in report["decisions"]}
        self.assertEqual(set(by_id), {"D-002", "D-003", "D-004"})  # D-001 is restricted
        self.assertEqual([item["id"] for item in by_id["D-002"]["evidence"]], ["E-001"])
        self.assertEqual(by_id["D-002"]["evidence"][0]["state"], "active")
        self.assertEqual(by_id["D-003"]["gaps"], ["no_evidence"])
        # Evidence is never inherited through a hidden note.
        self.assertEqual(by_id["D-004"]["gaps"], ["no_evidence"])
        self.assertEqual(report["matched"], 3)
        self.assertFalse(report["truncated"])
        self.assertNotIn("sentinel", json.dumps(report).replace("evidence-sentinel", ""))

    def test_trace_filters_and_limits(self) -> None:
        gaps = self.tools.trace(today="2026-09-24", gaps_only=True)
        self.assertEqual({record["decision_id"] for record in gaps["decisions"]}, {"D-003", "D-004"})
        limited = self.tools.trace(today="2026-09-24", limit=1)
        self.assertEqual(len(limited["decisions"]), 1)
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["summary"]["decisions"], 3)
        self.assertEqual(self.tools.trace(today="2026-09-24", limit=10**9)["matched"], 3)

    def test_hidden_decision_traces_like_a_missing_one(self) -> None:
        hidden = self.tools.trace(decision="D-001", today="2026-09-24")
        missing = self.tools.trace(decision="D-999", today="2026-09-24")
        hidden["decision"] = missing["decision"]
        self.assertEqual(hidden, missing)
        self.assertEqual(hidden["decisions"], [])

    def test_public_trace_withholds_register_details(self) -> None:
        report = self.public.trace(today="2026-09-24")
        self.assertEqual([record["decision_id"] for record in report["decisions"]], ["D-003"])
        self.assertEqual(report["evidence_details"], "withheld")
        text = json.dumps(report)
        self.assertNotIn("Private interview", text)
        self.assertNotIn("evidence-sentinel", text)

    def test_trace_validates_arguments(self) -> None:
        for kwargs in (
            {"decision": "E-001"}, {"decision": "D-1"}, {"decision": "../D-001"}, {"decision": 5},
            {"today": "2026-02-30"}, {"today": "x" * 40}, {"gaps_only": "yes"}, {"limit": -1},
            {"limit": True}, {"decision": "D-001\n"},
        ):
            with self.subTest(**{key: repr(value) for key, value in kwargs.items()}):
                self.assertFailure("invalid_argument", self.tools.trace, **kwargs)


class BacklinksToolTests(VaultToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        for index in range(3):
            write_doc(self.vault, f"notes/linker-{index}.md", title=f"Linker {index}", sensitivity="internal",
                      body="[[notes/internal-note]]")
        write_doc(self.vault, "notes/secret-linker.md", title="Secret linker", sensitivity="restricted",
                  body="[[notes/internal-note]] E-001")

    def test_backlinks_list_only_visible_sources(self) -> None:
        report = self.tools.backlinks("notes/internal-note")
        self.assertTrue(report["exists"])
        sources = [item["from"] for item in report["backlinks"]]
        self.assertEqual(sources, ["notes/linker-0", "notes/linker-1", "notes/linker-2"])
        self.assertEqual(report["count"], 3)
        self.assertNotIn("secret-linker", json.dumps(report))
        evidence = self.tools.backlinks("E-001")
        self.assertEqual([item["from"] for item in evidence["backlinks"]], ["notes/public-note"])

    def test_backlinks_limit_is_clamped_and_reported(self) -> None:
        report = self.tools.backlinks("notes/internal-note", limit=2)
        self.assertEqual(len(report["backlinks"]), 2)
        self.assertEqual(report["count"], 3)
        self.assertTrue(report["truncated"])
        self.assertFalse(self.tools.backlinks("notes/internal-note", limit=10**9)["truncated"])

    def test_hidden_targets_look_missing(self) -> None:
        for tools in (self.tools, self.public):
            for hidden, missing in (
                ("notes/restricted-secret", "notes/never-written"),
                ("restricted-secret", "never-written"),
                ("D-001", "D-999"),
            ):
                with self.subTest(policy=tools.policy, target=hidden):
                    hidden_report = tools.backlinks(hidden)
                    missing_report = tools.backlinks(missing)
                    for report in (hidden_report, missing_report):
                        report.pop("target")
                        report.pop("id")
                    self.assertEqual(hidden_report, missing_report)
        public_hidden = self.public.backlinks("E-001")
        public_missing = self.public.backlinks("E-999")
        for report in (public_hidden, public_missing):
            report.pop("target")
            report.pop("id")
        self.assertEqual(public_hidden, public_missing)

    def test_backlinks_validate_arguments(self) -> None:
        for bad in ("../x", "/etc/passwd", "~/x", "file:///etc/passwd", "", "a\x00b", 7):
            with self.subTest(target=repr(bad)):
                self.assertFailure("invalid_target", self.tools.backlinks, bad)
        for bad in (-1, True, "3"):
            with self.subTest(limit=repr(bad)):
                self.assertFailure("invalid_argument", self.tools.backlinks, "Home", bad)


class ResourceAndPromptTests(VaultToolsTestCase):
    def test_decision_index_lists_only_visible_decisions(self) -> None:
        write_decision(self.vault, "06-decisions/d-002-visible.md", decision_id="D-002", sensitivity="internal")
        index = self.tools.decision_index()
        self.assertEqual([row["decision_id"] for row in index["decisions"]], ["D-002"])
        self.assertEqual(index["decisions"][0]["uri"], "whykit://record/D-002")
        self.assertEqual(self.public.decision_index()["decisions"], [])

    def test_read_record_hides_hidden_missing_and_invalid_alike(self) -> None:
        self.assertIn("internal-sentinel", self.tools.read_record("notes/internal-note")["content"])
        self.assertIsNone(self.tools.read_record("notes/restricted-secret"))
        self.assertIsNone(self.tools.read_record("notes/never-written"))
        self.assertIsNone(self.tools.read_record("D-001"))
        self.assertFailure("invalid_target", self.tools.read_record, "../x")

    def test_summarize_prompt_embeds_ceiling_filtered_data(self) -> None:
        write_decision(self.vault, "06-decisions/d-002-visible.md", decision_id="D-002", sensitivity="internal",
                       body="prompt-body Based on E-001.")
        text = self.tools.summarize_decision_prompt("D-002")
        self.assertIn("prompt-body", text)
        self.assertIn('content_trust="untrusted_data"', text)
        self.assertIn("E-001", text)
        hidden = self.assertFailure("not_found", self.tools.summarize_decision_prompt, "D-001")
        missing = self.assertFailure("not_found", self.tools.summarize_decision_prompt, "D-999")
        self.assertEqual(hidden.message, missing.message)
        self.assertFailure("invalid_argument", self.tools.summarize_decision_prompt, "notes/internal-note")

    def test_gap_prompt_lists_live_decisions_with_gaps(self) -> None:
        write_decision(self.vault, "06-decisions/d-002-bare.md", decision_id="D-002", sensitivity="internal",
                       body="gap-body")
        text = self.tools.evidence_gaps_prompt("2026-09-24")
        self.assertIn("D-002", text)
        self.assertIn("no_evidence", text)
        self.assertNotIn("restricted-decision-sentinel", text)
        self.assertFailure("invalid_argument", self.tools.evidence_gaps_prompt, "not-a-date")


class MissingExtraTests(VaultToolsTestCase):
    def test_missing_extra_gives_a_working_install_hint_and_exits_2(self) -> None:
        import contextlib
        import io

        from whykit import mcp_server

        stderr = io.StringIO()
        # A None entry makes `from mcp.server import ...` raise ImportError.
        with patch.dict(sys.modules, {"mcp": None, "mcp.server": None}), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                mcp_server.main(["--root", str(self.vault)])
        self.assertEqual(caught.exception.code, 2)
        message = stderr.getvalue()
        self.assertIn("uv sync --extra mcp", message)
        self.assertIn("git+https://github.com/CometWeb-io/whykit", message)
        self.assertNotIn("pip install 'whykit[mcp]'", message)


class ErrorBoundaryTests(VaultToolsTestCase):
    def test_every_tool_is_read_only(self) -> None:
        before = tree_digest(self.vault)
        for name, arguments in (
            ("query", {"text": "needle-alpha"}),
            ("context", {"target": "notes/public-note"}),
            ("impact", {"target": "E-001"}),
            ("status", {"today": "2026-09-24"}),
            ("pack", {"targets": ["D-001", "notes/public-note"], "query": "needle"}),
            ("trace", {"today": "2026-09-24"}),
            ("backlinks", {"target": "E-001"}),
        ):
            payload, is_error = self.tools.call(name, arguments)
            self.assertFalse(is_error, (name, payload))
        self.assertEqual(tree_digest(self.vault), before)
        self.assertEqual(set(TOOL_NAMES), {"query", "context", "impact", "status", "pack", "trace", "backlinks"})

    def test_call_returns_structured_errors(self) -> None:
        payload, is_error = self.tools.call("context", {"target": "../x"})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "invalid_target")
        payload, is_error = self.tools.call("delete", {})
        self.assertEqual((payload["error"]["code"], is_error), ("unknown_tool", True))
        payload, is_error = self.tools.call("impact", {"target": "Home", "root": "/"})
        self.assertEqual((payload["error"]["code"], is_error), ("invalid_argument", True))
        payload, is_error = self.tools.call("call", {})
        self.assertEqual(payload["error"]["code"], "unknown_tool")

    def test_unexpected_failures_do_not_leak_their_text(self) -> None:
        leak = f"{self.vault}/notes/restricted-secret.md restricted-sentinel"
        with patch("whykit.query.query_vault", side_effect=OSError(leak)):
            payload, is_error = self.tools.call("query", {"text": "x"})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"]["code"], "internal_error")
        self.assertNotIn("restricted", json.dumps(payload))
        self.assertNotIn(str(self.vault), json.dumps(payload))

    def test_vault_that_disappears_is_reported(self) -> None:
        shutil.rmtree(self.vault / "00-context")
        for name, arguments in (("query", {}), ("context", {"target": "Home"}), ("status", {})):
            with self.subTest(tool=name):
                payload, is_error = self.tools.call(name, arguments)
                self.assertTrue(is_error)
                self.assertEqual(payload["error"]["code"], "vault_unavailable")
                self.assertNotIn(str(self.vault), json.dumps(payload))

    def test_construction_rejects_bad_roots_and_policies(self) -> None:
        with self.assertRaises(SystemExit):
            VaultTools(self.base)
        with self.assertRaises(SystemExit):
            VaultTools(self.vault, max_sensitivity="top-secret")
        self.assertEqual(VaultTools(self.vault, max_sensitivity="PUBLIC").policy, "public")

    def test_relative_root_is_pinned_at_startup(self) -> None:
        cwd = os.getcwd()
        try:
            os.chdir(self.base)
            tools = VaultTools(Path("vault"))
        finally:
            os.chdir(cwd)
        self.assertTrue(tools.vault.is_absolute())
        self.assertTrue(tools.context("notes/internal-note")["exists"])


if __name__ == "__main__":
    unittest.main()
