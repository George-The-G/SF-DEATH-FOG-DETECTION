"""Guide 11 spike: the four tests that must pass before any real case. Run: python spikes/marine_layer/run_checks.py (prints PASS / FAIL with numbers; not part of tests_mc)."""
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

sys.path.insert(0, str(Path(__file__).parent))
import swe  # noqa: E402

results = {}


def lake_at_rest():
    ny, nx, dx = 30, 40, 200.0
    y, x = np.mgrid[0:ny, 0:nx]
    b = 40 * np.exp(-((x - 12) ** 2 + (y - 15) ** 2) / 20.0) + 25 * np.exp(-((x - 28) ** 2 + (y - 8) ** 2) / 12.0) + 3 * np.sin(x / 3.0) * np.cos(y / 4.0)
    b = np.maximum(b, 0)
    top = 60.0                                  # level surface above the hills: wet everywhere
    gp = swe.reduced_gravity(7.0)
    m = swe.Model(b, np.maximum(top - b, 0.0), dx, gp, cd=0.005, bc=dict(west="wall", east="wall", north="wall", south="wall"))
    for _ in range(1000):
        m.step(dt=1.0)
    u, v = m.velocity()
    dev = float(np.abs((m.h + m.b) - top).max())
    vmax = float(max(np.abs(u).max(), np.abs(v).max()))
    # partially dry variant: the layer surface at 30 m, taller hills stay dry
    m2 = swe.Model(b, np.maximum(30.0 - b, 0.0), dx, gp, bc=dict(west="wall", east="wall", north="wall", south="wall"))
    for _ in range(1000):
        m2.step(dt=1.0)
    u2, v2 = m2.velocity()
    vmax2 = float(max(np.abs(u2).max(), np.abs(v2).max()))
    ok = vmax < 1e-6 and vmax2 < 1e-6
    results["1 lake at rest"] = (ok, f"max speed after 1000 steps: wet bed {vmax:.2e} m/s, bed with dry hills {vmax2:.2e} m/s; surface deviation {dev:.2e} m")


def dry_hill():
    ny, nx, dx = 30, 60, 100.0
    y, x = np.mgrid[0:ny, 0:nx]
    b = 50.0 * np.exp(-((x - 35) ** 2 + (y - 15) ** 2) / 30.0)       # a hill taller than the layer
    h = np.maximum(20.0 - b, 0.0)
    h[:, :12] += 6.0 * np.exp(-((x[:, :12] - 6) ** 2) / 10.0)         # a pulse that runs at the hill
    m = swe.Model(b, h, dx, swe.reduced_gravity(6.0), cd=0.003, bc=dict(west="wall", east="wall", north="wall", south="wall"))
    m0 = m.mass()
    hmin = 1e9
    for _ in range(1500):
        m.step()
        hmin = min(hmin, float(m.h.min()))
    rel = abs(m.mass() - m0) / m0
    crest_dry = float(m.h[15, 35])
    ok = hmin >= 0 and rel < 1e-6 and crest_dry < 0.5
    results["2 dry hill"] = (ok, f"min h {hmin:.2e} m, relative mass change {rel:.2e}, depth on the 50 m crest {crest_dry:.3f} m (layer 20 m)")


def dam_break():
    gp, hl, hr, dx = 1.0, 10.0, 2.0, 0.25
    nx = 400
    x = (np.arange(nx) + 0.5) * dx
    h = np.where(x < 50.0, hl, hr)[None, :].repeat(3, axis=0)
    m = swe.Model(np.zeros_like(h), h, dx, gp, bc=dict(west="open", east="open"), periodic_y=True, h_dry=1e-3)
    T = 12.0
    m.run(t_end=T)
    cl, cr_ = np.sqrt(gp * hl), np.sqrt(gp * hr)

    def f(cm):
        um = 2 * (cl - cm)
        hm = cm * cm / gp
        return um - (hm - hr) * np.sqrt(gp * (hm + hr) / (2 * hm * hr))
    cm = brentq(f, cr_, cl)
    hm, um = cm * cm / gp, 2 * (cl - cm)
    S = hm * um / (hm - hr)
    x_true = 50.0 + S * T
    prof = m.h[1]
    mid = 0.5 * (hm + hr)
    idx = np.where(prof > mid)[0]
    x_num = float(x[idx.max()])
    err = abs(x_num - x_true) / (x_true - 50.0)
    results["3 dam break"] = (err < 0.05, f"front at {x_num:.2f} m, analytic {x_true:.2f} m (travelled {x_true - 50:.2f}), error {100 * err:.1f} % of the distance travelled; plateau h {prof[(x > 50 + (S * T) * 0.2) & (x < 50 + S * T * 0.8)].mean():.3f} vs {hm:.3f}")


