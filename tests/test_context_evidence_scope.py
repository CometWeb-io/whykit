"""Independent evidence-scope regressions over synthetic local vaults."""
from __future__ import annotations

import datetime as dt
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whykit import context, graph, impact, lint, pack, trace
from whykit.evidence import retire_evidence
from whykit.mcp_server import VaultTools
from whykit.parse_cache import persistent
from whykit.sensitivity import classified_evidence
from whykit.vault_index import VaultIndex

DAY = dt.date(2026, 9, 17)
PRIVATE = "restrictedrowmetadatacanary"


def write_note(root, name, body="", **extra):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {"title": "Public guide", "aliases": [], "type": "guide", "status": "draft", "owner": "Ada Example",
              "created": DAY.isoformat(), "last_updated": DAY.isoformat(), "source_of_truth": False,
              "source_ids": [], "tags": [], "sensitivity": "public", **extra}
    path.write_text("---\n" + "\n".join(f"{key}: {json.dumps(value)}" for key, value in fields.items())
                    + "\n---\n\n" + body, encoding="utf-8")
    return path


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


class ContextEvidenceScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "vault"
        self.root.mkdir()
        write_note(self.root, "Home.md", "Local synthetic map.")
        self.note = write_note(self.root, "notes/public.md", "Public note cites E-001 and E-002.", source_ids=["E-001", "E-002"])
        write_note(self.root, "06-decisions/d-001-example.md", "E-002", type="decision", decision_id="D-001")
        self.register = write_note(self.root, "00-context/evidence-register.md", f"""| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |
|---|---|---|---|---|---|---|---|
| E-001 | Public source | fixture | 2026-09-17 | 2026-09-17 | https://public.invalid/one | Ordinary claim | public |
| E-002 | {PRIVATE} | fixture | 2026-09-17 | 2026-09-17 | https://private.invalid/{PRIVATE} | {PRIVATE} | restricted |
| E-003 | retiredchaincanary | fixture | 2026-09-17 | 2026-09-17 | https://example.invalid/three | Prior declaration | public |

## Retired sources

| ID | Source | Retired on | Why | Replaced by | Sensitivity |
|---|---|---|---|---|---|
""")

    def grant(self, *, rows=True, register=False, ids=("E-001",)):
        full = VaultIndex.load(self.root)
        active, retired, _, _ = classified_evidence(full)
        view = full.subset(lambda note: register or note.path != self.register)
        if rows:
            view.derived["evidence_register"] = ({key: row for key, row in active.items() if key in ids},
                                                   {key: row for key, row in retired.items() if key in ids}, [])
        return view

    def assert_no_private(self, report):
        self.assertFalse(PRIVATE in json.dumps(report), "restricted row metadata leaked")

    def test_public_pack_does_not_enrich_a_public_note_with_restricted_rows(self):
        report = pack.build_pack(self.root, targets=["notes/public"], allowed_sensitivities={"public"})
        self.assertEqual(report["resolved"], 1)
        self.assert_no_private(report)
        public = next(row for row in report["evidence"] if row["id"] == "E-001")
        self.assertEqual(public["record"]["source"], "Public source")

    def readers(self, view):
        return {
            "context-note": lambda: context.build_context(self.root, "notes/public", vault=view),
            "context-evidence": lambda: context.build_context(self.root, "E-002", vault=view),
            "impact-evidence": lambda: impact.analyze_impact(self.root, "E-002", vault=view),
            "graph": lambda: graph.build_graph(self.root, vault=view),
            "trace": lambda: trace.build_trace(self.root, today=DAY, vault=view, access_age_days={}),
            "pack": lambda: pack.build_pack(self.root, targets=["notes/public"], vault=view),
        }

    def test_sparse_grant_all_readers_never_reopen_or_reload_omitted_register(self):
        view = self.grant()
        before = tree(self.root)
        original = lint.read_vault_text

        def guarded(path, *args, **kwargs):
            if Path(path) == self.register:
                raise AssertionError("omitted register reopened")
            return original(path, *args, **kwargs)

        with patch.object(lint, "read_vault_text", side_effect=guarded), patch.object(
            VaultIndex, "load", side_effect=AssertionError("confined grant reloaded")
        ):
            for name, reader in self.readers(view).items():
                with self.subTest(reader=name):
                    result = reader()
                    self.assert_no_private(result)
                    if name in {"context-evidence", "impact-evidence"}:
                        self.assertFalse(result["exists"])
        self.assertEqual(tree(self.root), before)

    def test_empty_derived_rows_are_a_grant_and_absent_rows_use_only_captured_register(self):
        cases = [(self.grant(ids=(), register=True), False),
                 (self.grant(rows=False, register=True), True),
                 (self.grant(rows=False), False)]
        with patch.object(lint, "_parse_evidence_register", side_effect=AssertionError("ambient register parsed")):
            for view, exists in cases:
                with self.subTest(derived="evidence_register" in view.derived, captured=bool(
                    view.note_for(self.register)
                )):
                    report = context.build_context(self.root, "E-001", vault=view)
                    self.assertEqual(report["exists"], exists)
                    if exists:
                        self.assertEqual(report["evidence"]["record"]["source"], "Public source")

    def test_parent_evidence_view_cannot_be_widened_by_a_broader_child_grant(self):
        broader = self.grant(ids=("E-001", "E-002"))
        rows = self.grant().derived["evidence_register"]
        with lint.evidence_view(self.root, rows):
            for name, reader in self.readers(broader).items():
                with self.subTest(reader=name):
                    result = reader()
                    self.assert_no_private(result)
                    if name in {"context-evidence", "impact-evidence"}:
                        self.assertFalse(result["exists"])
        with lint.evidence_view(self.root, broader.derived["evidence_register"]):
            result = pack.build_pack(self.root, targets=["notes/public"],
                                     allowed_sensitivities={"public"}, vault=broader)
            self.assert_no_private(result)
        self.assertTrue(context.build_context(self.root, "E-002")["exists"])

    def test_captured_row_a_survives_disk_b_and_empty_grant_never_refills(self):
        view, empty = self.grant(), self.grant(ids=())
        self.register.write_text(self.register.read_text(encoding="utf-8").replace(
            "Public source", "diskchangedprivatecanary"
        ), encoding="utf-8")
        with patch.object(lint, "_parse_evidence_register", side_effect=AssertionError("disk B parsed")):
            report = context.build_context(self.root, "E-001", vault=view)
            self.assertEqual(report["evidence"]["record"]["source"], "Public source")
            self.assertFalse(context.build_context(self.root, "E-001", vault=empty)["exists"])

    def test_register_md_alias_and_poisoned_metadata_respect_row_floors(self):
        temporary = self.register.with_name("temporary-register.md")
        self.register.rename(temporary)
        self.register = temporary.rename(temporary.with_name("EVIDENCE-REGISTER.MD"))
        result = pack.build_pack(self.root, targets=["notes/public"], allowed_sensitivities={"public"})
        self.assert_no_private(result)
        public = next(row for row in result["evidence"] if row["id"] == "E-001")
        self.assertEqual(public["record"]["source"], "Public source")
        self.register.write_text(self.register.read_text(encoding="utf-8").replace(
            'sensitivity: "public"', 'sensitivity: "public"\nSensitivity: "restricted"'
        ), encoding="utf-8")
        poisoned = pack.build_pack(self.root, targets=["notes/public"], allowed_sensitivities={"public"})
        self.assert_no_private(poisoned)
        self.assertFalse(any(row.get("record") for row in poisoned["evidence"]))

    def test_retired_replacement_chain_raises_the_effective_floor(self):
        retire_evidence(self.root, "E-003", reason="Replaced declaration", replaced_by="E-002", today=DAY)
        write_note(self.root, "notes/retired.md", "Historical citation E-003.", source_ids=["E-003"])
        result = pack.build_pack(self.root, targets=["notes/retired"], allowed_sensitivities={"public"})
        self.assertFalse("retiredchaincanary" in json.dumps(result), "replacement-chain floor lost")

    def test_denied_primary_body_is_not_budgeted_and_does_not_change_public_budget(self):
        hidden = write_note(self.root, "notes/hidden.md", "privatebodycanary" * 1000, sensitivity="restricted")
        view = VaultIndex.load(self.root)
        original = context._budgeted

        def guarded(note, budget):
            self.assertNotEqual(note.path, hidden, "denied body entered materialization")
            return original(note, budget)

        with patch.object(context, "_budgeted", side_effect=guarded):
            denied = pack.build_pack(self.root, targets=["notes/hidden", "notes/public"],
                                     max_docs=2, max_chars=80, allowed_sensitivities={"public"}, vault=view)
            missing = pack.build_pack(self.root, targets=["notes/absent", "notes/public"],
                                      max_docs=2, max_chars=80, allowed_sensitivities={"public"}, vault=view)
        self.assertEqual(denied["budget"], missing["budget"])
        self.assertEqual(denied["contexts"], missing["contexts"])

    def test_unscoped_legacy_readers_keep_full_rows_and_record_details(self):
        full = VaultIndex.load(self.root)
        rows = lint.evidence_register(self.root)
        ordinary = {name: reader() for name, reader in self.readers(full).items()}
        self.assertEqual(ordinary["context-evidence"]["evidence"]["record"], rows[0]["E-002"])
        self.assertTrue(ordinary["impact-evidence"]["exists"])
        self.assertTrue(PRIVATE in json.dumps(ordinary))

    def test_captured_per_row_register_body_cannot_bypass_the_row_grant(self):
        view = self.grant(register=True)
        for target in ("00-context/evidence-register.md", "evidence-register"):
            with self.subTest(target=target):
                self.assertFalse(context.build_context(self.root, target, vault=view)["exists"])
        before = tree(self.root)
        with persistent(True):
            for reader in self.readers(view).values():
                self.assert_no_private(reader())
        self.assertEqual(tree(self.root), before)

    def test_mcp_public_controls_keep_hidden_rows_absent_and_no_persistent_writes(self):
        tools = VaultTools(self.root, max_sensitivity="public")
        before = tree(self.root)
        with persistent(True):
            self.assert_no_private(tools.context("notes/public"))
            self.assert_no_private(tools.pack(targets=["notes/public"]))
            self.assertFalse(tools.context("E-002")["exists"])
            self.assertIsNone(tools.read_record("E-002"))
        self.assertEqual(tree(self.root), before)

    def test_parent_rows_are_inherited_only_when_child_has_no_row_snapshot(self):
        absent = self.grant(rows=False)
        empty = self.grant(ids=())
        rows = self.grant().derived["evidence_register"]
        with lint.evidence_view(self.root, rows), patch.object(
            lint, "_parse_evidence_register", side_effect=AssertionError("ambient register parsed")
        ):
            inherited = context.build_context(self.root, "E-001", vault=absent)
            self.assertEqual(inherited["evidence"]["record"]["source"], "Public source")
            self.assertFalse(context.build_context(self.root, "E-001", vault=empty)["exists"])
            self.assertFalse(context.build_context(self.root, "E-002", vault=absent)["exists"])

    def test_homogeneous_raw_register_requires_payload_equality_not_only_equal_ids(self):
        write_note(self.root, "00-context/evidence-register.md", """| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-001 | Captured register B | fixture | 2026-09-17 | 2026-09-17 | https://example.invalid/one | Changed declaration |
""")
        exact = self.grant(register=True)
        positive = context.build_context(self.root, "evidence-register", vault=exact)
        self.assertTrue(positive["exists"])
        self.assertIn("Captured register B", positive["content"])
        previous = self.grant(register=True)
        previous.derived["evidence_register"][0]["E-001"]["source"] = "Granted previous A"
        self.assertFalse(context.build_context(self.root, "evidence-register", vault=previous)["exists"])
        row = context.build_context(self.root, "E-001", vault=previous)
        self.assertEqual(row["evidence"]["record"]["source"], "Granted previous A")

    def test_direct_lint_uses_supplied_rows_without_ambient_register_or_hidden_logs(self):
        write_note(self.root, "06-decisions/decision-log.md", "hidden decision log body")
        write_note(self.root, "00-context/review-log.md", "hidden review log body")
        view = self.grant()
        view = view.subset(lambda note: not note.path.name.endswith("-log.md"))
        view.derived["evidence_register"] = self.grant().derived["evidence_register"]
        original = lint.read_vault_text

        def guarded(path, *args, **kwargs):
            self.assertNotIn(Path(path), [self.register, self.root / "06-decisions/decision-log.md",
                                          self.root / "00-context/review-log.md"])
            return original(path, *args, **kwargs)

        before = tree(self.root)
        with persistent(True), patch.object(lint, "read_vault_text", side_effect=guarded):
            _, findings = lint.lint(self.root, ["notes/public.md"], vault=view,
                                    orphans=False, secrets=False, today=DAY)
        missing = [row.message for row in findings if row.code == "evidence.missing"]
        self.assertTrue(any("E-002" in message for message in missing))
        self.assertFalse(any("E-001" in message for message in missing))
        self.assertEqual(tree(self.root), before)

    def test_homogeneous_register_ignores_only_diagnostic_line_in_complete_row_grant(self):
        write_note(self.root, "00-context/evidence-register.md", """| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-001 | Complete source | fixture | 2026-09-17 | 2026-09-17 | https://example.invalid/one | Same declaration |
""")
        complete = self.grant(register=True)
        complete.derived["evidence_register"][0]["E-001"]["line"] = "0"
        result = context.build_context(self.root, "evidence-register", vault=complete)
        self.assertTrue(result["exists"], "diagnostic line is not evidence payload")
        self.assertIn("Complete source", result["content"])
        for field in ("source", "location", None):
            narrowed = self.grant(register=True)
            if field is None:
                narrowed.derived["evidence_register"][0].pop("E-001")
            else:
                narrowed.derived["evidence_register"][0]["E-001"][field] = "different granted payload"
            with self.subTest(field=field):
                self.assertFalse(context.build_context(self.root, "evidence-register", vault=narrowed)["exists"])

    def test_invalid_supplied_vault_cannot_be_discarded_into_an_ambient_reload(self):
        rows = self.grant().derived["evidence_register"]
        for policy in (True, False):
            parent = nullcontext() if policy else lint.evidence_view(self.root, rows)
            with self.subTest(explicit_public_policy=policy), parent, patch.object(
                VaultIndex, "load", side_effect=AssertionError("invalid supplied vault triggered reload")
            ) as reload:
                with self.assertRaises(ValueError):
                    if policy:
                        pack.build_pack(self.root, targets=["notes/public"],
                                        allowed_sensitivities={"public"}, vault=object())
                    else:
                        context.build_context(self.root, "notes/public", vault=object())
                reload.assert_not_called()
