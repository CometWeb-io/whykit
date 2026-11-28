# Tutorial: catch decision drift before a change lands

In about ten minutes you will keep a decision next to the code it governs, ask
which decisions a planned change touches, watch WhyKit flag the decision when
the source behind it is replaced, and resolve the flag by recording a
successor. Every command on this page runs in the test suite, so the output
you see is what the current version prints.

You need Git and a `whykit` command on your `PATH` (`uv tool install whykit`,
Python 3.11+). The shop, its code and its sources are fictional.

## 1. A repository with code and decisions

Create a tiny application and a vault inside it. Keeping the vault in the same
repository means decisions are reviewed in the same pull request as the code.

```bash
mkdir shop
cd shop
git init -q -b main
mkdir -p src/payments src/web
printf 'def charge(amount_cents: int) -> None:\n    ...\n' > src/payments/ledger.py
printf '<h1>Shop</h1>\n' > src/web/home.html
whykit init decisions
```

```text
WhyKit vault created: <path>
```

## 2. Record a decision and the code it covers

Register the source, then a draft decision that cites it. `--applies-to` says
which code the decision governs, as globs relative to the repository root:

```bash
whykit new --root decisions evidence \
  --source "Finance audit checklist, 2025" --type document \
  --location "https://example.com/finance/audit-2025.pdf" \
  --claims "Auditors reject ledgers with floating-point rounding differences"
whykit new --root decisions decision "Store money as integer cents" \
  --owner Payments --status draft --source E-001 --applies-to 'src/payments/**'
```

```text
created E-001: 00-context/evidence-register.md
created D-001: 06-decisions/d-001-store-money-as-integer-cents.md
```

Write the reasoning before approval. Save this as `d-001-body.md`:

<!-- tutorial: file=d-001-body.md -->
```markdown
# Decision record: Store money as integer cents

## Decision ID

D-001

## Status

Proposed

## Context

The 2025 finance audit (E-001) rejects ledgers with floating-point rounding differences.

## Decision

Store and compute every amount as an integer number of cents.

## Rationale

Integers make rounding explicit and reproducible, which the audit requires.

## Evidence

- E-001 — finance audit checklist, 2025.

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Decimal floats | Familiar | Rounding drift | Fails the audit |

## Consequences

- Display code converts cents to currency only at the edge.

## Ownership and review

- Owner: Payments
```

Keep the front matter, replace the scaffold body, approve the exact preview and
commit:

```bash
record=decisions/06-decisions/d-001-store-money-as-integer-cents.md
awk 'n < 2 { print } /^---$/ { n++ }' "$record" > front.md
cat front.md d-001-body.md > "$record"
rm front.md d-001-body.md
whykit review --root decisions approve D-001 --reviewer Payments --json > ../d-001-approval.json
whykit review --root decisions approve D-001 --reviewer Payments --write \
  --expect-hash "$(python3 -c 'import json; print(json.load(open("../d-001-approval.json"))["expected_sha256"])')"
git add .
git commit -q -m "Code and D-001"
```

## 3. Ask before you change

An agent, or you, is about to edit the ledger and the home page. Ask which
decisions cover those files:

```bash
whykit affected --root decisions --files src/payments/ledger.py src/web/home.html
```

```text
WhyKit affected — 2 changed files · 1 decision applies · 0 need review · 1 file not covered by any decision
  D-001  applies  Store money as integer cents
      files: src/payments/ledger.py
  not covered by any decision: src/web/home.html
```

`D-001` applies and nothing it rests on has changed, so there is no review
signal: read it before changing the ledger. The home page is reported as not
covered, which is information too: no recorded decision governs it. The caveat
line says source ages are not checked until you set
`[evidence_access_age_days]` in `whykit.toml`.

## 4. The world moves

The auditors publish a new checklist. Register it and retire the old source in
the vault, as a finance colleague would:

```bash
whykit new --root decisions evidence \
  --source "Finance audit checklist, 2026" --type document \
  --location "https://example.com/finance/audit-2026.pdf" \
  --claims "Auditors also require a currency code on every stored amount"
whykit evidence --root decisions retire E-001 \
  --why "Replaced by the 2026 checklist" --replaced-by E-002
git add .
git commit -q -m "Replace the audit checklist"
```

```text
created E-002: 00-context/evidence-register.md
retired E-001; replacement E-002; 1 current and 0 historical reference(s)
```

