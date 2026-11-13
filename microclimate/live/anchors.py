"""Stations as anchors (guide 03 step 3): pure numerics, no network.

Wind (3b-3d): per hour, fit the inflow speed U_r and direction th_r of a few city regions so that the simulated wind vectors of the library
(station_wind.npz: velocity / inflow speed at the station, per library direction) match the in-grid station winds. The prior pulls each region to the
forecast wind, a smoothness term ties neighbouring regions, and a region with fewer than two anchors takes the citywide fit.
Directions are carried as th in [90, 450) (so 10 degrees is 370); the library covers 180..360 only, anything else is clamped and flagged offshore.
Temperature / dew point (3f): per hour a sea-level offset a and a lapse adjustment b fitted to (station - lattice value moved to the station height).
"""
import numpy as np

from .. import dirlib
from . import config as C


# ------------------------------------------------------------------ directions and vectors
def wrap_dir(d):
    """Any direction (deg) -> [90, 450): north-east winds (0..90) are carried as 360..450 so that the library range 180..360 is contiguous."""
    d = np.asarray(d, float) % 360.0
    return np.where(d < 90.0, d + 360.0, d)


def eff_dir(th):
    """Direction the library can represent."""
    return np.clip(th, 180.0, 360.0)


def to_uv(spd, d):
    r = np.radians(d)
    return -spd * np.sin(r), -spd * np.cos(r)


