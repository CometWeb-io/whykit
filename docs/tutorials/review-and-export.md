# Tutorial: import an ADR, review changed evidence and export public data

This walkthrough follows one decision from an existing ADR to a review triggered
by corrected evidence. It finishes with an Explorer data export that keeps the
review warning and excludes a restricted note and evidence row. Every command
runs in the test suite. All names, reports and URLs below are fictional.

You need Git, Python 3 and `whykit` on your PATH. From a source checkout use
`uv tool install --from /path/to/whykit whykit`, or replace each `whykit` command
with `uv run --project /path/to/whykit whykit`.

## 1. Create an isolated ledger and register the source

Start in an empty directory. The files generated here belong to this disposable
example, not to your existing ADR repository.

```bash
whykit init review-ledger
cd review-ledger
git init -q -b main
```

```text
WhyKit vault created: <path>
```

The example's source catalog is safe to publish. Explicitly classify it as
public before adding public rows; the default register is internal, and a row
cannot lower that floor. Keep real private catalogs at their appropriate label.

```bash
python3 - <<'PY'
from pathlib import Path
path = Path('00-context/evidence-register.md')
text = path.read_text(encoding='utf-8')
assert 'sensitivity: internal\n' in text
path.write_text(text.replace('sensitivity: internal\n', 'sensitivity: public\n', 1), encoding='utf-8')
PY
whykit new evidence --source "Recovery replay sample" --type fixture \
  --location "https://example.com/replay" \
  --claims "The replay sample met the recovery target" --sensitivity public
```

```text
created E-001: 00-context/evidence-register.md
```

## 2. Import an existing ADR without inheriting its approval

Save this Nygard-style ADR as `../existing-adr/0007-use-managed-queues.md`.
Its original status is Accepted; that status is evidence about the source
process, not an approval in this new ledger.

<!-- tutorial: file=../existing-adr/0007-use-managed-queues.md -->
```markdown
# 7. Use managed queues

## Status

Accepted

## Context

The recovery replay needs predictable queue delivery with a small operations team.

## Decision

Use a managed queue for recovery traffic.

## Rationale

The observed recovery sample meets the target without operating a broker ourselves.

## Alternatives considered

- Operate our own broker: rejected because it adds maintenance to the small team.

## Consequences

### Positive

- The operations team can focus on replay recovery.

### Negative and trade-offs

- The team accepts the provider's limits and usage charges.
```

Preview the mapping. This checks the source format and hash without creating
a decision record:

```bash
whykit new decision --from ../existing-adr/0007-use-managed-queues.md \
  --owner "Ada Example" --source E-001 --sensitivity public --json > ../mapping.json
python3 - <<'PY'
import hashlib, json
from pathlib import Path
report = json.loads(Path('../mapping.json').read_text(encoding='utf-8'))
source = Path('../existing-adr/0007-use-managed-queues.md')
assert report['write'] is False and report['status'] == 'draft'
assert report['source']['status'] == 'Accepted'
assert report['source']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
assert not Path(report['path']).exists()
print('Preview: original status Accepted; destination draft; no decision written.')
PY
```

```text
Preview: original status Accepted; destination draft; no decision written.
```

Read `../mapping.json` and the original ADR. After checking the mapping, write
the draft. The original bytes remain available and their hash is retained:

```bash
whykit new decision --from ../existing-adr/0007-use-managed-queues.md \
  --owner "Ada Example" --source E-001 --sensitivity public --write --json > ../imported.json
python3 - <<'PY'
import hashlib, json
from pathlib import Path
report = json.loads(Path('../imported.json').read_text(encoding='utf-8'))
record = Path(report['path']).read_text(encoding='utf-8')
original = Path('../existing-adr/0007-use-managed-queues.md').read_bytes()
assert report['id'] == 'D-001' and report['status'] == 'draft'
assert 'status: draft\n' in record and 'human_reviewed: false\n' in record
assert f'source_sha256: "{hashlib.sha256(original).hexdigest()}"' in record
assert '## Original record' in record
print('Imported D-001 as draft; original ADR and source hash retained.')
PY
```

```text
Imported D-001 as draft; original ADR and source hash retained.
```

## 3. Review and approve the exact draft

The imported reasoning, alternatives and trade-offs need human review. This
fictional ADR already supplies the required sections. In a real import, fill
any remaining placeholders and verify the actual source before approval.

```bash
whykit review approve D-001 --reviewer "Ada Example" --json > ../approval.json
python3 - <<'PY'
import json
from pathlib import Path
preview = json.loads(Path('../approval.json').read_text(encoding='utf-8'))
assert preview['applied'] is False
print('Approval preview saved; no acceptance written.')
PY
```

```text
Approval preview saved; no acceptance written.
```

Open `../approval.json`, read `reviewed_record` and inspect every proposed
change. Only after accepting that preview, apply its hash. The date comes from
the same preview, so crossing midnight does not silently change the request.

