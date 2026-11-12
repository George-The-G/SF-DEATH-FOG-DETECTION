"""M2 part 1: steady mean wind for one inflow direction on the 20 m city grid (XLB, Warp backend, D3Q19, Smagorinsky LES)."""
import json
import math
import time
from pathlib import Path

import numpy as np
import warp as wp
import xlb
from scipy.ndimage import binary_erosion
from xlb.compute_backend import ComputeBackend
from xlb.grid import grid_factory
from xlb.operator.boundary_condition import (EquilibriumBC, ExtrapolationOutflowBC, FullwayBounceBackBC, HalfwayBounceBackBC, RegularizedBC)
from xlb.operator.macroscopic import Macroscopic
from xlb.operator.stepper import IncompressibleNavierStokesStepper
from xlb.precision_policy import PrecisionPolicy

U_LAT = 0.05  # inflow speed in lattice units; physical speed is rescaled afterwards (ratios only)


def idx_list(mask):
    i = np.where(mask)
    return [tuple(a.tolist()) for a in i]


def near_ground_mean(speed, solid, layers_above=2):
    """Mean speed ratio on the cell `layers_above` above the highest solid cell of each column (k=1 is the wall-adjacent layer
    and carries no information), over columns with enough headroom."""
    nz = solid.shape[2]
    top = nz - 1 - np.argmax(solid[:, :, ::-1], axis=2)  # highest solid k per column (floor k=0 is always solid)
    kk = top + layers_above
    ok = kk < nz - 6
    ii, jj = np.nonzero(ok)
    return float(speed[ii, jj, kk[ii, jj]].mean())


