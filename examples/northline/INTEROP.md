---
title: "Working across repositories"
aliases: ["INTEROP"]
type: guide
status: approved
owner: "Product Lead"
created: 2026-03-12
last_updated: 2026-09-17
source_of_truth: true
sensitivity: public
source_ids: []
tags: ["governance", "interop"]
workstream: "root"
---

# Working across repositories

This repository holds reasoning. Agents and automations may produce typed handoffs. The company's other repositories hold systems. They are edited in the same sessions, by the same people and agents, and that is exactly where things go wrong.

## The split

| | Knowledge (WhyKit) | Agent handoffs | Code |
|---|---|---|---|
| Source of truth for | Why a thing was decided, what we believe, what we measured | How an envelope was produced, and its producer-local status | What the system actually does |
| Changes when | A decision, a finding or a definition is filed | An agent/workflow emits a new handoff | Behaviour changes |
| Read by | People, agents, new joiners | Agent hosts and workflow adapters | Build pipelines, runtimes |
| Safe to be wrong in | No — it is quoted later as fact | No — a rewritten hash is a lie | It fails loudly |

The rule that follows: **never describe current system behaviour here as fact.** Describe the decision and link to the code. Code drifts; a decision does not.

A sentence here saying "the form sends X" will be false within a quarter and nobody will notice. A sentence saying "D-005: we decided the homepage should lead with the machine list, see `web/src/pages/Home.astro`" stays true even after the code changes, because it records a decision, and the link shows what happened to it.

A second rule: **never collapse statuses.** Evidence READY is not a Council GO. A Council GO is not a release GO. Skills remain standalone; this vault MAY apply stricter gates than the producer recorded.

## Checkout layout

```
workspace/
  knowledge/        <- this repository (WhyKit, Apache-2.0)
  web/
  api/
  infra/
```

Keep agent integrations outside the vault core. File their durable, provenance-preserving outputs here only after applying the normal review rules.

## Linking to another repository

Always prefix the repository name, and always use the path from that repository's root:

```
web/src/components/MachineList.astro
api/server.py:112
```

Never write a path from inside a subdirectory, and never write an absolute path from someone's machine.

## What lives where when a change touches both

A change that spans repositories produces two artefacts, not one:

1. **A decision record here**, in `06-decisions/`, carrying the reasoning, the alternatives, the evidence and the consequences.
2. **The change itself in the code repository**, whose commit message references the decision ID.

When the work came from a typed agent workflow, there may be a third artefact: **the producer handoff**, filed without rewriting provenance fields such as producer, version, as-of time and payload hash.

When a decision is reversed, append a new record and mark the old one `superseded`. Do not edit the original into agreement with the present.

## Rules for agents crossing a repository boundary

1. **Do not clone, pull or fetch other repositories** because a task mentions them.
2. **Do not run a code repository's build, deploy or release commands** from a session anchored here.
3. **Do not commit in a repository you were not asked to change.**
4. **Read before claiming.** If a document here asserts something about a code repository, open the file and check before repeating it.
5. **Report per repository.** When work touched three repositories, say what changed in each, separately.

## Secrets

No repository in this set holds credentials. This one additionally must not hold their values in prose, screenshots or pasted logs. Record where a secret lives and who can grant it. Never what it is.