def ridge():
    """Steady flow over a ridge with a downstream depth that forces a jump: the upstream depth is set by the crest being critical (Bernoulli)."""
    gp, q, zb, dx = 1.0, 1.0, 0.2, 0.1
    nx = 600
    x = (np.arange(nx) + 0.5) * dx
    b = (zb * np.exp(-((x - 30.0) / 3.0) ** 2))[None, :].repeat(3, axis=0)
    hc = (q * q / gp) ** (1 / 3.0)
    E = 1.5 * hc + zb
    h1 = brentq(lambda h: q * q / (2 * gp * h * h) + h - E, hc * 1.0001, 5.0)    # subcritical root
    hd = 1.45                                                                     # downstream depth: above the supercritical branch, so a jump forms
    m = swe.Model(b, np.full_like(b, h1), dx, gp, bc=dict(west=("inflow", h1, q / h1, 0.0), east=("depth", hd)), periodic_y=True, h_dry=1e-3)
    m.hu[:] = q
    m.run(t_end=300.0)
    h = m.h[1]
    hu = m.hu[1]
    up = float(h[(x > 10) & (x < 20)].mean())
    crest = int(np.argmax(b[1]))
    fr = np.abs(hu) / np.maximum(h, 1e-6) / np.sqrt(gp * np.maximum(h, 1e-6))
    err = abs(up - h1) / h1
    jump = bool(fr[(x > 31) & (x < 45)].max() > 1.0) and bool(fr[x > 50].mean() < 1.0)
    results["4 flow over a ridge"] = (err < 0.10 and jump and float(fr[(x > 10) & (x < 20)].max()) < 1.0,
                                      f"upstream depth {up:.3f} m vs 1D hydraulic result {h1:.3f} m (error {100 * err:.1f} %); upstream Froude max {float(fr[(x > 10) & (x < 20)].max()):.2f}; "
                                      f"supercritical lee {bool(fr[(x > 31) & (x < 45)].max() > 1.0)} then subcritical downstream {bool(fr[x > 50].mean() < 1.0)}; max Froude {float(fr.max()):.2f}")


def isotropy():
    """Extra check (not in the guide): the same dam break along x and along y gives identical depth profiles, and a forced flat flow stays straight."""
    gp, hl, hr, dx, n = 1.0, 10.0, 2.0, 0.25, 200
    x = (np.arange(n) + 0.5) * dx
    mx = swe.Model(np.zeros((40, n)), np.where(x < 25, hl, hr)[None, :].repeat(40, axis=0), dx, gp, bc=dict(west="open", east="open"), h_dry=1e-3)
    my = swe.Model(np.zeros((n, 40)), np.where(x < 25, hl, hr)[:, None].repeat(40, axis=1), dx, gp, bc=dict(north="open", south="open"), h_dry=1e-3)
    mx.run(t_end=6)
    my.run(t_end=6)
    d = float(np.abs(mx.h[20, :] - my.h[:, 20]).max())
    f = swe.Model(np.zeros((40, 80)), np.full((40, 80), 300.0), 400.0, 0.2, cd=0.0015, bc=dict(west=("inflow", 300.0, 7.0, 0.0), east="open", north="open", south="open"), forcing=(0.0015 * 49 / 300, 0.0))
    f.hu[:] = 2100.0
    f.run(steps=300)
    u, v = f.velocity()
    results["5 isotropy (extra)"] = (d < 1e-9 and float(np.abs(v).max()) < 1e-9, f"x versus y dam break max depth difference {d:.1e} m; forced flat flow mean u {u.mean():.2f} m/s, max |v| {float(np.abs(v).max()):.1e}")


if __name__ == "__main__":
    t0 = time.time()
    for fn in (lake_at_rest, dry_hill, dam_break, ridge, isotropy):
        fn()
    for k, (ok, msg) in results.items():
        print(("PASS" if ok else "FAIL"), k, "-", msg)
    print(f"{time.time() - t0:.1f} s")
    sys.exit(0 if all(ok for ok, _ in results.values()) else 1)
