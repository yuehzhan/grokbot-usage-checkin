"""Display offset for the weekly %: a whole-number correction (-2..+2) added when values are shown.

Why: the Grok desktop app can show the weekly % about 1 point higher than `/usage` for the same moment (the two
round a fractional value differently). `display_offset` lets the charts and pages match whichever one you look at.
Stored samples and week CSVs always stay raw; only shown / calculated values get the offset. Differences between
two readings (check-in start/end, hourly increases) are taken from raw values, where the offset cancels anyway.
"""

MIN, MAX = -2, 2


def clamp(v):
    """Config / request value -> int in -2..+2 (anything unreadable -> 0)."""
    if isinstance(v, bool):
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f != f:  # NaN
        return 0
    return max(MIN, min(MAX, int(round(f))))


def apply(pct, off):
    """Shown value: raw + offset, never below 0 (no upper cap: over 100 % stays visible). None stays None."""
    if pct is None:
        return None
    off = clamp(off)
    return max(0.0, float(pct) + off) if off else float(pct)


def tag(off):
    """Small neutral marker next to the main %: '(+1)' / '(−1)', '' for 0."""
    off = clamp(off)
    return "" if not off else (f"(+{off})" if off > 0 else f"(\u2212{-off})")


def option_text(off, lang="en"):
    """Menu text: −2, −1, +0, +1, +2 (plain numbers in every language; the gap to the desktop app is not constant)."""
    off = clamp(off)
    return f"+{off}" if off >= 0 else f"\u2212{-off}"


def suffix(off):
    """Cache-folder suffix: '' for 0, '-o+1' / '-o-2' otherwise (part of the image cache key)."""
    off = clamp(off)
    return "" if not off else f"-o{off:+d}"


def from_settings(cfg, data_dir=None):
    """Offset from the config dict, else from <data_dir>/page_settings.json (written by the dashboard page)."""
    v = (cfg or {}).get("display_offset")
    if v is None and data_dir:
        import json
        from pathlib import Path
        try:
            d = json.loads((Path(data_dir) / "page_settings.json").read_text(encoding="utf-8"))
            v = d.get("display_offset") if isinstance(d, dict) else None
        except (OSError, ValueError):
            v = None
    return clamp(v)
