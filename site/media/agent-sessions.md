# Agent sessions shown in the hero

The hero is based on five real sessions recorded on 7 November 2026. In every
session the agent could reach the vault only through the WhyKit MCP server.
All three agents answered the partner-channel prompt; Claude Code and Codex
were also asked one more question each, and the hero pairs each agent with one
question.

## Setup

- Vault: a copy of `examples/northline` from this repository at commit `73d6833`.
- Server: `whykit-mcp --root <copy of examples/northline> --max-sensitivity public`
  (WhyKit installed locally from that commit).
- Claude Code 2.1.280, run headless with `claude -p`, `--strict-mcp-config` and
  only the seven WhyKit tools allowed.
- Codex CLI 0.167.0-alpha, run with `codex exec -s read-only` and the server
  passed as `mcp_servers.whykit` configuration.
- Cursor Agent CLI, run with `cursor-agent -p --trust --approve-mcps`, the
  server in the folder's `.cursor/mcp.json` and a `.cursor/cli.json` that
  allows only `Mcp(whykit:*)`. Without that allow rule the print mode declined
  the tool calls.
- Every session ran from an empty working directory, so the vault files were not on
  their file path.

## Partner-channel prompt (all three agents)

> Our new sales lead wants to make partners the primary channel again. Before I
> reply, use WhyKit to find out why we moved away from partners, which evidence
> that rests on, and whether the decision is still current. Answer in five short
> lines and cite the record IDs.

## Claude Code

WhyKit tool calls, in order (one host step that loads tool schemas is omitted):

1. `query {"text": "partner", "type": "decision"}`
2. `query {"text": "partner"}`
3. `query {"text": "channel"}`
4. `pack {"targets": ["D-009", "D-010", "D-011", "E-013"], "agent": "claude", "max_docs": 4}`
5. `trace {"decision": "D-011"}`
6. `trace {"decision": "D-009"}`
7. `backlinks {"target": "D-011"}`

Answer, verbatim (18.9 s end to end):

1. **Why we moved away:** Partners were tried as the primary DACH channel for six months (D-009, owner Lena Krüger, March 2026) and produced no pipeline. They also pitched Pulse as an "AI add-on" to IT, the deal shape we lose under D-002.
2. **Evidence:** E-013, the internal partner-channel review for March to August 2026 from the CRM partner-source report. Two signed partners yielded three meetings and zero SQLs.
3. **Decision chain:** D-009 (partner-led) was superseded by D-010 (direct to ops directors, August 2026), which was superseded by D-011 (same decision, evidence attached, September 2026).
4. **Still current:** D-011 is the only live decision on the motion. It is approved, traces cleanly to E-013 with no gaps, the evidence is active and not stale as of today, and review is due 2027-02-01.
5. **What it allows:** Partners may still introduce and get paid on closed-won they originated, but they are explicitly "not the plan". Reversing this would need a new decision record superseding D-011 with new evidence, since the vault only has one dataset on partners and it is negative.

## Codex

WhyKit tool calls, in order (one shell command that searched the host's own
memory file, outside the vault, is omitted):

1. `query {"text": "partner", "limit": 30}`
2. `pack {"targets": ["D-009", "D-010", "D-011", "E-013"], "max_chars": 22000, "max_docs": 4}`
3. `trace {"decision": "D-011", "today": "2026-11-07"}`

Answer, verbatim:

> Partners lost primary status after two signed partners delivered three meetings and zero sales-qualified leads (D-009, E-013).
> They also pitched Pulse to IT as an "AI add-on," missing the intended buyer (D-009).
> Evidence: March–August 2026 partner review, recorded against the CRM partner-source report (E-013).
> Current policy: direct to ops/maintenance; partners earn referral fees on originated wins; D-011 replaced D-010, which replaced D-009.
> D-011 remains approved and unsuperseded; review due 2027-02-01. E-013 is active, but historical—require fresh evidence before reversing course.

## Cursor

WhyKit tool calls, in order (one host step that lists the server's tools and
two file searches in the empty working directory, which found nothing, are
omitted):

1. `query {"text": "partners primary channel", "limit": 20}`
2. `query {"text": "partner", "type": "decision", "limit": 20}`
3. `context {"target": "D-011", "max_chars": 8000}`
4. `context {"target": "D-009", "max_chars": 8000}`
5. `trace {"decision": "D-011"}`
6. `context {"target": "E-013", "max_chars": 6000}`
7. `context {"target": "D-010", "max_chars": 4000}`

Answer, verbatim (16.2 s end to end):

> D-009 made DACH system integrators the primary channel, with direct outbound secondary.
> It was dropped because two signed partners produced three meetings and zero SQL in about five months, and they pitched Pulse as an "AI add-on" to IT (E-013).
> D-010 (2026-08-11) superseded D-009: direct to ops and maintenance leads is the plan; partners may introduce and are paid only for a closed-won they originated.
> D-011 (approved 2026-09-15) is the live record of that same decision and is the one that cites E-013; E-013 is still active and not stale as of 2026-11-07.
> D-011 has no successor and no evidence gaps; its next review is 2027-02-01, so partners are still not the primary channel.

