"""Guide 11 spike: one-layer reduced-gravity shallow-water model of the marine layer (experiment only; nothing in the live build or viewer uses it).

Method (Audusse, Bouchut, Bristeau, Klein, Perthame 2004, "A fast and stable well-balanced scheme with hydrostatic reconstruction for shallow water flows"),
steps per time step, dimension by dimension on a regular grid with cell size dx:
  1. For every cell face between cells i and j, hydrostatic reconstruction with the bed step: b* = max(b_i, b_j); h_i* = max(0, h_i + b_i - b*), h_j* = max(0, h_j + b_j - b*);
     the velocity of each side is kept (u_i = hu_i / h_i, 0 where dry); reconstructed states U_i* = (h_i*, h_i* u_i, h_i* v_i).
  2. HLL numerical flux F(U_i*, U_j*) with wave speeds sL = min(uL - cL, uR - cR), sR = max(uL + cL, uR + cR), c = sqrt(g' h*). Both sides dry: flux 0.
  3. Well-balanced pressure correction added to the flux seen by each cell: the left cell gets F + (0, g'/2 (h_i^2 - h_i*^2)), the right cell gets F + (0, g'/2 (h_j^2 - h_j*^2)) in the normal momentum.
  4. Explicit Euler update of h, hu, hv with the face fluxes; dt = CFL * dx / max(|u| + c), CFL 0.4.
  5. Body forcing (a large-scale pressure-gradient acceleration, m/s^2) added to the momentum of wet cells; quadratic drag -Cd |u| u / h treated implicitly:
     hu_new = hu / (1 + dt Cd |u| / h). Cells with h < H_DRY are dry (zero momentum).
Boundaries: 'wall' (reflective, for tests), 'open' (zero-gradient outflow), and an inflow face with prescribed h, u, v. Coriolis is left out (small at this scale and span).
Constants: g' = g dTheta / Theta (Theta = 288 K); Cd 0.005 over land, 0.0015 over water (the guide's values); H_DRY = 0.5 m (guide) unless a test passes its own."""
import numpy as np

G = 9.81
THETA = 288.0
H_DRY = 0.5
CFL = 0.4


def reduced_gravity(dtheta_k):
    return G * dtheta_k / THETA


def _faces_x(h, hu, hv, b, gp, eps):
    """Fluxes across the x faces between columns j and j+1 for arrays [ny, nx]. Returns the flux for the LEFT cell and for the RIGHT cell (each 3 arrays [ny, nx-1])."""
    hl, hr, bl, br = h[:, :-1], h[:, 1:], b[:, :-1], b[:, 1:]
    ul = np.where(hl > eps, hu[:, :-1] / np.maximum(hl, eps), 0.0)
    vl = np.where(hl > eps, hv[:, :-1] / np.maximum(hl, eps), 0.0)
    ur = np.where(hr > eps, hu[:, 1:] / np.maximum(hr, eps), 0.0)
    vr = np.where(hr > eps, hv[:, 1:] / np.maximum(hr, eps), 0.0)
    bs = np.maximum(bl, br)
    hls, hrs = np.maximum(0.0, hl + bl - bs), np.maximum(0.0, hr + br - bs)
    cl, cr = np.sqrt(gp * hls), np.sqrt(gp * hrs)
    sl, sr = np.minimum(ul - cl, ur - cr), np.maximum(ul + cl, ur + cr)
    f1l, f2l, f3l = hls * ul, hls * ul * ul + 0.5 * gp * hls * hls, hls * ul * vl
    f1r, f2r, f3r = hrs * ur, hrs * ur * ur + 0.5 * gp * hrs * hrs, hrs * ur * vr
    d = np.where(sr - sl > 1e-12, sr - sl, 1.0)
    out = []
    for fl, fr, ql, qr in ((f1l, f1r, hls, hrs), (f2l, f2r, hls * ul, hrs * ur), (f3l, f3r, hls * vl, hrs * vr)):
        hll = (sr * fl - sl * fr + sl * sr * (qr - ql)) / d
        out.append(np.where(sl >= 0, fl, np.where(sr <= 0, fr, hll)))
    dry = (hls <= 1e-12) & (hrs <= 1e-12)   # truly dry on both sides (not the eps of the velocity cut: the pressure correction below must see the same states)
    out = [np.where(dry, 0.0, o) for o in out]
    left = [out[0], out[1] + 0.5 * gp * (hl * hl - hls * hls), out[2]]
    right = [out[0], out[1] + 0.5 * gp * (hr * hr - hrs * hrs), out[2]]
    return left, right


