#!/usr/bin/env python3
"""usage_history.py - long-range history, interval sampler, 24h clock chart, check-ins, and a
localhost viewer for usage-hud. Standard library + matplotlib only. Existing usage_record.py /
usage_view.py / chart_hud.py behavior is unchanged; this tool only *reads* their week-*.csv files
and state.json, and writes its own files under <data_dir>.

Subcommands (all accept --config / --data-dir / --tz / --lang / --privacy / --theme):
  history                 Render <data_dir>/history/history-{1m,3m,6m,1y}.png + index.html
  clock                   Render <data_dir>/history/clock-7d.png and clock-all.png
  sample [--pct N]        Take one reading -> <data_dir>/samples.csv, then re-render (failure-safe)
  sampler                 Run `sample` every sample_interval_minutes (single instance, aligned)
  checkin NAME [NOTE]     Append a check-in to <data_dir>/checkins.csv (--category ID, default other)
  serve                   Localhost viewer: history page, manual refresh, check-in API
  status                  Print sampler/server status JSON

Files written: samples.csv (timestamp ISO-8601 with UTC offset, pct), checkins.csv
(timestamp, name, note), history/* (history/pal/<palette>/* for the page's palette switcher),
sampler.pid, history_server.json, history_server.token (mode 600), sampler.log.
Every CSV/JSON write holds <data_dir>/.write.lock (usage_lock.py).
"""
import argparse, base64, calendar, csv, html, json, math, os, signal, subprocess, sys, threading, time
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import local_guard  # noqa: E402  (token / Host / Origin checks for the viewer)
import usage_lock  # noqa: E402  (one shared write lock per data dir)
import usage_palette  # noqa: E402  (orange = over, cool colour = under, grey baseline)
import usage_categories  # noqa: E402  (check-in categories; unknown -> other)
import usage_checkins  # noqa: E402  (check-in points and start/end sessions)
import usage_checkins_ui  # noqa: E402  (check-in tab: HTML/CSS/JS)
import proc_util  # noqa: E402  (Windows-safe "is this pid alive?")

INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 15, 1440, 60
RANGES = [("1m", 1, 3), ("3m", 3, 10), ("6m", 6, 20), ("1y", 12, 40)]  # key, months, min weeks
CHECKIN_PALETTE = ["#4D8DFF", "#FFE45C", "#FF8AD8", "#8C9EFF", "#B8F55A", "#C9A0FF", "#F5F5F5", "#7CB8FF"]

STR = {
    "en": {
        "eyebrow": "Usage · Check-in · Search", "title": "Grok Bot Usage & Check-in", "png_title": "Usage History", "r1m": "1 Month", "r3m": "Quarter",
        "r6m": "Half Year", "r1y": "1 Year", "clock_tab": "Clock", "checkin_tab": "Check-ins",
        "weeks": "Weeks", "avg_peak": "Avg weekly peak", "hit100": "Weeks at 100%", "extra": "Extra spend",
        "over_n": "{n} over pace", "pace_sub": "pace {p:g}%/day -> {w:g}%/week", "cap_sub": "hit the weekly cap",
        "extra_sub": "{n} month(s) with spend", "main": "Weekly peak vs pace, with daily cumulative usage",
        "leg_over": "Peak over pace", "leg_under": "Peak within pace", "leg_pace": "Pace ({p:g}%/day)",
        "leg_daily": "Daily cumulative", "sparse": "Not enough data: {n} week(s) (want >= {m})",
        "sparse_short": "Not enough data ({n} week(s)); trend is indicative only", "empty": "No data in this range",
        "spend_title": "Extra spend per month (cumulative max within month)", "no_spend": "No extra spend recorded",
        "footer": "generated {t} ({z})  ·  pace {p:g}%/day  ·  * = week not complete",
        "clock_eyebrow": "USAGE CLOCK // PER HOUR", "clock_7d": "Usage Clock — last 7 days", "clock_all": "Usage Clock — all time",
        "clock_peak": "Peak {h}:00–{h2}:00  avg +{v:.1f}%/h  ({n} intervals)", "clock_none": "No valid intervals yet",
        "collecting": "Collecting data ({n}/{m})", "clock_note": "Times in {z} · length/color = avg % increase in that hour · resets counted from new value · long gaps skipped",
        "ck_legend": "Check-ins ({n})", "last": "Last update: {t} ({z})", "refresh": "Refresh now", "loading": "Loading…",
        "range": "Range", "name": "Name", "all": "All", "note": "Note", "time": "Time", "recent": "Recent check-ins",
        "per_name": "Per name", "top_hour": "Most common hour", "none_ck": "No check-ins yet", "ck_btn": "Check in",
        "ck_hint": "Open this page through the local server to check in here, or run: usage_history.py checkin <name> [note] --category <id>",
        "category": "Category", "per_cat": "Per category", "count": "Check-ins", "share": "Share", "last_ck": "Last",
        "week": "Week start", "days": "Days", "peak": "Peak", "pace": "Pace", "at100": "100%", "yes": "yes",
        "month": "Month", "amount": "Amount", "weekly_detail": "Weekly detail", "conn_err": "Cannot reach local server",
        "clock_sub": "Average % increase per hour ({z}); 0:00 at top, clockwise", "no_clock": "Collecting data — clock charts appear after sampling starts",
        "hour_fmt": "{h}",
    },
    "zh-TW": {
        "eyebrow": "用量・打卡・搜尋", "title": "Grok Bot Usage & Check-in", "png_title": "用量歷史", "r1m": "一個月", "r3m": "一季", "r6m": "半年", "r1y": "一年",
        "clock_tab": "時鐘", "checkin_tab": "打卡", "weeks": "週數", "avg_peak": "平均週峰值", "hit100": "達 100% 週數",
        "extra": "額外付費", "over_n": "超出基準 {n} 週", "pace_sub": "基準 {p:g}%/天 → 每週 {w:g}%", "cap_sub": "該週用滿上限",
        "extra_sub": "{n} 個月有付費紀錄", "main": "每週峰值用量 vs 基準，與每日累計用量", "leg_over": "週峰值 超出基準",
        "leg_under": "週峰值 未超出", "leg_pace": "基準（{p:g}%/天）", "leg_daily": "每日累計",
        "sparse": "資料不足：{n} 週（建議 ≥ {m} 週）", "sparse_short": "資料不足（目前 {n} 週），趨勢僅供參考",
        "empty": "資料不足 — 此範圍尚無紀錄", "spend_title": "每月額外付費（取當月最高累計）", "no_spend": "此範圍無額外付費紀錄",
        "footer": "產生於 {t}（{z}）· 基準 {p:g}%/天 · * = 該週未滿",
        "clock_eyebrow": "USAGE CLOCK // 每小時用量", "clock_7d": "用量時鐘 — 近 7 天", "clock_all": "用量時鐘 — 全部",
        "clock_peak": "尖峰 {h}:00–{h2}:00　平均 +{v:.1f}%/小時　（{n} 個區間）", "clock_none": "尚無有效區間",
        "collecting": "資料收集中（{n}/{m}）", "clock_note": "時刻為 {z} · 長度/顏色 = 該小時平均用量增加 · 週重置以新值計 · 間隔過長略過",
        "ck_legend": "打卡（{n} 次）", "last": "最後更新：{t}（{z}）", "refresh": "立即更新", "loading": "讀取中…",
        "range": "範圍", "name": "名字", "all": "全部", "note": "備註", "time": "時間", "recent": "最近打卡", "per_name": "各名字統計",
        "top_hour": "最常時段", "none_ck": "尚無打卡", "ck_btn": "打卡",
        "ck_hint": "以本機服務開啟此頁才能在這裡打卡；或執行：usage_history.py checkin <名字> [備註] --category <類別id>",
        "category": "類別", "per_cat": "各類別統計", "count": "次數", "share": "占比", "last_ck": "最近一次",
        "week": "週起始", "days": "天數", "peak": "峰值", "pace": "基準", "at100": "達 100%", "yes": "是",
        "month": "月份", "amount": "金額", "weekly_detail": "每週明細", "conn_err": "無法連線到本機服務",
        "clock_sub": "每小時平均用量增加（{z}），0 時在上、順時針", "no_clock": "資料收集中 — 開始取樣後才會產生時鐘圖",
        "hour_fmt": "{h}時",
    },
}
# privacy / numeric: word-free symbols only (no service, product, timezone or city names)
STR["numeric"] = dict(STR["en"], **{
    "eyebrow": "", "title": "", "r1m": "1M", "r3m": "3M", "r6m": "6M", "r1y": "1Y", "clock_tab": "◷", "checkin_tab": "✓",
    "weeks": "#", "avg_peak": "Ø ▲", "hit100": "▲100", "extra": "∑ $", "over_n": "▲ {n}", "pace_sub": "{p:g}/d → {w:g}",
    "cap_sub": "▲100", "extra_sub": "{n}", "main": "", "leg_over": "▲", "leg_under": "▼", "leg_pace": "{p:g}/d", "leg_daily": "∑",
    "sparse": "{n}/{m}", "sparse_short": "{n}/{m}", "empty": "∅", "spend_title": "$", "no_spend": "∅", "footer": "{t}",
    "clock_eyebrow": "", "clock_7d": "◷ 7", "clock_all": "◷ ∞", "clock_peak": "▲ {h}–{h2}  +{v:.1f}  ({n})", "clock_none": "∅",
    "collecting": "{n}/{m}", "clock_note": "", "ck_legend": "✓ {n}", "last": "↻ {t}", "refresh": "↻", "loading": "…",
    "range": "", "name": "", "all": "∗", "note": "", "time": "", "recent": "✓", "per_name": "∑", "top_hour": "▲",
    "none_ck": "∅", "ck_btn": "✓", "ck_hint": "", "week": "", "days": "D", "peak": "▲", "pace": "→", "at100": "100",
    "yes": "▲", "month": "", "amount": "$", "weekly_detail": "", "conn_err": "×", "clock_sub": "", "no_clock": "∅", "hour_fmt": "{h}",
})


