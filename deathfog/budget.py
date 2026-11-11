"""`estimate`: what a polling schedule costs against each provider's free limits."""
from .collect import build_sources

PURPLEAIR_FREE_POINTS = 1_000_000


def estimate(cfg):
    rows = []
    for src in build_sources(cfg):
        interval = src.cfg.get("interval_min", 10)
        runs = 1440 / interval
        cost = src.cost()
        daily = runs * cost
        row = {"source": src.name, "interval_min": interval, "runs_per_day": runs,
               "cost_per_fetch": cost, "daily": daily, "limit": src.free_daily_limit, "note": ""}
        if src.free_daily_limit:
            row["pct_of_limit"] = 100.0 * daily / src.free_daily_limit
        if src.type_name == "purpleair" and daily:
            row["note"] = f"{PURPLEAIR_FREE_POINTS / (daily * 30):.1f} months of the free 1M points; then ~${daily * 30 / 100_000:.2f}/month"
        if src.type_name == "nws":
            row["note"] = "no published cap; ~1 request/second guidance"
        rows.append(row)
    return rows


def format_estimate(rows):
    lines = [f"{'source':<12}{'every':>6}{'runs/day':>10}{'cost/run':>10}{'per day':>10}{'% of free':>11}  note"]
    for r in rows:
        pct = f"{r['pct_of_limit']:.1f}%" if "pct_of_limit" in r else "-"
        lines.append(f"{r['source']:<12}{r['interval_min']:>5}m{r['runs_per_day']:>10.0f}{r['cost_per_fetch']:>10.1f}"
                     f"{r['daily']:>10.0f}{pct:>11}  {r['note']}")
    return "\n".join(lines)
