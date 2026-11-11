#!/usr/bin/env bash
# Restore the database/baseline from the `data` branch into ./state (or $1). Fresh start if the branch doesn't exist.
# REMOTE_URL = the repo's git URL (with a token in CI). DATA_BRANCH defaults to "data".
set -euo pipefail
: "${REMOTE_URL:?REMOTE_URL is required}"
DIR="${1:-state}"
BRANCH="${DATA_BRANCH:-data}"
mkdir -p "$DIR"
if git ls-remote --exit-code --heads "$REMOTE_URL" "$BRANCH" >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  git init -q "$tmp"
  git -C "$tmp" fetch -q --depth=1 "$REMOTE_URL" "$BRANCH"
  git -C "$tmp" archive FETCH_HEAD | tar -x -C "$DIR"
  rm -rf "$tmp"
  echo "restored state from branch '$BRANCH' ($(ls "$DIR" | wc -l) files)"
else
  echo "no '$BRANCH' branch yet: starting fresh"
fi
