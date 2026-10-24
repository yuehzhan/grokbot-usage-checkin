#!/usr/bin/env python3
"""Shared usage ledger: one small JSON file that any assistant/tool can read instead of
re-reading Grok Build (stdlib only).

Every sample writes the ledger atomically (tmp file + os.replace) while holding the shared
write lock of its data directory (<dir>/.write.lock, see usage_lock.py):

  {
    "schema": 1,
    "asOf": "2026-10-24T03:16:36+02:00",     # when this read was attempted (ISO 8601 + offset)
    "status": "ok" | "unavailable",
    "error": null | "reason",
    "weeklyPercentUsed": 4.0 | null,           # null when unavailable -- never 0, never guessed
    "resetsAt": "2026-10-31T09:00:00+02:00" | null,   # only what Grok Build displayed this read
    "periodStart": "...+02:00" | null,
    "source": "sampler" | "refresh" | "record" | "manual" | ...,
    "consecutiveFailures": 0,
    "lastGood": {"asOf", "weeklyPercentUsed", "resetsAt", "periodStart", "source"} | null,
    "spike": {"flag": false, "points": 3.0 | null, "since": "..." | null, "threshold": 20},
    "overBudget": {"flag": false, "threshold": 90},
    "alerts": [{"id": "spike:2026-10-24", "kind": "spike", "title": "...", "message": "..."}],
    "staleAfterHours": 6, "timezone": "UTC+2"
  }

New alerts (once per id; ids are per display-timezone day) are appended to an alerts log
(JSON lines) and optionally shown as a desktop toast (desktop_notify.py).

CLI for assistants:  python3 usage_ledger.py read PATH [--max-age-hours 6]
  -> prints the ledger plus {"fresh": bool, "ageHours": x}; exit 0 fresh, 3 stale/missing.
"""
import json
import os
import sys
from datetime import datetime, timedelta

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_dates  # noqa: E402
import usage_lock  # noqa: E402

try:
    import desktop_notify
except Exception:  # module next to this file; keep working without it
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import desktop_notify
    except Exception:
        desktop_notify = None

DEFAULTS = {"daily_spike_points": 20.0, "over_budget_percent": 90.0, "unavailable_after_failures": 3,
            "stale_after_hours": 6.0, "desktop_notify": True}

