#!/bin/sh
# Install the repository's Git hooks. Run once after cloning.
# For a vault created with `whykit init`, use `whykit install-hooks` instead.
set -e
root=$(git rev-parse --show-toplevel)
ln -sf ../../scripts/pre-commit "$root/.git/hooks/pre-commit"
echo "pre-commit hook installed -> scripts/pre-commit"
