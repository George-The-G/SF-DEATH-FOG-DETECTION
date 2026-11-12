"""Wind library for the other inflow directions: rotated-frame runs.

The XLB setup in wind.py only works for a due-west (270) inflow on the axis-aligned box. For any other wind-FROM direction
theta the city grid is resampled into a frame whose +x' axis points DOWNWIND (flow vector f = (-sin t, -cos t) in east/north)
and +y' points to the left of the flow (n = (-fy, fx)). In that frame the wind is a 270-style "from the left" inflow, which
wind.run() handles. The rotated box is the circumscribed rectangle of the original domain, so it has more cells (about 1.7x
at 45 degrees); cells outside the original domain take the nearest edge column (terrain and buildings continued outward,
sea stays sea). The mean field is rotated back onto the original grid (vector components rotated, interpolation weighted
by the fluid mask so zero wall cells do not drag the fluid values down).

Axis-aligned directions (180, 360, 90) are exact index permutations of the grid (no interpolation).

Stored files follow the 270 naming: library/wind_<10*dir:04d>.npz (u float16 [3,nx,ny,nz], velocity / inflow speed in
east, north, up) and wind_<..>.json (diagnostics).
"""
import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt, map_coordinates

DX = 20.0
PAD_UP = 100  # cells (2 km) of upwind fetch added in front of the domain (filler terrain tapers to sea level, see rotate_solid)
TAPER_CELLS = 50.0  # filler terrain falls to sea level over this distance (1 km) outside the original domain


def frame(theta_deg, nx, ny, pad=None):
    """Geometry of the rotated frame: flow vector f, left normal n, rotated grid size."""
    t = math.radians(theta_deg)
    fx, fy = -math.sin(t), -math.cos(t)
    for v in (0.0, 1.0, -1.0):          # kill 1e-16 noise so axis-aligned sizes are exact
        if abs(fx - v) < 1e-12:
            fx = v
        if abs(fy - v) < 1e-12:
            fy = v
    nxn, nyn = -fy, fx
    W, H = nx, ny                        # in cells
    nxr = int(math.ceil(W * abs(fx) + H * abs(fy) - 1e-9))
    nyr = int(math.ceil(W * abs(nxn) + H * abs(nyn) - 1e-9))
    pad = PAD_UP if pad is None else pad
    return dict(f=(fx, fy), n=(nxn, nyn), nxr=nxr, nyr=nyr, pad=pad)  # nxr/nyr: core size; the run grid is nxr + pad long in x


def _coords_rot_to_geo(fr, nx, ny):
    """Original-grid (float) index of every rotated cell centre: returns (I, J) arrays [nxr, nyr]."""
    fx, fy = fr["f"]
    nxn, nyn = fr["n"]
    ir = np.arange(fr["nxr"] + fr["pad"], dtype=np.float64) + 0.5 - fr["nxr"] / 2.0 - fr["pad"]  # x' in cells, centred on the core
    jr = np.arange(fr["nyr"], dtype=np.float64) + 0.5 - fr["nyr"] / 2.0
    X, Y = np.meshgrid(ir, jr, indexing="ij")
    I = nx / 2.0 + X * fx + Y * nxn - 0.5
    J = ny / 2.0 + X * fy + Y * nyn - 0.5
    return I, J


def _coords_geo_to_rot(fr, nx, ny):
    """Rotated-grid (float) index of every original cell centre: (I', J') arrays [nx, ny]."""
    fx, fy = fr["f"]
    nxn, nyn = fr["n"]
    ig = np.arange(nx, dtype=np.float64) + 0.5 - nx / 2.0
    jg = np.arange(ny, dtype=np.float64) + 0.5 - ny / 2.0
    X, Y = np.meshgrid(ig, jg, indexing="ij")
    xr = X * fx + Y * fy
    yr = X * nxn + Y * nyn
    return xr + fr["nxr"] / 2.0 + fr["pad"] - 0.5, yr + fr["nyr"] / 2.0 - 0.5