TEXT = {
    "en": {"title": "Usage alert", "spike": "+{p:g} pts in 24h, now {pct:g}% · {reset}",
           "budget": "Weekly usage {pct:g}% (≥ {th:g}%) · {reset}",
           "unavail": "Usage unavailable {n}× in a row · {last}",
           "resets": "resets {date} ({z})", "reset_unknown": "reset unknown",
           "last": "last good {pct:g}% at {t}", "no_last": "no good reading yet",
           "wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "dl": "en"},
    "zh-TW": {"title": "用量提醒", "spike": "24 小時內上升 {p:g} 點，目前 {pct:g}%・{reset}",
              "budget": "每週用量 {pct:g}%（≥ {th:g}%）・{reset}",
              "unavail": "連續 {n} 次無法取得用量・{last}",
              "resets": "重置 {date}（{z}）", "reset_unknown": "重置時間未知",
              "last": "最後成功 {pct:g}%（{t}）", "no_last": "尚無成功紀錄",
              "wd": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"], "dl": "zh-TW"},
    "numeric": {"title": "▲", "spike": "Δ24h +{p:g} → {pct:g}% · {reset}", "budget": "{pct:g}% ≥ {th:g}% · {reset}",
                "unavail": "∅ ×{n} · {last}", "resets": "↻ {date}", "reset_unknown": "↻ ?",
                "last": "✓ {pct:g}% @ {t}", "no_last": "✓ —", "wd": ["1", "2", "3", "4", "5", "6", "7"], "dl": "numeric"},
}
TEXT["zh-CN"] = dict(TEXT["zh-TW"], title="用量提醒", spike="24 小时内上升 {p:g} 点，目前 {pct:g}%・{reset}",
                     budget="每周用量 {pct:g}%（≥ {th:g}%）・{reset}", unavail="连续 {n} 次无法获取用量・{last}",
                     resets="重置 {date}（{z}）", reset_unknown="重置时间未知", last="最后成功 {pct:g}%（{t}）",
                     no_last="尚无成功记录", wd=["周一", "周二", "周三", "周四", "周五", "周六", "周日"], dl="zh-CN")


def offset_label(dt):
    off = dt.utcoffset() or timedelta(0)
    m = int(off.total_seconds() // 60); s = "+" if m >= 0 else "-"; m = abs(m)
    return f"UTC{s}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def _parse(s):
    try:
        d = datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.astimezone()
    except Exception:
        return None


def _num(v, default):
    try:
        return float(v)
    except Exception:
        return default


class _Lock:
    def __init__(self, path):
        self.f = open(path, "a+")

    def __enter__(self):
        try:
            import fcntl; fcntl.flock(self.f, fcntl.LOCK_EX)
        except ImportError:
            import msvcrt, time
            while True:
                try:
                    self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1); break
                except OSError:
                    time.sleep(0.2)
        return self

    def __exit__(self, *a):
        try:
            import fcntl; fcntl.flock(self.f, fcntl.LOCK_UN)
        except ImportError:
            try:
                import msvcrt; self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.f.close()


def atomic_write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path)); os.makedirs(d, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2); f.write("\n")
    os.replace(tmp, path)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def compute_spike(samples, now, pct, period_start, threshold, window_h=24.0, max_extra_h=12.0):
    """Jump in points vs the value ~24h earlier within the same quota period.

    samples: [(aware datetime, pct)] of *good* earlier readings. A drop in % (or the period start)
    counts as a reset: earlier readings are ignored and the baseline becomes 0 at the reset time.
    Returns {"flag", "points", "since", "threshold"}; points None if there's no usable baseline.
    """
    out = {"flag": False, "points": None, "since": None, "threshold": threshold}
    if pct is None:
        return out
    seq = sorted((t, float(p)) for t, p in samples if t <= now and (period_start is None or t >= period_start))
    seq.append((now, float(pct)))
    reset_at = period_start
    i = 1  # a value that goes down means a quota reset happened in between
    while i < len(seq):
        if seq[i][1] < seq[i - 1][1] - 0.5:
            reset_at = seq[i - 1][0] + (seq[i][0] - seq[i - 1][0]) / 2; seq = seq[i:]; i = 1
        else:
            i += 1
    target = now - timedelta(hours=window_h)
    older = [s for s in seq[:-1] if s[0] <= target]
    if older and older[-1][0] >= target - timedelta(hours=max_extra_h):
        base_t, base_p = older[-1]
    elif reset_at is not None and reset_at > target:
        base_t, base_p = reset_at, 0.0  # period (re)started within the last 24h
    else:
        return out
    pts = round(float(pct) - base_p, 1)
    out.update(points=pts, since=_iso(base_t), flag=pts >= threshold)
    return out


def _reset_text(T, reset, tz, privacy):
    if not reset:
        return T["reset_unknown"]
    r = reset.astimezone(tz) if (tz and reset.tzinfo) else reset  # the given time as-is, only shown in local time
    return T["resets"].format(date=usage_dates.reset_date(r, T.get("dl", "en")), wd=T["wd"][r.weekday()],
                              hm=f"{r:%H:%M}", z=offset_label(r) if r.tzinfo else "")


