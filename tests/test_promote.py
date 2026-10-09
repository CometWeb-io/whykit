"""`whykit new decision --from FILE`: an adopted ADR becomes a decision record.

The promotion must recognise the ADR shapes people actually have, keep the
source text and its provenance, never write on a dry run, and write the record
and its decision-log row together or not at all.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from _vaults import fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit import scaffold  # noqa: E402
from whykit.adopt import adopt  # noqa: E402
from whykit.io import stage_transaction, vault_mutation_lock  # noqa: E402
from whykit.lint import decision_log_rows, lint, load_note  # noqa: E402
from whykit.promote import build_promotion  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 17)

NYGARD = """# 7. Use queues for billing events

Date: 2019-03-04

## Status

Accepted

## Context

Billing events arrive in bursts after each release, see https://status.example.com/incidents/42.
The synchronous handler times out.

## Decision

We will put billing events on a queue and process them with workers.

## Consequences

Retries become cheap. Ordering is no longer guaranteed.

## Notes

Discussed in the March architecture review.
"""

MADR = """---
status: accepted
date: 2024-05-02
deciders: Example team
---
# Use PostgreSQL for the event store

## Context and Problem Statement

We need a durable event store that the team can operate.

## Decision Drivers

* Operational familiarity
* Transactional guarantees

## Considered Options

* PostgreSQL
* EventStoreDB
* Kafka with compaction

## Decision Outcome

Chosen option: "PostgreSQL", because the team already runs it and it meets the drivers.

### Consequences

* Good, because backups are already in place
* Bad, because projections need custom code

## Pros and Cons of the Options

### EventStoreDB

* Good, because it is built for events

## More Information

See https://www.example.com/adr-guide and https://www.example.com/runbook.
"""

Y_STATEMENT = """# Cache prices

In the context of the checkout service, facing slow price lookups, we decided for a
read-through cache and neglected direct database reads and a CDN, to achieve sub-100 ms
checkout, accepting that prices may be stale for 60 seconds.
"""

POLISH = """# ADR 3: Wybór kolejki

## Status

Zaakceptowana

## Kontekst

Zdarzenia rozliczeniowe przychodzą falami po każdym wdrożeniu.

## Rozważane opcje

- RabbitMQ
- Kafka

## Decyzja

Wybieramy RabbitMQ.

## Uzasadnienie

Zespół zna go najlepiej.

## Konsekwencje

