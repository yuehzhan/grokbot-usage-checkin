#!/usr/bin/env python3
"""Colour palettes shared by every chart and web page (stdlib only).

The colour rule is the same everywhere:

* ``over``     -- a warm colour (orange in the default): above the baseline / pace, and nothing else
* ``under``    -- the palette's cool colour (teal-green, blue, purple, mint...), also the accent colour
* ``baseline`` -- neutral grey line; it is a reference, not a state
* ``notice``   -- lavender for "no data / unavailable / stale" notices (never orange)

Deltas are always printed with an arrow as well (``▲`` over, ``▼`` under / on pace) so the
state never depends on colour alone. Over is never shown in red.

Presets (``palette`` in the config, ``--palette`` on the CLI), 21 in the menu: orange-teal (default),
orange-blue, orange-deepblue, amber-purple, pink-green, gold-sky, orange-purple, magenta-teal,
yellow-indigo, peach-mint, tangerine-cyan, rose-lime, sunflower-navy, safety-ultramarine,
tangerine-violet, amber-cobalt, and the solid-colour section teal-mono, pink-mono, purple-mono, blue-mono,
green-mono; plus ``custom`` (two colours of your own). ``high-contrast`` is a legacy key: still accepted, not in the menu. ``CVD_SAFE`` marks the ones (★ in the menu) whose over / under / baseline stay
clearly apart (CIEDE2000 >= 20) under simulated protanopia, deuteranopia, tritanopia, protanomaly and
deuteranomaly (Machado 2009), for both the dark and the light variant.
The solid-colour presets (MONO) use one hue for both states: over = bright light shade, under = deep muted shade,
so they differ by lightness only and the ▲ / ▼ tags and the optional patterns carry the state as well.
The cool ``under`` colour doubles as the page accent (``accent()``), so a switch recolours everything.
"""

DEFAULT = "orange-teal"

# each preset has a variant for dark and for light backgrounds. "over" is always a warm colour (orange,
# amber, gold, yellow, peach, pink/magenta) -- in the solid-colour presets the brighter, lighter shade --
# never red, brown, grey, black or aqua; "under" is the cool
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
    # solid colours (MONO, their own section below the ★ note in the menu): over = bright light shade, under = deep muted shade
    # of the same hue; they differ by lightness only (over / under contrast >= 3:1 on both backgrounds), so the
    # ▲ / ▼ tags and the optional patterns carry the state as well. The notice colour is a different hue.
    "teal-mono": _p("#8FF5E4", "#178A7D", "#B48CFF", "#109C8B", "#07443E", "#7A4FC9"),
    "pink-mono": _p("#FFB8DC", "#B84381", "#9EC5FF", "#D63F8E", "#58163D", "#3F64C2"),
    "purple-mono": _p("#D9CCFF", "#7C5CD6", "#F28CC8", "#8E63F0", "#361D84", "#B0487F"),
    "blue-mono": _p("#B3D7FF", "#2F6BD6", "#C59BFF", "#2D7BEF", "#0B2D75", "#7A4FC9"),
    "green-mono": _p("#A8F0BC", "#288C48", "#B48CFF", "#1E9A45", "#0A431F", "#7A4FC9"),
}
MONO = ("teal-mono", "pink-mono", "purple-mono", "blue-mono", "green-mono")
MONO_GROUP = {"en": "Solid colours", "zh-TW": "純色", "zh-CN": "纯色", "ja": "単色",
              "es": "Colores sólidos", "pt": "Cores sólidas", "fr": "Couleurs unies", "de": "Einfarbig", "id": "Warna solid"}

# "custom": two colours picked by the user (page colour pickers, or config "palette": "custom" with
# "custom_colors": {"under": "#RRGGBB", "over": "#RRGGBB"}). The key carries the colours --
# custom-<under><over>, e.g. custom-23c4aeffa51f -- so every pair has its own image folder and nothing stale is
# reused; derive() adjusts each for contrast on dark / light backgrounds and picks a notice colour of another hue.
CUSTOM = "custom"
CUSTOM_DEFAULT = {"under": "#23C4AE", "over": "#FFA51F"}
CUSTOM_LABEL = {"en": "Custom…", "zh-TW": "自訂顏色…", "zh-CN": "自定义颜色…",
                "es": "Personalizado…", "pt": "Personalizado…", "fr": "Personnalisé…", "de": "Eigene Farben…", "id": "Kustom…"}
_CUSTOM_RE = __import__("re").compile(r"custom-([0-9a-f]{6})([0-9a-f]{6})")
_HEX_RE = __import__("re").compile(r"#[0-9A-Fa-f]{6}")
_BG = {"dark": "#0B0F14", "light": "#F4F4F0"}
_NOTICES = (("#B48CFF", "#7A4FC9"), ("#F28CC8", "#B0487F"), ("#9EC5FF", "#3F64C2"), ("#7FD8C8", "#1F8A7A"))

