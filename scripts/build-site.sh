#!/usr/bin/env bash
# Assemble the GitHub Pages landing page into a single directory.
# Everything the landing page needs (fonts, media, favicon) lives in site/, so
# the default build is a copy plus .nojekyll. WHYKIT_SITE_DEMO=1 also builds the
# Explorer from examples/payments into demo/.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$root/_site}"

rm -rf "$out"
mkdir -p "$out"
cp -R "$root/site/." "$out/"
touch "$out/.nojekyll"

# Optional live demo: the Explorer built from examples/payments, a synthetic
# vault whose every note is public. The default public build refuses any
# non-public note, so this can never publish a private vault by accident.
# Needs Node.js 22+ with `npm ci --prefix apps/explorer` already run.
if [ "${WHYKIT_SITE_DEMO:-0}" = "1" ]; then
  WHYKIT_VAULT_DIR="$root/examples/payments" npm run build --prefix "$root/apps/explorer"
  mkdir -p "$out/demo"
  cp -R "$root/apps/explorer/dist/." "$out/demo/"
  echo "Explorer demo built from examples/payments in $out/demo"
fi
echo "Site assembled in $out"
