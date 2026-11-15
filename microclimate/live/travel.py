"""Multi-scale guide 08 Part A (switch config.FOG_TRAVEL, beta only): fog carried by the wind between 5-minute satellite scans. numpy only.

A(x, t) is the horizontal fog signal on the 214 x 203 tile grid (what guide 03 calls amp), kept at 5-minute steps:
  predict   semi-Lagrangian carry along the wind for one step; at the grid edge the inflow value (the prior at the nearest edge cell) comes in
  relax     A += (prior - A) * dt / TAU_PRIOR_S  (the model reasserts itself when nothing corrects it)
  observe   obs = clip(p * G, 0, 1), G = clip(trb / max(2 km box mean of trb, 0.02), 0, 2): the satellite's km-scale fog on the cells the tracer can reach
  correct   A += K * (obs - A), K = KMAX * c
Tile = 255 * A * trb; the 3D file is A * C3 * weight3d as in guide 03. With no scans A converges to the prior, so the path degrades to today's.
Wind here is uniform (the hour's speed and direction times TRAVEL_SPEED_FACTOR); the terrain-aware fogflow.js wind of the guide is NOT used yet.
Rows run north to south, columns west to east; wind direction is where the wind comes from."""
import numpy as np

STEP_S = 300.0               # the 5-minute step
TRAVEL_PAST_H = 6.0          # window start before now (h)
TRAVEL_FWD_H = 2.0           # window end after now (h)
TAU_PRIOR_S = 5400.0         # first guess: the model reasserts itself over about 1.5 h
KMAX = 0.5                   # largest correction gain (at c = 1)
TRAVEL_SPEED_FACTOR = 1.3    # 10 m wind to the fog layer (first guess; the front-speed factor of the model guides)
GUIDE_BOX = 25               # cells of the 2 km box filter on the 80 m grid
GUIDE_FLOOR = 0.02
GUIDE_MAX = 2.0
SCAN_TOL_S = 150.0           # a scan corrects the step it is within this many seconds of


def wind_uv(speed_ms, dir_from_deg, factor=TRAVEL_SPEED_FACTOR):
    """(east, north) m/s of the fog layer from the 10 m speed and the direction the wind comes FROM."""
    r = np.radians(dir_from_deg)
    return -np.sin(r) * speed_ms * factor, -np.cos(r) * speed_ms * factor


def predict(A, edge, u, v, dt_s=STEP_S, cell_m=80.0):
    """One semi-Lagrangian step. A, edge [H, W]; u, v east / north m/s (scalar or [H, W]). Departure point = cell - velocity * dt (bilinear gather);
    a departure point outside the grid takes the value of `edge` at the nearest cell (the inflow)."""
    H, W = A.shape
    rr, cc = np.meshgrid(np.arange(H, dtype=np.float64), np.arange(W, dtype=np.float64), indexing="ij")
    c0 = cc - np.asarray(u, np.float64) * dt_s / cell_m          # east wind: the air came from the west (smaller column)
    r0 = rr + np.asarray(v, np.float64) * dt_s / cell_m          # north wind (v > 0): came from the south (larger row)
    inside = (c0 >= 0) & (c0 <= W - 1) & (r0 >= 0) & (r0 <= H - 1)
    ci, ri = np.clip(c0, 0, W - 1), np.clip(r0, 0, H - 1)
    i0, j0 = np.minimum(np.floor(ri).astype(int), H - 2), np.minimum(np.floor(ci).astype(int), W - 2)
    ty, tx = ri - i0, ci - j0
    a = A.astype(np.float64)
    val = (a[i0, j0] * (1 - ty) * (1 - tx) + a[i0, j0 + 1] * (1 - ty) * tx + a[i0 + 1, j0] * ty * (1 - tx) + a[i0 + 1, j0 + 1] * ty * tx)
    ei, ej = np.clip(np.rint(r0).astype(int), 0, H - 1), np.clip(np.rint(c0).astype(int), 0, W - 1)
    return np.where(inside, val, edge[ei, ej]).astype(np.float32)


def relax(A, prior, dt_s=STEP_S, tau_s=TAU_PRIOR_S):
    return (A + (prior - A) * (dt_s / tau_s)).astype(np.float32)