# ------------------------------------------------------------------ config
class Ctx:
    """Resolved settings. Precedence: CLI flags > config JSON > defaults (same as usage_record.py)."""

    def __init__(self, args):
        self.cfg_path = Path(args.config) if getattr(args, "config", None) else (Path("usage_config.json") if Path("usage_config.json").exists() else None)
        self.args = args
        self.reload()

    def reload(self):
        cfg = {}
        if self.cfg_path and self.cfg_path.exists():
            try:
                cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"Warning: cannot parse config '{self.cfg_path}': {exc}", file=sys.stderr)
        self.cfg = cfg
        a = self.args
        g = lambda flag, key, default=None: getattr(a, flag, None) if getattr(a, flag, None) is not None else cfg.get(key, default)
        self.data_dir = Path(g("data_dir", "data_dir", os.environ.get("USAGE_DATA_DIR") or "./usage-data")).resolve()
        self.hist_dir = self.data_dir / "history"
        self.privacy = bool(g("privacy", "privacy", False)) or str(g("lang", "lang", "auto")).lower() == "numeric"
        self.pace = float(g("pace", "pace", 14))
        self.period_days = int(cfg.get("period_days", 7))
        self.tz_spec = str(g("tz", "tz", "local") or "local")
        self.tz = self._tz(self.tz_spec)
        lang = str(g("lang", "lang", "auto") or "auto")
        if self.privacy:
            lang = "numeric"
        elif lang.lower() == "auto":
            try:
                import chart_hud
                lang = chart_hud.detect_lang()
            except Exception:
                lang = "en"
        self.lang = lang
        self.L = STR.get(lang) or (STR["zh-TW"] if lang.startswith("zh") else STR["en"])
        self.interval = self._interval(cfg.get("sample_interval_minutes", INTERVAL_DEFAULT))
        self.host = str(g("host", "history_host", "127.0.0.1"))
        self.port = int(g("port", "history_port", 0))
        self.refresh_cooldown = int(cfg.get("manual_refresh_cooldown_seconds", 60))
        self.checkin_cooldown = int(cfg.get("checkin_cooldown_seconds", 5))
        self.extra_csv = cfg.get("extra_spend_csv")
        try:
            import usage_ledger
            self.ledger_path = usage_ledger.hud_ledger_path(self.data_dir, cfg)
        except Exception:
            self.ledger_path = str(self.data_dir / "ledger.json")
        self.palette = usage_palette.normalize(g("palette", "palette", usage_palette.DEFAULT))
        self.categories = usage_categories.load(cfg.get("checkin_categories"))
        self.max_categories = max(1, int(cfg.get("checkin_max_categories", usage_checkins.DEFAULT_MAX_CATEGORIES)))
        self.open_limit_hours = float(cfg.get("checkin_open_limit_hours", usage_checkins.DEFAULT_OPEN_LIMIT_HOURS))
        self.theme_path = g("theme", "theme", None)
        self.theme = self._theme(self.theme_path, self.palette)

    def ck_strings(self):
        if self.lang == "numeric":
            return usage_checkins_ui.NUMERIC
        return usage_checkins_ui.ZH_TW if self.lang.startswith("zh") else usage_checkins_ui.EN

    def cat_label(self, cid):
        """Display label: English, zh-TW for zh languages, position number in privacy/numeric mode."""
        cid = self.categories.normalize(cid)
        if self.lang == "numeric":
            return str(self.categories.ids.index(cid) + 1)
        return self.categories.label(cid, self.lang)

    def theme_for(self, palette):
        return self._theme(self.theme_path, palette)

    @staticmethod
    def _interval(v):
        try:
            iv = int(float(v))
        except Exception:
            iv = INTERVAL_DEFAULT
        if not INTERVAL_MIN <= iv <= INTERVAL_MAX:
            print(f"Warning: sample_interval_minutes={v} out of range, clamped to {INTERVAL_MIN}..{INTERVAL_MAX}", file=sys.stderr)
            iv = max(INTERVAL_MIN, min(INTERVAL_MAX, iv))
        return iv

    @staticmethod
    def _tz(spec) -> tzinfo:
        if spec.strip().lower() in ("", "local", "auto"):
            return datetime.now().astimezone().tzinfo
        from zoneinfo import ZoneInfo
        try:
            return ZoneInfo(spec)
        except Exception:
            print(f"Warning: invalid timezone '{spec}', using machine local timezone", file=sys.stderr)
            return datetime.now().astimezone().tzinfo

    @staticmethod
    def _theme(path, palette=None):
        # over/under/baseline/notice come from the palette unless a theme file sets them explicitly
        t = {"bg_color": "#000000", "text_color": "#E8F4FF", "accent_color": "#7FE7FF", "baseline_color": None,
             "under_color": None, "over_color": None, "notice_color": None}
        if path:
            p = Path(path) if Path(path).is_absolute() else (HERE / path if (HERE / path).exists() else Path(path))
            try:
                for k, v in json.loads(p.read_text(encoding="utf-8")).items():
                    if k in t and v:
                        t[k] = v
            except Exception as exc:
                print(f"Warning: cannot load theme '{path}': {exc}", file=sys.stderr)
        from matplotlib.colors import to_rgb
        r, g_, b = to_rgb(t["bg_color"])
        t["is_light"] = (0.299 * r + 0.587 * g_ + 0.114 * b) > 0.5
        pal = usage_palette.get(palette, t["is_light"])
        for k in ("over", "under", "baseline", "notice"):
            t[k + "_color"] = t[k + "_color"] or pal[k]
        t["palette"] = usage_palette.normalize(palette)
        t["card"] = _blend(t["bg_color"], t["text_color"], 0.05)
        t["border"] = _blend(t["bg_color"], t["accent_color"], 0.25)
        t["dim"] = _blend(t["text_color"], t["bg_color"], 0.35)
        t["grid"] = _blend(t["bg_color"], t["text_color"], 0.25)
        return t

    # time helpers
    def now(self):
        return datetime.now(self.tz)

    def zlabel(self, dt=None):
        dt = dt or self.now()
        off = dt.utcoffset() or timedelta(0)
        m = int(off.total_seconds() // 60); s = "+" if m >= 0 else "-"; m = abs(m)
        lab = f"UTC{s}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")
        if self.privacy:
            return lab
        return lab if self.tz_spec.lower() in ("local", "auto", "", "utc") else f"{self.tz_spec} {lab}"

    def disp(self, dt):
        """aware -> display tz; naive is treated as machine-local (how week CSVs are written)."""
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.astimezone(self.tz)


def _blend(c1, c2, t):
    from matplotlib.colors import to_rgb, to_hex
    a, b = to_rgb(c1), to_rgb(c2)
    return to_hex(tuple(x + (y - x) * t for x, y in zip(a, b)))


