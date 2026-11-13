#!/usr/bin/env bash
# Live fog data step of the collect workflow (copied from the v3-dev branch like site/v3, so changes here need no PR to main).
#   bash live_ci/refresh.sh build   fetch the last published live output, build a fresh run, publish it only if the build succeeded
#   bash live_ci/refresh.sh reuse   only carry the last published live output into this deploy (v2 runs every 10 min, live builds every 30)
# After a successful build the run archive file (microclimate/live/archive.py; at most one per UTC hour, C.ARCHIVE_EVERY_H) is added to the `live-archive` branch with a normal commit
# (REMOTE_URL and contents: write come from the workflow job). Off when DEATHFOG_ARCHIVE=false (env or repo variable) or config.ARCHIVE = False.
# Never fails the workflow: the v2 page must always deploy. Run from the repository root.
set -uo pipefail
MODE="${1:-reuse}"
OUT=site/v3/live
NEW=live_new
STATIC=live_static
ARCH=archive_new
RC=1
# Beta preview channel (config.BETA; opened with /v3/?beta=1): a second build of the same fetched inputs into its own folder, with its own state files, status.json and cloud
# score. It runs after everything of the public build is done, never writes the run archive, and any failure of it is logged and ignored.
OUTB=site/v3/live_beta
NEWB=live_beta_new
INPUTS=live_inputs/inputs.pkl
T_ALL=$(date +%s)
REPO="${GITHUB_REPOSITORY:-George-The-G/SF-DEATH-FOG-DETECTION}"
PAGES="https://${REPO%%/*}.github.io/${REPO#*/}/v3/live"
PAGESB="${PAGES}_beta"
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

# Beta build of the same job (build mode, after the public build, archive and publish): shares the public build's fetched inputs ($INPUTS), builds with config.BETA into $NEWB
# and publishes into $OUTB only when it succeeded (finalize.py, like the public one). Returns 0 always.
beta_build() {
  [ "$RC" -eq 0 ] || { echo "public build failed: beta build skipped (the carried beta output stays)"; return 0; }
  if [ -s "$INPUTS" ]; then echo "beta build reuses the public build's fetched inputs ($(wc -c < "$INPUTS") bytes)"; else echo "no shared inputs file: the beta build fetches for itself"; fi
  rm -rf "$NEWB"
  mkdir -p "$NEWB"
  cp "$OUTB"/state_*.npz "$NEWB"/ 2>/dev/null && echo "carried beta state files: $(ls "$NEWB" | grep -c '^state_')" || echo "no carried beta state files: cold start"
  cp "$OUTB"/dsr_cache.json "$NEWB"/ 2>/dev/null && echo "carried the GOES sunlight cache" || echo "no GOES sunlight cache carried"
  local S0 RCB
  S0=$(date +%s)
  if [ -x /usr/bin/time ]; then
    DEATHFOG_CLOUDSCORE_PREV="$OUTB/cloud_score.json" timeout 300 /usr/bin/time -v python3 -m microclimate.live build --beta --inputs "$INPUTS" --out "$NEWB" --static "$STATIC/live_static.npz" 2> >(tee live_time_beta.log >&2)
  else
    DEATHFOG_CLOUDSCORE_PREV="$OUTB/cloud_score.json" timeout 300 python3 -m microclimate.live build --beta --inputs "$INPUTS" --out "$NEWB" --static "$STATIC/live_static.npz"
  fi
  RCB=$?
  echo "beta build exit code $RCB after $(( $(date +%s) - S0 )) s"
  grep -E "Maximum resident|Elapsed" live_time_beta.log 2>/dev/null || true
  python3 live_ci/finalize.py "$NEWB" "$OUTB" "$RCB" ""
  return 0
}

echo "::group::previous live output from $PAGES"
python3 live_ci/fetch_previous.py "$PAGES" "$OUT" || echo "no previous live output could be fetched; keeping what is in $OUT"
echo "::endgroup::"

# >>> cloud score carry (guide 04): keep cloud_score.json and the sat*.png tiles of the last published output, in both modes; the build merges into the file named below
python3 live_ci/fetch_extras.py "$PAGES" "$OUT" || echo "no cloud score files carried"
export DEATHFOG_CLOUDSCORE_PREV="$OUT/cloud_score.json"
# <<< cloud score carry

# >>> beta channel carry (both modes): the last published live_beta output with its on-demand files, state files, GOES sunlight cache and cloud score. Own folder; never touches $OUT.
echo "::group::previous beta output from $PAGESB"
python3 live_ci/fetch_previous.py "$PAGESB" "$OUTB" --extras || echo "no previous beta output could be fetched"
[ -d "$OUTB" ] && { python3 live_ci/fetch_extras.py "$PAGESB" "$OUTB" || echo "no beta cloud score files carried"; }
echo "::endgroup::"
# <<< beta channel carry

[ "$MODE" = build ] || { echo "reuse mode: done"; exit 0; }

echo "::group::dependencies"
python3 -m pip install -q -r live_ci/requirements-live.txt || { echo "pip install failed"; python3 live_ci/finalize.py "$NEW" "$OUT" 90 "dependency install failed"; exit 0; }
echo "::endgroup::"

echo "::group::static tables (release live-static-v1)"
mkdir -p "$STATIC"
for f in live_static.npz live_ribbons.npz ribbons_city.bin ribbons_city_meta.json live_extra.npz station_wind.npz live_east.npz; do   # live_extra.npz (guide 06): fetched when missing, also on a cache hit; a failed download only means flat asphalt streets. station_wind.npz (guide 03): optional, the wind anchors stay off without it. live_east.npz (guide 14): optional, only the beta build with wind_east reads it (the old live_static.npz stays valid; the switch is simply not applied without it)
  [ -s "$STATIC/$f" ] || gh release download live-static-v1 -p "$f" -D "$STATIC" --repo "$REPO" --clobber || echo "download of $f failed (live_extra.npz: flat asphalt streets; station_wind.npz: the wind anchors stay off; live_east.npz: east wind not applied)"
done
ls -l "$STATIC"
echo "::endgroup::"
if [ ! -s "$STATIC/live_static.npz" ]; then
  python3 live_ci/finalize.py "$NEW" "$OUT" 91 "static table download failed"; exit 0
fi

echo "::group::build"
rm -rf "$NEW" "$ARCH" "$(dirname "$INPUTS")"
mkdir -p "$NEW"
cp "$OUT"/state_*.npz "$NEW"/ 2>/dev/null && echo "carried state files: $(ls "$NEW" | grep -c '^state_')" || echo "no carried state files: cold start"
START=$(date +%s)
if [ -x /usr/bin/time ]; then
  timeout 480 /usr/bin/time -v python3 -m microclimate.live build --out "$NEW" --static "$STATIC/live_static.npz" --archive "$ARCH" --save-inputs "$INPUTS" 2> >(tee live_time.log >&2)
else
  timeout 480 python3 -m microclimate.live build --out "$NEW" --static "$STATIC/live_static.npz" --archive "$ARCH" --save-inputs "$INPUTS"
fi
RC=$?
echo "build exit code $RC after $(( $(date +%s) - START )) s"
grep -E "Maximum resident|Elapsed" live_time.log 2>/dev/null || true
echo "::endgroup::"
python3 live_ci/finalize.py "$NEW" "$OUT" "$RC" ""
echo "::group::run archive"
archive_push || echo "archive step failed (ignored)"
echo "::endgroup::"
echo "::group::beta build"
beta_build || echo "beta step failed (ignored)"
echo "::endgroup::"
echo "live step total: $(( $(date +%s) - T_ALL )) s"
exit 0