- Dobrze, ponieważ obsługa jest prosta
- Źle, ponieważ trzeba utrzymać klaster
"""


def section(text: str, heading: str) -> str:
    """The body of one `## heading` section of a generated record."""
    after = text.split(f"\n## {heading}\n", 1)[1]
    return after.split("\n## ", 1)[0].strip()


class PromoteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.vault = self.base / "vault"
        fresh_vault(self.vault)
        self.source = self.base / "old-docs"

    def stage(self, name: str, text: str) -> Path:
        """Adopt *text* as `adr/<name>` and return the staged copy."""
        path = self.source / "adr" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        _, _, migration, _ = adopt(self.source, self.vault, write=True, owner="Example owner", today=TODAY)
        assert migration is not None
        return migration.parent / "adr" / name

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["new", "decision", *argv, "--root", str(self.vault)])
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def state(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in sorted((self.vault / "06-decisions").rglob("*")) if path.is_file()
        }


class ShapeMappingTests(PromoteTestCase):
    def test_nygard(self) -> None:
        staged = self.stage("0007-use-queues.md", NYGARD)
        promotion = build_promotion(self.vault, staged)
        self.assertEqual(promotion.shape, "nygard")
        self.assertEqual(promotion.title, "Use queues for billing events")
        self.assertEqual(promotion.source_status, "Accepted")
        self.assertEqual(promotion.source_date, "2019-03-04")
        self.assertEqual(promotion.source_id, "0007")
        self.assertEqual(promotion.batch, "2026-09-17")
        self.assertEqual(promotion.batch_path, "adr/0007-use-queues.md")
        self.assertIn("put billing events on a queue", promotion.sections["decision"])
        self.assertIn("Retries become cheap", promotion.sections["consequences"])
        self.assertNotIn("rationale", promotion.sections)
        self.assertEqual(promotion.unmapped, ["Notes"])
        self.assertEqual(promotion.evidence_candidates, ["https://status.example.com/incidents/42"])

    def test_madr(self) -> None:
        promotion = build_promotion(self.vault, self.stage("0002-postgres.md", MADR))
        self.assertEqual(promotion.shape, "madr")
        self.assertEqual(promotion.title, "Use PostgreSQL for the event store")
        self.assertEqual((promotion.source_status, promotion.source_date), ("accepted", "2024-05-02"))
        self.assertIn("durable event store", promotion.sections["context"])
        self.assertIn("Operational familiarity", promotion.sections["drivers"])
        self.assertTrue(promotion.sections["decision"].startswith('Chosen option: "PostgreSQL"'))
        self.assertEqual(promotion.sections["rationale"], "Because the team already runs it and it meets the drivers.")
        self.assertEqual(promotion.alternatives, ["EventStoreDB", "Kafka with compaction"])
        self.assertEqual(promotion.positive, ["Backups are already in place"])
        self.assertEqual(promotion.negative, ["Projections need custom code"])
        self.assertEqual(promotion.unmapped, ["Pros and Cons of the Options"])
        self.assertEqual(
            promotion.evidence_candidates,
            ["https://www.example.com/adr-guide", "https://www.example.com/runbook"],
        )

    def test_y_statement(self) -> None:
        promotion = build_promotion(self.vault, self.stage("cache.md", Y_STATEMENT))
        self.assertEqual(promotion.shape, "y-statement")
        self.assertEqual(promotion.title, "Cache prices")
        self.assertEqual(promotion.sections["context"], "The checkout service, facing slow price lookups.")
        self.assertEqual(promotion.sections["decision"], "A read-through cache.")
        self.assertEqual(promotion.sections["rationale"], "To achieve sub-100 ms checkout.")
        self.assertEqual(promotion.alternatives, ["direct database reads", "a CDN"])
        self.assertEqual(promotion.negative, ["Prices may be stale for 60 seconds."])

    def test_polish_headings(self) -> None:
        promotion = build_promotion(self.vault, self.stage("0003-kolejka.md", POLISH))
        self.assertEqual(promotion.shape, "polish-adr")
        self.assertEqual(promotion.title, "Wybór kolejki")
        self.assertEqual(promotion.source_status, "Zaakceptowana")
        self.assertEqual(promotion.source_id, "0003")
        self.assertEqual(promotion.sections["decision"], "Wybieramy RabbitMQ.")
        self.assertEqual(promotion.sections["rationale"], "Zespół zna go najlepiej.")
        self.assertEqual(promotion.alternatives, ["Kafka"])
        self.assertEqual(promotion.positive, ["Obsługa jest prosta"])
        self.assertEqual(promotion.negative, ["Trzeba utrzymać klaster"])

    def test_unrecognised_text_maps_nothing_and_says_so(self) -> None:
        promotion = build_promotion(self.vault, self.stage("musing.md", "# Musing\n\nWe might move to queues one day.\n"))
        self.assertEqual(promotion.shape, "unstructured")
        self.assertEqual(promotion.sections, {})
        self.assertTrue(any("no ADR shape recognised" in w for w in promotion.warnings))

    def test_foreign_ids_and_wikilinks_are_kept_as_text(self) -> None:
        text = NYGARD.replace("times out.", "times out, as E-012 showed; see [[pricing]] and D-004.")
        promotion = build_promotion(self.vault, self.stage("0007-use-queues.md", text))
        context = promotion.sections["context"]
        self.assertIn("`E-012`", context)
        self.assertIn("`D-004`", context)
        self.assertIn("`[[pricing]]`", context)
        self.assertTrue(any("E-012" in w for w in promotion.warnings))

    def test_numbering_is_dropped_but_numbers_in_titles_are_kept(self) -> None:
        cases = {
            "a.md": ("# 0012 - Split the monolith\n\n## Context\n\nSlow builds.\n\n## Decision\n\nSplit.\n", "Split the monolith", "0012"),
            "b.md": ("# ADR-4: Adopt OKRs\n\n## Context\n\nDrift.\n\n## Decision\n\nAdopt.\n", "Adopt OKRs", "4"),
            "c.md": ("# 2024 roadmap\n\n## Context\n\nPlanning.\n\n## Decision\n\nShip.\n", "2024 roadmap", None),
        }
        for name, (text, title, number) in cases.items():
            with self.subTest(name=name):
                promotion = build_promotion(self.vault, self.stage(name, text))
                self.assertEqual((promotion.title, promotion.source_id), (title, number))

    def test_explicit_title_wins(self) -> None:
        promotion = build_promotion(self.vault, self.stage("0007-use-queues.md", NYGARD), title="Queue billing events")
        self.assertEqual(promotion.title, "Queue billing events")


class DryRunTests(PromoteTestCase):
    def test_dry_run_prints_the_mapping_and_writes_nothing(self) -> None:
        staged = self.stage("0007-use-queues.md", NYGARD)
        before = self.state()
        code, out, err = self.run_cli("--from", str(staged))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state(), before)
        self.assertIn("(nygard)", out)
        self.assertIn("would create  D-001: 06-decisions/d-001-use-queues-for-billing-events.md", out)
        self.assertIn("Context", out)
        self.assertIn("<- Context", out)
        self.assertIn("unmapped      Notes", out)
        self.assertIn("Dry run. Nothing was written.", out)

    def test_dry_run_json_follows_the_record_create_schema(self) -> None:
        staged = self.stage("0002-postgres.md", MADR)
        code, out, err = self.run_cli("--from", str(staged), "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        schema = json.loads((ROOT / "schemas" / "record-create.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(payload, schema), [])
        self.assertFalse(payload["write"])
        self.assertEqual(payload["id"], "D-001")
        self.assertEqual(payload["source"]["format"], "madr")
        self.assertEqual(payload["source"]["sha256"], hashlib.sha256(staged.read_bytes()).hexdigest())
        self.assertEqual(payload["source"]["path"], staged.relative_to(self.vault).as_posix())
        self.assertIn({"section": "Decision", "from": "Decision Outcome"}, payload["mapping"])


class WriteTests(PromoteTestCase):
    def test_write_creates_a_record_a_log_row_and_provenance(self) -> None:
        staged = self.stage("0007-use-queues.md", NYGARD)
        code, out, err = self.run_cli("--from", str(staged), "--write", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["write"])
        record = self.vault / payload["path"]
        text = record.read_text(encoding="utf-8")
        note = load_note(record)
        self.assertEqual(note.front["decision_id"], "D-001")
        self.assertEqual(note.front["status"], "draft")
        provenance = note.front["provenance"]
        self.assertEqual(provenance["source_sha256"], hashlib.sha256(staged.read_bytes()).hexdigest())
        self.assertEqual(provenance["source_path"], "adr/0007-use-queues.md")
        self.assertEqual(provenance["payload_ref"], staged.relative_to(self.vault).as_posix())
        self.assertEqual(provenance["producer_run"], "2026-09-17")
        self.assertEqual(provenance["source_status"], "Accepted")
        self.assertIs(provenance["human_reviewed"], False)
        # The original survives whole, inside a fence lint does not parse.
        self.assertIn("````markdown\n" + NYGARD.rstrip("\n") + "\n````", text.split("\n## Original record\n", 1)[1])
        self.assertIn("put billing events on a queue", section(text, "Decision"))
        self.assertIn("<https://status.example.com/incidents/42>", section(text, "Evidence"))
        rows = {row["id"]: row for row in decision_log_rows(self.vault)}
        self.assertEqual(rows["D-001"]["status"], "proposed")
        _, findings = lint(self.vault, today=TODAY)
        errors = [f for f in findings if f.level == "error"]
        self.assertEqual(errors, [])

    def test_ids_continue_after_existing_decisions(self) -> None:
        scaffold.create_decision(self.vault, "Existing choice", owner="Example owner", today=TODAY)
        code, out, err = self.run_cli("--from", str(self.stage("0002-postgres.md", MADR)), "--write", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["id"], "D-002")

    def test_promoting_the_same_bytes_twice_is_refused(self) -> None:
        staged = self.stage("0007-use-queues.md", NYGARD)
        self.assertEqual(self.run_cli("--from", str(staged), "--write")[0], 0)
        before = self.state()
        for argv in (("--from", str(staged), "--write", "--json"), ("--from", str(staged), "--json")):
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)["error"]["code"], "target_exists")
                self.assertIn("D-001", json.loads(out)["error"]["message"])
        self.assertEqual(self.state(), before)

    def test_a_crash_between_staging_and_commit_is_finished_by_the_journal(self) -> None:
        staged = self.stage("0007-use-queues.md", NYGARD)
        before = self.state()

        def stage_only(vault: Path, updates: dict) -> None:
            stage_transaction(vault, updates)
            raise KeyboardInterrupt("simulated crash after the journal was written")

        with mock.patch.object(scaffold, "apply_transaction", stage_only), self.assertRaises(KeyboardInterrupt):
            scaffold.create_decision(
                self.vault, "", today=TODAY, promotion=build_promotion(self.vault, staged),
            )
        # Nothing reached the ledger: the record and the log row are both pending.
        self.assertEqual(self.state(), before)
        with vault_mutation_lock(self.vault):
            pass  # the next writer recovers the READY transaction
        after = self.state()
        records = [name for name in after if name.startswith("06-decisions/d-001-")]
        self.assertEqual(len(records), 1)
        self.assertIn("D-001", {row["id"] for row in decision_log_rows(self.vault)})

    def test_a_failed_write_leaves_neither_file(self) -> None:
        staged = self.stage("0007-use-queues.md", NYGARD)
        before = self.state()
        with mock.patch.object(scaffold, "apply_transaction", side_effect=OSError(28, "No space left on device")):
            code, _, err = self.run_cli("--from", str(staged), "--write")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.state(), before)

    def test_outside_the_vault_only_the_file_name_is_recorded(self) -> None:
        path = self.base / "elsewhere" / "0004-cache.md"
        path.parent.mkdir()
        path.write_text(Y_STATEMENT, encoding="utf-8")
        code, out, err = self.run_cli("--from", str(path), "--write", "--json")
        self.assertEqual(code, 0, err)
        text = (self.vault / json.loads(out)["path"]).read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), text)
        provenance = load_note(self.vault / json.loads(out)["path"]).front["provenance"]
        self.assertEqual(provenance["source_path"], "0004-cache.md")
        self.assertNotIn("payload_ref", provenance)

    def test_a_fence_inside_the_source_does_not_end_the_original_block(self) -> None:
        text = NYGARD + "\n```yaml\nqueue: billing\n```\n\n`````\nnested\n`````\n"
        staged = self.stage("0007-use-queues.md", text)
        code, out, err = self.run_cli("--from", str(staged), "--write", "--json")
        self.assertEqual(code, 0, err)
        record = (self.vault / json.loads(out)["path"]).read_text(encoding="utf-8")
        self.assertIn("``````markdown\n", record)
        self.assertTrue(record.rstrip().endswith("``````"))


class RefusalTests(PromoteTestCase):
    def test_import_cannot_create_approved_or_retired_records(self) -> None:
        path = self.stage("0007-use-queues.md", NYGARD)
        before = self.state()
        for status in ("approved", "superseded", "archived"):
            for write in (False, True):
                with self.subTest(status=status, write=write):
                    args = ["--from", str(path), "--status", status, "--json"]
                    code, out, err = self.run_cli(*args, *(["--write"] if write else []))
                    self.assertEqual(code, 2, err)
                    self.assertEqual(json.loads(out)["error"]["code"], "usage")
                    self.assertEqual(self.state(), before)

    def test_missing_binary_and_non_markdown_sources(self) -> None:
        cases = {
            "missing.md": None,
            "image.png": b"\x89PNG\r\n",
            "binary.md": b"\x00\x01\x02",
            "latin1.md": "Zażółć".encode("latin-1", "replace"),
        }
        for name, data in cases.items():
            path = self.base / name
            if data is not None:
                path.write_bytes(data)
            with self.subTest(name=name):
                code, out, _ = self.run_cli("--from", str(path), "--json")
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)["error"]["code"], "invalid_target")

    def test_write_without_from_is_a_usage_error(self) -> None:
        code, out, _ = self.run_cli("Title", "--write", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "usage")

    def test_no_title_and_no_source_is_a_usage_error(self) -> None:
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "usage")

    def test_plain_new_decision_is_unchanged(self) -> None:
        code, out, err = self.run_cli("Adopt usage-based pricing", "--json")
        self.assertEqual(code, 0, err)
        text = (self.vault / json.loads(out)["path"]).read_text(encoding="utf-8")
        self.assertNotIn("provenance:", text)
        self.assertNotIn("## Original record", text)


if __name__ == "__main__":
    unittest.main()
