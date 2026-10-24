#!/usr/bin/env python3
"""Colour palettes shared by every chart and web page (stdlib only).

The colour rule is the same everywhere:

* ``over``     -- a warm colour (orange in the default): above the baseline / pace, and nothing else
* ``under``    -- the palette's cool colour (teal-green, blue, purple, mint...), also the accent colour
* ``baseline`` -- neutral grey line; it is a reference, not a state
* ``notice``   -- lavender for "no data / unavailable / stale" notices (never orange)

Deltas are always printed with an arrow as well (``▲`` over, ``▼`` under / on pace) so the
state never depends on colour alone. Over is never shown in red.

Presets (``palette`` in the config, ``--palette`` on the CLI), 17 in all: orange-teal (default),
orange-blue, orange-deepblue, amber-purple, pink-green, gold-sky, orange-purple, magenta-teal,
yellow-indigo, peach-mint, tangerine-cyan, rose-lime, sunflower-navy, high-contrast, safety-ultramarine,
tangerine-violet, amber-cobalt. ``CVD_SAFE`` marks the ones (★ in the menu) whose over / under / baseline stay
clearly apart (CIEDE2000 >= 20) under simulated protanopia, deuteranopia, tritanopia, protanomaly and
deuteranomaly (Machado 2009), for both the dark and the light variant.
The cool ``under`` colour doubles as the page accent (``accent()``), so a switch recolours everything.
"""

DEFAULT = "orange-teal"

# each preset has a variant for dark and for light backgrounds. "over" is always a warm colour (orange,
# amber, gold, yellow, peach, pink/magenta), never red, brown, grey, black or aqua; "under" is the cool
# colour and also the page accent (frame, tabs, eyebrow text, focus rings); "notice" differs from both.
_B, _BL = "#9AA4AE", "#6B7480"  # baseline (dark / light background)


def _p(over, under, notice, l_over, l_under, l_notice):
    return {"dark": {"over": over, "under": under, "baseline": _B, "notice": notice},
            "light": {"over": l_over, "under": l_under, "baseline": _BL, "notice": l_notice}}


PALETTES = {
    "orange-teal": _p("#FFA51F", "#23C4AE", "#B48CFF", "#C96A00", "#0B8A7E", "#7A4FC9"),
    "orange-blue": _p("#FFA51F", "#4D8DFF", "#B48CFF", "#C96A00", "#1F5FD1", "#7A4FC9"),
    # Okabe & Ito orange with a deep blue: #3171C4 on dark backgrounds (3.9:1 on the cards, >= 20 from the grey
    # baseline in every simulated colour vision), a darker navy #1D3972 on light ones (the light baseline is darker)
    "orange-deepblue": _p("#E69F00", "#3171C4", "#CC79A7", "#B87F00", "#1D3972", "#A8558A"),
    "amber-purple": _p("#FFB000", "#9B7BFF", "#F28CC8", "#B37A00", "#6A4BD6", "#B0487F"),
    "pink-green": _p("#FF5FA2", "#3DDC84", "#B48CFF", "#C2306E", "#138A4C", "#7A4FC9"),
    "gold-sky": _p("#FFD23F", "#5EC8FF", "#C59BFF", "#9A7400", "#0A78B8", "#7A4FC9"),
    "orange-purple": _p("#FF8C1A", "#B07CFF", "#7FD8C8", "#C25E00", "#7A45D6", "#1F8A7A"),
    "magenta-teal": _p("#FF4FD8", "#2EC4B6", "#A8B4FF", "#B8269A", "#0E857A", "#4F5FC9"),
    "yellow-indigo": _p("#F5E14B", "#6C7BFF", "#FF9ECF", "#8F7F00", "#3E4CD1", "#A8558A"),
    "peach-mint": _p("#FFB38A", "#7FE0B0", "#B48CFF", "#C0602F", "#1E8A5A", "#7A4FC9"),
    "tangerine-cyan": _p("#FF8A3D", "#3FD0FF", "#C59BFF", "#C55A12", "#0A7FAF", "#7A4FC9"),
    "rose-lime": _p("#FF6FB5", "#A8E063", "#9EC5FF", "#C23A7E", "#4E8A12", "#3F64C2"),
    "sunflower-navy": _p("#FFC93C", "#3A6BD9", "#E59BFF", "#9C7200", "#00219E", "#A8558A"),
    "high-contrast": _p("#FFFF00", "#00BFFF", "#FF9BFF", "#7A6E00", "#005F99", "#9A2FA0"),
    "safety-ultramarine": _p("#FF832B", "#2A68DE", "#B48CFF", "#EB6100", "#002599", "#7A4FC9"),
    "tangerine-violet": _p("#FF9100", "#6C5CE7", "#F28CC8", "#CC7400", "#3814B8", "#B0487F"),
    "amber-cobalt": _p("#EE7733", "#0077BB", "#C59BFF", "#EB5500", "#003A66", "#7A4FC9"),
}