def box_mean(a, n=GUIDE_BOX):
    """Separable n-cell box mean with edge padding (cumulative sums)."""
    h = n // 2
    out = np.asarray(a, np.float64)
    for ax in (0, 1):
        p = np.concatenate([np.repeat(np.take(out, [0], axis=ax), h, axis=ax), out, np.repeat(np.take(out, [-1], axis=ax), h, axis=ax)], axis=ax)
        cs = np.cumsum(p, axis=ax)
        zero = np.zeros_like(np.take(cs, [0], axis=ax))
        cs = np.concatenate([zero, cs], axis=ax)
        n_ax = out.shape[ax]
        out = (np.take(cs, np.arange(n, n + n_ax), axis=ax) - np.take(cs, np.arange(0, n_ax), axis=ax)) / n
    return out


def guide(trb):
    """G = clip(trb / max(2 km box mean of trb, GUIDE_FLOOR), 0, GUIDE_MAX)."""
    return np.clip(trb / np.maximum(box_mean(trb), GUIDE_FLOOR), 0.0, GUIDE_MAX).astype(np.float32)


def observation(p, trb):
    """obs on the tile grid from the satellite probability p [H, W] (0..1): 0 where the tracer is 0."""
    return np.clip(np.asarray(p, np.float32) * guide(trb), 0.0, 1.0).astype(np.float32)


def correct(A, obs, c, kmax=KMAX):
    K = kmax * np.clip(c, 0.0, 1.0)
    return np.clip(A + K * (obs - A), 0.0, 1.0).astype(np.float32)


def step(A, prior, u, v, obs=None, c=None, dt_s=STEP_S, cell_m=80.0):
    """predict + relax (+ correct when a scan is given)."""
    A = relax(predict(A, prior, u, v, dt_s, cell_m), prior, dt_s)
    if obs is not None:
        A = correct(A, obs, c)
    return A


def run(times, prior_fn, trb_fn, wind_fn, scan_fn, A0, tol_s=SCAN_TOL_S, backward=True, cell_m=80.0):
    """The cycle over the 5-minute `times` (unix seconds, ascending, 300 s apart).
    prior_fn(t) -> amp5 [H, W]; trb_fn(t) -> trb [H, W]; wind_fn(t) -> (speed m/s, direction from, deg); scan_fn(t) -> (p, c) [H, W] each, or None when no scan is within tol_s.
    Returns (frames [n, H, W] float32, kinds [n]): 'observed' where a scan corrected the step (backward pass averaged in), 'interpolated' between scans up to the newest one,
    'forecast' after the newest scan (predict and relax only). `backward` runs the cycle backward in time from the newest scan and averages the two for the observed window."""
    n = len(times)
    scans = [scan_fn(t) for t in times]
    newest = max([i for i, s in enumerate(scans) if s is not None], default=-1)

    def sweep(order, sign, A):
        out = [None] * n
        for i in order:
            t = times[i]
            u, v = wind_uv(*wind_fn(t))
            A = step(A, prior_fn(t), sign * u, sign * v, *(observation(scans[i][0], trb_fn(t)), scans[i][1]) if scans[i] is not None else (), cell_m=cell_m)
            out[i] = A
        return out
    fwd = sweep(range(n), 1.0, A0)
    frames = list(fwd)
    if backward and newest > 0:
        bwd = sweep(range(newest, -1, -1), -1.0, fwd[newest])
        for i in range(newest):                       # the newest step keeps the forward value
            frames[i] = ((fwd[i] + bwd[i]) * 0.5).astype(np.float32)
    kinds = np.array(["forecast" if i > newest else ("observed" if scans[i] is not None else "interpolated") for i in range(n)])
    return np.stack(frames).astype(np.float32), kinds


def window(now_unix, past_h=TRAVEL_PAST_H, fwd_h=TRAVEL_FWD_H, step_s=STEP_S):
    """5-minute time stamps (unix s) from past_h before now to fwd_h after, aligned to the step."""
    t0 = int(now_unix // step_s * step_s) - int(past_h * 3600)
    n = int((past_h + fwd_h) * 3600 // step_s) + 1
    return t0 + step_s * np.arange(n, dtype=np.int64)