## Headline prompt (Claude Code)

> I'm about to rewrite our homepage headline to lead with 'AI-powered
> reliability'. Check WhyKit first: is there a decision against it, why, and is
> it still current? Answer in three short lines and cite the record IDs.

WhyKit tool calls, in order: `query {"text": "AI-powered", "type": "decision"}`,
`query {"text": "headline"}`, `query {"text": "reliability"}`,
`context {"target": "D-006"}`, `trace {"decision": "D-006"}`,
`context {"target": "01-strategy/messaging.md", "max_chars": 2500}`.

Answer, verbatim (15.6 s end to end):

> Yes, there is a standing decision against it, and it is still live.
>
> 1. **Decision:** D-006 (approved, owner Maya Chen) bars "AI" or any model vendor name from the H1, nav, and product name. The messaging doc lists "AI-powered" explicitly as a proof point we may not use, and already rejected "The AI reliability layer for mid-market manufacturing" for violating D-006.
> 2. **Why:** "AI reliability" is the category IT already bought. Win/loss notes (E-004) show 7 of 9 lost H1 2026 deals cited an existing IT-bought reliability tool, and the Vendor A brief (E-005) shows a competitor owning that framing. Leading with it frames you against a category you lose, to a buyer D-002 says is not the ICP.
> 3. **Still current:** D-006 is not superseded, both evidence items are active and not stale as of today, and the review date is 2027-01-06. If you want to proceed anyway, the route is a new decision superseding D-006 with fresh evidence, not a copy change.

## Paid-search prompt (Codex)

> Marketing wants to start a small paid search campaign next week. Check WhyKit
> first: is there a decision about paid acquisition, which evidence is it based
> on, and is it still current? Answer in three short lines and cite the record
> IDs.

WhyKit tool calls, in order (one shell command that searched the host's own
memory file, outside the vault, is omitted): `query {"text": "paid", "limit": 30}`,
`pack {"targets": ["D-003", "E-010", "05-operations/meeting-2026-09-04-pipeline.md", "D-011"], "max_chars": 22000, "max_docs": 4}`.

Answer, verbatim:

> D-003 bars paid acquisition—including small paid-search campaigns—until 30 organic demos in a calendar month or a superseding decision.
> Evidence: E-010, August's HubSpot report, recorded 11 demo requests, only 6 organic; D-003 aims to preserve organic measurement.
> D-003 remains approved with no visible successor, but review was due 2026-10-06; Maya Chen should refresh E-010 and review before launch.

## How the hero uses them

The hero is a diagram, not a transcript. Each agent in turn asks one question,
it goes through WhyKit, and the card shows the decision the agent landed on and
the source behind it:

| Agent | Question on the page | Decision | Source and figure |
|---|---|---|---|
| Claude Code | Can the headline say AI? | D-006, live | E-004: 7 of 9 lost deals cited an IT-bought tool |
| Codex | Can we run paid ads? | D-003, review overdue since 2026-10-06 | E-010: 6 organic demo requests in August |
| Cursor | Why not partners? | D-011, live | E-013: 3 meetings, 0 SQL |

The questions on the page are short versions of the prompts above. "+ any MCP
client" refers to the stdio server any MCP host can launch; only the three
hosts above were run. Northline is a synthetic example vault: its company,
people and figures are fictional.

Logos: Claude and Cursor marks come from Simple Icons 16.36.0 and belong to
their owners. Simple Icons does not include an OpenAI mark, so Codex is shown
with a generic terminal glyph.

## Setup commands shown on the page

Both were run on 7 November 2026 against throwaway configuration (a project
`.mcp.json` for Claude Code, a temporary `CODEX_HOME` for Codex):

```bash
claude mcp add whykit -- whykit-mcp --root /path/to/my-ledger
codex mcp add whykit -- whykit-mcp --root /path/to/my-ledger
```

For Cursor, the page shows the `.cursor/mcp.json` form used in the session above.

## Figures in the strip under the hero

- **5/5:** the five sessions above. Each answer named the decision that
  answers the question (D-011, D-006 or D-003) and the evidence it rests on.
- **17 s:** the mean of the three sessions whose host reported a duration:
  Claude Code 18.9 s and 15.6 s, Cursor 16.2 s. Codex `exec --json` does not
  report one, so its runs are not included.
- **3 agents:** Claude Code, Codex and Cursor, as listed under Setup.
- **0 files an agent can change:** the MCP server offers no write tools. The
  configuration smoke test in `tests/test_mcp_runtime.py` checks the vault
  bytes are unchanged (see `docs/mcp.md`).