# over / under / grey baseline at least CIEDE2000 20 apart in normal vision and simulated protanopia,
# deuteranopia, tritanopia, protanomaly 0.6 and deuteranomaly 0.6 (Machado 2009), dark and light variants
CVD_SAFE = {"orange-deepblue", "sunflower-navy", "safety-ultramarine", "tangerine-violet", "amber-cobalt"}
STAR = "★"

LABELS = {
    "orange-teal": {"en": "Orange / teal", "zh-TW": "橘／青綠", "zh-CN": "橙／青绿"},
    "orange-blue": {"en": "Orange / blue", "zh-TW": "橘／藍", "zh-CN": "橙／蓝"},
    "orange-deepblue": {"en": "Orange / deep blue", "zh-TW": "橘／深藍", "zh-CN": "橙／深蓝"},
    "amber-purple": {"en": "Amber / purple", "zh-TW": "琥珀／紫", "zh-CN": "琥珀／紫"},
    "pink-green": {"en": "Pink / green", "zh-TW": "桃紅／綠", "zh-CN": "桃红／绿"},
    "gold-sky": {"en": "Gold / sky blue", "zh-TW": "金／天藍", "zh-CN": "金／天蓝"},
    "orange-purple": {"en": "Orange / purple", "zh-TW": "橘／紫", "zh-CN": "橙／紫"},
    "magenta-teal": {"en": "Magenta / teal", "zh-TW": "洋紅／青綠", "zh-CN": "洋红／青绿"},
    "yellow-indigo": {"en": "Yellow / indigo", "zh-TW": "黃／靛藍", "zh-CN": "黄／靛蓝"},
    "peach-mint": {"en": "Peach / mint", "zh-TW": "蜜桃／薄荷", "zh-CN": "蜜桃／薄荷"},
    "tangerine-cyan": {"en": "Tangerine / cyan", "zh-TW": "橘紅／青", "zh-CN": "橘红／青"},
    "rose-lime": {"en": "Rose / lime", "zh-TW": "玫瑰／萊姆", "zh-CN": "玫瑰／莱姆"},
    "sunflower-navy": {"en": "Sunflower / navy", "zh-TW": "向日葵／深藍", "zh-CN": "向日葵／深蓝"},
    "high-contrast": {"en": "High contrast", "zh-TW": "高對比", "zh-CN": "高对比"},
    "safety-ultramarine": {"en": "Safety orange / ultramarine", "zh-TW": "安全橘／群青", "zh-CN": "安全橙／群青"},
    "tangerine-violet": {"en": "Tangerine / violet", "zh-TW": "亮橘／紫羅蘭", "zh-CN": "亮橙／紫罗兰"},
    "amber-cobalt": {"en": "Amber orange / cobalt", "zh-TW": "琥珀橘／鈷藍", "zh-CN": "琥珀橙／钴蓝"},
}

# one neutral line under the menu explaining the ★
STAR_NOTE = {"en": "★ = stronger contrast, easy to tell apart for every kind of colour vision",
             "zh-TW": "★ = 對比較強，各種色覺都容易分辨", "zh-CN": "★ = 对比较强，各种色觉都容易分辨"}


def cvd_safe(name=None):
    return normalize(name) in CVD_SAFE


def label(name, lang="en"):
    """Menu label: the plain name plus ' ★' for the CVD_SAFE presets."""
    n = normalize(name)
    lab = LABELS[n].get(lang) or LABELS[n]["en"]
    return lab + (" " + STAR if n in CVD_SAFE else "")

UP, DOWN = "▲", "▼"