def _months_back(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12; y -= 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


class FileLock:
    """Portable exclusive lock (fcntl on POSIX, msvcrt on Windows)."""

    def __init__(self, path):
        self.path = Path(path); self.fp = None

    def acquire(self, blocking=True, timeout=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fp = open(self.path, "a+")
        t0 = time.time()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fp.seek(0); msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if not blocking or (timeout is not None and time.time() - t0 > timeout):
                    self.fp.close(); self.fp = None
                    return False
                time.sleep(1)

    def release(self):
        if not self.fp:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fp.seek(0); msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fp, fcntl.LOCK_UN)
        finally:
            self.fp.close(); self.fp = None


# ------------------------------------------------------------------ data
def reset_ref(ctx):
    """Reset reference: <data_dir>/state.json (from Grok Build) > config reset_anchor > None."""
    try:
        st = json.loads((ctx.data_dir / "state.json").read_text(encoding="utf-8"))
        return datetime.fromisoformat(st["reset_at"])
    except Exception:
        pass
    anchor = ctx.cfg.get("reset_anchor")
    if anchor:
        try:
            dt = datetime.fromisoformat(str(anchor))
            return dt if dt.tzinfo else dt.replace(tzinfo=ctx.tz)
        except ValueError:
            try:
                wd, hm = str(anchor).split()
                days = [d.lower() for d in calendar.day_abbr]
                idx = next(i for i, d in enumerate(days) if wd.lower().startswith(d))
                h, m = map(int, hm.split(":"))
                base = datetime(2026, 1, 5, h, m, tzinfo=ctx.tz)  # a Monday
                return base + timedelta(days=idx)
            except Exception:
                pass
    return None


def load_weeks(ctx):
    ref = reset_ref(ctx)
    pd = timedelta(days=ctx.period_days)
    weeks = []
    for p in sorted(ctx.data_dir.glob("week-*.csv")):
        try:
            d0 = datetime.strptime(p.stem[5:], "%Y-%m-%d")
        except ValueError:
            continue
        if ref is not None:
            at = d0.replace(hour=23, minute=59).astimezone()
            ws = ref + ((at - ref) // pd) * pd
        else:
            ws = d0.astimezone()
        days = []
        try:
            for r in csv.DictReader(p.open(encoding="utf-8")):
                try:
                    dt = datetime.strptime(r["date"], "%Y-%m-%d").replace(hour=12)
                    note = (r.get("note") or "").strip()
                    if len(note) >= 4 and note[:5].replace(":", "").isdigit() and ":" in note[:5]:
                        hh, mm = note[:5].split(":"); dt = dt.replace(hour=int(hh), minute=int(mm))
                    days.append((int(r["day"]), ctx.disp(dt).replace(tzinfo=None), float(r["pct"])))
                except Exception:
                    continue
        except Exception as exc:
            print(f"Warning: skip {p}: {exc}", file=sys.stderr); continue
        if not days:
            continue
        days.sort()
        peak = max(x[2] for x in days); last = max(x[0] for x in days)
        pref = min(100.0, ctx.pace * min(last, ctx.period_days))
        weeks.append({"start": ctx.disp(ws).replace(tzinfo=None), "days": days, "peak": peak, "last": last,
                      "pace_ref": pref, "over": peak > pref, "hit100": peak >= 100, "complete": last >= ctx.period_days})
    return weeks


def load_extra(ctx):
    """Optional extra-spend CSV: columns time (YYYY-MM-DD HH:MM or ISO), usd_used (monthly cumulative)."""
    out = []
    if not ctx.extra_csv:
        return out
    p = Path(ctx.extra_csv)
    if not p.exists():
        return out
    for r in csv.DictReader(p.open(encoding="utf-8")):
        try:
            out.append((ctx.disp(datetime.fromisoformat(r["time"].strip())).replace(tzinfo=None), float(r["usd_used"])))
        except Exception:
            continue
    return sorted(out)


def load_samples(ctx):
    p = ctx.data_dir / "samples.csv"; out = []
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8")):
            try:
                out.append((ctx.disp(datetime.fromisoformat(r["timestamp"].strip())), float(r["pct"])))
            except Exception:
                continue
    return sorted(out)


def _clean(s, n):
    return " ".join(str(s or "").split())[:n]


def add_checkin(ctx, name, note="", category=None, phase="point", session_id=None, status="done", now=None):
    """One check-in event (point, session start or session end); see usage_checkins.
    `category` may be a list, 'a|b' or 'a,b' (1..checkin_max_categories ids)."""
    return usage_checkins.add(str(ctx.data_dir / "checkins.csv"), str(ctx.data_dir), name, phase, category, note,
                              cats=ctx.categories, max_categories=ctx.max_categories, session_id=session_id,
                              status=status, now=now or datetime.now(ctx.tz))


def load_events(ctx):
    return usage_checkins.read(str(ctx.data_dir / "checkins.csv"), ctx.categories)


def load_checkins(ctx):
    """Check-in marks for the charts: points and session starts (end rows are not marks)."""
    out = [{"t": ctx.disp(e["t"]), "name": e["name"], "note": e["note"], "category": e["cats"][0], "cats": e["cats"],
            "phase": e["phase"]} for e in load_events(ctx) if e["phase"] != "end"]
    names = []
    for r in out:
        if r["name"] not in names:
            names.append(r["name"])
    colors = {n: CHECKIN_PALETTE[i % len(CHECKIN_PALETTE)] for i, n in enumerate(names)}
    return out, names, colors


def load_sessions(ctx, now=None):
    """Sessions with duration, state and usage delta (from samples.csv; unavailable if no reading)."""
    sess = usage_checkins.sessions(load_events(ctx), now or ctx.now(), ctx.open_limit_hours)
    samples = usage_checkins.load_samples(str(ctx.data_dir / "samples.csv"))
    usage_checkins.attach_usage(sess, samples, max(usage_checkins.DEFAULT_USAGE_TOLERANCE_MIN, 1.5 * ctx.interval))
    return sess


def public_alias(ctx, names):
    """Privacy mode: names become #1, #2 ... (order of first check-in, same as the chart legend)."""
    return {n: (f"#{i + 1}" if ctx.privacy else n) for i, n in enumerate(names)}


def checkin_page_data(ctx, sums):
    _, names, colors = load_checkins(ctx)
    sess = load_sessions(ctx)
    return usage_checkins_ui.page_data(
        sess, public_alias(ctx, names), colors, ctx.cat_label, ctx.categories.ids, ctx.max_categories, ctx.tz,
        {S["key"]: int(S["start"].replace(tzinfo=ctx.tz).timestamp()) for S in sums}, {S["key"]: S["label"] for S in sums},
        ctx.ck_strings(), usage_checkins.load_samples(str(ctx.data_dir / "samples.csv")),
        {"write": "/api/checkin", "status": "/api/status"}, ctx.privacy, ctx.zlabel())


def public_checkins(ctx, rows, names):
    """Check-ins as shown to readers. Privacy mode: names become #1, #2 ... (order of first
    check-in, same as the chart legend) and free-text notes are dropped."""
    alias = {n: (f"#{i + 1}" if ctx.privacy else n) for i, n in enumerate(names)}
    return [dict(r, name=alias[r["name"]], note="" if ctx.privacy else r["note"]) for r in rows], alias


# ------------------------------------------------------------------ rendering
def _plt(ctx):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        import chart_hud
        chart_hud.configure_fonts_and_locale("en" if ctx.lang == "numeric" else ctx.lang)
        from matplotlib import font_manager
        have = {f.name for f in font_manager.fontManager.ttflist}
        extra = [f for f in ("Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans CJK JP", "Microsoft JhengHei", "PingFang TC") if f in have]
        plt.rcParams["font.family"] = list(plt.rcParams["font.family"]) + [f for f in extra if f not in plt.rcParams["font.family"]]
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def summarize(ctx, weeks, extra, key, months, minw, now):
    start = _months_back(now, months)
    sel = [w for w in weeks if w["start"] + timedelta(days=ctx.period_days) > start and w["start"] <= now]
    spend = {}
    for t, v in extra:
        if start <= t <= now:
            k = f"{t:%Y-%m}"; spend[k] = max(spend.get(k, 0.0), v)
    peaks = [w["peak"] for w in sel]
    return {"key": key, "label": ctx.L["r" + key], "start": start, "end": now, "weeks": sel, "n": len(sel),
            "avg": (sum(peaks) / len(peaks)) if peaks else None, "hit100": sum(w["hit100"] for w in sel),
            "over": sum(w["over"] for w in sel), "spend": dict(sorted(spend.items())), "spend_total": sum(spend.values()),
            "sparse": len(sel) < minw, "minw": minw}


def draw_range(ctx, S, path, now, zl):
    plt = _plt(ctx)
    import matplotlib.dates as mdates
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, FancyBboxPatch
    from matplotlib.ticker import FuncFormatter
    T, L = ctx.theme, ctx.L
    OVER, UNDER, BASE, NOTE = T["over_color"], T["under_color"], T["baseline_color"], T["notice_color"]
    up, dn = usage_palette.UP, usage_palette.DOWN
    fig = plt.figure(figsize=(10.24, 7.68), dpi=100, facecolor=T["bg_color"])
    hdr = fig.add_axes([0, 0, 1, 1]); hdr.set_axis_off(); hdr.set_xlim(0, 1); hdr.set_ylim(0, 1)
    rng = f"{S['start']:%Y-%m-%d} → {S['end']:%Y-%m-%d %H:%M}" + ("" if ctx.privacy else f"（{zl}）" if ctx.lang.startswith("zh") else f" ({zl})")
    if L.get("png_eyebrow", "USAGE HISTORY") and L["title"]:
        hdr.text(0.025, 0.975, L.get("png_eyebrow", "USAGE HISTORY"), color=T["accent_color"], fontsize=9.5, fontweight="bold", va="top")
    hdr.text(0.025, 0.935, (f"{L.get('png_title', L['title'])} — {S['label']}" if L["title"] else S["label"]), color=T["text_color"], fontsize=20, fontweight="bold", va="center")
    hdr.text(0.025, 0.897, rng, color=T["dim"], fontsize=11, fontweight="bold", va="center")
    hdr.plot([0.025, 0.975], [0.878, 0.878], color=T["border"], lw=1.1)
    hdr.plot([0.025, 0.22], [0.878, 0.878], color=T["accent_color"], lw=2.4)
    if S["sparse"]:
        hdr.text(0.975, 0.925, L["sparse"].format(n=S["n"], m=S["minw"]), color=NOTE, fontsize=11, fontweight="bold", ha="right", va="center",
                 bbox=dict(boxstyle="round,pad=0.35", facecolor=_blend(T["bg_color"], NOTE, 0.12), edgecolor=NOTE, lw=1.2))
    wk = min(100, ctx.pace * ctx.period_days)
    avg = "—" if S["avg"] is None else f"{up if S['avg'] > wk else dn} {S['avg']:.0f}%"
    cards = [(L["weeks"], str(S["n"]), L["over_n"].format(n=S["over"]), T["accent_color"]),
             (L["avg_peak"], avg, L["pace_sub"].format(p=ctx.pace, w=wk), OVER if (S["avg"] or 0) > wk else UNDER),
             (L["hit100"], str(S["hit100"]), L["cap_sub"], OVER if S["hit100"] else UNDER),
             (L["extra"], f"${S['spend_total']:.2f}", L["extra_sub"].format(n=len(S["spend"])), T["accent_color"])]
    for i, (t, v, sub, col) in enumerate(cards):
        ax = fig.add_axes([0.025 + i * 0.2395, 0.735, 0.228, 0.125]); ax.set_axis_off()
        ax.add_patch(FancyBboxPatch((0.01, 0.02), 0.98, 0.94, boxstyle="round,pad=0,rounding_size=0.06", transform=ax.transAxes,
                                    facecolor=T["card"], edgecolor=T["border"], lw=1.1))
        ax.plot([0.04, 0.96], [0.96, 0.96], color=col, lw=2.2, transform=ax.transAxes)
        ax.text(0.07, 0.74, t, color=T["dim"], fontsize=10.5, fontweight="bold", transform=ax.transAxes, va="center")
        ax.text(0.07, 0.43, v, color=col, fontsize=19, fontweight="bold", transform=ax.transAxes, va="center")
        ax.text(0.07, 0.15, sub, color=T["dim"], fontsize=8.8, transform=ax.transAxes, va="center")

    def style(ax):
        ax.set_facecolor(T["card"])
        for s in ax.spines.values():
            s.set_color(T["border"])
        ax.tick_params(colors=T["text_color"], labelsize=9)
        ax.grid(axis="y", color=T["grid"], alpha=0.5, ls=":", lw=0.8)

    ax = fig.add_axes([0.075, 0.30, 0.90, 0.38]); style(ax)
    if L["main"]:
        ax.set_title(L["main"], color=T["text_color"], fontsize=12.5, fontweight="bold", loc="left", pad=8)
    for w in S["weeks"]:
        c = OVER if w["over"] else UNDER
        ax.bar(w["start"], w["peak"], width=ctx.period_days - 0.4, align="edge", color=c, alpha=0.30, edgecolor=c, lw=1.4, zorder=2)
        if S["n"] <= 30:
            ax.text(w["start"] + timedelta(days=(ctx.period_days - 0.4) / 2), w["peak"] + 2.5,
                    f"{up if w['over'] else dn}{w['peak']:.0f}%" + ("" if w["complete"] else "*"),
                    color=c, fontsize=9, fontweight="bold", ha="center", va="bottom", zorder=5, clip_on=True)
        ax.plot([w["start"], w["start"] + timedelta(days=ctx.period_days - 0.4)], [w["pace_ref"]] * 2, color=BASE, lw=1.8, ls="--", zorder=3)
        ax.plot([d[1] for d in w["days"]], [d[2] for d in w["days"]], color=T["text_color"], lw=1.8, marker="o", ms=3.5, zorder=4)
    ax.axhline(100, color=T["grid"], lw=0.9, ls=":", zorder=1)
    ax.set_xlim(S["start"], S["end"] + timedelta(days=1)); ax.set_ylim(0, 128); ax.set_yticks(range(0, 101, 20))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    span = (S["end"] - S["start"]).days
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=S["start"].weekday()) if span <= 40 else
                               mdates.MonthLocator(bymonthday=[1, 15]) if span <= 200 else mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    if not S["weeks"]:
        ax.text(0.5, 0.5, L["empty"], transform=ax.transAxes, color=NOTE, fontsize=16, ha="center", fontweight="bold")
    elif S["sparse"]:
        ax.text(0.01, 0.95, L["sparse_short"].format(n=S["n"], m=S["minw"]), transform=ax.transAxes, color=NOTE, fontsize=10, va="top", fontweight="bold")
    ax.legend(handles=[Patch(facecolor=OVER, alpha=0.6, edgecolor=OVER, label=(up + " " + L["leg_over"]) if L["leg_over"] != up else up),
                       Patch(facecolor=UNDER, alpha=0.6, edgecolor=UNDER, label=(dn + " " + L["leg_under"]) if L["leg_under"] != dn else dn),
                       Line2D([0], [0], color=BASE, ls="--", lw=1.8, label=L["leg_pace"].format(p=ctx.pace)),
                       Line2D([0], [0], color=T["text_color"], lw=1.8, marker="o", ms=3.5, label=L["leg_daily"])],
              loc="upper right", ncol=4, frameon=False, fontsize=8.5, labelcolor=T["text_color"], borderaxespad=0.3)
    ax2 = fig.add_axes([0.075, 0.075, 0.90, 0.16]); style(ax2)
    if L["spend_title"]:
        ax2.set_title(L["spend_title"], color=T["text_color"], fontsize=11, fontweight="bold", loc="left", pad=6)
    if S["spend"]:
        ks = list(S["spend"]); vs = [S["spend"][k] for k in ks]
        ax2.barh(range(len(ks)), vs, color=T["accent_color"], alpha=0.6, height=0.6)
        ax2.set_ylim(-0.6, max(len(ks), 3) - 0.4); ax2.set_yticks(range(len(ks))); ax2.set_yticklabels(ks)
        for i, v in enumerate(vs):
            ax2.text(v, i, f"  ${v:.2f}", color=T["text_color"], va="center", fontsize=9.5, fontweight="bold")
        ax2.set_xlim(0, max(vs) * 1.25 + 0.01)
    else:
        ax2.set_xticks([]); ax2.set_yticks([])
        ax2.text(0.5, 0.5, L["no_spend"], transform=ax2.transAxes, color=T["dim"], ha="center", va="center", fontsize=11)
    hdr.text(0.5, 0.018, L["footer"].format(t=f"{now:%Y-%m-%d %H:%M}", z=zl, p=ctx.pace), color=T["dim"], fontsize=9, ha="center", fontweight="bold")
    fig.savefig(path, facecolor=T["bg_color"]); plt.close(fig)


def hourly_rates(samples, since, max_gap_h):
    b = {h: [] for h in range(24)}; n = 0
    for (t1, p1), (t2, p2) in zip(samples, samples[1:]):
        if since is not None and t1 < since:
            continue
        gh = (t2 - t1).total_seconds() / 3600
        if gh <= 0 or gh > max_gap_h:
            continue
        b[t1.hour].append((p2 - p1 if p2 >= p1 else p2) / gh); n += 1  # drop = weekly reset -> count new value
    return b, n


def draw_clock(ctx, samples, cks, colors, path, title, since, now, zl):
    plt = _plt(ctx)
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.ticker import FuncFormatter
    T, L = ctx.theme, ctx.L
    cmap = LinearSegmentedColormap.from_list("hud", [T["under_color"], T["over_color"]])
    max_gap = max(2.0, 1.5 * ctx.interval / 60)
    b, n = hourly_rates(samples, since, max_gap)
    avg = [(sum(v) / len(v)) if v else 0.0 for _, v in sorted(b.items())]
    need = max(6, min(24, int(24 * 60 / ctx.interval)))
    fig = plt.figure(figsize=(7.2, 8.0), dpi=110, facecolor=T["bg_color"])
    if L["clock_eyebrow"]:
        fig.text(0.05, 0.965, L["clock_eyebrow"], color=T["accent_color"], fontsize=9.5, fontweight="bold", va="top")
    fig.text(0.05, 0.925, title, color=T["text_color"], fontsize=18, fontweight="bold", va="center")
    st = since or (samples[0][0] if samples else now)
    fig.text(0.05, 0.89, f"{st:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M}" + ("" if ctx.privacy else f" ({zl})") + f" · n={n}",
             color=T["dim"], fontsize=10, fontweight="bold", va="center")
    ax = fig.add_axes([0.14, 0.19, 0.72, 0.64], projection="polar", facecolor=T["card"])
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    w = 2 * math.pi / 24; th = [(h + 0.5) * w for h in range(24)]
    vmax = max(max(avg), 1.0); norm = Normalize(0, vmax)
    ax.bar(th, avg, width=w * 0.86, color=[cmap(norm(a)) for a in avg], edgecolor=T["border"], lw=0.6, alpha=0.95, zorder=3)
    ax.set_xticks([h * w for h in range(0, 24, 3)])
    ax.set_xticklabels([L["hour_fmt"].format(h=h) for h in range(0, 24, 3)], color=T["text_color"], fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}%")); ax.set_rlabel_position(7.5)
    ax.tick_params(axis="y", colors=T["dim"], labelsize=8); ax.grid(color=T["grid"], alpha=0.5, ls=":", lw=0.8)
    ax.spines["polar"].set_color(T["border"])
    ck = [r for r in cks if r["t"] <= now and (since is None or r["t"] >= since)]
    names = []
    for r in ck:
        if r["name"] not in names:
            names.append(r["name"])
    shown = names[:8]
    ax.set_ylim(0, vmax * (1.15 + 0.07 * len(shown)))
    for i, nm in enumerate(shown):
        tt = [((r["t"].hour + r["t"].minute / 60) / 24) * 2 * math.pi for r in ck if r["name"] == nm]
        ax.scatter(tt, [vmax * (1.12 + 0.07 * i)] * len(tt), s=22, color=colors[nm], edgecolors=T["card"], linewidths=0.5, zorder=6,
                   label=(f"#{names.index(nm) + 1}" if ctx.privacy else nm))
    if shown:
        leg = fig.legend(loc="lower left", bbox_to_anchor=(0.02, 0.13), frameon=False, fontsize=8.5, labelcolor=T["text_color"],
                         title=L["ck_legend"].format(n=len(ck)), handletextpad=0.3)
        leg.get_title().set_color(T["dim"]); leg.get_title().set_fontsize(8.5)
    for h, a in enumerate(avg):
        if a > 0 and a >= vmax * 0.35:
            ax.text(th[h], a + vmax * 0.06, f"{a:.1f}", color=T["text_color"], fontsize=8, ha="center", va="center", zorder=5)
    if n:
        top = max(range(24), key=lambda h: avg[h])
        summ = L["clock_peak"].format(h=top, h2=top + 1, v=avg[top], n=n)
    else:
        summ = L["clock_none"]
    fig.text(0.5, 0.105, summ, color=T["text_color"], fontsize=11, fontweight="bold", ha="center")
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cax = fig.add_axes([0.25, 0.062, 0.5, 0.012]); cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.outline.set_edgecolor(T["border"]); cb.ax.tick_params(colors=T["dim"], labelsize=8)
    if L["clock_note"]:
        fig.text(0.5, 0.018, L["clock_note"].format(z=zl), color=T["dim"], fontsize=7.5, ha="center")
    if n < need:
        fig.text(0.95, 0.925, L["collecting"].format(n=n, m=need), color=T["notice_color"], fontsize=11, fontweight="bold", ha="right", va="center",
                 bbox=dict(boxstyle="round,pad=0.35", facecolor=_blend(T["bg_color"], T["notice_color"], 0.12), edgecolor=T["notice_color"]))
    fig.savefig(path, facecolor=T["bg_color"]); plt.close(fig)


def _palette_dirs(ctx):
    """(palette, out_dir): the configured palette goes to history/ (embedded in index.html), every
    preset also goes to history/pal/<name>/ so the page's palette menu can swap images."""
    return [(name, ctx.hist_dir / "pal" / name) for name in usage_palette.names()]


def _publish_default(ctx, files):
    """Copy the configured palette's images to history/ (what index.html embeds)."""
    import shutil
    src = ctx.hist_dir / "pal" / ctx.palette
    for fn in files:
        if (src / fn).exists():
            tmp = ctx.hist_dir / (fn + ".tmp")
            shutil.copyfile(src / fn, tmp); os.replace(tmp, ctx.hist_dir / fn)


def _with_palette(ctx, name, fn):
    keep = ctx.theme
    ctx.theme = ctx.theme_for(name)
    try:
        return fn()
    finally:
        ctx.theme = keep


def render_clock(ctx):
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    now = ctx.now(); zl = ctx.zlabel(now)
    s = [x for x in load_samples(ctx) if x[0] <= now]
    cks, _, colors = load_checkins(ctx)
    for name, d in _palette_dirs(ctx):
        d.mkdir(parents=True, exist_ok=True)
        _with_palette(ctx, name, lambda: (
            draw_clock(ctx, s, cks, colors, d / "clock-7d.png", ctx.L["clock_7d"], now - timedelta(days=7), now, zl),
            draw_clock(ctx, s, cks, colors, d / "clock-all.png", ctx.L["clock_all"], None, now, zl)))
    _publish_default(ctx, ["clock-7d.png", "clock-all.png"])
    return {"ok": True, "samples": len(s), "files": [str(ctx.hist_dir / "clock-7d.png"), str(ctx.hist_dir / "clock-all.png")]}


def render_history(ctx):
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    nowa = ctx.now(); zl = ctx.zlabel(nowa); now = nowa.replace(tzinfo=None, second=0, microsecond=0)
    weeks, extra = load_weeks(ctx), load_extra(ctx)
    sums = [summarize(ctx, weeks, extra, k, m, mw, now) for k, m, mw in RANGES]
    for name, d in _palette_dirs(ctx):
        d.mkdir(parents=True, exist_ok=True)
        for S in sums:
            _with_palette(ctx, name, lambda: draw_range(ctx, S, d / f"history-{S['key']}.png", now, zl))
    _publish_default(ctx, [f"history-{S['key']}.png" for S in sums])
    write_html(ctx, sums, now, nowa, zl)
    return {"ok": True, "html": str(ctx.hist_dir / "index.html"),
            "ranges": {S["key"]: {"weeks": S["n"], "sparse": S["sparse"]} for S in sums}}


# Ledger status bar + browser notifications (Web Notifications API; permission is asked from a button,
# never on load). When served, polls /api/ledger every 3 minutes; each alert id (one per day) fires once
# (seen ids kept in localStorage).
LEDGER_JS = "(function(){var $=id=>document.getElementById(id),served=/^https?:$/.test(location.protocol),led=null,S=JSON.parse($('ledgerstr').textContent);\ntry{led=JSON.parse($('ledgerdata').textContent);}catch(e){}\nfunction f(t,o){return String(t).replace(/\\{(\\w+)\\}/g,function(_,k){return o[k]!=null?o[k]:'';});}\nfunction el(t,x,c){var e=document.createElement(t);if(x!=null)e.textContent=x;if(c)e.className=c;return e;}\nfunction parts(iso){if(!iso)return null;var d=new Date(iso);if(isNaN(d))return null;var m=/([+-])(\\d\\d):(\\d\\d)$/.exec(iso);\nvar off=m?(m[1]=='-'?-1:1)*(+m[2]*60+ +m[3]):0,w=new Date(d.getTime()+off*60000),p=n=>String(n).padStart(2,'0');\nreturn {wd:S.wd[(w.getUTCDay()+6)%7],t:p(w.getUTCMonth()+1)+'-'+p(w.getUTCDate())+' '+p(w.getUTCHours())+':'+p(w.getUTCMinutes()),\nhm:p(w.getUTCHours())+':'+p(w.getUTCMinutes()),z:m?'UTC'+m[1]+(+m[2])+(+m[3]?':'+m[3]:''):'UTC'};}\nfunction render(L){var box=$('ledger');box.textContent='';if(!L){box.appendChild(el('div',S.none,'info'));return;}\nvar a=parts(L.asOf),at=a?a.t+(S.showz?' ('+a.z+')':''):'?',r=parts(L.resetsAt);\nif(L.status!=='ok'){var w=el('div',null,'warn'),lg=L.lastGood,lp=lg&&parts(lg.asOf);w.appendChild(el('b',S.unavailable));\nw.appendChild(document.createTextNode(' · '+f(S.tried,{t:at})+(L.error&&!S.privacy?' · '+f(S.reason,{e:L.error}):'')+' · '+\n(lg&&lg.weeklyPercentUsed!=null?f(S.last_good,{p:lg.weeklyPercentUsed,t:lp?lp.t:'?'}):S.no_last)+' · '+S.reset_unknown));box.appendChild(w);}\nelse{var i=el('div',null,'info');i.appendChild(el('b',f(S.now,{p:L.weeklyPercentUsed})));\ni.appendChild(document.createTextNode(' · '+(r?f(S.reset,{wd:r.wd,hm:r.hm,z:r.z}):S.reset_unknown)+' · '+f(S.read_at,{t:at})));box.appendChild(i);}\nvar bs=el('div',null,'badges');\nif(L.spike&&L.spike.flag){var s=parts(L.spike.since);bs.appendChild(el('span',f(S.spike,{p:L.spike.points,t:s?s.t:'?'}),'badge'));}\nif(L.overBudget&&L.overBudget.flag)bs.appendChild(el('span',f(S.budget,{p:L.weeklyPercentUsed,th:L.overBudget.threshold}),'badge'));\nif(L.status!=='ok'&&L.consecutiveFailures)bs.appendChild(el('span',f(S.fails,{n:L.consecutiveFailures}),'badge note'));\nvar age=a?(Date.now()-new Date(L.asOf).getTime())/3600000:null;\nif(age!=null&&age>(L.staleAfterHours||6))bs.appendChild(el('span',f(S.stale,{h:Math.floor(age)}),'badge note'));\nif(bs.children.length)box.appendChild(bs);}\nrender(led);\nvar nb=$('notifybtn'),KEY='usage.alertsSeen';\nfunction seen(){try{return JSON.parse(localStorage.getItem(KEY)||'[]');}catch(e){return [];}}\nfunction fire(L){if(!L||!L.alerts||!('Notification' in window)||Notification.permission!=='granted')return;\nvar s=seen(),ch=false;L.alerts.forEach(function(x){if(s.indexOf(x.id)>=0)return;try{new Notification(x.title,{body:x.message,tag:x.id});}catch(e){}s.push(x.id);ch=true;});\nif(ch){try{localStorage.setItem(KEY,JSON.stringify(s.slice(-60)));}catch(e){}}}\nfunction poll(){fetch('/api/ledger',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(L=>{if(L){led=L;render(L);fire(L);}}).catch(()=>{});}\nif(served){if('Notification' in window&&Notification.permission==='default'){nb.hidden=false;\nnb.onclick=function(){Notification.requestPermission().then(function(p){nb.hidden=(p!=='default');if(p==='granted')fire(led);});};}\nfire(led);poll();setInterval(poll,180000);}})();\n"
MANUAL_STR = {  # "Enter manually" form (POST /api/manual); shown only when served by usage_history.py serve
    "en": {"btn": "Enter manually", "pct": "Weekly %", "reset": "Next reset (optional)",
           "reset_ph": "Sat 09:00 / Oct 31, 09:00 / 2026-10-31 09:00", "save": "Save",
           "hint": "Copy the numbers from Grok Build /usage or the Grok usage page.",
           "pct_note": "Type it in, or install Grok Build to read it automatically"},
    "zh-TW": {"btn": "手動輸入", "pct": "本週用量 %", "reset": "下次重置（可留空）",
              "reset_ph": "Sat 09:00 / Oct 31, 09:00 / 2026-10-31 09:00", "save": "儲存",
              "hint": "從 Grok Build 的 /usage 或 Grok 用量頁面抄下數字。",
              "pct_note": "手動填寫，或安裝 Grok Build 自動讀取"},
    "numeric": {"btn": "✎", "pct": "%", "reset": "↻", "reset_ph": "", "save": "✓", "hint": "", "pct_note": ""},
}

LEDGER_STR = {
    "en": {"wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "now": "Now {p}%", "reset": "resets {wd} {hm} ({z})",
           "reset_unknown": "reset unknown", "read_at": "read {t}", "unavailable": "⚠ Usage unavailable", "tried": "tried {t}",
           "reason": "reason: {e}", "last_good": "last good {p}% ({t})", "no_last": "no good reading yet",
           "spike": "▲ +{p} pts in 24h (since {t})", "budget": "● {p}% (≥ {th}%)", "fails": "{n} failed reads in a row",
           "stale": "data is {h}h old", "none": "No ledger yet (run a sample once)", "notify": "Enable desktop notifications", "showz": True},
    "zh-TW": {"wd": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"], "now": "目前 {p}%", "reset": "重置 {wd} {hm}（{z}）",
              "reset_unknown": "重置時間未知", "read_at": "讀取於 {t}", "unavailable": "⚠ 無法取得目前用量", "tried": "嘗試於 {t}",
              "reason": "原因：{e}", "last_good": "最後成功 {p}%（{t}）", "no_last": "尚無成功紀錄",
              "spike": "▲ 24 小時暴增 +{p} 點（自 {t}）", "budget": "● 已達 {p}%（門檻 {th}%）", "fails": "連續 {n} 次無法取得",
              "stale": "資料已 {h} 小時未更新", "none": "尚無 ledger（取樣一次後出現）", "notify": "開啟桌面通知", "showz": True},
    "numeric": {"wd": ["1", "2", "3", "4", "5", "6", "7"], "now": "{p}%", "reset": "↻ {hm}", "reset_unknown": "↻ ?", "read_at": "@ {t}",
                "unavailable": "∅", "tried": "@ {t}", "reason": "", "last_good": "✓ {p}% @ {t}", "no_last": "✓ —",
                "spike": "▲ +{p} / 24h", "budget": "● {p}% ≥ {th}%", "fails": "∅ ×{n}", "stale": "⌛ {h}h", "none": "∅",
                "notify": "🔔", "showz": False, "privacy": True},
}


# Palette switcher: sets the CSS colour variables and swaps chart images to history/pal/<name>/ (relative
# paths, so it also works when index.html is opened as a file). The choice is remembered per browser.
PALETTE_JS = """(function(){var P=JSON.parse(document.getElementById('paldata').textContent),s=document.getElementById('palsel'),KEY='usage.palette';
function apply(n){if(!P.table[n])n=P.default;var c=P.table[n][P.light?'light':'dark'],r=document.documentElement.style;
['over','under','baseline','notice'].forEach(function(k){if(!P.fixed[k])r.setProperty('--'+k,c[k]);});
document.querySelectorAll('img[data-f]').forEach(function(i){if(!i.dataset.orig)i.dataset.orig=i.src;
i.src=(n===P.default)?i.dataset.orig:('pal/'+n+'/'+i.dataset.f);});if(s)s.value=n;}
var n=P.default;try{n=localStorage.getItem(KEY)||P.default;}catch(e){}apply(n);
if(s)s.onchange=function(){try{localStorage.setItem(KEY,s.value);}catch(e){}apply(s.value);};})();"""


def _theme_explicit(path):
    """Which semantic colours a theme file pins (those do not change with the palette)."""
    out = {"over": False, "under": False, "baseline": False, "notice": False}
    if not path:
        return out
    p = Path(path) if Path(path).is_absolute() else (HERE / path if (HERE / path).exists() else Path(path))
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {k: bool(d.get(k + "_color")) for k in out}
    except Exception:
        return out


def write_html(ctx, sums, now, nowa, zl):
    L, T = ctx.L, ctx.theme
    MS = MANUAL_STR["numeric"] if ctx.privacy else (MANUAL_STR["zh-TW"] if ctx.lang.startswith("zh") else MANUAL_STR["en"])
    LS = LEDGER_STR["numeric"] if ctx.privacy else (LEDGER_STR["zh-TW"] if ctx.lang.startswith("zh") else LEDGER_STR["en"])
    try:
        ledger = json.loads(Path(ctx.ledger_path).read_text(encoding="utf-8"))
        ledger.pop("_newAlerts", None)
    except Exception:
        ledger = None
    ledjson = json.dumps(ledger, ensure_ascii=False).replace("<", "\\u003c")
    lsjson = json.dumps(LS, ensure_ascii=False).replace("<", "\\u003c")
    b64 = lambda p: base64.b64encode(p.read_bytes()).decode() if p.exists() else ""
    tabs, panes = [], []
    for i, S in enumerate(sums):
        rows = "".join(f"<tr><td>{w['start']:%Y-%m-%d}</td><td>{w['last']}/{ctx.period_days}</td><td class='{'over' if w['over'] else 'under'}'>"
                       f"{usage_palette.UP if w['over'] else usage_palette.DOWN} {w['peak']:.0f}%</td><td>{w['pace_ref']:.0f}%</td><td>{L['yes'] if w['hit100'] else '—'}</td></tr>" for w in reversed(S["weeks"])) \
            or f"<tr><td colspan=5>{html.escape(L['empty'])}</td></tr>"
        sp = "".join(f"<tr><td>{k}</td><td>${v:.2f}</td></tr>" for k, v in S["spend"].items()) or f"<tr><td colspan=2>{html.escape(L['no_spend'])}</td></tr>"
        warn = f"<div class='warn'>{html.escape(L['sparse'].format(n=S['n'], m=S['minw']))}</div>" if S["sparse"] else ""
        tabs.append(f"<button data-k='{S['key']}' class='{'on' if i == 0 else ''}'>{html.escape(S['label'])}</button>")
        panes.append(f"<section id='p-{S['key']}' class='{'on' if i == 0 else ''}'>{warn}<img alt='' data-f='history-{S['key']}.png' src='data:image/png;base64,{b64(ctx.hist_dir / ('history-' + S['key'] + '.png'))}'>"
                     f"<div class='tables'><table><caption>{html.escape(L['weekly_detail'])}</caption><tr><th>{L['week']}</th><th>{L['days']}</th><th>{L['peak']}</th>"
                     f"<th>{L['pace']}</th><th>{L['at100']}</th></tr>{rows}</table><table><caption>{html.escape(L['spend_title'])}</caption>"
                     f"<tr><th>{L['month']}</th><th>{L['amount']}</th></tr>{sp}</table></div></section>")
    imgs = "".join(f"<figure><img alt='' data-f='{fn}' src='data:image/png;base64,{b64(ctx.hist_dir / fn)}'></figure>" for fn in ("clock-7d.png", "clock-all.png") if (ctx.hist_dir / fn).exists())
    tabs.append(f"<button data-k='clock'>{html.escape(L['clock_tab'])}</button>")
    panes.append(f"<section id='p-clock'><div class='sub'>{html.escape(L['clock_sub'].format(z=zl))}</div>"
                 f"<div class='clocks'>{imgs or '<div class=warn>' + html.escape(L['no_clock']) + '</div>'}</div></section>")
    ckdata = usage_checkins_ui.to_script_json(checkin_page_data(ctx, sums))
    rjs = json.dumps({k: L[k] for k in ("loading", "refresh", "conn_err")}, ensure_ascii=False).replace("<", "\\u003c")
    tabs.append(f"<button data-k='checkin'>{html.escape(L['checkin_tab'])}</button>")
    panes.append(usage_checkins_ui.pane_html(ctx.ck_strings(), html.escape(L["ck_hint"])))
    title = "" if ctx.privacy else L["title"]
    bg, tx, ac, card, bd, dim = T["bg_color"], T["text_color"], T["accent_color"], T["card"], T["border"], T["dim"]
    # palette switcher: config default, overridden per browser (localStorage 'usage.palette')
    fixed = {k: bool(v) for k, v in _theme_explicit(ctx.theme_path).items()}
    paldata = json.dumps({"default": ctx.palette, "light": T["is_light"], "table": usage_palette.js_table(), "fixed": fixed},
                         ensure_ascii=False).replace("<", "\\u003c")
    plang = "numeric" if ctx.privacy else ("zh-TW" if ctx.lang == "zh-TW" else ("zh-CN" if ctx.lang.startswith("zh") else "en"))
    palopts = "".join(f"<option value='{n}'{' selected' if n == ctx.palette else ''}>"
                      f"{html.escape(str(i + 1) if plang == 'numeric' else usage_palette.LABELS[n].get(plang, usage_palette.LABELS[n]['en']))}</option>"
                      for i, n in enumerate(usage_palette.names()))
    pal_label = {"numeric": "◐", "zh-TW": "配色", "zh-CN": "配色"}.get(plang, "Colours")
    doc = f"""<!doctype html><html lang="{'zh-Hant' if ctx.lang == 'zh-TW' else 'en'}"><head><meta charset="utf-8"><title>{html.escape(title or '↻')}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{{margin:0;background:{bg};color:{tx};font-family:"Noto Sans CJK TC","Noto Sans TC","PingFang TC","Microsoft JhengHei","Segoe UI",sans-serif}}
.wrap{{max-width:1060px;margin:0 auto;padding:20px}} .top{{color:{ac};font-size:12px;font-weight:700;letter-spacing:1px}} h1{{margin:4px 0 8px;font-size:26px}}
.bar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}} .upd{{color:{dim};font-size:13px;display:flex;align-items:center;gap:10px}}
nav{{display:inline-flex;border:1px solid {bd};border-radius:10px;padding:4px;background:{card};margin:6px 0 14px}}
nav>button{{background:none;border:1px solid transparent;color:{dim};font:inherit;font-weight:700;font-size:15px;padding:6px 18px;border-radius:7px;cursor:pointer}}
nav>button.on{{color:{tx};border-color:{ac}}} section{{display:none}} section.on{{display:block}} img{{width:100%;border:1px solid {bd};border-radius:10px}}
:root{{--over:{T['over_color']};--under:{T['under_color']};--baseline:{T['baseline_color']};--notice:{T['notice_color']}}}
.warn{{border:1px solid var(--notice);color:var(--notice);border-radius:8px;padding:8px 12px;margin-bottom:10px;font-weight:700}}
.tables{{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-top:12px}} .clocks{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} figure{{margin:0}}
table{{border-collapse:collapse;width:100%;background:{card};border:1px solid {bd};font-size:13px}} caption{{text-align:left;font-weight:700;padding:6px 0}}
th,td{{padding:5px 10px;border-bottom:1px solid {bd};text-align:left}} th{{color:{dim}}} .over{{color:var(--over);font-weight:700}} .under{{color:var(--under);font-weight:700}}
.sub{{color:{dim};font-size:13px;margin:6px 0}} .ckform{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}}
input,select{{background:{card};color:{tx};border:1px solid {bd};border-radius:6px;padding:6px 10px;font:inherit}}
.btn,#ckbtn,#ckr button{{background:{card};border:1px solid {bd};color:{tx};font:inherit;font-weight:700;padding:5px 14px;border-radius:7px;cursor:pointer}}
#ckr button.on,.btn{{border-color:{ac}}} .btn:disabled{{opacity:.6;cursor:wait}} #ckmsg,#rmsg,#mmsg{{color:var(--notice);font-weight:700}} [hidden]{{display:none!important}}
.spin{{display:inline-block;width:12px;height:12px;border:2px solid {ac};border-top-color:transparent;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:6px}}
@keyframes sp{{to{{transform:rotate(360deg)}}}}
.info{{background:{card};border:1px solid {bd};border-left:3px solid {ac};border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:14px}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}} .badge{{border:1px solid var(--over);color:var(--over);border-radius:999px;padding:3px 12px;font-weight:700;font-size:13px}}
.badge.note{{border-color:var(--notice);color:var(--notice)}} .pal{{color:{dim};font-size:13px}}
{usage_checkins_ui.CSS}
</style></head><body><div class="wrap"><div class="top">{html.escape(L['eyebrow'])}</div><h1>{html.escape(title)}</h1>
<div class="bar"><nav>{''.join(tabs)}</nav><div class="upd"><span id="last">{html.escape(L['last'].format(t=f'{now:%H:%M}', z=zl))}</span>
<label class="pal">{html.escape(pal_label)} <select id="palsel">{palopts}</select></label>
<button class="btn" id="manualbtn" hidden>{html.escape(MS['btn'])}</button><button class="btn" id="notifybtn" hidden>{html.escape(LS['notify'])}</button><button class="btn" id="refresh" hidden>{html.escape(L['refresh'])}</button><span id="rmsg"></span></div></div>
<form id="manualform" class="ckform" hidden><label>{html.escape(MS['pct'])} <input id="mpct" type="number" min="0" max="200" step="0.1" required style="width:6em"> <span class="sub">{html.escape(MS.get('pct_note', ''))}</span></label>
<label>{html.escape(MS['reset'])} <input id="mreset" maxlength="64" placeholder="{html.escape(MS['reset_ph'])}" style="width:22em"></label>
<button class="btn" type="submit">{html.escape(MS['save'])}</button><span class="sub">{html.escape(MS['hint'])}</span><span id="mmsg"></span></form>
<div id="ledger"></div>{''.join(panes)}</div>
<script type="application/json" id="ckdata">{ckdata}</script><script>{usage_checkins_ui.JS}</script>
<script type="application/json" id="ledgerdata">{ledjson}</script><script type="application/json" id="ledgerstr">{lsjson}</script><script>{LEDGER_JS}</script>
<script type="application/json" id="paldata">{paldata}</script><script>{PALETTE_JS}</script>
<script>
var TK=(document.querySelector('meta[name="usage-token"]')||{{}}).content||'';
document.querySelectorAll('nav>button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav>button,section').forEach(e=>e.classList.remove('on'));
b.classList.add('on');document.getElementById('p-'+b.dataset.k).classList.add('on');if(history.replaceState)history.replaceState(null,'','#'+b.dataset.k);}});
(function(){{var $=id=>document.getElementById(id),served=/^https?:$/.test(location.protocol),T={rjs};
var rb=$('refresh'),rm=$('rmsg');
var mb=$('manualbtn'),mf=$('manualform'),mm=$('mmsg');
if(served)fetch('/api/status').then(r=>r.ok?r.json():null).then(j=>{{if(!j||!j.ok)return;mb.hidden=false;if(j.pct_source!=='manual')rb.hidden=false;
if(j.pct_source==='manual')mf.hidden=false;
if(j.last_update)$('last').textContent=j.last_update;}}).catch(()=>{{}});
mb.onclick=function(){{mf.hidden=!mf.hidden;if(!mf.hidden)$('mpct').focus();}};
mf.onsubmit=function(e){{e.preventDefault();mm.textContent=T.loading;
fetch('/api/manual',{{method:'POST',headers:{{'X-Usage-Token':TK,'Content-Type':'application/json'}},body:JSON.stringify({{pct:$('mpct').value,reset:$('mreset').value}})}})
.then(r=>r.json()).then(j=>{{if(j.ok){{location.reload();return;}}mm.textContent=j.error||'×';}}).catch(()=>{{mm.textContent=T.conn_err;}});}};
rb.onclick=function(){{rb.disabled=true;rb.innerHTML='<span class="spin"></span>'+T.loading;rm.textContent='';
fetch('/api/refresh',{{method:'POST',headers:{{'X-Usage-Token':TK}}}}).then(r=>r.json()).then(j=>{{if(j.ok){{location.reload();return;}}
rm.textContent=j.error+(j.retry_in?' ('+j.retry_in+'s)':'');rb.disabled=false;rb.textContent=T.refresh;}}).catch(()=>{{rm.textContent=T.conn_err;rb.disabled=false;rb.textContent=T.refresh;}});}};
}})();
var h=location.hash.slice(1),t=document.querySelector("nav>button[data-k='"+h+"']");if(t)t.click();
</script></body></html>"""
    tmp = ctx.hist_dir / "index.html.tmp"; tmp.write_text(doc, encoding="utf-8"); os.replace(tmp, ctx.hist_dir / "index.html")


# ------------------------------------------------------------------ sampling
def manual_mode(ctx):
    """pct_source "manual" in the config: never start Grok Build; values come from --pct / the page."""
    return str(ctx.cfg.get("pct_source") or "auto").strip().lower() == "manual"


def save_manual_reset(ctx, reset_at):
    """Cache a typed-in reset like a Grok-reported one (<data_dir>/state.json), so week boundaries work."""
    payload = {"reset_at": reset_at.isoformat(timespec="minutes"), "period_days": ctx.period_days,
               "updated_at": datetime.now(ctx.tz).isoformat(timespec="minutes"), "source": "m" if ctx.privacy else "manual"}
    p = ctx.data_dir / "state.json"
    tmp = p.with_name(f"state.json.{os.getpid()}.tmp")
    with usage_lock.write_lock(ctx.data_dir):
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)