# over / under / grey baseline at least CIEDE2000 20 apart in normal vision and simulated protanopia,
# deuteranopia, tritanopia, protanomaly 0.6 and deuteranomaly 0.6 (Machado 2009), dark and light variants
CVD_SAFE = {"orange-deepblue", "sunflower-navy", "safety-ultramarine", "tangerine-violet", "amber-cobalt"}
STAR = "★"

LABELS = {
    "orange-teal": {"en": "Orange / teal", "zh-TW": "橘／青綠", "zh-CN": "橙／青绿"},
    "orange-blue": {"en": "Orange / blue", "zh-TW": "橘／藍", "zh-CN": "橙／蓝"},
    "orange-deepblue": {"en": "Orange / deep blue", "zh-TW": "橘／深藍", "zh-CN": "橙／深蓝"},
    "amber-purple": {"en": "Golden orange / purple", "zh-TW": "金橘／紫", "zh-CN": "金橙／紫"},
    "pink-green": {"en": "Pink / green", "zh-TW": "桃紅／綠", "zh-CN": "桃红／绿"},
    "gold-sky": {"en": "Gold / sky blue", "zh-TW": "金／天藍", "zh-CN": "金／天蓝"},
    "orange-purple": {"en": "Orange / purple", "zh-TW": "橘／紫", "zh-CN": "橙／紫"},
    "magenta-teal": {"en": "Magenta / teal", "zh-TW": "洋紅／青綠", "zh-CN": "洋红／青绿"},
    "yellow-indigo": {"en": "Yellow / indigo", "zh-TW": "黃／靛藍", "zh-CN": "黄／靛蓝"},
    "peach-mint": {"en": "Peach / mint", "zh-TW": "蜜桃／薄荷", "zh-CN": "蜜桃／薄荷"},
    "tangerine-cyan": {"en": "Tangerine / cyan", "zh-TW": "橘紅／青", "zh-CN": "橘红／青"},
    "rose-lime": {"en": "Rose / lime", "zh-TW": "玫瑰／萊姆", "zh-CN": "玫瑰／莱姆"},
    "sunflower-navy": {"en": "Sunflower / navy", "zh-TW": "向日葵／深藍", "zh-CN": "向日葵／深蓝"},
    # legacy (not in the menu since 1.2.0; old configs still draw with it), named by its colours
    "high-contrast": {"en": "Bright yellow / deep sky blue", "zh-TW": "亮黃／深天藍", "zh-CN": "亮黄／深天蓝"},
    "safety-ultramarine": {"en": "Safety orange / ultramarine", "zh-TW": "安全橘／群青", "zh-CN": "安全橙／群青"},
    "tangerine-violet": {"en": "Tangerine / violet", "zh-TW": "亮橘／紫羅蘭", "zh-CN": "亮橙／紫罗兰"},
    "amber-cobalt": {"en": "Amber orange / cobalt", "zh-TW": "琥珀橘／鈷藍", "zh-CN": "琥珀橙／钴蓝"},
    "teal-mono": {"en": "All teal", "zh-TW": "全青綠", "zh-CN": "全青绿"},
    "pink-mono": {"en": "All pink", "zh-TW": "全粉紅", "zh-CN": "全粉红"},
    "purple-mono": {"en": "All purple", "zh-TW": "全紫", "zh-CN": "全紫"},
    "blue-mono": {"en": "All blue", "zh-TW": "全藍", "zh-CN": "全蓝"},
    "green-mono": {"en": "All green", "zh-TW": "全綠", "zh-CN": "全绿"},
}

