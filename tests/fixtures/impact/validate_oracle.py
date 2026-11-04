"""Trusted synthetic behavior oracle. NOT EXECUTED at this design freeze.

Never imports an impact/graph/ranker, evaluates note code, calls a provider or
modifies a vault. Native materialization/writer evidence is a separate gate.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def behavior(profile: str, value: object) -> dict:
    """Real bounded fixture operations; relevance labels/edges are not consulted."""
    if profile == "retry.primary":
        if type(value) is not int or not 1 <= value <= 8:
            raise ValueError("invalid attempt cap")
        for attempt in range(1, value + 1):
            if attempt == 4:  # Fake transport: three failures, then a success.
                return {"calls": attempt, "result": "success"}
        return {"calls": value, "result": "exhausted"}
    if profile == "retry.secondary":
        if type(value) is not int or value <= 0:
            raise ValueError("invalid timeout")
        return {"deadline_ms": value, "reply_at_ms": 1000, "accepted": 1000 <= value}
    text = "ż" * 26 + "x"
    envelope = json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if profile == "budget.primary":
        if type(value) is not int or value <= 0:
            raise ValueError("invalid byte limit")
        wire = envelope + b"\n"
        return {"wire_bytes": len(wire), "accepted": len(wire) <= value}
    if profile == "budget.secondary":
        if type(value) is not bool:
            raise ValueError("invalid LF contract")
        wire = envelope + (b"\n" if value else b"")
        return {"wire_bytes": len(wire), "newline": wire.endswith(b"\n"),
                "valid_json": json.loads(wire.decode("utf-8")) == {"text": text}}
    raise ValueError("unknown trusted behavior profile")


def bound_bytes(root: Path, record: dict) -> bytes:
    """Read only declared synthetic relative files; hashes bind actual artifacts."""
    relative = Path(record["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("nonlocal fixture path")
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("unavailable fixture artifact")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("fixture artifact too large")
    raw = path.read_bytes()
    if digest(raw) != record["sha256"]:
        raise ValueError("native artifact hash mismatch")
    return raw


def source_bytes(workflow: dict, key: str, native: dict | None, root: Path | None) -> bytes:
    frozen = workflow["sources"][key]
    expected = frozen["utf8"].encode("utf-8")
    if native is None:
        raw = expected
    else:
        envelope = json.loads(bound_bytes(root, native["captures"][workflow["id"]][key]))
        if envelope["evidence_id"] != workflow["evidence_id"]:
            raise ValueError("wrong evidence identity")
        raw = base64.b64decode(envelope["content_base64"], validate=True)
        if envelope["source_version"] != frozen["source_version"] or envelope["size_bytes"] != len(raw):
            raise ValueError("wrong captured version")
    if raw != expected or "sha256:" + digest(raw) != frozen["source_version"]:
        raise ValueError("source does not match frozen exact bytes")
    return raw


def decision_transition(event: dict, native: dict | None, root: Path | None) -> dict:
    if event["root"] != "approved_decision":
        return {"state": "not_a_D_root"}
    if native is None:
        return {"state": "native_approval_not_materialized"}
    # Existing canonical digest only; never an impact/graph or new semantic hash.
    from whykit.immutability import approval_record_hash
    from whykit.lint import load_note
    item = native["decision_transitions"][event["id"]]
    before_raw = bound_bytes(root, item["before"])
    after_raw = bound_bytes(root, item["after"])
    before = load_note(root / item["before"]["path"], text=before_raw.decode("utf-8"))
    after = load_note(root / item["after"]["path"], text=after_raw.decode("utf-8"))
    a, b = approval_record_hash(before.text), approval_record_hash(after.text)
    if a != item["before"]["decision_record_sha256"] or b != item["after"]["decision_record_sha256"]:
        raise ValueError("wrong decision-record digest namespace")
    if event["id"].endswith("lifecycle-only"):
        if a != b or before_raw == after_raw or before.front["decision_id"] != after.front["decision_id"]:
            raise ValueError("lifecycle negative must change raw bytes but preserve governing record")
    else:
        if a == b or before.front["decision_id"] == after.front["decision_id"] or after.front["status"] != "approved":
            raise ValueError("supersession requires distinct genuinely approved successor")
        old_after_raw = bound_bytes(root, item["old_after"])
        if approval_record_hash(old_after_raw.decode("utf-8")) != a:
            raise ValueError("old approved reasoning was rewritten")
    return {"state": "digests_checked; writer_receipt_validation_separate", "before": a, "after": b,
            "raw_before": digest(before_raw), "raw_after": digest(after_raw)}


def consumer_config(workflow: dict, consumer: dict, event: dict, native: dict | None, root: Path | None):
    if native is None:
        return consumer.get("draft_settings_" + event["config_snapshot"])
    item = native["consumers"][event["id"]][consumer["id"]]
    bound_bytes(root, item["note"])  # Pin native card/Outcome bytes separately from executable fixture data.
    config = json.loads(bound_bytes(root, item["functional_config"]))
    if config["profile"] != consumer["functional_profile"]:
        raise ValueError("wrong functional consumer identity")
    return config.get("value")


def evaluate(oracle: dict, native: dict | None = None, root: Path | None = None) -> dict:
    workflows = {w["id"]: w for w in oracle["workflows"]}
    rows = []
    for supplemental, events in ((False, oracle["events"]), (True, oracle["supplemental_events"])):
        for event in events:
            workflow = workflows[event["workflow"]]
            # Exact changed root versions and selected input version are separate axes.
            for key in {event["before"], event["after"], event["selected_before"], event["selected_after"]}:
                source_bytes(workflow, key, native, root)
            before = json.loads(source_bytes(workflow, event["selected_before"], native, root))
            after = json.loads(source_bytes(workflow, event["selected_after"], native, root))
            transition = decision_transition(event, native, root)
            for consumer in workflow["consumers"]:
                if consumer["kind"] == "historical-outcome":
                    if native is not None:
                        item = native["consumers"][event["id"]][consumer["id"]]
                        if bound_bytes(root, item["note_before"]) != bound_bytes(root, item["note"]):
                            raise ValueError("historical Outcome bytes were rewritten")
                        config = json.loads(bound_bytes(root, item["functional_config"]))
                        if config.get("record") != consumer["historical_record"]:
                            raise ValueError("wrong frozen historical record")
                    old = expected_before = expected_after = consumer["historical_record"]
                    repair = None
                else:
                    key = workflow["fields"][consumer["branch"]]
                    setting = consumer_config(workflow, consumer, event, native, root)
                    old = behavior(consumer["functional_profile"], setting)
                    expected_before = behavior(consumer["functional_profile"], before[key])
                    expected_after = behavior(consumer["functional_profile"], after[key])
                    repair = {"value": after[key], "output": behavior(consumer["functional_profile"], after[key])}
                if old != expected_before:
                    raise ValueError("invalid fixture baseline: " + consumer["id"])
                affected = old != expected_after
                expected = consumer["id"] in event["expected_affected"]
                if not expected and consumer["id"] not in event["expected_unaffected"]:
                    raise ValueError("missing explicit oracle label")
                if affected != expected:
                    raise ValueError("frozen functional judgment mismatch: " + event["id"] + ":" + consumer["id"])
                rows.append({"event": event["id"], "consumer": consumer["id"], "kind": consumer["kind"],
                             "supplemental": supplemental, "affected": affected, "before_output": old,
                             "after_output_before_repair": old, "required_after_output": expected_after,
                             "minimal_repair": repair if affected else None, "decision_transition": transition,
                             "reason": workflow["functional_reason"] if affected else "Selected consumer operation unchanged; historical records remain historical"})
    core = [r for r in rows if not r["supplemental"]]
    extra = [r for r in rows if r["supplemental"]]
    if (len(core), sum(r["affected"] for r in core), len(extra), sum(r["affected"] for r in extra)) != (72, 8, 36, 8):
        raise ValueError("fixed oracle denominators changed")
    return {"format": "whykit.impact-functional-oracle-result/v1", "rows": rows,
            "core_judgments": 72, "core_affected": 8, "core_known_unaffected": 64,
            "supplemental_judgments": 36, "supplemental_affected": 8, "supplemental_known_unaffected": 28,
            "native_artifact_bytes_checked": native is not None,
            "native_writer_receipts_independently_verified": False,
            "ranker_evaluated": False, "execution_authorized": False,
            "unexecuted_assessment_controls": oracle["separate_uncertainty_and_security_controls"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, default=HERE / "oracle-v1.json")
    parser.add_argument("--native-bindings", type=Path)
    parser.add_argument("--native-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.oracle.read_bytes()
    native = json.loads(args.native_bindings.read_text(encoding="utf-8")) if args.native_bindings else None
    if bool(args.native_bindings) != bool(args.native_root):
        parser.error("native bindings/root are a pair")
    if native is not None and (native.get("oracle_sha256") != digest(raw) or native.get("fully_synthetic_local_scope") is not True):
        parser.error("native bindings must match oracle and explicit synthetic local scope")
    result = evaluate(json.loads(raw), native, args.native_root.resolve(strict=True) if args.native_root else None)
    result["oracle_sha256"] = digest(raw)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