def take_sample(ctx, pct=None, source="sampler", fail_reason=None, reset_at=None):
    """One reading -> samples.csv + shared ledger (usage_ledger.py).
    `pct` given = a manual reading (no Grok Build launch); `reset_at` (aware datetime) optionally
    goes with it and is cached to state.json.
    Shares <data_dir>/.read.lock with the sampler loop and manual refresh.
    A failed read is recorded as 'unavailable' (never 0%, never a guessed reset) and raises RuntimeError."""
    lk = FileLock(ctx.data_dir / ".read.lock")
    if not lk.acquire(timeout=600):
        raise RuntimeError("read lock timeout")
    err = None
    manual_reset = reset_at if pct is not None else None
    try:
        reset_at = None
        try:
            if fail_reason is not None:
                raise RuntimeError(fail_reason or "simulated failure")
            if pct is None:
                import grok_usage_reader
                res = grok_usage_reader.read_usage(timeout=60.0)
                if res.get("pct") is None:
                    raise RuntimeError("no percentage shown")
                pct, reset_at = float(res["pct"]), res.get("reset_at")
            else:
                source = "manual" if source == "sampler" else source
                reset_at = manual_reset
                if manual_reset is not None:
                    save_manual_reset(ctx, manual_reset)
        except Exception as exc:
            pct, reset_at, err = None, None, str(exc) or type(exc).__name__
        ts = datetime.now(ctx.tz)
        p = ctx.data_dir / "samples.csv"; ctx.data_dir.mkdir(parents=True, exist_ok=True)
        led = None
        with usage_lock.write_lock(ctx.data_dir):  # samples.csv + ledger as one write
            new = not p.exists() or p.stat().st_size == 0
            with p.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["timestamp", "pct"])
                w.writerow([ts.isoformat(timespec="seconds"), f"{float(pct):g}" if pct is not None else "unavailable"])
            try:
                import usage_ledger
                led = usage_ledger.record_hud(ctx.data_dir, ctx.cfg, pct, reset_at, source=source, error=err, tz=ctx.tz,
                                              lang=ctx.lang, exclude_last=True, now=ts)
            except Exception as exc:
                print(f"Warning: ledger write failed: {exc}", file=sys.stderr)
    finally:
        lk.release()
    for fn in (render_clock, render_history):  # failure-safe re-render
        try:
            fn(ctx)
        except Exception as exc:
            print(f"Warning: {fn.__name__} failed: {exc}", file=sys.stderr)
    if pct is None:
        raise RuntimeError("source read failed" if ctx.privacy else f"unavailable: {err}")
    return {"ok": True, "timestamp": ts.isoformat(timespec="seconds"), "pct": float(pct),
            "reset_at": reset_at.isoformat(timespec="minutes") if reset_at else None,
            "new_alerts": (led or {}).get("_newAlerts", [])}


