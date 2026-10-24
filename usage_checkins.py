"""
usage_checkins.py - check-in sessions shared by the dashboard tools.

A check-in is either one moment (phase "point") or a session made of a "start" row and a
later "end" row with the same id. The end row carries the end status: "done" or
"interrupted" (interrupted is a status, never a category) and an optional note/reason.

CSV (checkins.csv), one row per event:
    timestamp,name,note,category,id,phase,status
  * timestamp  ISO 8601 with UTC offset
  * category   1..N category ids joined with "|" (e.g. "code|review"); see usage_categories
  * id         session id (start and end share it; points get their own)
  * phase      start | end | point     (missing -> point: files from before sessions existed)
  * status     done | interrupted on end rows, empty otherwise

Older files (timestamp,name,note[,category]) stay readable: every row is a point and a
missing category is "other". The header is upgraded in place on the next write.
All writes go through usage_lock.write_lock(<lock dir>), shared with every other writer.
"""

import csv
import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import usage_categories
import usage_lock

HEADER = ["timestamp", "name", "note", "category", "id", "phase", "status"]
PHASES = ("start", "end", "point")
STATUSES = ("done", "interrupted")
DEFAULT_MAX_CATEGORIES = 3
DEFAULT_OPEN_LIMIT_HOURS = 6.0
DEFAULT_USAGE_TOLERANCE_MIN = 90


class CheckinError(ValueError):
    """Bad input (missing name, too many categories, nothing to end ...). `code` is a short id."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def clean(s, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def new_id() -> str:
    return "s" + secrets.token_hex(5)


def parse_categories(value, cats: "usage_categories.Categories", max_n: int = DEFAULT_MAX_CATEGORIES,
                     strict: bool = True) -> List[str]:
    """List, 'a|b', 'a,b' or repeated values -> 1..max_n normalized ids (unknown -> other).
    strict: raise CheckinError when more than max_n distinct ids are given; else truncate."""
    if value is None:
        raw: List[str] = []
    elif isinstance(value, (list, tuple)):
        raw = [p for v in value for p in str(v or "").replace(",", "|").split("|")]
    else:
        raw = str(value).replace(",", "|").split("|")
    out: List[str] = []
    for p in raw:
        if not p.strip():
            continue
        cid = cats.normalize(p)
        if cid not in out:
            out.append(cid)
    if not out:
        out = [usage_categories.OTHER]
    if len(out) > max(1, int(max_n)):
        if strict:
            raise CheckinError("too_many_categories", f"pick at most {int(max_n)} categories")
        out = out[: max(1, int(max_n))]
    return out


def upgrade_header(path: str) -> None:
    """Add missing columns to an older file (caller holds the write lock)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    head = [c.strip() for c in rows[0]] if rows else []
    if all(c in head for c in HEADER):
        return
    new_head = head + [c for c in HEADER if c not in head]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(new_head)
        for r in rows[1:]:
            w.writerow(r + [""] * (len(new_head) - len(r)))
    os.replace(tmp, path)


def _parse_ts(s: str) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(s or "").strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def read(path: str, cats: Optional["usage_categories.Categories"] = None) -> List[Dict]:
    """All events, oldest first: {t, name, note, cats, id, phase, status, line}."""
    cats = cats or usage_categories.load(None)
    out: List[Dict] = []
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            t = _parse_ts(r.get("timestamp"))
            name = clean(r.get("name"), 40)
            if t is None or not name:
                continue
            phase = (r.get("phase") or "").strip().lower()
            phase = phase if phase in PHASES else "point"
            status = (r.get("status") or "").strip().lower()
            out.append({"t": t, "name": name, "note": clean(r.get("note"), 200),
                        "cats": parse_categories(r.get("category"), cats, 99, strict=False),
                        "id": clean(r.get("id"), 40) or f"legacy-{i + 1}", "phase": phase,
                        "status": status if (phase == "end" and status in STATUSES) else "", "line": i + 2})
    out.sort(key=lambda e: e["t"])
    return out


