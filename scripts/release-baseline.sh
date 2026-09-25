#!/usr/bin/env bash
set -euo pipefail

: "${EVENT_NAME:?EVENT_NAME is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GITHUB_REF:?GITHUB_REF is required}"

if [[ "$EVENT_NAME" == "pull_request" ]]; then
  base="${PR_BASE_SHA:-}"
  if [[ -z "$base" ]]; then
    echo "Pull request base SHA is required for the release policy check" >&2
    exit 2
  fi
elif [[ "$GITHUB_REF" == refs/tags/v* ]]; then
  # Compare against the prior semantic release, or the true repository root
  # before the first release. Never weaken this to HEAD^ or HEAD...HEAD.
  base="$(
    git describe --match 'v[0-9]*' --tags --abbrev=0 "${GITHUB_SHA}^" 2>/dev/null ||
    git rev-list --max-parents=0 "$GITHUB_SHA" | tail -n1
  )"
else
  base="$(git rev-parse --verify --quiet "${GITHUB_SHA}^" 2>/dev/null || true)"
  if [[ -z "$base" ]]; then
    echo "A parent commit is required for the release policy check" >&2
    exit 2
  fi
fi

if [[ -z "$base" || "$base" == "$GITHUB_SHA" ]]; then
  echo "Unable to determine a real release baseline" >&2
  exit 2
fi

printf '%s\n' "$base"
