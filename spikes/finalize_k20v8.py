"""2026-11-16, George: keep the ORIGINAL fog_* files (before guides 1-12), swap the library to the k20v8 tracer set, delete the striped intermediates.
1. move the 126 original fog_* files from library_backup_prewalls_tracer to library_original_fog (sizes verified);
2. copy the 99 k20v8 fog3d_* pairs into library\ and rewrite the 9 west fog_<d>_<B> as sums of them (east fog_* stay);
3. print what would be deleted (delete is done by the caller after the check)."""
import json
import shutil
from pathlib import Path

import numpy as np

D = Path.home() / "deathfog-data"
BAK, ORIG, LIB, NEW = D / "library_backup_prewalls_tracer", D / "library_original_fog", D / "library", D / "scratch" / "library_walls_tracer_k20v8"
ORIG.mkdir(exist_ok=True)
orig = sorted(p for p in BAK.iterdir() if p.name.startswith("fog_"))
for p in orig:
    t = ORIG / p.name
    if not t.exists():
        shutil.copy2(p, t)
    assert t.stat().st_size == p.stat().st_size, p.name
print(f"originals copied and verified: {len(orig)} files, {sum(p.stat().st_size for p in orig) / 1e9:.2f} GB -> {ORIG}")
OLD_TO_NEW = {0: (0, 1, 2), 1: (3, 4, 5), 2: (6, 7, 8), 3: (9, 10)}
OLD_BANDS = [[0, 150], [150, 300], [300, 500], [500, 800]]
for p in sorted(NEW.glob("fog3d_*")):
    if not p.name.endswith(".tmp.npz"):
        shutil.copy2(p, LIB / p.name)
for d in (180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360):
    s = f"{int(round(d * 10)):04d}"
    for B, parts in OLD_TO_NEW.items():
        acc = sum(np.load(LIB / f"fog3d_{s}_{i:02d}.npz")["c"].astype(np.float32) for i in parts)
        out = np.clip(acc, 0, 1).astype(np.float16)
        tmp = LIB / f"fog_{s}_{B}.tmp.npz"
        np.savez_compressed(tmp, c=out)
        tmp.replace(LIB / f"fog_{s}_{B}.npz")
        dg = [json.load(open(LIB / f"fog3d_{s}_{i:02d}.json")) for i in parts]
        json.dump(dict(direction_deg=d, band_m=OLD_BANDS[B], derived="sum of the 11-band fog3d fields " + str(list(parts)) + " (walls wind, kd 20 sideways / 8 vertical, 2026-11-16)", nan=False, max=float(out.max()),
                       drift_last_2000_steps=max(x["drift_last_2000_steps"] for x in dg), mean_fluid_below_100m=float(sum(x["mean_fluid_below_100m"] for x in dg))), open(LIB / f"fog_{s}_{B}.json", "w"))
print("library swapped to the k20v8 set")
# verification: the library fog3d files equal the k20v8 ones; an original 4-band file is unchanged in the original folder
a = np.load(LIB / "fog3d_2700_03.npz")["c"]
b = np.load(NEW / "fog3d_2700_03.npz")["c"]
assert np.array_equal(a, b)
print("verified: library fog3d_2700_03 == k20v8; original fog_2700_0 in", ORIG, "exists:", (ORIG / "fog_2700_0.npz").exists())
