"""Forecast sensitivity: how fast the projection reacts to the most recent usage (stdlib only).

The projection blends the week-to-date rate with the rate of a recent window (usage_rate.blend_rate). A preset
chooses that window, the largest share the recent rate may take, and how much reading history is needed:

* ``standard``       24 h window, max weight 0.5, readings must span >= 6 h  (the default; the behaviour before presets)
* ``sensitive``      12 h window, max weight 0.5, readings must span >= 3 h
* ``very_sensitive``  6 h window, max weight 0.6, readings must span >= 2 h

The clamp (0.5x .. 2x the week-to-date rate) and the minimum of three readings are the same for every preset.
The choice is saved like the other page menus: ``forecast_sensitivity`` in usage_config.json, or in
<data_dir>/page_settings.json when there is no config file. Unknown / missing values mean ``standard``.
"""
DEFAULT = "standard"
KEYS = ("standard", "sensitive", "very_sensitive")

PRESETS = {
    "standard": {"window_h": 24.0, "max_weight": 0.5, "min_span_h": 6.0},
    "sensitive": {"window_h": 12.0, "max_weight": 0.5, "min_span_h": 3.0},
    "very_sensitive": {"window_h": 6.0, "max_weight": 0.6, "min_span_h": 2.0},
}

_ALIASES = {"very sensitive": "very_sensitive", "very-sensitive": "very_sensitive", "verysensitive": "very_sensitive"}


def clamp(v):
    """Config / request value -> one of KEYS (anything unreadable -> the default)."""
    if not isinstance(v, str):
        return DEFAULT
    k = v.strip().lower()
    k = _ALIASES.get(k, k)
    return k if k in PRESETS else DEFAULT


def params(v):
    """Preset parameters {"window_h", "max_weight", "min_span_h"} for a key (unknown -> standard)."""
    return dict(PRESETS[clamp(v)])


def suffix(v):
    """Cache-folder suffix: '' for the default, '-Fsensitive' / '-Fvery_sensitive' otherwise (image cache key part)."""
    k = clamp(v)
    return "" if k == DEFAULT else "-F" + k


def from_settings(cfg, data_dir=None):
    """Preset from the config dict, else from <data_dir>/page_settings.json (written by the dashboard page)."""
    v = (cfg or {}).get("forecast_sensitivity")
    if v is None and data_dir:
        import json
        from pathlib import Path
        try:
            d = json.loads((Path(data_dir) / "page_settings.json").read_text(encoding="utf-8"))
            v = d.get("forecast_sensitivity") if isinstance(d, dict) else None
        except (OSError, ValueError):
            v = None
    return clamp(v)