def _append(path: str, lock_dir: str, row: List[str]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with usage_lock.write_lock(lock_dir):
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        if not new:
            upgrade_header(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(HEADER)
            w.writerow(row)


def open_sessions(events: Iterable[Dict]) -> List[Dict]:
    """Start events that have no end yet (oldest first)."""
    ended = {e["id"] for e in events if e["phase"] == "end"}
    return [e for e in events if e["phase"] == "start" and e["id"] not in ended]


def add(path: str, lock_dir: str, name, phase: str = "point", categories=None, note: str = "",
        cats: Optional["usage_categories.Categories"] = None, max_categories: int = DEFAULT_MAX_CATEGORIES,
        session_id: Optional[str] = None, status: str = "done", now: Optional[datetime] = None) -> Dict:
    """Write one event. Returns {timestamp, name, note, categories, id, phase, status}.
    end: closes `session_id`, or the latest open session for name (+ categories if given)."""
    cats = cats or usage_categories.load(None)
    name, note = clean(name, 40), clean(note, 200)
    phase = str(phase or "point").strip().lower()
    if not name and not (phase == "end" and session_id):
        raise CheckinError("missing_name", "missing name")
    if phase not in PHASES:
        raise CheckinError("bad_phase", "phase must be start, end or point")
    now = now or datetime.now().astimezone()
    ts = now.isoformat(timespec="seconds")
    with usage_lock.write_lock(lock_dir):  # read-check-append as one step
        if phase == "end":
            status = str(status or "done").strip().lower()
            if status not in STATUSES:
                raise CheckinError("bad_status", "status must be done or interrupted")
            opened = open_sessions(read(path, cats))
            if session_id:
                match = [e for e in opened if e["id"] == clean(session_id, 40)]
                if not match:
                    raise CheckinError("no_open_session", "no open session with that id")
            else:
                match = [e for e in opened if e["name"] == name]
                if categories:
                    want = set(parse_categories(categories, cats, 99, strict=False))
                    match = [e for e in match if set(e["cats"]) == want] or [e for e in match if want & set(e["cats"])]
                if not match:
                    raise CheckinError("no_open_session", "no open session for this name")
            s = match[-1]
            row = [ts, s["name"], note, "|".join(s["cats"]), s["id"], "end", status]
        else:
            cl = parse_categories(categories, cats, max_categories)
            row = [ts, name, note, "|".join(cl), session_id and clean(session_id, 40) or new_id(), phase, ""]
        _append(path, lock_dir, row)
    return {"timestamp": row[0], "name": row[1], "note": row[2], "categories": row[3].split("|"),
            "id": row[4], "phase": row[5], "status": row[6]}


def sessions(events: Sequence[Dict], now: Optional[datetime] = None,
             open_limit_hours: float = DEFAULT_OPEN_LIMIT_HOURS) -> List[Dict]:
    """Pair start/end rows into sessions (points are zero-length sessions), oldest first.
    state: done | interrupted | running (open, within the limit) | open (open longer than the
    limit, shown as 'open/未結束') | point. duration_s: end-start for ended sessions,
    elapsed time for running ones, None for open and point."""
    now = now or datetime.now().astimezone()
    ends = {}
    for e in events:
        if e["phase"] == "end" and e["id"] not in ends:
            ends[e["id"]] = e
    out = []
    for e in events:
        if e["phase"] == "point":
            out.append({"id": e["id"], "name": e["name"], "cats": e["cats"], "start": e["t"], "end": None,
                        "state": "point", "duration_s": None, "note": e["note"], "end_note": ""})
        elif e["phase"] == "start":
            x = ends.get(e["id"])
            if x is not None:
                d = max(0.0, (x["t"] - e["t"]).total_seconds())
                out.append({"id": e["id"], "name": e["name"], "cats": e["cats"], "start": e["t"], "end": x["t"],
                            "state": x["status"] or "done", "duration_s": d, "note": e["note"], "end_note": x["note"]})
            else:
                age = (now - e["t"]).total_seconds()
                stale = age > float(open_limit_hours) * 3600
                out.append({"id": e["id"], "name": e["name"], "cats": e["cats"], "start": e["t"], "end": None,
                            "state": "open" if stale else "running", "duration_s": None if stale else max(0.0, age),
                            "note": e["note"], "end_note": ""})
    return out


def load_samples(path: str) -> List[Tuple[datetime, Optional[float]]]:
    """timestamp,pct CSV (samples.csv / hourly.csv); 'unavailable' -> None."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = _parse_ts(r.get("timestamp"))
            if t is None:
                continue
            try:
                v: Optional[float] = float(r.get("pct"))
            except (TypeError, ValueError):
                v = None
            out.append((t, v))
    out.sort(key=lambda x: x[0])
    return out


def pct_at(samples: Sequence[Tuple[datetime, Optional[float]]], t: datetime,
           tolerance_min: float = DEFAULT_USAGE_TOLERANCE_MIN) -> Optional[float]:
    """Weekly % of the valid sample nearest to t (within the tolerance), else None."""
    best, bd = None, None
    for st, v in samples:
        if v is None:
            continue
        d = abs((st - t).total_seconds())
        if d <= tolerance_min * 60 and (bd is None or d < bd):
            best, bd = v, d
    return best


def usage_delta(samples, start: datetime, end: Optional[datetime],
                tolerance_min: float = DEFAULT_USAGE_TOLERANCE_MIN) -> Dict:
    """{'status': ok|unavailable, 'start_pct', 'end_pct', 'delta', 'reason'}; never guessed."""
    if end is None:
        return {"status": "unavailable", "start_pct": None, "end_pct": None, "delta": None, "reason": "not ended"}
    a, b = pct_at(samples, start, tolerance_min), pct_at(samples, end, tolerance_min)
    if a is None or b is None:
        return {"status": "unavailable", "start_pct": a, "end_pct": b, "delta": None, "reason": "no reading near start/end"}
    if b + 0.5 < a:
        return {"status": "unavailable", "start_pct": a, "end_pct": b, "delta": None, "reason": "weekly reset in between"}
    return {"status": "ok", "start_pct": a, "end_pct": b, "delta": round(b - a, 1), "reason": ""}


def attach_usage(sess: List[Dict], samples, tolerance_min: float = DEFAULT_USAGE_TOLERANCE_MIN) -> List[Dict]:
    for s in sess:
        s["usage"] = usage_delta(samples, s["start"], s["end"], tolerance_min) if s["state"] in STATUSES else None
    return sess


def matches(s: Dict, categories: Optional[Iterable[str]] = None, name: Optional[str] = None) -> bool:
    """Filter: any selected category matches (empty selection = all)."""
    want = set(c for c in (categories or []) if c)
    return (not name or s["name"] == name) and (not want or bool(want & set(s["cats"])))


def summarize(sess: Iterable[Dict], since: Optional[datetime] = None) -> Dict:
    """Per-category: count, total_s (full session time under every category it has, so category
    totals can overlap), interrupted, open (past the limit), running. Overall total_s counts each
    session once. Only ended sessions add time."""
    by: Dict[str, Dict] = {}
    total, n = 0.0, 0
    for s in sess:
        if since is not None and s["start"] < since:
            continue
        n += 1
        d = s["duration_s"] if s["state"] in STATUSES else 0.0
        total += d or 0.0
        for c in s["cats"]:
            b = by.setdefault(c, {"count": 0, "total_s": 0.0, "interrupted": 0, "open": 0, "running": 0})
            b["count"] += 1
            b["total_s"] += d or 0.0
            b["interrupted"] += s["state"] == "interrupted"
            b["open"] += s["state"] == "open"
            b["running"] += s["state"] == "running"
    return {"count": n, "total_s": total, "by_category": by}


def fmt_duration(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    m = int(round(sec / 60.0))
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


# ------------------------------------------------------------------ search
def parse_query(q: str) -> List[str]:
    """Keyword box -> lower-case words; every word must match (AND). Plain substring
    matching, so CJK text needs no word splitting."""
    return [w.casefold() for w in str(q or "").split() if w.strip()]


def search(sess: Iterable[Dict], q: str = "", label=None, since: Optional[datetime] = None,
           until: Optional[datetime] = None, categories: Optional[Iterable[str]] = None) -> List[Dict]:
    """Sessions whose name, note, end note/reason or category labels contain every word of q
    (case-insensitive), optionally limited to categories (any match) and a start-date range."""
    terms = parse_query(q)
    label = label or (lambda c: c)
    out = []
    for s in sess:
        if since is not None and s["start"] < since:
            continue
        if until is not None and s["start"] > until:
            continue
        if not matches(s, categories):
            continue
        if terms:
            hay = " \n".join([s["name"], s.get("note") or "", s.get("end_note") or ""]
                             + [str(label(c)) for c in s["cats"]]).casefold()
            if not all(t in hay for t in terms):
                continue
        out.append(s)
    return out


def split_list(v) -> List[str]:
    """'a,b' / 'a|b' / ['a', 'b,c'] -> ['a', 'b', 'c'] (for API params and CLI flags)."""
    if v is None:
        return []
    vals = v if isinstance(v, (list, tuple)) else [v]
    return [p.strip() for x in vals for p in str(x).replace("|", ",").split(",") if p.strip()]


def to_json(s: Dict, label=None, alias=None, privacy: bool = False) -> Dict:
    """One session as plain JSON (ISO times with offset). Privacy: alias names, drop notes."""
    label = label or (lambda c: c)
    u = s.get("usage") or None
    return {"id": s["id"], "name": (alias or {}).get(s["name"], s["name"]),
            "categories": s["cats"], "category_labels": [label(c) for c in s["cats"]],
            "state": s["state"], "start": s["start"].isoformat(timespec="seconds"),
            "end": s["end"].isoformat(timespec="seconds") if s["end"] else None,
            "duration_min": round(s["duration_s"] / 60.0, 1) if s.get("duration_s") is not None else None,
            "note": "" if privacy else s.get("note", ""), "end_note": "" if privacy else s.get("end_note", ""),
            "usage": u}


# ------------------------------------------------------------------ per-day view
def day_usage_rise(samples, day_start: datetime, day_end: datetime,
                   tolerance_min: float = DEFAULT_USAGE_TOLERANCE_MIN) -> Optional[float]:
    """Actual weekly-% rise during [day_start, day_end) from the sampled readings: the sum of
    increases between consecutive valid readings (a weekly reset counts from the new value).
    The last reading shortly before the day is used as its starting point. None when there are
    fewer than two readings to compare."""
    pts = [(t, v) for t, v in samples if v is not None and day_start <= t < day_end]
    before = [(t, v) for t, v in samples if v is not None and t < day_start
              and (day_start - t).total_seconds() <= tolerance_min * 60]
    if before:
        pts.insert(0, before[-1])
    if len(pts) < 2:
        return None
    rise = 0.0
    for (_, a), (_, b) in zip(pts, pts[1:]):
        rise += (b - a) if b >= a else b
    return round(rise, 1)


def group_by_day(results: Iterable[Dict], samples, tz, tolerance_min: float = DEFAULT_USAGE_TOLERANCE_MIN) -> List[Dict]:
    """Search results grouped by local start day (tz), newest day first; sessions inside a day
    in time order. Subtotal: sessions, total time (each session once; only ended sessions add
    time) and the day's real weekly-% rise (not a sum of possibly overlapping sessions)."""
    days: Dict[str, List[Dict]] = {}
    for s in results:
        days.setdefault(s["start"].astimezone(tz).strftime("%Y-%m-%d"), []).append(s)
    out = []
    for key in sorted(days, reverse=True):
        d0 = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=tz)
        rise = day_usage_rise(samples, d0, d0 + timedelta(days=1), tolerance_min)
        ss = sorted(days[key], key=lambda x: x["start"])
        out.append({"date": key, "sessions": ss, "count": len(ss),
                    "total_s": sum((x["duration_s"] or 0.0) for x in ss if x["state"] in STATUSES),
                    "usage_rise": rise, "usage_status": "ok" if rise is not None else "unavailable"})
    return out


def day_to_json(day: Dict, label=None, alias=None, privacy: bool = False) -> Dict:
    return {"date": day["date"], "count": day["count"], "total_min": round(day["total_s"] / 60.0, 1),
            "usage_rise": day["usage_rise"], "usage_status": day["usage_status"],
            "sessions": [to_json(s, label, alias, privacy) for s in day["sessions"]]}
