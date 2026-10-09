---
title: Working across repositories
aliases:
  - INTEROP
type: guide
status: approved
owner: WhyKit maintainers
created: 2026-10-09
last_updated: 2026-10-09
source_of_truth: true
sensitivity: public
tags:
  - governance
  - interop
---

# Working across repositories

This repository holds reasoning. The company's other repositories hold systems.
They are edited in the same sessions, by the same people and agents, and that is
exactly where things go wrong. These are the rules that keep them apart.

## The split

| | Knowledge repository | Code repository |
|---|---|---|
| Source of truth for | Why a thing was decided, what we believe, what we measured | What the system actually does |
| Changes when | A decision, a finding or a definition changes | Behaviour changes |
| Read by | People, agents, new joiners | Build pipelines, runtimes |
| Safe to be wrong in | No — it is quoted later as fact | It fails loudly |

The rule that follows: **never describe current system behaviour here as fact.**
Describe the decision and link to the code. Code drifts; a decision does not. A
sentence here saying "the form sends X" will be false within a quarter and nobody
will notice. A sentence saying "D-041: we decided the form should send X, see
`web/src/forms/Contact.tsx`" stays true even after the code changes, because it
records a decision, and the link shows what happened to it.

## Checkout layout

Assume every repository is a sibling directory under one workspace root:

```
workspace/
  knowledge/        <- this repository
  web/
  api/
  infra/
```

Nothing enforces this, but the link convention below reads naturally only if it
holds. If your layout differs, write it down here, because every agent that opens
this file will otherwise assume the layout above.

## Linking to another repository

Always prefix the repository name, and always use the path from that
repository's root:

```
web/src/components/BookingEmbed.astro
api/server.py:112
```

Never write a path from inside a subdirectory, and never write an absolute path
from someone's machine. A bare `src/components/BookingEmbed.astro` is ambiguous
the moment a second repository has a `src/components/`, and an absolute path is
wrong for everyone except its author.

Link to a line number only when pointing at a specific decision point. Line
numbers rot faster than paths, so prefer naming the function or the section.

## What lives where when a change touches both

A change that spans repositories produces two artefacts, not one:

1. **A decision record here**, in `06-decisions/`, carrying the reasoning, the
   alternatives, the evidence and the consequences.
2. **The change itself in the code repository**, whose commit message references
   the decision ID.

Neither is a summary of the other. The decision record explains a choice to a
person who was not there; the commit explains a change to someone reading a diff.

When a decision is reversed, append a new record and mark the old one
`superseded`. Do not edit the original into agreement with the present.

## Rules for agents crossing a repository boundary

1. **Do not clone, pull or fetch other repositories** because a task mentions
   them. Work against what is already checked out, and say so if it is missing.
2. **Do not run a code repository's build, deploy or release commands** from a
   session anchored here. Knowledge work does not need them, and a deploy
   triggered as a side effect of research is the worst kind of surprise.
3. **Do not commit in a repository you were not asked to change.** Cross-repo
   work produces changes in several working trees; leave them there and report
   what is uncommitted where.
4. **Read before claiming.** If a document here asserts something about a code
   repository, open the file and check before repeating it. The assertion may
   predate the code.
5. **Report per repository.** When work touched three repositories, say what
   changed in each, separately. A merged summary hides which tree is dirty.

## Secrets

No repository in this set holds credentials. This one additionally must not hold
their *values* in prose, screenshots or pasted logs — a knowledge repository is
copied, exported and shared far more casually than a code repository.

Record where a secret lives and who can grant it. Never what it is.

## Keeping this file honest

`INTEROP.md` is the one document here that is allowed to describe other
repositories, and therefore the one most likely to go stale. Review it whenever
a repository is added, renamed or retired, and put that review on the same
cadence as the decision-log review.
