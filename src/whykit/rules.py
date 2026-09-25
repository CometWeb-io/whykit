"""Stable public catalog for WhyKit lint rule codes."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Rule:
    code: str
    default_level: str
    summary: str
    why: str
    fix: str


RULES = (
    Rule("agents.absent", "warning", "No AGENTS.md contract is present.", "Agents otherwise invent operating rules from context.", "Add AGENTS.md and state what agents may read, write, commit and escalate."),
    Rule("agents.unconfigured", "warning", "AGENTS.md still contains an unanswered TODO.", "A placeholder contract looks authoritative while leaving a material choice undefined.", "Replace every contract TODO with an explicit operating rule."),
    Rule("canonical.owner", "warning", "A canonical document has no real owner.", "A source of truth without ownership has no accountable reviewer.", "Set owner to a person or accountable role."),
    Rule("canonical.unapproved", "error", "A draft or review document is marked source_of_truth.", "Unapproved material must not silently become canonical.", "Set source_of_truth: false until status is approved."),
    Rule("date.filename_mismatch", "warning", "A dated filename and created date disagree.", "Point-in-time artifacts become hard to order and audit.", "Align the filename date with created, or correct the metadata."),
    Rule("date.invalid", "error", "created or last_updated is not a real ISO date.", "Invalid dates break deterministic review and timeline logic.", "Use a real YYYY-MM-DD date."),
    Rule("date.order", "error", "last_updated is earlier than created.", "The lifecycle metadata is internally contradictory.", "Correct one of the dates."),
    Rule("date.placeholder", "error", "A non-template document still uses YYYY-MM-DD.", "A placeholder date is indistinguishable from missing lifecycle data.", "Replace it with the real date."),
    Rule("decision.duplicate", "error", "A decision ID is reused.", "D-NNN identifiers are stable references and must be unique forever.", "Allocate a new decision ID; never recycle an old one."),
    Rule("decision.id_filename", "error", "decision_id and filename number disagree.", "Humans and tools use both forms as identifiers.", "Rename the file or correct decision_id so they match."),
    Rule("decision.id_missing", "error", "A decision record has no D-NNN identifier.", "Without an ID, citations and supersession cannot be stable.", "Add decision_id and a matching d-NNN-*.md filename."),
    Rule("decision.review_missing", "warning", "An approved decision has no review_by date.", "Accepted decisions otherwise look current forever as assumptions change.", "Set a realistic review_by date."),
    Rule("decision.superseded_by_missing", "warning", "A superseded decision has no newer record pointing back to it.", "The chain of reasoning is incomplete.", "Create or fix the newer decision's supersedes field."),
    Rule("decision.superseded_by_format", "warning", "superseded_by is not a D-NNN identifier.", "Lifecycle metadata should point to the same stable decision namespace as supersedes.", "Use a D-NNN identifier or remove the convenience field."),
    Rule("decision.superseded_by_mismatch", "warning", "superseded_by disagrees with the approved reverse supersedes edge.", "Two replacement pointers make the decision lineage ambiguous.", "Align superseded_by with the approved record whose supersedes field points here."),
    Rule("decision.supersedes_format", "error", "supersedes is not a D-NNN identifier.", "Supersession must point to a stable decision ID.", "Use a value such as D-012."),
    Rule("decision.supersedes_missing", "error", "supersedes points to a decision record that does not exist.", "A missing predecessor breaks decision history.", "Restore the record or correct the referenced ID."),
    Rule("decision.supersedes_status", "warning", "A newer decision supersedes an older record not marked superseded.", "The two records disagree about which decision is current.", "Mark the older record superseded after the replacement is accepted."),
    Rule("decision.supersession_fork", "error", "One decision has multiple approved replacements.", "Two accepted successors make the current decision lineage ambiguous.", "Resolve the conflict so only one approved decision supersedes the predecessor; preserve rejected alternatives as non-approved records."),
    Rule("decision.supersession_cycle", "error", "The supersedes relation contains a cycle.", "A decision cannot ultimately supersede itself; cyclic lineage makes historical/current state impossible to resolve.", "Break the cycle and restore an acyclic predecessor chain."),
    Rule("decision_log.ambiguous", "error", "A decision-log wikilink resolves to multiple records.", "The index must identify one decision record deterministically.", "Use the full vault-relative path in the wikilink."),
    Rule("decision_log.date_mismatch", "warning", "Decision log date differs from record created date.", "Two timelines for one decision are confusing and hard to audit.", "Align the row date and record metadata."),
    Rule("decision_log.duplicate", "error", "A D-NNN appears more than once in the decision log.", "The log must be a unique index.", "Keep one row and preserve history in the decision record."),
    Rule("decision_log.id_mismatch", "error", "A log row points to a record with another decision_id.", "The index would direct readers to the wrong rationale.", "Fix the row ID or record link."),
    Rule("decision_log.missing_record", "error", "A decision-log row points to a missing note.", "Indexed decisions must remain inspectable.", "Restore the record or repair the wikilink."),
    Rule("decision_log.record", "error", "A populated decision row has no record wikilink.", "The log cannot substitute for the rationale itself.", "Link the row to its d-NNN-*.md record."),
    Rule("decision_log.status_mismatch", "error", "Log status and record status disagree.", "Readers cannot tell which lifecycle state is authoritative.", "Update the log row to match the record."),
    Rule("decision_log.unindexed", "error", "A decision record is missing from decision-log.md.", "Unindexed decisions disappear from the operating memory.", "Add one decision-log row for the record."),
    Rule("config.invalid", "error", "whykit.toml is invalid.", "A repository-local policy must parse identically for humans, CI and agents.", "Fix the TOML structure or unsupported policy values."),
    Rule("delivery_status.invalid", "error", "delivery_status has an unsupported value.", "Downstream systems need a small stable delivery state machine.", "Use draft, ready_to_send or sent."),
    Rule("delivery_status.sent_at", "error", "A sent document has no sent_at timestamp/date field.", "Delivery history must distinguish drafted content from delivered content.", "Record sent_at when setting delivery_status: sent."),
    Rule("evidence.date", "error", "An evidence date or accessed date is invalid.", "Freshness cannot be reasoned about without valid dates.", "Use real YYYY-MM-DD dates."),
    Rule("evidence.access_missing", "warning", "An active source covered by an access-age policy has no Accessed date.", "The policy cannot tell whether this source was revisited.", "Record when the source was last accessed, or remove the policy for historical source types."),
    Rule("evidence.access_future", "warning", "An active source has an Accessed date after the lint date.", "A future date cannot establish that a source has already been checked.", "Correct the Accessed date or run lint with the intended --today date."),
    Rule("evidence.access_stale", "warning", "An active source has not been accessed within its configured age window.", "Mutable sources may have changed since the last check.", "Revisit the source and update Accessed after review; do not treat access alone as claim approval."),
    Rule("evidence.duplicate", "error", "An E-NNN identifier is reused.", "Evidence IDs are durable citations and cannot safely refer to two sources.", "Allocate a new E-NNN."),
    Rule("evidence.id_format", "error", "source_ids contains a malformed evidence ID.", "Machine resolution relies on the E-NNN contract.", "Use E- followed by at least three digits."),
        Rule("embed.missing", "error", "An Obsidian embed target cannot be resolved.", "Broken embeds render as missing media and hide the intended note.", "Fix the ![[target]] path or restore the note."),
    Rule("hub.unlinked_workstream", "warning", "Home.md does not link into a top-level working directory.", "The map of content should reach every workstream folder so agents and people can navigate.", "Add a wikilink from Home.md into the folder, or remove an unused directory."),
Rule("evidence.missing", "error", "A cited E-NNN has no populated active or retired row.", "A citation without a source location is not auditable evidence.", "Populate the register row or correct the citation."),
    Rule("evidence.replaced_by_format", "error", "A retired source has a malformed replacement ID.", "Replacement chains must use stable evidence identifiers.", "Use a valid E-NNN or leave the field empty."),
    Rule("evidence.replaced_by_missing", "error", "A retired source points to an unknown replacement.", "The provenance chain ends at a dangling identifier.", "Register the replacement source or correct the ID."),
    Rule("evidence.retired", "warning", "A document cites retired evidence.", "Historical citations may be valid, but current claims may need re-checking.", "Review the claim and cite the replacement where appropriate."),
    Rule("evidence.retired_date", "error", "A retired evidence row has an invalid retirement date.", "Retirement history must be chronologically auditable.", "Use a real YYYY-MM-DD date."),
    Rule("fact.inline_evidence", "warning", "A fact callout has no inline E-NNN citation.", "Fact callouts visually claim verification, so the supporting source should be one click away.", "Add an E-NNN in the callout."),
    Rule("frontmatter.invalid", "error", "YAML front matter cannot be parsed.", "Governance metadata is unavailable when parsing fails.", "Fix the front matter syntax."),
    Rule("frontmatter.missing", "error", "A governed note has no YAML front matter.", "Status, owner, sensitivity and provenance would be undefined.", "Add the standard WhyKit front matter block."),
    Rule("frontmatter.required", "error", "A required metadata key is missing.", "Core lifecycle and handling fields must be explicit.", "Add the named key."),
    Rule("markdown_link.missing", "warning", "A relative Markdown link points to a missing local file.", "WhyKit is editor-agnostic, so standard Markdown navigation should be as auditable as wikilinks.", "Fix the relative path or remove the stale link."),
    Rule("markdown_link.outside", "warning", "A relative Markdown link resolves outside the vault.", "The governed knowledge graph should not silently depend on untracked local files.", "Keep the asset/note inside the vault or use an explicit external URL/evidence location."),
    Rule("note.orphan", "warning", "Nothing in the vault links to this note.", "Unreachable notes become invisible operationally even though the file still exists.", "Link it from Home.md or a workstream map."),
    Rule("review_log.date", "error", "A review-log row has an invalid date.", "Review history needs an auditable timeline.", "Use a real YYYY-MM-DD date."),
    Rule("review_log.next_review", "error", "A review-log row has an invalid next-review date.", "Review scheduling cannot act on an invalid date.", "Use YYYY-MM-DD or an em dash when no next review applies."),
    Rule("review_log.outcome", "error", "A review-log row has an unsupported outcome.", "Automation depends on a stable review outcome vocabulary.", "Use confirmed, update-required, supersede-required or archived."),
    Rule("review_log.reviewer", "error", "A review-log row has no real reviewer.", "A review without accountable ownership is not an auditable review.", "Name the person or team that performed the review."),
    Rule("review_log.row", "error", "A review-log row is malformed.", "Malformed review history cannot be parsed reliably.", "Restore all seven review-log columns."),
    Rule("review_log.table", "error", "The review-log table is missing or malformed.", "Review events need one deterministic append-only structure.", "Restore the standard review-log table header."),
    Rule("review_log.target", "error", "A review-log target is missing or ambiguous.", "A review event must point to one governed record.", "Use an unambiguous vault wikilink."),
    Rule("report.undated", "error", "A point-in-time report filename has no YYYY-MM-DD suffix.", "Reports need an immutable temporal identity.", "Rename it to end in -YYYY-MM-DD.md."),
    Rule("review_by.invalid", "error", "review_by is not a real ISO date.", "Review automation cannot act on an invalid date.", "Use YYYY-MM-DD."),
    Rule("review_by.overdue", "warning", "An approved document is past review_by.", "The document may still be correct, but its freshness promise expired.", "Re-review it, update the evidence, and set the next review date."),
    Rule("secret.detected", "error", "Text resembles a credential or private key.", "A knowledge vault is not a secret manager.", "Remove the value, rotate it if real, and store only a secret-manager location."),
    Rule("secret.scan_non_utf8", "error", "A secret-scan candidate is not valid UTF-8.", "Unverified credential-shaped files create false confidence.", "Convert the file to UTF-8, exclude it explicitly, or scan it externally."),
    Rule("secret.scan_skipped_large_file", "error", "A text asset is larger than the built-in secret scanner limit.", "Silent skips create false confidence that a vault contains no credentials.", "Scan the file externally, exclude it explicitly, or split it into smaller governed assets."),
    Rule("secret.scan_unreadable", "error", "A secret-scan candidate could not be read.", "Unreadable credential-shaped files create false confidence.", "Fix permissions or exclude the path explicitly after an external scan."),
    Rule("sensitivity.invalid", "error", "sensitivity uses an unsupported label.", "Handling policy depends on a small shared vocabulary.", "Use public, internal, confidential or restricted."),
    Rule("status.invalid", "error", "status uses an unsupported lifecycle value.", "Tools and people need one state model.", "Use template, draft, in_review, approved, superseded or archived."),
    Rule("type.invalid", "error", "type uses an unsupported document type.", "Stable types make indexes and integrations predictable.", "Use a documented WhyKit type."),
    Rule("wikilink.ambiguous", "error", "A wikilink matches multiple notes.", "The same link would resolve differently as the vault changes.", "Use a vault-relative path rather than a bare stem/alias."),
    Rule("wikilink.missing", "error", "A wikilink cannot be resolved.", "Broken knowledge edges hide context and make the graph unreliable.", "Fix the target or remove the stale link."),
    Rule("wikilink.outside", "warning", "A wikilink tries to traverse outside the vault.", "Vault links must not silently depend on files outside the governed boundary.", "Copy/register the source inside the vault or record an external location as evidence."),
)

RULE_BY_CODE = {rule.code: rule for rule in RULES}


def _md_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ").strip()


def markdown_table() -> str:
    lines = [
        "| Code | Level | What it means | Default fix |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| `{rule.code}` | {rule.default_level} | {_md_cell(rule.summary)} | {_md_cell(rule.fix)} |"
        for rule in RULES
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit rules", description="List WhyKit lint rules or explain one stable rule code.")
    parser.add_argument("code", nargs="?", help="rule code, for example evidence.missing")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--markdown", action="store_true", help="emit the complete Markdown rule table")
    args = parser.parse_args(argv)
    if args.json and args.markdown:
        parser.error("--json and --markdown are mutually exclusive")
    if args.code and args.markdown:
        parser.error("--markdown renders the full catalog and does not accept a single code")

    if args.code:
        rule = RULE_BY_CODE.get(args.code)
        if rule is None:
            print(f"unknown rule code: {args.code}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(asdict(rule), ensure_ascii=False, indent=2))
        else:
            print(f"{rule.code} [{rule.default_level}]")
            print(rule.summary)
            print(f"Why: {rule.why}")
            print(f"Fix: {rule.fix}")
        return 0

    if args.json:
        print(json.dumps([asdict(rule) for rule in RULES], ensure_ascii=False, indent=2))
    elif args.markdown:
        print(markdown_table())
    else:
        for rule in RULES:
            print(f"{rule.default_level:<7} {rule.code:<34} {rule.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
