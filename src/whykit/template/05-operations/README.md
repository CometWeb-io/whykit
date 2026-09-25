# Operations

Lifecycle definitions, qualification, attribution, reporting, meetings and
outbound drafts.

- `meetings/` — meeting notes, dated;
- `outbound-drafts/` — messages before and after delivery.

Knowledge lifecycle and delivery lifecycle are separate. Use the normal
`status` field (`draft`, `in_review`, `approved`, …) for the document itself and:

```yaml
delivery_status: draft | ready_to_send | sent
sent_at: YYYY-MM-DD  # required only when delivery_status=sent
```

Keep delivery state honest. An approved draft is not necessarily sent, and a sent
message should not become editable history merely because its content later
changes.
