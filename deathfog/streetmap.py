"""Draw the top-down street map as inline SVG (no tiles, no external requests). North is up."""
import math
from html import escape

from .streets import band

SIZE = 480


def _project(radius_m):
    k = (SIZE / 2) / radius_m
    return lambda lat, lon, lat0, lon0: (
        SIZE / 2 + (lon - lon0) * math.cos(math.radians(lat0)) * 111320.0 * k,
        SIZE / 2 - (lat - lat0) * 110540.0 * k)


def map_svg(segs, st, center, radius_m, uid, top, marks=()):
    lat0, lon0 = center
    proj = _project(radius_m)
    k = (SIZE / 2) / radius_m
    drawn = {"none": [], "low": [], "mid": [], "hi": []}
    south = []
    for s in segs:
        x1, y1 = proj(s["a"][0], s["a"][1], lat0, lon0)
        x2, y2 = proj(s["b"][0], s["b"][1], lat0, lon0)
        b = band(s["risk"], st)
        why = [("runs east-west" if s["ew"] >= 0.75 else "runs north-south" if s["ew"] <= 0.35 else "runs diagonally")]
        if s["trees100"] >= 2:
            why.append(f"~{s['trees100']:.0f} trees/100 m")
        if s["parking"] == "yes":
            why.append("parked cars")
        if s["porous"]:
            why.append("porous surface")
        tip = f"{s['name'] or 'Unnamed street'}: {', '.join(why)}"
        drawn[b].append((x1, y1, x2, y2, tip))
        if s["ew"] >= 0.8 and b in ("mid", "hi"):
            south.append((x1, y1 + 4.5, x2, y2 + 4.5))
    out = [f'<svg class="map" viewBox="0 0 {SIZE} {SIZE}" role="img" aria-label="Top-down street map, north up, '
           f'with the streets most likely to get death fog in red">',
           f'<defs><clipPath id="mc{uid}"><rect width="{SIZE}" height="{SIZE}" rx="10"/></clipPath></defs>',
           f'<rect class="mbg" width="{SIZE}" height="{SIZE}" rx="10"/><g clip-path="url(#mc{uid})">']
    if drawn["none"]:       # the many grey streets are one path with no tooltips: keeps the page small
        d = "".join(f"M{x1:.0f} {y1:.0f}L{x2:.0f} {y2:.0f}" for x1, y1, x2, y2, _ in drawn["none"])
        out.append(f'<path class="ms-none" fill="none" d="{d}"/>')
    for b in ("low", "mid", "hi"):
        for x1, y1, x2, y2, tip in drawn[b]:
            out.append(f'<line class="ms-{b}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"><title>{escape(tip)}</title></line>')
    for x1, y1, x2, y2 in south:
        out.append(f'<line class="msouth" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    # labels for the top streets, placed on their longest stretch inside the frame
    for t in top[:4]:
        best, best_len = None, 0
        for s in segs:
            if s["name"] != t["name"]:
                continue
            x1, y1 = proj(s["a"][0], s["a"][1], lat0, lon0)
            x2, y2 = proj(s["b"][0], s["b"][1], lat0, lon0)
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if 30 < mx < SIZE - 30 and 20 < my < SIZE - 20 and s["len"] > best_len:
                best, best_len = (mx, my, math.degrees(math.atan2(y2 - y1, x2 - x1))), s["len"]
        if best:
            mx, my, ang = best
            ang = ang + 180 if ang > 90 else ang - 180 if ang <= -90 else ang
            out.append(f'<text class="ml" x="{mx:.1f}" y="{my - 5:.1f}" text-anchor="middle" '
                       f'transform="rotate({ang:.0f} {mx:.1f} {my:.1f})">{escape(t["name"])}</text>')
    out.append("</g>")
    for m in marks:                       # named spots, e.g. where skaters have seen the road stay dry
        x, y = proj(m["lat"], m["lon"], lat0, lon0)
        if 12 < x < SIZE - 12 and 12 < y < SIZE - 12:
            anchor, dx = ("end", -10) if x > SIZE * 0.55 else ("start", 10)
            out.append(f'<circle class="mmark" cx="{x:.1f}" cy="{y:.1f}" r="7"/><text class="ml" x="{x + dx:.1f}" y="{y + 4:.1f}" '
                       f'text-anchor="{anchor}">{escape(m.get("label", ""))}</text>')
    c = SIZE / 2
    out.append(f'<circle class="mcenter" cx="{c}" cy="{c}" r="5"/>')
    bar = 200 * k
    out.append(f'<line class="mscale" x1="14" y1="{SIZE - 16}" x2="{14 + bar:.1f}" y2="{SIZE - 16}"/>'
               f'<text class="mt" x="14" y="{SIZE - 22}">200 m</text>'
               f'<text class="mt" x="{SIZE - 22}" y="22" text-anchor="middle">N</text>'
               f'<path class="mnorth" d="M{SIZE - 22} 28 l-5 12 h10 z"/></svg>')
    return "".join(out)


def legend_html():
    items = (("hi", "Most likely"), ("mid", "Likely"), ("low", "Possible"), ("none", "Less likely"))
    return ('<div class="legend">' + "".join(f'<span><i class="mk-{b}"></i>{t}</span>' for b, t in items)
            + '<span><i class="mk-south"></i>South side of the street</span></div>')
