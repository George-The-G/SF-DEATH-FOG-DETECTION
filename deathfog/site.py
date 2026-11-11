"""Static status page (no JavaScript, no external requests) + status.json, published to GitHub Pages."""
import json
import pathlib
from datetime import timedelta
from html import escape

from . import db, engine, reports
from .collect import build_sources
from .util import hour_label, iso, local_hour, local_str, parse_iso

LEVEL_TEXT = {"none": "Quiet", "watch": "Watch", "likely": "Likely", "strong": "Strong"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202a;--mute:#5b6773;--line:#dde2e8;--none:#d9e4dc;--watch:#f4e3a1;--likely:#f6b26b;--strong:#e06666;--btn:#16202a;--btnink:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1419;--card:#1a222b;--ink:#e8edf2;--mute:#9aa7b4;--line:#2c3743;--none:#2a3a30;--watch:#6b5d1f;--likely:#8a5a1f;--strong:#8f2d2d;--btn:#e8edf2;--btnink:#0f1419}}
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
footer{font-size:.8rem;color:var(--mute);margin:18px 0}
"""


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
    cards = "".join(_zone_card(z.get("label", k), by_zone.get(k, {}).get("now"), by_zone.get(k, {}).get("ahead"), tz)
                    for k, z in cfg["zones"].items())
    form = cfg["general"].get("report_url", "")
    cta = (f'<a class="btn" href="{escape(form, quote=True)}">Log what you see (20 seconds)</a>' if form else
           '<p class="mute">The field-report form link has not been set up yet.</p>')
    updated = "never" if newest is None else local_str(newest, tz)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300"><title>SF Death Fog Watch</title><style>{CSS}</style></head><body><main>
<h1>SF Death Fog Watch</h1><p class="sub">Slippery-road mist risk for skaters. Weather data last fetched {escape(updated)}. Updates about every 10 minutes.</p>
{banner}{cards}{cta}{_baseline_html(baseline, tz)}{_verify_html(conn)}
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
