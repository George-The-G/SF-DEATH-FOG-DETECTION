"""M2 part 2: fog tracer. Steady advection-diffusion of a passive fog concentration on a stored mean wind (Warp, GPU).

Concentration is 1 in the inflow fog band and 0 elsewhere. Ground cells lose fog by deposition (vd/dx per second).
Results are relative (0-1), so humidity and fog thickness later scale them."""
import json
import math
import time
from pathlib import Path

import numpy as np
import warp as wp

wp.config.quiet = True


@wp.func
def pick(c: wp.array3d(dtype=wp.float32), s: wp.array3d(dtype=wp.int32), i: int, j: int, k: int, fb: float, nx: int, ny: int, nz: int):
    if i < 0 or j < 0 or k < 0 or i >= nx or j >= ny or k >= nz:
        return fb
    if s[i, j, k] != 0:
        return fb
    return c[i, j, k]


@wp.kernel
def step(c: wp.array3d(dtype=wp.float32), cn: wp.array3d(dtype=wp.float32), ux: wp.array3d(dtype=wp.float32),
         uy: wp.array3d(dtype=wp.float32), uz: wp.array3d(dtype=wp.float32), s: wp.array3d(dtype=wp.int32),
         fixed: wp.array3d(dtype=wp.float32), fixed_on: wp.array3d(dtype=wp.int32), dt: float, dx: float, kd: float,
         vd: float, loss: float, nx: int, ny: int, nz: int):
    i, j, k = wp.tid()
    if s[i, j, k] != 0:
        cn[i, j, k] = 0.0
        return
    if fixed_on[i, j, k] != 0:
        cn[i, j, k] = fixed[i, j, k]
        return
    c0 = c[i, j, k]
    xm = pick(c, s, i - 1, j, k, c0, nx, ny, nz)
    xp = pick(c, s, i + 1, j, k, c0, nx, ny, nz)
    ym = pick(c, s, i, j - 1, k, c0, nx, ny, nz)
    yp = pick(c, s, i, j + 1, k, c0, nx, ny, nz)
    zm = pick(c, s, i, j, k - 1, c0, nx, ny, nz)
    zp = pick(c, s, i, j, k + 1, c0, nx, ny, nz)
    u, v, w = ux[i, j, k], uy[i, j, k], uz[i, j, k]
    adv = float(0.0)
    if u > 0.0:
        adv += u * (c0 - xm) / dx
    else:
        adv += u * (xp - c0) / dx
    if v > 0.0:
        adv += v * (c0 - ym) / dx
    else:
        adv += v * (yp - c0) / dx
    if w > 0.0:
        adv += w * (c0 - zm) / dx
    else:
        adv += w * (zp - c0) / dx
    lap = (xm + xp + ym + yp + zm + zp - 6.0 * c0) / (dx * dx)
    out = c0 + dt * (kd * lap - adv) - dt * loss * c0
    below_solid = (k == 0) or (k > 0 and s[i, j, k - 1] != 0)
    if below_solid:
        out = out - dt * vd / dx * c0
    cn[i, j, k] = wp.max(0.0, wp.min(1.0, out))


def inflow_faces(direction_deg):
    th = math.radians(direction_deg)
    ux, uy = -math.sin(th), -math.cos(th)
    return [n for n, v in (("left", ux > 1e-6), ("right", ux < -1e-6), ("front", uy > 1e-6), ("back", uy < -1e-6)) if v]