def rotate_solid(solid, theta_deg):
    """Resample the boolean solid grid [nx,ny,nz] into the downwind frame. Linear interpolation of the 0/1 field
    thresholded at 0.5 (nearest-edge continuation outside the original domain). Returns (solid_rot, frame)."""
    nx, ny, nz = solid.shape
    fr = frame(theta_deg, nx, ny)
    I, J = _coords_rot_to_geo(fr, nx, ny)
    out = np.zeros((fr["nxr"] + fr["pad"], fr["nyr"], nz), bool)
    for k in range(nz):
        v = map_coordinates(solid[:, :, k].astype(np.float32), [I, J], order=1, mode="nearest")
        out[:, :, k] = v >= 0.5
    # Outside the original domain the edge-continued terrain (Marin / San Bruno / Daly City / East Bay ridges extruded into long
    # walls, plus tall cliffs next to the inflow BC) made the runs blow up, so the column height there is scaled by
    # 1 - smoothstep(distance_from_domain / TAPER_CELLS): terrain inside the real domain is untouched, the filler becomes sea.
    inside = (I >= -0.5) & (I <= nx - 0.5) & (J >= -0.5) & (J <= ny - 0.5)
    dist = distance_transform_edt(~inside)
    s = np.clip(dist / TAPER_CELLS, 0.0, 1.0)
    m = 1.0 - s * s * (3 - 2 * s)
    cnt = out.sum(axis=2)                                    # columns are contiguous from k=0
    keep = np.rint(cnt * m).astype(int)
    out = np.where(inside[:, :, None], out, np.arange(nz)[None, None, :] < keep[:, :, None])
    return out, fr


def unrotate_field(u_rot, solid, theta_deg, fr=None):
    """u_rot [3, nxr, nyr, nz] in the downwind frame (components along x', y', z) -> [3, nx, ny, nz] float16 in
    east/north/up. Fluid-mask-weighted bilinear interpolation; solid cells are 0."""
    nx, ny, nz = solid.shape
    fr = fr or frame(theta_deg, nx, ny)
    solid_r, _ = rotate_solid(solid, theta_deg)
    I, J = _coords_geo_to_rot(fr, nx, ny)
    fx, fy = fr["f"]
    nxn, nyn = fr["n"]
    out = np.zeros((3, nx, ny, nz), np.float16)
    for k in range(nz):
        fl = (~solid_r[:, :, k]).astype(np.float32)
        wgt = map_coordinates(fl, [I, J], order=1, mode="nearest")
        comp = []
        for a in range(3):
            num = map_coordinates(u_rot[a][:, :, k].astype(np.float32) * fl, [I, J], order=1, mode="nearest")
            comp.append(np.where(wgt > 0.05, num / np.maximum(wgt, 1e-6), 0.0))
        ue = comp[0] * fx + comp[1] * nxn
        un = comp[0] * fy + comp[1] * nyn
        for a, v in enumerate((ue, un, comp[2])):
            v = np.where(solid[:, :, k], 0.0, v)
            out[a][:, :, k] = v.astype(np.float16)
    return out


def run_direction(solid, theta_deg, steps=40000, avg_steps=8000, log=print):
    """Run the rotated-frame simulation for wind-FROM direction theta and return (u_geo float16 [3,nx,ny,nz], diag)."""
    from . import wind
    nx, ny, nz = solid.shape
    t0 = time.time()
    sr, fr = rotate_solid(solid, theta_deg)
    log(f"direction {theta_deg}: rotated grid {sr.shape} = {sr.size / 1e6:.1f}M cells, solid fraction {sr.mean():.4f} (orig {solid.mean():.4f})")
    u_rot, diag = wind.run(sr, 270.0, steps=steps, avg_steps=avg_steps, log=log)
    diag["direction_deg"] = theta_deg
    diag["frame"] = {"nxr": fr["nxr"], "nyr": fr["nyr"], "cells_M": round(sr.size / 1e6, 1)}
    if u_rot is None:
        return None, diag
    u = unrotate_field(u_rot, solid, theta_deg, fr)
    sp = np.sqrt((u.astype(np.float32) ** 2).sum(0))
    diag["max_speed_ratio"] = float(sp.max())
    diag["mean_speed_ratio_near_ground"] = wind.near_ground_mean(sp, np.concatenate([np.ones(solid.shape[:2] + (1,), bool), solid[:, :, 1:]], 2))
    diag["method"] = "rotated-frame (wind.run at 270 on the downwind-aligned resampled grid, field rotated back)"
    diag["total_seconds"] = round(time.time() - t0)
    return u, diag


def build(cfg, grids_dir, lib_dir, directions, steps=40000, avg_steps=8000, force=False):
    grids_dir, lib_dir = Path(grids_dir), Path(lib_dir)
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    out = []
    for d in directions:
        stem = f"wind_{int(round(d * 10)):04d}"
        f, fj = lib_dir / f"{stem}.npz", lib_dir / f"{stem}.json"
        if f.exists() and not force:
            print(f"{stem} exists, skipping", flush=True)
            continue
        u, diag = run_direction(solid, d, steps=steps, avg_steps=avg_steps, log=lambda s: print(s, flush=True))
        json.dump(diag, open(fj, "w"))
        if u is None or diag.get("nan"):
            print(f"direction {d}: NaN/abort, not saved: {json.dumps(diag)}", flush=True)
            out.append(diag)
            continue
        np.savez_compressed(f, u=u)
        print(json.dumps(diag), flush=True)
        out.append(diag)
    return out
