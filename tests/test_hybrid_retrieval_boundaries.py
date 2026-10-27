"""Independent hybrid scope, governance and bounded-work regressions.

Only synthetic local vaults; no model/provider or relevance-measurement claims.
"""
from __future__ import annotations

import builtins
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from _vaults import fill_decision
from whykit import dependency_graph
from whykit import hybrid_retrieval as hybrid
from whykit.context_applicability import context_metadata
from whykit.parse_cache import persistent
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision
from whykit.sensitivity import classified_evidence
from whykit.vault_index import VaultIndex

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 9, 17)
ALL = {"public", "internal", "confidential", "restricted"}
LOGS = ("00-context/review-log.md", "06-decisions/decision-log.md")


def write_note(root: Path, name: str, body: str = "", **metadata) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = {"title": "Visible reference", "aliases": [], "type": "guide", "status": "draft",
              "owner": "Ada Example", "created": DAY.isoformat(), "last_updated": DAY.isoformat(),
              "source_of_truth": False, "source_ids": [], "tags": [], "sensitivity": "public", **metadata}
    target.write_text("---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
                      + "\n---\n\n" + body, encoding="utf-8")
    return target


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def prepared(index: VaultIndex, rows=None):
    rows = ({}, {}, []) if rows is None else rows
    hashes = {note.path: hashlib.sha256(note.text.encode("utf-8")).hexdigest() for note in index.notes}
    metadata = context_metadata(index, [], False, set(), rows, {}, {"evidence_access_age_days": {}}, DAY, hashes)
    return {"rows": rows, "metadata": metadata, "document_hashes": hashes, "valid_on": DAY}


class HybridRetrievalBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "vault"
        self.root.mkdir()
        write_note(self.root, "seed.md", "needle [[answer]]")
        write_note(self.root, "answer.md", "Implementation instructions without matching vocabulary.")

    def retrieve(self, text="needle", **options):
        return hybrid.retrieve_hybrid(self.root, text, **{"valid_on": DAY, **options})

    def rank(self, index, text="needle", rows=None, **options):
        return hybrid.rank_prepared(index, text, **{**prepared(index, rows), **options})

    def approval_root(self) -> Path:
        root = self.base / "approval"
        shutil.copytree(ROOT / "examples/approval", root)
        (root / "whykit.toml").write_text("[evidence_access_age_days]\nfixture = 90\n", encoding="utf-8")
        return root

    def native_decision(self, root: Path, title: str, *, approve=True, **fields):
        identity, path = create_decision(root, title, owner="Ada Example", sensitivity="public", source_ids=["E-001"], today=DAY)
        fill_decision(path)
        text = path.read_text(encoding="utf-8")
        for key, value in fields.items():
            text = _frontmatter_replace(text, key, str(value))
        path.write_text(text, encoding="utf-8")
        if approve:
            options = {"reviewer": "Ada Example", "today": DAY, "next_review": "2026-12-16"}
            preview = approve_decision(root, identity, **options)
            approve_decision(root, identity, **options, write=True, expected_sha256=preview["expected_sha256"])
        return identity, path

    def test_default_public_denied_notes_cannot_change_scores_graph_or_cap_refusal(self) -> None:
        baseline_default = self.retrieve()
        self.assertTrue(baseline_default["results"])
        with patch.object(hybrid, "MAX_VISIBLE_BYTES", 4096), patch.object(hybrid, "MAX_CONSTRUCTION_NODES", 4):
            baseline = self.retrieve()
            for i in range(20):
                write_note(self.root, f"denied-{i:02d}.md", "needle " * 3000 + " [[seed]] [[answer]]",
                           sensitivity="restricted", title=f"PRIVATE_TITLE_{i}")
            # Duplicate alias-case sensitivity is canonical fail-closed, not public.
            write_note(self.root, "case-denied.md", "needle " * 1000, sensitivity="public", Sensitivity="restricted")
            self.assertEqual(self.retrieve(), baseline)
        self.assertNotIn("PRIVATE_TITLE", json.dumps(baseline))
        self.assertEqual(self.retrieve(allowed_sensitivities={"public"}), baseline_default)

    def test_missing_and_hidden_bridges_are_equivalent_and_never_walked(self) -> None:
        write_note(self.root, "seed.md", "needle [[bridge]]")
        missing = self.retrieve()
        write_note(self.root, "bridge.md", "[[answer]]", sensitivity="restricted")
        self.assertEqual(self.retrieve(), missing)
        self.assertNotIn("answer.md", {r["path"] for r in missing["results"]})

    def test_future_expired_invalid_bridge_nodes_do_not_affect_the_visible_graph(self) -> None:
        write_note(self.root, "seed.md", "needle [[bridge]]")
        baseline = self.retrieve()
        for fields in ({"valid_from": "2026-09-18"}, {"valid_until": DAY.isoformat()}, {"valid_from": "malformed"}):
            with self.subTest(fields=fields):
                bridge = write_note(self.root, "bridge.md", "needle needle [[answer]]", **fields)
                self.assertEqual(self.retrieve(), baseline)
                bridge.unlink()

    def test_capture_grant_preserves_only_explicit_e_rows_and_never_reloads(self) -> None:
        register = write_note(self.root, "00-context/evidence-register.md", """
| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |
|---|---|---|---|---|---|---|---|
| E-001 | Permitted source | fixture | 2026-09-17 | 2026-09-17 | https://example.com/one | First claim | public |
| E-002 | PRIVATE_ROW_CANARY | fixture | 2026-09-17 | 2026-09-17 | https://example.com/two | Second claim | restricted |
""")
        write_note(self.root, "seed.md", "needle", source_ids=["E-001"])
        write_note(self.root, "answer.md", "Useful material", source_ids=["E-001"])
        full = VaultIndex.load(self.root)
        active, _, _, _ = classified_evidence(full)
        grant = full.subset(lambda n: n.path != register)
        grant.derived["evidence_register"] = ({"E-001": active["E-001"]}, {}, [])
        with patch.object(VaultIndex, "load", side_effect=AssertionError("grant must never broaden")):
            report = self.retrieve(vault=grant, allowed_sensitivities={"public", "restricted"})
        self.assertIn("answer.md", {row["path"] for row in report["results"]})
        wire = json.dumps(report)
        self.assertNotIn("PRIVATE_ROW_CANARY", wire)
        self.assertNotIn("E-002", wire)
        empty = full.subset(lambda n: n.path != register)
        empty.derived["evidence_register"] = ({}, {}, [])
        with patch.object(VaultIndex, "load", side_effect=AssertionError("empty grant must not refill")):
            without = self.retrieve(vault=empty, allowed_sensitivities=ALL)
        self.assertNotIn("answer.md", {row["path"] for row in without["results"]})
        self.assertNotIn("evidence:E-001", json.dumps(without["citations"]))
        self.assertFalse(any(row["explanation"] is not None for row in without["results"]))

    def test_retired_and_unknown_validity_evidence_are_not_current_bridges(self) -> None:
        write_note(self.root, "seed.md", "needle", source_ids=["E-001"], valid_from="2026-09-01", valid_until="2027-01-01")
        write_note(self.root, "answer.md", "Useful content", source_ids=["E-001"], valid_from="2026-09-01", valid_until="2027-01-01")
        index = VaultIndex.load(self.root).subset(lambda _note: True)
        row = {"source": "A declaration", "sensitivity": "public", "type": "fixture", "accessed": DAY.isoformat()}
        active = self.rank(index, rows=({"E-001": row}, {}, []))
        self.assertIn("answer.md", {r["path"] for r in active["results"]})
        for rows, policy in ((({}, {"E-001": row}, []), "active_or_unknown"),
                             (({"E-001": row}, {}, []), "active")):
            with self.subTest(policy=policy, retired=bool(rows[1])):
                report = self.rank(index, rows=rows, validity=policy)
                self.assertEqual({r["path"] for r in report["results"]}, {"seed.md"})
                self.assertNotIn("evidence:E-001", json.dumps(report["citations"]))

    def test_changed_pinned_source_never_remains_a_current_decision_premise(self) -> None:
        from whykit.artifact_store import capture_artifact

        root = self.base / "source-state"
        shutil.copytree(ROOT / "examples/provenance", root, ignore=shutil.ignore_patterns(".whykit"))
        options = {"valid_on": DAY, "allowed_sensitivities": ALL, "limit": 100}
        baseline = hybrid.retrieve_hybrid(root, "separate approved reasoning", **options)
        self.assertIn("D-003", {r.get("decision_id") for r in baseline["results"]})
        source = self.base / "new-source.txt"
        source.write_bytes(b"A genuinely different local observation for the synthetic source.\n")
        preview = capture_artifact(root, "E-004", source, recorded_at="2026-09-17T13:00:00Z")
        capture_artifact(root, "E-004", source, recorded_at="2026-09-17T13:00:00Z", write=True,
                         expected_sha256=preview["expected_sha256"])
        updated = hybrid.retrieve_hybrid(root, "separate approved reasoning", **options)
        self.assertNotIn("D-003", {r.get("decision_id") for r in updated["results"]})
        references = hybrid.retrieve_hybrid(root, "separate approved reasoning", decision_role="reference", **options)
        decision = next(r for r in references["results"] if r.get("decision_id") == "D-003")
        self.assertFalse(decision["applicability"]["premise"])
        self.assertIn("claim_source_changed", decision["applicability"]["reasons"])
        self.assertIs(references["semantics_checked"], False)
        self.assertIs(references["execution_authorized"], False)

    def test_native_claim_with_ungranted_e_row_never_loads_sources_or_bridges(self) -> None:
        from whykit import artifact_store

        root = self.base / "provenance"
        shutil.copytree(ROOT / "examples/provenance", root, ignore=shutil.ignore_patterns(".whykit"))
        full = VaultIndex.load(root)
        active, retired, _, _ = classified_evidence(full)
        grant = full.subset(lambda note: note.path.name != "evidence-register.md")
        grant.derived["evidence_register"] = ({eid: row for eid, row in active.items() if eid != "E-004"},
                                               {eid: row for eid, row in retired.items() if eid != "E-004"}, [])
        original = artifact_store._read_regular
        def no_denied_capture(path, **options):
            self.assertFalse({"artifacts", "observations"} & set(Path(path).parts), path)
            return original(path, **options)
        with patch.object(VaultIndex, "load", side_effect=AssertionError("no broader grant")), \
                patch.object(artifact_store, "_read_regular", side_effect=no_denied_capture):
            report = hybrid.retrieve_hybrid(root, "claims", valid_on=DAY, allowed_sensitivities=ALL, vault=grant)
        self.assertFalse(any(row.get("claim_id") == "C-001" or row["path"].startswith("00-context/claims/")
                             for row in report["results"]))
        self.assertNotIn("E-004", {citation.get("evidence_id") for citation in report["citations"]})
        for row in report["results"]:
            if row.get("decision_id") == "D-003":
                self.assertFalse(row["applicability"]["premise"])

    def test_dependency_denied_native_claim_cannot_consume_construction_budget(self) -> None:
        before = VaultIndex.load(self.root).subset(lambda _note: True)
        with patch.object(hybrid, "MAX_CONSTRUCTION_NODES", 2):
            expected = self.rank(before, allowed_sensitivities=ALL)
            destination = self.root / "00-context/claims/c-001.md"
            destination.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples/provenance/00-context/claims/c-001.md", destination)
            captured = VaultIndex.load(self.root).subset(lambda _note: True)
            # The claim path is captured, but its E-004 row is outside this grant.
            actual = self.rank(captured, rows=({}, {}, []), allowed_sensitivities=ALL)
        self.assertEqual(actual, expected)

    def test_prepared_captured_A_ignores_disk_B_and_performs_no_file_io(self) -> None:
        index = VaultIndex.load(self.root).subset(lambda _note: True)
        expected = self.rank(index)
        write_note(self.root, "seed.md", "DIFFERENT_DISK_B [[ungranted]]")
        write_note(self.root, "ungranted.md", "needle needle [[answer]]")
        before = tree(self.root)
        with patch.object(VaultIndex, "load", side_effect=AssertionError("no recapture")), \
                patch.object(Path, "open", side_effect=AssertionError("no file open")), \
                patch.object(Path, "read_text", side_effect=AssertionError("no read_text")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("no read_bytes")), \
                patch.object(builtins, "open", side_effect=AssertionError("no builtin open")), \
                patch.object(os, "open", side_effect=AssertionError("no os.open")):
            actual = self.rank(index)
        self.assertEqual(actual, expected)
        self.assertEqual(tree(self.root), before)
        self.assertNotIn("ungranted", json.dumps(actual))

    def test_public_and_prepared_requests_do_not_inherit_persistent_cache_writes(self) -> None:
        index = VaultIndex.load(self.root).subset(lambda _note: True)
        before = tree(self.root)
        with persistent(True):
            self.retrieve()
            self.rank(index)
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.root / ".whykit/cache").exists())

    def test_half_open_and_unknown_validity_filters_apply_before_statistics(self) -> None:
        for name, fields in (("future", {"valid_from": "2026-09-18"}),
                             ("expired", {"valid_until": DAY.isoformat()}),
                             ("invalid", {"valid_from": "bad-date"})):
            with self.subTest(name=name):
                baseline = self.retrieve()
                path = write_note(self.root, name + ".md", "needle " * 100 + " [[answer]]", **fields)
                self.assertEqual(self.retrieve(), baseline)
                path.unlink()
        self.assertTrue(self.retrieve()["results"])
        strict = self.retrieve(validity="active")
        self.assertEqual(strict["results"], [])
        write_note(self.root, "active.md", "needle", valid_from=DAY.isoformat(), valid_until="2026-09-18")
        strict = self.retrieve(validity="active")
        self.assertEqual({r["path"] for r in strict["results"]}, {"active.md"})

    def test_native_receipt_then_review_request_controls_current_premise(self) -> None:
        root = self.approval_root()
        identity, path = self.native_decision(root, "needle approval choice")
        def retrieve(**kwargs):
            return hybrid.retrieve_hybrid(root, "needle", valid_on=DAY, **kwargs)
        baseline = retrieve()
        result = next(r for r in baseline["results"] if r["path"] == path.relative_to(root).as_posix())
        self.assertTrue(result["applicability"]["premise"])
        record_review(root, identity, reviewer="Ada Example", outcome="update-required", note_text="Recheck the premise", today=DAY)
        self.assertNotIn(path.relative_to(root).as_posix(), {r["path"] for r in retrieve()["results"]})
        historical = retrieve(decision_role="reference")
        for result in historical["results"]:
            if result["path"] == path.relative_to(root).as_posix():
                self.assertFalse(result["applicability"]["premise"])

    def test_true_future_and_expired_native_approvals_never_become_premises(self) -> None:
        root = self.approval_root()
        for label, fields in (("future", {"valid_from": "2026-09-18"}),
                              ("expired", {"valid_until": DAY.isoformat()})):
            self.native_decision(root, "needle " + label, **fields)
        report = hybrid.retrieve_hybrid(root, "needle", valid_on=DAY)
        self.assertFalse(any(r.get("type") == "decision" for r in report["results"]))

    def test_physical_global_log_folder_case_and_aliases_never_enter_corpus_or_hashes(self) -> None:
        root = self.approval_root()
        identity, _ = create_decision(root, "privatenativetitlerestrictedcanary", owner="Ada Example", sensitivity="restricted", source_ids=["E-001"], today=DAY)
        record_review(root, identity, reviewer="privatereviewerrestrictedcanary", outcome="update-required", note_text="privatereviewnoterestrictedcanary", today=DAY)
        for i, name in enumerate(LOGS):
            p = root / name
            p.write_text(_frontmatter_replace(p.read_text(encoding="utf-8"), "aliases", json.dumps([("alphagloballedgeraliascanary", "betagloballedgeraliascanary")[i]])), encoding="utf-8")
        for folder in ("00-context", "06-decisions"):
            directory = root / folder
            for name in ("review-log.md", "decision-log.md"):
                if (directory / name).exists():
                    (directory / name).rename(directory / name.upper())
            temporary = root / (folder + "-temporary-rename")
            directory.rename(temporary)
            temporary.rename(root / folder.upper())
        observed = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name.casefold() in {"review-log.md", "decision-log.md"}}
        self.assertEqual(set(observed), {"00-CONTEXT/REVIEW-LOG.MD", "06-DECISIONS/DECISION-LOG.MD"})
        original = hashlib.sha256
        def no_raw_log(raw=b"", *args, **kwargs):
            self.assertNotIn(raw, observed.values())
            return original(raw, *args, **kwargs)
        with patch.object(hashlib, "sha256", side_effect=no_raw_log):
            for text in ("privatenativetitlerestrictedcanary", "privatereviewerrestrictedcanary", "privatereviewnoterestrictedcanary", "alphagloballedgeraliascanary", "betagloballedgeraliascanary"):
                report = hybrid.retrieve_hybrid(root, text, valid_on=DAY)
                self.assertEqual(report["results"], [], text)

    def test_no_lexical_seeds_never_dump_graph_and_global_edge_work_is_bounded(self) -> None:
        for query in ("", "!!!", "NONEXISTENTVOCABULARY"):
            self.assertEqual(self.retrieve(query)["results"], [])
        for i in range(12):
            write_note(self.root, f"fan-{i:02d}.md", "needle [[seed]] [[answer]]")
        report = self.retrieve(seed_limit=8, max_edges=3)
        self.assertTrue(any(report["truncation"].values()))
        stats = report["stats"]
        examined = stats.get("examined_edges", stats.get("visited_edges"))
        self.assertIsNotNone(examined, "report must state global examined-edge work")
        self.assertLessEqual(examined, 3)
        self.assertEqual(report, self.retrieve(seed_limit=8, max_edges=3))

    def test_diamond_and_cycles_do_not_multiply_graph_support(self) -> None:
        write_note(self.root, "seed.md", "needle [[left]]")
        write_note(self.root, "left.md", "[[target]]")
        write_note(self.root, "right.md", "[[target]]")
        write_note(self.root, "target.md", "Useful destination")
        first = self.retrieve()
        initial = next(r for r in first["results"] if r["path"] == "target.md")
        write_note(self.root, "seed.md", "needle [[left]] [[right]]")
        write_note(self.root, "left.md", "[[target]] [[right]]")
        write_note(self.root, "right.md", "[[target]] [[left]] [[seed]]")
        second = self.retrieve()
        diamond = next(r for r in second["results"] if r["path"] == "target.md")
        self.assertEqual(diamond["graph_support"], initial["graph_support"])
        self.assertEqual(diamond["score"], initial["score"])
        self.assertEqual(len(diamond["explanation"]["nodes"]), len(set(diamond["explanation"]["nodes"])))
        self.assertEqual(second, self.retrieve())

    def test_zero_depth_lexical_ablation_never_materializes_the_graph(self) -> None:
        with patch.object(dependency_graph, "document_node", side_effect=AssertionError("C ablation must not construct graph")):
            report = self.retrieve(max_depth=0)
        self.assertTrue(report["results"])
        self.assertTrue(all(row["graph_support"] == 0 and row["explanation"] is None for row in report["results"]))

    def test_construction_limits_refuse_before_graph_document_materialization(self) -> None:
        for constant, maximum in (("MAX_CONSTRUCTION_NODES", 1), ("MAX_VISIBLE_BYTES", 1), ("MAX_TOKENS", 1)):
            with self.subTest(constant=constant), patch.object(hybrid, constant, maximum), \
                    patch.object(dependency_graph, "document_node", side_effect=AssertionError("must refuse before graph materialization")):
                report = self.retrieve()
                self.assertEqual(report["status"], "refused")
                self.assertEqual(report["reason"], "scope_too_large")
                self.assertEqual(report["results"], [])
                self.assertIsNone(report["stats"])
        write_note(self.root, "second.md", "Other material")
        write_note(self.root, "seed.md", "needle [[answer]] [[second]]")
        with patch.object(hybrid, "MAX_CONSTRUCTED_EDGES", 1):
            report = self.retrieve()
            self.assertEqual(report["status"], "refused")
            self.assertEqual(report["results"], [])

    def test_query_byte_budget_rejects_multibyte_overflow_before_discovery(self) -> None:
        with patch.object(VaultIndex, "load", side_effect=AssertionError("validate bytes before discovery")), \
                self.assertRaises(hybrid.HybridRetrievalError) as raised:
            self.retrieve("ą" * 2049)
        self.assertEqual(raised.exception.code, "invalid_inputs")

    def test_invalid_parameters_fail_fixed_and_never_discover_a_vault(self) -> None:
        cases = ({"valid_on": "2026-09-17"}, {"valid_on": dt.datetime(2026, 9, 17)},
                 {"validity": "all"}, {"validity": []}, {"decision_role": "all"}, {"decision_role": {}}, {"max_depth": True},
                 {"max_depth": 3}, {"max_edges": 2049}, {"seed_limit": 17},
                 {"max_nodes": 257}, {"allowed_sensitivities": {"UNKNOWN"}},
                 {"allowed_sensitivities": ["public"]}, {"limit": True})
        for options in cases:
            with self.subTest(options=options), patch.object(VaultIndex, "load", side_effect=AssertionError("validate before discovery")), \
                    self.assertRaises(hybrid.HybridRetrievalError) as raised:
                self.retrieve(**options)
            self.assertEqual(raised.exception.code, "invalid_inputs")
