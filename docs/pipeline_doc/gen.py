"""Figures for the fog pipeline documentation page (read-only on all project data; writes only into this folder).
Run on the PC: python gen.py [step ...]   steps: dl geom wind refine tracer asset goes tiles fog3d macro travel wet map
Outputs: out/*.webp (figures) and out/facts.json (numbers quoted in the page)."""
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "mc"))
D = Path.home() / "deathfog-data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
DL = HERE / "beta"
DL.mkdir(exist_ok=True)
BETA = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live_beta/"
PUB = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live/"
X0, Y0, NX, NY, NZ, DX = 540100.0, 4171500.0, 855, 811, 40, 20.0
FRAME3D = 12   # forecast hour with ground fog in beta (00:00 PT 18 Nov)
FRAME = 7   # beta frame shown in the live stages (index.json: frame 0 = 12:00 PT 17 Nov 2026, so 19:00 PT = 03:00 UTC 18 Nov)
FACTS_P = OUT / "facts.json"
FACTS = json.loads(FACTS_P.read_text()) if FACTS_P.exists() else {}
plt.rcParams.update({"font.size": 8.5, "axes.titlesize": 9.5, "figure.dpi": 110, "savefig.dpi": 110, "axes.titleweight": "bold"})
BOXC = "#ff2d55"   # crop box colour


def fact(k, v):
    FACTS[k] = v
    FACTS_P.write_text(json.dumps(FACTS, indent=1, default=float))


def save(fig, name, q=80):
    p = OUT / (name + ".png")
    fig.savefig(p, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    im = Image.open(p).convert("RGB")
    im.save(OUT / (name + ".webp"), quality=q, method=6)
    p.unlink()
    print("wrote", name, im.size, (OUT / (name + ".webp")).stat().st_size, flush=True)


def ll2utm(lon, lat):
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 32610, always_xy=True)
    return t.transform(lon, lat)


def km_extent():
    return [0, NX * DX / 1000, 0, NY * DX / 1000]


def to_km(x, y):
    return (np.asarray(x) - X0) / 1000.0, (np.asarray(y) - Y0) / 1000.0


POT_X, POT_Y = ll2utm(-122.4037, 37.7597)          # 20th & Kansas (zone start)
PI, PJ = int((POT_X - X0) // DX), int((POT_Y - Y0) // DX)
ZOOM = (PI - 60, PJ - 50, 120, 100)                 # i0, j0, ni, nj: 2.4 x 2.0 km around Potrero


def zoom_rect(ax, z=ZOOM, lw=1.6, label=None):
    i0, j0, ni, nj = z
    ax.add_patch(Rectangle((i0 * DX / 1000, j0 * DX / 1000), ni * DX / 1000, nj * DX / 1000, fill=False, ec=BOXC, lw=lw))
    if label:
        ax.text(i0 * DX / 1000, (j0 + nj) * DX / 1000 + 0.15, label, color=BOXC, fontsize=8, fontweight="bold")


def zoom_ext(z=ZOOM):
    i0, j0, ni, nj = z
    return [i0 * DX / 1000, (i0 + ni) * DX / 1000, j0 * DX / 1000, (j0 + nj) * DX / 1000]


def section_line(ax, j=PJ, c="#ffd60a"):
    ax.axhline(j * DX / 1000, color=c, lw=1.0, ls="--")


def get(url, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "sf-death-fog docs (github.com/George-The-G/SF-DEATH-FOG-DETECTION)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        path.write_bytes(r.read())
    return path


# ------------------------------------------------------------------ download a snapshot of the beta channel
def step_dl():
    idx = json.loads(get(BETA + "index.json", DL / "index.json").read_text())
    for f in ("status.json", "weather.json", "stations.json", "cloud_score.json", "fog0.png", "sat0.png", "day0.u8", "day1.u8", "day2.u8", "goes5/grid.json",
              "macro/index.json", "fog5/index.json"):
        try:
            get(BETA + f, DL / f)
        except Exception as e:  # noqa: BLE001
            print("fail", f, e)
    for fr_ in (FRAME, FRAME3D):
        f3 = idx["fog3d"]["files"][fr_]
        if f3:
            get(BETA + f3, DL / f3)
    get(PUB + "fog0.png", DL / "public_fog0.png")
    get(PUB + "index.json", DL / "public_index.json")
    for h in ("2026-11-18/02.npz", "2026-11-18/03.npz"):
        get(BETA + "goes5/" + h, DL / "goes5" / h)
    mi = json.loads((DL / "macro/index.json").read_text())
    print("macro index keys", list(mi)[:10])
    fr = mi.get("frames") or []
    want = [x for x in fr if str(x.get("t", "")).startswith("2026-11-18T03:0")] or fr[-1:]
    for x in want[:1]:
        get(BETA + "macro/" + x["file"], DL / "macro" / x["file"])
        fact("macro_frame", x)
    fi = json.loads((DL / "fog5/index.json").read_text())
    fr5 = fi.get("frames", [])
    pick = [x for x in fr5 if str(x.get("t", "")).startswith("2026-11-18T03:")]
    for x in pick[:12]:
        for k in ("tile", "vol"):
            if x.get(k) and not (DL / "fog5" / x[k]).exists():
                get(BETA + "fog5/" + x[k], DL / "fog5" / x[k])
    fact("fog5_pick", pick[:12])


# ------------------------------------------------------------------ geometry
def load_geom():
    ter = np.load(D / "grids/terrain_20m.npy").astype(np.float32)
    bh = np.load(D / "grids/bldg_mean_h_20m.npy").astype(np.float32)
    solid = np.load(D / "grids/solid_20m.npz")["solid"]
    return ter, bh, solid


def surface_k(solid):
    s = solid.copy()
    s[:, :, 0] = True
    top = NZ - 1 - np.argmax(s[:, :, ::-1], axis=2)
    return top


def hillshade(ter):
    gy, gx = np.gradient(ter, DX)
    sl = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    asp = np.arctan2(-gx, gy)
    az, alt = np.radians(315), np.radians(45)
    return np.clip(np.sin(alt) * np.sin(sl) + np.cos(alt) * np.cos(sl) * np.cos(az - asp), 0, 1)


def step_geom():
    ter, bh, solid = load_geom()
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6), gridspec_kw=dict(width_ratios=[1, 1, 0.95]))
    ext = km_extent()
    im = axs[0].imshow(np.where(ter > 0, ter, np.nan).T, origin="lower", extent=ext, cmap="terrain", vmin=-60, vmax=380)
    axs[0].set_facecolor("#cfe3f5")
    plt.colorbar(im, ax=axs[0], shrink=0.75, label="m above sea level")
    axs[0].set_title("Input: USGS 3DEP terrain on the 20 m grid")
    im = axs[1].imshow(np.where(bh > 0.1, bh, np.nan).T, origin="lower", extent=ext, cmap="magma_r", vmin=0, vmax=40)
    axs[1].imshow(hillshade(ter).T, origin="lower", extent=ext, cmap="gray", alpha=0.25)
    plt.colorbar(im, ax=axs[1], shrink=0.75, label="mean building height in the 20 m cell (m)")
    axs[1].set_title("Input: DataSF footprints, mean height per cell")
    for a in axs[:2]:
        zoom_rect(a, label="zoom (Potrero)")
        section_line(a)
        a.set_xlabel("km east of 540100 E"); a.set_ylabel("km north of 4171500 N")
    # zoom panel
    i0, j0, ni, nj = ZOOM
    axs[2].imshow(np.where(bh > 0.1, bh, np.nan)[i0:i0 + ni, j0:j0 + nj].T, origin="lower", extent=zoom_ext(), cmap="magma_r", vmin=0, vmax=40)
    axs[2].imshow(hillshade(ter)[i0:i0 + ni, j0:j0 + nj].T, origin="lower", extent=zoom_ext(), cmap="gray", alpha=0.3)
    axs[2].plot((POT_X - X0) / 1000, (POT_Y - Y0) / 1000, "o", mfc="none", mec="#0a84ff", ms=9, mew=2)
    axs[2].set_title("Zoom (red box): Potrero, 20 m cells")
    for s in axs[2].spines.values():
        s.set_edgecolor(BOXC); s.set_linewidth(2)
    save(fig, "geom_inputs")
    # vertical section of the solid mask through the Potrero row
    fig, ax = plt.subplots(figsize=(13, 2.6))
    sec = solid[:, PJ, :].T.astype(float)
    ax.imshow(sec, origin="lower", extent=[0, NX * DX / 1000, 0, NZ * DX], aspect="auto", cmap="Greys", vmin=0, vmax=1.4)
    ax.plot(np.arange(NX) * DX / 1000, ter[:, PJ], color="#8a5a00", lw=0.8, label="terrain")
    ax.set_ylim(0, 500); ax.set_xlabel("km east (yellow dashed line in the maps above)"); ax.set_ylabel("m above sea level")
    ax.set_title("Output: solid_20m.npz, west-east slice through Potrero (grey = solid cell: terrain + mean building height)")
    ax.legend(loc="upper right")
    save(fig, "geom_section")
    g = json.loads((D / "grids/geometry_summary.json").read_text())
    fact("geometry", g)
    fact("potrero_ij", [PI, PJ])


