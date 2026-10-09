"""Whole-workflow regressions from the one final F1 review."""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _claims import TODAY, approved_claim_vault, claim_vault, read_view, source_snapshot
from _jsonschema import validate
from test_content_quality import FILLED
from whykit import cli, immutability, review
from whykit.claim_review import decision_claim_review_current
from whykit.claims import evaluate_claims
from whykit.config import ConfigError
from whykit.graph import build_graph
from whykit.lint import load_note
from whykit.mcp_server import ToolFailure, VaultTools, output_schema
from whykit.pack import _markdown, build_pack
from whykit.placeholders import SCAFFOLD_EVIDENCE_TODO
from whykit.query import query_vault
from whykit.scaffold import _frontmatter_replace, create_claim, create_decision, create_evidence
from whykit.status import build_status
from whykit.trace import build_trace

ROOT = Path(__file__).resolve().parents[1]


class ClaimFinalReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.cid, self.cp, self.did, self.dp = approved_claim_vault(self.root)

    def approve(self, target, *, day=TODAY):
        kw = dict(reviewer="Ada Example", today=day)
        preview = review.approve_record(self.root, target, **kw)
        return review.approve_record(self.root, target, write=True,
                                    expected_sha256=preview["expected_sha256"], **kw)

    def record(self, target, outcome, *, day=TODAY):
        kw = dict(reviewer="Ada Example", outcome=outcome, today=day,
                  note_text="Observed change requires a fresh assessment.")
        preview = review.record_review(self.root, target, **kw)
        return review.record_review(self.root, target, write=True,
                                   expected_sha256=preview["expected_sha256"], **kw)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       text=True, encoding="utf-8").strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=MaciejZet", "-c", "user.email=maciekzmitruk@protonmail.com",
                 "commit", "-qm", "Synthetic review fixture")
        return self.git("rev-parse", "HEAD")

    def baseline(self):
        self.git("init", "-q")
        return self.commit()

    def decision(self, *, day=TODAY):
        did, path = create_decision(self.root, "Consider observations", owner="Ada Example",
                                    sensitivity="public", claim_ids=[self.cid], today=day)
        text = path.read_text(encoding="utf-8")
        for before, after in FILLED.items():
            text = text.replace(before, after)
        text = text.replace(SCAFFOLD_EVIDENCE_TODO, "- C-001").replace("(E-001)", "(C-001)")
        path.write_text(text + "\n## Claim assessment\n\n- C-001: The conflict is limited to the stated scope.\n", encoding="utf-8")
        return did, path

    def schema(self, name):
        return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))

    def test_filtered_query_and_pack_withhold_whole_dependency(self):
        original = self.cp.read_bytes()
        register = self.root / "00-context/evidence-register.md"
        original_register = register.read_text(encoding="utf-8")
        for kind in ("claim", "evidence"):
            with self.subTest(kind=kind):
                self.cp.write_bytes(original)
                text = original_register
                if kind == "claim":
                    self.cp.write_text(_frontmatter_replace(self.cp.read_text(encoding="utf-8"), "sensitivity", "restricted"), encoding="utf-8")
                else:
                    text = "\n".join(line.replace("public", "restricted") if line.startswith("| E-002 |") else line for line in text.splitlines()) + "\n"
                register.write_text(text, encoding="utf-8")
                query = query_vault(self.root, doc_type="claim", sensitivity="public")
                pack = build_pack(self.root, targets=[self.did], allowed_sensitivities={"public"})
                self.assertEqual(query["returned"], 0)
                self.assertEqual(pack["contexts"], [])
                payload = json.dumps([query, pack])
                for sentinel in (self.cid, self.cp.name, "Offline record reads fail", *[p.stem for p in (self.root / "00-context/claim-snapshots").glob("*.txt")]):
                    self.assertNotIn(sentinel, payload)

    def test_regular_cli_preserves_claim_ids_and_rejects_unknown_claim(self):
        for cid, expected in ((self.cid, 0), ("C-999", 2)):
            with self.subTest(cid=cid), contextlib.redirect_stdout(io.StringIO()) as out:
                result = cli.main(["new", "--root", str(self.root), "decision", "CLI decision", "--claim", cid, "--owner", "Ada Example", "--json"])
                self.assertEqual(result, expected)
                if result == 0:
                    front = load_note(self.root / json.loads(out.getvalue())["path"]).front
                    self.assertEqual(front.get("claim_ids"), [self.cid])

    def test_overlapping_approval_captures_cannot_be_mixed(self):
        did, _ = self.decision()
        register = self.root / "00-context/evidence-register.md"
        original = review._approval_plan
        def mutate(*args, **kwargs):
            register.write_text(register.read_text(encoding="utf-8").replace("Example observation", "Changed observation"), encoding="utf-8")
            return original(*args, **kwargs)
        with patch("whykit.review._approval_plan", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed|capture"):
                review.approve_record(self.root, did, reviewer="Ada Example", today=TODAY)

    def test_unresolved_third_relation_blocks_decision_approval(self):
        other = self.root.parent / "third"
        path = claim_vault(other)
        config = other / "whykit.toml"
        config.write_text(config.read_text(encoding="utf-8") + "\n[evidence_access_age_days]\nreport = 1\n", encoding="utf-8")
        eid = create_evidence(other, source="Additional observation", kind="report", location="https://source.example/third", claims="Offline reads", today=TODAY-dt.timedelta(days=1), sensitivity="public")
        register = other / "00-context/evidence-register.md"
        register.write_text(_frontmatter_replace(register.read_text(encoding="utf-8"), "last_updated", TODAY.isoformat()), encoding="utf-8")
        snapshot, digest = source_snapshot(other, b"Additional partial support.\n")
        path.write_text(path.read_text(encoding="utf-8") + f"| {eid} | supports | {snapshot} | {digest} | lines:1-1 | 2026-10-08 | Additional observation |\n", encoding="utf-8")
        self.root, self.cp = other, path
        self.approve(self.cid)
        day = TODAY + dt.timedelta(days=1)
        assessment = evaluate_claims(read_view(other), today=day)[self.cid]
        self.assertEqual(assessment["verification_status"], "disputed")
        self.assertTrue(assessment["binding_valid"])
        self.assertFalse(assessment["relations"][-1]["usable"])
        did, _ = self.decision(day=day)
        with self.assertRaisesRegex(ValueError, "resolved|usable"):
            review.approve_record(other, did, reviewer="Ada Example", today=day)

    def test_negative_review_invalidates_claim_and_dependent_decision(self):
        for outcome in ("update-required", "supersede-required", "archived"):
            with self.subTest(outcome=outcome):
                self.record(self.cid, outcome)
                view = read_view(self.root)
                self.assertEqual(evaluate_claims(view, today=TODAY)[self.cid]["verification_status"], "unknown")
                queue = review.review_queue(self.root, today=TODAY)
                self.assertIn(self.did, {row.get("decision_id") for row in queue})
                if outcome != "archived":
                    self.assertIn(self.cid, {row.get("claim_id") for row in queue})
                else:
                    self.assertEqual(load_note(self.cp).front["status"], "archived")
                self.assertFalse(decision_claim_review_current(view, load_note(self.dp), today=TODAY))

    def test_negative_decision_review_invalidates_decision_binding(self):
        self.record(self.did, "update-required")
        self.assertFalse(decision_claim_review_current(read_view(self.root), load_note(self.dp), today=TODAY))
        self.assertIn(self.did, {row.get("decision_id") for row in review.review_queue(self.root, today=TODAY)})

    def test_history_checks_replay_and_all_lifecycle_envelope_changes(self):
        base = self.baseline()
        log = self.root / "00-context/review-log.md"
        row = next(line for line in log.read_text(encoding="utf-8").splitlines() if "claim-receipt/v1:" in line and "decision-claim-receipt" not in line)
        log.write_text(log.read_text(encoding="utf-8") + row + "\n", encoding="utf-8")
        self.commit()
        with self.subTest(kind="replay"):
            self.assertTrue(immutability.claim_history_findings(base, "HEAD", root=str(self.root)))
        self.git("reset", "--hard", base)
        self.cp.write_text(_frontmatter_replace(self.cp.read_text(encoding="utf-8"), "superseded_by", "C-999"), encoding="utf-8")
        self.commit()
        with self.subTest(kind="dangling successor"):
            self.assertTrue(immutability.claim_history_findings(base, "HEAD", root=str(self.root)))
        self.git("reset", "--hard", base)
        text = _frontmatter_replace(self.cp.read_text(encoding="utf-8"), "status", "archived")
        self.cp.write_text(_frontmatter_replace(text, "last_verified", "2020-01-01"), encoding="utf-8")
        self.commit()
        with self.subTest(kind="unreviewed envelope"):
            self.assertTrue(immutability.claim_history_findings(base, "HEAD", root=str(self.root)))

    def test_history_keeps_lone_cr_significant(self):
        base = self.baseline()
        path = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r"))
        self.commit()
        self.assertTrue(immutability.claim_history_findings(base, "HEAD", root=str(self.root)))
        self.assertTrue(immutability.changed_records(base, "HEAD", str(self.root)))

    def test_two_real_supersessions_pass_trusted_history(self):
        base = self.baseline()
        prior = self.cid
        for n in (2, 3):
            cid, path = create_claim(self.root, f"Revision {n}", statement=f"Version {n} observation", scope=f"Desktop v{n}", valid_from="2026-10-01", supersedes=prior, today=TODAY, owner="Ada Example")
            path.write_text(path.read_text(encoding="utf-8").split("## Evidence")[0] + "## Evidence" + self.cp.read_text(encoding="utf-8").split("## Evidence")[1], encoding="utf-8")
            self.approve(cid)
            self.commit()
            prior = cid
        self.assertEqual(immutability.claim_history_findings(base, "HEAD", root=str(self.root)), [])

    def test_negative_payloads_validate_all_v2_contracts(self):
        next((self.root / "00-context/claim-snapshots").glob("*.txt")).unlink()
        reports = [(build_status(self.root, today=TODAY), self.schema("status-report-v2.schema.json")),
                   (VaultTools(self.root, max_sensitivity="restricted").status(today=TODAY.isoformat()), output_schema("status", contract_version=2)),
                   ({"contract_version": 2, "due_days": 30, "count": len(review.review_queue(self.root, today=TODAY)), "reviews": review.review_queue(self.root, today=TODAY)}, self.schema("review-queue-v2.schema.json"))]
        for payload, schema in reports:
            with self.subTest(schema=schema.get("title")):
                self.assertEqual(validate(payload, schema), [])
        base = self.baseline()
        self.cp.write_text(_frontmatter_replace(self.cp.read_text(encoding="utf-8"), "statement", "Changed accepted claim"), encoding="utf-8")
        self.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(immutability.main(["--base", base, "--root", str(self.root), "--json"]), 1)
        with self.subTest(schema="history"):
            self.assertEqual(validate(json.loads(out.getvalue()), self.schema("history-report-v2.schema.json")), [])
        from whykit.check import run_check
        with self.subTest(schema="check"):
            self.assertEqual(validate(run_check(self.root, profile_name="ci", base=base, today=TODAY), self.schema("check-report-v2.schema.json")), [])

    def test_versions_come_from_explicit_config_not_hidden_claims(self):
        config = self.root / "whykit.toml"
        config.write_text(config.read_text(encoding="utf-8").split("\n[claims]")[0], encoding="utf-8")
        self.cp.write_text(_frontmatter_replace(self.cp.read_text(encoding="utf-8"), "sensitivity", "restricted"), encoding="utf-8")
        missing = self.root.parent / "twin"
        shutil.copytree(self.root, missing)
        (missing / self.cp.relative_to(self.root)).unlink()
        (missing / self.dp.relative_to(self.root)).unlink()
        hidden = VaultTools(self.root, max_sensitivity="public")
        twin = VaultTools(missing, max_sensitivity="public")
        with self.subTest(kind="hidden twin"):
            self.assertEqual(hidden.context(self.cid), twin.context(self.cid))
            self.assertEqual(hidden.context(self.cid)["contract_version"], 1)
        config.write_text(config.read_text(encoding="utf-8") + "\n[claims]\nformat_version = 2\n", encoding="utf-8")
        self.cp.unlink()
        self.dp.unlink()
        with self.assertRaises(ConfigError):
            build_graph(self.root)

    def test_enabled_missing_targets_keep_v2(self):
        tools = VaultTools(self.root, max_sensitivity="restricted")
        for name in ("context", "impact", "backlinks"):
            with self.subTest(tool=name):
                result = getattr(tools, name)("E-999")
                self.assertFalse(result["exists"])
                self.assertEqual(result["contract_version"], 2)
                self.assertEqual(validate(result, output_schema(name, contract_version=2)), [])

    def test_trace_gap_filters_match_final_claim_assessment(self):
        tools = VaultTools(self.root, max_sensitivity="restricted")
        self.assertEqual(tools.trace(today=TODAY.isoformat(), gaps_only=True)["matched"], 0)
        self.dp.write_text(self.dp.read_text(encoding="utf-8").replace("source_ids: []", "source_ids: [E-001]"), encoding="utf-8")
        report = build_trace(self.root, today=TODAY)
        self.assertTrue(report["decisions"][0]["claim_gaps"])
        self.assertEqual(tools.trace(today=TODAY.isoformat(), gaps_only=True)["matched"], 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["trace", "--root", str(self.root), "--today", TODAY.isoformat(), "--gaps-only", "--json"])
        self.assertEqual(len(json.loads(out.getvalue())["decisions"]), 1)

    def test_filtered_graph_and_bounded_pack_report_omitted_provenance(self):
        self.dp.write_text(_frontmatter_replace(self.dp.read_text(encoding="utf-8"), "source_of_truth", "true"), encoding="utf-8")
        graph = build_graph(self.root, canonical_only=True)
        with self.subTest(kind="canonical graph"):
            self.assertTrue(any(row["target"] == self.cid and row["reason"] == "filtered" for row in graph["unresolved"]))
        pack = build_pack(self.root, targets=[self.did], max_docs=1)
        self.assertEqual(len(pack["contexts"]), 1)
        self.assertIn({"target": self.cid, "origin": "claim", "reason": "max_docs"}, pack["missing"])
        self.assertTrue(pack["budget"]["exhausted"])

    def test_evidence_change_invalidates_claim_query_cursor(self):
        tools = VaultTools(self.root, max_sensitivity="restricted")
        first = tools.query(limit=1)
        self.assertTrue(first["next_cursor"])
        register = self.root / "00-context/evidence-register.md"
        register.write_text(register.read_text(encoding="utf-8").replace("Example observation", "New observation"), encoding="utf-8")
        with self.assertRaises(ToolFailure):
            tools.query(limit=1, cursor=first["next_cursor"])

    def test_mcp_public_selection_keeps_dependency_floor_inside_confined_view(self):
        register = self.root / "00-context/evidence-register.md"
        text = "\n".join(line.replace("public", "restricted") if line.startswith("| E-002 |") else line for line in register.read_text(encoding="utf-8").splitlines()) + "\n"
        register.write_text(text, encoding="utf-8")
        report = query_vault(self.root, doc_type="claim", sensitivity="public",
                             vault=VaultTools(self.root, max_sensitivity="restricted").visible_index())
        self.assertEqual(report["results"], [])

    def test_unsupported_claim_config_is_not_silently_ignored_with_toml_header_variants(self):
        self.cp.unlink()
        self.dp.unlink()
        path = self.root / "whykit.toml"
        original = path.read_text(encoding="utf-8").split("\n[claims]")[0]
        for header in ('[ claims ]', '["claims"]'):
            with self.subTest(header=header):
                path.write_text(original + f"\n{header}\nformat_version = 2\n", encoding="utf-8")
                with self.assertRaises(ConfigError):
                    build_graph(self.root)

    def test_human_trace_and_markdown_pack_show_conflict_and_fragments(self):
        text = _markdown(build_pack(self.root, targets=[self.did]))
        for value in ("disputed", "supports", "contradicts", "Offline record reads fail"):
            with self.subTest(format="markdown", value=value):
                self.assertIn(value, text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["trace", "--root", str(self.root), "--today", TODAY.isoformat()])
        self.assertIn("disputed", out.getvalue())
