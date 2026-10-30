#!/usr/bin/env python3
"""usage_breakdown.py - the "usage breakdown" snapshots a Grok Bot saves for the dashboard (stdlib only).

Grok Bot can report its owner's usage when asked: the plan, the weekly limit used, how much of it is Grok Bot's,
and Grok Bot's usage split by bot and by activity (chats, routines, bot-to-bot, group chats). The bot saves that
report as a small JSON file in ``<data_dir>/breakdown/``; the **Breakdown** tab shows the newest one.

Snapshot format (every field is optional except that a snapshot needs *something* to show)::

    {
      "captured_at": "2026-10-29T09:00:00+08:00",   # when the report was read (ISO 8601; no offset = dashboard time zone)
      "period": "this_week",                        # this_week | last_7_days | last_30_days (other text is kept as is)
      "plan": "Super",
      "weekly_used_pct": 61.1,                      # whole weekly limit used, all apps on the plan
      "grokbot_used_pct": 54.3,                     # of that limit, used by Grok Bot
      "shared_with_other_apps": true,
      "resets_at": "2026-10-31T01:41:00+08:00",
      "extra_usage_source": "additional usage credits",
      "by_bot": [{"name": "Helper A", "share": 53}, {"name": "Helper B", "share": "<1%"}],
      "by_activity": [
        {"name": "Chats with the user", "share": 85},
        {"name": "Routines", "share": 8, "routines": [{"name": "Daily digest", "share": 5, "runs_per_week": 7}]},
        {"name": "Bot-to-Bot", "share": 7}
      ],
      "by_day": [{"date": "2026-10-28", "share": 14}],   # optional: the report cannot always split a period by day
      "disk_free_gb": 27.5, "disk_total_gb": 128,        # optional: free / total disk of the computer the bot runs on (GB)
      "notes": "free text"
    }

All ``share`` values are percentages of all Grok Bot usage in the period (not of the weekly limit). Parsing is
lenient: numbers may be strings (``"53%"``, ``"<1%"``), lists may be ``{name: share}`` maps, unknown keys are ignored
and a bad value drops that one field, never the snapshot. The report has no per-model usage, no usage of other apps
and no last-week percentage, so none is stored.

Files: ``breakdown/<YYYYMMDDTHHMMSSZ>.json`` (one per capture, never overwritten, kept as history). Any ``*.json``
in that folder is read, so a bot may also write its own file name.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR_NAME = "breakdown"
SCHEMA = 1
MAX_FILES = 200            # newest files read; older ones stay on disk
MAX_BYTES = 256 * 1024     # a bigger file is ignored
MAX_ITEMS = 60             # bots / activities / routines / days kept per snapshot
PERIODS = ("this_week", "last_7_days", "last_30_days")
KINDS = ("chat", "routine", "bot_to_bot", "group", "other")
ROUTINE_KINDS = ("other_routines", "not_routine")

_NUM = re.compile(r"(<)?\s*([-+]?\d+(?:[.,]\d+)?)")
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def breakdown_dir(data_dir):
    return Path(data_dir) / DIR_NAME


def pct(v, lo=0.0, hi=1000.0):
    """A percentage as (value, less_than_one). Accepts 53, 53.2, "53%", "61.1% used", "<1%"; None when unreadable."""
    if isinstance(v, bool) or v is None:
        return None, False
    if isinstance(v, (int, float)):
        x, lt = float(v), False
    else:
        m = _NUM.search(str(v))
        if not m:
            return None, False
        try:
            x = float(m.group(2).replace(",", "."))
        except ValueError:
            return None, False
        lt = bool(m.group(1))
        if lt:
            x = min(x, 1.0) / 2 if x else 0.0   # "<1%" -> 0.5, shown as "<1%"
    if x != x or x in (float("inf"), float("-inf")):
        return None, False
    return round(max(lo, min(hi, x)), 2), lt


def _num(v):
    x, _ = pct(v, 0.0, 1e6)
    return x


def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    if s in ("true", "yes", "y", "1", "shared"):
        return True
    if s in ("false", "no", "n", "0", "not shared"):
        return False
    return None


def _text(v, n=120):
    if v is None or isinstance(v, (dict, list)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()[:n]


def parse_time(v, tz, ref=None):
    """ISO 8601 (``Z`` ok; naive = ``tz``) or 'Saturday Oct 31 01:41' (year from ``ref``) -> aware datetime or None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e9:
        return datetime.fromtimestamp(v / 1000 if v > 1e12 else v, tz)
    s = _text(v, 80)
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=tz)
    except ValueError:
        pass
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?[,\s]+(\d{1,2}):(\d{2})\s*([AaPp][Mm])?", s)
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    ref = ref or datetime.now(tz)
    h = int(m.group(4)) % 24
    if m.group(6):
        h = int(m.group(4)) % 12 + (12 if m.group(6).lower() == "pm" else 0)
    try:
        d = datetime(int(m.group(3) or ref.year), _MONTHS[m.group(1).lower()], int(m.group(2)), h, int(m.group(5)), tzinfo=tz)
    except ValueError:
        return None
    if not m.group(3) and d < ref - timedelta(days=200):
        d = d.replace(year=d.year + 1)   # "Jan 2" read in late December
    return d