# ------------------------------------------------------------------ wind (20 m tier)
def wind_speed(path):
    u = np.load(path)["u"]
    return u


def near_surface(spd, top, dk=2):
    kk = np.clip(top + dk, 0, NZ - 1)
    return np.take_along_axis(spd, kk[:, :, None], axis=2)[:, :, 0]


def step_wind():
    ter, bh, solid = load_geom()
    top = surface_k(solid)
    hs = hillshade(ter)
    u = wind_speed(D / "library/wind_2700.npz").astype(np.float32)
    spd = np.sqrt((u ** 2).sum(axis=0))
    spd[solid] = np.nan
    ns = near_surface(spd, top)
    fig, axs = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw=dict(width_ratios=[1.15, 1]))
    ext = km_extent()
    axs[0].imshow(hs.T, origin="lower", extent=ext, cmap="gray")
    im = axs[0].imshow(ns.T, origin="lower", extent=ext, cmap="viridis", vmin=0, vmax=1.1, alpha=0.85)
    plt.colorbar(im, ax=axs[0], shrink=0.8, label="speed / inflow speed")
    axs[0].set_title("Output: wind_2700.npz (wind from 270), 2 cells above the local surface")
    zoom_rect(axs[0], label="zoom"); section_line(axs[0])
    i0, j0, ni, nj = ZOOM
    axs[1].imshow(hs[i0:i0 + ni, j0:j0 + nj].T, origin="lower", extent=zoom_ext(), cmap="gray")
    axs[1].imshow(ns[i0:i0 + ni, j0:j0 + nj].T, origin="lower", extent=zoom_ext(), cmap="viridis", vmin=0, vmax=1.1, alpha=0.8)
    st = 5
    ii, jj = np.meshgrid(np.arange(i0, i0 + ni, st), np.arange(j0, j0 + nj, st), indexing="ij")
    kk = np.clip(top[ii, jj] + 2, 0, NZ - 1)
    U, V = u[0][ii, jj, kk], u[1][ii, jj, kk]
    axs[1].quiver((ii + 0.5) * DX / 1000, (jj + 0.5) * DX / 1000, U, V, color="white", scale=14, width=0.004)
    axs[1].set_title("Zoom: flow vectors every 100 m (same layer)")
    for s in axs[1].spines.values():
        s.set_edgecolor(BOXC); s.set_linewidth(2)
    save(fig, "wind20_map")
    fig, ax = plt.subplots(figsize=(13, 2.8))
    sec = spd[:, PJ, :].T
    im = ax.imshow(sec, origin="lower", extent=[0, NX * DX / 1000, 0, NZ * DX], aspect="auto", cmap="viridis", vmin=0, vmax=1.2)
    ax.imshow(np.where(solid[:, PJ, :].T, 1.0, np.nan), origin="lower", extent=[0, NX * DX / 1000, 0, NZ * DX], aspect="auto", cmap="Greys", vmin=0, vmax=1.5)
    plt.colorbar(im, ax=ax, label="speed / inflow", pad=0.01)
    ax.set_xlabel("km east"); ax.set_ylabel("m above sea level")
    ax.set_title("Same field, west-east slice through Potrero: the city grows its own slow layer under a uniform inflow")
    save(fig, "wind20_section")
    nsv = ns[np.isfinite(ns)]
    fact("wind2700", dict(json=json.loads((D / "library/wind_2700.json").read_text()), near_surface_p5_p50_p95=[float(np.percentile(nsv, q)) for q in (5, 50, 95)]))
    # old (no walls) field at the same layer
    try:
        uo = np.load(D / "library_backup_prewalls_2026-11-16/wind_2700.npz")["u"].astype(np.float32)
        so = np.sqrt((uo ** 2).sum(axis=0))
        so[solid] = np.nan
        nso = near_surface(so, top)
        fig, axs = plt.subplots(1, 2, figsize=(12, 4.6))
        for a, f, t in ((axs[0], nso, "Before 16 Nov (stale GPU kernel, no walls): public build"), (axs[1], ns, "After the walls fix: beta build")):
            a.imshow(hs.T, origin="lower", extent=ext, cmap="gray")
            im = a.imshow(f.T, origin="lower", extent=ext, cmap="viridis", vmin=0, vmax=1.1, alpha=0.85)
            a.set_title(t)
        plt.colorbar(im, ax=axs, shrink=0.8, label="speed / inflow, 2 cells above surface")
        save(fig, "wind20_old_new")
        v = nso[np.isfinite(nso)]
        fact("wind2700_old_near_surface_p5_p50_p95", [float(np.percentile(v, q)) for q in (5, 50, 95)])
        del uo, so
    except Exception as e:  # noqa: BLE001
        print("old field", e)
    del u, spd
    # small multiples
    dirs = [180, 225, 270, 315, 360, 90]
    fig, axs = plt.subplots(2, 3, figsize=(13, 8.4))
    for a, d in zip(axs.flat, dirs):
        uu = np.load(D / f"library/wind_{int(round(d * 10)):04d}.npz")["u"].astype(np.float32)
        ss = np.sqrt((uu ** 2).sum(axis=0)); ss[solid] = np.nan
        f = near_surface(ss, top)
        a.imshow(hs.T, origin="lower", extent=ext, cmap="gray")
        im = a.imshow(f.T, origin="lower", extent=ext, cmap="viridis", vmin=0, vmax=1.1, alpha=0.85)
        th = np.radians(d)
        a.annotate("", xy=(8.5 - 3 * np.sin(th) * 0.5, 8 - 3 * np.cos(th) * 0.5), xytext=(8.5 + 3 * np.sin(th) * 0.5, 8 + 3 * np.cos(th) * 0.5),
                   arrowprops=dict(arrowstyle="-|>", color="white", lw=2.5))
        a.set_title(f"wind from {d:g} deg" + ("  (east: street wind only, no fog)" if d < 180 else ""))
        a.set_xticks([]); a.set_yticks([])
        del uu, ss
    plt.colorbar(im, ax=axs, shrink=0.6, label="speed / inflow, 2 cells above surface")
    save(fig, "wind20_dirs")


