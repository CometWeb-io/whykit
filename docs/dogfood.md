# Record a private-vault dogfood gate

A pinned WhyKit commit identifies the tool. Record the command, date and result
as well, so a test on a real vault can be distinguished from a synthetic fixture
or an unexecuted plan. Keep the actual report beside private test evidence;
the public repository contains the format, not anyone's operating notes.

For a read-only lint run, disable the parse cache:

```bash
whykit --no-cache lint --root /srv/private-vault --today 2026-10-09 --strict --json
```

Capture stdout privately and retain only counts, exit code and stable rule codes.
Do not publish raw findings, paths, titles, source IDs, URLs or note bodies.
Check the vault's file inventory and content hashes before and after the run,
including `.whykit/`, to detect unintended writes. Git reads should use
`GIT_OPTIONAL_LOCKS=0` when the index must also remain untouched.

The minimal local record below is illustrative; its hashes are placeholders.

```json
{
  "date": "2026-10-09",
  "whykit": {
    "git_sha": "0000000000000000000000000000000000000000",
    "dirty": false
  },
  "vault": {
    "git_sha": "0000000000000000000000000000000000000000",
    "dirty": false,
    "content_sha256_before": "0000000000000000000000000000000000000000000000000000000000000000",
    "content_sha256_after": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "runs": [{
    "command": ["whykit", "--no-cache", "lint", "--root", "<private-vault>", "--today", "2026-10-09", "--strict", "--json"],
    "exit_code": 0,
    "errors": 0,
    "warnings": 0,
    "result": "PASS"
  }],
  "vault_unchanged": true
}
```

Use the actual Git SHAs. For a nested ledger, the vault SHA is the containing
repository's commit; the content hash binds the selected ledger directory.
Record whether the tool or vault was dirty. A dirty checkout's Git SHA alone
does not identify the tested files: include `source_manifest_sha256` for the
tool and content hashes for the vault. Bind commands to the snapshot actually
tested, rather than the latest release or an earlier green run.

`PASS` means that the named command completed with exit 0. A failed command is
`FAIL`; counts are `null` when the command does not report them or could not run. An omitted command
is not a pass. A skipped history gate must remain marked unchecked; use a
trusted, explicitly pinned `--base` when checking history. `HEAD` is useful for
a local smoke but does not by itself establish protected policy governance.

One real vault and one OS qualify only that run. Record performance, transport,
model/host behaviour, other platforms and publication gates separately. Lint,
trace and a local public-export projection do not prove claim truth, a human
reviewer's identity, successful publication or interoperability with every host.