# Optional non-colour cues for any palette ("Add patterns" on the page, ``patterns`` in the config or a theme file):
# hatching on "over" bars and areas, dashed "over" line segments, ▲/▼ markers and symbols, striped "over" badges.
# Charts with patterns are drawn into their own folder, history/pal/<palette>-pat/ (see key()).
HATCH = "///"
PAT_SUFFIX = "-pat"


def key(name, patterns=False):
    """Folder / cache key of a palette, with or without patterns: 'orange-teal' or 'orange-teal-pat'."""
    return normalize(name) + (PAT_SUFFIX if patterns else "")


def split_key(k):
    """('orange-teal', True) for 'orange-teal-pat'; unknown names fall back to the default palette."""
    k = str(k or "")
    pat = k.endswith(PAT_SUFFIX) and k[: -len(PAT_SUFFIX)] in PALETTES
    return normalize(k[: -len(PAT_SUFFIX)] if pat else k), bool(pat)


def is_key(k):
    k = str(k or "")
    return k in PALETTES or (k.endswith(PAT_SUFFIX) and k[: -len(PAT_SUFFIX)] in PALETTES)


def names():
    return list(PALETTES)


def normalize(name):
    """Return a known preset name; unknown or empty values fall back to the default."""
    n = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {"default": DEFAULT, "teal": "orange-teal", "blue": "orange-blue",
               # earlier name of orange-deepblue (configs, theme files and saved browser choices still work)
               "colorblind": "orange-deepblue", "okabe-ito": "orange-deepblue", "color-blind": "orange-deepblue",
               "colourblind": "orange-deepblue", "colour-blind": "orange-deepblue", "cb": "orange-deepblue"}
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


def blend(c1, c2, t):
    """Mix two #RRGGBB colours (t=0 -> c1, t=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]; b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


# Period colours for the clock chart (one per day / week / month / quarter): distinct hues for dark and light
# backgrounds. period_colors() drops hues close to the palette's warm "over" colour so a period never reads as
# "over"; orange-deepblue uses the Okabe & Ito set instead.
_CAT = {"dark": ["#4E9BFF", "#3DDC84", "#C77DFF", "#FF6FB5", "#2EC4B6", "#F5E14B", "#FF8C42", "#A6B1BC", "#8FD3FF", "#B6E36B", "#E8EEF4"],
        "light": ["#1F5FD1", "#138A4C", "#7A3FC4", "#C2306E", "#0E857A", "#8F7F00", "#C25E00", "#5B6570", "#2B8CC4", "#5E8A12", "#22272D"]}
_CAT_CB = {"dark": ["#56B4E9", "#009E73", "#F0E442", "#CC79A7", "#0072B2", "#D55E00", "#FFFFFF", "#999999"],
           "light": ["#0072B2", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#9A8A00", "#000000", "#777777"]}


def _hsv(h):
    import colorsys
    return colorsys.rgb_to_hsv(*(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)))


def _near(a, b, lim):
    (ha, sa, _), (hb, sb, _) = _hsv(a), _hsv(b)
    if sa < 0.2 or sb < 0.2:  # greys / near-white never clash by hue
        return False
    d = abs(ha - hb) * 360
    return min(d, 360 - d) < lim


def period_colors(name=None, light=False, n=8):
    """Up to n clearly different colours for the clock chart's periods, the palette's cool colour first."""
    key = "light" if light else "dark"
    c = get(name, light)
    if normalize(name) == "orange-deepblue":
        return _CAT_CB[key][:n]
    out = [c["under"]]
    for x in _CAT[key]:
        if not _near(x, c["over"], 28) and not any(_near(x, y, 22) for y in out):
            out.append(x)
    if len(out) < n:  # not enough left: allow ones near another period colour back, never near "over"
        out += [x for x in _CAT[key] if x not in out and not _near(x, c["over"], 28)]
    return out[:n]


def accent(name=None, light=False):
    """Accent for frames, tabs and headings: the palette's cool (under) colour."""
    return get(name, light)["under"]


def js_table():
    """JSON-serialisable table for the browser palette switcher."""
    return {k: {"dark": v["dark"], "light": v["light"], "cvd_safe": k in CVD_SAFE} for k, v in PALETTES.items()}