def run_band(u, solid, spec, direction_deg, band, speed_ms=5.0, kd=2.0, vd=0.02, loss_per_km=0.0, transits=3.0, log=print, max_steps=120000):
    """u: [3,nx,ny,nz] velocity ratio (float16 ok). Returns steady concentration [nx,ny,nz] float16 and diagnostics."""
    nx, ny, nz = solid.shape
    dx = float(spec["cell_m"])
    uf = [(u[a].astype(np.float32) * speed_ms) for a in range(3)]
    umax = float(max(np.abs(x).max() for x in uf))
    dt = 0.4 * dx / max(umax, 1.0)
    fixed = np.zeros((nx, ny, nz), np.float32)
    on = np.zeros((nx, ny, nz), np.int32)
    zc = (np.arange(nz) + 0.5) * dx
    inband = ((zc >= band[0]) & (zc < band[1])).astype(np.float32)
    for name in inflow_faces(direction_deg):
        sl = {"left": (0, slice(None), slice(None)), "right": (nx - 1, slice(None), slice(None)),
              "front": (slice(None), 0, slice(None)), "back": (slice(None), ny - 1, slice(None))}[name]
        on[sl] = 1
        view = fixed[sl]
        view[:] = inband[None, :] if view.ndim == 2 else view
    on[:, :, nz - 1] = 1  # clear air above the fog band
    fixed[:, :, nz - 1] = 0.0
    solid_i = solid.astype(np.int32)
    dev = "cuda:0"
    A = lambda a, dt_=wp.float32: wp.array(a, dtype=dt_, device=dev)
    c = A(np.zeros((nx, ny, nz), np.float32))
    cn = A(np.zeros((nx, ny, nz), np.float32))
    UX, UY, UZ = (A(x) for x in uf)
    S, F, ON = A(solid_i, wp.int32), A(fixed), A(on, wp.int32)
    mean_u = float(np.sqrt(uf[0] ** 2 + uf[1] ** 2)[~solid].mean()) + 1e-3
    steps = int(transits * (nx * dx) / max(mean_u, 1.0) / dt)
    steps = min(max(steps, 4000), max_steps)
    t0, prev = time.time(), None
    for s_ in range(steps):
        wp.launch(step, dim=(nx, ny, nz), inputs=[c, cn, UX, UY, UZ, S, F, ON, dt, dx, kd, vd, loss_per_km / 1000.0, nx, ny, nz], device=dev)
        c, cn = cn, c
        if s_ == steps - 2001:
            wp.synchronize()
            prev = c.numpy().copy()
    wp.synchronize()
    out = c.numpy()
    drift = float(np.abs(out - prev).max()) if prev is not None else None
    diag = {"direction_deg": direction_deg, "band_m": band, "steps": steps, "dt_s": round(dt, 3), "seconds": round(time.time() - t0, 1),
            "nan": bool(np.isnan(out).any()), "max": float(out.max()), "drift_last_2000_steps": drift,
            "mean_fluid_below_100m": float(out[:, :, :5][~solid[:, :, :5]].mean())}
    return out.astype(np.float16), diag


def converge_band(u, solid, spec, direction_deg, band, tol=0.01, schedule=(6, 12, 24, 48, 96), max_steps=600000, **kw):
    """Rerun run_band from zero with growing `transits` until the drift over the last 2000 steps is < tol.
    The default 3 transits (run_band) leaves the high bands (300-800 m) unconverged because the tracer there leaks
    slowly through the top boundary and the deposition-limited ground layer. Returns (c, diag, history)."""
    hist = []
    for tr in schedule:
        c, diag = run_band(u, solid, spec, direction_deg, band, transits=tr, max_steps=max_steps, **kw)
        diag["transits"] = tr
        hist.append(diag)
        print(json.dumps(diag), flush=True)
        if diag["drift_last_2000_steps"] is not None and diag["drift_last_2000_steps"] < tol:
            break
    return c, diag, hist


