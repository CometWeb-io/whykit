# Security policy

WhyKit is designed to store organizational context. Treat a real operating
vault as sensitive data even when the template and example vaults are public.

## Default deployment rule

**Keep real company vaults private unless every included source is deliberately
public.** A public Git repository is not a safe default for CRM exports,
transcripts, customer material, internal strategy, screenshots or research notes.

## Sensitivity labels

Governed notes use one of:

- `public` — safe for intentional public distribution;
- `internal` — normal company-only material;
- `confidential` — limited business/customer material;
- `restricted` — highest internal handling tier.

The label is metadata, not access control. Repository permissions still decide who
can read the files.

## Credentials

Never store credential values. Record the system or secret-manager location and
who can grant access instead.

`whykit lint` detects common credential patterns across Markdown, JSON, YAML,
CSV, logs and other text assets. Detection is intentionally conservative and can
miss novel or encoded secrets. Use your Git host's secret scanning as a second
control.

## Imported files

Before committing imported material:

1. classify its sensitivity;
2. remove secrets and unnecessary personal data;
3. record provenance and a SHA-256 digest in a source-ingestion record;
4. store only what the decision/research workflow needs;
5. verify repository visibility and collaborators.

Binary screenshots and documents are not fully inspected by the built-in linter.
Review them manually or with an appropriate DLP/scanning tool before committing.

## Vulnerability reports

Do not open a public issue containing credentials, private vault content or a live
exploit against a third party. Email **hello@cometweb.io** without attaching
sensitive vault data. Prefer GitHub private vulnerability reporting when that
channel is available for this repository.
