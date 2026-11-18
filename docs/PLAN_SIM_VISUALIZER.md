# Plan: Simulation visualizer (voxels and wind vectors) -- saved for later, nothing built yet

Goal: see how the solver grids line up with the real city, to debug sub-cell buildings and the 20 m / 5 m tiers, and later to show wind.

## Where it lives
A "Simulation visualizer" section in the viewer settings (`site/v3`, beside the existing layers), off by default, loaded on demand so it adds nothing to the normal page weight.

## Section 1: Voxel grids (toggles)
| Toggle | Source | Notes |
|---|---|---|
| 20 m solid blocks | `grids/solid_20m.npz` (855 x 811 x 40) | export only solid cells that touch fluid (the shell); instanced thin-line boxes |
| 20 m cell lattice | `grid_spec.json` | wireframe lattice planes in the view, fading with distance, so the cell size is visible |
| 5 m street tile(s) | the 5 m spike geometry (`spikes/street5m`, 560 x 560 x 60) | drawn only where a tile exists; smaller boxes appear as the camera nears the surface |
| 10 m derived maps | `derive.py` output (6 levels above ground) | optional, as flat level planes |
| Real buildings and terrain | existing `buildings.bin`, `terrain.glb` | always the reference; the voxels overlay them |

Tier look: 20 m boxes in thin pale lines; as the camera gets within about 400 m of the surface, the 5 m boxes fade in inside the same space (level of detail by camera distance). Colour tells the tier (20 m cool, 5 m warm). A "fill" toggle shades boxes by mean building height so filled-in sub-cell buildings stand out; a "mismatch" toggle highlights 20 m cells whose mean building height is above 2 m but whose footprint share is small.

## Section 2: Wind vectors (later)
Direction picker (the library directions), height-above-ground slider, spacing (every n cells). One arrow per sample: direction = flow, length and colour = speed ratio. No particles. Sources: `library/wind_<dir>.npz` (20 m) and the 5 m field where it exists. Arrows are instanced from a packed typed array.

## Data export (one script, offline on the PC)
`tools/export_voxels.py` writes `voxels_20m.bin` (shell cell indices, uint16 triples, plus a small header) and `voxels_5m_<tile>.bin`. Estimated size: the 20 m shell is a few hundred thousand cells, about 1 to 2 MB packed; 5 m tiles about 1 MB each. Hosted next to the other viewer data, fetched only when the section is opened.

## Steps
1. Export script and size check. 2. Instanced line-box renderer and the 20 m toggle over the real buildings. 3. Distance-based tier swap for 5 m. 4. Mismatch and fill shading. 5. Wind arrows (needs the recomputed library). 6. Phone check: the panel must work at 375 px wide and the layers must stay under a frame budget (cap instance counts).

## Open questions
Which 5 m tiles exist (only 20th and Kansas so far). Whether arrows should be per-direction files or sampled from the live blend. Where the settings section sits relative to the existing layer controls.