class Model:
    """h, hu, hv, b [ny, nx] float64. bc: dict of side -> 'wall' | 'open' | ('inflow', h, u, v) | ('depth', h) for 'west','east','north','south'."""

    def __init__(self, b, h, dx, gp, cd=0.0, bc=None, forcing=(0.0, 0.0), h_dry=H_DRY, periodic_y=False):
        self.b, self.dx, self.gp, self.h_dry, self.periodic_y = np.asarray(b, float), float(dx), float(gp), float(h_dry), periodic_y
        self.cd = np.broadcast_to(np.asarray(cd, float), self.b.shape).copy()
        self.h = np.asarray(h, float).copy()
        self.hu, self.hv = np.zeros_like(self.h), np.zeros_like(self.h)
        self.bc = dict(west="wall", east="wall", north="wall", south="wall")
        self.bc.update(bc or {})
        self.fx, self.fy = forcing
        self.t = 0.0
        self.steps = 0

    # ---- boundaries by padding one ghost layer
    def _pad(self):
        def pad(a, kind):
            return np.pad(a, 1, mode="edge")
        h, hu, hv, b = (np.pad(a, 1, mode="edge") for a in (self.h, self.hu, self.hv, self.b))
        if self.periodic_y:
            for a in (h, hu, hv, b):
                a[0, :], a[-1, :] = a[-2, :], a[1, :]
        for side, sl, inner in (("west", (slice(None), 0), (slice(None), 1)), ("east", (slice(None), -1), (slice(None), -2)),
                                ("north", (0, slice(None)), (1, slice(None))), ("south", (-1, slice(None)), (-2, slice(None)))):
            if self.periodic_y and side in ("north", "south"):
                continue
            k = self.bc[side]
            if k == "wall":
                h[sl], b[sl] = h[inner], b[inner]
                hu[sl], hv[sl] = hu[inner], hv[inner]
                if side in ("west", "east"):
                    hu[sl] = -hu[inner]
                else:
                    hv[sl] = -hv[inner]
            elif k == "open":
                pass   # edge padding = zero gradient
            elif k[0] == "inflow":
                _, hh, uu, vv = k
                h[sl], hu[sl], hv[sl], b[sl] = hh, hh * uu, hh * vv, b[inner]
            elif k[0] == "depth":
                h[sl], b[sl] = k[1], b[inner]
        return h, hu, hv, b

    def dt(self):
        eps = self.h_dry
        u = np.where(self.h > eps, np.hypot(self.hu, self.hv) / np.maximum(self.h, eps), 0.0)
        c = np.sqrt(self.gp * self.h)
        return CFL * self.dx / max(float((u + c).max()), 1e-6)

    def step(self, dt=None):
        eps = self.h_dry
        dt = self.dt() if dt is None else dt
        h, hu, hv, b = self._pad()
        ny, nx = self.h.shape
        # x faces on the padded array: [ny+2, nx+1]
        (l1, l2, l3), (r1, r2, r3) = _faces_x(h, hu, hv, b, self.gp, eps)
        # y faces: swap roles of u and v by transposing
        (m1, m2, m3), (n1, n2, n3) = _faces_x(h.T, hv.T, hu.T, b.T, self.gp, eps)
        m1, m2, m3, n1, n2, n3 = (a.T for a in (m1, m2, m3, n1, n2, n3))   # [ny+1, nx+2]: m*: flux for the south cell of the face, n*: for the north cell
        sl = (slice(1, -1), slice(None))
        dh = -(l1[sl][:, 1:] - r1[sl][:, :-1]) / self.dx
        dhu = -(l2[sl][:, 1:] - r2[sl][:, :-1]) / self.dx
        dhv = -(l3[sl][:, 1:] - r3[sl][:, :-1]) / self.dx
        sy = (slice(None), slice(1, -1))
        dh += -(m1[sy][1:, :] - n1[sy][:-1, :]) / self.dx
        dhv += -(m2[sy][1:, :] - n2[sy][:-1, :]) / self.dx
        dhu += -(m3[sy][1:, :] - n3[sy][:-1, :]) / self.dx
        self.h = np.maximum(self.h + dt * dh, 0.0)
        self.hu += dt * dhu
        self.hv += dt * dhv
        wet = self.h > eps
        self.hu = np.where(wet, self.hu + dt * self.h * self.fx, 0.0)
        self.hv = np.where(wet, self.hv + dt * self.h * self.fy, 0.0)
        sp = np.where(wet, np.hypot(self.hu, self.hv) / np.maximum(self.h, eps), 0.0)
        fac = 1.0 / (1.0 + dt * self.cd * sp / np.maximum(self.h, eps))
        self.hu, self.hv = self.hu * fac, self.hv * fac
        self.h = np.where(wet, self.h, np.where(self.h > 0, self.h, 0.0))
        self.t += dt
        self.steps += 1
        return dt

    def run(self, t_end=None, steps=None):
        while (t_end is not None and self.t < t_end) or (steps is not None and self.steps < steps):
            if steps is not None and self.steps >= steps:
                break
            self.step()
        return self

    def mass(self):
        return float(self.h.sum() * self.dx * self.dx)

    def velocity(self):
        w = self.h > self.h_dry
        return np.where(w, self.hu / np.maximum(self.h, self.h_dry), 0.0), np.where(w, self.hv / np.maximum(self.h, self.h_dry), 0.0)
