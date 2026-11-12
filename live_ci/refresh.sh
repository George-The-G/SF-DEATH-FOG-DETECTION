#!/usr/bin/env bash
# Live fog data step of the collect workflow (copied from the v3-dev branch like site/v3, so changes here need no PR to main).
#   bash live_ci/refresh.sh build   fetch the last published live output, build a fresh run, publish it only if the build succeeded
#   bash live_ci/refresh.sh reuse   only carry the last published live output into this deploy (v2 runs every 10 min, live builds every 30)
# Never fails the workflow: the v2 page must always deploy. Run from the repository root.
set -uo pipefail
MODE="${1:-reuse}"
OUT=site/v3/live
NEW=live_new
STATIC=live_static
REPO="${GITHUB_REPOSITORY:-George-The-G/SF-DEATH-FOG-DETECTION}"
PAGES="https://${REPO%%/*}.github.io/${REPO#*/}/v3/live"
mkdir -p "$OUT"

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
for f in live_static.npz live_ribbons.npz ribbons_city.bin ribbons_city_meta.json; do
  [ -s "$STATIC/$f" ] || gh release download live-static-v1 -p "$f" -D "$STATIC" --repo "$REPO" --clobber || echo "download of $f failed"
done
ls -l "$STATIC"
echo "::endgroup::"
if [ ! -s "$STATIC/live_static.npz" ]; then
  python3 live_ci/finalize.py "$NEW" "$OUT" 91 "static table download failed"; exit 0
fi

echo "::group::build"
rm -rf "$NEW"
START=$(date +%s)
if [ -x /usr/bin/time ]; then
  timeout 480 /usr/bin/time -v python3 -m microclimate.live build --out "$NEW" --static "$STATIC/live_static.npz" 2> >(tee live_time.log >&2)
else
  timeout 480 python3 -m microclimate.live build --out "$NEW" --static "$STATIC/live_static.npz"
fi
RC=$?
echo "build exit code $RC after $(( $(date +%s) - START )) s"
grep -E "Maximum resident|Elapsed" live_time.log 2>/dev/null || true
echo "::endgroup::"
python3 live_ci/finalize.py "$NEW" "$OUT" "$RC" ""
exit 0
