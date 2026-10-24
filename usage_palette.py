#!/usr/bin/env python3
"""Colour palettes shared by every chart and web page (stdlib only).

The colour rule is the same everywhere:

* ``over``     -- orange: above the baseline / pace (the ONLY meaning of orange)
* ``under``    -- the palette's cool colour (teal-green, blue, or colour-blind safe blue)
* ``baseline`` -- neutral grey line; it is a reference, not a state
* ``notice``   -- lavender for "no data / unavailable / stale" notices (never orange)

Deltas are always printed with an arrow as well (``▲`` over, ``▼`` under / on pace) so the
state never depends on colour alone. Over is never shown in red.

Presets (``palette`` in the config, ``--palette`` on the CLI):
  orange-teal  (default) orange over, blue-leaning teal-green under
  orange-blue            orange over, blue under
  colorblind             Okabe-Ito orange over, Okabe-Ito sky blue / blue under
"""

DEFAULT = "orange-teal"

# each preset has a variant for dark and for light backgrounds
PALETTES = {
    "orange-teal": {
        "dark": {"over": "#FFA51F", "under": "#23C4AE", "baseline": "#9AA4AE", "notice": "#B48CFF"},
        "light": {"over": "#C96A00", "under": "#0B8A7E", "baseline": "#6B7480", "notice": "#7A4FC9"},
    },
    "orange-blue": {
        "dark": {"over": "#FFA51F", "under": "#4D8DFF", "baseline": "#9AA4AE", "notice": "#B48CFF"},
        "light": {"over": "#C96A00", "under": "#1F5FD1", "baseline": "#6B7480", "notice": "#7A4FC9"},
    },
    "colorblind": {  # Okabe & Ito (2008) colour-universal-design set
        "dark": {"over": "#E69F00", "under": "#56B4E9", "baseline": "#9AA4AE", "notice": "#CC79A7"},
        "light": {"over": "#B87F00", "under": "#0072B2", "baseline": "#6B7480", "notice": "#A8558A"},
    },
}

LABELS = {
    "orange-teal": {"en": "Orange / teal", "zh-TW": "橘／青綠", "zh-CN": "橙／青绿"},
    "orange-blue": {"en": "Orange / blue", "zh-TW": "橘／藍", "zh-CN": "橙／蓝"},
    "colorblind": {"en": "Colour-blind safe", "zh-TW": "色盲友善", "zh-CN": "色盲友好"},
}

UP, DOWN = "▲", "▼"


def names():
    return list(PALETTES)


def normalize(name):
    """Return a known preset name; unknown or empty values fall back to the default."""
    n = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {"default": DEFAULT, "teal": "orange-teal", "blue": "orange-blue",
               "okabe-ito": "colorblind", "color-blind": "colorblind", "colourblind": "colorblind",
               "cb": "colorblind"}
    n = aliases.get(n, n)
    return n if n in PALETTES else DEFAULT


def get(name=None, light=False):
    """Colours of a preset as a dict: over, under, baseline, notice."""
    return dict(PALETTES[normalize(name)]["light" if light else "dark"])


def arrow(delta):
    """▲ when above the baseline, ▼ when at or below it."""
    try:
        return UP if float(delta) > 0 else DOWN
    except (TypeError, ValueError):
        return ""


def fmt_delta(delta, fmt="{:+.0f}%"):
    """'▲ +5%' / '▼ -3%'."""
    return f"{arrow(delta)} {fmt.format(delta)}"


def css_vars(name=None, light=False):
    p = get(name, light)
    return ";".join(f"--{k}:{v}" for k, v in p.items())


def js_table():
    """JSON-serialisable table for the browser palette switcher."""
    return {k: {"dark": v["dark"], "light": v["light"]} for k, v in PALETTES.items()}
