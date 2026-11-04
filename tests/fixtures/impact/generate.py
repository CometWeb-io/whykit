"""Native synthetic impact corpus, generated before any assessment implementation."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
from typing import Any

from whykit.artifact_store import capture_artifact, read_artifact
from whykit.claim_ledger import create_claim, read_claim
from whykit.config import load_config
from whykit.context_applicability import context_metadata
from whykit.immutability import _approval_event_matches, approval_record_hash
from whykit.lint import load_note
from whykit.outcome_ledger import create_outcome, read_outcome
from whykit.placeholders import ALTERNATIVES_EMPTY_ROW, SCAFFOLD_SECTION_PROMPTS
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision, create_evidence

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DAY = dt.date(2026, 9, 17)
AS_OF = dt.date(2026, 10, 27)
OWNER = "Ada Example"
LABELS = {"public", "internal", "confidential", "restricted"}
DOCUMENT_NAMESPACE = "document_raw_sha256"


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def put(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode("utf-8") if isinstance(value, str) else value)


def validator():
    spec = importlib.util.spec_from_file_location("impact_fixture_validator", HERE / "validate_oracle.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(path: Path, *, root: Path, decision: bool = False) -> dict:
    value = {"path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes())}
    if decision:
        value["decision_record_sha256"] = approval_record_hash(path.read_text(encoding="utf-8"))
    return value


def dependency(relation: str, target: str, namespace: str, sha: str) -> dict:
    return {"relation": relation, "target": target, "pin": {"namespace": namespace, "sha256": sha}}


def provenance(values: dict) -> str:
    return "\n" + "\n".join("  " + key + ": " + json.dumps(value, ensure_ascii=False) for key, value in values.items())


def impact_declaration(kind: str | None, dependencies: list) -> str:
    return json.dumps({"format": "whykit.impact-dependencies/v1", "artifact_kind": kind,
                       "dependencies": dependencies}, separators=(",", ":"))


def rendered_note(title: str, body: str, *, kind: str = "reference", label: str = "restricted", source_ids=None,
                  producer: dict | None = None) -> str:
    fields = {"title": title, "aliases": [], "type": kind, "status": "draft", "owner": OWNER,
              "created": DAY.isoformat(), "last_updated": DAY.isoformat(), "source_of_truth": False,
              "sensitivity": label, "source_ids": source_ids or [], "tags": ["synthetic-fixture"]}
    text = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()) + "\n---\n\n" + body + "\n"
    return _frontmatter_replace(text, "provenance", provenance(producer)) if producer else text


class NativeWriter:
    def __init__(self, log=None):
        self.log = log
        self.steps = []

    def emit(self, api: str, **fields):
        # Public provenance omits root-bound expected-preview hashes; private log retains them.
        clean = {key: value for key, value in fields.items() if key != "expected_sha256"}
        self.steps.append({"api": api, **clean})
        if self.log is not None:
            self.log.write(json.dumps({"api": api, **fields}, sort_keys=True, ensure_ascii=False) + "\n")
            self.log.flush()

    def capture(self, vault: Path, eid: str, raw: bytes, stamp: str, name: str) -> dict:
        source = vault / ".inputs" / (name + ".json")
        put(source, raw)
        plan = capture_artifact(vault, eid, source, recorded_at=stamp)
        out = capture_artifact(vault, eid, source, recorded_at=stamp, write=True, expected_sha256=plan["expected_sha256"])
        self.emit("capture_artifact.preview_apply", evidence_id=eid, source_version=out["source_version"],
                  path=out["path"], recorded_at=stamp, expected_sha256=plan["expected_sha256"])
        return out

    def claim(self, vault: Path, workflow: dict, branch: str, source_key: str, *, expected_id: str | None = None, today: dt.date = DAY, value_only: bool = False) -> dict:
        raw = workflow["sources"][source_key]["utf8"].encode()
        key = workflow["fields"][branch]
        quote = json.dumps(json.loads(raw)[key], separators=(",", ":"))
        if not value_only:
            quote = json.dumps(key) + ":" + quote
        start = raw.index(quote.encode())
        link = {"evidence_id": workflow["evidence_id"], "source_version": workflow["sources"][source_key]["source_version"],
                "location": {"start_byte": start, "end_byte": start + len(quote.encode()), "quote": quote},
                "assessment": {"kind": "human", "by": OWNER, "relation": "supports"}}
        args: dict[str, Any] = {"title": f"Synthetic {workflow['id']} {branch} input", "owner": OWNER, "today": today,
                "valid_from": "2026-09-01", "valid_until": "2027-01-01"}
        plan = create_claim(vault, quote, [link], **args)
        out = create_claim(vault, quote, [link], write=True, expected_sha256=plan["expected_sha256"], **args)
        if expected_id is not None and out["claim_id"] != expected_id:
            raise ValueError("native C allocation changed")
        self.emit("create_claim.preview_apply", claim_id=out["claim_id"], path=out["path"], quote=quote,
                  source_version=link["source_version"], expected_sha256=plan["expected_sha256"])
        return {"claim_id": out["claim_id"], "path": out["path"]}

    def decision(self, vault: Path, workflow: dict, branch: str, claim: dict, source_key: str,
                 *, supersedes: str | None = None, expected_id: str | None = None, today: dt.date = DAY) -> dict:
        did, path = create_decision(vault, f"Synthetic {workflow['id']} {branch} choice" + (" successor" if supersedes else ""),
                                    owner=OWNER, sensitivity="restricted", source_ids=[workflow["evidence_id"]],
                                    supersedes=supersedes, today=today)
        if expected_id is not None and did != expected_id:
            raise ValueError("native D allocation changed")
        text = path.read_text(encoding="utf-8")
        key = workflow["fields"][branch]
        value = json.loads(workflow["sources"][source_key]["utf8"])[key]
        replacements = {SCAFFOLD_SECTION_PROMPTS["Context"]: "A synthetic consumer must follow a declared bounded parameter.",
                        SCAFFOLD_SECTION_PROMPTS["Decision"]: f"For {workflow['id']} use {key}={json.dumps(value)}.",
                        SCAFFOLD_SECTION_PROMPTS["Rationale"]: "The captured named-field declaration is the explicit input; separate functional checks establish its fixture effect.",
                        ALTERNATIVES_EMPTY_ROW: "| Keep an unrelated value | Familiar | Violates the selected parameter | Not the declared input |",
                        "### Positive\n\n-\n": "### Positive\n\n- A reproducible bounded operation.\n",
                        "### Negative and trade-offs\n\n-\n": "### Negative and trade-offs\n\n- Inputs and consumers require review.\n"}
        for before, after in replacements.items():
            if text.count(before) != 1:
                raise ValueError("native skeleton changed")
            text = text.replace(before, after)
        claim_hash = digest((vault / claim["path"]).read_bytes())
        producer = {"claim_versions": json.dumps({claim["claim_id"]: "sha256:" + claim_hash}, separators=(",", ":")),
                    "whykit_impact": impact_declaration(None, [dependency("requires", "claim:" + claim["claim_id"], "claim_markdown_sha256", claim_hash)])}
        for field, value in {"aliases": json.dumps([did]), "valid_from": "2026-09-01", "valid_until": "2027-01-01",
                             "claim_ids": json.dumps([claim["claim_id"]]), "provenance": provenance(producer)}.items():
            text = _frontmatter_replace(text, field, value)
        put(path, text)
        plan = approve_decision(vault, did, reviewer=OWNER, today=today, next_review="2026-12-31")
        approve_decision(vault, did, reviewer=OWNER, today=today, next_review="2026-12-31", write=True, expected_sha256=plan["expected_sha256"])
        self.emit("create_decision_and_approve.preview_apply", decision_id=did, path=path.relative_to(vault).as_posix(),
                  record_sha256=approval_record_hash(path.read_text(encoding="utf-8")), expected_sha256=plan["expected_sha256"])
        return {"decision_id": did, "path": path.relative_to(vault).as_posix()}

    def outcome(self, vault: Path, workflow: dict, consumer: dict, did: str, claim: dict | None,
                config_sha: str, source_key: str, *, expected_id: str) -> dict:
        historic = consumer["kind"] == "historical-outcome"
        if historic:
            value = "3" if workflow["id"] == "retry" else "0"
            baseline, target = None, None
            observed = {"value": value, "as_of": DAY.isoformat(), "claim": None}
        else:
            assert claim is not None
            setting = consumer["draft_settings_" + source_key]
            value = str(setting if type(setting) is int else (65 if setting else 64))
            pin = {"claim_id": claim["claim_id"], "sha256": digest((vault / claim["path"]).read_bytes())}
            baseline = {"value": value, "as_of": DAY.isoformat(), "claim": pin}
            target = {"value": value, "by": "2026-12-01"}
            observed = None
        data = {"decision_id": did, "metric": {"name": "Declared fixture " + consumer["branch"] + " parameter", "unit": "count",
                "definition": "Trusted fixture profile " + consumer["functional_profile"] + "; functional-config-sha256:" + config_sha + ". Local declaration, not a certified measurement."},
                "baseline": baseline, "target": target, "observed": observed,
                "implementation_refs": ["https://changes.example/local/opaque-reference"]}
        args: dict[str, Any] = {"owner": OWNER, "title": "Synthetic " + consumer["id"], "today": DAY}
        plan = create_outcome(vault, data, **args)
        out = create_outcome(vault, data, write=True, expected_sha256=plan["expected_sha256"], **args)
        if out["outcome_id"] != expected_id:
            raise ValueError("native O allocation changed")
        self.emit("create_outcome.preview_apply", outcome_id=out["outcome_id"], path=out["path"], expected_sha256=plan["expected_sha256"])
        return out


def initialize(vault: Path) -> None:
    vault.mkdir(parents=True)
    put(vault / "Home.md", rendered_note("Synthetic corpus map", "Fixed local behavior fixtures.", label="public"))
    for relative in ("00-context/evidence-register.md", "06-decisions/decision-log.md"):
        text = (REPO / "src/whykit/template" / relative).read_text(encoding="utf-8")
        for key, value in (("owner", OWNER), ("sensitivity", "public")):
            text = _frontmatter_replace(text, key, value)
        put(vault / relative, text)
    put(vault / "whykit.toml", '[evidence_access_age_days]\nfixture = 365\n')


def make_base(vault: Path, oracle: dict, source_key: str, writer: NativeWriter) -> dict:
    initialize(vault)
    identities = {}
    for workflow in oracle["workflows"]:
        eid = create_evidence(vault, source="Synthetic " + workflow["id"] + " declared parameters",
                              location="https://source.example/" + workflow["id"], kind="fixture",
                              claims="Two independent named-field declarations; source authenticity is not inferred.",
                              sensitivity="restricted", today=DAY)
        if eid != workflow["evidence_id"]:
            raise ValueError("native E allocation changed")
        writer.emit("create_evidence", evidence_id=eid)
        for minute, (key, source) in enumerate(workflow["sources"].items()):
            writer.capture(vault, eid, source["utf8"].encode(), f"2026-09-17T00:{minute:02}:00Z", workflow["id"] + "-" + key)
        writer.capture(vault, eid, workflow["sources"][source_key]["utf8"].encode(), "2026-09-17T01:00:00Z", workflow["id"] + "-selected")
        claims, decisions, configs, paths = {}, {}, {}, {}
        for index, branch in enumerate(("primary", "secondary")):
            claims[branch] = writer.claim(vault, workflow, branch, source_key, expected_id=workflow["claim_ids"][index])
            decisions[branch] = writer.decision(vault, workflow, branch, claims[branch], source_key,
                                                expected_id=workflow["decision_ids"][index])
        for consumer in workflow["consumers"]:
            config = {"profile": consumer["functional_profile"],
                      **({"record": consumer["historical_record"]} if consumer["kind"] == "historical-outcome"
                         else {"value": consumer["draft_settings_" + source_key]})}
            config_path = vault / ".functional" / (consumer["id"] + ".json")
            put(config_path, json_text(config))
            configs[consumer["id"]] = config_path.relative_to(vault).as_posix()
            config_sha = digest(config_path.read_bytes())
            branch = consumer["branch"]
            if consumer["kind"] in {"planned-outcome", "historical-outcome"}:
                idx = 2 if branch == "historical" else 0 if branch == "primary" else 1
                selected_branch = "primary" if branch == "historical" else branch
                out = writer.outcome(vault, workflow, consumer, decisions[selected_branch]["decision_id"],
                                     None if branch == "historical" else claims[branch], config_sha, source_key,
                                     expected_id=workflow["outcome_ids"][idx])
                paths[consumer["id"]] = out["path"]
                continue
            suffix = {"specification": "spec", "pull-request": "pr", "task": "task"}[consumer["kind"]]
            folder = {"spec": "specs", "pr": "pull-requests", "task": "tasks"}[suffix]
            relative = f"00-context/implementation/{folder}/{workflow['id']}-{branch}.md"
            if suffix == "spec":
                selected = decisions[branch]
                target = "decision:" + selected["decision_id"]
                sha = approval_record_hash((vault / selected["path"]).read_text(encoding="utf-8"))
                namespace, relation = "decision_record_sha256", "implements"
            else:
                previous = f"00-context/implementation/{'specs' if suffix == 'pr' else 'pull-requests'}/{workflow['id']}-{branch}.md"
                target, sha, namespace, relation = "document:" + previous, digest((vault / previous).read_bytes()), DOCUMENT_NAMESPACE, "implements" if suffix == "pr" else "requires"
            producer: dict[str, Any] = {"whykit_impact": impact_declaration(suffix if suffix == "spec" else consumer["kind"],
                        [dependency(relation, target, namespace, sha)])}
            if suffix == "pr":
                producer.update({"repository_key": consumer["repository_key"], "external_ref": consumer["external_ref"], "number": 7,
                                 "source_identity_authenticated": False})
            body = "# Synthetic consumer\n\nTrusted fixture profile " + consumer["functional_profile"] + ".\n\nfunctional-config-sha256:" + config_sha + "\n\nThis local card declares a dependency; it does not authenticate a provider or authorize execution."
            put(vault / relative, rendered_note("Synthetic " + consumer["id"], body,
                                                kind="specification" if suffix == "spec" else "reference",
                                                source_ids=[eid], producer=producer))
            paths[consumer["id"]] = relative
        identities[workflow["id"]] = {"claims": claims, "decisions": decisions, "consumers": paths, "configs": configs}
    return identities


def copy_vault(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".whykit"))


def make_event(output: Path, oracle: dict, event: dict, base: Path, identities: dict, writer: NativeWriter) -> dict:
    vault = output / "vaults" / "events" / event["id"]
    copy_vault(base, vault)
    workflow = next(w for w in oracle["workflows"] if w["id"] == event["workflow"])
    wf = identities[workflow["id"]]
    # Snapshot files use .bin, outside all vault roots; no duplicate note/ID exposure.
    before = output / "snapshots" / event["id"]
    notes_before = {}
    for consumer_id, path in wf["consumers"].items():
        snapshot = before / (consumer_id + ".bin")
        put(snapshot, (vault / path).read_bytes())
        notes_before[consumer_id] = record(snapshot, root=output)
    decision_path = vault / wf["decisions"]["primary"]["path"]
    decision_before = before / "decision-before.bin"
    put(decision_before, decision_path.read_bytes())
    transition = None
    if event["root"] == "source_version":
        if event["config_snapshot"] == "Z":
            writer.capture(vault, workflow["evidence_id"], workflow["sources"]["A"]["utf8"].encode(),
                           "2026-10-26T10:00:00Z", "event-before-A")
        writer.capture(vault, workflow["evidence_id"], workflow["sources"][event["after"]]["utf8"].encode(),
                       "2026-10-27T10:00:00Z", "event-after")
    elif event["id"].endswith("lifecycle-only"):
        record_review(vault, wf["decisions"]["primary"]["decision_id"], reviewer=OWNER, outcome="confirmed",
                      note_text="Only the governance review deadline changes; the selected parameter does not.",
                      next_review="2027-02-01", today=dt.date(2026, 10, 1))
        writer.emit("record_review.confirmed", decision_id=wf["decisions"]["primary"]["decision_id"], date="2026-10-01")
        transition = {"before": record(decision_before, root=output, decision=True),
                      "after": record(decision_path, root=output, decision=True)}
    else:
        writer.capture(vault, workflow["evidence_id"], workflow["sources"]["B"]["utf8"].encode(),
                       "2026-10-27T10:00:00Z", "event-before-successor")
        new_claim = writer.claim(vault, workflow, "primary", "B", today=AS_OF)
        successor = writer.decision(vault, workflow, "primary", new_claim, "B",
                                    supersedes=wf["decisions"]["primary"]["decision_id"], today=AS_OF)
        transition = {"before": record(decision_before, root=output, decision=True),
                      "after": record(vault / successor["path"], root=output, decision=True),
                      "old_after": record(decision_path, root=output, decision=True),
                      "successor_claim": record(vault / new_claim["path"], root=output),
                      "successor_claim_id": new_claim["claim_id"], "successor_decision_id": successor["decision_id"]}
    consumers = {}
    for consumer in workflow["consumers"]:
        identity = consumer["id"]
        consumers[identity] = {"note": record(vault / wf["consumers"][identity], root=output),
                               "note_before": notes_before[identity],
                               "functional_config": record(vault / wf["configs"][identity], root=output)}
    return {"vault": vault.relative_to(output).as_posix(), "consumers": consumers,
            "decision_transition": transition, "workflow": workflow["id"], "event": event}


def generate(output: Path, *, creation_log=None) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    oracle_raw = (HERE / "oracle-v1.json").read_bytes()
    oracle = json.loads(oracle_raw)
    writer = NativeWriter(creation_log)
    bases, identities = {}, {}
    for key in ("A", "Z"):
        bases[key] = output / "vaults" / ("base-" + key)
        identities[key] = make_base(bases[key], oracle, key, writer)
    bindings: dict[str, Any] = {"format": "whykit.synthetic-impact-native-binding/v1", "oracle_sha256": digest(oracle_raw),
                "fully_synthetic_local_scope": True, "captures": {}, "consumers": {}, "decision_transitions": {}}
    for workflow in oracle["workflows"]:
        captures = {}
        for key, source in workflow["sources"].items():
            sha = source["source_version"].split(":", 1)[1]
            captures[key] = record(bases["A"] / f"00-context/artifacts/{workflow['evidence_id']}/{sha}.json", root=output)
        bindings["captures"][workflow["id"]] = captures
    cases = {}
    for event in oracle["events"] + oracle["supplemental_events"]:
        key = event["config_snapshot"]
        item = make_event(output, oracle, event, bases[key], identities[key], writer)
        cases[event["id"]] = item
        bindings["consumers"][event["id"]] = item["consumers"]
        if item["decision_transition"]:
            bindings["decision_transitions"][event["id"]] = item["decision_transition"]
    # Dedicated relocation snapshots are uncertainty controls, NOT extra binary judgments.
    controls = {}
    for workflow in oracle["workflows"]:
        vault = output / "vaults" / "controls" / (workflow["id"] + "-relocated")
        copy_vault(bases["A"], vault)
        writer.capture(vault, workflow["evidence_id"], workflow["sources"]["B_relocated"]["utf8"].encode(),
                       "2026-10-27T10:00:00Z", "relocated-after")
        controls[workflow["id"] + "-relocated"] = {"vault": vault.relative_to(output).as_posix(),
            "expected_selector_state": "unknown", "binary_denominator": False,
            "reason": "Old byte position no longer identifies the same named field; no reanchor", "workflow": workflow["id"]}
    for workflow in oracle["workflows"]:
        vault = output / "vaults" / "controls" / (workflow["id"] + "-value-only")
        copy_vault(bases["A"], vault)
        claim = writer.claim(vault, workflow, "primary", "A", value_only=True)
        decision = writer.decision(vault, workflow, "primary", claim, "A")
        decision_sha = approval_record_hash((vault / decision["path"]).read_text(encoding="utf-8"))
        relative = f"00-context/implementation/tasks/{workflow['id']}-value-only.md"
        producer = {"whykit_impact": impact_declaration("task", [
            dependency("requires", "decision:" + decision["decision_id"], "decision_record_sha256", decision_sha)])}
        put(vault / relative, rendered_note("Synthetic value-only selector consumer", "This fixture names an ambiguous scalar-only input; its impact category must remain unknown.",
                                            source_ids=[workflow["evidence_id"]], producer=producer))
        writer.capture(vault, workflow["evidence_id"], workflow["sources"]["B"]["utf8"].encode(),
                       "2026-10-27T10:00:00Z", "value-only-after")
        controls[workflow["id"] + "-value-only"] = {"vault": vault.relative_to(output).as_posix(),
            "expected_selector_state": "unknown", "binary_denominator": False, "workflow": workflow["id"],
            "selector_claim_id": claim["claim_id"], "selector_decision_id": decision["decision_id"], "unknown_consumer_path": relative,
            "reason": "Scalar-only quote does not identify a logical field; no semantic/offset inference"}
    # These advisory metadata files were created only by this process. Never remove unknown entries.
    generated_vaults = list(bases.values()) + [output / item["vault"] for item in cases.values()] + [output / item["vault"] for item in controls.values()]
    for vault in generated_vaults:
        directory = vault / ".whykit"
        lock = directory / "mutation.lock"
        if lock.exists():
            owner = json.loads(lock.read_text(encoding="utf-8"))
            transactions = directory / "transactions"
            if (lock.is_symlink() or set(owner) != {"pid", "started_at"} or owner["pid"] != os.getpid()
                    or not set(directory.iterdir()) <= {lock, transactions}):
                raise ValueError("unexpected writer directory contents")
            if transactions.exists():
                if transactions.is_symlink() or not transactions.is_dir() or any(transactions.iterdir()):
                    raise ValueError("unfinished native transaction")
                transactions.rmdir()
            lock.unlink()
            directory.rmdir()
    put(output / "native-bindings.json", json_text(bindings))
    # Canonical inventory excludes only advisory writer/cache implementation artifacts.
    files = {path.relative_to(output).as_posix(): {"sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
             for path in sorted(output.rglob("*")) if path.is_file() and ".whykit" not in path.relative_to(output).parts}
    # Phase1 path strings were illustrative; native materialization follows the approved singular directory.
    mapping = {consumer["path"]: identities["A"][workflow["id"]]["consumers"][consumer["id"]]
               for workflow in oracle["workflows"] for consumer in workflow["consumers"]}
    manifests = {"format": "whykit.synthetic-impact-corpus/v1", "oracle_sha256": digest(oracle_raw),
                 "core_logical_entities": 28, "core_consumer_judgments": 72, "supplemental_consumer_judgments": 36,
                 "base_snapshots": {key: {"vault": path.relative_to(output).as_posix(), "identities": identities[key]}
                                    for key, path in bases.items()},
                 "cases": cases, "uncertainty_controls": controls,
                 "phase1_draft_path_to_native": mapping,
                 "native_outcome_contract": "Unchanged: baseline.claim pins C; decision_ref is unversioned identity; implementation_refs opaque; no synthetic task pin",
                 "native_creation_steps": writer.steps, "canonical_files": files,
                 "canonical_tree_sha256": digest(json_text(files).encode()),
                 "physical_file_count": len(files),
                 "count_scope": "28 logical entities per base, two bases plus12event snapshots and4uncertainty snapshots; value-only controls add nativeC/nativeD/task outside core28; physical files/versions separately inventoried"}
    return manifests



def verify_stored_dependencies(vault: Path) -> int:
    """Inspect stored declarations/pins, without an impact parser or ranking rule."""
    decisions = {load_note(path).front["decision_id"]: path for path in (vault / "06-decisions").glob("d-*-*.md")}
    count = 0
    paths = list((vault / "00-context/implementation").rglob("*.md")) + list(decisions.values())
    for path in paths:
        note = load_note(path)
        producer = note.front.get("provenance")
        if not isinstance(producer, dict):
            raise ValueError("missing stored provenance")
        scalar = producer.get("whykit_impact")
        if not isinstance(scalar, str) or len(scalar.encode("utf-8")) > 16384:
            raise ValueError("typed dependencies must be a bounded namespaced JSON scalar")
        declaration = json.loads(scalar)
        if (not isinstance(declaration, dict) or set(declaration) != {"format", "artifact_kind", "dependencies"}
                or declaration["format"] != "whykit.impact-dependencies/v1"):
            raise ValueError("wrong stored namespaced declaration")
        if path.is_relative_to(vault / "00-context/implementation"):
            kind = declaration["artifact_kind"]
            if kind not in {"spec", "pull-request", "task"} or note.front["sensitivity"] != "restricted":
                raise ValueError("wrong native card floor/marker")
            if note.front["type"] != ("specification" if kind == "spec" else "reference"):
                raise ValueError("wrong native card type")
            for component in path.relative_to(vault / "00-context/implementation").parts:
                slug = component[:-3] if component.endswith(".md") else component
                if len(slug) > 64 or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) is None:
                    raise ValueError("noncanonical native card path")
        elif declaration["artifact_kind"] is not None:
            raise ValueError("native D must have null artifact kind")
        entries = declaration["dependencies"]
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError("typed dependency count")
        for entry in entries:
            if set(entry) != {"relation", "target", "pin"} or entry["relation"] not in {"requires", "implements", "measures"}:
                raise ValueError("typed entry keys/relation")
            pin = entry["pin"]
            if not isinstance(pin, dict) or set(pin) != {"namespace", "sha256"} or re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]) is None:
                raise ValueError("typed pin")
            kind, target = entry["target"].split(":", 1)
            if kind == "claim" and pin["namespace"] == "claim_markdown_sha256":
                raw = (vault / "00-context/claims" / (target.lower() + ".md")).read_bytes()
                actual = digest(raw)
            elif kind == "decision" and pin["namespace"] == "decision_record_sha256":
                actual = approval_record_hash(decisions[target].read_text(encoding="utf-8"))
            elif kind == "document" and pin["namespace"] == "document_raw_sha256":
                relative = Path(target)
                if relative.is_absolute() or ".." in relative.parts or "\\" in target:
                    raise ValueError("nonlocal document target")
                actual = digest((vault / relative).read_bytes())
            else:
                raise ValueError("unsupported stored fixture target/namespace")
            if actual != pin["sha256"]:
                raise ValueError("stored dependency pin does not match actual native bytes")
            count += 1
    return count


def verify_corpus(output: Path, manifest: dict) -> dict:
    """Strict existing native parsers/anchors/receipts; no future assessment engine."""
    from whykit.compiled_context import _capture
    from whykit.claim_provenance import verify_anchor
    checks = []
    roots = [("base-" + key, output / value["vault"]) for key, value in manifest["base_snapshots"].items()]
    roots += [(name, output / value["vault"]) for name, value in manifest["cases"].items()]
    roots += [(name, output / value["vault"]) for name, value in manifest["uncertainty_controls"].items()]
    oracle = json.loads((HERE / "oracle-v1.json").read_text(encoding="utf-8"))
    default_ids = manifest["base_snapshots"]["A"]["identities"]
    for name, vault in roots:
        stored_pins = verify_stored_dependencies(vault)
        for workflow in oracle["workflows"]:
            ids = default_ids[workflow["id"]]
            for consumer in workflow["consumers"]:
                config = vault / ids["configs"][consumer["id"]]
                raw_note = (vault / ids["consumers"][consumer["id"]]).read_bytes()
                if ("functional-config-sha256:" + digest(config.read_bytes())).encode() not in raw_note:
                    raise ValueError("functional config is not bound into actual native consumer bytes")
        claims = sorted((vault / "00-context/claims").glob("c-*.md"))
        outcomes = sorted((vault / "00-context/outcomes").glob("o-*.md"))
        decisions = sorted((vault / "06-decisions").glob("d-*-*.md"))
        for path in claims:
            claim = read_claim(vault, path.stem.upper(), allowed_sensitivities=LABELS)
            if claim is None:
                raise ValueError("native C missing")
            for link in claim["payload"]["links"]:
                raw = read_artifact(vault, link["evidence_id"], link["source_version"], allowed_sensitivities=LABELS)
                if raw is None or verify_anchor(link, raw) != "matched":
                    raise ValueError("native fragment pin/anchor mismatch")
        log = (vault / "00-context/review-log.md").read_text(encoding="utf-8")
        for path in decisions:
            note = load_note(path)
            if not _approval_event_matches(note.text, path.relative_to(vault).as_posix(), None, log):
                raise ValueError("native approval receipt mismatch: " + name + ":" + path.name)
        for path in outcomes:
            out = read_outcome(vault, path.stem.upper(), allowed_sensitivities=LABELS)
            if out is None:
                raise ValueError("native O missing")
        visible, rows, _, hashes, states, reviews = _capture(vault.resolve(), LABELS, None, AS_OF)
        metadata = context_metadata(visible, reviews[0], reviews[1], reviews[2], rows, states, load_config(vault)[0], AS_OF, hashes)
        checks.append({"snapshot": name, "claims": len(claims), "outcomes": len(outcomes), "matched_decision_receipts": len(decisions), "stored_native_dependency_pins": stored_pins,
                       "decision_premises": sum(value["applicability"]["premise"] for value in metadata.values()),
                       "descriptive_decisions_retained": sum(value["decision_id"] is not None for value in metadata.values()),
                       "claim_source_states": {key: sorted(value) for key, value in states.items()}})
    bindings = json.loads((output / "native-bindings.json").read_text(encoding="utf-8"))
    v = validator()
    result = v.evaluate(json.loads((HERE / "oracle-v1.json").read_text(encoding="utf-8")), bindings, output.resolve())
    return {"format": "whykit.synthetic-impact-verification/v1", "native_checks": checks,
            "existing_native_receipt_checks_completed": True,
            "functional_oracle": result, "independent_external_review": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--creation-log", type=Path)
    parser.add_argument("--verification", type=Path)
    args = parser.parse_args()
    with args.creation_log.open("x", encoding="utf-8") if args.creation_log else __import__("contextlib").nullcontext() as log:
        manifest = generate(args.destination, creation_log=log)
    put(args.manifest, json_text(manifest))
    if args.verification:
        put(args.verification, json_text(verify_corpus(args.destination.resolve(), manifest)))
