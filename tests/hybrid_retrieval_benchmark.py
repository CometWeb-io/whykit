"""Offline synthetic M09 harness. Measurements require an explicit frozen-input hash.

Corpus generation/validation do not call retrieval. A locked partition here is
still author-designed regression data, not independent gold or real-model proof.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURE = ROOT / "tests/fixtures/hybrid-retrieval"
ARMS = ("A", "B", "C", "D")


def wire(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(name: str) -> dict:
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def metrics(paths: list[str], relevance: dict[str, int], k: int) -> dict:
    """Duplicates consume ranks, earn no repeated gain; zero gold is not perfect."""
    if type(k) is not int or k < 1 or any(type(v) is not int or not 0 <= v <= 2 for v in relevance.values()):
        raise ValueError("invalid metric inputs")
    seen = set()
    selected = []
    for rank, path in enumerate(paths[:k]):
        if path not in seen:
            selected.append((rank, path))
            seen.add(path)
    positive = {path for path, grade in relevance.items() if grade > 0}
    hits = len({path for _, path in selected} & positive)
    dcg = sum((2 ** relevance.get(path, 0) - 1) / math.log2(rank + 2) for rank, path in selected)
    ideal = sum((2 ** grade - 1) / math.log2(rank + 2)
                for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:k]))
    return {"recall": hits / len(positive) if positive else None,
            "ndcg": dcg / ideal if ideal else None,
            "precision": hits / k,
            "false_positives": sum(path not in positive for _, path in selected),
            "duplicate_results": len(paths[:k]) - len(selected)}


def inputs_manifest() -> dict:
    """Hash every corpus byte and its generator/qrels/recipe before any arm runs."""
    names = ("topics.json", "qrels.json", "parameters.json", "generate.py", "corpus-manifest.json")
    files = {name: sha((FIXTURE / name).read_bytes()) for name in names}
    corpus = load("corpus-manifest.json")
    for relative, expected in corpus["files"].items():
        raw = (FIXTURE / "vault" / relative).read_bytes()
        if sha(raw) != expected["sha256"] or len(raw) != expected["bytes"]:
            raise ValueError("corpus bytes changed: " + relative)
    actual = {path.relative_to(FIXTURE / "vault").as_posix() for path in (FIXTURE / "vault").rglob("*")
              if path.is_file() and ".whykit" not in path.relative_to(FIXTURE / "vault").parts}
    if actual != set(corpus["files"]):
        raise ValueError("unexpected or missing canonical corpus file")
    qrels = load("qrels.json")
    if len(qrels["positive_intents"]) != 24 or len(qrels["negative_controls"]) != 8:
        raise ValueError("wrong fixed case counts")
    if (corpus["semantic_records"], corpus["rankable_documents"], corpus["evidence_intermediaries"]) != (48, 42, 6):
        raise ValueError("wrong fixed ontology")
    for case in qrels["positive_intents"]:
        if case["partition"] != corpus["topics"][case["topic"]]["partition"]:
            raise ValueError("partition mismatch")
        for identity in case["relevance"]:
            topic, kind = identity.split(":")
            if kind not in corpus["topics"][topic]["paths"]:
                raise ValueError("qrels identity is not a rankable note")
    # Native writers and templates are generation dependencies, not measured algorithms.
    sources = ["artifact_store", "claim_ledger", "outcome_ledger", "review", "scaffold", "placeholders"]
    generation_sources = {f"src/whykit/{name}.py": sha((ROOT / f"src/whykit/{name}.py").read_bytes()) for name in sources}
    for relative in ("00-context/evidence-register.md", "06-decisions/decision-log.md"):
        generation_sources["src/whykit/template/" + relative] = sha((ROOT / "src/whykit/template" / relative).read_bytes())
    bound = {"files": files, "canonical_tree_sha256": corpus["canonical_tree_sha256"],
             "generation_sources": generation_sources}
    return {"format": "whykit.synthetic-hybrid-input-freeze/v1", **bound, "sha256": sha(wire(bound))}


def load_generator():
    spec = importlib.util.spec_from_file_location("whykit_synthetic_hybrid_generator", FIXTURE / "generate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_corpus(destination: Path, scale: int = 0) -> dict:
    corpus = load("corpus-manifest.json")
    shutil.copytree(FIXTURE / "vault", destination, ignore=shutil.ignore_patterns(".whykit"))
    if scale not in (0, 1000, 20000):
        raise ValueError("unsupported fixed scale tier")
    if scale:
        writer = load_generator().write_note
        for number in range(scale):
            writer(destination / f"scale/item-{number:05}.md", f"Unrelated filler {number:05}",
                   "A stationary lantern illuminates an empty garden. No task-specific process is described.")
    return corpus


def prepared(root: Path, case: dict, *, scope: list[str] | None = None, omit_evidence: str | None = None) -> dict:
    """One cooperating read transaction; never inherit persistent cache writes."""
    from whykit.io import vault_read_lock
    from whykit.lint import path_cache
    from whykit.vault_index import NoteCache, reuse_notes
    with vault_read_lock(root), reuse_notes(NoteCache()), path_cache(fresh=True):
        return _prepared(root, case, scope=scope, omit_evidence=omit_evidence)


def _prepared(root: Path, case: dict, *, scope: list[str] | None = None, omit_evidence: str | None = None) -> dict:
    """One captured canonical permission view shared by every arm; no rank calls."""
    from whykit.compiled_context import _capture
    from whykit.config import load_config
    from whykit.context_applicability import context_metadata
    from whykit.sensitivity import classified_evidence
    from whykit.vault_index import VaultIndex
    params = load("parameters.json")
    allowed = set(scope if scope is not None else case.get("allowed_sensitivities", params["allowed_sensitivities"]))
    today = dt.date.fromisoformat(case.get("valid_on", params["valid_on"]))
    corpus = load("corpus-manifest.json")
    semantics = {path for topic in corpus["topics"].values() for path in topic["paths"].values()}
    backing = {"00-context/evidence-register.md", "00-context/review-log.md", "06-decisions/decision-log.md"}
    index = VaultIndex.load(root)
    grant = index.subset(lambda note: index.relative(note.path) in semantics | backing
                         or index.relative(note.path).startswith(("scale/", "private-controls/")))
    active, retired, occurrences, _ = classified_evidence(grant)
    if omit_evidence is not None:
        active.pop(omit_evidence, None)
        retired.pop(omit_evidence, None)
    grant.derived["evidence_register"] = (active, retired, occurrences)
    visible, rows, _snapshot, hashes, claim_states, review = _capture(root.resolve(), allowed, grant, today)
    metadata = context_metadata(visible, review[0], review[1], review[2], rows, claim_states, load_config(root)[0], today, hashes)
    visible.derived["evidence_register"] = rows
    return {"index": visible, "rows": rows, "metadata": metadata, "document_hashes": hashes,
            "valid_on": today, "allowed_sensitivities": allowed, "params": params}


def run_arm(arm: str, state: dict, case: dict, *, limit: int = 5) -> dict:
    from whykit.query import query_vault
    from whykit.retrieval import retrieve_bm25
    index = state["index"]
    if arm == "A":
        return query_vault(index.root, text=case["text"], limit=limit, vault=index)
    if arm == "B":
        return {"status": "ranked", "results": retrieve_bm25(index.root, case["text"], limit=limit, vault=index)}
    if arm not in ("C", "D"):
        raise ValueError("invalid arm")
    from whykit.hybrid_retrieval import rank_prepared
    params = state["params"]
    return rank_prepared(index, case["text"], rows=state["rows"], metadata=state["metadata"],
                         document_hashes=state["document_hashes"], valid_on=state["valid_on"],
                         allowed_sensitivities=state["allowed_sensitivities"],
                         validity=case.get("validity", params["validity"]), decision_role=case["decision_role"], limit=limit,
                         seed_limit=params["seed_limit"], max_depth=0 if arm == "C" else params["max_depth"],
                         max_nodes=params["max_nodes"], max_edges=params["max_edges"])


def qrel_paths(case: dict, corpus: dict) -> dict[str, int]:
    return {corpus["topics"][topic]["paths"][kind]: grade
            for identity, grade in case["relevance"].items() for topic, kind in [identity.split(":")]}


def percentiles(samples: list[float]) -> dict:
    return {"samples": len(samples), "median_seconds": statistics.median(samples),
            "p95_seconds": sorted(samples)[math.ceil(0.95 * len(samples)) - 1], "max_seconds": max(samples)}


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def guarded_read_counts():
    """Measure actual bytes returned by existing guarded reads, not all OS I/O."""
    from whykit import artifact_store, compiled_context
    stack = ExitStack()
    counts = {"guarded_file_opens": 0, "guarded_bytes_returned": 0,
              "scope": "Existing compiled_context/artifact_store open_regular_binary reads; initial index text parsing and OS physical I/O not included"}
    class Handle:
        def __init__(self, handle):
            self.handle = handle
        def __getattr__(self, key):
            return getattr(self.handle, key)
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def read(self, *args):
            raw = self.handle.read(*args)
            counts["guarded_bytes_returned"] += len(raw)
            return raw
    for module in (artifact_store, compiled_context):
        original = module.open_regular_binary
        def opened(*args, _original=original, **kwargs):
            counts["guarded_file_opens"] += 1
            return Handle(_original(*args, **kwargs))
        stack.enter_context(patch.object(module, "open_regular_binary", side_effect=opened))
    return stack, counts



def runtime_fingerprint() -> dict:
    paths = sorted((ROOT / "src/whykit").rglob("*.py"))
    paths += [Path(__file__).resolve(), ROOT / "tests/test_hybrid_retrieval_metrics.py"]
    files = {path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in paths}
    return {"files": files, "sha256": sha(wire(files))}


def worker(root: Path, case: dict, arm: str) -> dict:
    import importlib
    for name in ("query", "retrieval", "hybrid_retrieval"):
        importlib.import_module("whykit." + name)
    resource_module = importlib.import_module("resource") if importlib.util.find_spec("resource") else None
    runtime_before = runtime_fingerprint()
    stack, reads = guarded_read_counts()
    baseline = resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss if resource_module else None
    started = time.perf_counter()
    with stack:
        state = prepared(root, case)
        report = run_arm(arm, state, case)
        payload = wire(report)
    seconds = time.perf_counter() - started
    peak = resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss if resource_module else None
    if runtime_fingerprint() != runtime_before:
        raise ValueError("runtime source changed during fresh-process measurement")
    unit = 1024 * 1024 if sys.platform == "darwin" else 1024
    return {"runtime_sha256": runtime_before["sha256"], "seconds": seconds, "bytes_serialized_including_lf": len(payload), "report_sha256": sha(payload),
            "status": report.get("status", "ranked"), "reason": report.get("reason"), "stats": report.get("stats"),
            "rss_peak_mib": peak / unit if peak is not None else None,
            "rss_baseline_peak_mib": baseline / unit if baseline is not None else None,
            "rss_high_water_increment_mib": (peak - baseline) / unit if peak is not None and baseline is not None else None,
            "rss_scope": "Fresh-process ru_maxrss high-water increase after imports; not allocated bytes or current RSS", **reads}


def rank_benchmark(root: Path, corpus: dict, repeats: int, *, performance_only: bool = False) -> dict:
    cases = load("qrels.json")
    results = []
    chosen = cases["positive_intents"] + cases["negative_controls"]
    if performance_only:
        chosen = [case for case in chosen if case["id"] in load("parameters.json")["performance_cases"]]
    for case in chosen:
        state = prepared(root, case)
        outputs: dict[str, dict] = {}
        samples: dict[str, list[float]] = {arm: [] for arm in ARMS}
        for iteration in range(repeats + 1):
            order = ARMS[iteration % 4:] + ARMS[:iteration % 4]
            for arm in order:
                started = time.perf_counter()
                report = run_arm(arm, state, case)
                elapsed = time.perf_counter() - started
                if iteration:
                    samples[arm].append(elapsed)
                paths = [item["path"] for item in report["results"]]
                output = {"paths": paths, "status": report.get("status", "ranked"), "reason": report.get("reason"),
                          "metrics": {str(k): metrics(paths, qrel_paths(case, corpus), k) for k in (3, 5)},
                          "result_sha256": sha(wire(report)), "stats": report.get("stats"), "truncation": report.get("truncation"),
                          "results": report["results"]}
                if arm in outputs and outputs[arm] != output:
                    raise ValueError("nondeterministic output: " + case["id"] + ":" + arm)
                outputs[arm] = output
        for arm in ARMS:
            outputs[arm]["warm_timing"] = percentiles(samples[arm])
        graph_added = set(outputs["D"]["paths"]) - set(outputs["C"]["paths"])
        gold = qrel_paths(case, corpus)
        results.append({"id": case["id"], "partition": case["partition"], "qrels": gold,
                        "positive_intent": bool(case["relevance"]), "expected_empty": case.get("expect_empty"),
                        "graph_added_relevant": sorted(p for p in graph_added if gold.get(p, 0) > 0),
                        "graph_added_grade_zero": sorted(p for p in graph_added if gold.get(p, 0) == 0),
                        "unsafe_premises": {arm: [item["path"] for item in outputs[arm]["results"]
                            if case["decision_role"] == "premise" and item.get("type") == "decision"
                            and not item.get("applicability", {}).get("premise")] for arm in ("C", "D")}, **outputs})
    summaries = {}
    for partition in ("development", "locked", "all"):
        selected = [r for r in results if r["positive_intent"] and (partition == "all" or r["partition"] == partition)]
        summaries[partition] = {arm: {f"{metric}_at_{k}": statistics.mean(r[arm]["metrics"][str(k)][metric] for r in selected)
                                     for k in (3, 5) for metric in ("recall", "ndcg", "precision")} for arm in ARMS}
    losses = [{"id": r["id"], "k": k, "metric": metric, "C": r["C"]["metrics"][str(k)][metric], "D": r["D"]["metrics"][str(k)][metric]}
              for r in results if r["positive_intent"] for k in (3, 5) for metric in ("recall", "ndcg")
              if r["D"]["metrics"][str(k)][metric] < r["C"]["metrics"][str(k)][metric]]
    return {"quality_cases_complete": not performance_only, "summary": summaries, "C_to_D_losses": losses, "cases": results}




def paired_coverage(checks: dict) -> dict:
    """Map the nine frozen scenario families without inventing nine local PASSes."""
    references = {
        "expired_previous_and_unapproved_future_successor": [
            "tests/test_context_applicability_boundaries.py::test_future_or_draft_successor_relation_does_not_itself_supersede_a_current_predecessor",
            "tests/test_context_applicability_boundaries.py::test_persisted_superseded_predecessor_remains_nonbinding_with_successor_outside_grant"],
        "source_A_B_A_and_wrong_pinned_version": [
            "tests/test_hybrid_retrieval_boundaries.py::test_changed_pinned_source_never_remains_a_current_decision_premise",
            "tests/test_claim_provenance.py::test_raw_version_precedes_fragment_and_never_normalizes"],
        "cycle_diamond_navigation_false_positives": [
            "tests/test_hybrid_retrieval_boundaries.py::test_diamond_and_cycles_do_not_multiply_graph_support"],
        "construction_refusal_no_partial_corpus": [
            "tests/test_hybrid_retrieval_boundaries.py::test_construction_limits_refuse_before_graph_document_materialization"]}
    local = {
        "denied_high_tf_notes_and_hidden_hub": "hidden_insert_public_output_and_hash",
        "public_and_sparse_evidence_row_grants": "sparse_grant_omitted_evidence",
        "half_open_start_end_and_unknown": "half_open_unknown_and_governance",
        "source_A_B_A_and_wrong_pinned_version": "source_A_B_A_pin_unchanged",
        "captured_A_disk_B": "captured_A_disk_B",
        "read_only_inherited_persistent_cache": "inherited_persistent_cache_no_writes"}
    result = {}
    for family in load("qrels.json")["paired_controls"]:
        check = local.get(family)
        result[family] = {"harness_check": check, "harness_passed": checks[check]["passed"] if check else None,
                          "coverage": "partial" if family == "source_A_B_A_and_wrong_pinned_version" else "harness" if check else "external_test_reference_only",
                          "external_tests": references.get(family, []), "external_tests_executed_by_harness": False}
    result["source_A_B_A_and_wrong_pinned_version"]["limitation"] = "Local check tests changed latest version with immutable pin and explicit A return; no forged wrong-version identity is exercised here. Related external verification tests are references, not an invented local PASS."
    result["cycle_diamond_navigation_false_positives"]["limitation"] = "Navigation relevance is measured in graded cases; cycle/diamond invariance belongs to the separately run boundary suite."
    return result


def run_controls() -> dict:
    """Paired synthetic mutations, separate from relevance means and independent N."""
    from whykit.artifact_store import capture_artifact
    from whykit.parse_cache import persistent
    corpus = load("corpus-manifest.json")
    first = corpus["topics"]["retry"]
    case = next(c for c in load("qrels.json")["positive_intents"] if c["id"] == "retry-choice")
    writer = load_generator().write_note
    outcomes = {}
    with tempfile.TemporaryDirectory(prefix="whykit-hybrid-controls-") as directory:
        base = Path(directory).resolve()
        root = base / "hidden"
        copy_corpus(root)
        before = run_arm("D", prepared(root, case, scope=["public"]), case, limit=100)
        marker = "DENIED_CORPUS_CANARY_17"
        for number in range(20):
            writer(root / f"private-controls/hidden-{number:02}.md", marker,
                   (case["text"] + " " + marker + " ") * 40, sensitivity="restricted")
        writer(root / "private-controls/hidden-hub.md", marker,
               " ".join(f"[[{t['decision_id']}]]" for t in corpus["topics"].values()), sensitivity="restricted")
        original_sha = hashlib.sha256
        def no_hidden_hash(raw=b"", *args, **kwargs):
            if marker.encode() in raw:
                raise AssertionError("denied raw payload reached SHA256")
            return original_sha(raw, *args, **kwargs)
        with patch.object(hashlib, "sha256", side_effect=no_hidden_hash):
            after = run_arm("D", prepared(root, case, scope=["public"]), case, limit=100)
        assert wire(before) == wire(after), "denied content changed public output/statistics"
        assert not any(item["path"].startswith(("00-context/claims/", "00-context/outcomes/")) for item in after["results"])
        outcomes["hidden_insert_public_output_and_hash"] = {"passed": True, "sha256": sha(wire(before))}
        root = base / "sparse"
        copy_corpus(root)
        sparse = prepared(root, case, scope=["public", "restricted"], omit_evidence=first["evidence_id"])
        report = run_arm("D", sparse, case, limit=100)
        denied = {first["paths"]["claim"], first["paths"]["outcome"], first["paths"]["decision"]}
        assert not denied.intersection(item["path"] for item in report["results"]), "omitted E-row grant widened"
        outcomes["sparse_grant_omitted_evidence"] = {"passed": True, "omitted_evidence": first["evidence_id"]}
        root = base / "dates"
        copy_corpus(root)
        for day, excluded in (("2026-09-30", True), ("2026-10-01", False), ("2027-01-01", True)):
            dated = {**case, "valid_on": day}
            report = run_arm("D", prepared(root, dated), dated, limit=100)
            exists = first["paths"]["decision"] in {item["path"] for item in report["results"]}
            assert exists != excluded, "half-open/current governance control failed: " + day
        strict = {**case, "validity": "active"}
        report = run_arm("D", prepared(root, strict), strict, limit=100)
        assert all(item["validity"]["state"] == "active" for item in report["results"])
        outcomes["half_open_unknown_and_governance"] = {"passed": True}
        root = base / "source"
        copy_corpus(root)
        bound_paths = [root / first["paths"][key] for key in ("claim", "decision")]
        bound_paths.append(root / "00-context/review-log.md")
        bound = {p: p.read_bytes() for p in bound_paths}
        original_source = (root / ".inputs/source-01.txt").read_bytes()
        source = root / ".inputs/change.txt"
        source.write_bytes(b"A different explicitly observed source version.\n")
        for raw, stamp, current in ((source.read_bytes(), "2026-10-27T10:00:00Z", False),
                                    (original_source, "2026-10-27T12:00:00Z", True)):
            source.write_bytes(raw)
            plan = capture_artifact(root, first["evidence_id"], source, recorded_at=stamp)
            capture_artifact(root, first["evidence_id"], source, recorded_at=stamp,
                             write=True, expected_sha256=plan["expected_sha256"])
            report = run_arm("D", prepared(root, case), case, limit=100)
            present = first["paths"]["decision"] in {item["path"] for item in report["results"]}
            assert present == current, "pinned source currentness not applied"
            assert all(p.read_bytes() == raw_before for p, raw_before in bound.items()), "capture rewrote pinned claim/decision/receipt"
        outcomes["source_A_B_A_pin_unchanged"] = {"passed": True, "semantic_equivalence_checked": False}
        root = base / "snapshot"
        copy_corpus(root)
        state = prepared(root, case)
        report = run_arm("D", state, case, limit=100)
        target = root / first["paths"]["decision"]
        target.write_bytes(target.read_bytes() + b"\nUnreviewed later disk edit.\n")
        assert wire(run_arm("D", state, case, limit=100)) == wire(report), "prepared snapshot reopened later disk bytes"
        refreshed = run_arm("D", prepared(root, case), case, limit=100)
        assert first["paths"]["decision"] not in {item["path"] for item in refreshed["results"]}, "edited approval remained a premise"
        outcomes["captured_A_disk_B"] = {"passed": True}
        root = base / "readonly"
        copy_corpus(root)
        before_tree = tree(root)
        with persistent(True):
            run_arm("D", prepared(root, case), case)
        assert tree(root) == before_tree, "read path wrote cache/journal/source"
        outcomes["inherited_persistent_cache_no_writes"] = {"passed": True}
    return {"scope": "Six local paired development checks; not nine local tests or additional independent relevance cases",
            "checks": outcomes, "coverage": paired_coverage(outcomes)}


def fresh_process_benchmark(root: Path, repeats: int, freeze: str) -> list[dict]:
    qrels = load("qrels.json")
    cases = {case["id"]: case for case in qrels["positive_intents"]}
    rows = []
    for case_id in load("parameters.json")["performance_cases"]:
        for iteration in range(repeats + 1):
            for arm in ARMS[iteration % 4:] + ARMS[:iteration % 4]:
                command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--root", str(root),
                           "--case", case_id, "--arm", arm, "--expect-fixture-sha256", freeze]
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=180)
                if result.returncode:
                    raise RuntimeError("worker failed: " + result.stderr[-2000:])
                row = json.loads(result.stdout)
                if iteration:
                    rows.append({"case": case_id, "arm": arm, **row})
    assert all(case in cases for case in load("parameters.json")["performance_cases"])
    return rows



def decisions(ranking: dict, fresh: list[dict], scale: int, complete: bool) -> dict:
    """Report preregistered comparisons; no automatic default or milestone promotion."""
    cases = ranking["cases"]
    warm = [{"case": row["id"], "D_to_C_p95_ratio": row["D"]["warm_timing"]["p95_seconds"] / row["C"]["warm_timing"]["p95_seconds"],
             "D_p95_seconds": row["D"]["warm_timing"]["p95_seconds"]} for row in cases]
    fresh_summaries = []
    for case in sorted({row["case"] for row in fresh}):
        for arm in ARMS:
            selected = [row for row in fresh if row["case"] == case and row["arm"] == arm]
            if selected:
                fresh_summaries.append({"case": case, "arm": arm, **percentiles([row["seconds"] for row in selected]),
                    "max_rss_peak_mib": max(row["rss_peak_mib"] for row in selected) if all(row["rss_peak_mib"] is not None for row in selected) else None,
                    "max_rss_high_water_increment_mib": max(row["rss_high_water_increment_mib"] for row in selected) if all(row["rss_high_water_increment_mib"] is not None for row in selected) else None})
    locked = ranking["summary"]["locked"]
    deltas = {metric: {"D_minus_A": locked["D"][metric] - locked["A"][metric],
                       "D_minus_C": locked["D"][metric] - locked["C"][metric]}
              for metric in ("recall_at_5", "ndcg_at_5")}
    rss = [row["rss_high_water_increment_mib"] for row in fresh if row["arm"] == "D"]
    return {"locked_deltas": deltas,
            "original_metric_gain_on_this_fixture": ranking["quality_cases_complete"] and all(v["D_minus_A"] > 0 for v in deltas.values()),
            "incremental_graph_nonregression": all(v["D_minus_C"] >= 0 for v in deltas.values()),
            "incremental_graph_positive_metric": any(v["D_minus_C"] > 0 for v in deltas.values()),
            "warm_per_case": warm,
            "warm_ratio_gate": all(row["D_to_C_p95_ratio"] <= 2 for row in warm),
            "warm_absolute_gate": all(row["D_p95_seconds"] <= 1 for row in warm),
            "fresh_process_summary": fresh_summaries,
            "fresh_process_gate": all(row["p95_seconds"] <= 10 for row in fresh_summaries if row["arm"] == "D") if complete and fresh_summaries else None,
            "rss_20k_gate": all(v <= 256 for v in rss) if scale == 20000 and complete and rss and all(v is not None for v in rss) else None,
            "scope": "Local preregistered engineering gates; synthetic fixture only; no automatic M09/default ranking promotion"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-inputs", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--expect-fixture-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scale", type=int, choices=(0, 1000, 20000), default=0)
    parser.add_argument("--skip-fresh-process", action="store_true", help="Exploratory only: report incomplete cost qualification")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    parser.add_argument("--arm", choices=ARMS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    frozen = inputs_manifest()
    if args.freeze_inputs:
        if args.run or args.worker:
            parser.error("freeze and measurement are separate operations")
        with args.freeze_inputs.open("xb") as handle:
            handle.write(wire(frozen))
        return
    if not (args.run or args.worker) or frozen["sha256"] != args.expect_fixture_sha256:
        parser.error("measurements require --run and matching --expect-fixture-sha256")
    if args.worker:
        cases = load("qrels.json")["positive_intents"]
        case = next(c for c in cases if c["id"] == args.case)
        print(wire(worker(args.root, case, args.arm)).decode(), end="")
        return
    if args.output is None or args.output.exists():
        parser.error("--output must name an absent result file")
    params = load("parameters.json")
    runtime_before = runtime_fingerprint()
    with tempfile.TemporaryDirectory(prefix="whykit-hybrid-benchmark-") as directory:
        root = Path(directory).resolve() / "vault"
        corpus = copy_corpus(root, args.scale)
        before = tree(root)
        controls = run_controls()
        ranking = rank_benchmark(root, corpus, params["repeats"], performance_only=bool(args.scale))
        fresh = [] if args.skip_fresh_process else fresh_process_benchmark(root, params["fresh_process_repeats"], frozen["sha256"])
        if tree(root) != before:
            raise ValueError("read-only benchmark changed the synthetic source tree")
    if runtime_fingerprint() != runtime_before or any(row["runtime_sha256"] != runtime_before["sha256"] for row in fresh):
        raise ValueError("runtime source changed across benchmark processes")
    report = {"format": "whykit.synthetic-hybrid-benchmark/v1", "scope": params["scope"], "fixture_freeze": frozen,
              "runtime_source": runtime_before,
              "python": platform.python_version(), "platform": platform.platform(), "scale_distractors": args.scale,
              "warm_timing_scope": "Already captured identical permitted index; filtering/ranking included, discovery/source capture/serialization excluded",
              "fresh_process_timing_scope": params["fresh_process_timing"], "fresh_process_complete": not args.skip_fresh_process,
              "fresh_process": fresh, "acceptance": decisions(ranking, fresh, args.scale, not args.skip_fresh_process), "paired_controls": controls, "source_unchanged": True, "external_calls": 0, "model_calls": 0, "spend": "0", **ranking}
    with args.output.open("xb") as handle:
        handle.write(wire(report))


if __name__ == "__main__":
    main()
