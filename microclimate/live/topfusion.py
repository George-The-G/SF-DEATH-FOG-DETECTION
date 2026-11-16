"""Fog top by fusing measurements (guide 06, switch TOP_FUSION).

One-dimensional Kalman filter over the hourly fog top (random walk), observations taken in turn each hour, then a Rauch-Tung-Striebel backward pass over the hours
up to the last one that has a non-model observation (a sounding improves the hours before it; forecast hours keep the forward result). Plain numpy.
Metres above sea level throughout. Pure functions: nothing here reads config state except the defaults of `fuse`.
"""
import numpy as np

from . import config as C

ORDER = ("model", "goes", "ceiling", "sounding")   # update order inside an hour: the most precise source last


def fuse(obs, lower=None, p0=None, q=None, gate=None):
    """obs: {name: (z[nh], sigma[nh])} with NaN where a source has no value that hour; must hold 'model' (the first model value starts the state).
    lower: optional [nh] hard lower bound (NaN = none). Returns (top[nh], sd[nh], info): top is the smoothed state with the lower bound applied (the caller applies the
    floor and cap), sd the smoothed standard deviation. info: per source hours used / rejected."""
    p0 = C.TF_P0_M if p0 is None else p0
    q = C.TF_Q_M if q is None else q
    gate = C.TF_GATE_SIGMA if gate is None else gate
    zm = np.asarray(obs["model"][0], float)
    nh = len(zm)
    names = [k for k in ORDER if k in obs] + [k for k in obs if k not in ORDER]
    used = {k: 0 for k in names}
    rej = {k: 0 for k in names}
    first = np.nonzero(np.isfinite(zm))[0]
    x0 = float(zm[first[0]]) if len(first) else 300.0
    xf, pf = np.zeros(nh), np.zeros(nh)      # filtered
    xp, pp = np.zeros(nh), np.zeros(nh)      # predicted
    x, P = x0, p0 ** 2
    for k in range(nh):
        if k > 0:
            P += q ** 2
        xp[k], pp[k] = x, P
        for nm in names:
            z, s = obs[nm][0][k], obs[nm][1][k]
            if not (np.isfinite(z) and np.isfinite(s)):
                continue
            if abs(z - x) > gate * np.sqrt(P + s * s):
                rej[nm] += 1
                continue
            K = P / (P + s * s)
            x += K * (z - x)
            P *= 1.0 - K
            used[nm] += 1
        xf[k], pf[k] = x, P
    # backward pass up to the last hour with a non-model observation
    kl = -1
    for nm in names:
        if nm == "model":
            continue
        z = np.asarray(obs[nm][0], float)
        ok = np.nonzero(np.isfinite(z) & np.isfinite(np.asarray(obs[nm][1], float)))[0]
        if len(ok):
            kl = max(kl, int(ok[-1]))
    xs, ps = xf.copy(), pf.copy()
    for k in range(kl - 1, -1, -1):
        c = pf[k] / pp[k + 1]
        xs[k] = xf[k] + c * (xs[k + 1] - xp[k + 1])
        ps[k] = pf[k] + c * c * (ps[k + 1] - pp[k + 1])
    top = xs.copy()
    if lower is not None:
        lw = np.asarray(lower, float)
        top = np.where(np.isfinite(lw), np.maximum(top, lw), top)
    info = {nm: dict(used=used[nm], rejected=rej[nm]) for nm in names}
    return top, np.sqrt(np.maximum(ps, 0.0)), info
