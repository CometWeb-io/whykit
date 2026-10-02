"""In-process coverage for the read/report commands and the review workflow.

These call each command's `main(argv)` directly instead of spawning the CLI, so
they stay fast enough to exercise every exit-code and output branch. The CLI
dispatch itself is covered by the subprocess suites.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whykit import backlinks, check, context, impact, pack, review, snapshot  # noqa: E402
from whykit.evidence import retire_evidence  # noqa: E402
from whykit.lint import lint  # noqa: E402
from whykit.review import record_review, review_queue  # noqa: E402
from whykit.scaffold import create_decision, create_evidence  # noqa: E402
from _vaults import fresh_vault  # noqa: E402


def call(main, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def front(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}: (.*)$", text.split("\n---\n", 1)[0])
    return match.group(1) if match else None


class LedgerFixture(unittest.TestCase):
    """A vault with evidence, a superseded decision and a retired source.

    Built once per class and copied per test. Every record is dated from the
    day the template vault was stamped, never from a fixed calendar date, so
    `created` can never land after `last_updated` as the calendar moves on.

    E-001 retired, replaced by E-002 (cited by D-001 and Home.md)
    E-002 active (cited by D-002)
    D-001 approved, superseded by D-002
    D-002 approved, supersedes D-001
    D-003 draft
    """

    _template: Path
    _tmp: tempfile.TemporaryDirectory
    day: dt.date

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._template = Path(cls._tmp.name) / "template"
        cls.day = fresh_vault(cls._template)
        vault, day = cls._template, cls.day
        review_by = (day + dt.timedelta(days=60)).isoformat()
        for source, location in (("First survey", "https://example.com/one"), ("Second survey", "https://example.com/two")):
            create_evidence(vault, source=source, location=location, kind="survey", claims=f"{source} claim", today=day)
        create_decision(vault, "Adopt the ledger", owner="Research", status="approved",
                        source_ids=["E-001"], review_by=review_by, today=day)
        create_decision(vault, "Adopt the ledger everywhere", owner="Research", status="approved",
                        source_ids=["E-002"], review_by=review_by, supersedes="D-001", today=day)
        create_decision(vault, "Maybe archive old notes", owner="Ops", today=day)
        retire_evidence(vault, "E-001", reason="Superseded survey", replaced_by="E-002", today=day)
        home = vault / "Home.md"
        home.write_text(home.read_text(encoding="utf-8") + "\nSee [[06-decisions/d-002-adopt-the-ledger-everywhere]] (E-001).\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        shutil.copytree(self._template, self.vault, symlinks=True)
        self.vault = self.vault.resolve()
        self.d1 = next((self.vault / "06-decisions").glob("d-001-*.md"))
        self.d2 = next((self.vault / "06-decisions").glob("d-002-*.md"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def errors(self) -> list[str]:
        _, findings = lint(self.vault, today=self.day)
        return [f"{item.path}: [{item.code}] {item.message}" for item in findings if item.level == "error"]


class FixtureSanityTests(LedgerFixture):
    def test_fixture_vault_lints_without_errors(self) -> None:
        self.assertEqual(self.errors(), [])


class ReviewTests(LedgerFixture):
    def test_review_dated_before_the_record_existed_is_refused_and_writes_nothing(self) -> None:
        log = self.vault / "00-context/review-log.md"
        before = {path: path.read_bytes() for path in (self.d2, log)}
        with self.assertRaisesRegex(ValueError, "before .* was created"):
            record_review(self.vault, "D-002", reviewer="Research", outcome="confirmed",
                          today=self.day - dt.timedelta(days=1))
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_back_dated_review_never_moves_last_updated_backwards(self) -> None:
        # The record existed ten days before the review and was edited after it;
        # recording the review late must not rewind either file's last_updated.
        created = self.day - dt.timedelta(days=10)
        reviewed = self.day - dt.timedelta(days=5)
        text = self.d2.read_text(encoding="utf-8").replace(f"created: {self.day.isoformat()}", f"created: {created.isoformat()}", 1)
        self.d2.write_text(text, encoding="utf-8")
        result = record_review(self.vault, "D-002", reviewer="Research", outcome="confirmed",
                               next_review=(self.day + dt.timedelta(days=30)).isoformat(), today=reviewed)
        self.assertEqual(result["date"], reviewed.isoformat())
        self.assertEqual(front(self.d2.read_text(encoding="utf-8"), "last_updated"), self.day.isoformat())
        log = (self.vault / "00-context/review-log.md").read_text(encoding="utf-8")
        self.assertEqual(front(log, "last_updated"), self.day.isoformat())
        self.assertIn(f"| {reviewed.isoformat()} | [[06-decisions/{self.d2.stem}]] |", log)
        self.assertEqual(self.errors(), [])

    def test_confirmed_review_without_next_date_uses_the_policy_interval(self) -> None:
        later = self.day + dt.timedelta(days=3)
        result = record_review(self.vault, "D-002", reviewer="Research", outcome="confirmed", today=later)
        expected = (later + dt.timedelta(days=90)).isoformat()
        self.assertEqual(result["next_review"], expected)
        self.assertEqual(result["decision_id"], "D-002")
        self.assertEqual(front(self.d2.read_text(encoding="utf-8"), "review_by"), expected)
        self.assertEqual(front(self.d2.read_text(encoding="utf-8"), "last_updated"), later.isoformat())

    def test_non_confirming_outcome_logs_the_event_without_touching_the_record(self) -> None:
        before = self.d2.read_bytes()
        result = record_review(self.vault, "D-002", reviewer="Research", outcome="update-required",
                               note_text="Pricing | changed", today=self.day)
        self.assertIsNone(result["next_review"])
        self.assertEqual(self.d2.read_bytes(), before)
        log = (self.vault / "00-context/review-log.md").read_text(encoding="utf-8")
        self.assertIn(r"Pricing \| changed", log)

    def test_documents_can_be_reviewed_by_path(self) -> None:
        result = record_review(self.vault, "Home", reviewer="Research", outcome="archived", today=self.day)
        self.assertEqual(result["target"], "Home.md")
        self.assertIsNone(result["decision_id"])

    def test_invalid_review_requests_are_rejected(self) -> None:
        cases = {
            "placeholder reviewer": dict(target="D-002", reviewer="TODO"),
            "blank reviewer": dict(target="D-002", reviewer="  "),
            "unknown outcome": dict(target="D-002", outcome="approved"),
            "unparseable next review": dict(target="D-002", next_review="2026-02-30"),
            "next review not after review": dict(target="D-002", next_review="SAME_DAY"),
            "missing target": dict(target="D-999"),
            "missing document": dict(target="no-such-note"),
            "review log reviewing itself": dict(target="00-context/review-log.md"),
            "confirming a draft": dict(target="D-003"),
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                kwargs = {"reviewer": "Research", "outcome": "confirmed", "today": self.day} | overrides
                if kwargs.get("next_review") == "SAME_DAY":
                    kwargs["next_review"] = self.day.isoformat()
                target = kwargs.pop("target")
                with self.assertRaises(ValueError):
                    record_review(self.vault, target, **kwargs)
        self.assertNotIn("| Research |", (self.vault / "00-context/review-log.md").read_text(encoding="utf-8"))

    def test_review_log_is_recreated_from_the_template_when_missing(self) -> None:
        (self.vault / "00-context/review-log.md").unlink()
        record_review(self.vault, "D-002", reviewer="Research", outcome="archived", today=self.day)
        log = (self.vault / "00-context/review-log.md").read_text(encoding="utf-8")
        self.assertIn("| Date | Target | Reviewer |", log)
        self.assertEqual(front(log, "created"), self.day.isoformat())

    def test_malformed_review_log_is_refused(self) -> None:
        (self.vault / "00-context/review-log.md").write_text("---\ntitle: x\n---\n# no table\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            record_review(self.vault, "D-002", reviewer="Research", outcome="archived", today=self.day)

    def test_queue_filters_by_owner_and_overdue_state(self) -> None:
        far = self.day + dt.timedelta(days=365)
        queue = review_queue(self.vault, today=far, due_days=0)
        self.assertTrue(queue)
        self.assertTrue(all(item["state"] == "overdue" for item in review_queue(self.vault, today=far, overdue_only=True)))
        self.assertEqual(review_queue(self.vault, today=far, owner="nobody-owns-this"), [])
        self.assertEqual(review_queue(self.vault, today=self.day, due_days=0, overdue_only=True), [])

    def test_cli_list_and_record_report_exit_codes(self) -> None:
        root = str(self.vault)
        far = (self.day + dt.timedelta(days=365)).isoformat()
        code, out, _ = call(review.main, "--root", root, "list", "--today", far, "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["count"], len(payload["reviews"]))
        code, out, _ = call(review.main, "--root", root, "list", "--today", far)
        self.assertEqual(code, 0)
        self.assertIn("OVERDUE", out)
        self.assertEqual(call(review.main, "--root", root, "list", "--today", "soon")[0], 2)
        self.assertEqual(call(review.main, "--root", root, "list", "--due-days", "-1")[0], 2)
        self.assertEqual(call(review.main, "--root", root, "record", "D-002", "--reviewer", "R", "--today", "x")[0], 2)
        code, _, err = call(review.main, "--root", root, "record", "D-999", "--reviewer", "R")
        self.assertEqual(code, 2)
        self.assertIn("missing", err)
        code, out, _ = call(review.main, "--root", root, "record", "D-002", "--reviewer", "R", "--today", self.day.isoformat())
        self.assertEqual(code, 0)
        self.assertIn("next review:", out)
        code, out, _ = call(review.main, "--root", root, "record", "D-002", "--reviewer", "R",
                            "--outcome", "archived", "--today", self.day.isoformat(), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["outcome"], "archived")
        self.assertEqual(call(review.main, "--root", str(self.vault.parent), "list")[0], 2)


class ImpactTests(LedgerFixture):
    def test_retired_evidence_reports_its_replacement_and_citations(self) -> None:
        report = impact.analyze_impact(self.vault, "E-001")
        self.assertEqual((report["state"], report["replacement"]), ("retired", "E-002"))
        paths = [item["path"] for item in report["references"]]
        self.assertIn("Home.md", paths)
        self.assertIn(self.d1.relative_to(self.vault).as_posix(), paths)
        self.assertNotIn("00-context/evidence-register.md", paths)

    def test_decision_reports_supersession_evidence_and_backlinks(self) -> None:
        report = impact.analyze_impact(self.vault, "D-002")
        self.assertTrue(report["exists"])
        self.assertEqual(report["supersedes"], "D-001")
        self.assertEqual(report["evidence"], ["E-002"])
        self.assertIn("Home", [item["id"] for item in report["incoming"]])
        predecessor = impact.analyze_impact(self.vault, "D-001")
        self.assertEqual([item["decision_id"] for item in predecessor["superseded_by"]], ["D-002"])

    def test_unknown_targets_do_not_exist(self) -> None:
        for target, kind in (("E-404", "evidence"), ("D-404", "decision"), ("missing-note", "document")):
            with self.subTest(target):
                report = impact.analyze_impact(self.vault, target)
                self.assertFalse(report["exists"])
                self.assertEqual(report["kind"], kind)

    def test_cli_prints_every_kind_and_exits_nonzero_for_missing(self) -> None:
        root = str(self.vault)
        code, out, _ = call(impact.main, "E-001", "--root", root)
        self.assertEqual(code, 0)
        self.assertIn("replacement    E-002", out)
        code, out, _ = call(impact.main, "D-001", "--root", root)
        self.assertEqual(code, 0)
        self.assertIn("superseded by", out)
        code, out, _ = call(impact.main, "D-002", "--root", root)
        self.assertIn("supersedes     D-001", out)
        self.assertIn("referenced by", out)
        code, out, _ = call(impact.main, "Home", "--root", root)
        self.assertEqual(code, 0)
        self.assertIn("links to", out)
        code, out, _ = call(impact.main, "E-404", "--root", root, "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["exists"])
        code, out, _ = call(impact.main, "nothing-here", "--root", root)
        self.assertEqual((code, out.strip()), (1, "nothing-here: not found"))
        self.assertEqual(call(impact.main, "D-001", "--root", str(self.vault.parent))[0], 2)


class BacklinksTests(LedgerFixture):
    def test_targets_normalise_to_graph_ids(self) -> None:
        d2 = self.d2.relative_to(self.vault).with_suffix("").as_posix()
        cases = {
            "D-002": ("decision", d2),
            "D-404": ("decision", "D-404"),
            "E-002": ("evidence", "evidence:E-002"),
            f"{d2}.md": ("document", d2),
            str(self.d2): ("document", d2),
            "Home": ("document", "Home"),
        }
        for target, (kind, node_id) in cases.items():
            with self.subTest(target):
                report = backlinks.build_backlinks(self.vault, target)
                self.assertEqual((report["kind"], report["id"]), (kind, node_id))
        self.assertTrue(backlinks.build_backlinks(self.vault, "D-002")["count"] >= 1)
        self.assertFalse(backlinks.build_backlinks(self.vault, "D-404")["exists"])

    def test_cli_human_json_and_missing(self) -> None:
        root = str(self.vault)
        code, out, _ = call(backlinks.main, "D-002", "--root", root)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"backlinks for .*d-002-.* \(\d+\)")
        self.assertIn("Home", out)
        code, out, _ = call(backlinks.main, "E-002", "--root", root, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], "evidence:E-002")
        code, _, err = call(backlinks.main, "missing-note", "--root", root)
        self.assertEqual(code, 1)
        self.assertIn("target not found", err)
        self.assertEqual(call(backlinks.main, "D-002", "--root", str(self.vault.parent))[0], 2)


class ContextTests(LedgerFixture):
    def test_body_budget_truncates_and_no_body_omits(self) -> None:
        report = context.build_context(self.vault, "D-002", max_chars=40)
        self.assertTrue(report["content_truncated"])
        self.assertEqual(len(report["content"]), 40)
        self.assertGreater(report["content_chars"], 40)
        self.assertEqual([item["id"] for item in report["evidence"]], ["E-002"])
        bare = context.build_context(self.vault, "D-002", include_body=False)
        self.assertEqual((bare["content"], bare["content_truncated"]), ("", False))
        with self.assertRaises(ValueError):
            context.build_context(self.vault, "D-002", max_chars=-1)

    def test_evidence_context_carries_the_register_row(self) -> None:
        report = context.build_context(self.vault, "E-001")
        self.assertEqual(report["kind"], "evidence")
        self.assertEqual(report["evidence"]["state"], "retired")
        self.assertEqual(report["replacement"], "E-002")

    def test_cli_outputs(self) -> None:
        root = str(self.vault)
        code, out, _ = call(context.main, "D-002", "--root", root, "--max-chars", "30")
        self.assertEqual(code, 0)
        self.assertIn("--- document ---", out)
        self.assertIn("[content truncated]", out)
        code, out, _ = call(context.main, "E-002", "--root", root)
        self.assertEqual(code, 0)
        self.assertIn("source       Second survey", out)
        code, out, _ = call(context.main, "D-002", "--root", root, "--no-body", "--json")
        self.assertEqual(json.loads(out)["content"], "")
        self.assertEqual(call(context.main, "D-404", "--root", root)[0:2], (1, "D-404: not found\n"))
        self.assertEqual(call(context.main, "D-002", "--root", root, "--max-chars", "-1")[0], 2)
        self.assertEqual(call(context.main, "D-002", "--root", str(self.vault.parent))[0], 2)


class PackTests(LedgerFixture):
    def test_markdown_bundle_lists_contexts_evidence_and_missing_targets(self) -> None:
        code, out, _ = call(pack.main, "D-002", "E-001", "D-404", "--root", str(self.vault), "--format", "markdown", "--for", "generic")
        self.assertEqual(code, 1)
        self.assertIn("# WhyKit context bundle", out)
        self.assertIn("## Deduplicated evidence", out)
        self.assertIn("**E-001** [retired]", out)
        self.assertIn("`D-404` — missing", out)

    def test_budget_exhaustion_truncates_and_drops_later_bodies(self) -> None:
        report = pack.build_pack(self.vault, targets=["D-002", "D-001"], max_chars=25)
        self.assertEqual(report["budget"]["used_chars"], 25)
        self.assertTrue(report["budget"]["exhausted"])
        self.assertEqual(report["contexts"][1]["content"], "")
        code, out, _ = call(pack.main, "D-002", "--root", str(self.vault), "--format", "markdown", "--max-chars", "10")
        self.assertEqual(code, 0)
        self.assertIn("Content truncated by bundle budget", out)

    def test_unknown_agent_falls_back_to_the_generic_preamble(self) -> None:
        self.assertEqual(pack.build_pack(self.vault, targets=["D-002"], agent="  Unknown ")["agent"], "generic")

    def test_argument_validation(self) -> None:
        root = str(self.vault)
        for argv in ((), ("D-002", "--max-docs", "0"), ("D-002", "--max-chars", "-1")):
            with self.subTest(argv=argv):
                self.assertEqual(call(pack.main, *argv, "--root", root)[0], 2)
        self.assertEqual(call(pack.main, "D-002", "--root", str(self.vault.parent))[0], 2)


class SnapshotTests(LedgerFixture):
    def test_snapshot_output_modes(self) -> None:
        root, day = str(self.vault), self.day.isoformat()
        code, out, _ = call(snapshot.main_snapshot, "--root", root, "--today", day, "--compact")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out)["as_of"], day)
        self.assertEqual(call(snapshot.main_snapshot, "--root", root, "--today", "today")[0], 2)
        outside = Path(self.tmp.name) / "elsewhere.json"
        code, _, err = call(snapshot.main_snapshot, "--root", root, "--output", str(outside))
        self.assertEqual(code, 2)
        self.assertIn("inside the vault", err)
        self.assertFalse(outside.exists())
        self.assertEqual(call(snapshot.main_snapshot, "--root", str(self.vault.parent))[0], 2)

    def test_verify_reports_match_and_every_kind_of_drift(self) -> None:
        root, day = str(self.vault), self.day.isoformat()
        self.assertEqual(call(snapshot.main_snapshot, "--root", root, "--today", day, "--output", "base.json")[0], 0)
        (self.vault / "base.json").rename(self.vault / ".whykit-base.json")
        code, out, _ = call(snapshot.main_verify, ".whykit-base.json", "--root", root, "--today", day)
        self.assertEqual(code, 0)
        self.assertIn("snapshot verification: MATCH", out)

        (self.vault / "notes" / "added.md").write_text("# Added\n", encoding="utf-8")
        (self.vault / "Home.md").write_text((self.vault / "Home.md").read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        self.d1.unlink()
        code, out, _ = call(snapshot.main_verify, ".whykit-base.json", "--root", root, "--today", day)
        self.assertEqual(code, 1)
        self.assertIn("snapshot verification: DRIFT", out)
        self.assertRegex(out, r"added\s+1")
        self.assertRegex(out, r"removed\s+1")
        self.assertRegex(out, r"changed\s+1")
        self.assertIn("health changes", out)

    def test_time_alone_changes_health_but_not_content(self) -> None:
        baseline = snapshot.build_snapshot(self.vault, today=self.day)
        report = snapshot.compare_snapshot(self.vault, baseline, today=self.day + dt.timedelta(days=400))
        self.assertTrue(report["matches"])
        self.assertTrue(report["health_changed"])
        self.assertIn("review_due", report["health_delta"])

    def test_verify_rejects_unusable_baselines(self) -> None:
        root = str(self.vault)
        cases = {
            "wrong-format.json": json.dumps({"format": "other/v9"}),
            "not-an-object.json": "[]",
            "broken.json": "{",
        }
        for name, body in cases.items():
            with self.subTest(name):
                (self.vault / name).write_text(body, encoding="utf-8")
                code, _, err = call(snapshot.main_verify, name, "--root", root)
                self.assertEqual(code, 2)
                self.assertTrue(err.strip())
        self.assertEqual(call(snapshot.main_verify, "absent.json", "--root", root)[0], 2)
        self.assertEqual(call(snapshot.main_verify, "absent.json", "--root", root, "--today", "x")[0], 2)
        self.assertEqual(call(snapshot.main_verify, "absent.json", "--root", str(self.vault.parent))[0], 2)


class CheckTests(LedgerFixture):
    def test_release_profile_fails_closed_outside_git(self) -> None:
        report = check.run_check(self.vault, profile_name="release", today=self.day)
        by_name = {item["name"]: item for item in report["checks"]}
        self.assertFalse(report["passed"])
        if not check._git_repo(self.vault):
            self.assertFalse(by_name["git_repository"]["passed"])
            self.assertFalse(by_name["clean_tree"]["passed"])
        self.assertFalse(by_name["history"]["passed"])
        self.assertIn("--base", by_name["history"]["detail"])

    def test_base_ref_requires_a_git_work_tree(self) -> None:
        if check._git_repo(self.vault):
            self.skipTest("temporary directory is inside a git work tree")
        report = check.run_check(self.vault, profile_name="local", base="HEAD~1", today=self.day)
        history = next(item for item in report["checks"] if item["name"] == "history")
        self.assertFalse(history["passed"])
        self.assertIn("not in a git work tree", history["detail"])

    def test_cli_human_output_and_argument_errors(self) -> None:
        root, day = str(self.vault), self.day.isoformat()
        code, out, _ = call(check.main, "--root", root, "--profile", "local", "--today", day)
        self.assertEqual(code, 0)
        self.assertIn("WhyKit local gate — PASS", out)
        self.assertIn("lint findings", out)
        code, out, _ = call(check.main, "--root", root, "--profile", "release", "--today", day)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertEqual(call(check.main, "--root", root, "--profile", "no-such-profile")[0], 2)
        self.assertEqual(call(check.main, "--root", root, "--today", "x")[0], 2)
        self.assertEqual(call(check.main, "--root", str(self.vault.parent))[0], 2)


class SymlinkedVaultTests(unittest.TestCase):
    def test_supersession_through_a_symlinked_vault_path(self) -> None:
        # macOS temp dirs live behind /var -> /private/var; any vault reached
        # through a symlinked ancestor must still supersede in place.
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real"
            day = fresh_vault(real)
            link = Path(td) / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available")
            review_by = (day + dt.timedelta(days=30)).isoformat()
            create_decision(link, "First", owner="Research", status="approved", review_by=review_by, today=day)
            decision_id, path = create_decision(link, "Second", owner="Research", status="approved",
                                                review_by=review_by, supersedes="D-001", today=day)
            self.assertEqual(decision_id, "D-002")
            first = next((real / "06-decisions").glob("d-001-*.md")).read_text(encoding="utf-8")
            self.assertEqual(front(first, "status"), "superseded")
            self.assertEqual(front(first, "superseded_by"), "D-002")


if __name__ == "__main__":
    unittest.main()