# ------------------------------------------------------------------ refined 5 m / 2.5 m patches
def step_refine():
    ter, bh, solid = load_geom()
    hs = hillshade(ter)
    z = np.load(D / "refined/patch_corr_2700.npz")
    cb = z["corr_bldg"]
    js = json.loads((D / "refined/patch_corr_2700.json").read_text())
    pats = js.get("patches", [])
    fig, axs = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw=dict(width_ratios=[1.15, 1]))
    ext = km_extent()
    axs[0].imshow(hs.T, origin="lower", extent=ext, cmap="gray")
    im = axs[0].imshow(cb.T, origin="lower", extent=ext, cmap="RdYlBu", vmin=0, vmax=1.2)
    for p in pats:
        axs[0].add_patch(Rectangle((p["ix0"] * DX / 1000, p["iy0"] * DX / 1000), p["ncx"] * DX / 1000, p["ncy"] * DX / 1000, fill=False, ec="k", lw=0.25))
    plt.colorbar(im, ax=axs[0], shrink=0.8, label="corr_bldg = street speed with buildings / without")
    axs[0].set_title(f"Output: patch_corr_2700.npz, {len(pats)} nested 5 m patches (black outlines)")
    zoom_rect(axs[0], label="zoom")
    z25 = np.load(D / "refined/patch_corr_2700_dx2p5.npz")["corr_bldg"]
    i0, j0, ni, nj = ZOOM
    axs[1].imshow(hs[i0:i0 + ni, j0:j0 + nj].T, origin="lower", extent=zoom_ext(), cmap="gray")
    axs[1].imshow(z25[i0:i0 + ni, j0:j0 + nj].T, origin="lower", extent=zoom_ext(), cmap="RdYlBu", vmin=0, vmax=1.2)
    axs[1].set_title("Zoom: the 2.5 m Potrero run (patch_corr_2700_dx2p5.npz)")
    for s in axs[1].spines.values():
        s.set_edgecolor(BOXC); s.set_linewidth(2)
    save(fig, "refine_map")
    v = cb[np.isfinite(cb)]
    fact("refine", dict(patches=len(pats), fine_dx_m=js.get("fine_dx_m"), cells=int(v.size), corr_bldg_p5_p50_p95=[float(np.percentile(v, q)) for q in (5, 50, 95)],
                        totals=js.get("totals")))
    # existing pictures (copied, resized)
    for src, name, w in ((D / "spikes/street5m_v2/compare_speed_maps_walls.png", "street5m_v2", 1300), (D / "scratch/marine_layer_demo.png", "marine_demo", 1000)):
        try:
            im = Image.open(src).convert("RGB")
            if im.width > w:
                im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
            im.save(OUT / (name + ".webp"), quality=80, method=6)
            print("copied", name, im.size)
        except Exception as e:  # noqa: BLE001
            print("copy fail", src, e)
    try:
        fact("street5m_v2", json.loads((D / "spikes/street5m_v2/compare_walls.json").read_text()))
    except Exception as e:  # noqa: BLE001
        print(e)


