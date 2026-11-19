# Guide 11: marine-layer depth model: a time-boxed spike with a go/no-go

Read `00_README.md` first. Needs guides 06 and 07 for inputs and scoring. PC, CPU only. **This is an experiment, not a feature: nothing in the live build or the viewer changes. Time box: 5 working days. The deliverable is a report with numbers and a recommendation.**

## Why a spike
The single biggest physics gap left after guides 01-08 is that the wind fields have no inversion, so the tracer lets fog climb over hills too easily, and the fog top is one number for the whole city. A one-layer "reduced-gravity shallow-water" model treats the marine layer as a sheet of cool air of depth `h(x, y)` under a lid. It is the standard simple model for flow through coastal gaps and can produce a top that varies in space and a flow that respects it. It is also easy to get subtly wrong (dry cells where hills rise above the layer, shocks behind ridges), so we find out cheaply whether it earns a place.

## The model (keep it this small)
- Grid: 40 x 35 km at 200 m (200 x 175), covering the Gate, Marin Headlands, the San Bruno gap and the bay to Oakland. Terrain `b(x, y)` from the 3DEP tiles of guide 09, block-averaged, sea = 0.
- Unknowns: layer depth `h >= 0` (so the top is at `b + h`) and depth-averaged velocity `(u, v)`.
- Equations: mass `dh/dt + div(h u) = 0`; momentum with reduced gravity `g' = g * dTheta / Theta` (`dTheta` = inversion strength, 5-10 K typical, so `g'` is about 0.15-0.35 m/s^2), a large-scale pressure-gradient forcing that would drive the observed open-ocean wind, and quadratic drag `-Cd |u| u / h` with `Cd` about 0.005 over land, 0.0015 over water.
- **Numerics: use a published well-balanced, positivity-preserving finite-volume scheme, do not invent one.** Recommended: first-order HLL fluxes with hydrostatic reconstruction (Audusse et al. 2004), explicit Euler steps with CFL 0.4, drag treated implicitly, cells with `h < 0.5 m` treated as dry (zero velocity). Vectorised numpy. Before writing code, read the method and list its steps in the report.
- Boundaries: at inflow edges prescribe `h` (the fused fog top over the ocean) and the open-ocean wind; elsewhere zero-gradient outflow.
- Run to a quasi-steady state for a fixed forcing (about 2-3 h of model time), not as a live time-stepper.

## Tests that must pass before any real case (in a scratch folder, not `tests_mc`)
1. **Lake at rest:** `b + h` constant over bumpy terrain with zero wind stays at rest to 1e-6 m/s for 1000 steps (this is what "well-balanced" means; if it fails, nothing else can be trusted).
2. **Dry hill:** a hill taller than the layer stays dry, no negative `h`, mass conserved to 1e-6 relative.
3. **Dam break** on a flat bed against the analytic solution: front position within 5 %.
4. **Flow over a low ridge** in a channel: subcritical upstream, a jump downstream, and the upstream depth matches the classic one-dimensional hydraulic result within 10 %.

## Real cases
Three fog events with different character from the archive or hindcasts (a deep push, a shallow layer that stops at the hills, a Gate-only tongue). For each: inputs from the sounding and guide 06 (`h` offshore, `dTheta`), offshore wind from buoy 46026 or the model.

## Scoring (the go/no-go)
1. **Where the fog is:** the model's "layer present and deeper than terrain allows fog" mask against the guide 07 satellite low-cloud probability at 2 km: CSI, against the same score for today's tracer-based sheet on the same hours.
2. **Top height:** `b + h` at the Oakland sounding location against its inversion base; and against airport ceiling + 250 m as a weak check.
3. **Gap flow:** wind speed at the Golden Gate Bridge station (GGBC1) and at Fort Point against the model, compared with the XLB-based station fit.
4. **Cost:** seconds per case on the PC and an estimate for the Action.
5. **Sensitivity:** change `dTheta` by 2 K and the offshore `h` by 50 m: does the answer change sensibly or flip?

**Go** if (1) beats the tracer sheet by at least 0.05 CSI on two of three cases without losing on the third, (2) the top error is under 100 m, and a case runs in under 30 s. **No-go** otherwise. Either way, write what was learned.

## If it is a go (not part of this spike)
The follow-up guide would use `b + h` as a spatial fog top in guides 03 and 05 (band weights per column instead of one top), and scale the tracer by a blocking factor where terrain rises above the layer. That needs its own guide and George's approval.

## Report
Method steps, the four test results, three case pictures (depth map, flow arrows, satellite beside it), the score table, cost, sensitivity, recommendation. State every constant and where it came from.

## Pitfalls
- A scheme that is not well-balanced creates fake winds over every hill. Test 1 first.
- Coriolis is small at this scale and time span: leave it out and say so.
- Do not tune `Cd` or the forcing per case to win the score; use one setting for all three.
