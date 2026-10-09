"""Synthetic source/claim data; accepted fixtures use the real review API."""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from _vaults import fresh_vault
from whykit.config import load_config
from whykit.scaffold import create_evidence
from whykit.vault_index import VaultIndex

TODAY = dt.date(2026, 10, 9)
HEADER = "| evidence_id | relation | snapshot | source_snapshot_hash | fragment | observed_at | rationale |\n| --- | --- | --- | --- | --- | --- | --- |\n"


def claim_text(rows: list[str], *, claim_id: str = "C-001") -> str:
    return f"""---
type: claim
claim_id: {claim_id}
title: Offline reads
statement: Cached decisions are readable offline.
scope: Desktop v2.1 without synchronization.
status: draft
owner: Ada Example
created: 2026-10-09
last_updated: 2026-10-09
valid_from: 2026-10-01
valid_to: 2026-12-31
sensitivity: public
---

## Evidence

{HEADER}{''.join(rows)}"""


def source_snapshot(root: Path, data: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(data).hexdigest()
    relative = f"00-context/claim-snapshots/{digest}.txt"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative, digest


def claim_vault(root: Path, *, today: dt.date = TODAY) -> Path:
    fresh_vault(root, "--minimal")
    config = root / "whykit.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[claims]\nformat_version = 1\n", encoding="utf-8")
    register = root / "00-context/evidence-register.md"
    register.write_text(register.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: public"), encoding="utf-8")
    rows = []
    for relation, content in (("supports", b"Cached records can be read offline.\n"),
                              ("contradicts", b"Offline record reads fail in desktop v2.1.\n")):
        eid = create_evidence(root, source="Example observation", kind="report",
                              location="https://source.example/observation", claims="Offline reads",
                              today=today, sensitivity="public")
        path, digest = source_snapshot(root, content)
        rows.append(f"| {eid} | {relation} | {path} | {digest} | lines:1-1 | {today.isoformat()} | Recorded desktop observation |\n")
    path = root / "00-context/claims/c-001-offline-reads.md"
    path.parent.mkdir(parents=True)
    path.write_text(claim_text(rows), encoding="utf-8")
    return path


def read_view(root: Path) -> dict:
    from whykit.claims import capture_claims
    return capture_claims(VaultIndex.load(root), load_config(root)[0])


def approved_claim_vault(root: Path, *, today: dt.date = TODAY) -> tuple[str, Path, str, Path]:
    from whykit.review import approve_record
    from whykit.scaffold import create_decision
    from whykit.placeholders import SCAFFOLD_EVIDENCE_TODO
    from test_content_quality import FILLED
    cpath = claim_vault(root, today=today)
    plan = approve_record(root, 'C-001', reviewer='Ada Example', today=today)
    approve_record(root, 'C-001', reviewer='Ada Example', today=today, write=True, expected_sha256=plan['expected_sha256'])
    did, dpath = create_decision(root, 'Evaluate offline reads', owner='Ada Example', sensitivity='public', claim_ids=['C-001'], today=today)
    text = dpath.read_text(encoding='utf-8')
    for before, after in FILLED.items():
        text = text.replace(before, after)
    text = text.replace(SCAFFOLD_EVIDENCE_TODO, '- C-001').replace('(E-001)', '(C-001)')
    text += '\n## Claim assessment\n\n- C-001: Conflicting observations are limited to the named environments; verify before expanding support.\n'
    dpath.write_text(text, encoding='utf-8')
    plan = approve_record(root, did, reviewer='Ada Example', today=today)
    approve_record(root, did, reviewer='Ada Example', today=today, write=True, expected_sha256=plan['expected_sha256'])
    return 'C-001', cpath, did, dpath