# ------------------------------------------------------------------ fog tracer (11 bands)
def step_tracer():
    ter, bh, solid = load_geom()
    top = surface_k(solid)
    hs = hillshade(ter)
    ext = km_extent()
    bands = [(0, "0-40 m"), (3, "140-200 m"), (9, "500-640 m")]
    fig, axs = plt.subplots(2, 3, figsize=(13, 7.4), gridspec_kw=dict(height_ratios=[1.6, 1]))
    stats = {}
    for c_, (b, lab) in enumerate(bands):
        c = np.load(D / f"library/fog3d_2700_{b:02d}.npz")["c"].astype(np.float32)
        c[solid] = np.nan
        ns = near_surface(c, top, dk=1)
        a = axs[0, c_]
        a.imshow(hs.T, origin="lower", extent=ext, cmap="gray")
        im = a.imshow(np.sqrt(np.clip(ns, 0, 1)).T, origin="lower", extent=ext, cmap="Blues", vmin=0, vmax=1, alpha=0.85)
        a.set_title(f"band {b:02d} (inflow {lab})")
        section_line(a); a.set_xticks([]); a.set_yticks([])
        a = axs[1, c_]
        a.imshow(np.sqrt(np.clip(c[:, PJ, :].T, 0, 1)), origin="lower", extent=[0, NX * DX / 1000, 0, NZ * DX], aspect="auto", cmap="Blues", vmin=0, vmax=1)
        a.imshow(np.where(solid[:, PJ, :].T, 1.0, np.nan), origin="lower", extent=[0, NX * DX / 1000, 0, NZ * DX], aspect="auto", cmap="Greys", vmin=0, vmax=1.5)
        a.set_ylim(0, 800); a.set_xlabel("km east"); a.set_ylabel("m ASL" if c_ == 0 else "")
        stats[lab] = dict(json=json.loads((D / f"library/fog3d_2700_{b:02d}.json").read_text()))
        del c
    fig.colorbar(im, ax=axs, shrink=0.5, label="tracer concentration (sqrt scale), 1 = inflow band")
    
    save(fig, "tracer_bands")
    fact("tracer2700", stats)


# ------------------------------------------------------------------ packed assets (live_tracer3d, live_static)
def step_asset():
    T = np.load(D / "scratch/release_walls_k20v8/live_tracer3d_walls.npz")
    S = np.load(D / "scratch/release_walls_k20v8/live_static_walls.npz")
    dirs = list(np.asarray(T["dirs"], float))
    r = dirs.index(270.0)
    tr = T["tr3d"]
    fig, axs = plt.subplots(1, 4, figsize=(14, 3.7))
    for a, k in zip(axs[:3], (0, 2, 6)):
        img = sum(tr[r, b, k].astype(np.float32) for b in (0, 1, 2)) / 255.0
        a.imshow(np.sqrt(np.clip(img, 0, 1)), cmap="Blues", vmin=0, vmax=1)
        a.imshow(np.where(T["fluid"][k] < 128, 1.0, np.nan), cmap="Greys", vmin=0, vmax=1.6)
        a.set_title(f"270, bands 0-2 (0-140 m), level {k} ({T['z_m'][k]:.1f} m)"); a.set_xticks([]); a.set_yticks([])
    axs[3].imshow(T["urban"], cmap="Oranges"); axs[3].set_title("urban share (0-255), for the urban lift"); axs[3].set_xticks([]); axs[3].set_yticks([])
    save(fig, "asset_tracer3d")
    tr80 = S["tr80"]; sd = list(np.asarray(S["dirs"], float)); r2 = sd.index(270.0)
    x, y, tz = S["x"], S["y"], S["tracer"][r2]
    fig, axs = plt.subplots(1, 2, figsize=(12, 5))
    axs[0].imshow(np.sqrt(np.clip(tr80[r2], 0, 1)), cmap="Blues", vmin=0, vmax=0.8, extent=[0, 214 * 0.08, 0, 203 * 0.08])
    axs[0].set_title("tr80[270]: 80 m tile tracer (what the 2D fog tile multiplies)")
    sc = axs[1].scatter((x - X0) / 1000, (y - Y0) / 1000, c=np.sqrt(np.clip(tz / 0.03, 0, 1)), s=0.05, cmap="Blues", vmin=0, vmax=1, rasterized=True)
    axs[1].set_aspect("equal"); axs[1].set_xlim(0, 17.1); axs[1].set_ylim(0, 16.2); axs[1].set_facecolor("#333")
    axs[1].set_title(f"tracer[270] at {len(x):,} street points (sqrt of tracer / TRACER_REF 0.03)")
    save(fig, "asset_static")
    fact("asset", dict(tr3d_shape=list(tr.shape), dirs=dirs, z_m=[float(v) for v in T["z_m"]], band_edges=[float(v) for v in T["band_edges_m"]], points=int(len(x)),
                       tracer_p50_p95_270=[float(np.percentile(tz, 50)), float(np.percentile(tz, 95))]))