def update(path, *, pct, reset_at=None, period_start=None, source="sampler", error=None, samples=(),
           cfg=None, lang="en", tz=None, now=None, alerts_log=None, alerts_state=None, notify=None, lock_dir=None):
    """Write one sample (or a failure when pct is None) to the ledger. Returns the ledger dict.

    ``lock_dir``: directory whose shared write lock (usage_lock) guards the write; default is the
    ledger's own directory."""
    c = dict(DEFAULTS); c.update({k: v for k, v in (cfg or {}).items() if v is not None})
    th_spike = _num(c["daily_spike_points"], 20.0); th_budget = _num(c["over_budget_percent"], 90.0)
    n_fail = max(1, int(_num(c["unavailable_after_failures"], 3)))
    tz = tz or datetime.now().astimezone().tzinfo
    now = (now or datetime.now(tz)).astimezone(tz)
    T = TEXT.get(lang) or (TEXT["zh-TW"] if str(lang).startswith("zh") else TEXT["en"])
    privacy = lang == "numeric"
    alerts_log = alerts_log or os.path.join(os.path.dirname(os.path.abspath(path)), "alerts.log")
    alerts_state = alerts_state or os.path.join(os.path.dirname(os.path.abspath(path)), "alerts-state.json")
    with usage_lock.write_lock(lock_dir or os.path.dirname(os.path.abspath(path))):
        prev = load_json(path) or {}
        ok = pct is not None
        if ok:
            pct = float(pct)
        ps = period_start or (reset_at - timedelta(days=7) if reset_at else None)
        led = {
            "schema": 1, "asOf": _iso(now), "status": "ok" if ok else "unavailable",
            "error": None if ok else (str(error or "no percentage shown")[:300]),
            "weeklyPercentUsed": pct if ok else None,
            "resetsAt": _iso(reset_at.astimezone(tz)) if (ok and reset_at) else None,
            "periodStart": _iso(ps.astimezone(tz)) if (ok and ps) else None,
            "source": source,
            "consecutiveFailures": 0 if ok else int(prev.get("consecutiveFailures") or 0) + 1,
        }
        if ok:
            led["lastGood"] = {k: led[k] for k in ("asOf", "weeklyPercentUsed", "resetsAt", "periodStart", "source")}
        else:
            led["lastGood"] = prev.get("lastGood") if prev.get("lastGood") else (
                {k: prev.get(k) for k in ("asOf", "weeklyPercentUsed", "resetsAt", "periodStart", "source")} if prev.get("status") == "ok" else None)
        led["spike"] = compute_spike(list(samples), now, pct if ok else None, ps, th_spike)
        led["overBudget"] = {"flag": bool(ok and pct >= th_budget), "threshold": th_budget}
        led["staleAfterHours"] = _num(c["stale_after_hours"], 6.0)
        led["timezone"] = offset_label(now)
        day = f"{now:%Y-%m-%d}"; rtxt = _reset_text(T, reset_at if ok else None, tz, privacy)
        alerts = []
        if led["spike"]["flag"]:
            alerts.append({"id": f"spike:{day}", "kind": "spike", "title": T["title"],
                           "message": T["spike"].format(p=led["spike"]["points"], pct=pct, reset=rtxt)})
        if led["overBudget"]["flag"]:
            alerts.append({"id": f"budget:{day}", "kind": "over_budget", "title": T["title"],
                           "message": T["budget"].format(pct=pct, th=th_budget, reset=rtxt)})
        if not ok and led["consecutiveFailures"] >= n_fail:
            lg = led["lastGood"]
            if lg and lg.get("weeklyPercentUsed") is not None:
                lt = _parse(lg.get("asOf")); lt = lt.astimezone(tz) if lt else None
                last = T["last"].format(pct=float(lg["weeklyPercentUsed"]), t=f"{lt:%m-%d %H:%M}" if lt else "?")
            else:
                last = T["no_last"]
            alerts.append({"id": f"unavailable:{day}", "kind": "unavailable", "title": T["title"],
                           "message": T["unavail"].format(n=led["consecutiveFailures"], last=last)})
        led["alerts"] = alerts
        atomic_write_json(path, led)
        # once-per-id dedupe shared by the alerts log and the desktop toast
        st = load_json(alerts_state) or {}
        sent = st.get("sent") or {}
        cutoff = now - timedelta(days=14)
        sent = {k: v for k, v in sent.items() if (_parse(v) or now) >= cutoff}
        new = [a for a in alerts if a["id"] not in sent]
        for a in new:
            res = "noop:disabled"
            if c.get("desktop_notify") not in (False, "false", "0", 0):
                try:
                    res = (notify or (desktop_notify.notify if desktop_notify else (lambda *x, **k: "noop:no module")))(a["title"], a["message"])
                except Exception as exc:
                    res = f"error:{type(exc).__name__}"
            sent[a["id"]] = _iso(now)
            with open(alerts_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": _iso(now), **a, "notify": res}, ensure_ascii=False) + "\n")
        if new or sent != (st.get("sent") or {}):
            atomic_write_json(alerts_state, {"sent": sent})
        led["_newAlerts"] = [a["id"] for a in new]
    return led


# ------------------------------------------------------------------ usage-hud glue
ALERT_KEYS = ("daily_spike_points", "over_budget_percent", "unavailable_after_failures", "stale_after_hours", "desktop_notify")


def hud_ledger_path(data_dir, cfg):
    p = (cfg or {}).get("ledger_path")
    return os.path.abspath(os.path.expanduser(str(p))) if p else os.path.join(str(data_dir), "ledger.json")


def hud_samples(data_dir):
    """Good readings from <data_dir>/samples.csv ('unavailable' rows are skipped)."""
    import csv
    p = os.path.join(str(data_dir), "samples.csv"); out = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = _parse((r.get("timestamp") or "").strip())
                try:
                    out.append((t, float(r["pct"]))) if t else None
                except Exception:
                    continue
    return out


def _hud_period_start(data_dir, now, period_days):
    """Period start from the reset Grok Build reported earlier (<data_dir>/state.json), else None."""
    st = load_json(os.path.join(str(data_dir), "state.json")) or {}
    r = _parse(st.get("reset_at")) if st.get("reset_at") else None
    if not r:
        return None
    step = timedelta(days=period_days)
    k = (now - r) // step
    return r + k * step


def record_hud(data_dir, cfg, pct, reset_at=None, source="sampler", error=None, tz=None, lang="en",
               exclude_last=False, now=None):
    cfg = cfg or {}
    tz = tz or datetime.now().astimezone().tzinfo
    now = (now or datetime.now(tz)).astimezone(tz)
    samples = hud_samples(data_dir)
    if exclude_last and pct is not None and samples:
        samples = samples[:-1]
    ps = None
    if reset_at is None and pct is not None:
        ps = _hud_period_start(data_dir, now, int(cfg.get("period_days", 7) or 7))
    elif reset_at is not None:
        ps = reset_at - timedelta(days=int(cfg.get("period_days", 7) or 7))
    lg = "numeric" if lang == "numeric" else (usage_dates.lang_key(lang) if str(lang).startswith("zh") else "en")
    return update(hud_ledger_path(data_dir, cfg), pct=pct, reset_at=reset_at, period_start=ps, source=source, error=error,
                  samples=samples, cfg={k: cfg.get(k) for k in ALERT_KEYS}, lang=lg, tz=tz, now=now,
                  alerts_log=os.path.join(str(data_dir), "alerts.log"),
                  alerts_state=os.path.join(str(data_dir), "alerts-state.json"), lock_dir=str(data_dir))


def read(path, max_age_hours=None, now=None):
    led = load_json(path)
    now = now or datetime.now().astimezone()
    if not led:
        return {"fresh": False, "ageHours": None, "ledger": None}
    t = _parse(led.get("asOf"))
    age = round((now - t).total_seconds() / 3600, 2) if t else None
    mx = _num(max_age_hours if max_age_hours is not None else led.get("staleAfterHours"), 6.0)
    return {"fresh": age is not None and age <= mx, "ageHours": age, "ledger": led}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Read the usage ledger (freshness check for assistants)")
    ap.add_argument("cmd", choices=["read"]); ap.add_argument("path")
    ap.add_argument("--max-age-hours", type=float, default=None)
    a = ap.parse_args()
    r = read(a.path, a.max_age_hours)
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r["fresh"] else 3)