# Latin-script interface languages (es, pt, fr, de, id), in the LABELS order above; merged in below
LATIN_LABELS = {
    "es": (
        "Naranja / verde azulado",
        "Naranja / azul",
        "Naranja / azul oscuro",
        "Naranja dorado / morado",
        "Rosa / verde",
        "Dorado / celeste",
        "Naranja / morado",
        "Magenta / verde azulado",
        "Amarillo / índigo",
        "Melocotón / menta",
        "Mandarina / cian",
        "Rosa / lima",
        "Girasol / azul marino",
        "Amarillo vivo / azul cielo intenso",
        "Naranja de seguridad / azul ultramar",
        "Mandarina / violeta",
        "Naranja ámbar / azul cobalto",
        "Todo verde azulado",
        "Todo rosa",
        "Todo morado",
        "Todo azul",
        "Todo verde",
    ),
    "pt": (
        "Laranja / verde-azulado",
        "Laranja / azul",
        "Laranja / azul-escuro",
        "Laranja-dourado / roxo",
        "Rosa / verde",
        "Dourado / azul-celeste",
        "Laranja / roxo",
        "Magenta / verde-azulado",
        "Amarelo / índigo",
        "Pêssego / menta",
        "Tangerina / ciano",
        "Rosa / lima",
        "Girassol / azul-marinho",
        "Amarelo vivo / azul-celeste intenso",
        "Laranja de segurança / ultramarino",
        "Tangerina / violeta",
        "Laranja-âmbar / azul-cobalto",
        "Todo verde-azulado",
        "Todo rosa",
        "Todo roxo",
        "Todo azul",
        "Todo verde",
    ),
    "fr": (
        "Orange / bleu canard",
        "Orange / bleu",
        "Orange / bleu foncé",
        "Orange doré / violet",
        "Rose / vert",
        "Or / bleu ciel",
        "Orange / violet",
        "Magenta / bleu canard",
        "Jaune / indigo",
        "Pêche / menthe",
        "Mandarine / cyan",
        "Rose vif / citron vert",
        "Tournesol / bleu marine",
        "Jaune vif / bleu ciel profond",
        "Orange sécurité / outremer",
        "Mandarine / violet",
        "Orange ambré / bleu cobalt",
        "Tout bleu canard",
        "Tout rose",
        "Tout violet",
        "Tout bleu",
        "Tout vert",
    ),
    "de": (
        "Orange / Petrol",
        "Orange / Blau",
        "Orange / Dunkelblau",
        "Goldorange / Lila",
        "Pink / Grün",
        "Gold / Himmelblau",
        "Orange / Lila",
        "Magenta / Petrol",
        "Gelb / Indigo",
        "Pfirsich / Mint",
        "Mandarine / Cyan",
        "Rosé / Limette",
        "Sonnengelb / Marineblau",
        "Leuchtgelb / tiefes Himmelblau",
        "Signalorange / Ultramarin",
        "Mandarine / Violett",
        "Bernsteinorange / Kobaltblau",
        "Ganz Petrol",
        "Ganz Pink",
        "Ganz Lila",
        "Ganz Blau",
        "Ganz Grün",
    ),
    "id": (
        "Oranye / hijau toska",
        "Oranye / biru",
        "Oranye / biru tua",
        "Oranye keemasan / ungu",
        "Merah muda / hijau",
        "Emas / biru langit",
        "Oranye / ungu",
        "Magenta / hijau toska",
        "Kuning / nila",
        "Persik / mint",
        "Jeruk / sian",
        "Mawar / limau",
        "Bunga matahari / biru dongker",
        "Kuning terang / biru langit pekat",
        "Oranye keselamatan / ultramarin",
        "Jeruk / violet",
        "Oranye amber / biru kobalt",
        "Serba hijau toska",
        "Serba merah muda",
        "Serba ungu",
        "Serba biru",
        "Serba hijau",
    ),
}
for _lg, _names in LATIN_LABELS.items():
    for _key, _name in zip(LABELS, _names):
        LABELS[_key][_lg] = _name

# one neutral line under the menu explaining the ★
STAR_NOTE = {"en": "★ = stronger contrast, easy to tell apart for every kind of colour vision",
             "zh-TW": "★ = 對比較強，各種色覺都容易分辨", "zh-CN": "★ = 对比较强，各种色觉都容易分辨",
             "es": "★ = más contraste, fácil de distinguir con cualquier tipo de visión del color",
             "pt": "★ = mais contraste, fácil de distinguir com qualquer tipo de visão de cores",
             "fr": "★ = contraste renforcé, facile à distinguer pour tous les types de vision des couleurs",
             "de": "★ = stärkerer Kontrast, für jede Art des Farbsehens gut unterscheidbar",
             "id": "★ = kontras lebih kuat, mudah dibedakan untuk semua jenis penglihatan warna"}


def cvd_safe(name=None):
    return normalize(name) in CVD_SAFE


def is_hex(c):
    return isinstance(c, str) and bool(_HEX_RE.fullmatch(c.strip()))


def custom_key(under, over):
    """'custom-23c4aeffa51f' for under #23C4AE / over #FFA51F (ValueError unless both are #RRGGBB)."""
    if not (is_hex(under) and is_hex(over)):
        raise ValueError("custom colours must be #RRGGBB")
    return "custom-" + under.strip()[1:].lower() + over.strip()[1:].lower()


def parse_custom(k):
    """(under, over) as '#RRGGBB' for a custom key, else None."""
    m = _CUSTOM_RE.fullmatch(str(k or "").strip().lower())
    return ("#" + m.group(1).upper(), "#" + m.group(2).upper()) if m else None


def is_custom(k):
    return parse_custom(k) is not None