# ------------------------------------------------------------------ GOES: CONUS context -> crop -> low-cloud fields
def step_goes():
    import h5py
    from microclimate.live import goes, goes5
    from microclimate.live import config as C
    cache = D / "live/goes_cache"
    acm = sorted(cache.glob("OR_ABI-L2-ACMC-*s20263160301*.nc"))[0]
    with h5py.File(acm, "r") as a:
        full = a["ACM"][:]
        nav = goes.Nav(a["goes_imager_projection"])
        x, y = goes._axis(a, "x"), goes._axis(a, "y")
    box = goes.BOX
    lons = np.array([box[0], box[2], box[2], box[0], box[0]]); lats = np.array([box[1], box[1], box[3], box[3], box[1]])
    bx, by = nav.xy(lons, lats)
    ci = np.interp(bx, x, np.arange(len(x))); ri = np.interp(by, y[::-1], np.arange(len(y))[::-1])
    f = 4
    small = full[::f, ::f].astype(float); small[small > 3] = np.nan
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.4), gridspec_kw=dict(width_ratios=[1.6, 1]))
    axs[0].imshow(small, cmap="Greys_r", vmin=0, vmax=3, extent=[0, full.shape[1], full.shape[0], 0])
    axs[0].plot(ci, ri, color=BOXC, lw=1.6)
    axs[0].set_title("Input: one GOES-18 ABI-L2-ACMC CONUS scan (12 Nov 03:01 UTC), clear-sky mask 0..3")
    axs[0].set_xticks([]); axs[0].set_yticks([])
    c0, c1, r0, r1 = int(ci.min()) - 3, int(ci.max()) + 4, int(ri.min()) - 3, int(ri.max()) + 4
    crop = full[r0:r1, c0:c1].astype(float); crop[crop > 3] = np.nan
    axs[1].imshow(crop, cmap="Greys_r", vmin=0, vmax=3)
    axs[1].set_title(f"The crop goes.BOX = {box} ({crop.shape[1]} x {crop.shape[0]} px)")
    axs[1].set_xticks([]); axs[1].set_yticks([])
    for s in axs[1].spines.values():
        s.set_edgecolor(BOXC); s.set_linewidth(2)
    save(fig, "goes_conus_crop")
    fact("goes_conus", dict(file=acm.name, shape=list(full.shape), crop_px=list(crop.shape)))
    # beta 5-minute store: the scan nearest 03:00 UTC 18 Nov
    store = DL / "goes5"
    cr = goes5.Crop5(store)
    import datetime as dt
    tw = dt.datetime(2026, 11, 18, 3, 0, tzinfo=dt.timezone.utc).timestamp()
    j = int(np.argmin(np.abs(cr.t_scan - tw)))
    import pandas as pd
    import pvlib
    el = float(pvlib.solarposition.get_solarposition(pd.DatetimeIndex([pd.Timestamp(int(cr.t_scan[j]), unit="s", tz="UTC")]), cr.centre[1], cr.centre[0])["elevation"].iloc[0])
    p, c, top, _ = goes.low_cloud(cr, j, sun_el=el)
    ts = dt.datetime.fromtimestamp(int(cr.t_scan[j]), dt.timezone.utc).isoformat()
    g = json.loads((store / "grid.json").read_text())
    # the city domain on the crop: convert its corners to pixels
    from pyproj import Transformer
    tr = Transformer.from_crs(32610, 4326, always_xy=True)
    dx_, dy_ = [X0, X0 + NX * DX, X0 + NX * DX, X0, X0], [Y0, Y0, Y0 + NY * DX, Y0 + NY * DX, Y0]
    lo, la = tr.transform(dx_, dy_)
    gx, gy = cr.nav.xy(np.array(lo), np.array(la))
    cx = np.interp(gx, cr.x, np.arange(p.shape[1]))
    acm_s = cr.acm[j].astype(float); acm_s[acm_s > 3] = np.nan
    ht = cr.ht[j].astype(float); ht[ht <= 0] = np.nan
    fig, axs = plt.subplots(1, 4, figsize=(15, 3.9))
    panels = ((acm_s, "acm: clear-sky mask class", "Greys_r", 0, 3), (ht, "ht: cloud-top height (m)", "turbo", 0, 3000), (p, "p: low-cloud probability", "Blues", 0, 1),
              (c, "c: confidence", "Greens", 0, 1))
    for a, (arr, t, cm, v0, v1) in zip(axs, panels):
        im = a.imshow(arr, cmap=cm, vmin=v0, vmax=v1)
        a.set_title(t); a.set_xticks([]); a.set_yticks([])
        plt.colorbar(im, ax=a, shrink=0.7)
        cy = np.interp(gy, cr.y[::-1], np.arange(p.shape[0])[::-1])
        a.plot(cx, cy, color=BOXC, lw=1.2)
    fig.suptitle(f"Beta 5-minute store, scan {ts}: raw crop (left two) and goes.low_cloud rule v1 (right two). Red = 20 m city domain", fontweight="bold")
    save(fig, "goes_lowcloud")
    fact("goes5_scan", dict(t=ts, shape=list(p.shape), grid=g, n_scans_loaded=int(len(cr.t_scan)), p_mean_city=None))


# ------------------------------------------------------------------ 2D fog tiles and satellite tiles
def tile_of(img, k, cols=8, w=214, h=203):
    r, c = divmod(k, cols)
    return img[r * h:(r + 1) * h, c * w:(c + 1) * w], (c * w, r * h)