def _kind(name):
    n = name.lower()
    if re.search(r"bot[-\s]*to[-\s]*bot|bot2bot|b2b", n):
        return "bot_to_bot"
    if re.fullmatch(r"(group|groups|group chats?|group conversations?)", n.strip()) or re.search("群組|群组|群聊", n):
        return "group"
    if re.search(r"not from a routine|non[-\s]*routine|no routine|outside .*routine|非.*排程|不是.*排程", n):
        return "not_routine"
    if re.search(r"other routines?|其他.*(排程|例行)", n):
        return "other_routines"
    if "routine" in n or "排程" in n or "例行" in n or "scheduled" in n:
        return "routine"
    if re.fullmatch(r"(chats?|conversations?|direct (chats?|messages)|(chats?|conversations?) with (the |my )?(user|you|me)|user chats?)", n.strip()) \
            or re.search("對話|对话|聊天", n):
        return "chat"
    return "other"


def _items(v, name_keys=("name", "bot", "label", "title"), share_keys=("share", "pct", "percent", "usage", "value")):
    """List of {name, share} dicts from a list of dicts / [name, share] pairs / a {name: share} map."""
    out = []
    if isinstance(v, dict):
        v = [{"name": k, "share": x} if not isinstance(x, dict) else dict(x, name=x.get("name", k)) for k, x in v.items()]
    if not isinstance(v, list):
        return out
    for it in v[:MAX_ITEMS]:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            it = {"name": it[0], "share": it[1]}
        if not isinstance(it, dict):
            continue
        name = next((_text(it[k], 80) for k in name_keys if k in it and _text(it[k], 80)), "")
        raw = next((it[k] for k in share_keys if k in it), None)
        share, lt = pct(raw, 0, 100)
        if name or share is not None:
            out.append((name, share, lt, it))
    return out


def normalize(raw, tz=timezone.utc, fallback_time=None):
    """Lenient snapshot parse. Returns a clean dict (schema 1), or None when ``raw`` has nothing usable.
    ``fallback_time`` (aware datetime) replaces a missing / unreadable ``captured_at``."""
    if not isinstance(raw, dict):
        return None
    cap = parse_time(raw.get("captured_at") or raw.get("as_of") or raw.get("timestamp"), tz) or fallback_time
    if cap is None:
        return None
    s = {"schema": SCHEMA, "captured_at": cap.isoformat(timespec="seconds"),
         "period": _text(raw.get("period"), 40).lower().replace(" ", "_").replace("-", "_") or "this_week",
         "plan": _text(raw.get("plan"), 60) or None}
    s["weekly_used_pct"] = pct(raw.get("weekly_used_pct", raw.get("weekly_used")))[0]
    gb, gb_lt = pct(raw.get("grokbot_used_pct", raw.get("grokbot_used")))
    s["grokbot_used_pct"], s["grokbot_lt1"] = gb, gb_lt
    s["shared_with_other_apps"] = _bool(raw.get("shared_with_other_apps"))
    rt = raw.get("resets_at", raw.get("reset_at"))
    rd = parse_time(rt, tz, cap)
    s["resets_at"] = rd.isoformat(timespec="seconds") if rd else None
    s["resets_text"] = None if rd else (_text(rt, 60) or None)
    s["extra_usage_source"] = _text(raw.get("extra_usage_source"), 160) or None
    s["by_bot"] = [{"name": n, "share": sh, "lt1": lt} for n, sh, lt, _ in _items(raw.get("by_bot", raw.get("bots"))) if n and sh is not None]
    acts = []
    for n, sh, lt, it in _items(raw.get("by_activity", raw.get("activities"))):
        if not n:
            continue
        rs = []
        for rn, rsh, rlt, rit in _items(it.get("routines")):
            if not rn:
                continue
            runs = next((_num(rit[k]) for k in ("runs_per_week", "runs", "about_runs_per_week") if k in rit), None)
            rs.append({"name": rn, "kind": _kind(rn) if _kind(rn) in ROUTINE_KINDS else "routine", "share": rsh, "lt1": rlt,
                       "runs_per_week": None if runs is None else round(runs, 1)})
        acts.append({"name": n, "kind": _kind(n) if _kind(n) in KINDS else "other", "share": sh, "lt1": lt, "routines": rs})
    s["by_activity"] = acts
    days = []
    bd = raw.get("by_day")
    if isinstance(bd, dict):
        bd = [{"date": k, "share": v} for k, v in bd.items()]
    for n, sh, lt, it in _items(bd, ("date", "day", "name"), ("share", "pct", "percent", "value")):
        if n and sh is not None:
            days.append({"date": n, "share": sh, "lt1": lt})
    s["by_day"] = days or None
    s["disk_free_gb"], s["disk_total_gb"] = disk_gb(raw)
    s["notes"] = _text(raw.get("notes"), 600)
    has = (s["weekly_used_pct"] is not None or gb is not None or s["by_bot"] or acts or s["plan"] or s["resets_at"])
    return s if has else None


