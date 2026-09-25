# Company knowledge vault

This repository holds the reasoning behind how this company operates: durable
context, the evidence behind claims, and the decisions that were actually taken —
with the rationale that made them make sense at the time.

It is built on [WhyKit](https://github.com/cometweb/whykit).

## Start here

Open `Home.md`. It is the map of content and the entry point for every session,
human or agent.

## Before you write anything

1. Read `AGENTS.md`. It is the contract everyone works under, and it ships with
   questions this company has to answer for itself. Answer them first.
2. Read the README in the directory you are about to write in.
3. Check the note is not already covered somewhere canonical. Link, do not copy.

## The rule that matters most

Do not let these blur:

| Type | Means |
|---|---|
| Verified fact | Supported by reviewed evidence, cited as `E-NNN` |
| Decision | An authorized choice, recorded as `D-NNN`, never rewritten |
| Hypothesis | Plausible, not demonstrated |
| Recommendation | Proposed, not yet accepted |
| Open question | A known unknown with an owner |

An unsupported claim should not become company memory just because somebody —
or some agent — wrote it confidently.

## Checking your work

```bash
whykit lint
whykit lint --strict
```

The linter checks shape, not truth. It will tell you that a claim has no
evidence; it cannot tell you the evidence is any good.

## Sensitivity

Every note carries `sensitivity: public | internal | confidential | restricted`.
**This vault is not public unless you have deliberately made it so.** Never lower
a sensitivity label to make sharing easier, and never record a secret value —
record where the secret lives and who can grant it.
