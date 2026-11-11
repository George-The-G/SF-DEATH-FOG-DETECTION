#!/usr/bin/env bash
# Save ./state (or $1) to the `data` branch as a single commit, force-pushed, so the repo never accumulates history.
set -euo pipefail
: "${REMOTE_URL:?REMOTE_URL is required}"
DIR="${1:-state}"
BRANCH="${DATA_BRANCH:-data}"
cd "$DIR"
rm -rf .git
git init -q -b "$BRANCH"
git config user.name "deathfog-bot"
git config user.email "deathfog-bot@users.noreply.github.com"
git add -A
git commit -q -m "state $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push -q -f "$REMOTE_URL" "$BRANCH"
echo "saved state to branch '$BRANCH'"
