import math
from datetime import datetime, timezone

ISO = "%Y-%m-%dT%H:%M:%SZ"


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime(ISO)


def parse_iso(s):
    """Parse ISO-8601 text; naive values are treated as UTC."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _zone(tzname):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tzname)
    except Exception:  # tzdata missing (common on Windows) -> fall back to UTC
        return timezone.utc


def parse_local(s, tzname):
    """Parse a timestamp typed by a person (naive = local time in tzname) into UTC."""
    s = s.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError(f"unrecognised timestamp: {s!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(tzname))
    return dt.astimezone(timezone.utc)


def local_str(dt, tzname):
    d = dt.astimezone(_zone(tzname))
    return f"{d:%a} {d.hour % 12 or 12}:{d:%M} {d:%p}"


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def kmh_to_mph(v):
    return v * 0.621371


def dew_point_c(t_c, rh_pct):
    """Magnus approximation. Returns None when inputs are unusable."""
    if t_c is None or rh_pct is None or rh_pct <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(rh_pct / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def knots_to_mph(v):
    return v * 1.15078


def in_to_mm(v):
    return v * 25.4


def local_hour(dt, tzname):
    return dt.astimezone(_zone(tzname)).hour


def hour_label(dt, tzname):
    h = local_hour(dt, tzname)
    return f"{h % 12 or 12}{'a' if h < 12 else 'p'}"


def c_to_f(c):
    """Absolute temperature, Celsius -> Fahrenheit (the page and alerts show Fahrenheit)."""
    return c * 9 / 5 + 32


def dc_to_f(dc):
    """A temperature *difference* in Celsius degrees -> Fahrenheit degrees (no +32)."""
    return dc * 9 / 5
