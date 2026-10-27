# Saved GitHub export fixtures

These three single-object REST payloads are synthetic. Repository names, IDs,
SHAs, timestamps and text are invented; no network request or real CI result
was used to create them. The pull request has a deleted head repository. The
queued workflow has `conclusion:null`; it does not mean success or failure.

From this unreleased source checkout, prepare a throwaway vault and explicit
restricted E-ID using the existing commands:

```bash
uv run whykit init /private/tmp/whykit-github-example --minimal
uv run whykit new evidence --root /private/tmp/whykit-github-example --source "Synthetic issue export" --type dataset --location https://github.com/example-labs/fixture/issues/7 --claims "Untrusted fixture metadata" --date 2026-09-17 --accessed 2026-09-17 --sensitivity restricted --json
uv run whykit import github --root /private/tmp/whykit-github-example --kind issue --repository example-labs/fixture --evidence E-001 --input examples/github-import/issue.json --recorded-at 2026-09-17T12:00:00Z --json
```

Review the preview, then repeat the import with `--write --expect-hash` and its
returned `expected_sha256`. A new preview is required after any input change.
Use a distinct E-ID whose location matches each other fixture if importing
`pull-request.json` or `workflow-run.json`. Do not change an approved record
or an unrelated evidence location to force a match.

The importer preserves raw bytes, declared provider metadata and local source
observations. It never authenticates the source, executes instructions, fetches
URLs, approves decisions or turns a CI conclusion into independent test proof.
Raw base64 storage and restricted classification are not encryption or ACL.
See [the import guide](../../docs/github-import.md) for limits and recovery.
