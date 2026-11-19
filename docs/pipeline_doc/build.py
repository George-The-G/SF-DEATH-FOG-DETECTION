"""Builds fog-pipeline.html: one self-contained page (images inlined as base64 WebP, charts as inline SVG).
Run from this folder: python3 build.py  ->  ../fog-pipeline.html
Inputs: assets/*.webp (made by gen.py on the PC), facts.json, beta_snapshot/*.json."""
import base64, html, json, math, pathlib

HERE = pathlib.Path(__file__).parent
A = HERE / "assets"
F = json.loads((HERE / "facts.json").read_text())
W = json.loads((HERE / "beta_snapshot/weather.json").read_text())
S = json.loads((HERE / "beta_snapshot/status.json").read_text())
CS = json.loads((HERE / "beta_snapshot/cloud_score.json").read_text())
OUT = HERE.parent / "fog-pipeline.html"
BOX = "#ff2d55"


def img(name, alt, cap, wide=False):
    b = base64.b64encode((A / f"{name}.webp").read_bytes()).decode()
    cls = "fig wide" if wide else "fig"
    return (f'<figure class="{cls}" id="fig-{name}"><img src="data:image/webp;base64,{b}" alt="{html.escape(alt)}">'
            f"<figcaption>{cap}</figcaption></figure>")


def esc(s):
    return html.escape(str(s))


# ---------------------------------------------------------------- charts (inline SVG, colours from CSS tokens)
N = len(W["t"])
OBS = 8   # observed_until_frame in index.json


def hour_label(i):
    t = W["t"][i]
    return t[11:13]


