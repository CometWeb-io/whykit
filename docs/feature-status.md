# What to expect from WhyKit 0.4

This page describes the **unreleased 0.4 development branch**, not the 0.3.4
package on PyPI. It sets expectations for a proposed public alpha, not a claim
that 0.4 has passed its release checks. These labels describe support and risk;
they are not feature flags and do not disable commands.

| Area | Status | What to rely on and what remains outside the promise |
|---|---|---|
| Markdown vault, evidence, decisions, trace, lint, review, history and recovery | Core | The existing 0.3.4 workflows are the starting point. Back up and test your own vault and automation before upgrading; see [upgrade and recovery](migration-0.4.md). Checks report structure and declared links, not whether evidence or conclusions are true. |
| Sensitivity filtering, public export, Explorer and read-only MCP | Core with explicit boundaries | Filters limit returned data; they are not authentication, authorization or an operating-system access control. Incorrectly labelled information may be exposed. Review an export before publishing and protect the source vault. |
| Captures, claims, source observations, outcomes and knowledge v2 | Preview | These are explicitly created or requested records and views. Matching bytes do not authenticate a source, establish truth or prove currentness. Older clients cannot evaluate the new contracts; see [claims](claims.md), [outcomes](outcomes.md) and [knowledge v2](knowledge-v2.md). |
| Private knowledge bundle and restore | Preview; supported POSIX hosts only | A bundle contains raw data, is not encrypted, excludes Git history and trusted baselines, and restore is not globally atomic. Unsupported hosts, including native Windows, refuse these operations. Keep a separately verified backup of the vault and its Git history. |
| Compiled context, dependency graph and temporal graph | Preview | Scope, byte budgets, graph limits, declared validity and truncation matter. A digest or explanation is not source authenticity, permission to act or proof that every relevant dependency was declared. |
| BM25 retrieval | Opt-in preview | Substring matching remains the default. The available synthetic examples do not establish better answers or real-world relevance. See [retrieval](retrieval.md). |
| Hybrid retrieval | Experimental and opt-in | Graph and ranking limits can truncate results; existing benchmarks do not establish a production performance or quality advantage. It does not change the default search. |
| Offline evaluator, agent preflight, local workspace and saved GitHub import | Preview | The evaluator scores supplied responses; it does not establish model accuracy or source truth. Preflight checks declared inputs and does not authorize execution or find omitted decisions. Workspace status is not a cross-vault transaction. Import consumes saved, untrusted payloads; it does not fetch or authenticate provider state. |

Machine-readable formats have their own documented version and exit-code rules;
see the [automation contract](automation.md). The labels above do not add a new
compatibility guarantee for preview formats. WhyKit does not provide enterprise
RBAC, authenticated reviewers, autonomous approvals or a compliance
certification. No independent deployment or model-quality result is implied by
the alpha label.
