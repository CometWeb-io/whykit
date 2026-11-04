# Local multi-vault status

Use a local workspace manifest when you already have access to several vaults
and need their lint and review counts in one report. The workspace reads each
vault in place. It does not merge notes, copy content between vaults, or create a
database or persistent cache.

## Create a private manifest

Keep this file outside public repositories. Paths are relative to the manifest,
not the working directory; absolute paths are also accepted.

```toml
workspace_version = 1
audience = "local"

[[vaults]]
alias = "project-a"
root = "../project-a"

[[vaults]]
alias = "project-b"
root = "../project-b"
```

Aliases contain 1–64 ASCII letters, digits, underscores or hyphens and start
with a letter or digit. They must be unique without regard to case. Duplicate
or overlapping roots are rejected, including alternate spellings of the same
directory. Unknown keys and unsupported manifest versions are rejected.

## Read the report

With the package installed, call the local Python API:

```python
import datetime as dt
import json
from pathlib import Path

from whykit.workspace import build_workspace_status

report = build_workspace_status(
    Path("/path/to/workspace.toml"), today=dt.date(2026, 10, 27),
)
print(json.dumps(report, indent=2))
```

The date argument is optional; the default is the current local date. This
library API does not add a command or a new executable module.

The JSON report follows [workspace-status.schema.json](../schemas/workspace-status.schema.json).
It lists each alias with document, lint error, lint warning, due review and
overdue review counts. Each vault's own review horizon comes from its policy;
the report includes that horizon as `due_days`. Due reviews include overdue
ones, matching the existing status report. Aliases are sorted without regard
to case.

A missing or broken vault gets a fixed per-vault error instead of a misleading
clean count. Other vaults are still checked. Symlinked manifests, roots and
ancestors are refused. Symlinks and special files inside a vault cause that
vault's read to fail before the status reader opens its content. Windows
reparse points (including junctions) are also refused. The existing
macOS root directory aliases for temporary and system directories are allowed
as ancestors. Git metadata directories are not traversed.

Each vault has either a `status` object or an `error` string, never both.
Per-vault errors are `vault_not_found`, `invalid_config`, `unsafe_path` or
`read_failed`. A refused manifest raises `WorkspaceError` with a fixed code:
`invalid_manifest`, `local_only`, `duplicate_alias`, `overlapping_roots` or
`unsafe_path`. Private values and underlying exception messages are omitted.
Inspect a failing vault locally using the normal single-vault tools.

The API uses
the existing `build_status` engine and acquires a separate read lock for each
vault. The report is not an atomic snapshot across vaults. Pending recovery
fails the affected read; no recovery or other mutation is performed.

## Local trust boundary

Only `audience = "local"` is supported. Public and other audiences fail closed
before status reads. No titles, file paths, owners, evidence identifiers,
finding messages, note bodies or sensitivity labels are forwarded. The input
manifest itself contains paths and must stay private.

Counts and user-selected aliases are still private operational metadata.
This report is not safe for publication merely because it omits note text.
An audience declaration is not authentication or authorization. Sensitivity
labels do not grant access or consent; the process has the local user's file
access and counts all notes, including restricted notes. Do not expose this
module through a public server, multi-user service or agent connector.

The filesystem and the caller are trusted local inputs. Read locks cooperate
with WhyKit writers; external editors or hostile processes replacing paths
during a read are outside that guarantee. No tenant isolation, SSO, RBAC,
cross-vault search or cross-vault references are implemented here. See the
[security model](security-model.md) for the existing single-vault boundaries.