def next_boundary(after_ts, iv_min, tz):
    """Next run time strictly after `after_ts` (epoch). Aligns to clock boundaries in `tz` when the
    interval divides 1440 (so 30 -> :00/:30, 60 -> top of hour); otherwise `after + interval`."""
    s = iv_min * 60
    if 1440 % iv_min:
        return after_ts + s
    d = datetime.fromtimestamp(after_ts, tz)
    mid = d.replace(hour=0, minute=0, second=0, microsecond=0)
    k = int((d - mid).total_seconds() // s) + 1
    return (mid + timedelta(seconds=k * s)).timestamp()


def run_sampler(ctx, skip_first=False):
    lk = FileLock(ctx.data_dir / ".sampler.lock")
    if not lk.acquire(blocking=False):
        print(json.dumps({"ok": False, "error": "sampler already running"})); sys.exit(1)
    pidf = ctx.data_dir / "sampler.pid"
    with usage_lock.write_lock(ctx.data_dir):
        pidf.write_text(str(os.getpid()))
    log = ctx.data_dir / "sampler.log"

    def logw(msg):
        with log.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {msg}\n")

    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        logw(f"sampler start pid={os.getpid()} interval={ctx.interval}m")
        last = time.time()

        def once():
            if manual_mode(ctx):  # nothing to read; values are entered by hand
                logw("pct_source is manual: no automatic read (enter values on the page or with 'sample --pct N')")
                return
            try:
                logw(json.dumps(take_sample(ctx), ensure_ascii=False))
            except Exception as exc:
                logw(f"sample failed: {exc}")
        if not skip_first:
            once()
        while True:
            old = ctx.interval; ctx.reload()  # re-read config every minute
            if ctx.interval != old:
                logw(f"interval changed {old}m -> {ctx.interval}m")
            target = next_boundary(last, ctx.interval, ctx.tz); now = time.time()
            if now >= target:
                last = target if now - target < 300 else now
                once(); continue
            time.sleep(max(1, min(target - now, 60 - now % 60)))
    finally:
        try:
            pidf.unlink()
        except OSError:
            pass
        lk.release()


# ------------------------------------------------------------------ server
def _date_param(ctx, v, end=False):
    try:
        d = datetime.strptime(str(v).strip(), "%Y-%m-%d").replace(tzinfo=ctx.tz)
    except (TypeError, ValueError):
        return None
    return d + timedelta(days=1) - timedelta(seconds=1) if end else d


def checkin_query(ctx, q):
    """/api/checkins and /api/checkins/search (q = parse_qs dict): sessions matching the keyword
    words (AND), categories (any) and date range, grouped by day, newest first.
    Privacy mode: names are #1, #2 ... and notes are not shown or searched."""
    g = lambda k: (q.get(k) or [""])[0]  # noqa: E731
    _, names, _ = load_checkins(ctx)
    alias = public_alias(ctx, names)
    sess = load_sessions(ctx)
    if ctx.privacy:
        sess = [dict(x, name=alias.get(x["name"], x["name"]), note="", end_note="") for x in sess]
    cats = usage_checkins.split_list(q.get("category", []) + q.get("categories", []))
    nm = g("name").strip()
    hits = usage_checkins.search(sess, g("q"), ctx.cat_label, _date_param(ctx, g("from")), _date_param(ctx, g("to"), True),
                                 [ctx.categories.normalize(c) for c in cats])
    if nm:
        hits = [x for x in hits if x["name"] == nm]
    days = usage_checkins.group_by_day(hits, usage_checkins.load_samples(str(ctx.data_dir / "samples.csv")), ctx.tz)
    summ = usage_checkins.summarize(hits)
    return {"ok": True, "timezone": ctx.zlabel(), "count": len(hits), "query": g("q"),
            "total_min": round(summ["total_s"] / 60.0, 1),
            "by_category": {c: dict(v, total_min=round(v.pop("total_s") / 60.0, 1)) for c, v in summ["by_category"].items()},
            "categories": [{"id": c, "label": ctx.cat_label(c)} for c in ctx.categories.ids],
            "days": [usage_checkins.day_to_json(d, ctx.cat_label) for d in days],
            "sessions": [usage_checkins.to_json(x, ctx.cat_label) for x in sorted(hits, key=lambda x: x["start"], reverse=True)]}


def print_search(ctx, res):
    """Plain-text search result: one block per day, newest first."""
    L = ctx.ck_strings()
    print(f"{res['count']} match(es) · {ctx.zlabel()}")
    for d in res["days"]:
        u = "unavailable" if d["usage_rise"] is None else f"+{d['usage_rise']:g} pt"
        print(f"\n{d['date']}  {d['count']} session(s) · {usage_checkins.fmt_duration(d['total_min'] * 60)} · usage {u}")
        for x in d["sessions"]:
            t1 = x["start"][11:16]
            t2 = ("–" + x["end"][11:16]) if x["end"] else ""
            ud = x["usage"]
            us = "—" if not ud else (f"{ud['delta']:+g} pt" if ud["status"] == "ok" else "unavailable")
            dur = usage_checkins.fmt_duration(x["duration_min"] * 60) if x["duration_min"] is not None else "—"
            note = " / ".join(v for v in (x["note"], x["end_note"]) if v)
            print(f"  {t1}{t2}  {x['name']}  [{', '.join(x['category_labels'])}]  {dur}  {L.get(x['state'], x['state'])}"
                  f"  {us}" + (f"  — {note}" if note else ""))


def serve(ctx, allow_remote=False):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    if ctx.host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        print(json.dumps({"ok": False, "error": "non-loopback host refused; pass --allow-remote"})); sys.exit(2)
    extra_hosts = [str(h) for h in (ctx.cfg.get("history_allowed_hosts") or [])]
    if ctx.host not in ("127.0.0.1", "localhost", "::1"):
        extra_hosts.append(ctx.host)
        print("WARNING: serving on a non-loopback address; anyone on the network who learns the token can check in "
              "and trigger refreshes. Add the name you browse with to history_allowed_hosts.", file=sys.stderr)
    busy = threading.Lock(); state = {"last_refresh": 0.0, "ck": {}}
    token = local_guard.new_token()
    token_file = ctx.data_dir / "history_server.token"

    def regen():
        for fn in (render_clock, render_history):
            try:
                fn(ctx)
            except Exception as exc:
                print(f"Warning: {exc}", file=sys.stderr)
    if not (ctx.hist_dir / "index.html").exists():
        regen()

    def last_update():
        p = ctx.hist_dir / "index.html"
        if not p.exists():
            return None
        t = datetime.fromtimestamp(p.stat().st_mtime, ctx.tz)
        return ctx.L["last"].format(t=f"{t:%H:%M}", z=ctx.zlabel(t))

    def checkin(d):
        name = _clean(d.get("name"), 40)
        phase = str(d.get("phase") or "point").strip().lower()
        cats = d.get("categories", d.get("category"))
        key = (name or str(d.get("id") or ""), phase)
        if time.time() - state["ck"].get(key, 0) < ctx.checkin_cooldown:
            return 429, {"ok": False, "error": "cooldown", "retry_in": ctx.checkin_cooldown}
        try:
            r = add_checkin(ctx, name, d.get("note") or d.get("reason") or "", cats, phase,
                            session_id=d.get("id") or None, status=d.get("status") or "done")
        except usage_checkins.CheckinError as exc:
            return 400, {"ok": False, "error": str(exc), "code": exc.code}
        state["ck"][key] = time.time()
        regen()
        if ctx.privacy:
            r = dict(r, name="", note="")
        return 200, {"ok": True, "checkin": r}

    WRITE_PATHS = ("/api/checkin", "/api/refresh", "/api/manual")

    def manual(d):
        """POST /api/manual {"pct": 42, "reset": "Sat 09:00"} -> one manual sample."""
        import manual_entry
        try:
            pct = manual_entry.parse_pct(d.get("pct"))
            reset_at = manual_entry.parse_reset_input(d.get("reset"), datetime.now(ctx.tz), ctx.tz, ctx.period_days)
        except manual_entry.ManualError as exc:
            return 400, {"ok": False, "error": str(exc), "code": "bad_manual_value"}
        if not busy.acquire(blocking=False):
            return 409, {"ok": False, "error": "busy"}
        try:
            return 200, take_sample(ctx, pct, source="manual", reset_at=reset_at)
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"manual entry failed: {exc}"}
        finally:
            busy.release()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct="application/json; charset=utf-8", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(body)

        def _host_ok(self):
            if local_guard.host_allowed(self.headers.get("Host"), self.server.server_address[1], extra_hosts):
                return True
            self._send(421, {"ok": False, "error": "unexpected Host header"})
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            u = urlparse(self.path); q = parse_qs(u.query); g = lambda k: (q.get(k) or [""])[0]
            if u.path in WRITE_PATHS:
                return self._send(405, {"ok": False, "error": "use POST with the X-Usage-Token header"}, extra={"Allow": "POST"})
            if u.path in ("/", "/index.html"):
                p = ctx.hist_dir / "index.html"
                if not p.exists():
                    return self._send(404, {"ok": False, "error": "not rendered"})
                page = local_guard.inject_token(p.read_text(encoding="utf-8"), token)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            parts = u.path[1:].split("/")
            if u.path.endswith(".png") and (len(parts) == 1 or (len(parts) == 3 and parts[0] == "pal" and parts[1] in usage_palette.names())) \
                    and all(x and x not in (".", "..") for x in parts):
                p = ctx.hist_dir.joinpath(*parts)
                return self._send(200, p.read_bytes(), "image/png") if p.is_file() else self._send(404, {"ok": False, "error": "not found"})
            if u.path in ("/api/ledger", "/ledger.json"):
                lp = Path(ctx.ledger_path)
                return self._send(200, lp.read_bytes()) if lp.exists() else self._send(404, {"ok": False, "error": "no ledger yet; run: usage_history.py sample"})
            if u.path == "/api/status":
                return self._send(200, {"ok": True, "last_update": last_update(), "busy": busy.locked(), "timezone": ctx.zlabel(),
                                        "sample_interval_minutes": ctx.interval, "pct_source": "manual" if manual_mode(ctx) else "auto"})
            if u.path in ("/api/checkins", "/api/checkins/search"):  # read-only
                return self._send(200, checkin_query(ctx, q))
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return
            u = urlparse(self.path)
            if u.path in WRITE_PATHS:
                ok, code, why = local_guard.check_write(self.headers, self.server.server_address[1], token, extra_hosts)
                if not ok:
                    return self._send(code, {"ok": False, "error": why})
            if u.path == "/api/checkin":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 10000)).decode("utf-8") if n else ""
                    d = json.loads(raw) if raw.strip().startswith("{") else \
                        {k: (v if k in ("category", "categories") else v[0]) for k, v in parse_qs(raw).items()}
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*checkin(d))
            if u.path == "/api/manual":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 4000)).decode("utf-8") if n else ""
                    d = json.loads(raw) if raw.strip().startswith("{") else {k: v[0] for k, v in parse_qs(raw).items()}
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*manual(d))
            if u.path == "/api/refresh":
                if manual_mode(ctx):
                    return self._send(400, {"ok": False, "error": "pct_source is manual: use Enter manually", "code": "manual_mode"})
                if not busy.acquire(blocking=False):
                    return self._send(409, {"ok": False, "error": "busy"})
                try:
                    wait = ctx.refresh_cooldown - (time.time() - state["last_refresh"])
                    if wait > 0:
                        return self._send(429, {"ok": False, "error": "cooldown", "retry_in": int(wait) + 1})
                    state["last_refresh"] = time.time()
                    try:
                        return self._send(200, take_sample(ctx, source="refresh"))
                    except Exception as exc:
                        return self._send(500, {"ok": False, "error": "source read failed" if ctx.privacy else f"refresh failed: {exc}"})
                finally:
                    busy.release()
            self._send(404, {"ok": False, "error": "not found"})

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer((ctx.host, ctx.port), H)
    url = f"http://{ctx.host}:{srv.server_address[1]}/"
    info = ctx.data_dir / "history_server.json"
    with usage_lock.write_lock(ctx.data_dir):
        local_guard.write_token_file(token_file, token)
        info.write_text(json.dumps({"pid": os.getpid(), "url": url, "token_file": str(token_file)}))
    print(json.dumps({"ok": True, "url": url, "token_file": str(token_file)}), flush=True)
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(SystemExit(0)))
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        local_guard.remove_token_file(token_file, token)
        try:
            info.unlink()
        except OSError:
            pass


