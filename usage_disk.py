"""usage_disk.py - free disk space of the machine the dashboard runs on, for the top of the Built-in tab (stdlib only).

Reads the home folder's filesystem with ``shutil.disk_usage`` (falls back to the filesystem root). No network, no API calls.
The level is "danger" when free space is strictly under the danger threshold, "warn" when strictly under the warning
threshold, else "ok" (so exactly at the warning threshold is still ok, and exactly at the danger threshold is only a warning).
The thresholds scale with the disk: by default free space under 23 % of the total is a warning and under 16 % is danger
(on a 128 GB disk about 29.4 GB and 20.5 GB). They can be set in usage_config.json as ``disk_warn_pct`` / ``disk_danger_pct``
(percent of the total). The older fixed keys ``disk_warn_gb`` / ``disk_danger_gb`` (1 GB = 1024**3 bytes, what Windows / Explorer
calls GB) still work: when one is set to a valid number it wins over the percentage for that level.

The bot that writes the Built-in snapshots can also record the disk of the computer *it* runs on (``disk_free_gb`` /
``disk_total_gb`` in the snapshot). ``disk_source`` in usage_config.json: ``"auto"`` (default) uses that report only when it
is there and the dashboard clearly runs on another machine (the local disk's total size differs from the reported one by more
than 5 % and more than 1 GB; on the same machine the live local reading is better), ``"local"`` never prefers it, ``"snapshot"`` always
prefers it (either way it is the fallback when the local disk cannot be read). Missing or odd fields are ignored and the local reading is used.

Test hook (never needed in normal use): the environment variables ``USAGE_DISK_FAKE_FREE_GB`` (and optionally
``USAGE_DISK_FAKE_TOTAL_GB``, default 128) replace the real reading, so screenshots and tests never depend on the real disk.
"""
import math
import os
import shutil
from pathlib import Path

GB = 1024 ** 3
WARN_PCT = 23.0     # free space strictly under this share of the total: warning (orange)
DANGER_PCT = 16.0   # free space strictly under this share of the total: danger (red)

# Colours of the bar. The danger colour lives in this one constant (red is also an assistant's name colour - change it here only).
# They are deliberately NOT the chart's --over / --under colours: orange there means "over the baseline" and nothing else.
DISK_DANGER_COLOR = "#E5484D"   # red
DISK_WARN_COLOR = "#F08C24"     # orange
# "ok" has no colour here: the page draws it in a neutral grey so it is not mistaken for a state.

# Shown in code style in the help popover (a plain read-only command; the dashboard never runs it).
DU_COMMAND = "du -sh ~/* /workspace/* 2>/dev/null | sort -h | tail"

ENV_FREE = "USAGE_DISK_FAKE_FREE_GB"
ENV_TOTAL = "USAGE_DISK_FAKE_TOTAL_GB"


def level(free_gb, warn_gb, danger_gb):
    """'danger' | 'warn' | 'ok' for a free space in GB. 'Under' means strictly less than (the boundary itself is the milder level)."""
    if free_gb < danger_gb:
        return "danger"
    if free_gb < warn_gb:
        return "warn"
    return "ok"


def _num(v, default):
    try:
        if isinstance(v, bool):
            raise ValueError
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) and f > 0 else default


def thresholds(cfg, total_gb):
    """(warn_gb, danger_gb) for a disk of `total_gb`. Default: 23 % and 16 % of the total (``disk_warn_pct`` /
    ``disk_danger_pct`` in the config, 0-100). A valid ``disk_warn_gb`` / ``disk_danger_gb`` in the config wins over the
    percentage for that level (backward compatible). Bad values fall back to the defaults; danger is never above warn."""
    cfg = cfg if isinstance(cfg, dict) else {}
    pct = lambda key, default: min(_num(cfg.get(key), default), 100.0)
    warn = _num(cfg.get("disk_warn_gb"), None)
    danger = _num(cfg.get("disk_danger_gb"), None)
    if warn is None:
        warn = total_gb * pct("disk_warn_pct", WARN_PCT) / 100
    if danger is None:
        danger = total_gb * pct("disk_danger_pct", DANGER_PCT) / 100
    return warn, min(danger, warn)


def _fake():
    try:
        free = float(os.environ[ENV_FREE])
    except (KeyError, ValueError):
        return None
    if not math.isfinite(free) or free < 0:
        return None
    total = _num(os.environ.get(ENV_TOTAL), 128.0)
    return free * GB, max(total, free) * GB


def read(path=None):
    """(free_bytes, total_bytes) of the home folder's filesystem (or `path`'s), the root as a fallback; None when unreadable."""
    fake = _fake()
    if fake:
        return fake
    for p in ([path] if path else []) + [str(Path.home()), os.path.abspath(os.sep)]:
        try:
            u = shutil.disk_usage(p)
            return u.free, u.total
        except (OSError, ValueError, RuntimeError):
            continue
    return None


SOURCES = ("auto", "local", "snapshot")
SAME_MACHINE_PCT = 5.0   # "auto": totals this close (or within 1 GB) mean the report and the dashboard share a disk


def source(cfg=None):
    v = str((cfg or {}).get("disk_source", "auto") if isinstance(cfg, dict) else "auto").strip().lower()
    return v if v in SOURCES else "auto"


def from_snapshot(snap):
    """(free_bytes, total_bytes, captured_at_epoch) from a normalized snapshot's disk fields, or None."""
    try:
        f, t = snap.get("disk_free_gb"), snap.get("disk_total_gb")
        if f is None or t is None or t <= 0 or f < 0 or f > t * 1.01:
            return None
        from datetime import datetime
        return float(f) * GB, float(t) * GB, int(datetime.fromisoformat(snap["captured_at"]).timestamp())
    except (AttributeError, TypeError, ValueError, KeyError):
        return None


def different_machine(local, reported):
    """True when the local disk's total size differs from the reported one by more than 5 % (and more than 1 GB)."""
    diff = abs(local[1] - reported[1])
    return diff > GB and diff > reported[1] * SAME_MACHINE_PCT / 100


def pick(cfg, local, snap):
    """('local'|'snapshot', (free, total), ts): which reading the bar shows. `local` may be None, `snap` is a normalized snapshot or None."""
    rep, mode = (from_snapshot(snap) if snap else None), source(cfg)
    use_report = bool(rep) and (local is None or mode == "snapshot" or (mode == "auto" and different_machine(local, rep)))
    if use_report:
        return "snapshot", rep[:2], rep[2]
    return ("local", local, None) if local else (None, None, None)


def _floor1(x):
    return math.floor(x * 10 + 1e-9) / 10   # shown value never rounds up past a threshold (29.96 shows 29.9, not 30.0)


def page_data(cfg=None, reading=None, snap=None):
    """JSON-ready dict for the bar, or None when the disk cannot be read. `reading` (free, total bytes) is for tests;
    `snap` is the newest normalized snapshot (its optional disk fields may be preferred, see `pick`)."""
    local = reading if reading is not None else read()
    src, r, ts = pick(cfg, local if local and local[1] > 0 else None, snap)
    if not r or r[1] <= 0:
        return None
    free, total = r[0] / GB, r[1] / GB
    warn, danger = thresholds(cfg, total)
    return {"free": _floor1(free), "total": round(total), "pct": round(max(0.0, min(100.0, free / total * 100)), 1),
            "level": level(free, warn, danger), "warn_gb": round(warn, 2), "danger_gb": round(danger, 2),
            "colors": {"warn": DISK_WARN_COLOR, "danger": DISK_DANGER_COLOR}, "du": DU_COMMAND,
            "src": src, "ts": ts}
