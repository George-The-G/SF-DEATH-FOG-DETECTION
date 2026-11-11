"""Static status page (no JavaScript, no external requests) + status.json, published to GitHub Pages."""
import json
import pathlib
from datetime import timedelta
from html import escape
from zoneinfo import ZoneInfo

from . import db, engine, reports
from .detectors import build_detector
from .collect import build_sources
from .util import hour_label, iso, local_hour, local_str, parse_iso

LEVEL_TEXT = {"none": "Quiet", "watch": "Watch", "likely": "Likely", "strong": "Strong"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202a;--mute:#5b6773;--line:#dde2e8;--none:#d9e4dc;--watch:#f4e3a1;--likely:#f6b26b;--strong:#e06666;--btn:#16202a;--btnink:#fff;--c-sat:#1f5fa8;--c-near:#7fb2e5;--c-calm:#8a6fb0;--c-swing:#d9822b;--c-cool:#2a9d8f;--c-sun:#7b8794}
@media (prefers-color-scheme:dark){:root{--bg:#0f1419;--card:#1a222b;--ink:#e8edf2;--mute:#9aa7b4;--line:#2c3743;--none:#2a3a30;--watch:#6b5d1f;--likely:#8a5a1f;--strong:#8f2d2d;--btn:#e8edf2;--btnink:#0f1419;--c-sat:#5b9be0;--c-near:#3d6a9a;--c-calm:#a78bd0;--c-swing:#e39a4d;--c-cool:#4fc1b2;--c-sun:#8fa0b3}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.1rem;margin:0}
.sub,.mute{color:var(--mute);font-size:.9rem}.banner{background:var(--strong);color:#fff;padding:10px 12px;border-radius:8px;margin:10px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.top{display:flex;justify-content:space-between;align-items:center;gap:8px}
.pill{padding:3px 10px;border-radius:99px;font-weight:600;font-size:.9rem;border:1px solid var(--line)}
.lvl-none{background:var(--none)}.lvl-watch{background:var(--watch)}.lvl-likely{background:var(--likely)}.lvl-strong{background:var(--strong);color:#fff}
ul{margin:.4rem 0 .6rem;padding-left:1.1rem}.strip{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px}
.blk{min-width:2.1rem;text-align:center;font-size:.75rem;padding:4px 2px;border-radius:5px}
a.btn{display:block;text-align:center;background:var(--btn);color:var(--btnink);padding:12px;border-radius:10px;text-decoration:none;font-weight:600;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:.9rem}td,th{text-align:left;padding:5px 4px;border-bottom:1px solid var(--line)}
.hero{padding:14px 12px}.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.hero>input{position:absolute;opacity:0;pointer-events:none}.tabs label{padding:6px 12px;border:1px solid var(--line);border-radius:99px;font-size:.9rem;cursor:pointer}
.panel{display:none}.chart{width:100%;height:auto;display:block}.chart text{fill:var(--mute);font-size:12px}.chart .nowl{stroke:var(--ink);stroke-width:1.5;stroke-dasharray:4 3}
.chart .nowt{fill:var(--ink);font-weight:600}.chart .grid{stroke:var(--line);stroke-dasharray:3 3}.chart .past{opacity:.5}.chart rect.bar{stroke:var(--card);stroke-width:.6}
.chart .b-w{fill:var(--watch)}.chart .b-l{fill:var(--likely)}.chart .b-s{fill:var(--strong)}.chart .band{opacity:.28}
.t-saturation{fill:var(--c-sat)}.t-saturation_near{fill:var(--c-near)}.t-calm{fill:var(--c-calm)}.t-swing{fill:var(--c-swing)}.t-cooling{fill:var(--c-cool)}.t-no_sun{fill:var(--c-sun)}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:.8rem;color:var(--mute);margin:6px 0}.legend i{display:inline-block;width:.8em;height:.8em;border-radius:2px;margin-right:4px;vertical-align:-1px}
.why{margin:.2rem 0 0}.why h3{font-size:.95rem;margin:.6rem 0 0}
footer{font-size:.8rem;color:var(--mute);margin:18px 0}
"""


TRIGGERS = [  # stack order (bottom to top), plain-English label, short phrase for the tooltip
    ("saturation", "Air is saturated (temp = dew point)"),
    ("saturation_near", "Air close to saturated"),
    ("swing", "Big drop from today's high"),
    ("cooling", "Cooling fast"),
    ("calm", "Calm wind"),
    ("no_sun", "No sun"),
]
_LABEL = dict(TRIGGERS)
CW, CH, ML, MR, MT, MB = 480, 262, 30, 50, 24, 34


def _local(dt, tz):
    return dt.astimezone(ZoneInfo(tz))


def _chart_svg(tl, now, tz, thresholds):
    """Stacked hourly bars: height = death-fog score (0-100), colours = which triggers add up to it."""
    pw, ph = CW - ML - MR, CH - MT - MB
    y = lambda v: MT + ph * (1 - v / 100.0)
    step = pw / len(tl)
    bw = step * 0.78
    out = [f'<svg class="chart" viewBox="0 0 {CW} {CH}" role="img" aria-label="Death fog score by hour, past 12 hours and next 24 hours">']
    lo = {"watch": thresholds.get("watch", 40), "likely": thresholds.get("likely", 60), "strong": thresholds.get("strong", 80)}
    for cls, a, b, name in (("b-w", lo["watch"], lo["likely"], "Watch"), ("b-l", lo["likely"], lo["strong"], "Likely"),
                            ("b-s", lo["strong"], 100, "Strong")):
        out.append(f'<rect class="band {cls}" x="{ML}" y="{y(b):.1f}" width="{pw}" height="{y(a) - y(b):.1f}"/>'
                   f'<text x="{ML + pw + 4}" y="{(y(a) + y(b)) / 2 + 4:.1f}">{name}</text>')
    for v in (lo["watch"], lo["likely"], lo["strong"]):
        out.append(f'<line class="grid" x1="{ML}" x2="{ML + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                   f'<text x="{ML - 4}" y="{y(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    out.append(f'<text x="{ML - 4}" y="{y(0) + 4:.1f}" text-anchor="end">0</text>')
    now_x = None
    for i, h in enumerate(tl):
        x0 = ML + i * step + (step - bw) / 2
        base, tips = 0.0, []
        bars = []
        for key, _ in TRIGGERS:
            v = h["parts"].get(key, 0)
            if v <= 0:
                continue
            bars.append(f'<rect class="bar t-{key}" x="{x0:.1f}" y="{y(base + v):.1f}" width="{bw:.1f}" height="{y(base) - y(base + v):.1f}"/>')
            tips.append(f"{_LABEL[key]} +{v:.0f}")
            base += v
        t = h["t"]
        lt = _local(t, tz)
        tip = f'{lt.strftime("%a")} {hour_label(t, tz)}: {LEVEL_TEXT[h["level"]]} ({h["score"]:.0f})' + (" - " + "; ".join(tips) if tips else "")
        if h["rain"]:
            tip += " (rain keeps this low: a different problem)"
        out.append(f'<g class="{"past" if t <= now else ""}"><title>{escape(tip)}</title>'
                   f'<rect x="{x0 - 1:.1f}" y="{MT}" width="{bw + 2:.1f}" height="{ph}" fill="transparent"/>{"".join(bars)}</g>')
        if now_x is None and t > now:
            prev = tl[i - 1]["t"] if i else None
            frac = ((now - prev).total_seconds() / (t - prev).total_seconds()) if prev else 0
            now_x = ML + (i - 1 + frac) * step + step / 2 if i else ML
        if lt.hour % 3 == 0:
            cx = ML + i * step + step / 2
            out.append(f'<text x="{cx:.1f}" y="{CH - 18}" text-anchor="middle">{hour_label(t, tz)}</text>')
            if lt.hour == 0:
                out.append(f'<line class="grid" x1="{cx:.1f}" x2="{cx:.1f}" y1="{MT}" y2="{MT + ph}"/>'
                           f'<text x="{cx + 3:.1f}" y="{CH - 4}">{lt.strftime("%a")}</text>')
    if now_x is not None:
        out.append(f'<line class="nowl" x1="{now_x:.1f}" x2="{now_x:.1f}" y1="{MT}" y2="{MT + ph}"/>'
                   f'<text class="nowt" x="{now_x:.1f}" y="{MT - 8}" text-anchor="middle">now</text>')
    out.append("</svg>")
    return "".join(out)


def _why_html(tl, now, tz):
    future = [h for h in tl if h["t"] > now - timedelta(minutes=30)] or tl
    peak = max(future, key=lambda h: h["score"])
    lp = _local(peak["t"], tz)
    when = f"{lp.strftime('%a')} {lp.hour % 12 or 12} {'AM' if lp.hour < 12 else 'PM'}"
    head = (f"Why it may happen around {when}: {LEVEL_TEXT[peak['level']].lower()} ({peak['score']:.0f})"
            if peak["level"] != "none" else f"Closest it gets ({when}): {peak['score']:.0f}, still quiet")
    if peak["why"]:
        items = "<ul>" + "".join(f"<li><b>{escape(_LABEL.get(w[0], w[0]))}:</b> {escape(w[1])}</li>" for w in peak["why"]) + "</ul>"
    else:
        items = '<p class="mute">No triggers firing.</p>'
    rain = '<p class="mute">Rain is expected, which caps the score: wet roads from rain are a different problem.</p>' if peak["rain"] else ""
    return f'<div class="why"><h3>{escape(head)}</h3>{items}{rain}</div>'


def _hero_html(cfg, conn, by_zone, now, tz):
    """The chart at the top of the page: one tab per zone, switched with plain CSS (no JavaScript)."""
    zones = list(cfg["zones"].items())
    thresholds = {k: cfg.get("detector", {}).get(k, d) for k, d in (("watch", 40), ("likely", 60), ("strong", 80))}
    look_back, look_ahead = cfg.get("site", {}).get("chart_back_hours", 12), cfg.get("site", {}).get("chart_ahead_hours", 24)
    radios, tabs, panels, css = [], [], [], []
    for i, (key, z) in enumerate(zones):
        label = z.get("label", key)
        radios.append(f'<input type="radio" name="zone" id="zs{i}"{" checked" if i == 0 else ""}>')
        tabs.append(f'<label for="zs{i}">{escape(label)}</label>')
        css.append(f"#zs{i}:checked~#zp{i}{{display:block}}#zs{i}:checked~.tabs label[for=zs{i}]{{background:var(--btn);color:var(--btnink);border-color:var(--btn)}}"
                   f"#zs{i}:focus-visible~.tabs label[for=zs{i}]{{outline:2px solid var(--c-sat);outline-offset:2px}}")
        tl = engine.timeline(conn, cfg, key, now, look_back, look_ahead)
        now_e = by_zone.get(key, {}).get("now")
        if len(tl) < 3:
            panels.append(f'<div class="panel" id="zp{i}"><p class="mute">No forecast data for {escape(label)} yet.</p></div>')
            continue
        cur = now_e or min(tl, key=lambda h: abs((h["t"] - now).total_seconds()))
        pill = f'<span class="pill lvl-{cur["level"]}">Now: {LEVEL_TEXT[cur["level"]]} · {cur["score"]:.0f}</span>'
        panels.append(f'<div class="panel" id="zp{i}"><div class="top"><h2>{escape(label)}</h2>{pill}</div>'
                      f'{_chart_svg(tl, now, tz, thresholds)}{_why_html(tl, now, tz)}</div>')
    legend = '<div class="legend">' + "".join(f'<span><i class="t-{k}" style="background:var(--c-{c})"></i>{escape(t)}</span>' for (k, t), c in
                                              zip(TRIGGERS, ("sat", "near", "swing", "cool", "calm", "sun"))) + "</div>"
    return (f'<section class="card hero"><style>{"".join(css)}</style><h2>Death fog chance</h2>'
            '<p class="sub">Each bar is one hour. Taller = more likely; colours show what is adding up. '
            'Faded bars are the past 12 hours, the rest is the forecast.</p>'
            f'{"".join(radios)}<div class="tabs">{"".join(tabs)}</div>{"".join(panels)}{legend}'
            '<p class="mute">The score is a rule-of-thumb index from weather data, not a measured probability. '
            '<a href="#how">How it is calculated</a> &middot; <a href="#sources">where the data comes from</a></p></section>')


SOURCE_INFO = {   # what each built-in source type is, shown on the page (plugins fall back to their type name)
    "nws": ("NWS station observations", "https://www.weather.gov/documentation/services-web-api",
            "Real measurements (temperature, dew point, humidity, wind) from {stations}. When fresh, these override the model for the 'right now' score."),
    "openmeteo": ("Open-Meteo forecast model", "https://open-meteo.com/",
                  "Hourly model output at each zone's coordinates (best-match model, NOAA's over North America): temperature, dew point, humidity, "
                  "wind, sunshine, rain. Supplies the forecast, the past 36 hours for the 24-hour swing, and anything the stations lack. CC BY 4.0."),
    "purpleair": ("PurpleAir street sensors", "https://www.purpleair.com/",
                  "Community sensors near the zones: temperature and humidity measured on the block."),
    "goes_fog": ("NOAA GOES-West satellite imagery", "https://www.star.nesdis.noaa.gov/GOES/",
                 "Fog imagery snapshot, for a visual check only."),
}


def _links(cfg):
    repo = (cfg.get("site", {}).get("repo_url") or "").rstrip("/")
    return (lambda path: f"{repo}/{path}") if repo else (lambda path: "")


def _a(url, text):
    return f'<a href="{escape(url, quote=True)}">{escape(text)}</a>' if url else escape(text)


def _sources_html(cfg, conn, now, tz, baseline):
    rows = []
    for src in build_sources(cfg):
        title, url, blurb = SOURCE_INFO.get(src.type_name, (src.type_name, "", "Custom source added as a plugin."))
        blurb = blurb.replace("{stations}", ", ".join(src.cfg.get("stations", [])) or "nearby stations")
        last = db.last_ok(conn, src.name)
        every = src.cfg.get("interval_min")
        meta = ("never fetched yet" if last is None else f"last fetched {local_str(last, tz)}") + (f", every {every} min" if every else "")
        rows.append(f"<li><b>{_a(url, title)}</b>: {escape(blurb)} <span class=\"mute\">({escape(meta)})</span></li>")
    order = cfg.get("state", {}).get("obs_sources", [])
    prio = (f"<p class=\"mute\">Priority: measurements ({escape(', '.join(order))}) win over the model wherever they are fresh; "
            "the model fills in the rest and provides the forecast.</p>") if order else ""
    base = ('<li><b>' + _a("https://open-meteo.com/en/docs/historical-forecast-api", "Open-Meteo past forecasts") + "</b> and <b>"
            + _a("https://mesonet.agron.iastate.edu/request/download.phtml", "Iowa Environmental Mesonet") + "</b>: "
            "the 30-day replay above uses the model's past hourly runs, and checks them against SFO airport observations.</li>")
    return ('<section class="card" id="sources"><h2>Data sources</h2><p class="sub">All free, none need an account.</p><ul>'
            + "".join(rows) + base + "</ul>" + prio +
            '<p class="mute">Model grids are a few km wide, so they cannot see your street: shade, trees and parked cars are '
            "handled by each zone's susceptibility setting and by field reports.</p></section>")


def _how_html(cfg):
    link = _links(cfg)
    det_cfg = cfg.get("detector", {})
    det = build_detector(cfg)
    mod = type(det).__module__
    code_url = link("blob/main/" + mod.replace(".", "/") + ".py") if mod.startswith("deathfog.") else link("tree/main/plugins")
    th = det.params
    pts = getattr(det, "points", None)
    if pts and {"spread_hit_c", "wind_max_mph", "swing_min_c"} <= set(th):
        rows = [
            ("Air near saturation", f"temp - dew point gap at most {th['spread_hit_c']:g} C with humidity at least {th['rh_min']:g}% "
             f"(+{pts['saturation']}); within {th['spread_watch_c']:g} C counts half as much (+{pts['saturation_near']})"),
            ("Big drop from today's high", f"24-hour high minus now is at least {th['swing_min_c']:g} C (+{pts['swing']})"),
            ("Cooling fast", f"temperature fell {th['cool_rate_c_3h']:g} C or more in 3 hours (+{pts['cooling']})"),
            ("Calm wind", f"wind at most {th['wind_max_mph']:g} mph (+{pts['calm']})"),
            ("No sun", f"sunshine under {th['solar_max_wm2']:g} W/m2 (+{pts['no_sun']})"),
            ("Rain", f"more than {th['rain_mm']:g} mm in the last 3 hours caps the score at {getattr(det, 'rain_cap', 20)}: wet roads from rain are a different problem"),
        ]
        table = ("<table><tr><th>Trigger</th><th>Counts when</th></tr>"
                 + "".join(f"<tr><td>{escape(a)}</td><td>{escape(b)}</td></tr>" for a, b in rows) + "</table>")
        susc = ", ".join(f"{escape(z.get('label', k))} x{z.get('susceptibility', 1.0):g}" for k, z in cfg["zones"].items())
        tail = (f"<p>Points add up to 0-100, then are multiplied by each zone's susceptibility ({susc}). "
                f"Bands: Watch {th['watch']:g}+, Likely {th['likely']:g}+, Strong {th['strong']:g}+.</p>")
    else:
        table, tail = '<p class="mute">This detector is a custom plugin; see its code for the rules.</p>', ""
    name = escape(str(det_cfg.get("type", "death_fog_v1")))
    links = " &middot; ".join(x for x in (_a(code_url, f"Detector code ({name})") if code_url else "",
                                          _a(link("tree/main/deathfog/sources"), "Data-source adapters") if link("x") else "",
                                          _a(link("blob/main/config.toml"), "All thresholds and zones") if link("x") else "") if x)
    return ('<section class="card" id="how"><h2>How detection works</h2>'
            "<p class=\"sub\">Condensation forms on a road when its surface cools to the dew point. Roads lose heat fastest on calm, "
            "clear nights after a warm day, so the detector looks for those conditions.</p>"
            f"{table}{tail}" + (f'<p>{links}</p>' if links else "") +
            '<p class="mute">Thresholds are educated guesses, tuned against field reports as they come in.</p></section>')


def _reasons_html(e):
    hits = [r["detail"] for r in e["reasons"] if r["hit"] and r["points"]]
    if not hits:
        return '<p class="mute">No triggers firing.</p>'
    return "<ul>" + "".join(f"<li>{escape(h)}</li>" for h in hits) + "</ul>"


def _zone_card(label, now_e, ahead_e, tz):
    if not now_e and not ahead_e:
        return f'<section class="card"><h2>{escape(label)}</h2><p class="mute">No data yet.</p></section>'
    head = now_e or ahead_e
    pill = f'<span class="pill lvl-{head["level"]}">{LEVEL_TEXT[head["level"]]} · {head["score"]:.0f}</span>'
    body = [f'<div class="top"><h2>{escape(label)}</h2>{pill}</div>']
    if now_e:
        src = ", ".join(sorted(set(now_e["provenance"].values())))
        body.append(f'<p class="sub">Right now ({escape(now_e["inputs"]["basis"])}; data from {escape(src)})</p>')
        body.append(_reasons_html(now_e))
    if ahead_e and ahead_e.get("hourly"):
        peak = ahead_e
        body.append(f'<p class="sub">Next 12 hours: peak {LEVEL_TEXT[peak["level"]].lower()} ({peak["score"]:.0f}) around '
                    f'{escape(local_str(parse_iso(peak["valid_utc"]), tz))}</p><div class="strip">')
        for h in ahead_e["hourly"]:
            t = parse_iso(h["valid_utc"])
            body.append(f'<span class="blk lvl-{h["level"]}" title="{escape(local_str(t, tz))}: {LEVEL_TEXT[h["level"]]} ({h["score"]:.0f})">'
                        f'{hour_label(t, tz)}</span>')
        body.append("</div>")
    return '<section class="card">' + "".join(body) + "</section>"


def _baseline_html(b, tz):
    if not b:
        return '<section class="card"><h2>Last 30 days</h2><p class="mute">Baseline not built yet.</p></section>'
    rows = []
    for z in b["zones"].values():
        start = z["typical_start_hour_local"]
        when = "n/a" if start is None else f"~{start % 12 or 12}{'am' if start < 12 else 'pm'}"
        rows.append(f"<tr><td>{escape(z['label'])}</td><td>{z['episodes']}</td><td>{z['episodes_per_week']}</td>"
                    f"<td>{when}</td><td>{z['pct_of_hours']['likely'] + z['pct_of_hours']['strong']:.0f}%</td></tr>")
    return ('<section class="card"><h2>Last 30 days (replay)</h2>'
            f'<p class="sub">{escape(b["start"])} to {escape(b["end"])}: how often the detector would have flagged '
            "death-fog conditions using past model data.</p><table><tr><th>Zone</th><th>Events</th><th>Per week</th>"
            "<th>Usually starts</th><th>Hours flagged</th></tr>" + "".join(rows) + "</table>"
            '<p class="mute"><a href="baseline.json">full detail</a></p></section>')


def _verify_html(conn):
    n = conn.execute("SELECT COUNT(*) c FROM reports").fetchone()["c"]
    v = reports.verify(conn, days=30)
    t = v["total"]
    scored = t["tp"] + t["fp"] + t["fn"] + t["tn"]
    if scored < 5:
        txt = f"{n} field reports so far. About 20 are needed before accuracy numbers mean anything."
    else:
        pct = lambda x: "n/a" if x is None else f"{100 * x:.0f}%"
        txt = (f"{n} field reports; of {scored} checked, {t['tp']} hits, {t['fn']} misses, {t['fp']} false alarms "
               f"(precision {pct(t['precision'])}, recall {pct(t['recall'])}).")
    return f'<section class="card"><h2>How accurate is it?</h2><p>{escape(txt)}</p></section>'


def render(cfg, conn, evals, baseline, now):
    tz = cfg["general"].get("timezone", "America/Los_Angeles")
    stale_min = cfg.get("site", {}).get("stale_min", 60)
    last = [t for t in (db.last_ok(conn, s.name) for s in build_sources(cfg)) if t]
    newest = max(last) if last else None
    banner = ""
    if newest is None or now - newest > timedelta(minutes=stale_min):
        banner = ('<div class="banner">Data is stale: the collector has not fetched fresh weather recently, '
                  "so the scores below may be out of date.</div>")
    by_zone = {}
    for e in evals:
        by_zone.setdefault(e["zone"], {})[e["mode"]] = e
    hero = _hero_html(cfg, conn, by_zone, now, tz)
    cards = "".join(_zone_card(z.get("label", k), by_zone.get(k, {}).get("now"), by_zone.get(k, {}).get("ahead"), tz)
                    for k, z in cfg["zones"].items())
    form = cfg["general"].get("report_url", "")
    cta = (f'<a class="btn" href="{escape(form, quote=True)}">Log what you see (20 seconds)</a>' if form else
           '<p class="mute">The field-report form link has not been set up yet.</p>')
    updated = "never" if newest is None else local_str(newest, tz)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300"><title>SF Death Fog Watch</title><style>{CSS}</style></head><body><main>
<h1>SF Death Fog Watch</h1><p class="sub">Slippery-road mist risk for skaters. Weather data last fetched {escape(updated)}. Updates about every 10 minutes.</p>
{banner}{hero}{cards}{cta}{_baseline_html(baseline, tz)}{_verify_html(conn)}{_how_html(cfg)}{_sources_html(cfg, conn, now, tz, baseline)}
<footer>Experimental hobby tool, not safety advice: judge the road yourself. Scores come from weather data, not from looking
at your street. Weather data: <a href="https://open-meteo.com/">Open-Meteo.com</a> (CC BY 4.0), NOAA National Weather Service,
Iowa Environmental Mesonet.</footer></main></body></html>"""


def write_site(cfg, conn, now, out_dir, baseline_path=None):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    baseline = None
    if baseline_path and pathlib.Path(baseline_path).exists():
        baseline = json.loads(pathlib.Path(baseline_path).read_text())
        (out / "baseline.json").write_text(json.dumps(baseline, indent=1))
    evals = engine.evaluate_all(conn, cfg, now)
    (out / "index.html").write_text(render(cfg, conn, evals, baseline, now), encoding="utf-8")
    status = {"generated_utc": iso(now), "zones": [
        {"zone": e["zone"], "mode": e["mode"], "level": e["level"], "score": round(e["score"], 1), "valid_utc": e["valid_utc"],
         "reasons": [r["detail"] for r in e["reasons"] if r["hit"] and r["points"]]} for e in evals]}
    (out / "status.json").write_text(json.dumps(status, indent=1))
    return out / "index.html"
