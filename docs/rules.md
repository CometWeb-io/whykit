| Code | Level | What it means | Default fix |
|---|---|---|---|
| `agents.absent` | warning | No AGENTS.md contract is present. | Add AGENTS.md and state what agents may read, write, commit and escalate. |
| `agents.unconfigured` | warning | AGENTS.md still contains an unanswered TODO. | Replace every contract TODO with an explicit operating rule. |
| `canonical.owner` | warning | A canonical document has no real owner. | Set owner to a person or accountable role. |
| `canonical.unapproved` | error | A draft or review document is marked source_of_truth. | Set source_of_truth: false until status is approved. |
| `date.filename_mismatch` | warning | A dated filename and created date disagree. | Align the filename date with created, or correct the metadata. |
| `date.invalid` | error | created or last_updated is not a real ISO date. | Use a real YYYY-MM-DD date. |
| `date.order` | error | last_updated is earlier than created. | Correct one of the dates. |
| `date.placeholder` | error | A non-template document still uses YYYY-MM-DD. | Replace it with the real date. |
| `decision.duplicate` | error | A decision ID is reused. | Allocate a new decision ID; never recycle an old one. |
| `decision.id_filename` | error | decision_id and filename number disagree. | Rename the file or correct decision_id so they match. |
| `decision.id_missing` | error | A decision record has no D-NNN identifier. | Add decision_id and a matching d-NNN-*.md filename. |
| `decision.review_missing` | warning | An approved decision has no review_by date. | Set a realistic review_by date. |
| `decision.superseded_by_missing` | warning | A superseded decision has no newer record pointing back to it. | Create or fix the newer decision's supersedes field. |
| `decision.superseded_by_format` | warning | superseded_by is not a D-NNN identifier. | Use a D-NNN identifier or remove the convenience field. |
| `decision.superseded_by_mismatch` | warning | superseded_by disagrees with the approved reverse supersedes edge. | Align superseded_by with the approved record whose supersedes field points here. |
| `decision.supersedes_format` | error | supersedes is not a D-NNN identifier. | Use a value such as D-012. |
| `decision.supersedes_missing` | error | supersedes points to a decision record that does not exist. | Restore the record or correct the referenced ID. |
| `decision.supersedes_status` | warning | A newer decision supersedes an older record not marked superseded. | Mark the older record superseded after the replacement is accepted. |
| `decision.supersession_fork` | error | One decision has multiple approved replacements. | Resolve the conflict so only one approved decision supersedes the predecessor; preserve rejected alternatives as non-approved records. |
| `decision.supersession_cycle` | error | The supersedes relation contains a cycle. | Break the cycle and restore an acyclic predecessor chain. |
| `decision_log.ambiguous` | error | A decision-log wikilink resolves to multiple records. | Use the full vault-relative path in the wikilink. |
| `decision_log.date_mismatch` | warning | Decision log date differs from record created date. | Align the row date and record metadata. |
| `decision_log.duplicate` | error | A D-NNN appears more than once in the decision log. | Keep one row and preserve history in the decision record. |
| `decision_log.id_mismatch` | error | A log row points to a record with another decision_id. | Fix the row ID or record link. |
| `decision_log.missing_record` | error | A decision-log row points to a missing note. | Restore the record or repair the wikilink. |
| `decision_log.record` | error | A populated decision row has no record wikilink. | Link the row to its d-NNN-*.md record. |
| `decision_log.status_mismatch` | error | Log status and record status disagree. | Update the log row to match the record. |
| `decision_log.unindexed` | error | A decision record is missing from decision-log.md. | Add one decision-log row for the record. |
| `config.invalid` | error | whykit.toml is invalid. | Fix the TOML structure or unsupported policy values. |
| `delivery_status.invalid` | error | delivery_status has an unsupported value. | Use draft, ready_to_send or sent. |
| `delivery_status.sent_at` | error | A sent document has no sent_at timestamp/date field. | Record sent_at when setting delivery_status: sent. |
| `evidence.date` | error | An evidence date or accessed date is invalid. | Use real YYYY-MM-DD dates. |
| `evidence.access_missing` | warning | An active source covered by an access-age policy has no Accessed date. | Record when the source was last accessed, or remove the policy for historical source types. |
| `evidence.access_future` | warning | An active source has an Accessed date after the lint date. | Correct the Accessed date or run lint with the intended --today date. |
| `evidence.access_stale` | warning | An active source has not been accessed within its configured age window. | Revisit the source and update Accessed after review; do not treat access alone as claim approval. |
| `evidence.duplicate` | error | An E-NNN identifier is reused. | Allocate a new E-NNN. |
| `evidence.id_format` | error | source_ids contains a malformed evidence ID. | Use E- followed by at least three digits. |
| `embed.missing` | error | An Obsidian embed target cannot be resolved. | Fix the ![[target]] path or restore the note. |
| `hub.unlinked_workstream` | warning | Home.md does not link into a top-level working directory. | Add a wikilink from Home.md into the folder, or remove an unused directory. |
| `evidence.missing` | error | A cited E-NNN has no populated active or retired row. | Populate the register row or correct the citation. |
| `evidence.replaced_by_format` | error | A retired source has a malformed replacement ID. | Use a valid E-NNN or leave the field empty. |
| `evidence.replaced_by_missing` | error | A retired source points to an unknown replacement. | Register the replacement source or correct the ID. |
| `evidence.retired` | warning | A document cites retired evidence. | Review the claim and cite the replacement where appropriate. |
| `evidence.retired_date` | error | A retired evidence row has an invalid retirement date. | Use a real YYYY-MM-DD date. |
| `fact.inline_evidence` | warning | A fact callout has no inline E-NNN citation. | Add an E-NNN in the callout. |
| `fact.evidence_missing` | warning | A fact callout cites an E-NNN with no populated register row. | Register the source or correct the cited ID. |
| `frontmatter.invalid` | error | YAML front matter cannot be parsed. | Fix the front matter syntax. |
| `frontmatter.empty` | warning | A required metadata key is present but has no value. | Fill in the value, or use the documented placeholder in templates. |
| `frontmatter.missing` | error | A governed note has no YAML front matter. | Add the standard WhyKit front matter block. |
| `frontmatter.required` | error | A required metadata key is missing. | Add the named key. |
| `markdown_link.missing` | warning | A relative Markdown link or image points to a missing local file. | Fix the relative path or remove the stale link. |
| `markdown_link.outside` | warning | A relative Markdown link or image resolves outside the vault. | Keep the asset/note inside the vault or use an explicit external URL/evidence location. |
| `note.orphan` | warning | Nothing in the vault links to this note. | Link it from Home.md or a workstream map. |
| `review_log.date` | error | A review-log row has an invalid date. | Use a real YYYY-MM-DD date. |
| `review_log.next_review` | error | A review-log row has an invalid next-review date. | Use YYYY-MM-DD or an em dash when no next review applies. |
| `review_log.outcome` | error | A review-log row has an unsupported outcome. | Use confirmed, update-required, supersede-required or archived. |
| `review_log.reviewer` | error | A review-log row has no real reviewer. | Name the person or team that performed the review. |
| `review_log.row` | error | A review-log row is malformed. | Restore all seven review-log columns. |
| `review_log.table` | error | The review-log table is missing or malformed. | Restore the standard review-log table header. |
| `review_log.target` | error | A review-log target is missing or ambiguous. | Use an unambiguous vault wikilink. |
| `report.undated` | error | A point-in-time report filename has no YYYY-MM-DD suffix. | Rename it to end in -YYYY-MM-DD.md. |
| `review_by.invalid` | error | review_by is not a real ISO date. | Use YYYY-MM-DD. |
| `review_by.overdue` | warning | An approved document is past review_by. | Re-review it, update the evidence, and set the next review date. |
| `secret.detected` | error | Text resembles a credential or private key. | Remove the value, rotate it if real, and store only a secret-manager location. |
| `secret.scan_non_utf8` | error | A secret-scan candidate is not valid UTF-8. | Convert the file to UTF-8, exclude it explicitly, or scan it externally. |
| `secret.scan_skipped_large_file` | error | A text asset is larger than the built-in secret scanner limit. | Scan the file externally, exclude it explicitly, or split it into smaller governed assets. |
| `secret.scan_unreadable` | error | A secret-scan candidate could not be read. | Fix permissions or exclude the path explicitly after an external scan. |
| `sensitivity.invalid` | error | sensitivity uses an unsupported label. | Use public, internal, confidential or restricted. |
| `source_of_truth.invalid` | warning | source_of_truth is not a boolean. | Use source_of_truth: true or source_of_truth: false. |
| `status.invalid` | error | status uses an unsupported lifecycle value. | Use template, draft, in_review, approved, superseded or archived. |
| `type.invalid` | error | type uses an unsupported document type. | Use a documented WhyKit type. |
| `wikilink.ambiguous` | error | A wikilink matches multiple notes. | Use a vault-relative path rather than a bare stem/alias. |
| `wikilink.missing` | error | A wikilink cannot be resolved. | Fix the target or remove the stale link. |
| `wikilink.outside` | warning | A wikilink tries to traverse outside the vault. | Copy/register the source inside the vault or record an external location as evidence. |