def run(solid, direction_deg, steps=40000, avg_steps=8000, avg_every=100, omega=1.95, log=print, check_every=1000):
    """Returns mean velocity ratio field u/U_in as float16 [3, nx, ny, nz] (x east, y north, z up) and diagnostics."""
    nx, ny, nz = solid.shape
    prec = PrecisionPolicy.FP32FP32
    be = ComputeBackend.WARP
    vs = xlb.velocity_set.D3Q19(precision_policy=prec, compute_backend=be)
    xlb.init(velocity_set=vs, default_backend=be, default_precision_policy=prec)
    # XLB keeps BC ids in a process-global registry; a second run() in the same process otherwise gets ids 6+ and silently
    # produces an all-zero flow field. Reset it so every run starts at id 1.
    from xlb.operator.boundary_condition.boundary_condition_registry import boundary_condition_registry
    boundary_condition_registry.__init__()
    grid = grid_factory((nx, ny, nz), compute_backend=be)
    th = math.radians(direction_deg)
    ux, uy = -math.sin(th) * U_LAT, -math.cos(th) * U_LAT  # wind FROM direction -> flow vector
    solid = solid.copy()
    solid[:, :, 0] = True  # ground / sea surface as a no-slip floor

    interior = {}
    box = np.zeros((nx, ny, nz), bool)
    for name, sl in {"left": (0, slice(None), slice(None)), "right": (nx - 1, slice(None), slice(None)),
                     "front": (slice(None), 0, slice(None)), "back": (slice(None), ny - 1, slice(None)),
                     "top": (slice(None), slice(None), nz - 1)}.items():
        m = np.zeros((nx, ny, nz), bool)
        m[sl] = True
        m[:, :, 0] = False
        box |= m
        interior[name] = m
    face_cells = np.zeros_like(box)
    for name, m in interior.items():
        e = m.copy()
        for other, m2 in interior.items():
            if other != name:
                e &= ~m2
        interior[name] = e
        face_cells |= e
    edges = box & ~face_cells  # edge and corner cells of the box
    # Faces whose normal flow component is ~0 (due N/E/S/W wind) are neither inlet nor outlet: a velocity inlet / extrapolation outlet
    # with purely tangential flow is ill-posed. Those faces, and the open top, get a far-field equilibrium BC (rho=1, free-stream u).
    # (A tangential Regularized velocity BC on the top made the density grow exponentially and the run go NaN.)
    eps = 1e-6
    inflow = [n for n, v in (("left", ux > eps), ("right", ux < -eps), ("front", uy > eps), ("back", uy < -eps)) if v]
    outflow = [n for n, v in (("left", ux < -eps), ("right", ux > eps), ("front", uy < -eps), ("back", uy > eps)) if v]
    lateral = [n for n in ("left", "right", "front", "back") if n not in inflow and n not in outflow]
    in_mask = np.zeros_like(box)
    for n in inflow:
        in_mask |= interior[n]
    far_mask = interior["top"].copy()
    for n in lateral:
        far_mask |= interior[n]
    out_mask = np.zeros_like(box)
    for n in outflow:
        out_mask |= interior[n]
    # solid shell: solid cells touching fluid or the box (interior solid cells carry no information)
    shell = solid & ~binary_erosion(solid, structure=np.ones((3, 3, 3), bool), border_value=1)
    shell &= ~(in_mask | out_mask | far_mask | edges)
    wall_cells = shell | (edges & ~in_mask & ~far_mask)
    bcs = [FullwayBounceBackBC(indices=idx_list(wall_cells & ~shell)),
           RegularizedBC("velocity", prescribed_value=(ux, uy, 0.0), indices=idx_list(in_mask & ~solid)),
           ExtrapolationOutflowBC(indices=idx_list(out_mask & ~solid)),
           EquilibriumBC(1.0, (ux, uy, 0.0), indices=idx_list(far_mask & ~solid)),
           HalfwayBounceBackBC(indices=idx_list(shell))]
    stepper = IncompressibleNavierStokesStepper(grid=grid, boundary_conditions=bcs, collision_type="SmagorinskyLESBGK")
    f0, f1, bc_mask, missing = stepper.prepare_fields()
    macro = Macroscopic(compute_backend=be, precision_policy=prec, velocity_set=vs)
    rho_f = grid.create_field(cardinality=1, dtype=prec.store_precision)
    u_f = grid.create_field(cardinality=3, dtype=prec.store_precision)
    acc, n_acc, t0 = None, 0, time.time()
    rho_lo, rho_hi = 1e9, -1e9
    for s in range(steps):
        f0, f1 = stepper(f0, f1, bc_mask, missing, omega, s)
        f0, f1 = f1, f0
        if s >= steps - avg_steps and (s % avg_every == 0):
            macro(f0, rho_f, u_f)
            wp.synchronize()
            u = u_f.numpy()
            acc = u.astype(np.float64) if acc is None else acc + u
            n_acc += 1
        if s % check_every == 0:
            macro(f0, rho_f, u_f)
            wp.synchronize()
            uu = u_f.numpy()
            # Only fluid cells count: solid cells deep inside terrain (bc id 0, no boundary condition) are never coupled to the flow and
            # can drift to garbage in a long run without affecting anything (their output is zeroed); the 180 / 360 runs were
            # aborted at step 1000 by such a cell while the largest fluid speed was 0.06 - 0.10 (a false alarm of this check).
            uu = np.where(solid[None], 0.0, uu)
            sp_ = np.sqrt((np.nan_to_num(uu, nan=1e9, posinf=1e9, neginf=1e9) ** 2).sum(0))
            if not np.isfinite(uu).all() or sp_.max() > 0.5:
                at = np.unravel_index(int(np.argmax(sp_)), sp_.shape)
                i_, j_, k_ = (int(a) for a in at)
                nb = solid[max(i_ - 1, 0):i_ + 2, max(j_ - 1, 0):j_ + 2, max(k_ - 1, 0):k_ + 2]
                fl_max = float(np.where(solid, 0.0, sp_).max())
                log(f"step {s}: NaN/inf or |u|>0.5 in velocity field (max at cell {tuple(int(a) for a in at)}, bc id {int(bc_mask.numpy()[(0,) + at])}, "
                    f"solid={bool(solid[at])}, solid neighbours={int(nb.sum())}/{nb.size}, max over fluid cells={fl_max:.3f}), aborting")
                return None, {"direction_deg": direction_deg, "steps": s, "aborted_at": s, "seconds": round(time.time() - t0),
                              "nan": True, "max_speed_ratio": 0.0, "mean_speed_ratio_near_ground": 0.0}
            umax = float(np.sqrt((uu ** 2).sum(0)).max())
            del uu
            rr = rho_f.numpy()
            rho_lo, rho_hi = min(rho_lo, float(rr.min())), max(rho_hi, float(rr.max()))
            del rr
            if s % 2000 == 0:
                log(f"step {s}/{steps} {time.time() - t0:.0f}s max|u|={umax:.4f} rho[{rho_lo:.3f},{rho_hi:.3f}]")
    mean = (acc / n_acc) / U_LAT
    mean[:, solid] = 0.0
    speed = np.sqrt((mean ** 2).sum(0))
    diag = {"direction_deg": direction_deg, "steps": steps, "seconds": round(time.time() - t0),
            "nan": bool(np.isnan(mean).any()), "max_speed_ratio": float(np.nanmax(speed)),
            "mean_speed_ratio_near_ground": near_ground_mean(speed, solid),
            "rho_min": rho_lo, "rho_max": rho_hi}
    return mean.astype(np.float16), diag


def build(cfg, grids_dir, lib_dir, directions=None, steps=40000, avg_steps=8000):
    grids_dir, lib_dir = Path(grids_dir), Path(lib_dir)
    lib_dir.mkdir(parents=True, exist_ok=True)
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    out = []
    for d in directions or cfg["library"]["directions_deg"]:
        f = lib_dir / f"wind_{int(d * 10):04d}.npz"
        fj = lib_dir / f"wind_{int(d * 10):04d}.json"
        if f.exists():
            try:
                j = json.load(open(fj))
                bad = bool(j.get("nan", True)) or not j.get("max_speed_ratio", 0.0) > 0.0
            except Exception:
                bad = True
            if not bad:
                continue
            print(f"discarding bad wind field {f.name} (nan=true or missing diagnostics)", flush=True)
            f.unlink()
        u, diag = run(solid, d, steps=steps, avg_steps=avg_steps)
        json.dump(diag, open(fj, "w"))
        if diag["nan"]:
            print(f"direction {d}: NaN, not saved: {json.dumps(diag)}", flush=True)
            out.append(diag)
            continue
        np.savez_compressed(f, u=u)
        out.append(diag)
        print(json.dumps(diag), flush=True)
    return out