def rebuild_bands(cfg, grids_dir, lib_dir, bands=(2, 3), direction=270, tol=0.01):
    """Back up fog_<dir>_<b>.npz/.json to *.bak.* once, then recompute those bands to convergence and overwrite."""
    import shutil
    grids_dir, lib_dir = Path(grids_dir), Path(lib_dir)
    spec = json.load(open(grids_dir / "grid_spec.json"))
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    lib = cfg["library"]
    u = np.load(lib_dir / f"wind_{int(direction * 10):04d}.npz")["u"]
    for bi in bands:
        stem = f"fog_{int(direction * 10):04d}_{bi}"
        for ext in (".npz", ".json"):
            bak = lib_dir / f"{stem}.bak{ext}"
            if not bak.exists():
                shutil.copy2(lib_dir / f"{stem}{ext}", bak)
        t0 = time.time()
        c, diag, hist = converge_band(u, solid, spec, direction, lib["fog_bands_m"][bi], tol=tol, speed_ms=lib["ref_speed_ms"],
                                      kd=lib["eddy_diffusivity_m2s"], vd=lib["deposition_velocity_ms"], loss_per_km=lib["fog_loss_per_km"])
        diag["total_seconds_incl_retries"] = round(time.time() - t0, 1)
        diag["attempts"] = [(h["transits"], h["drift_last_2000_steps"]) for h in hist]
        np.savez_compressed(lib_dir / f"{stem}.npz", c=c)
        json.dump(diag, open(lib_dir / f"{stem}.json", "w"))
        print("DONE", bi, json.dumps(diag), flush=True)


def build(cfg, grids_dir, lib_dir, directions=None):
    grids_dir, lib_dir = Path(grids_dir), Path(lib_dir)
    spec = json.load(open(grids_dir / "grid_spec.json"))
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    lib = cfg["library"]
    res = []
    for d in directions or lib["directions_deg"]:
        wf = lib_dir / f"wind_{int(d * 10):04d}.npz"
        if not wf.exists():
            continue
        u = np.load(wf)["u"]
        for bi, band in enumerate(lib["fog_bands_m"]):
            f = lib_dir / f"fog_{int(d * 10):04d}_{bi}.npz"
            if f.exists():
                continue
            c, diag = run_band(u, solid, spec, d, band, speed_ms=lib["ref_speed_ms"], kd=lib["eddy_diffusivity_m2s"],
                               vd=lib["deposition_velocity_ms"], loss_per_km=lib["fog_loss_per_km"])
            np.savez_compressed(f, c=c)
            json.dump(diag, open(lib_dir / f"fog_{int(d * 10):04d}_{bi}.json", "w"))
            print(json.dumps(diag), flush=True)
            res.append(diag)
    return res


def build_dirs(cfg, grids_dir, lib_dir, directions, tol=0.01, schedule=(3, 6, 12, 24, 48)):
    """Fog tracer bands for non-270 directions, each band converged (drift over the last 2000 steps < tol) by growing the number
    of transits; writes fog_<d>_<b>.npz/.json (diagnostics include the attempts). Skips bands that already exist."""
    grids_dir, lib_dir = Path(grids_dir), Path(lib_dir)
    spec = json.load(open(grids_dir / "grid_spec.json"))
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    lib = cfg["library"]
    for d in directions:
        stem0 = f"{int(round(d * 10)):04d}"
        wf = lib_dir / f"wind_{stem0}.npz"
        if not wf.exists():
            print(f"no wind field for {d}, skipping", flush=True)
            continue
        u = np.load(wf)["u"]
        for bi, band in enumerate(lib["fog_bands_m"]):
            f = lib_dir / f"fog_{stem0}_{bi}.npz"
            if f.exists():
                continue
            t0 = time.time()
            c, diag, hist = converge_band(u, solid, spec, d, band, tol=tol, schedule=schedule, speed_ms=lib["ref_speed_ms"],
                                          kd=lib["eddy_diffusivity_m2s"], vd=lib["deposition_velocity_ms"], loss_per_km=lib["fog_loss_per_km"])
            diag["total_seconds_incl_retries"] = round(time.time() - t0, 1)
            diag["attempts"] = [(h["transits"], h["drift_last_2000_steps"]) for h in hist]
            np.savez_compressed(f, c=c)
            json.dump(diag, open(lib_dir / f"fog_{stem0}_{bi}.json", "w"))
            print("DONE", d, bi, json.dumps(diag), flush=True)