def from_uv(u, v):
    return np.hypot(u, v), (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0


def region_weights(x, y, cx, cy, sigma):
    """[R, N] Gaussian weights normalised to sum to 1 at every point (softmax of -d^2 / 2 sigma^2: smooth, stable far from every centre)."""
    x, y = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
    z = -((x[None, :] - np.asarray(cx)[:, None]) ** 2 + (y[None, :] - np.asarray(cy)[:, None]) ** 2) / (2.0 * sigma ** 2)
    z = z - z.max(axis=0, keepdims=True)
    w = np.exp(z)
    return w / w.sum(axis=0, keepdims=True)


def dir_weights(th, dirs):
    """Library blend weights [..., D] for directions th (deg, in the carried [90, 450) form)."""
    t = eff_dir(np.asarray(th, float))
    flat = dirlib.weights(t.ravel(), np.asarray(dirs, float))
    return flat.reshape(t.shape + (len(dirs),))


def predict(U, th, W, uvlib, dirs):
    """Predicted station vectors [S, 2] = sum_r W[r, i] U_r uv_i(th_r)."""
    pred = np.zeros((W.shape[1], 2))
    for r in range(len(U)):
        wd = dir_weights(th[r], dirs)
        pred += W[r][:, None] * U[r] * np.tensordot(wd, uvlib, axes=1)
    return pred


# ------------------------------------------------------------------ small Levenberg-Marquardt (no scipy)
def lm(res, p0, lo, hi, iters=60):
    p = np.clip(np.asarray(p0, float), lo, hi)
    r = res(p)
    c = float(r @ r)
    lam = 1e-2
    eps = np.where(np.arange(len(p)) < len(p) // 2, 1e-3, 0.05)
    for _ in range(iters):
        J = np.empty((len(r), len(p)))
        for k in range(len(p)):
            q = p.copy()
            q[k] += eps[k]
            J[:, k] = (res(q) - r) / eps[k]
        A, g = J.T @ J, J.T @ r
        step_ok = False
        for _ in range(12):
            dp = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -g)
            pn = np.clip(p + dp, lo, hi)
            rn = res(pn)
            cn = float(rn @ rn)
            if cn < c:
                small = np.max(np.abs(pn - p) / eps) < 3.0
                p, r, c = pn, rn, cn
                lam = max(lam * 0.3, 1e-9)
                step_ok = True
                break
            lam *= 5.0
        if not step_ok or small:
            break
    return p, c


def circ_mean(deg_wrapped, w=None):
    d = np.radians(np.asarray(deg_wrapped, float))
    w = np.ones_like(d) if w is None else np.asarray(w, float)
    return float(wrap_dir(np.degrees(np.arctan2((w * np.sin(d)).sum(), (w * np.cos(d)).sum()))))


def qc_mask(uv, limit=None):
    """Valid-and-not-outlier mask over stations: a vector differing from the median of the others by more than `limit` m/s is dropped (needs 3 valid)."""
    limit = C.WIND_QC_MS if limit is None else limit
    ok = np.isfinite(uv).all(axis=1)
    keep = ok.copy()
    if ok.sum() >= 3:
        idx = np.nonzero(ok)[0]
        for i in idx:
            others = [j for j in idx if j != i]
            med = np.median(uv[others], axis=0)
            if np.hypot(*(uv[i] - med)) > limit:
                keep[i] = False
    return keep, int(ok.sum() - keep.sum())


def fit_hour(obs_uv, W, uvlib, dirs, Uf, thf, nbrs):
    """One hour. obs_uv [S, 2] (NaN = no report), W [R, S] region weights at the stations, uvlib [D, S, 2], Uf / thf [R] forecast inflow (m/s, carried deg).
    -> dict(U [R], th [R] (raw, carried), mode 'regional' | 'citywide' | 'forecast', n, dropped, active [R], offshore)."""
    R = len(Uf)
    out = dict(U=np.array(Uf, float), th=np.array(thf, float), mode="forecast", n=0, dropped=0, active=np.zeros(R, bool), offshore=False)
    keep, dropped = qc_mask(obs_uv)
    out["dropped"] = dropped
    if not keep.any():
        return out
    obs = obs_uv[keep]
    uvl = uvlib[:, keep, :]
    Wk = W[:, keep]
    out["n"] = int(keep.sum())
    so, sU, sT = C.WIND_OBS_SIGMA_MS, C.WIND_PRIOR_SIGMA_MS, C.WIND_PRIOR_SIGMA_DEG
    lo_u, hi_u = C.WIND_U_MIN_MS, C.WIND_U_MAX_MS
    # stage 1: citywide inflow (one region), prior = the mean forecast
    fu, fv = to_uv(np.asarray(Uf), np.asarray(thf))
    Uc0, thc0 = from_uv(fu.mean(), fv.mean())[0], float(wrap_dir(from_uv(fu.mean(), fv.mean())[1]))
    ones = np.ones((1, obs.shape[0]))

    def res1(p):
        d = (obs - predict(p[:1], p[1:], ones, uvl, dirs)).ravel() / so
        return np.concatenate([d, [(p[0] - Uc0) / sU, (p[1] - thc0) / sT]])
    p1, _ = lm(res1, [Uc0, thc0], [lo_u, 90.0], [hi_u, 450.0])
    Uc, thc = float(p1[0]), float(p1[1])
    # regions with at least WIND_REGION_MIN_ANCHORS reporting anchors fit their own inflow
    cnt = (Wk >= C.WIND_ANCHOR_MIN_W).sum(axis=1)
    active = cnt >= C.WIND_REGION_MIN_ANCHORS
    U = np.full(R, Uc)
    th = np.full(R, thc)
    out["mode"] = "citywide"
    if active.any():
        ia = np.nonzero(active)[0]
        na = len(ia)

        def unpack(p):
            Uf_, tf_ = U.copy(), th.copy()
            Uf_[ia], tf_[ia] = p[:na], p[na:]
            return Uf_, tf_

        def res2(p):
            Ua, ta = unpack(p)
            parts = [(obs - predict(Ua, ta, Wk, uvl, dirs)).ravel() / so,
                     (Ua[ia] - np.asarray(Uf)[ia]) / sU, (ta[ia] - np.asarray(thf)[ia]) / sT]
            for a, b in nbrs:
                if active[a] or active[b]:
                    parts.append([(Ua[a] - Ua[b]) / C.WIND_SMOOTH_SIGMA_MS, (ta[a] - ta[b]) / C.WIND_SMOOTH_SIGMA_DEG])
            return np.concatenate([np.atleast_1d(np.asarray(q, float)) for q in parts])
        p2, _ = lm(res2, np.concatenate([U[ia], th[ia]]), np.concatenate([np.full(na, lo_u), np.full(na, 90.0)]), np.concatenate([np.full(na, hi_u), np.full(na, 450.0)]))
        U, th = unpack(p2)
        out["mode"] = "regional"
    out["U"], out["th"], out["active"] = U, th, active
    used = np.nonzero(active)[0] if active.any() else np.arange(1)
    # offshore: the fit sits at the edge of the library range (the prior keeps it from leaving it), so also look at the observed vectors themselves
    om = float(wrap_dir(from_uv(*obs.sum(axis=0))[1])) if np.hypot(*obs.sum(axis=0)) > 0.5 else 270.0
    out["offshore"] = bool(np.any((th[used] < 180.0) | (th[used] > 360.0)) or om < 180.0 or om > 360.0)
    return out


def fit_series(obs_uv, W, uvlib, dirs, Uf, thf, nbrs):
    """obs_uv [nh, S, 2]; Uf, thf [nh, R]. -> dict of arrays: U, th [nh, R] (NaN where no station reported), offshore [nh], mode [nh], n [nh], dropped [nh]."""
    nh, R = Uf.shape
    U, th = np.full((nh, R), np.nan), np.full((nh, R), np.nan)
    off, mode, n, dr = np.zeros(nh, bool), np.empty(nh, object), np.zeros(nh, int), np.zeros(nh, int)
    for h in range(nh):
        o = fit_hour(obs_uv[h], W, uvlib, dirs, Uf[h], thf[h], nbrs)
        mode[h], n[h], dr[h], off[h] = o["mode"], o["n"], o["dropped"], o["offshore"]
        if o["mode"] != "forecast":
            U[h], th[h] = o["U"], o["th"]
    return dict(U=U, th=th, offshore=off, mode=mode, n=n, dropped=dr)


def roll_med_dir(th, w):
    """Causal rolling median of carried directions (relative to the newest value in the window)."""
    out = np.full(len(th), np.nan)
    for k in range(len(th)):
        seg = th[max(0, k - w + 1):k + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            ref = seg[-1]
            out[k] = ref + np.median((seg - ref + 180.0) % 360.0 - 180.0)
    return out


# ------------------------------------------------------------------ temperature / dew point
def fit_T_hour(resid, dz_km, min_n=None):
    """resid [S] (obs minus lattice moved to the station height with the base lapse; NaN = no report), dz_km [S] (station minus lattice height, km).
    Ridge least squares for the sea-level offset a (K) and the lapse adjustment b (K/km). None when fewer than min_n stations report."""
    min_n = C.T_FIT_MIN_STATIONS if min_n is None else min_n
    ok = np.isfinite(resid) & np.isfinite(dz_km)
    if ok.sum() < min_n:
        return None
    A = np.stack([np.ones(ok.sum()), dz_km[ok]], axis=1)
    sig2 = 0.5 ** 2
    M = A.T @ A / sig2 + np.diag([1.0 / C.T_PRIOR_OFFSET_K ** 2, 1.0 / C.T_PRIOR_LAPSE_K_PER_KM ** 2])
    a, b = np.linalg.solve(M, A.T @ resid[ok] / sig2)
    return float(np.clip(a, -C.TEMP_CLIP_C, C.TEMP_CLIP_C)), float(np.clip(b, *C.T_LAPSE_ADJ_CLIP))


def horizontal(resid, sx, sy, px, py, L=None):
    """Fallback (fewer than T_FIT_MIN_STATIONS): sum_j w_j r_j / max(1, sum_j w_j) with Gaussian w_j(distance, L): equals the residual at a lone station and
    is pulled to zero away from the stations. resid [S] (NaN skipped); -> [N]."""
    L = C.T_HORIZ_L_M if L is None else L
    ok = np.isfinite(resid)
    px, py = np.atleast_1d(np.asarray(px, float)), np.atleast_1d(np.asarray(py, float))
    if not ok.any():
        return np.zeros(len(px))
    d2 = (px[:, None] - np.asarray(sx)[ok][None, :]) ** 2 + (py[:, None] - np.asarray(sy)[ok][None, :]) ** 2
    w = np.exp(-d2 / (2.0 * L ** 2))
    return (w * resid[ok][None, :]).sum(axis=1) / np.maximum(1.0, w.sum(axis=1))