def status(ctx):
    alive = proc_util.pid_alive  # os.kill(pid, 0) would send Ctrl+C on Windows
    out = {"ok": True, "data_dir": str(ctx.data_dir), "sample_interval_minutes": ctx.interval, "timezone": ctx.zlabel()}
    pf = ctx.data_dir / "sampler.pid"
    out["sampler_pid"] = int(pf.read_text()) if pf.exists() and alive(pf.read_text()) else None
    out["pct_source"] = "manual" if manual_mode(ctx) else "auto"
    sf = ctx.data_dir / "history_server.json"
    try:
        s = json.loads(sf.read_text()); out["server"] = s if alive(s["pid"]) else None
    except Exception:
        out["server"] = None
    return out


# ------------------------------------------------------------------ CLI
def main(argv=None):
    p = argparse.ArgumentParser(description="Grok Bot Usage & Check-in (usage-hud): long-range history, sampler, clock chart, check-ins and viewer")
    p.add_argument("cmd", choices=["history", "clock", "sample", "sampler", "checkin", "start", "end", "search", "serve",
                                   "status", "ledger"])
    p.add_argument("rest", nargs="*", help="checkin/start/end: NAME [NOTE...]; search: WORDS...")
    p.add_argument("--config"); p.add_argument("--data-dir", dest="data_dir"); p.add_argument("--tz")
    p.add_argument("--lang"); p.add_argument("--theme"); p.add_argument("--pace", type=float)
    p.add_argument("--palette", choices=usage_palette.names(), help="chart/page colours (default: config 'palette' or orange-teal)")
    p.add_argument("--privacy", action="store_true", default=None)
    p.add_argument("--pct", help="sample: use this weekly %% (0-200) instead of reading Grok Build (manual entry)")
    p.add_argument("--reset", help="sample --pct: next reset, e.g. 'Sat 09:00', 'Oct 31, 09:00' or '2026-10-31 09:00'")
    p.add_argument("--simulate-fail", metavar="REASON", help="sample: record a failed read (testing)")
    p.add_argument("--max-age-hours", type=float, help="ledger: freshness limit (default stale_after_hours or 6)")
    p.add_argument("--host"); p.add_argument("--port", type=int); p.add_argument("--allow-remote", action="store_true")
    p.add_argument("--category", action="append", help="checkin/start: category id(s), comma list or repeated "
                   "(1..checkin_max_categories; unknown -> other); search: any of these")
    p.add_argument("--status", choices=list(usage_checkins.STATUSES), default="done", help="end: done or interrupted")
    p.add_argument("--id", dest="session_id", help="end: session id (default: latest open session for NAME)")
    p.add_argument("--from", dest="date_from", help="search: first day YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", help="search: last day YYYY-MM-DD")
    p.add_argument("--json", action="store_true", help="search: JSON output")
    p.add_argument("--skip-first", action="store_true", help="sampler: wait for the next boundary before the first sample")
    a = p.parse_args(argv)
    ctx = Ctx(a)
    if a.cmd == "history":
        print(json.dumps(render_history(ctx), ensure_ascii=False))
    elif a.cmd == "clock":
        print(json.dumps(render_clock(ctx), ensure_ascii=False))
    elif a.cmd == "sample":
        pct = reset_at = None
        if a.pct is not None or a.reset:
            import manual_entry
            try:
                if a.pct is None:
                    raise manual_entry.ManualError("--reset needs --pct")
                pct = manual_entry.parse_pct(a.pct)
                reset_at = manual_entry.parse_reset_input(a.reset, datetime.now(ctx.tz), ctx.tz, ctx.period_days)
            except manual_entry.ManualError as exc:
                print(json.dumps({"ok": False, "error": str(exc)})); sys.exit(2)
        elif manual_mode(ctx) and not a.simulate_fail:
            print(json.dumps({"ok": False, "error": "pct_source is manual: pass --pct N [--reset WHEN]"})); sys.exit(2)
        try:
            print(json.dumps(take_sample(ctx, pct, fail_reason=a.simulate_fail, reset_at=reset_at), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": "source read failed" if ctx.privacy else str(exc)})); sys.exit(5)
    elif a.cmd == "sampler":
        run_sampler(ctx, a.skip_first)
    elif a.cmd in ("checkin", "start", "end"):
        phase = {"checkin": "point"}.get(a.cmd, a.cmd)
        if not a.rest and not (phase == "end" and a.session_id):
            print(json.dumps({"ok": False, "error": f"usage: {a.cmd} NAME [NOTE] [--category a,b]"})); sys.exit(2)
        try:
            r = add_checkin(ctx, a.rest[0] if a.rest else "", " ".join(a.rest[1:]), a.category, phase,
                            session_id=a.session_id, status=a.status)
        except usage_checkins.CheckinError as exc:
            print(json.dumps({"ok": False, "error": str(exc), "code": exc.code})); sys.exit(2)
        for fn in (render_clock, render_history):
            try:
                fn(ctx)
            except Exception as exc:
                print(f"Warning: {exc}", file=sys.stderr)
        print(json.dumps({"ok": True, "checkin": r}, ensure_ascii=False))
    elif a.cmd == "search":
        q = {"q": [" ".join(a.rest)], "category": a.category or [], "from": [a.date_from or ""], "to": [a.date_to or ""]}
        res = checkin_query(ctx, q)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print_search(ctx, res)
    elif a.cmd == "serve":
        serve(ctx, a.allow_remote)
    elif a.cmd == "status":
        print(json.dumps(status(ctx), ensure_ascii=False))
    elif a.cmd == "ledger":  # for assistants: exit 0 = fresh, 3 = stale or missing
        import usage_ledger
        r = usage_ledger.read(ctx.ledger_path, a.max_age_hours)
        r["path"] = ctx.ledger_path
        print(json.dumps(r, ensure_ascii=False)); sys.exit(0 if r["fresh"] else 3)


if __name__ == "__main__":
    main()
