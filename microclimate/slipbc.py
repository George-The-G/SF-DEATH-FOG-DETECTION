"""Free-slip (specular reflection) floor for XLB: a missing population is replaced by the mirror image (z component of the lattice
velocity flipped) of the node's own outgoing population. XLB 0.3.2 has no specular BC. Meant for flat horizontal floors (open sea)."""
from typing import Any

import numpy as np
import warp as wp

from xlb.operator.boundary_condition import HalfwayBounceBackBC


class SlipFloorBC(HalfwayBounceBackBC):
    def __init__(self, indices=None):
        super().__init__(indices=indices)

    def _construct_warp(self):
        c = np.array(self.velocity_set._c)
        q = c.shape[1]
        mir = []
        for l in range(q):
            t = (c[0, l], c[1, l], -c[2, l])
            mir.append(next(m for m in range(q) if (c[0, m], c[1, m], c[2, m]) == t))
        _mir = wp.constant(wp.types.vector(length=q, dtype=wp.int32)(mir))
        _q = q
        _opp = self.velocity_set.opp_indices

        @wp.func
        def functional(index: Any, timestep: Any, missing_mask: Any, f_0: Any, f_1: Any, f_pre: Any, f_post: Any):
            _f = f_post
            for l in range(_q):
                if missing_mask[l] == wp.uint8(1):
                    if missing_mask[_opp[l]] == wp.uint8(1):
                        _f[l] = f_pre[_opp[l]]  # a solid floor node itself (both sides missing): plain bounce-back keeps it inert
                    else:
                        _f[l] = f_pre[_mir[l]]
            return _f

        kernel = self._construct_kernel(functional)
        return functional, kernel
