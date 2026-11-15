# Guide 11: marine-layer depth model, spike report (2026-11-15)

Code: `spikes/marine_layer/swe.py` (solver), `run_checks.py` (the four required tests plus one extra), `demo_terrain.py` (cost, sensitivity, picture). Nothing in the live build or viewer uses it.

## Verdict: INCONCLUSIVE (the solver is verified; the go/no-go scoring was NOT run)
- Verified: the solver passes all four required tests and an extra isotropy check; a 2.5 h case runs in 2.4 s (400 m grid) and 9.7 s (the guide's 200 m grid), well under 30 s.
- Not done: the three real fog events and their scoring (CSI against the satellite probability, top error at the Oakland sounding, gap-flow wind at GGBC1 and Fort Point). They need archived weather, soundings and 5-minute satellite scans; this PC cannot reach api.open-meteo.com and the 5-minute store started today.
- The demo on the real terrain, with fixed illustrative forcing, gives layer depths and tops that are too deep (mean 641 m over land, top up to 1114 m, against an inflow depth of 300 m). That says the forcing and drag balance are not calibrated, not that the model fails. A real go/no-go needs the large-scale forcing set from the observed offshore wind, and one setting for all three cases (guide rule: no per-case tuning).
- Recommendation: do not decide yet. Run the three real cases once the data exists (the top-fusion history fetch in `tools/top_fusion_gate/` and the live archive can supply the weather), with the forcing fixed in advance.

## Method (steps from Audusse et al. 2004, listed before coding as the guide asks)
1. Hydrostatic reconstruction at every face: b* = max(b_i, b_j); h_i* = max(0, h_i + b_i - b*); velocities kept; states U_i* = (h_i*, h_i* u_i, h_i* v_i).
2. HLL flux with sL = min(uL - cL, uR - cR), sR = max(uL + cL, uR + cR), c = sqrt(g' h*); both sides dry gives zero flux.
3. Well-balanced pressure correction added to each cell's face flux: g'/2 (h^2 - h*^2) in the normal momentum.
4. Explicit Euler, dt = 0.4 dx / max(|u| + c); dimension by dimension (x faces, then y faces by transposing).
5. Body forcing (pressure-gradient acceleration) and quadratic drag -Cd |u| u / h, implicit: hu_new = hu / (1 + dt Cd |u| / h). Cells with h < 0.5 m have zero momentum (dry).
Boundaries: wall, open (zero gradient), inflow (prescribed h, u, v), fixed depth. Coriolis left out (small at this scale and time span).
Constants and sources: g = 9.81 m/s2, Theta = 288 K (first guess, typical marine layer), g' = g dTheta / Theta (6 K gives 0.20 m/s2); Cd 0.005 over land and 0.0015 over water (the guide's values); H_DRY 0.5 m (the guide); CFL 0.4 (the guide). Demo forcing: acceleration = 0.0015 u^2 / h_in so that open-ocean drag is balanced (first guess).

## The four required tests (`python spikes/marine_layer/run_checks.py`)
1. Lake at rest, 1000 steps over bumpy terrain: max speed 9e-16 m/s on a wet bed and 7e-16 m/s with dry hills (target 1e-6). A first version of the code failed the dry-hills variant (5e-4 m/s) because the dry-face test used the 0.5 m velocity cutoff instead of true dryness; fixed.
2. Dry hill (50 m hill, 20 m layer, a pulse running at it, walls, 1500 steps): minimum depth 0, relative mass change 0 (target 1e-6), depth on the crest 0.
3. Dam break on a flat bed (h 10 m / 2 m, g' = 1): front at 85.88 m against 85.98 m analytic, error 0.3 % of the distance travelled (target 5 %); plateau depth 5.076 m against 5.079 m.
4. Flow over a low ridge (q = 1, bump 0.2 m, downstream depth 1.45 m): upstream depth 1.468 m against 1.468 m from the 1D hydraulic (Bernoulli, critical crest) result, error 0.0 % (target 10 %); subcritical upstream (max Froude 0.56), supercritical lee (max 1.29) then subcritical downstream, so a jump forms.
5. Extra: the same dam break along x and along y give identical profiles; a forced flat flow stays straight (max |v| 0).

## Cost
400 m grid (100 x 87 cells), 2.5 h of model time: 2.4 s, 1209 steps. 200 m grid (200 x 175, the guide's): 9.7 s, 2269 steps. In the Action, numpy on one core is probably 1.5 to 2 times slower: about 20 s for the 200 m case (estimate, not measured).

## Sensitivity (400 m, 2.5 h, illustrative forcing; these are NOT scored)
| change | mean depth over land | max top |
|---|---|---|
| base (dTheta 6 K, offshore h 300 m) | 641 m | 1114 m |
| dTheta 4 K | 495 m | 906 m |
| dTheta 8 K | 700 m | 1291 m |
| offshore h 250 m | 613 m | 1112 m |
| offshore h 350 m | 642 m | 1098 m |
The answer moves sensibly and does not flip with dTheta of plus or minus 2 K (a change of about 100 to 150 m in mean depth), and hardly remembers the offshore depth (plus or minus 50 m moves the mean depth by under 30 m). Whether that is right needs the real cases.

## Pictures
`C:\Users\George\deathfog-data\scratch\marine_layer_demo.png`: depth map with flow arrows and the layer top (demo forcing, uncalibrated). The flow turns south in the lee of the hills because the layer piles up against the terrain; there is no Coriolis. A satellite picture beside it needs real cases.

## If it is a go (not part of this spike)
As in the guide: `b + h` as a spatial fog top in guides 03 and 05, and a blocking factor on the tracer where terrain rises above the layer. Needs its own guide and George's approval.