def chart(title, panels, height=150, unit_note=""):
    """panels: list of dict(label, ymin, ymax, series=[(values, cls, kind, name)], band=(lo,hi,cls) optional, ticks=[...])"""
    w, padl, padr, padt = 760, 46, 12, 18
    ph = height
    H = padt + len(panels) * (ph + 26) + 22
    x = lambda i: padl + (w - padl - padr) * i / (N - 1)
    out = [f'<svg class="chart" viewBox="0 0 {w} {H}" role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>']
    for pi, p in enumerate(panels):
        y0 = padt + pi * (ph + 26)
        lo, hi = p["ymin"], p["ymax"]
        y = lambda v, y0=y0, lo=lo, hi=hi: y0 + ph - (ph * (min(max(v, lo), hi) - lo) / (hi - lo))
        # observed shade
        out.append(f'<rect class="obs" x="{padl}" y="{y0}" width="{x(OBS) - padl:.1f}" height="{ph}"/>')
        for tv in p["ticks"]:
            out.append(f'<line class="grid" x1="{padl}" x2="{w - padr}" y1="{y(tv):.1f}" y2="{y(tv):.1f}"/>'
                       f'<text class="tick" x="{padl - 5}" y="{y(tv) + 3.5:.1f}" text-anchor="end">{tv:g}</text>')
        if "band" in p:
            blo, bhi, bcls = p["band"]
            pts = [f"{x(i):.1f},{y(bhi[i]):.1f}" for i in range(N)] + [f"{x(i):.1f},{y(blo[i]):.1f}" for i in reversed(range(N))]
            out.append(f'<polygon class="{bcls}" points="{" ".join(pts)}"/>')
        for vals, cls, kind, name in p["series"]:
            if kind == "bar":
                bw = (w - padl - padr) / N * 0.7
                for i, v in enumerate(vals):
                    if v > 0:
                        out.append(f'<rect class="{cls}" x="{x(i) - bw / 2:.1f}" y="{y(v):.1f}" width="{bw:.1f}" height="{y(lo) - y(v):.1f}"/>')
            else:
                d = " ".join(("M" if i == 0 else "L") + f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
                out.append(f'<path class="{cls}" d="{d}"/>')
        out.append(f'<text class="plab" x="{padl + 4}" y="{y0 - 5}">{p["label"]}</text>')
        out.append(f'<line class="now" x1="{x(OBS):.1f}" x2="{x(OBS):.1f}" y1="{y0}" y2="{y0 + ph}"/>')
    # x axis: every 12 h
    yb = padt + len(panels) * (ph + 26) - 20
    for i in range(0, N, 6):
        t = W["t"][i]
        lab = t[11:13] + ":00" if i % 24 else t[5:10].replace("-", "/") + " " + t[11:13] + "h"
        out.append(f'<text class="tick" x="{x(i):.1f}" y="{yb + 12}" text-anchor="middle">{lab}</text>')
    out.append(f'<text class="tick em" x="{x(OBS) + 4:.1f}" y="{padt + 10}">forecast &#8594;</text>')
    out.append(f'<text class="tick em" x="{x(OBS) - 4:.1f}" y="{padt + 10}" text-anchor="end">&#8592; observed</text>')
    out.append("</svg>")
    return "".join(out)


def legend(items):
    return '<p class="legend">' + "".join(f'<span><i class="sw {c}"></i>{esc(n)}</span>' for c, n in items) + "</p>"


T, Td = W["temp_c"], W["dewpoint_c"]
svg_forcing = chart("Beta forcing, 17 to 20 Nov", [
    dict(label="Air temperature and dew point (°C)", ymin=4, ymax=18, ticks=[5, 10, 15],
         series=[(T, "l1", "line", "T"), (Td, "l2", "line", "Td")]),
    dict(label="RRFS low cloud (0–1) and fog source s(t) used (0–1)", ymin=0, ymax=1, ticks=[0, 0.5, 1],
         series=[([v / 100 for v in W["cloud_low_pct"]], "l3", "line", "low cloud"), (W["fog_source"], "l1 thick", "line", "s(t)")]),
    dict(label="Wind speed (m/s), nudged", ymin=0, ymax=12, ticks=[0, 5, 10],
         series=[(W["wind_ms"], "l2", "line", "wind")]),
], height=110)
lcl = [125 * max(a - b, 0) for a, b in zip(T, Td)]
top, sg = W["fog_top_m"], W["fog_top_sigma_m"]
svg_top = chart("Fused fog top", [
    dict(label="Fog top ± 1 sigma (m above sea level) and 125·(T−Td) cloud-base estimate (m above ground)", ymin=0, ymax=1400,
         ticks=[0, 500, 1000], band=([t - s for t, s in zip(top, sg)], [t + s for t, s in zip(top, sg)], "bandfill"),
         series=[(top, "l1 thick", "line", "top"), (lcl, "l3", "line", "base")]),
], height=170)
wet = F["wet"]
svg_wet = chart("Wetness index over the city", [
    dict(label="Wetness index W over 92,429 street points: mean and 90th percentile (0–100)", ymin=0, ymax=100, ticks=[0, 50, 100],
         series=[(wet["p90"], "l3", "line", "p90"), (wet["mean"], "l1 thick", "line", "mean")]),
    dict(label="Precipitation (mm/h)", ymin=0, ymax=6, ticks=[0, 3, 6],
         series=[(W["precip_mm"], "barfill", "bar", "rain")]),
], height=110)

# ---------------------------------------------------------------- stage blocks


def vtable(rows):
    h = ['<div class="tw"><table class="vars"><thead><tr><th>Variable</th><th>Where</th><th>Value</th><th>What it does</th><th>Cloud menu</th></tr></thead><tbody>']
    for name, where, val, eff, menu in rows:
        m = f'<span class="menu">{menu}</span>' if menu else '<span class="dim">none</span>'
        h.append(f"<tr><td><code>{name}</code></td><td><code>{where}</code></td><td>{val}</td><td>{eff}</td><td>{m}</td></tr>")
    h.append("</tbody></table></div>")
    return "".join(h)


def third(items):
    li = "".join(f'<li><a href="{u}" target="_blank" rel="noopener">{esc(n)}</a>: {note}</li>' for n, u, note in items)
    return f'<details class="third"><summary>Third-party data and technology ({len(items)})</summary><ul>{li}</ul></details>'


def stage(sid, title, where, runs, what, inputs, outputs, vars_, tp, extra=""):
    io = ""
    if inputs:
        io += '<h4 class="io in">Input</h4>' + "".join(inputs)
    if outputs:
        io += '<h4 class="io out">Output</h4>' + "".join(outputs)
    return (f'<section class="stage" id="{sid.lower()}" data-stage="{sid}"><header><span class="sid">{sid}</span>'
            f'<h3>{title}</h3><p class="where"><code>{where}</code> · {runs}</p></header>'
            f'<div class="what">{what}</div>{io}{extra}<h4 class="io">Variables you control</h4>{vtable(vars_)}{third(tp)}</section>')


L = dict(  # third-party links
    xlb=("XLB (Autodesk)", "https://github.com/Autodesk/XLB", "the lattice Boltzmann wind solver, version 0.3.2"),
    warp=("NVIDIA Warp", "https://github.com/NVIDIA/warp", "Python GPU kernels; XLB runs on it, and so does the fog tracer"),
    lbm=("Lattice Boltzmann method", "https://en.wikipedia.org/wiki/Lattice_Boltzmann_methods", "background on the D3Q19 scheme"),
    smag=("Smagorinsky model", "https://en.wikipedia.org/wiki/Large_eddy_simulation#Smagorinsky%E2%80%93Lilly_model", "the subgrid turbulence closure"),
    dep=("USGS 3DEP", "https://www.usgs.gov/3d-elevation-program", "terrain elevation"),
    datasf=("DataSF Building Footprints", "https://data.sfgov.org/", "about 177,000 building outlines with lidar roof heights"),
    osm=("OpenStreetMap via Overpass", "https://www.openstreetmap.org/copyright", "streets and surface tags (the street points the wetness runs on)"),
    om=("Open-Meteo", "https://open-meteo.com/", "serves the RRFS forecast on the 6 × 5 city lattice"),
    rrfs=("NOAA RRFS", "https://registry.opendata.aws/noaa-rrfs/", "the forecast model behind Open-Meteo (ncep_rrfs_conus)"),
    goes=("NOAA GOES-18 on AWS", "https://registry.opendata.aws/noaa-goes/", "ABI-L2-ACMC clear-sky mask and ABI-L2-ACHAC cloud-top height"),
    coops=("NOAA CO-OPS", "https://tidesandcurrents.noaa.gov/", "station 9414290: observed wind and air temperature"),
    awc=("Aviation Weather Center API", "https://aviationweather.gov/data/api/", "METARs from KHAF, KSFO and KOAK: T, Td, visibility, ceilings"),
    nws=("NWS API", "https://www.weather.gov/documentation/services-web-api", "SFOC1 downtown and other stations"),
    spc=("SPC soundings", "https://www.spc.noaa.gov/exper/soundings/", "Oakland balloon sounding: the marine inversion base"),
    iem=("Iowa Environmental Mesonet", "https://mesonet.agron.iastate.edu/", "RAWS and other station reports for the anchors"),
    ndbc=("NOAA NDBC", "https://www.ndbc.noaa.gov/", "buoys 46026 and 46237 upwind of the Gate"),
    pvlib=("pvlib", "https://pvlib-python.readthedocs.io/", "sun position and clear-sky irradiance"),
    numpy=("NumPy", "https://numpy.org/", "all array work"),
    scipy=("SciPy", "https://scipy.org/", "smoothing, interpolation, image filters"),
    pillow=("Pillow", "https://python-pillow.org/", "PNG encoding of tiles, atlases and macro frames"),
    pyproj=("pyproj", "https://pyproj4.github.io/pyproj/", "UTM 10N ↔ lon/lat and the GOES fixed-grid projection"),
    h5=("h5py / netCDF4", "https://www.h5py.org/", "reading the GOES NetCDF files"),
    req=("Requests", "https://requests.readthedocs.io/", "every HTTP fetch in the live build"),
    kalman=("Kalman filter", "https://en.wikipedia.org/wiki/Kalman_filter", "the top-fusion forward pass"),
    rts=("Rauch–Tung–Striebel smoother", "https://en.wikipedia.org/wiki/Kalman_filter#Rauch%E2%80%93Tung%E2%80%93Striebel", "the backward pass of the top fusion"),
    audusse=("Audusse et al. 2004", "https://doi.org/10.1137/S1064827503431090", "hydrostatic reconstruction for shallow water over terrain"),
    gha=("GitHub Actions", "https://docs.github.com/actions", "runs the live build (collect.yml) about every 30 minutes"),
    ghp=("GitHub Pages", "https://pages.github.com/", "hosts the public and beta sites"),
    gh_rel=("GitHub Releases", "https://docs.github.com/repositories/releasing-projects-on-github", "carries the npz library assets the build downloads"),
    three=("three.js 0.160.0", "https://threejs.org/", "WebGL drawing, loaded from jsDelivr"),
    jsd=("jsDelivr", "https://www.jsdelivr.com/", "the CDN that serves three.js"),
    webgl=("WebGL 2", "https://registry.khronos.org/webgl/specs/latest/2.0/", "3D textures for the voxel fog"),
)
tp = lambda *k: [L[x] for x in k]

# ---------------------------------------------------------------- content
g = F["geometry"]; w27 = F["wind2700"]; rf = F["refine"]; fz = S["forcing"]; tf = fz["top_fusion"]
h24 = CS["summary"]["h24"]["rrfs"]

stages = []

# ====== A: offline
stages.append(stage("A1", "City geometry: terrain and buildings on a 20 m grid", "microclimate/geometry.py → solid_20m.npz", "offline, PC, once",
    f"""<p>Terrain from USGS 3DEP and building footprints from DataSF are burned into a voxel grid of
    <b>{g['shape'][0]} × {g['shape'][1]} × {g['shape'][2]}</b> cells, 20 m on each side, starting at UTM 10N x0 = 540100, y0 = 4171500 (i = east, j = north,
    cell centres at z = (k + 0.5) · 20 m above sea level). A cell is solid if the ground or a building's mean height reaches it.
    This run: <b>{g['buildings']:,}</b> buildings, highest roof {g['max_source_height_m']:.0f} m, terrain max {g['terrain_max_m']:.0f} m,
    solid fraction <b>{g['solid_fraction']:.3f}</b>.</p>""",
    [img("geom_inputs", "Terrain and building height maps with a red box around Potrero, and a zoomed Potrero panel",
         f"Inputs on the whole domain. The <b style='color:{BOX}'>red box</b> is the Potrero crop shown enlarged on the right; the yellow dashed line is the slice used below.", wide=True)],
    [img("geom_section", "West-east slice of the solid grid through Potrero",
         "Output: <code>solid_20m.npz</code>, the west–east slice through Potrero (grey = solid).", wide=True)],
    [("dx", "microclimate.toml [domain]", "20 m", "cell size of every offline field", ""),
     ("x0, y0, nx, ny, nz", "microclimate.toml [domain]", "540100, 4171500, 855, 811, 40", "extent and height of the grid (800 m tall)", ""),
     ("zone.potrero", "microclimate.toml", "box around Potrero", "the zoom area used for checks and the 2.5 m run", "")],
    tp("dep", "datasf", "osm", "pyproj", "numpy")))

stages.append(stage("A2", "Wind library: 20 m city LBM, 15 directions", "microclimate/wind.py, windrot.py → wind_&lt;dir&gt;.npz → live_static / live_east.npz", "offline, PC GPU (RTX 4080 SUPER)",
    f"""<p>XLB solves the flow over <code>solid_20m</code> with a D3Q19 lattice Boltzmann scheme and a Smagorinsky closure, once per wind direction,
    with a <b>uniform inflow</b> of U_LAT = 0.05 lattice units. Each run is done in a rotated frame (the grid is resampled so the wind blows along x, then the
    field is rotated back), and the result is stored as <b>speed divided by inflow speed</b>, so one run serves every wind speed. The live build later multiplies
    by the forecast wind and blends the two nearest directions.</p>
    <p>Directions: nine westerly (180–360° every 22.5°) and six easterly (22.5–135°). 157.5° is a known gap. Easterly fields are <b>street wind only</b>:
    their tracer rows are zero (status.json: "east tracer rows are zero").</p>
    <p>This direction (270°): {w27['json']['steps']:,} steps in {w27['json']['seconds']} s, max ratio {w27['json']['max_speed_ratio']:.2f}, density 0.92–1.08.
    Near-surface ratio p5/p50/p95 = <b>{w27['near_surface_p5_p50_p95'][0]:.2f} / {w27['near_surface_p5_p50_p95'][1]:.2f} / {w27['near_surface_p5_p50_p95'][2]:.2f}</b>.
    The library made before 16 Nov had <b>{F['wind2700_old_near_surface_p5_p50_p95'][0]:.3f} / {F['wind2700_old_near_surface_p5_p50_p95'][1]:.3f} / {F['wind2700_old_near_surface_p5_p50_p95'][2]:.3f}</b>:
    a stale GPU kernel cache meant it ran with no walls at all.</p>""",
    [img("wind20_dirs", "Six wind directions at street level", "Input of the 20 m tier is only geometry plus a uniform inflow direction. Six of the 15 runs, 2 cells above the local surface; arrows show the inflow.")],
    [img("wind20_map", "Speed ratio map for 270 degrees with a red Potrero box and a zoom with flow vectors",
         f"Output <code>wind_2700.npz</code>, 2 cells above the surface. The <b style='color:{BOX}'>red box</b> is the zoom on the right (vectors every 100 m).", wide=True),
     img("wind20_section", "Vertical slice of wind speed ratio", "Same field, west–east slice through Potrero: the city builds its own slow layer under a uniform inflow.", wide=True),
     img("wind20_old_new", "Before and after the walls fix", "Before 16 Nov (public build, no walls) and after the walls fix (beta build).")],
    [("directions_deg", "microclimate.toml [library]", "202.5…360 (toml); assets carry 180…360 + 22.5…135", "which directions are run", ""),
     ("omega", "wind.run()", "1.95", "relaxation rate; sets the lattice viscosity (closer to 2 = less viscous, less stable)", ""),
     ("U_LAT", "wind.py", "0.05", "inflow speed in lattice units; keeps the Mach number low", ""),
     ("steps / avg_steps", "wind.run()", "40000 / 8000", "spin-up length and the window that is averaged into the stored field", ""),
     ("walls, sea, outlet", "branch wind-cube-check (docs/WIND_SOLVER_CONFIG.md)", "bounce-back walls, free-slip sea, fixed-pressure outlet", "the boundary set used for the beta library (not on v3-dev)", ""),
     ("WIND_EAST", "live/config.py, C.BETA['wind_east']", "False public, True beta", "lets the build use the six east directions for street wind", "")],
    tp("xlb", "warp", "lbm", "smag", "numpy")))

stages.append(stage("A3", "Nested refinement: 5 m patches and a 2.5 m Potrero run", "refine.py (branch v3-refine) → patch_corr_2700.npz, patch_corr_2700_dx2p5.npz", "offline, PC GPU, 270° only",
    f"""<p>297 patches of the city are re-run at 5 m with real building shapes. Each patch takes its <b>boundary from the 20 m field</b> (one-way nesting with a sponge),
    and the result is a correction <code>corr_bldg</code> = street speed with buildings ÷ without. This run: {rf['patches']} patches, {rf['cells']:,} street cells,
    corr p5/p50/p95 = <b>{rf['corr_bldg_p5_p50_p95'][0]:.2f} / {rf['corr_bldg_p5_p50_p95'][1]:.2f} / {rf['corr_bldg_p5_p50_p95'][2]:.2f}</b>.
    A 2.5 m Potrero patch exists and wins where it has a value. The wetness uses <code>rel_wind = clip(corr / 0.91, 0.25, 1)</code>.</p>
    <p>Guide 12's <code>street5m_v2</code> spike is a separate, <b>un-nested</b> 5 m run with uniform inflow (go on the physics, with conditions).
    Note: the corrections were computed on the no-walls 20 m field, so they are stale for the beta library (see Disagreements).</p>""",
    [],
    [img("refine_map", "Map of 297 nested patches and a 2.5 m Potrero zoom",
         f"Output: the 297 nested 5 m patches (black outlines). The <b style='color:{BOX}'>red box</b> on the left is the 2.5 m Potrero run on the right.", wide=True),
     img("street5m_v2", "Three street-level speed maps", "Guide 12 spike (street5m_v2): 5 m with walls at 7.5 m and 25 m above ground, against the 20 m walls library at 7.5 m.", wide=True)],
    [("fine dx", "refine.py", "5 m (Potrero 2.5 m)", "cell size inside each patch", ""),
     ("sponge width", "refine.py", "a few coarse cells", "absorbs the mismatch where the 20 m boundary meets the 5 m buildings", ""),
     ("USE_REFINED", "wetness.py", "True", "use corr_bldg in the street wind", ""),
     ("REFINE_NORM", "wetness.py", "0.91", "divides corr so the city median maps near 1", "")],
    tp("xlb", "warp", "numpy")))

stages.append(stage("A4", "Fog tracer: 11 inflow bands per direction", "microclimate/fog.py → fog_&lt;dir&gt;_&lt;band&gt;.npz → live_tracer3d.npz", "offline, PC GPU",
    f"""<p>For each direction and each <b>inflow height band</b>, a steady advection–diffusion tracer is run through the wind field: concentration 1 enters at the
    west edge in that band, ground cells lose it by deposition. The live build later decides how much fog is in each band (from the fog top) and adds the bands up.
    Band edges (m): {', '.join(str(int(e)) for e in F['asset']['band_edges'])}. The old four bands are exact sums: 0–2, 3–5, 6–8, 9–10.</p>
    <p>270°, three bands: about 11,000 steps of 1 s, 17 s each, drift over the last 2000 steps ≤ 0.006.</p>""",
    [],
    [img("tracer_bands", "Tracer concentration maps and slices for three bands", "Output for 270°: top row near the ground, bottom row the west–east slice. Low fog hugs the west and is eaten by the hills; the 500–640 m band rides over the city.", wide=True)],
    [("fog_bands3d_m", "microclimate.toml [library]", "11 bands, 0–800 m", "the height layers fog can enter in", ""),
     ("eddy_diffusivity_m2s (kd)", "microclimate.toml / spike scripts", "2.0 in toml; <b>20</b> in the beta assets", "sideways spreading", ""),
     ("kdv", "fog.run_band()", "toml: none (= kd); beta assets: <b>8</b>", "up–down mixing; small keeps a layer under its inversion", ""),
     ("deposition_velocity_ms", "microclimate.toml", "0.02 m/s", "how fast the ground eats fog", ""),
     ("ref_speed_ms", "microclimate.toml", "5 m/s", "the speed the tracer is solved at", ""),
     ("transits", "fog.run_band()", "3 (more if not converged)", "how many domain crossings are simulated", ""),
     ("fog_loss_per_km", "microclimate.toml", "0.0", "evaporation along the path (off)", "")],
    tp("warp", "numpy")))

stages.append(stage("A5", "Asset packing: the npz files the live build downloads", "microclimate/city.py, live/tracer3d.py, spikes/patch_static.py → live_static.npz, live_tracer3d.npz, live_east.npz (release assets)", "offline, uploaded to GitHub Releases",
    f"""<p>The offline fields are sampled to what the build needs: the 80 m tile tracer <code>tr80</code>, the street-point tracer and wind at
    <b>{F['asset']['points']:,}</b> points, and a 3D tracer of shape {tuple(F['asset']['tr3d_shape'])} (directions × bands × 24 levels of 25 m × 203 × 214).
    Beta builds copy in the <code>*_walls.npz</code> assets (refresh.sh); the public build still uses the old ones.</p>
    <p>Point tracer at 270°: p50 {F['asset']['tracer_p50_p95_270'][0]:.3f}, p95 {F['asset']['tracer_p50_p95_270'][1]:.3f}.</p>""",
    [],
    [img("asset_static", "tr80 and the street-point tracer", "Output <code>live_static.npz</code>: the 80 m tile tracer and the point tracer (√(tracer / 0.03)).", wide=True),
     img("asset_tracer3d", "Four tracer3d panels", "Output <code>live_tracer3d.npz</code> at 270°, bands 0–2, at three levels, and the urban share used by the (off) urban lift.", wide=True)],
    [("AIR_MIN", "live/tracer3d.py", "0.8", "an 80 m cell with less open air than this (of 16 sub-cells) is set to 0", ""),
     ("BLDG_H", "live/tracer3d.py", "2 m", "a 20 m cell counts as built-up above this mean building height", ""),
     ("URBAN_W_BLDG / _ROAD", "live/tracer3d.py", "0.7 / 0.3", "building and road weights of the urban share", ""),
     ("asset choice", "refresh.sh", "*_walls.npz for beta", "which library a build downloads", "")],
    tp("numpy", "gh_rel")))

stages.append(stage("A6", "Marine-layer shallow-water spike (research)", "spike, guide 11", "offline, not used live",
    "<p>A 400 m shallow-water model of the marine layer over terrain (hydrostatic reconstruction). Forcing was illustrative and the result was inconclusive, so nothing live uses it.</p>",
    [], [img("marine_demo", "Layer depth and layer top after 2.5 h", "Layer depth with flow arrows and layer top after 2.5 h (illustrative forcing).")],
    [("grid", "guide 11 spike", "400 m", "cell size", "")],
    tp("audusse", "numpy")))

# ====== B: live build
srcs = S["data_as_of"]
stages.append(stage("B1", "Gather: every live input", "live/build.py, fetch.py, stations.py", "GitHub Action, every ~30 min",
    f"""<p>One run pulls the RRFS forecast on a 6 × 5 lattice (37.70–37.83 N, 122.52–122.35 W) from Open-Meteo, plus CO-OPS, METARs, NWS, the Oakland sounding, 21 stations and GOES.
    Lattice points west of x = 545000 form the <b>coast series</b> s(t). Snapshot run {S['generated_iso'][:19]} Z: sources used
    {', '.join(S['sources_used'])}; none failed; {srcs['stations']['reporting']} of {srcs['stations']['requests']} stations reporting;
    sounding valid {srcs['sounding']['valid'][:16]} Z with {srcs['sounding']['levels']} levels.</p>""",
    [img("inputs_map", "Map of all live inputs over the GOES box with a zoom",
         f"Every live input. The <b style='color:{BOX}'>red box</b> on the left is the zoom on the right; black is the 20 m wind and fog domain (17.1 × 16.2 km). Orange squares are the coast lattice points.", wide=True)],
    [],
    [("LAT / LON lattice", "live/config.py", "5 × 6 points", "where the model forecast is sampled", ""),
     ("COAST_X_MAX", "live/config.py", "545000 m", "lattice points west of this make the coast series", ""),
     ("STATION_FETCH_DEADLINE_S", "live/config.py", "14 s", "extra station requests are abandoned after this", ""),
     ("STATION_MAX_AGE_MIN", "live/config.py", "90", "how old a station report may be", ""),
     ("NUDGE_ANCHORS", "live/config.py (beta on)", "False public, True beta", "the station fit drives street wind and T/Td", "")],
    tp("om", "rrfs", "coops", "awc", "nws", "spc", "iem", "ndbc", "req")))

gc = F["goes_conus"]
stages.append(stage("B2", "GOES ingest: clear-sky mask and cloud-top height", "live/goes.py, goes5.py → state/goes5/&lt;date&gt;/&lt;hh&gt;.npz", "every run; 5-minute scans",
    f"""<p>Each CONUS scan ({gc['shape'][1]} × {gc['shape'][0]} px) is cropped to <code>goes.BOX = (−123.3, 37.2, −121.9, 38.3)</code>, which is
    74 × 51 px around the Bay. ACM (clear-sky mask) and ACHA (cloud-top height) scans are paired within 60 s. Rule v1 turns them into
    <b>p</b> = low-cloud probability (cloudy with top below 1000 m) and <b>c</b> = confidence (1 by day, 0.7 by night, 0 within ±6° of sunrise/sunset).
    High cloud above a pixel makes it unscorable.</p>""",
    [img("goes_conus_crop", "Whole CONUS clear-sky mask with a red crop box and the crop enlarged",
         f"Input: one whole CONUS scan (this file is from 12 Nov 03:01 UTC; the geometry is the same every scan). The small <b style='color:{BOX}'>red box</b> is what gets cropped, enlarged on the right.", wide=True)],
    [img("goes_lowcloud", "Raw crop and low-cloud rule panels",
         f"Output for the 18 Nov 03:01 UTC scan: raw crop (left two) and rule v1 (right two). <b style='color:{BOX}'>Red</b> = the 20 m city domain inside the crop.", wide=True)],
    [("LOW_TOP_M", "live/config.py", "1000 m", "a cloudy pixel below this top is low cloud / fog", ""),
     ("GOES5_PAIR_TOL_S", "live/config.py", "60 s", "ACM and ACHA scans pair within this", ""),
     ("GOES5_MAX_SCANS_PER_RUN / DEADLINE_S", "live/config.py", "6 / 60 s", "ingest budget per run", ""),
     ("GOES5_KEEP_H", "live/config.py", "48 h", "store retention", ""),
     ("GOES5_TERM_EL", "live/config.py", "6°", "confidence 0 near the day/night algorithm swap", ""),
     ("GOES5_CONF_DAY / NIGHT", "live/config.py", "1.0 / 0.7", "confidence of a valid pixel", ""),
     ("GOES5_HIGH_WIN", "live/config.py", "2 px", "neighbourhood for the high-cloud rule", ""),
     ("C.BETA['goes5']", "live/config.py", "on in beta", "turns the 5-minute store on", "")],
    tp("goes", "h5", "pyproj", "numpy")))

fs_ = fz["fog_source"]
stages.append(stage("B3", "Forcing: fog source s(t), nudging and station anchors", "live/forcing.py, anchors.py", "every run",
    f"""<p>The coast series gives a model fog source s<sub>model</sub>(t) from RRFS low cloud. Where GOES and the KHAF ceiling observe it,
    s<sub>used</sub> = 0.5 · s<sub>model</sub> + 0.5 · s<sub>obs</sub>, and the correction fades into the forecast with a 3 h e-folding time (wind and temperature: 6 h).
    The station fit (anchors {', '.join(fz['anchors']['wind']['anchors'])}) corrects wind speed and direction. This run: {fs_['hours_with_obs']} observed hours,
    mean s model {fs_['mean_model']:.2f}, wind ratio at the last nudged hour {fz['wind_nudge']['ratio_last']}, direction offset {fz['wind_nudge']['dir_offset_last']:.0f}°.</p>"""
    + legend([("l1", "T / s(t) used"), ("l2", "Td / wind"), ("l3", "RRFS low cloud")])
    + f'<figure class="fig wide">{svg_forcing}<figcaption>Input and output in one: beta weather.json. Shaded = observed hours (until frame 8, 20:00 PT 17 Nov).</figcaption></figure>',
    [], [],
    [("W_MODEL / W_OBS", "live/config.py", "0.5 / 0.5", "blend of model and observed fog source", ""),
     ("FOG_E_FOLD_H", "live/config.py", "3 h", "how fast an observed fog correction fades into the forecast", ""),
     ("WIND_E_FOLD_H", "live/config.py", "6 h", "same for wind and temperature", ""),
     ("anchors kmax", "live/anchors.py", "8", "stations used in the regional fit", "")],
    tp("om", "rrfs", "goes", "awc", "coops", "pvlib")))

stages.append(stage("B4", "Fog top: fusion of model, GOES, ceilings and the sounding", "live/topfusion.py (C.BETA top_fusion), fogtop.py", "every run",
    f"""<p>A Kalman filter with a Rauch–Tung–Striebel backward pass. The model humidity profile gives a top every hour (σ 120 m, 250 m when it hits a floor or cap);
    GOES gives the low-cloud top over Ocean Beach (σ 250 m); a ceiling plus 250 m of stratus is an observation (σ 120 m); the sounding inversion (σ 50 m + 30 m per hour away).
    Observations more than 3 σ from the state are skipped. This run: model {tf['model']['used']} used / {tf['model']['rejected']} rejected, GOES {tf['goes']['used']} used,
    sounding <b>{tf['sounding']['rejected']} rejected</b> (inversion base {fz['sounding']['inversion_base_m']:.0f} m, far above the fog).</p>"""
    + legend([("l1", "fused top"), ("bandfill", "± 1 σ"), ("l3", "125·(T−Td)")])
    + f'<figure class="fig wide">{svg_top}<figcaption>Output: fog_top_m and fog_top_sigma_m from weather.json, with the dew-point cloud-base estimate the base stage uses.</figcaption></figure>',
    [], [],
    [("TF_SIGMA_MODEL_M / _BAD_M", "live/config.py", "120 / 250 m", "trust in the model top", ""),
     ("TF_SIGMA_SND_M, TF_SND_PER_H_M, TF_SND_MAX_H", "live/config.py", "50 m, 30 m/h, 9 h", "trust in the sounding and how fast it ages", ""),
     ("TF_SIGMA_CEIL_M, STRATUS_THICK_M", "live/config.py", "120, 250 m", "ceiling + stratus depth as a top", ""),
     ("TF_SIGMA_GOES_M, TF_GOES_MIN_PIX", "live/config.py", "250 m, 5", "trust in the GOES top", ""),
     ("TF_P0_M, TF_Q_M", "live/config.py", "150, 60 m", "start uncertainty and random walk per hour", ""),
     ("TF_GATE_SIGMA", "live/config.py", "3", "outlier gate", ""),
     ("FOG_TOP_MIN / MAX", "wetness.py", "50 / 900 m", "clamps the top used by the wetness", "Lowest top / Highest top (viewer only)"),
     ("topSrc, topFixed", "fog2.js", "Forecast, 200 m", "the viewer can ignore this top", "Height from / Fixed height")],
    tp("kalman", "rts", "spc", "goes", "awc")))

stages.append(stage("B5", "Wetness simulation at every street point", "microclimate/wetness.py simulate()", "every run, 10 min steps, 60 min output",
    f"""<p>Each of the street points gets its own fog arrival (tracer × fog source, lagged by travel time at 1.3 × wind), drizzle, rain, in-place fog, sun on a water film and pavement heat.
    W is an index from 0 to 100 (the index says: "hand-set constants (not calibrated): an index, not a measurement").
    This run: {wet['frames']} hourly frames, {wet['cols']:,} columns; mean W saturates near 100 from frame 16 when rain of 2–6 mm/h arrives.</p>"""
    + legend([("l1", "mean W"), ("l3", "p90 W"), ("barfill", "rain")])
    + f'<figure class="fig wide">{svg_wet}<figcaption>Output: day0–2.u8 summarised over the city, with the rain that drives it.</figcaption></figure>',
    [], [],
    [("TRACER_REF", "wetness.py", "0.03", "tracer level that counts as full fog at a street", ""),
     ("FOG_EXP", "wetness.py", "0.5", "compresses the tracer range", ""),
     ("K_FOG", "wetness.py", "20", "how fast fog wets a street", ""),
     ("GAP_FOG / CLOUD_FOG", "wetness.py", "4 / 40", "T−Td at which fog inflow is zero; low cloud % for full inflow", ""),
     ("LAG_SPEED_FACTOR", "wetness.py", "1.3", "fog front speed as a multiple of the wind", ""),
     ("RH_CLOUD, LCL_M_PER_K, BASE_RAMP_M", "wetness.py", "80 %, 125 m/K, 60 m", "cloud base from T−Td and its fade", ""),
     ("C.BETA physics list", "live/config.py", "sun_clear, film, pavement, cloud_base, lag_uniform, drizzle, insitu_fog", "which physics pieces are on", "")],
    tp("pvlib", "numpy", "osm")))

tl = F["tiles"]
stages.append(stage("B6", "2D fog tiles (the sprite sheets)", "live/outputs.py fog_frames() → fog0–2.png", "every run",
    f"""<p>Public: tile = 255 · s(t − lag) · Σ<sub>d</sub> w<sub>d</sub> tr80<sub>d</sub> · GOES factor, with GOES factor = 0.30 + 0.7 · max(low, 0.5 · cloudy) in observed hours.
    Beta (tile_from_3d on): the tile is level 0 of the 3D fog, so it carries the cloud base. 214 × 203 px per tile at 80 m, 8 tiles per row.
    Tile 7 (19:00 PT): beta mean <b>{tl['beta_tile7_mean']:.0f}</b> (the base was ~594 m, above a 221 m top, so no ground fog), public mean {tl['public_tile7_mean']:.0f}.</p>""",
    [],
    [img("fog_tiles", "Sprite sheet with two red boxes and four enlarged tiles",
         f"Output: the whole beta fog0.png. The <b style='color:{BOX}'>red boxes</b> are tiles 7 and 12, enlarged below with the public tile 7 and the GOES sat tile 7.", wide=True)],
    [("CLEAR_MAX_OPACITY", "live/config.py", "0.30", "most fog allowed where GOES says clear", ""),
     ("C.BETA tile_from_3d", "live/config.py", "on in beta", "2D tile = 3D level 0", ""),
     ("C.BETA fog_base_2d", "live/config.py", "on in beta", "applies the cloud base to the 2D tile", ""),
     ("PAST / FUTURE", "live/outputs.py", "6 / 30 frames", "which frames get tiles", ""),
     ("density", "foglab.js", "33 % (5–60)", "overall opacity in the viewer", "Fog density")],
    tp("pillow", "numpy")))

f3 = F["fog3d"]
stages.append(stage("B7", "3D fog volume, cloud base and lid", "live/fog3d.py, fogbase.py, outputs.py write_fog3d() → fog3d_NNN.png", "every run",
    f"""<p>vol = amp · Σ<sub>d</sub> w<sub>d</sub> Σ<sub>b</sub> a<sub>b</sub> tr3d / 255 · weight3d, where a<sub>b</sub> is how much of band b lies under the fused top.
    The base is z<sub>m</sub> + 125 · (T − Td) + offset (the urban lift over warm, calm, built-up ground is off); a strong column keeps at least 30 m of fog under its top; the lid fades from top + 50 m to top + 150 m.
    24 levels of 25 m are packed into a 6 × 4 atlas PNG; the viewer reads value 16 as full density. Frame 12 (00:00 PT 18 Nov): top {f3['fog_top_m']:.0f} m, σ {f3['sigma']:.0f} m,
    base {f3['base_m']:.0f} m, max value {f3['max_value']:.0f}.</p>""",
    [],
    [img("fog3d_atlas", "Atlas of 24 levels with level 0 boxed, and a rebuilt slice",
         f"Output: fog3d_012.png. The <b style='color:{BOX}'>red box</b> is level 0 (12.5 m), which beta also uses as the 2D tile. Bottom: the slice and level means rebuilt from the atlas.", wide=True)],
    [("FOG_MIN_THICK_M", "live/fogbase.py", "30 m", "a strong column keeps at least this much fog below its top", ""),
     ("AMP_STRONG", "live/fogbase.py", "0.5", "amp (source × satellite factor) above which the 30 m guard acts", ""),
     ("DT_URBAN_K, DT_CAP_K", "live/fogbase.py", "1.0, 1.5 K", "extra near-ground warmth of built-up ground (urban lift) and its cap", ""),
     ("SUPPLY_DAMP", "live/fogbase.py", "0.7", "thick, well-fed fog cools the ground and shrinks the lift", ""),
     ("VENT_U0, HEAT_K, SUN_REF_WM2", "live/fogbase.py", "3 m/s, 10, 600 W/m²", "wind mixes the warm layer away; pavement and sun heat that count as full heat", ""),
     ("LID_MARGIN_M / LID_FADE_M", "live/config.py", "50 / 100 m", "where the lid starts and how soft it is", "Softness (viewer side)"),
     ("FOG3D_BUDGET", "live/outputs.py", "12 MB", "size cap of all atlases", ""),
     ("C.BETA fog3d, fog_base, fog_lid, urban_lift", "live/config.py", "on, on, on, off", "switches", "3D fog data")],
    tp("pillow", "numpy")))

stages.append(stage("B8", "Macro frames: the wide-area satellite view", "live/goes5.py → macro/&lt;yyyymmddThhmm&gt;.png", "every run, last 6 h",
    "<p>Every 5-minute scan in the last 6 h becomes a 141 × 111 px PNG at 0.01° over the GOES box: R = p × 255, G = c × 255, B = cloud top / 8 m. 72 frames in this snapshot.</p>",
    [], [img("macro_frame", "Three channels of a macro frame", f"Output: macro/20261118T0301.png. <b style='color:{BOX}'>Red</b> = the 20 m city domain.", wide=True)],
    [("GOES5_MACRO_H", "live/config.py", "6 h", "how far back", ""),
     ("GOES5_MACRO_RES", "live/config.py", "0.01°", "grid spacing", ""),
     ("GOES5_DEFAULT_TOP_M", "live/config.py", "300 m", "parallax top when none is known", ""),
     ("macro", "fog2.js", "1", "draw the wide-area view", "Wide-area view")],
    tp("goes", "pillow")))

f5 = F["fog5"]
stages.append(stage("B9", "fog5: five-minute fog frames (fog travel)", "live/travel.py, fog5.py → fog5/index.json, tile_*.png, vol_*.png", "every run",
    f"""<p>Guide 08: predict (advect the last state with the wind), relax toward the hourly prior, observe (GOES 5-minute scan), correct. {f5['n']} frames from
    {f5['first'][11:16]} to {f5['last'][11:16]} UTC: {f5['kinds']['observed']} observed, {f5['kinds']['forecast']} forecast. The wind is uniform for now.</p>""",
    [], [img("travel", "Confidence per frame and a sprite of 12 tiles", f"Output: index.json confidence by frame; the 03 UTC tile sheet with the <b style='color:{BOX}'>:00 tile boxed</b> and enlarged.", wide=True)],
    [("STEP_S", "live/travel.py", "300 s", "frame step", ""),
     ("TRAVEL_PAST_H / FWD_H", "live/travel.py", "6 / 2 h", "window", ""),
     ("TAU_PRIOR_S", "live/travel.py", "5400 s", "relaxation toward the hourly field", ""),
     ("KMAX", "live/travel.py", "0.5", "largest correction gain", ""),
     ("TRAVEL_SPEED_FACTOR", "live/travel.py", "1.3", "front speed vs wind", ""),
     ("GUIDE_BOX, SMOOTH_SIGMA, HOURLY_MIX, SCAN_TOL_S", "live/travel.py", "25, 2.5, 0.3, 150 s", "observation box, smoothing, hourly mix, scan timing", ""),
     ("VOL_PAST_H / FWD_H, FOG5_BUDGET", "live/fog5.py", "3 / 1 h, 15 MB", "which frames get volumes", ""),
     ("travel", "fog2.js", "1", "use the 5-minute frames", "5-minute fog frames")],
    tp("goes", "scipy", "pillow")))

hd = CS["headline"]
stages.append(stage("B10", "Cloud score: does the model match the satellite?", "live/cloud_score.py → cloud_score.json", "every run",
    f"""<p>Each hour and box is a yes/no for low cloud at 0.5. Fields scored: rrfs, source, fog_sheet, fog_sheet_nosat. Headline: <b>{esc(hd['text'])}</b>.
    That headline field is nudged with the same satellite it is scored against, so it measures consistency, not skill (cloud_score.json says so).
    The independent RRFS score over 24 h: day CSI {h24['day']['csi']}, night CSI {h24['night']['csi']} (hit rate {h24['night']['hit_rate']}).</p>""",
    [], [], [("yes_at", "cloud_score.py", "0.5", "threshold for a yes", ""), ("high_cloud_max", "cloud_score.py", "0.3", "more high cloud makes a box unscorable", ""), ("HOURS_LIVE_H", "cloud_score.py", "48", "hour-level history kept", "")],
    tp("goes")))

stages.append(stage("B11", "Sky layers and publishing", "live/skylayers.py; build.py writes status.json, index.json", "every run",
    f"""<p>Up to three cloud layers per hour above the fog from the RRFS humidity profile, METAR layers and the satellite top (guide 13). Then everything is written and deployed by GitHub Actions
    to Pages. This beta run took {S['run_seconds']} s ({S['simulate_seconds']} s of simulation) and wrote 3D atlases of {S['fog3d']['bytes_written']/1e6:.1f} MB.</p>""",
    [], [],
    [("SKY_RH_THRESHOLD", "skylayers.py", "92 %", "saturation for a cloud layer", ""),
     ("SKY_MIN_THICK_M / SKY_THIN_M", "skylayers.py", "60 / 150 m", "thinnest layer kept; thickness of a satellite-only deck", ""),
     ("SKY_METAR_NUDGE / SKY_SAT_NUDGE", "skylayers.py", "0.5 / 0.6", "weight of airport and satellite evidence", ""),
     ("SKY_CIRRUS_M", "skylayers.py", "6000 m", "tops above are not drawn", ""),
     ("sky", "fog2.js", "0", "draw the sky layers", "Sky layers"),
     ("DEATHFOG_LIVE", "repo variable", "true", "stops the live build when false", "")],
    tp("awc", "gha", "ghp")))

# ====== C: viewer
stages.append(stage("C1", "Viewer: voxel fog and the cloud menu", "site/v3/fog2.js, foglab.js, fog3dview.js", "in the browser",
    "<p>The 3D atlas becomes a WebGL 3D texture that is ray-marched over the terrain. The fog top shape, softness, detail, billows and light are drawn here, not computed in the build. The cloud menu opens from the cloud button.</p>",
    [], [img("shot_beta_now", "Beta viewer at 21:31 PT", "The beta viewer (18 Nov run, 21:31 PT forecast frame)."),
         img("shot_foglab", "The cloud menu", "The cloud menu (foglab.js): Fog top, Shape, Wind, Light, Advanced.")],
    [("follow", "fog2.js", "0.3", "how much the top follows the terrain", "Shape"),
     ("smooth", "fog2.js", "3.5 h", "time smoothing of the top", "Time smoothing (h)"),
     ("topSrc / topFixed", "fog2.js", "data / 200 m", "use the fused top or a fixed one", "Height from / Fixed height"),
     ("topMin / topMax", "fog2.js", "90 / 900 m", "clamp of the drawn top", "Lowest top / Highest top"),
     ("soft, bump", "fog2.js", "1, 0.25", "edge softness and lumpy top", "Softness / Lumpy top"),
     ("detail, warp", "fog2.js", "0.9, 1.25", "fine noise and billows", "Fine detail / Billows"),
     ("flow, flowSpeed, advect, eddy", "fog2.js", "1, 1.0, 1.0, 0.2", "detail follows the fogflow wind; drift; eddies", "Detail follows wind / Detail drift speed / Shape drift / Eddies"),
     ("sun, night, shadow", "fog2.js", "0.5, 1, 0.8", "lighting", "Sunlight / Night brightness / Self-shadow"),
     ("mode, steps, res, vres, vlayers", "fog2.js", "voxel, 0, 0, 0, 0 (auto)", "technique and quality", "Technique / Steps / Resolution / Voxel size / Voxel layers"),
     ("skip, accum, motion, motionSteps, taa", "fog2.js", "1, 6, 1, 0.6, 1", "speed tricks", "Skip empty space / Still refinement / Cheaper while moving / Trace 1 in 16"),
     ("edgeFade, data3d, dither, blur, auto", "fog2.js", "3000 m, 1, 1, 1, off", "edge, data source, filtering, auto quality", "Fog beyond the data edge / 3D fog data / Dither / Blur / Auto quality drop"),
     ("presets", "foglab.js", "Default, Flat marine layer, Hugs the hills, Classic", "set several sliders at once", "Preset buttons")],
    tp("three", "jsd", "webgl")))

stages.append(stage("C2", "Viewer: wide-area macro view", "site/v3/macro.js, macrocalc.js, sky.js", "in the browser",
    "<p>The macro PNGs are drawn as a cloud deck around the city, kept above the fog top. sky.js draws the sky layers when the Sky layers switch is on.</p>",
    [], [img("shot_macro_coast", "Viewer from the coast with the macro deck", "Macro view from the coast (an older run: note the data-delayed banner).")],
    [("macro", "fog2.js", "1", "draw the deck", "Wide-area view"), ("sky", "fog2.js", "0", "draw the sky layers", "Sky layers")],
    tp("three")))

stages.append(stage("C3", "Viewer: fog wind and 5-minute travel", "site/v3/fogflow.js, fogtravel.js", "in the browser",
    "<p>fogflow.js builds a mass-consistent 2D wind over the terrain from the forecast wind; it only moves the fog's fine detail. fogtravel.js plays the fog5 frames between the hourly ones.</p>",
    [], [], [("flow, flowSpeed", "fog2.js", "1, 1.0", "detail follows this wind", "Detail follows wind / Detail drift speed"), ("travel", "fog2.js", "1", "play the 5-minute frames", "5-minute fog frames")],
    tp("three")))

STAGES_HTML = "".join(stages)

# ---------------------------------------------------------------- wind tiers
TIERS = [
    ("Macro (region)", "~1 km (0.01°)", "none: no wind simulation", "RRFS 6 × 5 lattice via Open-Meteo; 21 stations; GOES scans", "B1, B2, B8"),
    ("City 20 m (A2)", "20 m, 855 × 811 × 40", "XLB LBM, 15 directions", "Geometry from 3DEP + DataSF (A1); a uniform inflow per direction. Live: scaled by the nudged RRFS wind (B3)", "A2 → B5–B7"),
    ("Nested 5 m (A3)", "5 m, 297 patches; 2.5 m Potrero", "XLB LBM, 270° only", "Boundary from the 20 m field (one-way, sponge); buildings at 5 m", "A3 → B5"),
    ("Street 5 m spike (guide 12)", "5 m", "XLB LBM", "Uniform inflow, not nested", "research"),
    ("Fog tracer (A4)", "20 m → 80 m tiles, 25 m levels", "Warp advection–diffusion", "The 20 m wind field for each direction; fog band at the west edge", "A4 → B6, B7"),
    ("Viewer fogflow (C3)", "browser grid", "mass-consistent 2D", "Forecast wind from weather.json + terrain", "C3"),
    ("fog5 travel (B9)", "80 m", "uniform advection", "One forecast wind for the whole city", "B9"),
]
tiers_html = '<div class="tw"><table class="vars tiers"><thead><tr><th>Tier</th><th>Resolution</th><th>Solver</th><th>Where its input comes from</th><th>Feeds</th></tr></thead><tbody>' + "".join(
    f"<tr><td><b>{a}</b></td><td>{b}</td><td>{c}</td><td>{d}</td><td>{e}</td></tr>" for a, b, c, d, e in TIERS) + "</tbody></table></div>"

DIS = [
    ("Wind solver walls", "v3-dev <code>wind.py</code> lacks the walls fix and free-slip sea. The beta library was built from branch <code>wind-cube-check</code>. 00_README's \"Guide 01 needs no new wind runs\" is out of date.",
     f"Near-surface ratio at 270°: old {F['wind2700_old_near_surface_p5_p50_p95'][1]:.3f} median, walls {w27['near_surface_p5_p50_p95'][1]:.3f}."),
    ("Tracer diffusivity", "Beta tracer assets use kd 20 and kdv 8 from spike scripts. <code>microclimate.toml</code> still says <code>eddy_diffusivity_m2s = 2.0</code> and has no vertical key.", ""),
    ("5 m correction", "README says the 297-patch correction is used. True for public (rel_wind median 0.498); the beta static has rel_wind = 1 everywhere, because the correction was computed on the no-walls field.", ""),
    ("TRACER_REF", "0.03 was tuned on the old tracer. The beta point tracer at 270° is p5 0.18 / p50 0.27 / p95 0.46, so almost every point saturates.", "Public: p5 0.001, p50 0.12, p95 0.54."),
    ("fog5 tiles vs hourly tiles", "fog5 tiles are 255 · A · trb with no cloud base; the beta hourly tile uses tile_from_3d with the base. At 03 UTC the hourly tile is empty while fog5 shows fog. (fog5 volumes do use base and lid.)", ""),
    ("Switch table", "<code>fog_lid</code>, <code>fog_base_2d</code> and <code>tile_from_3d</code> (16 Nov) are missing from 00_README's switch table.", ""),
    ("Sky lab hint", "The lab hint and a fog2.js comment say \"nothing is drawn\" for sky, but macro.js does draw the deck (kept above the fog top).", ""),
    ("fog5 wind", "status.json says \"uniform (fogflow.js wind not used yet)\"; guide 08 plans terrain-aware wind.", ""),
    ("Fog-top cap", "wetness <code>FOG_TOP_MAX</code> is 900 m, the forcing cap is 1300 m.", ""),
    ("Directions", "<code>microclimate.toml</code> lists 202.5–360°; the assets also carry 180° and the six east directions.", ""),
]
dis_html = "<ol class='dis'>" + "".join(f"<li><b>{a}.</b> {b} <span class='dim'>{c}</span></li>" for a, b, c in DIS) + "</ol>"

# ---------------------------------------------------------------- manifest for machines
manifest = {
    "schema": 1, "title": "SF death fog: cloud pipeline", "snapshot": {"beta_status_generated": S["generated_iso"], "repo_branch": "v3-dev", "frames": "frame 0 = 2026-11-17T12:00-08:00, observed until frame 8"},
    "grid": {"crs": "EPSG:32610", "x0": 540100, "y0": 4171500, "dx_m": 20, "shape_ijk": g["shape"], "i": "east", "j": "north", "z_center_m": "(k+0.5)*20"},
    "stages": [{"id": s.split('data-stage="')[1].split('"')[0], "title": s.split("<h3>")[1].split("</h3>")[0]} for s in stages],
    "wind_tiers": [dict(zip(["tier", "resolution", "solver", "input_from", "feeds"], [html.unescape(x) for x in t])) for t in TIERS],
    "beta_switches": ["fog3d", "fog_base", "urban_lift=False", "fog_travel", "top_fusion", "fog_lid", "fog_base_2d", "tile_from_3d", "goes5", "wind_east"],
    "disagreements": [a for a, _, _ in DIS],
}
MAN = json.dumps(manifest, ensure_ascii=False, indent=1).replace("</", "<\\/")

FLOW = """<svg class="flow" viewBox="0 0 980 300" role="img" aria-label="Pipeline overview"><title>Pipeline overview</title>
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker></defs>
<text class="lane" x="10" y="20">A · OFFLINE (PC GPU)</text>
<text class="lane" x="10" y="132">B · LIVE BUILD (GitHub Action, ~30 min)</text>
<text class="lane" x="10" y="250">C · VIEWER (browser)</text>
<g class="nodes">
<a href="#a1"><rect x="10" y="30" width="140" height="44" rx="6"/><text x="80" y="56">A1 Geometry</text></a>
<a href="#a2"><rect x="170" y="30" width="140" height="44" rx="6"/><text x="240" y="56">A2 Wind 20 m</text></a>
<a href="#a3"><rect x="330" y="30" width="140" height="44" rx="6"/><text x="400" y="56">A3 Nested 5 m</text></a>
<a href="#a4"><rect x="490" y="30" width="140" height="44" rx="6"/><text x="560" y="56">A4 Fog tracer</text></a>
<a href="#a5"><rect x="650" y="30" width="140" height="44" rx="6"/><text x="720" y="56">A5 Assets (npz)</text></a>
<a href="#b1"><rect x="10" y="142" width="110" height="44" rx="6"/><text x="65" y="168">B1 Gather</text></a>
<a href="#b2"><rect x="10" y="196" width="110" height="34" rx="6"/><text x="65" y="217">B2 GOES</text></a>
<a href="#b3"><rect x="140" y="142" width="110" height="44" rx="6"/><text x="195" y="168">B3 Forcing</text></a>
<a href="#b4"><rect x="270" y="142" width="110" height="44" rx="6"/><text x="325" y="168">B4 Fog top</text></a>
<a href="#b5"><rect x="400" y="142" width="110" height="44" rx="6"/><text x="455" y="168">B5 Wetness</text></a>
<a href="#b6"><rect x="530" y="142" width="110" height="44" rx="6"/><text x="585" y="168">B6 2D tiles</text></a>
<a href="#b7"><rect x="660" y="142" width="110" height="44" rx="6"/><text x="715" y="168">B7 3D fog</text></a>
<a href="#b8"><rect x="140" y="196" width="110" height="34" rx="6"/><text x="195" y="217">B8 Macro</text></a>
<a href="#b9"><rect x="270" y="196" width="110" height="34" rx="6"/><text x="325" y="217">B9 fog5</text></a>
<a href="#b10"><rect x="400" y="196" width="110" height="34" rx="6"/><text x="455" y="217">B10 Score</text></a>
<a href="#b11"><rect x="790" y="142" width="170" height="44" rx="6"/><text x="875" y="168">B11 Sky + publish</text></a>
<a href="#c1"><rect x="530" y="258" width="140" height="34" rx="6"/><text x="600" y="279">C1 Voxel fog</text></a>
<a href="#c2"><rect x="690" y="258" width="130" height="34" rx="6"/><text x="755" y="279">C2 Macro</text></a>
<a href="#c3"><rect x="840" y="258" width="130" height="34" rx="6"/><text x="905" y="279">C3 Flow</text></a>
</g>
<g class="edges">
<path d="M150,52 H170"/><path d="M310,52 H330"/><path d="M470,52 H490"/><path d="M630,52 H650"/><path d="M240,74 C240,108 560,108 560,76" class="soft"/>
<path d="M720,74 C720,110 470,110 455,142"/><path d="M720,74 V142"/>
<path d="M120,164 H140"/><path d="M250,164 H270"/><path d="M380,164 H400"/><path d="M510,164 H530"/><path d="M640,164 H660"/><path d="M770,164 H790"/>
<path d="M65,196 V186"/><path d="M120,213 H140"/><path d="M250,213 H270"/>
<path d="M875,186 V244 H600 V256"/><path d="M755,244 V256"/><path d="M905,244 V256"/>
</g></svg>"""

CSS = """
:root{--bg:#f6f7f8;--panel:#ffffff;--fg:#1b2127;--dim:#5c6670;--line:#d9dee3;--accent:#2a6f97;--accent2:#c2410c;--fogfill:rgba(42,111,151,.16);
--c1:#2a6f97;--c2:#7a8b99;--c3:#c2410c;--obs:rgba(27,33,39,.05);--menu:#ecf3f8;--menufg:#1f5576;--code:#eef1f4;--box:#ff2d55}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11161b;--panel:#171e24;--fg:#e3e8ec;--dim:#97a3ad;--line:#2b353e;--accent:#7cb9de;--accent2:#f59e5b;--fogfill:rgba(124,185,222,.18);
--c1:#7cb9de;--c2:#a6b4bf;--c3:#f59e5b;--obs:rgba(255,255,255,.04);--menu:#1d2d38;--menufg:#a8d2ec;--code:#1f2830;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11161b;--panel:#171e24;--fg:#e3e8ec;--dim:#97a3ad;--line:#2b353e;--accent:#7cb9de;--accent2:#f59e5b;--fogfill:rgba(124,185,222,.18);
--c1:#7cb9de;--c2:#a6b4bf;--c3:#f59e5b;--obs:rgba(255,255,255,.04);--menu:#1d2d38;--menufg:#a8d2ec;--code:#1f2830;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-block:24px 80px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;grid-template-columns:200px minmax(0,1fr);gap:32px}
nav.toc{position:sticky;top:16px;align-self:start;font-size:13px;max-height:calc(100vh - 32px);overflow:auto}
nav.toc a{display:block;color:var(--dim);text-decoration:none;padding:2px 0}
nav.toc a:hover{color:var(--accent)} nav.toc b{display:block;margin-top:12px;font-size:11px;letter-spacing:.08em;color:var(--fg)}
main{min-width:0}
h1{font-size:30px;line-height:1.2;margin:0 0 8px;text-wrap:balance}
h2{font-size:21px;margin:48px 0 12px;padding-top:8px;border-top:2px solid var(--fg);text-wrap:balance}
h3{font-size:18px;margin:0;text-wrap:balance}
h4.io{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin:18px 0 6px}
h4.in::before{content:"→ ";color:var(--accent)} h4.out::before{content:"← ";color:var(--accent2)}
p{max-width:72ch} .lede{color:var(--dim);font-size:16px}
a{color:var(--accent)}
code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:1px 4px;border-radius:3px;word-break:break-word}
.stage{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:18px 0}
.stage header{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:10px}
.sid{font:700 13px ui-monospace,Menlo,monospace;color:var(--panel);background:var(--fg);padding:2px 7px;border-radius:4px}
.where{margin:0;color:var(--dim);font-size:13px;flex-basis:100%}
.fig{margin:10px 0;max-width:100%} .fig img{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
figcaption{font-size:13px;color:var(--dim);margin-top:4px;max-width:90ch}
.tw{overflow-x:auto;margin:6px 0;border:1px solid var(--line);border-radius:6px}
table.vars{border-collapse:collapse;width:100%;font-size:13px;min-width:720px}
.vars th{text-align:left;background:var(--code);font-weight:600;padding:6px 8px;white-space:nowrap}
.vars td{padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}
.vars td:first-child code{white-space:normal} .vars td:nth-child(2) code{white-space:nowrap}
.menu{display:inline-block;background:var(--menu);color:var(--menufg);border-radius:4px;padding:0 6px;font-weight:600}
.dim{color:var(--dim)}
details.third{margin-top:12px;border:1px dashed var(--line);border-radius:6px;padding:6px 10px}
details.third summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--dim)}
details.third ul{margin:8px 0 2px;padding-left:18px;font-size:13.5px}
svg.chart{width:100%;height:auto;display:block;background:var(--panel)}
svg.chart text{fill:var(--dim);font:11px system-ui,sans-serif} svg.chart .plab{fill:var(--fg);font-weight:600}
svg.chart .em{fill:var(--fg);font-weight:600}
svg.chart .grid{stroke:var(--line);stroke-width:1} svg.chart .obs{fill:var(--obs)} svg.chart .now{stroke:var(--fg);stroke-dasharray:3 3}
svg.chart path{fill:none;stroke-width:1.6} .l1{stroke:var(--c1)} .l2{stroke:var(--c2)} .l3{stroke:var(--c3)} .thick{stroke-width:2.4!important}
.bandfill{fill:var(--fogfill)} .barfill{fill:var(--c1)}
.legend{font-size:12.5px;color:var(--dim);display:flex;flex-wrap:wrap;gap:4px 14px;margin:8px 0 0}
.sw{display:inline-block;width:14px;height:3px;margin-right:5px;vertical-align:middle;background:currentColor}
.sw.l1{background:var(--c1)} .sw.l2{background:var(--c2)} .sw.l3{background:var(--c3)} .sw.bandfill{background:var(--fogfill);height:10px} .sw.barfill{background:var(--c1);height:10px;width:8px}
.flowbox{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
svg.flow{min-width:760px;width:100%;height:auto}
svg.flow rect{fill:var(--menu);stroke:var(--accent);stroke-width:1.2} svg.flow a:hover rect{fill:var(--accent)}
svg.flow text{fill:var(--fg);font:600 12.5px system-ui,sans-serif;text-anchor:middle} svg.flow .lane{text-anchor:start;fill:var(--dim);font-size:11px;letter-spacing:.08em}
svg.flow .edges path{fill:none;stroke:var(--dim);stroke-width:1.3;marker-end:url(#ar)} svg.flow .edges .soft{stroke-dasharray:4 3} svg.flow .ah{fill:var(--dim)}
ol.dis li{margin:6px 0;max-width:90ch}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}
.facts div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.facts b{display:block;font-size:20px} .facts span{font-size:12px;color:var(--dim)}
.boxkey{color:var(--box);font-weight:700}
@media (max-width:860px){.wrap{grid-template-columns:minmax(0,1fr)} nav.toc{position:static;max-height:none;columns:2;border-bottom:1px solid var(--line);padding-bottom:10px} .stage{padding:14px}}
"""

SHORT = dict(A1="Geometry", A2="Wind library 20 m", A3="Nested 5 m", A4="Fog tracer", A5="Asset packing", A6="Marine-layer spike", B1="Gather", B2="GOES ingest", B3="Forcing", B4="Fog top", B5="Wetness", B6="2D fog tiles", B7="3D fog, base, lid", B8="Macro frames", B9="fog5 travel", B10="Cloud score", B11="Sky layers, publish", C1="Voxel fog, cloud menu", C2="Macro view", C3="Fog wind, travel")
toc = ['<nav class="toc" aria-label="Contents"><b>OVERVIEW</b><a href="#overview">Pipeline at a glance</a><a href="#tiers">Wind tiers and their inputs</a><a href="#disagree">Code vs guides</a>']
for grp, label in (("A", "A · OFFLINE"), ("B", "B · LIVE BUILD"), ("C", "C · VIEWER")):
    toc.append(f"<b>{label}</b>")
    for s in stages:
        sid = s.split('data-stage="')[1].split('"')[0]
        if sid[0] == grp:
            t = SHORT[sid]
            toc.append(f'<a href="#{sid.lower()}">{sid} {t}</a>')
toc.append('<b>MACHINES</b><a href="#manifest">JSON manifest</a></nav>')

page = f"""<title>Fog Pipeline Atlas</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="Every stage of the SF death-fog cloud pipeline, with real beta data in and out, the variables that steer it, cloud-menu controls and third-party sources.">
<style>{CSS}</style>
<div class="wrap">
{''.join(toc)}
<main>
<header id="overview">
<p class="dim" style="margin:0;font-size:12px;letter-spacing:.08em">SF DEATH FOG · v3 · CLOUD PIPELINE</p>
<h1>Fog Pipeline Atlas</h1>
<p class="lede">How the fog on the map is made: geometry and wind are simulated once on a GPU, then every ~30 minutes a GitHub Action blends forecasts, stations and satellite into fog tops, street wetness and 3D fog, which the browser draws.
Every figure is real output: the beta build of <b>{S['generated_iso'][:16].replace('T', ' ')} UTC</b> (17 Nov, 20:43 PT), the offline libraries on the PC, and branch v3-dev.
Crops are shown inside their source with a <span class="boxkey">red box</span>.</p>
<div class="facts">
<div><b>855 × 811 × 40</b><span>20 m cells in the city grid</span></div>
<div><b>15</b><span>wind directions simulated</span></div>
<div><b>11</b><span>fog inflow bands per direction</span></div>
<div><b>{F['asset']['points']:,}</b><span>street points simulated</span></div>
<div><b>{S['run_seconds']:.0f} s</b><span>one beta build</span></div>
</div>
<div class="flowbox">{FLOW}</div>
<p class="dim" style="font-size:13px">Click a box to jump to its stage. Each stage lists its input, its output, the variables you can change (and the matching cloud-menu control, if any), and its third-party sources.</p>
</header>

<h2 id="tiers">Wind tiers: where each gets its input</h2>
<p>There is no single wind model. Each tier below takes its input from a different place; only the 20 m and 5 m tiers are real flow simulations.</p>
{tiers_html}

<h2 id="disagree">Where the code and the guides disagree</h2>
<p>The code is the source of truth. These are places where a guide, the README or a config file says something the running code or data does not.</p>
{dis_html}

<h2 id="a">A · Offline: simulated once on the PC</h2>
{''.join(s for s in stages if 'data-stage="A' in s)}
<h2 id="b">B · Live build: every ~30 minutes</h2>
{''.join(s for s in stages if 'data-stage="B' in s)}
<h2 id="c">C · Viewer: in the browser</h2>
{''.join(s for s in stages if 'data-stage="C' in s)}

<h2 id="manifest">For machines</h2>
<p>A JSON summary of stages, wind tiers, switches and disagreements is embedded as <code>&lt;script type="application/json" id="pipeline-manifest"&gt;</code>. Every stage is a <code>&lt;section data-stage&gt;</code> and every variable table has the columns Variable, Where, Value, What it does, Cloud menu.</p>
<p class="dim" style="font-size:12px">Built by <code>pipeline-doc/work/build.py</code> from figures made by <code>gen.py</code>. Snapshot data: beta status.json, weather.json, cloud_score.json, fog5/index.json, macro/index.json.</p>
<script type="application/json" id="pipeline-manifest">{MAN}</script>
</main></div>
"""
OUT.write_text(page, encoding="utf-8")
print(OUT, len(page) / 1e6, "MB")
