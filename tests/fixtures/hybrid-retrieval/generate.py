"""Native deterministic development corpus; generation never calls retrieval."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from whykit.artifact_store import capture_artifact
from whykit.claim_ledger import create_claim
from whykit.outcome_ledger import create_outcome
from whykit.placeholders import ALTERNATIVES_EMPTY_ROW, SCAFFOLD_SECTION_PROMPTS
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision, create_evidence

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
APPROVED = dt.date(2026, 9, 17)
AS_OF = dt.date(2026, 10, 27)
OWNER = "Ada Example"
ALL_LABELS = ["public", "internal", "confidential", "restricted"]


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_note(path: Path, title: str, body: str, *, kind: str = "reference", **extra) -> None:
    metadata = {"title": title, "aliases": [], "type": kind, "status": "draft", "owner": OWNER,
                "created": APPROVED.isoformat(), "last_updated": APPROVED.isoformat(),
                "source_of_truth": False, "sensitivity": "public", "source_ids": [], "tags": [], **extra}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
                     + "\n---\n\n" + body + "\n").encode("utf-8"))


def decision(root: Path, title: str, topic: dict, eid: str, *, old: bool, claim: dict,
             supersedes: str | None = None) -> tuple[str, Path]:
    did, path = create_decision(root, title, owner=OWNER, source_ids=[eid], sensitivity="public",
                                supersedes=supersedes, today=APPROVED)
    text = path.read_text(encoding="utf-8")
    replacements = {SCAFFOLD_SECTION_PROMPTS["Context"]: f"A synthetic team needs a {topic['words']} policy and operating procedure.",
                    SCAFFOLD_SECTION_PROMPTS["Decision"]: topic["wrong"] if old else topic["choice"],
                    SCAFFOLD_SECTION_PROMPTS["Rationale"]: f"This {topic['words']} choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.",
                    ALTERNATIVES_EMPTY_ROW: "| Unbounded operation | Simple | No safe limit | Fails the declared constraint |",
                    "### Positive\n\n-\n": "### Positive\n\n- A bounded and inspectable process.\n",
                    "### Negative and trade-offs\n\n-\n": "### Negative and trade-offs\n\n- Verification requires time.\n"}
    for before, after in replacements.items():
        assert text.count(before) == 1
        text = text.replace(before, after)
    for key, value in {"valid_from": "2026-01-01" if old else "2026-10-01",
                       "valid_until": "2026-10-01" if old else "2027-01-01"}.items():
        text = _frontmatter_replace(text, key, value)
    text = _frontmatter_replace(text, "aliases", json.dumps([did]))
    # Bind the source claim before approval. Public readers correctly cannot use its restricted support.
    text = _frontmatter_replace(text, "claim_ids", json.dumps([claim["claim_id"]]))
    pins = json.dumps({claim["claim_id"]: "sha256:" + digest((root / claim["path"]).read_bytes())}, separators=(",", ":"))
    text = _frontmatter_replace(text, "provenance", "\n  claim_versions: " + json.dumps(pins))
    path.write_bytes(text.encode("utf-8"))
    plan = approve_decision(root, did, reviewer=OWNER, today=APPROVED, next_review="2026-12-31")
    approve_decision(root, did, reviewer=OWNER, today=APPROVED, next_review="2026-12-31",
                     write=True, expected_sha256=plan["expected_sha256"])
    return did, path


def generate(root: Path) -> dict:
    """Create an absent synthetic vault; return identities and exact content inventory."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    write_note(root / "Home.md", "Synthetic corpus map", "Fixed development records; no external project data.")
    for relative in ("00-context/evidence-register.md", "06-decisions/decision-log.md"):
        source = REPO / "src/whykit/template" / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        for key, value in (("owner", OWNER), ("sensitivity", "public")):
            text = _frontmatter_replace(text, key, value)
        target.write_bytes(text.encode("utf-8"))
    (root / "whykit.toml").write_bytes(b'[evidence_access_age_days]\nfixture = 365\n')
    topics = json.loads((HERE / "topics.json").read_text(encoding="utf-8"))["topics"]
    identities = {}
    for number, topic in enumerate(topics, 1):
        eid = create_evidence(root, source=topic["label"] + " declaration", location=f"https://source.example/{topic['id']}",
                              kind="fixture", claims=topic["source"], sensitivity="public", today=APPROVED)
        raw = (topic["source"] + "\n").encode("utf-8")
        source = root / ".inputs" / f"source-{number:02}.txt"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(raw)
        stamp = "2026-09-17T10:00:00Z"
        plan = capture_artifact(root, eid, source, recorded_at=stamp)
        capture = capture_artifact(root, eid, source, recorded_at=stamp, write=True, expected_sha256=plan["expected_sha256"])
        link = {"evidence_id": eid, "source_version": capture["source_version"],
                "location": {"start_byte": 0, "end_byte": len(raw) - 1, "quote": topic["source"]},
                "assessment": {"kind": "human", "by": OWNER, "relation": "supports"}}
        args = {"title": topic["label"] + " source declaration", "owner": OWNER, "today": APPROVED,
                "valid_from": "2026-09-01", "valid_until": "2027-01-01"}
        plan = create_claim(root, topic["source"], [link], **args)
        claim = create_claim(root, topic["source"], [link], write=True, expected_sha256=plan["expected_sha256"], **args)
        old_id, old_path = decision(root, topic["label"] + " previous policy", topic, eid, old=True, claim=claim)
        did, current_path = decision(root, topic["label"] + " current policy", topic, eid, old=False,
                                     claim=claim, supersedes=old_id)
        record_review(root, did, reviewer=OWNER, outcome="confirmed", note_text="The declared process remains the reviewed choice.",
                      next_review="2026-12-31", today=dt.date(2026, 10, 1))
        sample_claim = {"claim_id": claim["claim_id"], "sha256": digest((root / claim["path"]).read_bytes())}
        data = {"decision_id": did,
                "metric": {"name": topic["metric"], "unit": topic["unit"], "definition": "Duration declared in this synthetic outcome; values are not verified by the source claim."},
                "baseline": {"value": topic["baseline"], "as_of": "2026-09-17", "claim": None},
                "target": {"value": topic["observed"], "by": "2026-10-01"},
                "observed": {"value": topic["observed"], "as_of": "2026-10-01", "claim": sample_claim},
                "implementation_refs": [f"https://change.example/{number:02}"]}
        args_o = {"owner": OWNER, "title": f"Measured duration window {number:02}", "today": dt.date(2026, 10, 1)}
        plan = create_outcome(root, data, **args_o)
        outcome = create_outcome(root, data, write=True, expected_sha256=plan["expected_sha256"], **args_o)
        spec = f"specifications/procedure-{number:02}.md"
        write_note(root / spec, f"Operating instructions {number:02}",
                   f"# Operating procedure\n\n{topic['procedure']}\n\nApplies the choice in [[{did}]].",
                   kind="specification", valid_from="2026-10-01", valid_until="2027-01-01")
        decoy = f"notes/distractor-{number:02}.md"
        write_note(root / decoy, topic["label"] + " policy comparison", (topic["words"] + " policy operating procedure observed duration. ") * 3
                   + "\nThis rejected counterexample must not be followed: " + topic["wrong"])
        hub = f"notes/navigation-{number:02}.md"
        write_note(root / hub, f"Navigation index {number:02}", f"Navigation only, without policy, procedure or measurement content. [[{did}]] [[{did}]] [[{old_id}]]")
        paths = {"claim": claim["path"], "previous": old_path.relative_to(root).as_posix(),
                 "decision": current_path.relative_to(root).as_posix(), "outcome": outcome["path"],
                 "specification": spec, "distractor": decoy, "navigation": hub}
        identities[topic["id"]] = {"partition": topic["partition"], "evidence_id": eid, "claim_id": claim["claim_id"],
                                    "decision_id": did, "previous_id": old_id, "source_version": capture["source_version"], "paths": paths}
    files = {p.relative_to(root).as_posix(): {"sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
             for p in sorted(root.rglob("*")) if p.is_file() and ".whykit" not in p.relative_to(root).parts}
    # Writer locks/journals are implementation artifacts, not canonical corpus bytes.
    return {"format": "whykit.synthetic-hybrid-corpus/v1", "as_of": AS_OF.isoformat(), "generation_seed": None,
            "randomness": "none", "topics": identities, "semantic_records": 48, "rankable_documents": 42,
            "evidence_intermediaries": 6,
            "rankable_paths": sorted(path for topic in identities.values() for path in topic["paths"].values()),
            "non_ranked_markdown_backing": ["Home.md", "00-context/evidence-register.md", "00-context/review-log.md", "06-decisions/decision-log.md"],
            "files": files,
            "canonical_tree_sha256": digest(json_text(files).encode("utf-8"))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    args.manifest.write_bytes(json_text(generate(args.destination)).encode("utf-8"))