def disk_gb(raw):
    """(free_gb, total_gb) from the optional ``disk_free_gb`` / ``disk_total_gb`` fields (numbers or text like "27.5 GB"), or
    (None, None) when either is missing or they do not make sense (free > total, total <= 0). Never raises."""
    try:
        free, total = (_num(raw.get(k, raw.get(k.replace("_gb", "")))) for k in ("disk_free_gb", "disk_total_gb"))
    except Exception:
        return None, None
    if free is None or total is None or total <= 0 or free > total * 1.01:
        return None, None
    return round(free, 1), round(total, 1)


def stamp(captured_at):
    """File stem for a capture: UTC ``YYYYMMDDTHHMMSSZ``."""
    return datetime.fromisoformat(captured_at).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_snapshot(data_dir, snap):
    """Save a normalized snapshot as breakdown/<stamp>.json (never overwrites: a clash gets -2, -3 ...). Returns the path."""
    from usage_lock import write_lock
    d = breakdown_dir(data_dir)
    with write_lock(data_dir):
        d.mkdir(parents=True, exist_ok=True)
        stem, n = stamp(snap["captured_at"]), 1
        while (d / (stem + ("" if n == 1 else f"-{n}") + ".json")).exists():
            n += 1
        p = d / (stem + ("" if n == 1 else f"-{n}") + ".json")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    return p


def import_text(data_dir, text, tz=timezone.utc, now=None):
    """Paste / import path: parse JSON text (a snapshot, or a snapshot inside a ```json fence) and save it.
    Returns (snapshot, path); raises ValueError with a short reason."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        raw = json.loads(t)
    except ValueError as exc:
        i, j = t.find("{"), t.rfind("}")
        try:
            raw = json.loads(t[i:j + 1]) if 0 <= i < j else None
        except ValueError:
            raw = None
        if raw is None:
            raise ValueError(f"not valid JSON: {exc}")
    snap = normalize(raw, tz, now or datetime.now(tz))
    if snap is None:
        raise ValueError("no usable fields (need at least a usage %, a plan, a reset time, or by_bot / by_activity)")
    return snap, write_snapshot(data_dir, snap)


def load(data_dir, tz=timezone.utc):
    """All readable snapshots, oldest first. Unreadable / oversized / unusable files are skipped; a file without a
    usable captured_at is dated by its modification time."""
    d = breakdown_dir(data_dir)
    if not d.is_dir():
        return []
    files = []
    for p in d.glob("*.json"):
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_file() and st.st_size <= MAX_BYTES:
            files.append((st.st_mtime, p))
    out = []
    for mt, p in sorted(files, reverse=True)[:MAX_FILES]:
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        s = normalize(raw, tz, datetime.fromtimestamp(mt, tz))
        if s:
            out.append(s)
    out.sort(key=lambda s: datetime.fromisoformat(s["captured_at"]).timestamp())
    return out


def newest_mtime(data_dir):
    """Newest modification time among the snapshot files (0 when none): lets the server notice a dropped-in file."""
    d = breakdown_dir(data_dir)
    try:
        return max((p.stat().st_mtime for p in d.glob("*.json")), default=0.0)
    except OSError:
        return 0.0


def latest_and_previous(snaps):
    """(newest, previous snapshot of the same period and the same reset, or None)."""
    if not snaps:
        return None, None
    cur = snaps[-1]
    for s in reversed(snaps[:-1]):
        if s["period"] != cur["period"]:
            continue
        a, b = s.get("resets_at"), cur.get("resets_at")
        if a and b and abs(datetime.fromisoformat(a).timestamp() - datetime.fromisoformat(b).timestamp()) > 3600:
            return cur, None        # the week rolled over: not comparable
        return cur, s
    return cur, None


if __name__ == "__main__":  # python3 usage_breakdown.py FILE|-  : check a snapshot without saving
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    txt = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    try:
        r = normalize(json.loads(txt), timezone.utc, datetime.now(timezone.utc))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)})); sys.exit(2)
    print(json.dumps({"ok": r is not None, "snapshot": r}, ensure_ascii=False, indent=1)); sys.exit(0 if r else 2)