```bash
approval_hash=$(python3 -c 'import json; print(json.load(open("../approval.json"))["expected_sha256"])')
approval_date=$(python3 -c 'import json; print(json.load(open("../approval.json"))["date"])')
whykit review approve D-001 --reviewer "Ada Example" --today "$approval_date" \
  --write --expect-hash "$approval_hash" --json > ../approved.json
git add .
git commit -q -m "Review imported queue decision"
python3 - <<'PY'
import json
from pathlib import Path
assert json.loads(Path('../approved.json').read_text(encoding='utf-8'))['applied'] is True
print('D-001 approved from the exact reviewed preview.')
PY
```

```text
D-001 approved from the exact reviewed preview.
```

The reviewer name records attribution. Use your protected repository review
process for authenticated acceptance; a name and hash do not establish identity.

## 4. Correct the evidence and find the affected decision

The corrected replay sample changes the support for this decision. Register a
new source and inspect the impact before retiring the old one:

```bash
whykit new evidence --source "Corrected recovery sample" --type fixture \
  --location "https://example.com/replay-v2" \
  --claims "The corrected sample no longer meets the recovery target" --sensitivity public
whykit impact E-001 --json > ../impact.json
python3 - <<'PY'
import json
from pathlib import Path
impact = json.loads(Path('../impact.json').read_text(encoding='utf-8'))
assert impact['reference_count'] == 1
assert impact['references'][0]['path'] == '06-decisions/d-001-use-managed-queues.md'
print('Impact: D-001 cites the source being replaced.')
PY
whykit evidence retire E-001 --why "Corrected replay data replaces the original sample" --replaced-by E-002
```

```text
created E-002: 00-context/evidence-register.md
Impact: D-001 cites the source being replaced.
retired E-001; replacement E-002; 1 current and 0 historical reference(s)
```

The strict trace now exits 1. This is the expected signal to review the live
decision, not a tool failure:

<!-- tutorial: exit=1 -->
```bash
whykit trace --decision D-001 --strict --json > ../trace.json
```

```bash
python3 - <<'PY'
import json
from pathlib import Path
trace = json.loads(Path('../trace.json').read_text(encoding='utf-8'))
decision = trace['decisions'][0]
assert decision['decision_id'] == 'D-001'
assert 'retired_evidence' in decision['gaps']
assert decision['evidence'][0]['replaced_by'] == 'E-002'
print('Review required: D-001 still cites retired E-001; replacement E-002 exists.')
PY
whykit review record D-001 --reviewer "Ada Example" --outcome update-required \
  --note "The corrected sample requires revising this decision."
```

```text
Review required: D-001 still cites retired E-001; replacement E-002 exists.
review recorded: 06-decisions/d-001-use-managed-queues.md -> update-required
```

This event records the assessment; it does not repair the reasoning or replace
the citation. Use [record and supersede](record-and-supersede.md) when deciding
what should replace D-001. Until then, keep the retired-evidence warning visible.

## 5. Export public data with the warning intact

Add fictional restricted material to check both document and row filtering.
These markers are synthetic test data, not credentials:

```bash
whykit new evidence --source "Private operator report" --type fixture \
  --location "https://example.com/private-report" \
  --claims "PRIVATE_EVIDENCE_CANARY" --sensitivity restricted
whykit new note "Operator notes" --workstream notes --owner "Ada Example" \
  --type reference --sensitivity restricted --link-from Home.md
printf '\nSYNTHETIC_PRIVATE_TOKEN: fictional operator detail.\n' >> notes/operator-notes.md
whykit explorer-index > ../public-index.json
python3 - <<'PY'
import json
from pathlib import Path
text = Path('../public-index.json').read_text(encoding='utf-8')
index = json.loads(text)
assert index['exportMode'] == 'public'
assert any(note.get('decisionId') == 'D-001' for note in index['docs'])
assert {row['id'] for row in index['evidence']} == {'E-001', 'E-002'}
assert 'PRIVATE_EVIDENCE_CANARY' not in text and 'SYNTHETIC_PRIVATE_TOKEN' not in text
assert not any(note['id'] == 'notes/operator-notes' for note in index['docs'])
assert any(finding['code'] == 'evidence.retired' for finding in index['lint']['findings'])
print('Public export: D-001 and public sources included; restricted note and row omitted.')
print('The retired-evidence warning remains visible.')
PY
```

```text
created E-003: 00-context/evidence-register.md
created note: notes/operator-notes.md
Public export: D-001 and public sources included; restricted note and row omitted.
The retired-evidence warning remains visible.
```

`../public-index.json` is the public Explorer data payload, not a standalone web
page. The internal review log is not made public by this export. Classification
filters this payload; filesystem permissions remain the access boundary for the
vault itself. WhyKit preserves the source/decision/review relationship and the
known gap. It does not establish whether either replay report is true.
