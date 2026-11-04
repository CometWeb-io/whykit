# Import saved GitHub evidence

This optional **unreleased source-checkout** command imports one saved REST JSON
object: an issue, pull request or workflow run. It does not contact GitHub.
The file is untrusted input: its repository, author fields and commit SHAs are
declarations. Matching them establishes internal consistency, not authenticity.

## Prepare a bound evidence record

Use `new evidence` to create a restricted E-ID with a bare canonical resource
URL, such as `https://github.com/example-labs/fixture/issues/7`. The selected
`--repository`, resource number or run ID, payload URL and evidence location
must agree. The first version supports github.com REST objects only: not
Enterprise hosts, webhook envelopes, arrays, arbitrary `gh --json` output,
comments, reviews, logs or downloaded workflow artifacts.

The three [synthetic payloads](../examples/github-import/README.md) show the
accepted profiles. An issue profile refuses the `pull_request` marker. A PR
binds its base repository and separate base/head SHAs; a deleted or forked head
repository does not change the base binding. A workflow binds its repository,
head SHA and explicit run attempt. `conclusion:null` stays absent; importing a
saved `success` does not establish that tests ran, covered the right code or
proved the code safe. Provider timestamps remain untrusted declared times.

```bash
uv run whykit import github --root /path/to/vault --kind issue --repository example-labs/fixture --evidence E-001 --input /private/export/issue.json --recorded-at 2026-09-17T12:00:00Z --json
```

## Review and apply the same input

Preview creates no files. It returns the declared metadata, source byte version,
size, observation time and `expected_sha256`. Review these and the original
export, then repeat with `--write --expect-hash <reviewed-hash>`. Any changed
input, evidence row, receipt or source history requires a fresh preview.
The existing E-ID must already be restricted; the importer does not allocate
one, rewrite the evidence register or amend approved reasoning.

One existing transaction writes the immutable capture, source observation and
immutable import receipt under `00-context/imports/github/E-NNN/<sha256>.json`.
The validated byte snapshot is the captured snapshot. Exact immediate retry is
idempotent; replaying an old preview after later observations is refused.
A → B → A retains both captures and all observations. Changed whitespace alone
is a new byte version. Unknown provider fields stay in raw capture, without
silently changing the normalized receipt. The receipt contains bounded metadata,
not the issue body, title, author login, comments, patches or logs.

Existing captures with a lower stored classification are refused even when the
current E-row is restricted. Their immutable envelope cannot be silently
upgraded: later row downgrades could expose the old capture. Use an appropriate
new restricted E-ID rather than rewriting that historical capture.

Normal [recovery](security-model.md#transaction-reads-and-recovery) applies to
interrupted writes. This is logical transactional consistency, not a promise of
filesystem-wide atomicity or authenticated remote history. Keep the original
export until recovery and source verification have completed.

## Trust and limits

Input is a bounded regular local file of at most 1 MiB, with strict JSON,
at most 20 nesting levels and 20,000 JSON values, scalars up to 64 KiB
characters, positive provider IDs up to 2^63−1 and canonical E-IDs of 3–15
digits. Selected scalar fields are validated. FIFO/device/symlink inputs, ambiguous vault paths, credentialed
URLs, mismatched resources and corrupted existing captures/receipts are refused.
New provider fields are preserved as untrusted bytes; unsupported profiles need
a reviewed adapter change. No input content becomes code, shell commands, host
configuration, approved Markdown, a claim or an Outcome automatically.

Results always set `source_identity_authenticated`, `remote_state_checked`,
`semantics_checked` and `execution_authorized` to false. Sensitivity is a data
filter, not authentication, ACL or encryption. Public readers must not include
these restricted raw bytes or their private metadata. Full explicit private
[knowledge bundles](knowledge-v2.md) preserve the receipt as raw source data;
that does not make it authenticated provider evidence.

The profiles follow the official GitHub REST descriptions for
[issues](https://docs.github.com/en/rest/issues/issues#get-an-issue),
[pull requests](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)
and [workflow runs](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run).
These sources define object shapes; local fixture tests do not prove live
provider connectivity or identity. For other local files use `artifact capture`; for exact-byte vault migration
use the explicit private knowledge bundle.
