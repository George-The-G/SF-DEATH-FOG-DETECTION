#!/usr/bin/env bash
# Live fog data step of the collect workflow (copied from the v3-dev branch like site/v3, so changes here need no PR to main).
#   bash live_ci/refresh.sh build   fetch the last published live output, build a fresh run, publish it only if the build succeeded
#   bash live_ci/refresh.sh reuse   only carry the last published live output into this deploy (v2 runs every 10 min, live builds every 30)
# After a successful build the run archive file (microclimate/live/archive.py) is added to the `live-archive` branch with a normal commit
# (REMOTE_URL and contents: write come from the workflow job). Off when DEATHFOG_ARCHIVE=false (env or repo variable) or config.ARCHIVE = False.
# Never fails the workflow: the v2 page must always deploy. Run from the repository root.
set -uo pipefail
MODE="${1:-reuse}"
OUT=site/v3/live
NEW=live_new
STATIC=live_static
ARCH=archive_new
RC=1
REPO="${GITHUB_REPOSITORY:-George-The-G/SF-DEATH-FOG-DETECTION}"
PAGES="https://${REPO%%/*}.github.io/${REPO#*/}/v3/live"
mkdir -p "$OUT"

archive_enabled() {
  local v="${DEATHFOG_ARCHIVE:-}"
  if [ -z "$v" ] && [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
    v=$(timeout 20 gh variable get DEATHFOG_ARCHIVE --repo "$REPO" 2>/dev/null || true)   # works only if the token may read variables
  fi
  if [ -z "$v" ]; then
    v=$(python3 -c "from microclimate.live import config as C; print('true' if C.ARCHIVE else 'false')" 2>/dev/null || echo true)
  fi
  [ "$(printf '%s' "$v" | tr 'A-Z' 'a-z')" != false ]
}

# Add the run file to branch live-archive (yyyy/mm/dd/), normal commit and normal push, weekly clean-up. Every failure is logged and ignored.
archive_push() {
  [ "$RC" -eq 0 ] || return 0
  ls "$ARCH"/run_*.npz >/dev/null 2>&1 || { echo "no archive file from this build"; return 0; }
  archive_enabled || { echo "archive is switched off"; return 0; }
  [ -n "${REMOTE_URL:-}" ] || { echo "REMOTE_URL is not set: archive not pushed"; return 0; }
  local W
  W=$(mktemp -d) || return 0
  (
    git init -q "$W" || exit 1
    git -C "$W" config user.name "deathfog-bot"
    git -C "$W" config user.email "deathfog-bot@users.noreply.github.com"
    if timeout 60 git ls-remote --exit-code --heads "$REMOTE_URL" live-archive >/dev/null 2>&1; then
      timeout 120 git -C "$W" fetch -q --depth=1 "$REMOTE_URL" live-archive || { echo "fetch of live-archive failed"; exit 1; }
      git -C "$W" checkout -q -B live-archive FETCH_HEAD || { echo "checkout of live-archive failed"; exit 1; }
    else
      git -C "$W" checkout -q --orphan live-archive || { echo "could not start live-archive"; exit 1; }
    fi
    python3 -m microclimate.live.archive place "$W" "$ARCH"/run_*.npz || { echo "could not place the run file"; exit 1; }
    python3 -m microclimate.live.archive prune "$W" --days 120 || echo "weekly clean-up failed (ignored)"
    git -C "$W" add -A || exit 1
    if git -C "$W" diff --cached --quiet; then
      echo "nothing new to archive"
    else
      git -C "$W" commit -q -m "run archive $(date -u +%Y-%m-%dT%H:%MZ)" || { echo "commit failed"; exit 1; }
      timeout 120 git -C "$W" push -q "$REMOTE_URL" live-archive || { echo "push to live-archive failed"; exit 1; }
      echo "archived $(ls "$ARCH" | tr '\n' ' ') on branch live-archive"
    fi
  ) 2>&1 | sed -E 's#(x-access-token:)[^@]*@#\1***@#g' || true
  rm -rf "$W"
  return 0
}

echo "::group::previous live output from $PAGES"
python3 live_ci/fetch_previous.py "$PAGES" "$OUT" || echo "no previous live output could be fetched; keeping what is in $OUT"
echo "::endgroup::"

# >>> cloud score carry (guide 04): keep cloud_score.json and the sat*.png tiles of the last published output, in both modes; the build merges into the file named below
python3 live_ci/fetch_extras.py "$PAGES" "$OUT" || echo "no cloud score files carried"
export DEATHFOG_CLOUDSCORE_PREV="$OUT/cloud_score.json"
# <<< cloud score carry

[ "$MODE" = build ] || { echo "reuse mode: done"; exit 0; }

echo "::group::dependencies"
python3 -m pip install -q -r live_ci/requirements-live.txt || { echo "pip install failed"; python3 live_ci/finalize.py "$NEW" "$OUT" 90 "dependency install failed"; exit 0; }
echo "::endgroup::"

echo "::group::static tables (release live-static-v1)"
mkdir -p "$STATIC"
for f in live_static.npz live_ribbons.npz ribbons_city.bin ribbons_city_meta.json station_wind.npz; do
  [ -s "$STATIC/$f" ] || gh release download live-static-v1 -p "$f" -D "$STATIC" --repo "$REPO" --clobber || echo "download of $f failed (station_wind.npz is optional: the wind anchors stay off without it)"
done
ls -l "$STATIC"
echo "::endgroup::"
if [ ! -s "$STATIC/live_static.npz" ]; then
  python3 live_ci/finalize.py "$NEW" "$OUT" 91 "static table download failed"; exit 0
fi

echo "::group::build"
rm -rf "$NEW" "$ARCH"
mkdir -p "$NEW"
cp "$OUT"/state_*.npz "$NEW"/ 2>/dev/null && echo "carried state files: $(ls "$NEW" | grep -c '^state_')" || echo "no carried state files: cold start"
START=$(date +%s)
if [ -x /usr/bin/time ]; then
  timeout 480 /usr/bin/time -v python3 -m microclimate.live build --out "$NEW" --static "$STATIC/live_static.npz" --archive "$ARCH" 2> >(tee live_time.log >&2)
else
  timeout 480 python3 -m microclimate.live build --out "$NEW" --static "$STATIC/live_static.npz" --archive "$ARCH"
fi
RC=$?
echo "build exit code $RC after $(( $(date +%s) - START )) s"
grep -E "Maximum resident|Elapsed" live_time.log 2>/dev/null || true
echo "::endgroup::"
python3 live_ci/finalize.py "$NEW" "$OUT" "$RC" ""
echo "::group::run archive"
archive_push || echo "archive step failed (ignored)"
echo "::endgroup::"
exit 0
