#!/bin/sh
# Install the repository's Git hooks. Run once after cloning.
# For a vault created with `whykit init`, use `whykit install-hooks` instead.
set -e
root=$(git rev-parse --show-toplevel)
# Ask Git where hooks live: `.git` is a file in a linked worktree or submodule,
# and core.hooksPath may point somewhere else entirely.
hooks=$(git rev-parse --path-format=absolute --git-path hooks)
mkdir -p "$hooks"
ln -sf "$root/scripts/pre-commit" "$hooks/pre-commit"
echo "pre-commit hook installed: $hooks/pre-commit -> scripts/pre-commit"