def sat_rgb(arr):
    v = arr.astype(float)
    low = np.where(v <= 250, v / 250.0, 0)
    rgb = (1 - low)[..., None] * np.array([0.12, 0.14, 0.18]) + low[..., None] * np.array([0.95, 0.97, 1.0])
    rgb[v == 252] = (0.95, 0.6, 0.2); rgb[v == 255] = (0.5, 0.5, 0.5)
    return rgb


def step_tiles():
    fog = np.asarray(Image.open(DL / "fog0.png").convert("L"))
    sat = np.asarray(Image.open(DL / "sat0.png").convert("L"))
    pub = np.asarray(Image.open(DL / "public_fog0.png").convert("L"))
    fig = plt.figure(figsize=(13, 7.4))
    gs = fig.add_gridspec(2, 4, height_ratios=[1.3, 1])
    a = fig.add_subplot(gs[0, :])
    a.imshow(np.sqrt(fog / 255.0), cmap="Greys_r", vmin=0, vmax=0.6)
    for k, lab in ((FRAME, "7"), (FRAME3D, "12")):
        _, (ox, oy) = tile_of(fog, k)
        a.add_patch(Rectangle((ox, oy), 214, 203, fill=False, ec=BOXC, lw=2))
        a.text(ox + 6, oy + 22, lab, color=BOXC, fontsize=10, fontweight="bold")
    a.set_title("Output: beta fog0.png, the whole sprite sheet (8 tiles per row, 214 x 203 px each; tile k = k hours after 12:00 PT 17 Nov). Red = tiles 7 and 12")
    a.set_xticks([]); a.set_yticks([])
    panels = ((tile_of(fog, FRAME)[0], "beta, tile 7 (19:00 PT, observed)", "f"), (tile_of(pub, FRAME)[0], "public, tile 7 (2D formula)", "f"),
              (tile_of(sat, FRAME)[0], "GOES sat0.png, tile 7", "s"), (tile_of(fog, FRAME3D)[0], "beta, tile 12 (00:00 PT, forecast)", "f"))
    for i, (arr, ttl, kind) in enumerate(panels):
        ax = fig.add_subplot(gs[1, i])
        ax.imshow(np.sqrt(arr / 255.0), cmap="Blues", vmin=0, vmax=0.6) if kind == "f" else ax.imshow(sat_rgb(arr))
        ax.set_title(ttl, fontsize=8.5); ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_edgecolor(BOXC); sp.set_linewidth(1.5)
    save(fig, "fog_tiles")
    pi = json.loads((DL / "public_index.json").read_text())
    st = tile_of(sat, FRAME)[0].astype(float)
    fact("tiles", dict(beta_tile7_mean=float(tile_of(fog, FRAME)[0].mean()), public_tile7_mean=float(tile_of(pub, FRAME)[0].mean()), beta_tile12_mean=float(tile_of(fog, FRAME3D)[0].mean()),
                       public_label=pi.get("label"), sat7_low_share_mean=float(np.mean(np.where(st <= 250, st / 250, 0))), sat7_high=float(np.mean(st == 252)), sat7_nodata=float(np.mean(st == 255))))


