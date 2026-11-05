# Upgrade from 0.3.4 to 0.4 and recover a backup

This guide applies to version 0.4, not the 0.3.4 package. Check your installed
version and do not assume an unversioned PyPI install gives you 0.4. Test a
specific 0.4 build before upgrading a real vault.
The examples below use separate executables, `OLD_WHYKIT` and `NEW_WHYKIT`, so
installing one cannot silently replace the other. Set them to absolute paths.

## Before changing a vault

1. Stop all vault writers, editors and scheduled jobs. Keep them stopped through
   the backup and its verification. Put the backup in a private location outside
   the vault and outside its Git checkout, with enough free space.
2. Record the 0.3.4 executable and the exact 0.4 build you are testing. Pin the
   client in scripts and CI. Record any independently trusted approval baseline
   and back up external attachments, Git LFS content, submodules and remote
   refs separately. A copied hash or locally editable receipt does not prove
   identity or trust.
3. Make and verify a complete pre-upgrade backup of vault files **and** the
   matching Git repository, including uncommitted and ignored files. The WhyKit
   knowledge bundle alone is not that backup: it excludes Git history and trusted
   baselines, is not encrypted and restore is not globally atomic.

The example below is for a quiescent vault at the root of a standalone Git
repository on a POSIX host with `cp -a`. It deliberately refuses a linked
worktree (`.git` file), symlinks and special files, shallow history and Git object
alternates. It does not back up submodules, LFS objects or external trust stores;
use a suitable backup procedure for those and for Windows. A live copy is not an
atomic snapshot. Test a restore before depending on it.

```bash
set -eu
# Replace these paths with private, absolute paths; BACKUP must not exist.
VAULT=/private/example/vault
BACKUP=/private/example/pre-upgrade-backup
OLD_WHYKIT=/private/example/whykit-0.3.4/bin/whykit
NEW_WHYKIT=/private/example/whykit-0.4/bin/whykit

"$OLD_WHYKIT" --version
"$NEW_WHYKIT" --version
test "$("$OLD_WHYKIT" --version)" = "whykit 0.3.4"
case "$("$NEW_WHYKIT" --version)" in
  "whykit 0.4."*) ;;
  *) echo "Expected a 0.4 client; stop." >&2; exit 2 ;;
esac
# Confirm the second executable is the exact 0.4 build you intend to test.
test -d "$VAULT/.git"
test ! -L "$VAULT/.git"
test "$(git -C "$VAULT" rev-parse --is-shallow-repository)" = false
test ! -e "$VAULT/.git/objects/info/alternates"
unsupported_entries=$(find "$VAULT" ! -type f ! -type d -print -quit)
test -z "$unsupported_entries"
test ! -e "$BACKUP"
test ! -L "$BACKUP"
python3 - "$VAULT" "$BACKUP" <<'PY'
import os, sys
source, backup = map(os.path.realpath, sys.argv[1:])
common = os.path.commonpath((source, backup))
if common in (source, backup):
    raise SystemExit("vault and backup must not contain one another")
PY
cp -a "$VAULT" "$BACKUP"
diff -qr "$VAULT" "$BACKUP"
source_head=$(git -C "$VAULT" rev-parse --verify HEAD)
backup_head=$(git -C "$BACKUP" rev-parse --verify HEAD)
test "$source_head" = "$backup_head"
git -C "$BACKUP" fsck --full
```

A successful comparison applies only to that stopped copy at that time; it does
not verify permissions, external data or trust. If any command fails,
stop and resolve it before proceeding. Keep the backup private and unchanged.

## Try the upgrade on a copy

Restore the verified backup into another **new** private directory. Do not
replace the original vault or the backup. The example assumes `RESTORED` is
outside both and does not exist:

```bash
RESTORED=/private/example/upgrade-trial
test ! -e "$RESTORED"
test ! -L "$RESTORED"
python3 - "$VAULT" "$BACKUP" "$RESTORED" <<'PY'
import os, sys
source, backup, restored = map(os.path.realpath, sys.argv[1:])
for other in (source, backup):
    if os.path.commonpath((other, restored)) in (other, restored):
        raise SystemExit("trial must not overlap the vault or backup")
PY
cp -a "$BACKUP" "$RESTORED"
diff -qr "$BACKUP" "$RESTORED"
old_status=0
new_status=0
"$OLD_WHYKIT" lint --root "$RESTORED" --json || old_status=$?
"$NEW_WHYKIT" lint --root "$RESTORED" --json || new_status=$?
printf '0.3.4 lint exit: %s; 0.4 lint exit: %s\n' "$old_status" "$new_status"
if [ "$old_status" -ne 0 ] || [ "$new_status" -ne 0 ]; then
  echo "Stop and investigate both lint reports." >&2
  exit 1
fi
```

Compare findings and exit codes, then run the `status`, `trace`, `query`, Git
history and CI checks your own workflow requires with both clients. Warnings,
results and exit codes may differ; investigate differences rather than assuming
all 0.3.4 vaults or automations are equivalent. Existing Markdown is not
rewritten merely by these read commands. The local checks and your trusted
baseline must be appropriate for the vault; they do not establish source truth.
Only after reviewing the trial should you make a separate plan to upgrade the
real vault. The commands here do not migrate it automatically.

## Rollback and newer records

Keep the 0.4 client and the current vault intact. If the vault has 0.4 claims,
claim pins, outcomes or other new records, do **not** use 0.3.4 as the authority
for them. In particular, 0.3.4 `trace` can say there are no gaps even when a
pinned claim changed; a green trace is not claim verification. Older `lint` can
also reject new record types. Do not delete new records or rewrite an approved
decision to make an older check pass.

Run the conservative check with the 0.4 client:

```bash
if "$NEW_WHYKIT" knowledge downgrade-check --root "$RESTORED" --json; then
  echo "Unexpected success; stop and investigate." >&2
  exit 2
else
  result=$?
  test "$result" -eq 1 || exit "$result"
fi
```

`blocked` means new records or claim-related markers were found;
`review_required` means none were detected. **Both exit 1** and neither
therefore authorizes a downgrade. Exit 2 is an error or refusal, including on
unsupported hosts. Running 0.3.4 directly bypasses this check. It cannot inspect
every future feature, authenticate the backup or protect against later writes.
See [downgrade and recovery details](knowledge-v2.md#downgrade-and-recovery).

To return to 0.3.4, use your independently verified pre-upgrade backup and its
matching Git history and trusted baseline; restore them into a **new** private
location. With the same variables as above, for example:

```bash
RECOVERED=/private/example/recovered-034
test ! -e "$RECOVERED"
test ! -L "$RECOVERED"
python3 - "$VAULT" "$BACKUP" "$RESTORED" "$RECOVERED" <<'PY'
import os, sys
*existing, recovered = map(os.path.realpath, sys.argv[1:])
for other in existing:
    if os.path.commonpath((other, recovered)) in (other, recovered):
        raise SystemExit("recovery must not overlap an existing vault or backup")
PY
cp -a "$BACKUP" "$RECOVERED"
diff -qr "$BACKUP" "$RECOVERED"
backup_head=$(git -C "$BACKUP" rev-parse --verify HEAD)
recovered_head=$(git -C "$RECOVERED" rev-parse --verify HEAD)
test "$backup_head" = "$recovered_head"
git -C "$RECOVERED" fsck --full
"$OLD_WHYKIT" lint --root "$RECOVERED" --json
```

Verify the matching trusted baseline and run your other history and CI checks.
Keep the post-upgrade vault separately: restoring an earlier backup does not
include later edits. If no verified pre-upgrade backup exists, keep using 0.4
and recover or supersede records through a reviewed workflow. Never infer a safe
rollback solely from a successful old-client command or from the downgrade
check.
