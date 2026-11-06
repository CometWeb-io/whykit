# Use case: revisit a GTM decision after its evidence changes

> Fictional, local walkthrough. No customer results, automatic source checks or AI-quality gains are implied. Check your installed version against the [command reference](../cli.md). Keep real interview notes and private strategy in a private vault.

## The job

A small product team chose marketing agencies as its first customer segment. It documented the rationale based on interview notes. Later interviews contradict part of that rationale. The team needs to find the decision that relied on the old source **without rewriting what it originally believed**.

WhyKit preserves a register of sources, decisions and review events. It checks declared relationships; it cannot tell whether the interview data is true or the ICP is right.

## 1. Create the source and a draft decision

Install the [latest published WhyKit](https://pypi.org/project/whykit/) with Python 3.11+ and uv 0.13.4+, or use a source checkout.

~~~bash
uv tool install whykit
whykit init icp-ledger
whykit new --root icp-ledger evidence --source "Agency interviews, round 1 (fictional)" --type dataset --location "https://example.com/research/agency-round-1.csv" --claims "Interviewees expressed interest in recurring site audits"
whykit new --root icp-ledger decision "Prioritize marketing agencies as the initial ICP" --owner Growth --status draft --source E-001
whykit trace --root icp-ledger --decision D-001
~~~

Open the created Markdown decision. Write the actual context, reasoning, alternatives and trade-offs. This is only a scaffold until somebody completes and approves it. See [how to inspect and approve a real decision](../tutorials/record-and-supersede.md). A newly created draft is **not** proof that a decision has been accepted. The starter vault also contains unanswered questions in its AGENTS.md.

## 2. Register the newer evidence and mark the old source as replaced

Later, the team obtains a follow-up interview dataset. Only a person can determine whether it replaces the old dataset. For this fictional walkthrough, record the replacement explicitly:

~~~bash
whykit new --root icp-ledger evidence --source "Agency interviews, round 2 (fictional)" --type dataset --location "https://example.com/research/agency-round-2.csv" --claims "Respondents reported insufficient budget for a standalone audit tool"
whykit impact --root icp-ledger E-001
whykit evidence retire --root icp-ledger E-001 --why "Round 2 replaces the original screening sample" --replaced-by E-002
whykit trace --root icp-ledger --decision D-001
~~~

The impact query identifies declared references to E-001. The trace can show that the decision cites retired evidence and that E-002 is its registered replacement. It **does not** judge whether the ICP should change, fetch the new dataset, compare its contents, or update the decision.

## 3. Make the human decision

Open the original decision, read both real sources and assess the new evidence. If the accepted decision needs to change, record a successor instead of silently editing its reasoning. Record who reviewed it and when. The [record-and-supersede tutorial](../tutorials/record-and-supersede.md) explains the approval and Git history checks.

For a technical SEO variation, the decision might be **merge two URLs**, citing a saved query analysis and a SERP review. A later human review could replace that evidence and trigger a check of decisions citing it. WhyKit is **not** Search Console, rank tracking, a crawler, or a causal SEO testing tool.

## What this example proves — and what it does not

| Shown by the documented workflow | Not shown |
| --- | --- |
| Named evidence IDs and a decision's declared citation | The underlying evidence is accurate |
| Explicit evidence retirement and dependency inspection | Automatic external change monitoring |
| Human-owned review and supersession workflow | A recommended business or SEO action |
| Markdown and Git records that tools can inspect | Better AI answers or commercial outcomes |

Optional [read-only MCP](../mcp.md) can expose selected vault context to a configured AI host, with its documented sensitivity and host-access limitations. It cannot write decisions or turn a source claim into verified truth.

## When to skip WhyKit

If you only need a brief note, a Git commit, a Search Console annotation or a conventional ADR, use that simpler tool. WhyKit is worth testing when **multiple material decisions depend on sources that can be retired or replaced**, and someone will actually review the affected decisions.