# ------------------------------------------------------------------ 3D fog atlas
def step_fog3d():
    idx = json.loads((DL / "index.json").read_text())
    FRAME = FRAME3D
    fn = idx["fog3d"]["files"][FRAME]
    at = np.asarray(Image.open(DL / fn).convert("L")).astype(float)
    T = np.load(D / "scratch/release_walls_k20v8/live_tracer3d_walls.npz")
    ground = T["ground_m"]
    w, h = 214, 203
    vol = np.stack([at[(k // 6) * h:(k // 6 + 1) * h, (k % 6) * w:(k % 6 + 1) * w] for k in range(24)])
    fig = plt.figure(figsize=(13, 9.2))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1], width_ratios=[1.3, 1])
    a = fig.add_subplot(gs[0, :])
    a.imshow(np.sqrt(np.clip(at / 16.0, 0, 1)), cmap="Greys_r", vmin=0, vmax=1)
    for k in range(24):
        a.text((k % 6) * w + 4, (k // 6) * h + 16, f"{(k + 0.5) * 25:.0f} m", color="#ffd60a", fontsize=7)
    a.add_patch(Rectangle((0, 0), w, h, fill=False, ec=BOXC, lw=2))
    a.set_title(f"Output: beta {fn} (6 x 4 atlas, one tile per 25 m level, value 16 = full density in the viewer). Red = level 0 (12.5 m)")
    a.set_xticks([]); a.set_yticks([])
    b = fig.add_subplot(gs[1, 0])
    row = 203 - 1 - int((PJ * DX) // 80)
    sec = vol[:, row, :]
    b.imshow(np.sqrt(np.clip(sec / 16.0, 0, 1)), origin="lower", extent=[0, 214 * 0.08, 0, 600], aspect="auto", cmap="Blues", vmin=0, vmax=1)
    b.fill_between(np.arange(214) * 0.08, 0, ground[row], color="#6b5b45")
    tl = idx["fog3d"]["top_layer"][FRAME]
    wx = json.loads((DL / "weather.json").read_text())
    b.axhline(wx["fog_top_m"][FRAME], color="#ff9f0a", ls="--", lw=1, label=f"fused fog top {wx['fog_top_m'][FRAME]:.0f} m")
    bm = idx["fog3d"].get("base_m", [None] * 99)[FRAME]
    if bm:
        b.axhline(bm, color="#30d158", ls=":", lw=1.2, label=f"cloud base (fog_base) {bm:.0f} m")
    b.set_ylim(0, 600); b.legend(loc="upper right", fontsize=7); b.set_xlabel("km east"); b.set_ylabel("m above sea level")
    b.set_title("Rebuilt from the atlas: west-east slice through Potrero")
    c = fig.add_subplot(gs[1, 1])
    prof = vol.reshape(24, -1).mean(axis=1)
    c.barh((np.arange(24) + 0.5) * 25, prof / 16.0, height=20, color="#0a84ff")
    c.set_xlabel("mean density over the grid (1 = full)"); c.set_ylabel("m above sea level"); c.set_title("Mean of each level")
    save(fig, "fog3d_atlas")
    fact("fog3d", dict(file=fn, top_layer=tl, base_m=bm, fog_top_m=wx["fog_top_m"][FRAME], sigma=wx["fog_top_sigma_m"][FRAME], max_value=float(at.max())))


# ------------------------------------------------------------------ macro frame
def step_macro():
    mi = json.loads((DL / "macro/index.json").read_text())
    x = FACTS.get("macro_frame")
    im = np.asarray(Image.open(DL / "macro" / x["file"]).convert("RGBA")).astype(float)
    fig, axs = plt.subplots(1, 3, figsize=(14, 4.4))
    b = (-123.3, 37.2, -121.9, 38.3)
    ext = [b[0], b[2], b[1], b[3]]
    from pyproj import Transformer
    tr = Transformer.from_crs(32610, 4326, always_xy=True)
    lo, la = tr.transform([X0, X0 + NX * DX, X0 + NX * DX, X0, X0], [Y0, Y0, Y0 + NY * DX, Y0 + NY * DX, Y0])
    for a, (ch, t, cm, vmax) in zip(axs, ((0, "R: low-cloud probability x 255", "Blues", 255), (1, "G: confidence x 255", "Greens", 255), (2, "B: cloud top / 8 m", "turbo", 255))):
        imx = a.imshow(im[..., ch], extent=ext, cmap=cm, vmin=0, vmax=vmax)
        a.plot(lo, la, color=BOXC, lw=1.3)
        a.set_title(t); plt.colorbar(imx, ax=a, shrink=0.75)
    fig.suptitle(f"Output: beta macro/{x['file']} ({im.shape[1]} x {im.shape[0]} px, 0.01 deg over the GOES box). Red = 20 m city domain", fontweight="bold")
    save(fig, "macro_frame")
    fact("macro_index", dict(keys=list(mi), n=len(mi.get("frames", []))))


# ------------------------------------------------------------------ fog travel (5-minute)
def step_travel():
    fi = json.loads((DL / "fog5/index.json").read_text())
    fr = fi["frames"]
    import datetime as dt
    ts = [dt.datetime.fromisoformat(f["t"]) for f in fr]
    kinds = [f["kind"] for f in fr]
    col = {"observed": "#0a84ff", "interpolated": "#bf5af2", "forecast": "#ff9f0a"}
    pick = FACTS.get("fog5_pick") or []
    tile_name = pick[0]["tile"] if pick else None
    fig = plt.figure(figsize=(13, 7))
    gs = fig.add_gridspec(2, 3, height_ratios=[0.55, 1])
    a = fig.add_subplot(gs[0, :])
    a.scatter(ts, [f.get("conf", 0) or 0 for f in fr], c=[col.get(k, "k") for k in kinds], s=12)
    for k, c in col.items():
        a.scatter([], [], c=c, label=k)
    a.legend(ncol=3, fontsize=7, loc="upper left"); a.set_ylabel("mean confidence c"); a.set_title(f"fog5/index.json: {len(fr)} five-minute frames by kind")
    if tile_name:
        sheet = np.asarray(Image.open(DL / "fog5" / tile_name).convert("L")).astype(float)
        b = fig.add_subplot(gs[1, :2])
        b.imshow(np.sqrt(sheet / 255.0), cmap="Greys_r", vmin=0, vmax=0.6)
        b.add_patch(Rectangle((0, 0), 214, 203, fill=False, ec=BOXC, lw=2))
        b.set_title(f"{tile_name}: the 12 five-minute tiles of 03 UTC (19:00 PT). Red = :00"); b.set_xticks([]); b.set_yticks([])
        c = fig.add_subplot(gs[1, 2])
        t0 = sheet[0:203, 0:214]; t1 = sheet[0:203, 214 * 6:214 * 7] if sheet.shape[1] >= 214 * 7 else sheet[203:406, 0:214]
        c.imshow(np.sqrt(t0 / 255.0), cmap="Blues", vmin=0, vmax=0.6); c.set_title("tile :00 enlarged"); c.set_xticks([]); c.set_yticks([])
        for s in c.spines.values():
            s.set_edgecolor(BOXC); s.set_linewidth(1.5)
    save(fig, "travel")
    fact("fog5", dict(schema=fi.get("schema"), step_min=fi.get("step_min"), n=len(fr), kinds={k: kinds.count(k) for k in set(kinds)}, first=fr[0]["t"], last=fr[-1]["t"], tile=tile_name))


# ------------------------------------------------------------------ street wetness (downstream)
def step_wet():
    idx = json.loads((DL / "index.json").read_text())
    S = idx["ribbons_n"] + idx["ribbons_city_n"]
    arrs = [np.frombuffer((DL / f).read_bytes(), np.uint8).reshape(-1, S) for f in idx["frame_files"]]
    W = np.concatenate(arrs).astype(float)
    W[W == 255] = np.nan
    fact("wet", dict(frames=int(W.shape[0]), cols=S, mean=[float(np.nanmean(r)) for r in W], p90=[float(np.nanpercentile(r, 90)) for r in W]))


# ------------------------------------------------------------------ the wide map: where each input comes from
def step_map():
    from microclimate.live import config as C
    b = (HERE / "terrain_wide.bin").read_bytes()
    import struct
    mg, nx, ny, x0, y0, cell, zs = struct.unpack("<4sHHddff", b[:32])
    hz = np.frombuffer(b[32:32 + 2 * nx * ny], np.uint16).reshape(ny, nx).astype(float) * zs
    ext = [(x0) / 1000, (x0 + nx * cell) / 1000, (y0 - ny * cell) / 1000, y0 / 1000]
    fig, axs = plt.subplots(1, 2, figsize=(14, 6.6), gridspec_kw=dict(width_ratios=[1.1, 1]))
    for a, zoom in ((axs[0], False), (axs[1], True)):
        a.imshow(np.where(hz > 0, hz, np.nan), extent=ext, cmap="terrain", vmin=-150, vmax=1100)
        a.set_facecolor("#cfe3f5")
        a.add_patch(Rectangle((X0 / 1000, Y0 / 1000), NX * DX / 1000, NY * DX / 1000, fill=False, ec="#1c1c1e", lw=2.2, label="20 m wind / fog domain"))
        ll = [(lat, lon) for lat in np.linspace(*C.LAT_RANGE, C.N_LAT) for lon in np.linspace(*C.LON_RANGE, C.N_LON)]
        lx, ly = ll2utm([p[1] for p in ll], [p[0] for p in ll])
        coast = np.asarray(lx) < C.COAST_X_MAX
        a.scatter(np.asarray(lx)[~coast] / 1000, np.asarray(ly)[~coast] / 1000, marker="s", s=22, c="#1c1c1e", label="RRFS lattice point (Open-Meteo)")
        a.scatter(np.asarray(lx)[coast] / 1000, np.asarray(ly)[coast] / 1000, marker="s", s=22, c="#ff9f0a", label="lattice point in the coast series s(t)")
        kinds = {"coops": ("^", "#0a84ff", "CO-OPS"), "metar": ("D", "#bf5af2", "airport METAR"), "iem": ("o", "#30d158", "IEM / RAWS"), "nws": ("v", "#64d2ff", "NWS"), "ndbc": ("*", "#ff375f", "NDBC buoy")}
        seen = set()
        for sid, s in C.STATIONS.items():
            mk, cc, lab = kinds[s["kind"]]
            sx, sy = ll2utm(s["lon"], s["lat"])
            a.scatter(sx / 1000, sy / 1000, marker=mk, s=60 if mk == "*" else 30, c=cc, edgecolors="k", linewidths=0.4, label=None if lab in seen else lab, zorder=5)
            seen.add(lab)
            if zoom or s["kind"] in ("ndbc", "metar"):
                a.text(sx / 1000 + 0.3, sy / 1000 + 0.3, sid, fontsize=6.5, clip_on=True)
        snd = ll2utm(-122.22, 37.72)   # Oakland sounding site (approximately the airport)
        a.scatter(snd[0] / 1000, snd[1] / 1000, marker="P", s=70, c="#ffd60a", edgecolors="k", label="Oakland balloon sounding (approx.)", zorder=6)
        for bx_, nm in ((C.UPWIND_BOX, "upwind box"), (C.BAY_BOX, "bay box"), (C.TF_COAST_BOX, "GOES top box")):
            xs, ys = ll2utm([bx_[0], bx_[2], bx_[2], bx_[0], bx_[0]], [bx_[1], bx_[1], bx_[3], bx_[3], bx_[1]])
            a.plot(np.asarray(xs) / 1000, np.asarray(ys) / 1000, color="#5e5ce6", lw=1, ls="--")
            a.text(xs[3] / 1000, ys[3] / 1000 + 0.4, nm, color="#5e5ce6", fontsize=7, clip_on=True)
        a.set_xlabel("UTM 10N east (km)"); a.set_ylabel("UTM 10N north (km)")
        if zoom:
            a.set_xlim(X0 / 1000 - 26, X0 / 1000 + NX * DX / 1000 + 14); a.set_ylim(Y0 / 1000 - 10, Y0 / 1000 + NY * DX / 1000 + 12)
            a.set_title("Zoom (red box on the left); black = 20 m wind / fog domain, 17.1 x 16.2 km")
            for s_ in a.spines.values():
                s_.set_edgecolor(BOXC); s_.set_linewidth(2)
        else:
            gx, gy = ll2utm([-122.8, -122.8], [37.4, 37.4])
            zx0, zx1 = X0 / 1000 - 26, X0 / 1000 + NX * DX / 1000 + 14
            zy0, zy1 = Y0 / 1000 - 10, Y0 / 1000 + NY * DX / 1000 + 12
            a.add_patch(Rectangle((zx0, zy0), zx1 - zx0, zy1 - zy0, fill=False, ec=BOXC, lw=1.8))
            a.set_title("The whole GOES box (goes.BOX, 400 m wide terrain): every live input")
    axs[0].legend(loc="lower left", fontsize=6.5, framealpha=0.9)
    save(fig, "inputs_map")


def step_dl12():
    idx = json.loads((DL / "index.json").read_text())
    f3 = idx["fog3d"]["files"][FRAME3D]
    get(BETA + f3, DL / f3)
    fact("dl12_note", "fog3d_012.png fetched after the rest of the snapshot")


STEPS = dict(dl12=step_dl12, dl=step_dl, geom=step_geom, wind=step_wind, refine=step_refine, tracer=step_tracer, asset=step_asset, goes=step_goes, tiles=step_tiles, fog3d=step_fog3d,
             macro=step_macro, travel=step_travel, wet=step_wet, map=step_map)

if __name__ == "__main__":
    for s in (sys.argv[1:] or list(STEPS)):
        print("==", s, flush=True)
        try:
            STEPS[s]()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("STEP FAILED", s, e, flush=True)