def resolve(name, custom_colors=None):
    """Palette key for a config: 'custom' + custom_colors {"under", "over"} -> 'custom-<under><over>'; invalid or
    missing custom colours fall back to CUSTOM_DEFAULT; anything else -> normalize(name)."""
    if str(name or "").strip().lower() == CUSTOM:
        cc = custom_colors if isinstance(custom_colors, dict) else {}
        u, o = cc.get("under"), cc.get("over")
        if not (is_hex(u) and is_hex(o)):
            u, o = CUSTOM_DEFAULT["under"], CUSTOM_DEFAULT["over"]
        return custom_key(u, o)
    return normalize(name)


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _fit(c, bg, toward, need=3.0):
    """c moved toward white (dark background) or black (light) until it has `need`:1 contrast with bg."""
    t = 0.0
    out = c.upper()
    while contrast(out, bg) < need and t < 1:
        t = min(1.0, t + 0.04)
        out = blend(c, toward, t)
    return out


def derive(under, over):
    """Dark / light variants for a custom pair: each colour kept as picked when readable, otherwise lightened
    (dark background) or darkened (light) to 3:1; notice = the first stock notice colour of another hue."""
    v = {}
    for var, toward in (("dark", "#FFFFFF"), ("light", "#000000")):
        v[var] = (_fit(over, _BG[var], toward), _fit(under, _BG[var], toward))
    notice = next((n for n in _NOTICES if not _near(n[0], under, 30) and not _near(n[0], over, 30)), _NOTICES[0])
    return _p(v["dark"][0], v["dark"][1], notice[0], v["light"][0], v["light"][1], notice[1])


def similar(under, over):
    """True when the two picked colours are hard to tell apart (contrast below 1.6:1 and close in hue or both
    nearly grey): the page then notes that the ▲ / ▼ tags and patterns still show the state."""
    if not (is_hex(under) and is_hex(over)):
        return False
    return contrast(under, over) < 1.6 and (_near(under, over, 40) or min(_hsv(under)[1], _hsv(over)[1]) < 0.2)


def label(name, lang="en"):
    """Menu label: the plain name plus ' ★' for the CVD_SAFE presets."""
    if is_custom(name) or str(name or "").strip().lower() == CUSTOM:
        return CUSTOM_LABEL.get(lang) or CUSTOM_LABEL["en"]
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
    base = k[: -len(PAT_SUFFIX)] if k.endswith(PAT_SUFFIX) else None
    pat = base is not None and (base in PALETTES or is_custom(base))
    return normalize(base if pat else k), bool(pat)


def is_key(k):
    k = str(k or "")
    base = k[: -len(PAT_SUFFIX)] if k.endswith(PAT_SUFFIX) else k
    return base in PALETTES or is_custom(base)


# kept for old configs, theme files and command lines, but no longer offered in the menu (names()):
# "high-contrast" (alias "yellow-deepsky") still draws with its own colours when set
LEGACY = ("high-contrast",)


def names():
    """Menu order: two-colour presets, the ★ (CVD_SAFE) ones as one block at the end of that section, then the
    solid colours (MONO). Legacy presets are left out (still accepted by normalize() / get())."""
    two = [n for n in PALETTES if n not in MONO and n not in LEGACY]
    return [n for n in two if n not in CVD_SAFE] + [n for n in two if n in CVD_SAFE] + list(MONO)


def known(name):
    """Any preset that can be drawn: menu presets plus LEGACY ones."""
    return name in PALETTES


def normalize(name):
    """Return a known preset name; unknown or empty values fall back to the default."""
    n = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    if is_custom(n):
        return n
    if n == CUSTOM:
        return custom_key(CUSTOM_DEFAULT["under"], CUSTOM_DEFAULT["over"])
    aliases = {"default": DEFAULT, "teal": "orange-teal", "blue": "orange-blue",
               # earlier name of orange-deepblue (configs, theme files and saved browser choices still work)
               "colorblind": "orange-deepblue", "okabe-ito": "orange-deepblue", "color-blind": "orange-deepblue",
               "colourblind": "orange-deepblue", "colour-blind": "orange-deepblue", "cb": "orange-deepblue",
               # colour name of high-contrast (the stored key stays "high-contrast")
               "yellow-deepsky": "high-contrast", "all-teal": "teal-mono", "mono-teal": "teal-mono",
               "teal-monochrome": "teal-mono", "brightyellow-deepsky": "high-contrast"}
    n = aliases.get(n, n)
    return n if n in PALETTES else DEFAULT


def get(name=None, light=False):
    """Colours of a preset as a dict: over, under, baseline, notice."""
    n = normalize(name)
    p = derive(*parse_custom(n)) if is_custom(n) else PALETTES[n]
    return dict(p["light" if light else "dark"])


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
    return {k: {"dark": PALETTES[k]["dark"], "light": PALETTES[k]["light"], "cvd_safe": k in CVD_SAFE} for k in names()}