Now someone changes the ledger. Without `--files`, WhyKit asks Git which files
changed since `HEAD` (in a pull request, pass the base with `--base`):

```bash
printf '# rounding fix\n' >> src/payments/ledger.py
whykit affected --root decisions
```

```text
WhyKit affected — 1 changed file · 1 decision applies · 1 needs review
  D-001  review   Store money as integer cents
      files: src/payments/ledger.py
      - a cited source was retired
```

The change itself may be fine. What WhyKit can say is narrower and checkable:
the decision that governs this file rests on a source that has since been
replaced, so its reasoning should be looked at before this lands.

For a pull request, `--format markdown` writes the same report as a table, and
`--strict` makes the job fail while a review signal is open:

<!-- tutorial: exit=1 -->
```bash
whykit affected --root decisions --format markdown --strict
```

```text
### WhyKit: decisions this change touches
| **D-001** Store money as integer cents | `src/payments/ledger.py` | ⚠️ review: a cited source was retired |
```

## 5. Resolve it: supersede, do not rewrite

The 2026 checklist changes the decision: amounts now carry a currency code.
Record a successor that cites the new source and covers the same code:

```bash
whykit new --root decisions decision "Store money as integer cents with a currency code" \
  --owner Payments --status draft --source E-002 --supersedes D-001 \
  --applies-to 'src/payments/**'
```

```text
created D-002: 06-decisions/d-002-store-money-as-integer-cents-with-a-currency-code.md
```

<!-- tutorial: file=d-002-body.md -->
```markdown
# Decision record: Store money as integer cents with a currency code

## Decision ID

D-002

## Status

Proposed

## Context

The 2026 finance audit (E-002) also requires a currency code on every stored amount. Supersedes D-001.

## Decision

Store every amount as integer minor units together with its ISO 4217 currency code.

## Rationale

Integers keep rounding exact, and the currency code satisfies the new checklist.

## Evidence

- E-002 — finance audit checklist, 2026.

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Keep D-001 unchanged | No migration | Fails the 2026 audit | The checklist is mandatory |

## Consequences

- Every amount column gains a currency column.

## Ownership and review

- Owner: Payments
```

```bash
record=decisions/06-decisions/d-002-store-money-as-integer-cents-with-a-currency-code.md
awk 'n < 2 { print } /^---$/ { n++ }' "$record" > front.md
cat front.md d-002-body.md > "$record"
rm front.md d-002-body.md
whykit review --root decisions approve D-002 --reviewer Payments --json > ../d-002-approval.json
whykit review --root decisions approve D-002 --reviewer Payments --write \
  --expect-hash "$(python3 -c 'import json; print(json.load(open("../d-002-approval.json"))["expected_sha256"])')"
whykit affected --root decisions --files src/payments/ledger.py
```

```text
WhyKit affected — 1 changed file · 1 decision applies · 0 need review
  D-002  applies  Store money as integer cents with a currency code
```

`D-001` is superseded, so it no longer applies; its reasoning stays in the
history exactly as it was approved. `D-002` governs the ledger now.

## 6. Let Git keep scopes current

Commits that name a decision are evidence of where it applies. Commit a
change that cites `D-002`, then ask WhyKit for scope suggestions across every
live decision, including scoped ones:

```bash
mkdir -p src/payments/currency
printf 'ISO_4217 = {"EUR", "USD"}\n' > src/payments/currency/codes.py
git add .
git commit -q -m "Add currency codes for D-002"
whykit scope suggest --root decisions --all
```

```text
WhyKit scope suggestions — 1 decision(s), 1 with suggestions
  D-002  Store money as integer cents with a currency code
      current: src/payments/**
      src/payments/**                  1 commit(s), 2 file(s) (already covered)
```

The suggestion is already covered by the scope you wrote. For decisions
without a scope, `--write` adds the suggested patterns.

## What you have

- Decisions that say which code they govern (`applies_to`), suggested from
  Git history and kept current without rewriting their reasoning.
- One command that maps a change to those decisions and checks what they rest
  on: retired, missing or stale sources, changed captured sources, overdue or
  failed reviews.
- An honest report: covered files, uncovered files and unscoped decisions,
  and never a verdict that a change is safe.

Next: run it in every pull request with the
[CI recipe](../affected.md#run-it-in-pull-requests), or let agents ask it
before they edit with [`whykit connect`](../agents.md).
