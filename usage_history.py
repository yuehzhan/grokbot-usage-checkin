#!/usr/bin/env python3
"""usage_history.py - long-range history, interval sampler, 24h clock chart, check-ins, and a
localhost viewer for usage-hud. Standard library + matplotlib only. Existing usage_record.py /
usage_view.py / chart_hud.py behavior is unchanged; this tool only *reads* their week-*.csv files
and state.json, and writes its own files under <data_dir>.

Subcommands (all accept --config / --data-dir / --tz / --lang / --privacy / --theme):
  history                 Render <data_dir>/history/history-{1m,3m,6m,1y}.png + index.html
  clock                   Render <data_dir>/history/clock-1d.png (last 24 h) and clock-wk-ot.png (this reset week,
                          one colour per day, overlay + translucent); other ranges (1 month ... all, grouped by
                          week / month / quarter / year) and layouts (os = overlay solid, sd = side by side) are
                          drawn when the page asks for them
  sample [--pct N]        Take one reading -> <data_dir>/samples.csv, then re-render (failure-safe)
  sampler                 Run `sample` every sample_interval_minutes (single instance, aligned)
  checkin NAME [NOTE]     Append a check-in to <data_dir>/checkins.csv (--category ID, default other)
  serve                   Localhost viewer: history page, manual refresh, check-in API, check for updates
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
import usage_dates  # noqa: E402  (explicit next-reset date, e.g. Sat, Oct 31, 01:41)
import usage_offset  # noqa: E402  (display offset -2..+2 for the shown weekly %; stored values stay raw)
import usage_categories  # noqa: E402  (check-in categories; unknown -> other)
import usage_checkins  # noqa: E402  (check-in points and start/end sessions)
import usage_checkins_ui  # noqa: E402  (check-in tab: HTML/CSS/JS)
import proc_util  # noqa: E402  (Windows-safe "is this pid alive?")

INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 15, 1440, 60
RANGES = [("1m", 1, 3), ("3m", 3, 10), ("6m", 6, 20), ("1y", 12, 40)]  # key, months, min weeks
CHECKIN_PALETTE = ["#4D8DFF", "#FFE45C", "#FF8AD8", "#8C9EFF", "#B8F55A", "#C9A0FF", "#F5F5F5", "#7CB8FF"]

STR = {
    "en": {
        "eyebrow": "Usage · Check-in · Search", "title": "Grok Bot Usage & Check-in", "png_title": "Usage History", "rwk": "This Week", "week_empty": "No reading this week yet: enter the weekly % above.", "r1m": "1 Month", "r3m": "Quarter",
        "r6m": "Half Year", "r1y": "1 Year", "clock_tab": "Clock", "checkin_tab": "Check-ins",
        "weeks": "Weeks", "avg_peak": "Avg weekly peak", "hit100": "Weeks at 100%", "extra": "Extra spend",
        "over_n": "{n} over pace", "pace_sub": "pace {p:g}%/day -> {w:g}%/week", "cap_sub": "hit the weekly cap",
        "extra_sub": "{n} month(s) with spend", "main": "Weekly peak vs pace, with daily cumulative usage",
        "leg_over": "Peak over pace", "leg_under": "Peak within pace", "leg_pace": "Pace ({p:g}%/day)",
        "leg_daily": "Daily cumulative", "sparse": "Not enough data: {n} week(s) (want >= {m})",
        "sparse_short": "Not enough data ({n} week(s)); trend is indicative only", "empty": "No data in this range",
        "spend_title": "Extra spend per month (cumulative max within month)", "no_spend": "No extra spend recorded",
        "footer": "generated {t} ({z})  ·  pace {p:g}%/day  ·  * = week not complete",
        "clock_eyebrow": "USAGE CLOCK // PER HOUR", "clock_1d": "Usage Clock — last 24 hours", "clock_r": "Usage Clock — {r}",
        "clock_rt": {"wk": "this week", "1m": "last month", "3m": "last quarter", "6m": "last 6 months", "1y": "last year", "all": "all"},
        "clock_ro": {"wk": "This week", "1m": "Last month", "3m": "Last quarter", "6m": "Last 6 months", "1y": "Last year", "all": "All"}, "clock_rl": "Range",
        "clock_lo": {"ov": "Overlay", "sd": "Side by side"}, "clock_ll": "Layout", "clock_fo": {"t": "Translucent", "s": "Solid"}, "clock_fl": "Fill",
        "clock_wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "clock_day": "{wd} {md}", "clock_wkl": "Wk {i} {a}–{b}",
        "clock_mon": "{mon} {y}", "clock_q": "Q{q} {y}", "clock_today": "{l} (today)", "clock_curp": "{l} (now)",
        "clock_note_g": "Times in {z} · colour = day (or week / month / quarter), see the legend · length = % increase in that hour · long gaps skipped",
        "clock_peak": "Peak {h}:00–{h2}:00  avg +{v:.1f}%/h  ({n} intervals)", "clock_none": "No valid intervals yet",
        "collecting": "Collecting data ({n}/{m})", "clock_note": "Times in {z} · length/color = avg % increase in that hour · resets counted from new value · long gaps skipped",
        "ck_legend": "Check-ins ({n})", "last": "Last update: {t} ({z})", "refresh": "⟳ Refresh", "loading": "Loading…", "refreshing": "⟳ Refreshing… {s} s",
        "ph_read": "Reading usage from Grok Build…", "ph_draw": "Drawing charts…",
        "range": "Range", "name": "Name", "all": "All", "note": "Note", "time": "Time", "recent": "Recent check-ins",
        "per_name": "Per name", "top_hour": "Most common hour", "none_ck": "No check-ins yet", "ck_btn": "Check in",
        "ck_hint": "Open this page through the local server to check in here, or run: usage_history.py checkin <name> [note] --category <id>",
        "category": "Category", "per_cat": "Per category", "count": "Check-ins", "share": "Share", "last_ck": "Last",
        "week": "Week start", "days": "Days", "peak": "Peak", "pace": "Pace", "at100": "100%", "yes": "yes",
        "month": "Month", "amount": "Amount", "weekly_detail": "Weekly detail", "conn_err": "Cannot reach local server",
        "clock_sub": "% increase per hour ({z}); 0:00 at top, clockwise. Left: last 24 hours. Right: colour = day (or week / month / quarter), see the legend under the dial; length = % increase in that hour", "no_clock": "Collecting data — clock charts appear after sampling starts",
        "hour_fmt": "{h}",
    },
    "zh-TW": {
        "eyebrow": "用量・打卡・搜尋", "title": "Grok Bot Usage & Check-in", "png_title": "用量歷史", "rwk": "本週", "week_empty": "本週還沒有紀錄：在上方輸入本週用量 % 後就會出現。", "r1m": "一個月", "r3m": "一季", "r6m": "半年", "r1y": "一年",
        "clock_tab": "時鐘", "checkin_tab": "打卡", "weeks": "週數", "avg_peak": "平均週峰值", "hit100": "達 100% 週數",
        "extra": "額外付費", "over_n": "超出基準 {n} 週", "pace_sub": "基準 {p:g}%/天 → 每週 {w:g}%", "cap_sub": "該週用滿上限",
        "extra_sub": "{n} 個月有付費紀錄", "main": "每週峰值用量 vs 基準，與每日累計用量", "leg_over": "週峰值 超出基準",
        "leg_under": "週峰值 未超出", "leg_pace": "基準（{p:g}%/天）", "leg_daily": "每日累計",
        "sparse": "資料不足：{n} 週（建議 ≥ {m} 週）", "sparse_short": "資料不足（目前 {n} 週），趨勢僅供參考",
        "empty": "資料不足 — 此範圍尚無紀錄", "spend_title": "每月額外付費（取當月最高累計）", "no_spend": "此範圍無額外付費紀錄",
        "footer": "產生於 {t}（{z}）· 基準 {p:g}%/天 · * = 該週未滿",
        "clock_eyebrow": "USAGE CLOCK // 每小時用量", "clock_1d": "用量時鐘 — 近 24 小時", "clock_r": "用量時鐘 — {r}",
        "clock_rt": {"wk": "本週", "1m": "近一月", "3m": "近一季", "6m": "近半年", "1y": "近一年", "all": "全部"},
        "clock_ro": {"wk": "本週", "1m": "近一月", "3m": "近一季", "6m": "近半年", "1y": "近一年", "all": "全部"}, "clock_rl": "範圍",
        "clock_lo": {"ov": "疊加", "sd": "並排"}, "clock_ll": "排列", "clock_fo": {"t": "半透明", "s": "實心"}, "clock_fl": "填色",
        "clock_wd": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"], "clock_day": "{md} {wd}", "clock_wkl": "第{i}週 {a}–{b}",
        "clock_mon": "{y}年{m}月", "clock_q": "{y} Q{q}", "clock_today": "{l}（今天）", "clock_curp": "{l}（目前）",
        "clock_note_g": "時刻為 {z} · 顏色 = 不同天（或週／月／季），對照圖例 · 長度 = 該小時用量增加 · 間隔過長略過",
        "clock_peak": "尖峰 {h}:00–{h2}:00　平均 +{v:.1f}%/小時　（{n} 個區間）", "clock_none": "尚無有效區間",
        "collecting": "資料收集中（{n}/{m}）", "clock_note": "時刻為 {z} · 長度/顏色 = 該小時平均用量增加 · 週重置以新值計 · 間隔過長略過",
        "ck_legend": "打卡（{n} 次）", "last": "最後更新：{t}（{z}）", "refresh": "⟳ 重新整理", "loading": "讀取中…", "refreshing": "⟳ 更新中… {s} 秒",
        "ph_read": "正在從 Grok Build 讀取用量…", "ph_draw": "正在產生圖表…",
        "range": "範圍", "name": "名字", "all": "全部", "note": "備註", "time": "時間", "recent": "最近打卡", "per_name": "各名字統計",
        "top_hour": "最常時段", "none_ck": "尚無打卡", "ck_btn": "打卡",
        "ck_hint": "以本機服務開啟此頁才能在這裡打卡；或執行：usage_history.py checkin <名字> [備註] --category <類別id>",
        "category": "類別", "per_cat": "各類別統計", "count": "次數", "share": "占比", "last_ck": "最近一次",
        "week": "週起始", "days": "天數", "peak": "峰值", "pace": "基準", "at100": "達 100%", "yes": "是",
        "month": "月份", "amount": "金額", "weekly_detail": "每週明細", "conn_err": "無法連線到本機服務",
        "clock_sub": "每小時用量增加（{z}），0 時在上、順時針。左圖：近 24 小時。右圖：顏色 = 不同天（或週／月／季），對照下方圖例；長度 = 該小時用量增加", "no_clock": "資料收集中 — 開始取樣後才會產生時鐘圖",
        "hour_fmt": "{h}時",
    },
}
# privacy / numeric: word-free symbols only (no service, product, timezone or city names)
STR["zh-CN"] = dict(STR["zh-TW"], **{
    "refreshing": "⟳ 更新中… {s} 秒", "ph_read": "正在从 Grok Build 读取用量…", "ph_draw": "正在生成图表…",
    "clock_tab": "时钟", "clock_1d": "用量时钟 — 近 24 小时", "clock_r": "用量时钟 — {r}",
    "clock_rt": {"wk": "本周", "1m": "近一月", "3m": "近一季", "6m": "近半年", "1y": "近一年", "all": "全部"},
    "clock_ro": {"wk": "本周", "1m": "近一月", "3m": "近一季", "6m": "近半年", "1y": "近一年", "all": "全部"}, "clock_rl": "范围",
    "clock_lo": {"ov": "叠加", "sd": "并排"}, "clock_ll": "排列", "clock_fo": {"t": "半透明", "s": "实心"}, "clock_fl": "填色",
    "clock_wd": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"], "clock_wkl": "第{i}周 {a}–{b}", "clock_curp": "{l}（当前）",
    "clock_sub": "每小时用量增加（{z}），0 时在上、顺时针。左图：近 24 小时。右图：颜色 = 不同天（或周／月／季），对照下方图例；长度 = 该小时用量增加",
    "clock_note_g": "时刻为 {z} · 颜色 = 不同天（或周／月／季），对照图例 · 长度 = 该小时用量增加 · 间隔过长略过",
})
STR["numeric"] = dict(STR["en"], **{
    "eyebrow": "", "title": "", "rwk": "W", "week_empty": "∅", "r1m": "1M", "r3m": "3M", "r6m": "6M", "r1y": "1Y", "clock_tab": "◷", "checkin_tab": "✓",
    "weeks": "#", "avg_peak": "Ø ▲", "hit100": "▲100", "extra": "∑ $", "over_n": "▲ {n}", "pace_sub": "{p:g}/d → {w:g}",
    "cap_sub": "▲100", "extra_sub": "{n}", "main": "", "leg_over": "▲", "leg_under": "▼", "leg_pace": "{p:g}/d", "leg_daily": "∑",
    "sparse": "{n}/{m}", "sparse_short": "{n}/{m}", "empty": "∅", "spend_title": "$", "no_spend": "∅", "footer": "{t}",
    "clock_eyebrow": "", "clock_1d": "◷ 1", "clock_r": "◷ {r}", "clock_rt": {"wk": "W", "1m": "1M", "3m": "3M", "6m": "6M", "1y": "1Y", "all": "∞"},
    "clock_ro": {"wk": "W", "1m": "1M", "3m": "3M", "6m": "6M", "1y": "1Y", "all": "∞"}, "clock_rl": "◷",
    "clock_lo": {"ov": "▤", "sd": "▥"}, "clock_ll": "◫", "clock_fo": {"t": "◌", "s": "●"}, "clock_fl": "◐",
    "clock_wd": ["1", "2", "3", "4", "5", "6", "7"], "clock_day": "{md}", "clock_wkl": "{i}: {a}–{b}", "clock_mon": "{y}-{m:02d}", "clock_q": "{y} Q{q}",
    "clock_today": "{l} ●", "clock_curp": "{l} ●", "clock_note_g": "", "clock_peak": "▲ {h}–{h2}  +{v:.1f}  ({n})", "clock_none": "∅",
    "collecting": "{n}/{m}", "clock_note": "", "ck_legend": "✓ {n}", "last": "↻ {t}", "refresh": "↻", "loading": "…", "refreshing": "… {s}", "ph_read": "", "ph_draw": "",
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
        # language / time zone: CLI flag > config > <data_dir>/page_settings.json (the page menus) > auto
        def gp(flag, key):
            v = g(flag, key)
            return v if v not in (None, "") else _read_page_setting(self.data_dir, key)
        self.lang_locked = getattr(a, "lang", None) is not None
        self.tz_locked = getattr(a, "tz", None) is not None
        self.lang_setting = page_lang(gp("lang", "lang"))
        self.tz_setting = page_tz(gp("tz", "tz"))
        # week files keep being written in the CLI/config zone (machine local otherwise); the page menu alone
        # (page_settings.json) changes only what is shown, so existing week files are never split or renamed
        self.data_tz = self._tz(str(g("tz", "tz", "local") or "local"))
        self.privacy = bool(g("privacy", "privacy", False)) or self.lang_setting == "numeric"
        self.pace = float(g("pace", "pace", 14))
        self.period_days = int(cfg.get("period_days", 7))
        self.tz_spec = "local" if self.tz_setting == "auto" else self.tz_setting
        self.tz = self._tz(self.tz_spec)
        lang = self.lang_setting
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
        # off: only the configured palette is drawn on every refresh; others are drawn when the page asks for them
        self.render_all_palettes = bool(cfg.get("render_all_palettes", False))
        self.theme_path = g("theme", "theme", None)
        # "Add patterns" (hatching, ▲/▼, dashed over-line) for any palette: config key "patterns" (or, without a
        # config file, <data_dir>/page_settings.json); a theme file with "patterns": true turns it on as well
        pv = cfg.get("patterns")
        if pv is None:
            pv = _read_page_setting(self.data_dir, "patterns")
        self.patterns = bool(pv) or bool(_theme_file(self.theme_path).get("patterns"))
        # display offset added to the shown weekly % (config "display_offset", or page_settings.json), -2..+2
        ov = cfg.get("display_offset")
        if ov is None:
            ov = _read_page_setting(self.data_dir, "display_offset")
        self.display_offset = usage_offset.clamp(ov)
        self.theme = self.theme_for(self.pal_key)

    @property
    def pal_key(self):
        """Folder / cache key of the configured palette: 'orange-teal', 'orange-teal-pat' with patterns, plus
        '-o+1' etc. when a display offset is set (so images drawn with another offset are never reused)."""
        return self.dir_key(self.palette, self.patterns)

    def dir_key(self, name, patterns=False):
        return usage_palette.key(name, patterns) + self.key_suffix

    @property
    def key_suffix(self):
        """Image cache key part for the page settings that change the drawing: display offset ('-o+1'), a chosen
        language ('-Lzh_tw') and a chosen time zone ('-Zasia_taipei'); empty for the defaults. A value fixed by a CLI
        flag stays out of the key: it cannot change while the service runs, and a restart redraws everything anyway."""
        lang = "auto" if self.lang_locked else self.lang_setting
        tz = "auto" if self.tz_locked else self.tz_setting
        return (usage_offset.suffix(self.display_offset) + ("" if lang == "auto" else "-L" + _slug(lang))
                + ("" if tz == "auto" else "-Z" + _slug(tz)))

    def show(self, pct):
        """Weekly % as shown: raw + display offset (never below 0)."""
        return usage_offset.apply(pct, self.display_offset)

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
        name, pat = usage_palette.split_key(strip_offset(palette))
        t = self._theme(self.theme_path, name)
        t["patterns"] = bool(t["patterns"]) or pat
        return t

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
        t = {"bg_color": "#000000", "text_color": "#E8F4FF", "accent_color": None, "baseline_color": None,
             "under_color": None, "over_color": None, "notice_color": None, "patterns": None}
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
        t["patterns"] = bool(t["patterns"])  # theme opt-in; theme_for() adds the page's "Add patterns" choice
        # the accent (frames, tabs, eyebrow text, focus rings) is the palette's cool colour unless the theme pins one
        t["accent_color"] = t["accent_color"] or t["under_color"]
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


PAGE_LANGS = ("auto", "zh-TW", "zh-CN", "en", "ja", "ko", "numeric")
LANG_NAMES = {"zh-TW": "繁體中文", "zh-CN": "简体中文", "en": "English", "ja": "日本語", "ko": "한국어", "numeric": "純數字 123"}
PAGE_ZONES = ("Asia/Taipei", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Tokyo", "Asia/Seoul", "Asia/Singapore",
              "Asia/Bangkok", "Asia/Kolkata", "Asia/Dubai", "Australia/Sydney", "Pacific/Auckland", "Europe/London",
              "Europe/Paris", "Europe/Berlin", "Europe/Moscow", "America/Sao_Paulo", "America/New_York", "America/Chicago",
              "America/Denver", "America/Los_Angeles", "Pacific/Honolulu", "UTC")
PICK_STR = {  # labels of the language / time zone menus and their "auto" option, in every page language
    "zh-TW": {"lang": "語言", "tz": "時區", "auto": "自動（跟系統）", "locked": "已由啟動參數指定"},
    "zh-CN": {"lang": "语言", "tz": "时区", "auto": "自动（跟随系统）", "locked": "已由启动参数指定"},
    "en": {"lang": "Language", "tz": "Time zone", "auto": "Auto (system)", "locked": "Set by a command-line flag"},
    "ja": {"lang": "言語", "tz": "タイムゾーン", "auto": "自動（システム）", "locked": "起動オプションで指定済み"},
    "ko": {"lang": "언어", "tz": "시간대", "auto": "자동(시스템)", "locked": "실행 옵션으로 지정됨"},
    "numeric": {"lang": "Aa", "tz": "◷", "auto": "auto", "locked": "CLI"},
}


def _slug(v):
    return "".join(c if c.isalnum() else "_" for c in str(v)).lower()


def page_lang(v):
    """A language setting as stored ('auto', 'zh-TW', ..., 'numeric'); anything else counts as 'auto'."""
    v = str(v or "auto").strip()
    for k in PAGE_LANGS:
        if v.lower() == k.lower():
            return k
    return "auto" if v.lower() in ("", "local", "system") else v  # e.g. 'zh' from an old config: resolved later


def valid_zone(v):
    """True for an IANA zone name zoneinfo knows (or UTC)."""
    if not isinstance(v, str) or not v or len(v) > 64 or v.startswith(("/", ".")) or ".." in v or "\\" in v:
        return False
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(v)
        return True
    except Exception:
        return False


def page_tz(v):
    """A time zone setting as stored: 'auto' (machine local) or a zone name."""
    v = str(v or "auto").strip()
    return "auto" if v.lower() in ("", "auto", "local", "system") else v


def tz_option_label(zone, now=None):
    """'Asia/Taipei (UTC+8)': the zone with its current UTC offset."""
    from zoneinfo import ZoneInfo
    dt = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone) if zone != "local" else None)
    off = dt.utcoffset() or timedelta(0)
    m = int(off.total_seconds() // 60); sgn = "+" if m >= 0 else "-"; m = abs(m)
    return f"UTC{sgn}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")


_KEY_SFX = __import__("re").compile(r"(-o[+-][12])?(-L[a-z0-9_]+)?(-Z[a-z0-9_]+)?$")


def strip_offset(k):
    """'gold-sky-pat-o+1-Lja-Zasia_tokyo' -> 'gold-sky-pat' (image folder key without the page-settings part:
    display offset, language, time zone)."""
    k = str(k or "")
    m = _KEY_SFX.search(k)
    return k[: m.start()] if m else k


def is_dir_key(k):
    return usage_palette.is_key(strip_offset(k))


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
def shown_reset(ctx, now=None):
    """The next reset to show, exactly as /usage (or a typed-in reset) gave it, or None (then the HUD shows the
    current week's start + period). Never rolled forward or corrected, even if it looks past or odd; only a value
    older than one period (/usage has not given one since) falls back to the computed time."""
    try:
        st = json.loads((ctx.data_dir / "state.json").read_text(encoding="utf-8"))
        r = datetime.fromisoformat(str(st["reset_at"]))
        upd = datetime.fromisoformat(str(st.get("updated_at") or st["reset_at"]))
    except Exception:
        return None
    now = now or datetime.now(ctx.tz)
    if upd.tzinfo is None:
        upd = upd.replace(tzinfo=ctx.tz)
    if now - upd > timedelta(days=ctx.period_days):
        return None
    return r.isoformat(timespec="minutes")


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
            return dt if dt.tzinfo else dt.replace(tzinfo=ctx.data_tz)
        except ValueError:
            try:
                wd, hm = str(anchor).split()
                days = [d.lower() for d in calendar.day_abbr]
                idx = next(i for i, d in enumerate(days) if wd.lower().startswith(d))
                h, m = map(int, hm.split(":"))
                base = datetime(2026, 1, 5, h, m, tzinfo=ctx.data_tz)  # a Monday
                return base + timedelta(days=idx)
            except Exception:
                pass
    return None


def load_weeks(ctx):
    """Week CSVs for the history tabs, with the display offset applied to every value (files are not changed)."""
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
                    days.append((int(r["day"]), ctx.disp(dt).replace(tzinfo=None), ctx.show(float(r["pct"]))))  # CSV stays raw
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
    PAT = T.get("patterns"); HATCH = usage_palette.HATCH if PAT else None
    cards = [(L["weeks"], str(S["n"]), L["over_n"].format(n=S["over"]), T["accent_color"]),
             (L["avg_peak"], avg, L["pace_sub"].format(p=ctx.pace, w=wk), OVER if (S["avg"] or 0) > wk else UNDER),
             (L["hit100"], (f"{up if S['hit100'] else dn} " if PAT else "") + str(S["hit100"]), L["cap_sub"], OVER if S["hit100"] else UNDER),
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
        if HATCH and w["over"]:  # over = hatched, under = plain, so the two differ without colour
            ax.bar(w["start"], w["peak"], width=ctx.period_days - 0.4, align="edge", facecolor="none", edgecolor=c, hatch=HATCH, lw=0, zorder=2.5)
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
    ax.legend(handles=[Patch(facecolor=OVER, alpha=0.6, edgecolor=OVER, hatch=HATCH, label=(up + " " + L["leg_over"]) if L["leg_over"] != up else up),
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


def hourly_rates(samples, since, max_gap_h, until=None):
    b = {h: [] for h in range(24)}; n = 0
    for (t1, p1), (t2, p2) in zip(samples, samples[1:]):
        if (since is not None and t1 < since) or (until is not None and t1 >= until):
            continue
        gh = (t2 - t1).total_seconds() / 3600
        if gh <= 0 or gh > max_gap_h:
            continue
        b[t1.hour].append((p2 - p1 if p2 >= p1 else p2) / gh); n += 1  # drop = weekly reset -> count new value
    return b, n


CLOCK_MODES = ("ot", "os", "sd")  # overlay translucent (default) / overlay solid / side by side


def clock_periods(ctx, key, since, now, samples):
    """Colour groups for the clock dial, oldest first: [(label, start, end, is_current)], at most 8.
    wk -> days, 1m -> reset weeks, 3m/6m -> months, 1y -> quarters, all -> quarters (or years past 8 quarters).
    None for the 24-hour dial (one series coloured by size)."""
    if key == "1d":
        return None
    L = ctx.L
    st = since or (samples[0][0] if samples else now)
    out = []

    def add(label, a, b, cur):
        a2, b2 = max(a, st), min(b, now)
        if a2 < b2 or cur:
            out.append(((L["clock_today"] if key == "wk" else L["clock_curp"]).format(l=label) if cur else label, a2, b2, cur))

    def month_start(y, m):
        return st.replace(year=y, month=m, day=1, hour=0, minute=0, second=0, microsecond=0)

    if key == "wk":
        d = st.replace(hour=0, minute=0, second=0, microsecond=0)
        while d < now:
            e = (d + timedelta(days=1, hours=3)).replace(hour=0, minute=0)
            add(L["clock_day"].format(wd=L["clock_wd"][d.weekday()], md=f"{d:%m/%d}"), d, e, d.date() == now.date())
            d = e
    elif key == "1m":
        try:
            ws = _period_start(ctx, st)
        except Exception:
            ws = st
        i = 1
        while ws < now:
            we = ws + timedelta(days=ctx.period_days)
            add(L["clock_wkl"].format(i=i, a=f"{max(ws, st):%m/%d}", b=f"{min(we, now) - timedelta(seconds=1):%m/%d}"), ws, we, ws <= now < we)
            ws = we; i += 1
    else:
        unit = {"3m": 1, "6m": 1, "1y": 3}.get(key, 3)
        if key == "all" and ((now.year - st.year) * 12 + now.month - st.month) // 3 + 1 > 8:
            unit = 12
        y, m = st.year, ((st.month - 1) // unit) * unit + 1
        while True:
            a = month_start(y, m)
            if a >= now:
                break
            y2, m2 = (y + (m - 1 + unit) // 12, (m - 1 + unit) % 12 + 1)
            b = month_start(y2, m2)
            if unit == 1:
                lab = L["clock_mon"].format(mon=calendar.month_abbr[m], y=y, m=m)
            elif unit == 3:
                lab = L["clock_q"].format(q=(m - 1) // 3 + 1, y=y)
            else:
                lab = str(y)
            add(lab, a, b, a <= now < b)
            y, m = y2, m2
    return out[-8:]


def draw_clock(ctx, samples, cks, colors, path, title, since, now, zl, key="1d", mode="ot"):
    plt = _plt(ctx)
    from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgba
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter
    T, L = ctx.theme, ctx.L
    cmap = LinearSegmentedColormap.from_list("hud", [T["under_color"], T["over_color"]])
    max_gap = max(2.0, 1.5 * ctx.interval / 60)
    b, n = hourly_rates(samples, since, max_gap)
    avg = [(sum(v) / len(v)) if v else 0.0 for _, v in sorted(b.items())]
    need = max(6, min(24, int(24 * 60 / ctx.interval)))
    periods = clock_periods(ctx, key, since, now, samples)
    series = []  # (label, colour, [24 values], has_data, is_current)
    if periods:
        cols = usage_palette.period_colors(T["palette"], T["is_light"], 8)
        for gi, (lab, a, e, cur) in enumerate(periods):
            gb, gn = hourly_rates(samples, a, max_gap, e)
            series.append((lab, cols[gi % len(cols)], [(sum(v) / len(v)) if v else 0.0 for _, v in sorted(gb.items())], gn > 0, cur))
    fig = plt.figure(figsize=(7.2, 8.6), dpi=110, facecolor=T["bg_color"])
    if L["clock_eyebrow"]:
        fig.text(0.05, 0.972, L["clock_eyebrow"], color=T["accent_color"], fontsize=9.5, fontweight="bold", va="top")
    fig.text(0.05, 0.94, title, color=T["text_color"], fontsize=18, fontweight="bold", va="center")
    st = since or (samples[0][0] if samples else now)
    fig.text(0.05, 0.908, f"{st:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M}" + ("" if ctx.privacy else f" ({zl})") + f" · n={n}",
             color=T["dim"], fontsize=10, fontweight="bold", va="center")
    ax = fig.add_axes([0.14, 0.27, 0.72, 0.59], projection="polar", facecolor=T["card"])
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    w = 2 * math.pi / 24; th = [(h + 0.5) * w for h in range(24)]
    vmax = max([max(avg), 1.0] + [max(v) for _, _, v, _, _ in series]); norm = Normalize(0, vmax)
    if not series:
        ax.bar(th, avg, width=w * 0.86, color=[cmap(norm(a)) for a in avg], edgecolor=T["border"], lw=0.6, alpha=0.95, zorder=3)
    elif mode == "sd":  # thin wedges side by side, one per period, oldest first (clockwise)
        g = len(series); ww = w * 0.86 / g
        for gi, (_, col, vals, _, _) in enumerate(series):
            ax.bar([h * w + w * 0.07 + (gi + 0.5) * ww for h in range(24)], vals, width=ww * 0.92, color=col, edgecolor=T["card"], lw=0.3, zorder=3)
    else:  # overlay: full-width bars per hour, longest drawn first so a shorter bar never hides a taller one
        for h in range(24):
            order = sorted(range(len(series)), key=lambda gi: -series[gi][2][h])
            for rank, gi in enumerate(order):
                v = series[gi][2][h]
                if v <= 0:
                    continue
                col = series[gi][1]
                if mode == "os":
                    ax.bar(th[h], v, width=w * 0.86, color=col, edgecolor=T["card"], lw=0.6, zorder=3 + rank * 0.01)
                else:
                    ax.bar(th[h], v, width=w * 0.86, facecolor=to_rgba(col, 0.4), edgecolor=col, lw=1.1, zorder=3 + rank * 0.01)
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
        leg = ax.legend(loc="lower left", bbox_to_anchor=(-0.16, -0.12), frameon=False, fontsize=8.5, labelcolor=T["text_color"],
                        title=L["ck_legend"].format(n=len(ck)), handletextpad=0.3)
        leg.get_title().set_color(T["dim"]); leg.get_title().set_fontsize(8.5)
    if not series:
        for h, a in enumerate(avg):
            if a > 0 and a >= vmax * 0.35:
                ax.text(th[h], a + vmax * 0.06, f"{a:.1f}", color=T["text_color"], fontsize=8, ha="center", va="center", zorder=5)
    if n:
        top = max(range(24), key=lambda h: avg[h])
        summ = L["clock_peak"].format(h=top, h2=top + 1, v=avg[top], n=n)
    else:
        summ = L["clock_none"]
    fig.text(0.5, 0.222, summ, color=T["text_color"], fontsize=11, fontweight="bold", ha="center")
    if series:  # legend under the dial: colour = period (oldest first); periods without data are dimmed
        hs = [Patch(facecolor=to_rgba(col, 0.45 if (mode == "ot" and has) else (1.0 if has else 0.25)), edgecolor=to_rgba(col, 1.0 if has else 0.3),
                    lw=1.2, label=lab) for lab, col, _, has, _ in series]
        nc = min(4, len(hs)); rows = -(-len(hs) // nc)
        order = [r * nc + c for c in range(nc) for r in range(rows) if r * nc + c < len(hs)]  # read left to right, then down
        hs = [hs[i] for i in order]; series = [series[i] for i in order]
        lg = fig.legend(handles=hs, loc="upper center", bbox_to_anchor=(0.5, 0.198), ncol=nc, frameon=False, fontsize=9,
                        handlelength=1.4, columnspacing=1.2, handletextpad=0.5)
        for txt, (_, _, _, has, cur) in zip(lg.get_texts(), series):
            txt.set_color(T["text_color"] if has else T["dim"]); txt.set_alpha(1.0 if has else 0.6)
            if cur:
                txt.set_fontweight("bold")
        note = L.get("clock_note_g") or ""
    else:
        sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
        cax = fig.add_axes([0.25, 0.165, 0.5, 0.012]); cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
        cb.outline.set_edgecolor(T["border"]); cb.ax.tick_params(colors=T["dim"], labelsize=8)
        note = L["clock_note"]
    if note:
        fig.text(0.5, 0.04, note.format(z=zl), color=T["dim"], fontsize=7.5, ha="center", wrap=True)
    if n < need:
        fig.text(0.95, 0.94, L["collecting"].format(n=n, m=need), color=T["notice_color"], fontsize=11, fontweight="bold", ha="right", va="center",
                 bbox=dict(boxstyle="round,pad=0.35", facecolor=_blend(T["bg_color"], T["notice_color"], 0.12), edgecolor=T["notice_color"]))
    fig.savefig(path, facecolor=T["bg_color"]); plt.close(fig)


GEN_FILE = ".gen"  # history/.gen: id of the latest render; history/pal/<name>/.gen: render that palette matches


def _cycle_palettes(ctx):
    """Palette keys drawn on every refresh: the configured one (all presets with render_all_palettes), with the
    configured patterns choice; the other combinations are drawn lazily (render_palette)."""
    if ctx.render_all_palettes:
        return [ctx.dir_key(n, ctx.patterns) for n in usage_palette.names()]
    return [ctx.pal_key]


def _palette_dirs(ctx, names=None):
    """(palette, out_dir): images go to history/pal/<name>/; the configured palette is also copied to history/
    (embedded in index.html). Other palettes are drawn lazily by render_palette() when the page asks."""
    return [(name, ctx.hist_dir / "pal" / name) for name in (_cycle_palettes(ctx) if names is None else names)]


def _read_text(p):
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_text(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _new_gen(ctx, names):
    """A fresh render id; palettes not drawn in this pass become stale and are redrawn on demand."""
    gen = f"{time.time_ns():x}"
    _write_text(ctx.hist_dir / GEN_FILE, gen)
    for _, d in _palette_dirs(ctx, names):
        _write_text(d / GEN_FILE, gen)
    return gen


def _publish_default(ctx, files):
    """Copy the configured palette's images to history/ (what index.html embeds)."""
    import shutil
    src = ctx.hist_dir / "pal" / ctx.pal_key
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


def _clock_inputs(ctx):
    now = ctx.now()
    return now, ctx.zlabel(now), [x for x in load_samples(ctx) if x[0] <= now], load_checkins(ctx)


# Clock tab: left = last 24 hours, right = a range picked on the page. "wk" (the current reset week) is drawn on
# every refresh; the others only when the page asks (POST /api/palette/render {"clock": key}), then cached.
CLOCK_RANGES = ("wk", "1m", "3m", "6m", "1y", "all")
CLOCK_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}  # rolling ranges; "wk" = since the last reset


def _clock_since(ctx, key, now):
    if key == "1d":
        return now - timedelta(hours=24)
    if key == "wk":
        try:
            return _period_start(ctx, now)
        except Exception:
            return now - timedelta(days=ctx.period_days)
    if key == "all":
        return None
    return now - timedelta(days=CLOCK_DAYS[key])  # rolling window counted back from now


def _clock_title(ctx, key):
    L = ctx.L
    return L["clock_1d"] if key == "1d" else L["clock_r"].format(r=L["clock_rt"][key])


def clock_file(key, mode=None):
    return "clock-1d.png" if key == "1d" else f"clock-{key}-{mode or CLOCK_MODES[0]}.png"


def _draw_clocks(ctx, names, now, zl, s, ck, items=(("1d", None), ("wk", "ot"))):
    cks, _, colors = ck
    for name, d in _palette_dirs(ctx, names):
        d.mkdir(parents=True, exist_ok=True)
        for key, mode in items:
            _with_palette(ctx, name, lambda: draw_clock(ctx, s, cks, colors, d / clock_file(key, mode), _clock_title(ctx, key),
                                                          _clock_since(ctx, key, now), now, zl, key, mode or "ot"))


def render_clock(ctx):
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    now, zl, s, ck = _clock_inputs(ctx)
    names = _cycle_palettes(ctx)
    _draw_clocks(ctx, names, now, zl, s, ck)
    _publish_default(ctx, ["clock-1d.png", clock_file("wk")])
    for d in [ctx.hist_dir] + [p for _, p in _palette_dirs(ctx)]:
        for old in ("clock-7d.png", "clock-all.png", "clock-wk.png"):  # names used by earlier versions
            if (d / old).exists():
                (d / old).unlink()
    _new_gen(ctx, names)
    return {"ok": True, "samples": len(s), "files": [str(ctx.hist_dir / "clock-1d.png"), str(ctx.hist_dir / clock_file("wk"))]}


def render_clock_range(ctx, name, key, mode="ot"):
    """history/pal/<name>/clock-<key>-<mode>.png for the clock tab's range / layout menus; cached until the next refresh."""
    d = ctx.hist_dir / "pal" / name
    gen = _read_text(ctx.hist_dir / GEN_FILE)
    fn = clock_file(key, mode)
    stamp = d / f"{GEN_FILE}-{fn[:-4]}"
    if gen and (d / fn).exists() and _read_text(stamp) == gen:
        return False
    now, zl, s, ck = _clock_inputs(ctx)
    _draw_clocks(ctx, [name], now, zl, s, ck, items=((key, mode),))
    if gen:
        _write_text(stamp, gen)
    return True


def render_current_week(ctx, nowa, names=None):
    """'This week' tab: the current week's daily cumulative chart (chart_hud) for the given palettes."""
    out = ctx.hist_dir / "week-current.png"
    publish = names is None or ctx.pal_key in names
    try:
        ws = _period_start(ctx, nowa)
        f = ctx.data_dir / f"week-{ws.astimezone(ctx.tz):%Y-%m-%d}.csv"
        rows = [r for r in csv.DictReader(f.open(encoding="utf-8"))] if f.exists() else []
        if not rows:
            for d in ([out] if publish else []) + [dd / "week-current.png" for _, dd in _palette_dirs(ctx, names)]:
                if d.exists():
                    d.unlink()
            return False
        import chart_hud
        for name, d in _palette_dirs(ctx, names):
            d.mkdir(parents=True, exist_ok=True)
            base, pat = usage_palette.split_key(strip_offset(name))
            chart_hud.render(rows, str(d / "week-current.png"), f"{ws.astimezone(ctx.tz):%Y-%m-%d}",
                             {"lang": "numeric" if ctx.privacy else ctx.lang, "privacy": ctx.privacy, "pace": ctx.pace,
                              "palette": base, "reset_hm": f"{ws.astimezone(ctx.tz):%H:%M}",
                              "accent_color": _theme_explicit_value(ctx.theme_path, "accent"),
                              "patterns": pat or bool(_theme_file(ctx.theme_path).get("patterns")),
                              "display_offset": ctx.display_offset, "reset_at": shown_reset(ctx)})
        if publish:
            _publish_default(ctx, ["week-current.png"])
        return True
    except Exception as exc:
        print(f"Warning: current week chart failed: {exc}", file=sys.stderr)
        return False


def _history_inputs(ctx):
    nowa = ctx.now(); zl = ctx.zlabel(nowa); now = nowa.replace(tzinfo=None, second=0, microsecond=0)
    weeks, extra = load_weeks(ctx), load_extra(ctx)
    return nowa, zl, now, [summarize(ctx, weeks, extra, k, m, mw, now) for k, m, mw in RANGES]


def _draw_ranges(ctx, names, sums, now, zl):
    for name, d in _palette_dirs(ctx, names):
        d.mkdir(parents=True, exist_ok=True)
        for S in sums:
            _with_palette(ctx, name, lambda: draw_range(ctx, S, d / f"history-{S['key']}.png", now, zl))


def render_history(ctx):
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    nowa, zl, now, sums = _history_inputs(ctx)
    names = _cycle_palettes(ctx)
    _draw_ranges(ctx, names, sums, now, zl)
    _publish_default(ctx, [f"history-{S['key']}.png" for S in sums])
    render_current_week(ctx, nowa, names)
    gen = _new_gen(ctx, names)
    write_html(ctx, sums, now, nowa, zl, gen)
    return {"ok": True, "html": str(ctx.hist_dir / "index.html"),
            "ranges": {S["key"]: {"weeks": S["n"], "sparse": S["sparse"]} for S in sums}}


def render_palette(ctx, name, clock=None, mode=None, patterns=False):
    """Draw one palette's full image set into history/pal/<key>/ when the page switches to it (plus, with
    clock=<range>, that clock-tab range); key = name, or name + '-pat' with patterns. Cached: nothing is drawn
    while the files match the latest render."""
    if name not in usage_palette.names():
        return {"ok": False, "error": "unknown palette"}
    name = ctx.dir_key(name, patterns)
    if clock is not None and clock not in CLOCK_RANGES:
        return {"ok": False, "error": "unknown clock range"}
    if mode is not None and mode not in CLOCK_MODES:
        return {"ok": False, "error": "unknown clock layout"}
    extra = clock is not None and (clock, mode or "ot") != ("wk", "ot")
    d = ctx.hist_dir / "pal" / name
    gen = _read_text(ctx.hist_dir / GEN_FILE)
    t0 = time.perf_counter()
    if gen and _read_text(d / GEN_FILE) == gen:
        drew = extra and render_clock_range(ctx, name, clock, mode or "ot")
        out = {"ok": True, "name": name, "v": gen, "cached": not drew}
        return dict(out, seconds=round(time.perf_counter() - t0, 2)) if drew else out
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    now, zl, s, ck = _clock_inputs(ctx)
    _draw_clocks(ctx, [name], now, zl, s, ck)
    nowa, zl2, now2, sums = _history_inputs(ctx)
    _draw_ranges(ctx, [name], sums, now2, zl2)
    render_current_week(ctx, nowa, [name])
    if not gen:
        gen = f"{time.time_ns():x}"; _write_text(ctx.hist_dir / GEN_FILE, gen)
    _write_text(d / GEN_FILE, gen)
    if extra:
        render_clock_range(ctx, name, clock, mode or "ot")
    return {"ok": True, "name": name, "v": gen, "cached": False, "seconds": round(time.perf_counter() - t0, 2)}


# Ledger status bar + browser notifications (Web Notifications API; permission is asked from a button,
# never on load). When served, polls /api/ledger every 3 minutes; each alert id (one per day) fires once
# (seen ids kept in localStorage).
LEDGER_JS = "(function(){var $=id=>document.getElementById(id),served=/^https?:$/.test(location.protocol),led=null,S=JSON.parse($('ledgerstr').textContent),OFF=S.off||0,adj=function(p){return p==null?p:Math.max(0,Math.round((+p+OFF)*10)/10);};\ntry{led=JSON.parse($('ledgerdata').textContent);}catch(e){}\nfunction f(t,o){return String(t).replace(/\\{(\\w+)\\}/g,function(_,k){return o[k]!=null?o[k]:'';});}\nfunction el(t,x,c){var e=document.createElement(t);if(x!=null)e.textContent=x;if(c)e.className=c;return e;}\nfunction parts(iso){if(!iso)return null;var d=new Date(iso);if(isNaN(d))return null;var m=/([+-])(\\d\\d):(\\d\\d)$/.exec(iso);\nvar off=m?(m[1]=='-'?-1:1)*(+m[2]*60+ +m[3]):0,w=new Date(d.getTime()+off*60000),p=n=>String(n).padStart(2,'0');\nreturn {wd:S.wd[(w.getUTCDay()+6)%7],t:p(w.getUTCMonth()+1)+'-'+p(w.getUTCDate())+' '+p(w.getUTCHours())+':'+p(w.getUTCMinutes()),\nhm:p(w.getUTCHours())+':'+p(w.getUTCMinutes()),date:RD(w,S.dl),z:m?'UTC'+m[1]+(+m[2])+(+m[3]?':'+m[3]:''):'UTC'};}\nfunction render(L){var box=$('ledger');box.textContent='';if(!L){box.appendChild(el('div',S.none,'info'));return;}\nvar a=parts(L.asOf),at=a?a.t+(S.showz?' ('+a.z+')':''):'?',r=parts(L.resetsAt);\nif(L.status!=='ok'){var w=el('div',null,'warn'),lg=L.lastGood,lp=lg&&parts(lg.asOf);w.appendChild(el('b',S.unavailable));\nw.appendChild(document.createTextNode(' · '+f(S.tried,{t:at})+(L.error&&!S.privacy?' · '+f(S.reason,{e:L.error}):'')+' · '+\n(lg&&lg.weeklyPercentUsed!=null?f(S.last_good,{p:adj(lg.weeklyPercentUsed),t:lp?lp.t:'?'}):S.no_last)+' · '+S.reset_unknown));box.appendChild(w);}\nelse{var i=el('div',null,'info');i.appendChild(el('b',f(S.now,{p:adj(L.weeklyPercentUsed)})));if(S.otag)i.appendChild(el('span',' '+S.otag,'otag'));\ni.appendChild(document.createTextNode(' · '+(r?f(S.reset,{wd:r.wd,hm:r.hm,date:r.date,z:r.z}):S.reset_unknown)+' · '+f(S.read_at,{t:at})));box.appendChild(i);}\nvar bs=el('div',null,'badges');\nif(L.spike&&L.spike.flag){var s=parts(L.spike.since);bs.appendChild(el('span',f(S.spike,{p:L.spike.points,t:s?s.t:'?'}),'badge'));}\nif(L.overBudget&&L.overBudget.flag)bs.appendChild(el('span',f(S.budget,{p:adj(L.weeklyPercentUsed),th:L.overBudget.threshold}),'badge'));\nif(L.status!=='ok'&&L.consecutiveFailures)bs.appendChild(el('span',f(S.fails,{n:L.consecutiveFailures}),'badge note'));\nvar age=a?(Date.now()-new Date(L.asOf).getTime())/3600000:null;\nif(age!=null&&age>(L.staleAfterHours||6))bs.appendChild(el('span',f(S.stale,{h:Math.floor(age)}),'badge note'));\nif(bs.children.length)box.appendChild(bs);}\nrender(led);\nvar nb=$('notifybtn'),KEY='usage.alertsSeen';\nfunction seen(){try{return JSON.parse(localStorage.getItem(KEY)||'[]');}catch(e){return [];}}\nfunction fire(L){if(!L||!L.alerts||!('Notification' in window)||Notification.permission!=='granted')return;\nvar s=seen(),ch=false;L.alerts.forEach(function(x){if(s.indexOf(x.id)>=0)return;try{new Notification(x.title,{body:x.message,tag:x.id});}catch(e){}s.push(x.id);ch=true;});\nif(ch){try{localStorage.setItem(KEY,JSON.stringify(s.slice(-60)));}catch(e){}}}\nfunction poll(){fetch('/api/ledger',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(L=>{if(L){led=L;render(L);fire(L);}}).catch(()=>{});}\nif(served){if('Notification' in window&&Notification.permission==='default'){nb.hidden=false;\nnb.onclick=function(){Notification.requestPermission().then(function(p){nb.hidden=(p!=='default');if(p==='granted')fire(led);});};}\nfire(led);poll();setInterval(poll,180000);}})();\n"
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

LEDGER_JS = LEDGER_JS.replace("function parts(", usage_dates.JS + "\nfunction parts(", 1)

LEDGER_STR = {
    "en": {"wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "now": "Now {p}%", "reset": "resets {date} ({z})", "dl": "en",
           "reset_unknown": "reset unknown", "read_at": "read {t}", "unavailable": "⚠ Usage unavailable", "tried": "tried {t}",
           "reason": "reason: {e}", "last_good": "last good {p}% ({t})", "no_last": "no good reading yet",
           "spike": "▲ +{p} pts in 24h (since {t})", "budget": "● {p}% (≥ {th}%)", "fails": "{n} failed reads in a row",
           "stale": "data is {h}h old", "none": "No ledger yet (run a sample once)", "notify": "Enable desktop notifications", "showz": True},
    "zh-TW": {"wd": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"], "now": "目前 {p}%", "reset": "重置 {date}（{z}）", "dl": "zh-TW",
              "reset_unknown": "重置時間未知", "read_at": "讀取於 {t}", "unavailable": "⚠ 無法取得目前用量", "tried": "嘗試於 {t}",
              "reason": "原因：{e}", "last_good": "最後成功 {p}%（{t}）", "no_last": "尚無成功紀錄",
              "spike": "▲ 24 小時暴增 +{p} 點（自 {t}）", "budget": "● 已達 {p}%（門檻 {th}%）", "fails": "連續 {n} 次無法取得",
              "stale": "資料已 {h} 小時未更新", "none": "尚無 ledger（取樣一次後出現）", "notify": "開啟桌面通知", "showz": True},
    "numeric": {"wd": ["1", "2", "3", "4", "5", "6", "7"], "now": "{p}%", "reset": "↻ {date}", "dl": "numeric", "reset_unknown": "↻ ?", "read_at": "@ {t}",
                "unavailable": "∅", "tried": "@ {t}", "reason": "", "last_good": "✓ {p}% @ {t}", "no_last": "✓ —",
                "spike": "▲ +{p} / 24h", "budget": "● {p}% ≥ {th}%", "fails": "∅ ×{n}", "stale": "⌛ {h}h", "none": "∅",
                "notify": "🔔", "showz": False, "privacy": True},
}
LEDGER_STR["zh-CN"] = dict(LEDGER_STR["zh-TW"], wd=["周一", "周二", "周三", "周四", "周五", "周六", "周日"], dl="zh-CN",
                           now="目前 {p}%", reset="重置 {date}（{z}）", reset_unknown="重置时间未知", read_at="读取于 {t}",
                           unavailable="⚠ 无法获取当前用量", tried="尝试于 {t}", reason="原因：{e}", last_good="最后成功 {p}%（{t}）",
                           no_last="尚无成功记录", spike="▲ 24 小时暴增 +{p} 点（自 {t}）", budget="● 已达 {p}%（阈值 {th}%）",
                           fails="连续 {n} 次无法获取", stale="数据已 {h} 小时未更新", none="尚无 ledger（取样一次后出现）",
                           notify="开启桌面通知")


UPDATE_STR = {  # "Check for updates" / "Update" (POST /api/update/check, /api/update/apply); served page only
    "en": {"btn": "Check for updates", "go": "Update", "checking": "Checking…",
           "new": "New version: {h} {s} ({n} behind)", "up_to_date": "You have the latest version.",
           "not_git": "This folder was not installed with git clone (for example from a ZIP), so it cannot update itself. "
                      "Download the new version by hand, or reinstall with git clone (you can move your usage-data folder over).",
           "no_git": "Git was not found, so updates cannot be checked. Install Git and try again.",
           "no_remote": "This folder has no 'origin' remote, so updates cannot be checked.",
           "fetch_failed": "Could not reach GitHub to check for updates (are you offline?). Try again later.",
           "local_changes": "A new version is out, but program files in this folder were changed. To keep your changes, "
                            "it will not update by itself. Please update by hand.",
           "diverged": "A new version is out, but this folder has its own commits that GitHub does not have. "
                       "It will not update by itself. Please update by hand.",
           "wrong_branch": "This folder is not on the main branch, so it will not update by itself.",
           "git_error": "Could not check for updates.", "cooldown": "Please wait {s}s before checking again.",
           "busy": "An update is already running.", "confirm": "Update and restart the dashboard now? Your data is kept.",
           "updating": "Updating… (about 1–3 minutes; the page reloads by itself)",
           "slow": "The update is taking too long. See update.log in the data folder, or update with the commands in the README.",
           "failed": "The update did not start: {e}", "auto": "Check for updates automatically"},
    "zh-TW": {"btn": "檢查更新", "go": "更新", "checking": "檢查中…",
              "new": "有新版本：{h} {s}（落後 {n} 個）", "up_to_date": "已經是最新版本。",
              "not_git": "這個資料夾不是用 git clone 安裝的（例如從 ZIP 解壓縮），所以不能自動更新。"
                         "請手動下載新版，或改用 git clone 重新安裝（usage-data 資料夾可以直接搬過去）。",
              "no_git": "找不到 Git，無法檢查更新。請先安裝 Git 再試一次。",
              "no_remote": "這個資料夾沒有設定 origin，無法檢查更新。",
              "fetch_failed": "連不到 GitHub，無法檢查更新（可能沒有網路）。請稍後再試。",
              "local_changes": "有新版本，但這個資料夾裡的程式檔被改過。為了不蓋掉你的修改，不會自動更新，請手動更新。",
              "diverged": "有新版本，但這個資料夾有 GitHub 上沒有的提交，不會自動更新，請手動更新。",
              "wrong_branch": "這個資料夾目前不在 main 分支，不會自動更新。",
              "git_error": "無法檢查更新。", "cooldown": "請等 {s} 秒再檢查。",
              "busy": "已經在更新了。", "confirm": "要現在更新並重新啟動儀表板嗎？你的資料會保留。",
              "updating": "更新中…（大約 1–3 分鐘，完成後頁面會自動重新整理）",
              "slow": "更新等太久了。請看資料資料夾裡的 update.log，或照 README 的指令手動更新。",
              "failed": "更新沒有開始：{e}", "auto": "自動檢查更新"},
    "numeric": {"btn": "⇪", "go": "⇪ ↻", "checking": "…", "new": "⇪ {h} (−{n})", "up_to_date": "✓",
                "not_git": "× zip", "no_git": "× git", "no_remote": "× git", "fetch_failed": "× ⇅", "local_changes": "× ✎",
                "diverged": "× ⑂", "wrong_branch": "× ⑂", "git_error": "×", "cooldown": "⌛ {s}s", "busy": "↻ …",
                "confirm": "⇪ ↻ ?", "updating": "↻ …", "slow": "× log", "failed": "×", "auto": "⇪ ⏱", "privacy": True},
}
UPDATE_CHECK_COOLDOWN = 30  # seconds between real "git fetch" runs; a repeat click inside it gets the last answer
UPDATE_AUTO_FIRST_DELAY = 60  # automatic check: first look a minute after start (then about once a day)


# Palette switcher: a keyboard-usable list (button + role=listbox) with each palette's two colours. Picking one asks
# the server to draw that palette (POST /api/palette/render, cached), preloads its images from history/pal/<name>/,
# then switches the CSS colour variables (--over/--under/--accent/--bd...) and the images together. The choice is
# remembered per browser (localStorage 'usage.palette'). Opened as a file, only palettes already on disk can be shown.
# Display offset menu: remembered in the browser (usage.displayOffset) and saved in the config by
# POST /api/display-offset, which redraws the charts; the page then reloads. Without a server it is read-only.
OFFSET_JS = r"""(function(){var s=document.getElementById('offsel');if(!s)return;var served=/^https?:$/.test(location.protocol),
T=(document.querySelector('meta[name="usage-token"]')||{}).content||'',K='usage.displayOffset';
try{localStorage.setItem(K,s.value);}catch(e){}
if(!served){s.disabled=true;return;}
s.onchange=function(){var v=parseInt(s.value,10);s.disabled=true;try{localStorage.setItem(K,String(v));}catch(e){}
fetch('/api/display-offset',{method:'POST',headers:{'X-Usage-Token':T,'Content-Type':'application/json'},body:JSON.stringify({offset:v})})
.then(function(r){return r.json();}).then(function(j){if(j&&j.ok)location.reload();else s.disabled=false;}).catch(function(){s.disabled=false;});};})();"""
# Language / time zone menus: same as the offset menu (browser: usage.lang / usage.tz; server: POST /api/lang,
# /api/tz, saved as "lang" / "tz" in usage_config.json or page_settings.json); the page reloads redrawn.
PICK_JS = r"""(function(){var T=(document.querySelector('meta[name="usage-token"]')||{}).content||'',served=/^https?:$/.test(location.protocol);
[].forEach.call(document.querySelectorAll('select[data-api]'),function(s){var K=s.dataset.k,F=s.dataset.f;
try{localStorage.setItem(K,s.value);}catch(e){}
if(!served||s.dataset.locked){s.disabled=true;return;}
s.onchange=function(){var v=s.value,b={};b[F]=v;s.disabled=true;try{localStorage.setItem(K,v);}catch(e){}
fetch(s.dataset.api,{method:'POST',headers:{'X-Usage-Token':T,'Content-Type':'application/json'},body:JSON.stringify(b)})
.then(function(r){return r.json();}).then(function(j){if(j&&j.ok)location.reload();else s.disabled=false;}).catch(function(){s.disabled=false;});};});})();"""
PALETTE_JS = r"""(function(){var $=function(i){return document.getElementById(i);},P=JSON.parse($('paldata').textContent),KEY='usage.palette',PKEY='usage.patterns',pchk=$('patchk'),CK={r:'usage.clockRange',l:'usage.clockLayout',f:'usage.clockFill'},D={r:'wk',l:'ov',f:'t'},
served=/^https?:$/.test(location.protocol),btn=$('palbtn'),msg=$('palmsg'),V=P.light?'light':'dark',cur=P.base,pat=!!P.pat,CR={r:'wk',l:'ov',f:'t'},seq=0,
TKN=(document.querySelector('meta[name="usage-token"]')||{}).content||'',clk=$('clkimg'),
imgs=[].slice.call(document.querySelectorAll('img[data-f]'));imgs.forEach(function(i){i.dataset.orig=i.getAttribute('src');i.dataset.f0=i.dataset.f;});
function rgb(h){h=String(h).replace('#','');if(h.length==3)h=h.replace(/./g,'$&$&');return [0,2,4].map(function(i){return parseInt(h.substr(i,2),16)||0;});}
function mix(a,b,t){var x=rgb(a),y=rgb(b);return '#'+x.map(function(v,i){return ('0'+Math.round(v+(y[i]-v)*t).toString(16)).slice(-2);}).join('');}
function lum(h){var c=rgb(h).map(function(v){v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);});return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2];}
function vars(n){var c=P.table[n][V],o={};['over','under','baseline','notice'].forEach(function(k){if(!P.fixed[k])o['--'+k]=c[k];});
var a=P.fixed.accent?P.accent:(P.fixed.under?P.under:c.under);o['--accent']=a;o['--bd']=mix(P.bg,a,0.25);o['--on-accent']=lum(a)>0.4?'#0B0F14':'#FFFFFF';return o;}
function mode(c){return c.l==='sd'?'sd':'o'+c.f;}function same(a,b){return a.r===b.r&&a.l===b.l&&a.f===b.f;}
function fileOf(i,c){return i===clk?'clock-'+c.r+'-'+mode(c)+'.png':i.dataset.f0;}
function key(n,p){return (p?n+'-pat':n)+P.osfx;}
function src(i,k,r,v){var f=fileOf(i,r);return (k===P.default&&f===i.dataset.f0)?i.dataset.orig:'pal/'+k+'/'+f+(v?'?v='+encodeURIComponent(v):'');}
function pre(u){return new Promise(function(res){var im=new Image();im.onload=function(){(im.decode?im.decode():Promise.resolve()).then(function(){res(true);},function(){res(true);});};
im.onerror=function(){res(false);};im.src=u;});}
function say(t,spin){msg.textContent='';if(spin){var e=document.createElement('span');e.className='spin';msg.appendChild(e);}if(t)msg.appendChild(document.createTextNode(t));}
// a keyboard-usable drop-down: button + role=listbox (arrows, Home/End, Enter/Space, Esc, Tab), styled like a select
function listbox(b,list,label,getCur,onPick){if(!b||!list)return {mark:function(){},has:function(){return false;}};var opts=[].slice.call(list.querySelectorAll('[role=option]')),act=0;
function idx(v){for(var i=0;i<opts.length;i++)if(opts[i].dataset.v===v)return i;return 0;}
function hi(i){act=(i+opts.length)%opts.length;opts.forEach(function(o,j){o.classList.toggle('act',j===act);});list.setAttribute('aria-activedescendant',opts[act].id);
if(opts[act].scrollIntoView)opts[act].scrollIntoView({block:'nearest'});}
function open(){list.hidden=false;b.setAttribute('aria-expanded','true');hi(idx(getCur()));list.focus();}
function close(f){list.hidden=true;b.setAttribute('aria-expanded','false');if(f)b.focus();}
function pick(i){close(true);onPick(opts[i].dataset.v);}
b.onclick=function(){if(list.hidden)open();else close(true);};
b.onkeydown=function(e){if(['ArrowDown','ArrowUp','Enter',' '].indexOf(e.key)>=0){e.preventDefault();open();}};
list.onkeydown=function(e){var k=e.key;if(k==='ArrowDown'){e.preventDefault();hi(act+1);}else if(k==='ArrowUp'){e.preventDefault();hi(act-1);}
else if(k==='Home'){e.preventDefault();hi(0);}else if(k==='End'){e.preventDefault();hi(opts.length-1);}
else if(k==='Enter'||k===' '){e.preventDefault();pick(act);}else if(k==='Escape'){e.preventDefault();close(true);}else if(k==='Tab'){close(false);}};
opts.forEach(function(o,i){o.onclick=function(){pick(i);};o.onmousemove=function(){if(act!==i)hi(i);};});
document.addEventListener('click',function(e){if(!list.hidden&&!b.parentNode.contains(e.target))close(false);});
return {has:function(v){return opts.some(function(o){return o.dataset.v===v;});},
mark:function(v){opts.forEach(function(o){o.setAttribute('aria-selected',o.dataset.v===v?'true':'false');});label.innerHTML=opts[idx(v)].innerHTML;}};}
var PL=listbox(btn,$('pallist'),$('palcur'),function(){return cur;},function(v){go(v,CR,true,pat);}),
CL=listbox($('clkbtn'),$('clklist'),$('clkcur'),function(){return CR.r;},function(v){go(cur,with_('r',v),true,pat);}),
LL=listbox($('clobtn'),$('clolist'),$('clocur'),function(){return CR.l;},function(v){go(cur,with_('l',v),true,pat);}),
FL=listbox($('clfbtn'),$('clflist'),$('clfcur'),function(){return CR.f;},function(v){go(cur,with_('f',v),true,pat);});
function with_(k,v){var c={r:CR.r,l:CR.l,f:CR.f};c[k]=v;return c;}
function marks(c){CL.mark(c.r);LL.mark(c.l);FL.mark(c.f);if($('clfsel'))$('clfsel').hidden=c.l==='sd';}
// images are fetched and decoded first, then colours and images change in the same task (no half-switched page)
function commit(n,r,us,p){var st=document.documentElement.style,o=vars(n);Object.keys(o).forEach(function(k){st.setProperty(k,o[k]);});
imgs.forEach(function(i,j){i.dataset.f=fileOf(i,r);i.src=us[j];});cur=n;pat=p;CR=r;PL.mark(n);marks(r);if(pchk)pchk.checked=p;document.documentElement.dataset.palette=n;document.documentElement.dataset.patterns=p?'1':'';
document.documentElement.dataset.clock=r.r+'-'+mode(r);}
function go(n,r,save,p){if(!P.table[n])n=P.base;p=!!(p||P.pf);var k=key(n,p);r={r:r.r,l:r.l,f:r.f};if(!clk||!CL.has(r.r))r.r=D.r;if(!LL.has(r.l))r.l=D.l;if(!FL.has(r.f))r.f=D.f;
var my=++seq,need=k!==P.default||!same(r,D);btn.setAttribute('aria-busy','true');if(need)say(P.s.busy,true);
var prep=(!need||!served)?Promise.resolve({ok:true,v:''}):fetch('/api/palette/render',{method:'POST',headers:{'X-Usage-Token':TKN,'Content-Type':'application/json'},
body:JSON.stringify({name:n,patterns:p,clock:r.r,mode:mode(r)})}).then(function(x){return x.json();});
prep.then(function(j){if(my!==seq)return;if(!j||!j.ok)throw 0;var us=imgs.map(function(i){return src(i,k,r,j.v);});
return Promise.all(us.map(pre)).then(function(ok){if(my!==seq)return;if(ok.indexOf(false)>=0)throw 0;commit(n,r,us,p);say('');btn.removeAttribute('aria-busy');
if(save){try{localStorage.setItem(KEY,n);localStorage.setItem(PKEY,p?'1':'0');Object.keys(CK).forEach(function(k){localStorage.setItem(CK[k],r[k]);});}catch(e){}}});}).catch(function(){if(my!==seq)return;btn.removeAttribute('aria-busy');PL.mark(cur);marks(CR);if(pchk)pchk.checked=pat;
say(served?P.s.fail:P.s.file);});}
// saved choices: palette name (P.alias maps names saved by older pages) and the "Add patterns" switch
var n=P.base,p=pat,r={r:D.r,l:D.l,f:D.f};try{n=localStorage.getItem(KEY)||P.base;var sp=localStorage.getItem(PKEY);if(sp==='1'||sp==='0')p=sp==='1';
Object.keys(CK).forEach(function(k){r[k]=localStorage.getItem(CK[k])||D[k];});}catch(e){}
n=P.alias[n]||n;if(!P.table[n])n=P.base;p=!!(p||P.pf);document.documentElement.dataset.palette=P.base;document.documentElement.dataset.clock='wk-ot';
if(pchk){pchk.checked=pat;pchk.disabled=!!P.pf;pchk.onchange=function(){var on=pchk.checked;go(cur,CR,true,on);
if(served)fetch('/api/patterns',{method:'POST',headers:{'X-Usage-Token':TKN,'Content-Type':'application/json'},body:JSON.stringify({on:on})}).catch(function(){});};}
if(key(n,p)!==P.default||!same(r,D))go(n,r,false,p);
window.usagePalette=function(v,save){go(v,CR,save,pat);};window.usagePatterns=function(on,save){go(cur,CR,save,on);};window.usageClockRange=function(v,save){go(cur,with_('r',v),save,pat);};
window.usageClockLayout=function(v,save){go(cur,with_('l',v),save,pat);};window.usageClockFill=function(v,save){go(cur,with_('f',v),save,pat);};})();"""


# "Check for updates": asks the local server (POST /api/update/check); when GitHub has a newer version
# an "Update" button appears. The server also checks by itself about once a day (switch on the page,
# GET /api/update/state shows the last result). "Update" (POST /api/update/apply) starts the updater, then
# the page polls /api/status until a *new* server answers (different boot id) and reloads. Updating
# itself only ever happens after a click.
UPDATE_JS = """(function(){var $=id=>document.getElementById(id),S=JSON.parse($('updstr').textContent),
TK=(document.querySelector('meta[name="usage-token"]')||{}).content||'',cb=$('updbtn'),gb=$('updgo'),um=$('umsg'),ur=$('urow'),
ab=$('updauto'),al=$('updautol'),busy=false;
if(!/^https?:$/.test(location.protocol))return;
function f(t,o){return String(t).replace(/\\{(\\w+)\\}/g,function(_,k){return o[k]!=null?o[k]:'';});}
function say(t){um.textContent=t||'';ur.hidden=!t;}
function post(p,b){return fetch(p,{method:'POST',headers:{'X-Usage-Token':TK,'Content-Type':'application/json'},body:b?JSON.stringify(b):''}).then(r=>r.json());}
function newer(j){var l=j.latest||{};return f(S['new'],{h:l.hash||'',s:S.privacy?'':(l.subject||''),n:j.behind}).replace(/\\s+(\\(|（)/,' $1');}
function show(j,quiet){gb.hidden=true;var st=j.state||'git_error';
if(st==='behind'){say(newer(j));gb.hidden=false;return;}
if(quiet&&st!=='local_changes'&&st!=='diverged')return;
var m=S[st]||S.git_error;if((st==='local_changes'||st==='diverged')&&j.latest&&!S.privacy)m=newer(j)+' · '+m;
say(m+(j.detail&&!S.privacy?' ('+j.detail+')':''));}
function load(){if(busy)return;fetch('/api/update/state',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(j=>{if(!j||!j.ok||busy)return;
cb.hidden=false;al.hidden=false;ab.checked=!!j.auto;if(j.auto&&j.result)show(j.result,true);}).catch(()=>{});}
load();setInterval(load,1800000);
ab.onchange=function(){ab.disabled=true;post('/api/update/settings',{auto:ab.checked}).then(j=>{ab.disabled=false;
if(j.ok)ab.checked=!!j.auto;else{ab.checked=!ab.checked;say(j.error||S.git_error);}}).catch(()=>{ab.disabled=false;ab.checked=!ab.checked;});};
cb.onclick=function(){cb.disabled=true;gb.hidden=true;say(S.checking);
post('/api/update/check').then(j=>{cb.disabled=false;
if(j.error==='cooldown'){say(f(S.cooldown,{s:j.retry_in}));return;}
if(j.error==='busy'){say(S.checking);return;}show(j,false);}).catch(()=>{cb.disabled=false;say(S.git_error);});};
gb.onclick=function(){if(!confirm(S.confirm))return;gb.disabled=cb.disabled=true;
post('/api/update/apply').then(j=>{if(!j.ok){gb.disabled=cb.disabled=false;
say(j.error==='busy'?S.busy:f(S.failed,{e:S.privacy?'':(S[j.state]||j.error||'')}));return;}
busy=true;gb.hidden=cb.hidden=true;say(S.updating);var old=j.boot,t0=Date.now();
(function poll(){if(Date.now()-t0>600000){say(S.slow);busy=false;cb.hidden=false;cb.disabled=false;return;}
fetch('/api/status',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(s=>{if(s&&s.ok&&s.boot&&s.boot!==old){location.reload();return;}
setTimeout(poll,3000);}).catch(()=>{setTimeout(poll,3000);});})();})
.catch(()=>{gb.disabled=cb.disabled=false;say(S.failed.replace('{e}',''));});};})();"""

def _theme_file(path):
    if not path:
        return {}
    p = Path(path) if Path(path).is_absolute() else (HERE / path if (HERE / path).exists() else Path(path))
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


PAGE_SETTINGS = "page_settings.json"  # page switches saved here when the dashboard runs without a config file


def _read_page_setting(data_dir, key):
    try:
        d = json.loads((Path(data_dir) / PAGE_SETTINGS).read_text(encoding="utf-8"))
        return d.get(key) if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def save_page_setting(cfg_path, data_dir, key, value):
    """Save one page switch into usage_config.json (other keys kept as they are) when there is one, else into
    <data_dir>/page_settings.json. Returns the file written."""
    def write(p, obj, indent):
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
        os.replace(tmp, p)
    if cfg_path and Path(cfg_path).exists():
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError(f"cannot read {Path(cfg_path).name}")
        cfg[key] = value
        write(Path(cfg_path), cfg, 2)
        return Path(cfg_path)
    p = Path(data_dir) / PAGE_SETTINGS
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        pass
    d[key] = value
    write(p, d, 1)
    return p


def _theme_explicit(path):
    """Which colours a theme file pins (those do not change with the palette)."""
    d = _theme_file(path)
    return {k: bool(d.get(k + "_color")) for k in ("over", "under", "baseline", "notice", "accent")}


def _theme_explicit_value(path, key):
    return _theme_file(path).get(key + "_color") or None


def _on_colour(hexcol):
    """Readable text colour on a filled accent button."""
    from matplotlib.colors import to_rgb
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in to_rgb(hexcol)]
    return "#0B0F14" if 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] > 0.4 else "#FFFFFF"


PAL_STR = {"en": {"busy": "Drawing charts…", "fail": "Could not draw this palette", "file": "Open the page through the dashboard server to switch palettes"},
           "zh-TW": {"busy": "正在產生圖表…", "fail": "無法產生這組配色", "file": "請透過儀表板伺服器開啟頁面才能切換配色"},
           "zh-CN": {"busy": "正在生成图表…", "fail": "无法生成这组配色", "file": "请通过仪表板服务器打开页面才能切换配色"},
           "numeric": {"busy": "…", "fail": "×", "file": "×"}}


def write_html(ctx, sums, now, nowa, zl, gen=""):
    L, T = ctx.L, ctx.theme
    MS = MANUAL_STR["numeric"] if ctx.privacy else (MANUAL_STR["zh-TW"] if ctx.lang.startswith("zh") else MANUAL_STR["en"])
    LS = LEDGER_STR["numeric"] if ctx.privacy else (LEDGER_STR[usage_dates.lang_key(ctx.lang)] if ctx.lang.startswith("zh") else LEDGER_STR["en"])
    US = UPDATE_STR["numeric"] if ctx.privacy else (UPDATE_STR["zh-TW"] if ctx.lang.startswith("zh") else UPDATE_STR["en"])
    try:
        ledger = json.loads(Path(ctx.ledger_path).read_text(encoding="utf-8"))
        ledger.pop("_newAlerts", None)
    except Exception:
        ledger = None
    ledjson = json.dumps(ledger, ensure_ascii=False).replace("<", "\\u003c")
    lsjson = json.dumps(dict(LS, off=ctx.display_offset, otag=usage_offset.tag(ctx.display_offset)), ensure_ascii=False).replace("<", "\\u003c")
    usjson = json.dumps(US, ensure_ascii=False).replace("<", "\\u003c")
    b64 = lambda p: base64.b64encode(p.read_bytes()).decode() if p.exists() else ""
    tabs, panes = [], []
    wk = ctx.hist_dir / "week-current.png"
    tabs.append(f"<button data-k='wk' class='on'>{html.escape(L['rwk'])}</button>")
    panes.append(f"<section id='p-wk' class='on'>" + (f"<img alt='' data-f='week-current.png' src='data:image/png;base64,{b64(wk)}'>"
                 if wk.exists() else f"<div class='warn'>{html.escape(L['week_empty'])}</div>") + "</section>")
    for i, S in enumerate(sums):
        rows = "".join(f"<tr><td>{w['start']:%Y-%m-%d}</td><td>{w['last']}/{ctx.period_days}</td><td class='{'over' if w['over'] else 'under'}'>"
                       f"{usage_palette.UP if w['over'] else usage_palette.DOWN} {w['peak']:.0f}%</td><td>{w['pace_ref']:.0f}%</td><td>{L['yes'] if w['hit100'] else '—'}</td></tr>" for w in reversed(S["weeks"])) \
            or f"<tr><td colspan=5>{html.escape(L['empty'])}</td></tr>"
        sp = "".join(f"<tr><td>{k}</td><td>${v:.2f}</td></tr>" for k, v in S["spend"].items()) or f"<tr><td colspan=2>{html.escape(L['no_spend'])}</td></tr>"
        warn = f"<div class='warn'>{html.escape(L['sparse'].format(n=S['n'], m=S['minw']))}</div>" if S["sparse"] else ""
        tabs.append(f"<button data-k='{S['key']}' >{html.escape(S['label'])}</button>")
        panes.append(f"<section id='p-{S['key']}'>{warn}<img alt='' data-f='history-{S['key']}.png' src='data:image/png;base64,{b64(ctx.hist_dir / ('history-' + S['key'] + '.png'))}'>"
                     f"<div class='tables'><table><caption>{html.escape(L['weekly_detail'])}</caption><tr><th>{L['week']}</th><th>{L['days']}</th><th>{L['peak']}</th>"
                     f"<th>{L['pace']}</th><th>{L['at100']}</th></tr>{rows}</table><table><caption>{html.escape(L['spend_title'])}</caption>"
                     f"<tr><th>{L['month']}</th><th>{L['amount']}</th></tr>{sp}</table></div></section>")
    imgs = ""
    if (ctx.hist_dir / "clock-1d.png").exists() and (ctx.hist_dir / clock_file("wk")).exists():
        def menu(mid, label, opts, cur):
            rows = "".join(f"<li role='option' id='{mid}-{k}' data-v='{k}' aria-selected='{'true' if k == cur else 'false'}'><span>{html.escape(v)}</span></li>"
                           for k, v in opts)
            return (f"<span class='pal psel' id='{mid}sel'><span id='{mid}l'>{html.escape(label)}</span>"
                    f"<button type='button' class='pbtn' id='{mid}btn' aria-haspopup='listbox' aria-expanded='false' aria-labelledby='{mid}l {mid}cur'>"
                    f"<span id='{mid}cur'><span>{html.escape(dict(opts)[cur])}</span></span></button>"
                    f"<ul class='plist' id='{mid}list' role='listbox' tabindex='-1' aria-labelledby='{mid}l' hidden>{rows}</ul></span>")
        bar = (menu("clk", L["clock_rl"], [(k, L["clock_ro"][k]) for k in CLOCK_RANGES], "wk")
               + menu("clo", L["clock_ll"], [(k, L["clock_lo"][k]) for k in ("ov", "sd")], "ov")
               + menu("clf", L["clock_fl"], [(k, L["clock_fo"][k]) for k in ("t", "s")], "t"))
        imgs = (f"<figure><div class='clkbar'></div><img alt='' data-f='clock-1d.png' src='data:image/png;base64,{b64(ctx.hist_dir / 'clock-1d.png')}'></figure>"
                f"<figure><div class='clkbar'>{bar}</div>"
                f"<img alt='' id='clkimg' data-f='{clock_file('wk')}' src='data:image/png;base64,{b64(ctx.hist_dir / clock_file('wk'))}'></figure>")
    tabs.append(f"<button data-k='clock'>{html.escape(L['clock_tab'])}</button>")
    panes.append(f"<section id='p-clock'><div class='sub'>{html.escape(L['clock_sub'].format(z=zl))}</div>"
                 f"<div class='clocks'>{imgs or '<div class=warn>' + html.escape(L['no_clock']) + '</div>'}</div></section>")
    ckdata = usage_checkins_ui.to_script_json(checkin_page_data(ctx, sums))
    rjs = json.dumps({k: L[k] for k in ("loading", "refresh", "conn_err", "refreshing", "ph_read", "ph_draw")}, ensure_ascii=False).replace("<", "\\u003c")
    tabs.append(f"<button data-k='checkin'>{html.escape(L['checkin_tab'])}</button>")
    panes.append(usage_checkins_ui.pane_html(ctx.ck_strings(), html.escape(L["ck_hint"])))
    title = "" if ctx.privacy else L["title"]
    bg, tx, ac, card, bd, dim = T["bg_color"], T["text_color"], T["accent_color"], T["card"], T["border"], T["dim"]
    # palette switcher: config default, overridden per browser (localStorage 'usage.palette')
    fixed = {k: bool(v) for k, v in _theme_explicit(ctx.theme_path).items()}
    plang = "numeric" if ctx.privacy else ("zh-TW" if ctx.lang == "zh-TW" else ("zh-CN" if ctx.lang.startswith("zh") else "en"))
    paldata = json.dumps({"default": ctx.pal_key, "base": ctx.palette, "pat": ctx.patterns, "osfx": ctx.key_suffix, "light": T["is_light"],
                          "table": usage_palette.js_table(), "alias": {"colorblind": "orange-deepblue"}, "fixed": fixed,
                          "bg": bg, "accent": ac, "under": T["under_color"], "gen": gen, "pf": bool(_theme_file(ctx.theme_path).get("patterns")), "s": PAL_STR[plang]},
                         ensure_ascii=False).replace("<", "\\u003c")
    variant = "light" if T["is_light"] else "dark"

    def pal_row(i, n):
        c = usage_palette.PALETTES[n][variant]
        name = (str(i + 1) + (" " + usage_palette.STAR if usage_palette.cvd_safe(n) else "")) if plang == "numeric" else usage_palette.label(n, plang)
        return (f"<li role='option' id='po-{n}' data-v='{n}' aria-selected='{'true' if n == ctx.palette else 'false'}'>"
                f"<i class='dot' style='background:{c['over']}'></i><i class='dot' style='background:{c['under']}'></i>"
                f"<span>{html.escape(name)}</span></li>")
    palrows = [pal_row(i, n) for i, n in enumerate(usage_palette.names())]
    palcur = palrows[usage_palette.names().index(ctx.palette)].split(">", 1)[1].rsplit("</li>", 1)[0]
    pal_label = {"numeric": "◐", "zh-TW": "配色", "zh-CN": "配色"}.get(plang, "Colours")
    star_note = "" if plang == "numeric" else usage_palette.STAR_NOTE[plang]
    pat_label = {"numeric": "▨", "zh-TW": "加上紋路", "zh-CN": "加上纹路"}.get(plang, "Add patterns")
    off_label = {"numeric": "±", "zh-TW": "顯示校正", "zh-CN": "显示校正"}.get(plang, "Display offset")
    off_opts = "".join(f"<option value='{o}'{' selected' if o == ctx.display_offset else ''}>{html.escape(usage_offset.option_text(o, plang))}</option>"
                       for o in range(usage_offset.MIN, usage_offset.MAX + 1))
    PK = PICK_STR["numeric" if ctx.privacy else (ctx.lang if ctx.lang in PICK_STR else plang)]
    lang_opts = "".join(f"<option value='{k}'{' selected' if k == ctx.lang_setting else ''}>{html.escape(PK['auto'] if k == 'auto' else LANG_NAMES[k])}</option>"
                        for k in PAGE_LANGS)
    zones = list(PAGE_ZONES) + ([ctx.tz_setting] if ctx.tz_setting not in PAGE_ZONES + ("auto",) else [])
    tz_opts = (f"<option value='auto'{' selected' if ctx.tz_setting == 'auto' else ''}>{html.escape(PK['auto'])} ({tz_option_label('local')})</option>"
               + "".join(f"<option value='{html.escape(z)}'{' selected' if z == ctx.tz_setting else ''}>{html.escape(z)} ({tz_option_label(z) if valid_zone(z) else '?'})</option>"
                         for z in zones))
    lock_attr = lambda on: f" data-locked='1' title='{html.escape(PK['locked'])}'" if on else ""
    pick_html = (f"<label class=\"pal\" id=\"langl\">{html.escape(PK['lang'])} <select id=\"langsel\" class=\"offsel\" data-api=\"/api/lang\" data-k=\"usage.lang\" data-f=\"lang\"{lock_attr(ctx.lang_locked)}>{lang_opts}</select></label>"
                 f"<label class=\"pal\" id=\"tzl\">{html.escape(PK['tz'])} <select id=\"tzsel\" class=\"offsel\" data-api=\"/api/tz\" data-k=\"usage.tz\" data-f=\"tz\"{lock_attr(ctx.tz_locked)}>{tz_opts}</select></label>"
                 f"<script>{PICK_JS}</script>")
    onac, hl = _on_colour(ac), _blend(bg, tx, 0.14)
    doc = f"""<!doctype html><html lang="{ {'zh-TW': 'zh-Hant', 'zh-CN': 'zh-Hans', 'ja': 'ja', 'ko': 'ko'}.get(ctx.lang, 'en') }" data-patterns="{'1' if T.get('patterns') else ''}"><head><meta charset="utf-8"><title>{html.escape(title or '↻')}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{{margin:0;background:{bg};color:{tx};font-family:"Noto Sans CJK TC","Noto Sans TC","PingFang TC","Microsoft JhengHei","Segoe UI",sans-serif}}
.wrap{{max-width:1060px;margin:0 auto;padding:20px}} .top{{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:1px}} h1{{margin:4px 0 8px;font-size:26px}}
.bar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}} .upd{{color:{dim};font-size:13px;display:flex;align-items:center;gap:10px}}
nav{{display:inline-flex;border:1px solid var(--bd);border-radius:10px;padding:4px;background:{card};margin:6px 0 14px}}
nav>button{{background:none;border:1px solid transparent;color:{dim};font:inherit;font-weight:700;font-size:15px;padding:6px 18px;border-radius:7px;cursor:pointer}}
nav>button.on{{color:{tx};border-color:var(--accent)}} section{{display:none}} section.on{{display:block}} img{{width:100%;border:1px solid var(--bd);border-radius:10px}}
:root{{--over:{T['over_color']};--under:{T['under_color']};--baseline:{T['baseline_color']};--notice:{T['notice_color']};--accent:{ac};--bd:{bd};--on-accent:{onac};--card:{card}}}
a{{color:var(--accent)}} :focus-visible{{outline:2px solid var(--accent);outline-offset:2px}} input[type=checkbox],input[type=radio]{{accent-color:var(--accent)}}
nav>button:hover{{color:{tx};border-color:var(--bd)}} .btn:hover,#ckbtn:hover,#ckr button:hover{{border-color:var(--accent)}}
.warn{{border:1px solid var(--notice);color:var(--notice);border-radius:8px;padding:8px 12px;margin-bottom:10px;font-weight:700}}
.tables{{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-top:12px}} .clocks{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} figure{{margin:0}}
.clkbar{{display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:12px;min-height:36px;margin-bottom:6px}} .clkbar .plist{{min-width:8em}} .psel[hidden]{{display:none}}
table{{border-collapse:collapse;width:100%;background:{card};border:1px solid var(--bd);font-size:13px}} caption{{text-align:left;font-weight:700;padding:6px 0}}
th,td{{padding:5px 10px;border-bottom:1px solid var(--bd);text-align:left}} th{{color:{dim}}} .over{{color:var(--over);font-weight:700}} .under{{color:var(--under);font-weight:700}}
.sub{{color:{dim};font-size:13px;margin:6px 0}} .ckform{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}}
input,select{{background:{card};color:{tx};border:1px solid var(--bd);border-radius:6px;padding:6px 10px;font:inherit}}
.btn,#ckbtn,#ckr button{{background:{card};border:1px solid var(--bd);color:{tx};font:inherit;font-weight:700;padding:5px 14px;border-radius:7px;cursor:pointer}}
#ckr button.on,.btn{{border-color:var(--accent)}} .btn:disabled{{opacity:.6;cursor:wait}}
.btn.primary{{background:var(--accent);color:var(--on-accent);border-color:var(--accent);padding:6px 18px}} .btn.primary:hover{{filter:brightness(1.1)}}
.btn.updgo{{background:#A5F3FC;color:#06303A;border:2px solid #22D3EE;padding:6px 18px}} .btn.updgo:hover{{filter:brightness(1.06)}}
.urow{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:2px 0 10px;font-size:13px}} #umsg{{font-weight:700;color:{dim}}} #ckmsg,#rmsg,#mmsg{{color:var(--notice);font-weight:700}} [hidden]{{display:none!important}}
.spin{{display:inline-block;width:12px;height:12px;border:2px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:6px}}
@keyframes sp{{to{{transform:rotate(360deg)}}}}
.info{{background:{card};border:1px solid var(--bd);border-left:3px solid var(--accent);border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:14px}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}} .badge{{border:1px solid var(--over);color:var(--over);border-radius:999px;padding:3px 12px;font-weight:700;font-size:13px}}
.badge.note{{border-color:var(--notice);color:var(--notice)}}
html[data-patterns="1"] .badge:not(.note){{background-image:repeating-linear-gradient(135deg,transparent 0 5px,color-mix(in srgb,var(--over) 22%,transparent) 5px 8px)}}
html[data-patterns="1"] .badge:not(.note)::before{{content:"▲ "}} .pal{{color:{dim};font-size:13px}}
.psel{{position:relative;display:inline-flex;align-items:center;gap:6px}} .pbtn{{background:{card};color:{tx};border:1px solid var(--bd);border-radius:6px;padding:6px 10px;font:inherit;cursor:pointer;display:inline-flex;align-items:center;gap:6px}}
.pbtn:hover{{border-color:var(--accent)}} .pbtn::after{{content:'▾';color:{dim};margin-left:2px}} .pbtn>span,.plist li>span{{white-space:nowrap}}
.plist{{position:absolute;right:0;top:calc(100% + 4px);z-index:20;margin:0;padding:4px;list-style:none;background:{card};border:1px solid var(--bd);border-radius:8px;min-width:100%;max-height:min(80vh,560px);overflow:auto;box-shadow:0 8px 24px rgba(0,0,0,.45)}}
.plist li{{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:5px;cursor:pointer;color:{tx};font-size:14px}} .plist li.act{{background:{hl}}}
.plist li[aria-selected=true]{{font-weight:700}} .plist li[aria-selected=true]::after{{content:'✓';margin-left:auto;padding-left:14px;color:var(--accent)}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;flex:none;box-shadow:0 0 0 1px rgba(128,128,128,.55)}} #palmsg{{color:{dim};font-size:12px}}
#pallist{{min-width:max(100%,17em);max-height:min(88vh,760px)}}
.plist li.pnote{{cursor:default;color:{dim};font-size:12px;line-height:1.45;border-top:1px solid var(--bd);border-radius:0;margin-top:4px;padding:7px 8px 4px;white-space:normal;position:sticky;bottom:-4px;background:{card}}}
.ptog{{display:inline-flex;align-items:center;gap:5px;cursor:pointer}} .ptog input{{margin:0}} .offsel{{padding:3px 6px}} .otag{{color:{dim};font-size:.85em;font-weight:400}}
{usage_checkins_ui.CSS}
</style></head><body><div class="wrap"><div class="top">{html.escape(L['eyebrow'])}</div><h1>{html.escape(title)}</h1>
<div class="bar"><nav>{''.join(tabs)}</nav><div class="upd"><span id="last">{html.escape(L['last'].format(t=f'{now:%H:%M}', z=zl))}</span>
<span class="pal psel" id="psel"><span id="pall">{html.escape(pal_label)}</span><button type="button" class="pbtn" id="palbtn" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="pall palcur"><span id="palcur">{palcur}</span></button>
<ul class="plist" id="pallist" role="listbox" tabindex="-1" aria-labelledby="pall" hidden>{''.join(palrows)}{f"<li role='none' class='pnote' id='palnote'>{html.escape(star_note)}</li>" if star_note else ""}</ul><span id="palmsg" role="status"></span></span>
<label class="pal ptog" id="patl"><input type="checkbox" id="patchk"{" checked" if ctx.patterns else ""}> {html.escape(pat_label)}</label>
<label class="pal" id="offl">{html.escape(off_label)} <select id="offsel" class="offsel">{off_opts}</select></label><script>{OFFSET_JS}</script>{pick_html}
<button class="btn" id="manualbtn" hidden>{html.escape(MS['btn'])}</button><button class="btn" id="notifybtn" hidden>{html.escape(LS['notify'])}</button><button class="btn primary" id="refresh" hidden>{html.escape(L['refresh'])}</button><span id="rmsg" role="status" aria-live="polite"></span>
<button class="btn" id="updbtn" hidden>{html.escape(US['btn'])}</button><label class="pal" id="updautol" hidden><input type="checkbox" id="updauto"> {html.escape(US['auto'])}</label></div></div>
<div id="urow" class="urow" hidden><span id="umsg"></span><button class="btn updgo" id="updgo" hidden>{html.escape(US['go'])}</button></div>
<form id="manualform" class="ckform" hidden><label>{html.escape(MS['pct'])} <input id="mpct" type="number" min="0" max="200" step="0.1" required style="width:6em"> <span class="sub">{html.escape(MS.get('pct_note', ''))}</span></label>
<label>{html.escape(MS['reset'])} <input id="mreset" maxlength="64" placeholder="{html.escape(MS['reset_ph'])}" style="width:22em"></label>
<button class="btn" type="submit">{html.escape(MS['save'])}</button><span class="sub">{html.escape(MS['hint'])}</span><span id="mmsg"></span></form>
<div id="ledger"></div>{''.join(panes)}</div>
<script type="application/json" id="ckdata">{ckdata}</script><script>{usage_checkins_ui.JS}</script>
<script type="application/json" id="ledgerdata">{ledjson}</script><script type="application/json" id="ledgerstr">{lsjson}</script><script>{LEDGER_JS}</script>
<script type="application/json" id="paldata">{paldata}</script><script>{PALETTE_JS}</script>
<script type="application/json" id="updstr">{usjson}</script><script>{UPDATE_JS}</script>
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
// refresh: live elapsed seconds on the button, the server's phase (reading usage / drawing charts) next to it
rb.onclick=function(){{if(rb.disabled)return;var t0=Date.now(),tick,poll,done=false;rb.disabled=true;rb.setAttribute('aria-busy','true');rm.textContent='';
function show(){{rb.innerHTML='<span class="spin"></span>';rb.appendChild(document.createTextNode(T.refreshing.replace('{{s}}',Math.floor((Date.now()-t0)/1000))));}}
function stop(msg){{done=true;clearInterval(tick);clearTimeout(poll);rb.disabled=false;rb.removeAttribute('aria-busy');rb.textContent=T.refresh;rm.textContent=msg||'';}}
function ph(){{fetch('/api/status',{{cache:'no-store'}}).then(r=>r.ok?r.json():null).then(j=>{{if(done||!j)return;var t=T['ph_'+j.phase];if(t!=null)rm.textContent=t;}})
.catch(()=>{{}}).then(()=>{{if(!done)poll=setTimeout(ph,1500);}});}}
show();tick=setInterval(show,1000);poll=setTimeout(ph,700);
fetch('/api/refresh',{{method:'POST',headers:{{'X-Usage-Token':TK}}}}).then(r=>r.json()).then(j=>{{if(j.ok){{done=true;clearTimeout(poll);location.reload();return;}}
var e=String(j.error||'×');stop((e.length>90?e.slice(0,90)+'…':e)+(j.retry_in?' ('+j.retry_in+'s)':''));}}).catch(()=>{{stop(T.conn_err);}});}};
}})();
var h=location.hash.slice(1),t=document.querySelector("nav>button[data-k='"+h+"']");if(t)t.click();
</script></body></html>"""
    tmp = ctx.hist_dir / "index.html.tmp"; tmp.write_text(doc, encoding="utf-8"); os.replace(tmp, ctx.hist_dir / "index.html")


# ------------------------------------------------------------------ sampling
def manual_mode(ctx):
    """pct_source "manual" in the config: never start Grok Build; values come from --pct / the page."""
    return str(ctx.cfg.get("pct_source") or "auto").strip().lower() == "manual"


def save_manual_reset(ctx, reset_at, source="manual"):
    """Cache a reset (typed in or read from Grok Build) to <data_dir>/state.json, so week boundaries work."""
    payload = {"reset_at": reset_at.isoformat(timespec="minutes"), "period_days": ctx.period_days,
               "updated_at": datetime.now(ctx.tz).isoformat(timespec="minutes"),
               "source": ("m" if source == "manual" else "a") if ctx.privacy else source}
    p = ctx.data_dir / "state.json"
    tmp = p.with_name(f"state.json.{os.getpid()}.tmp")
    with usage_lock.write_lock(ctx.data_dir):
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)


def _period_start(ctx, ts, reset_at=None):
    """Start of the usage week containing `ts` (aware). Uses the reading's reset time, else the saved/configured
    reset reference; with no reset known, continues the latest week file or starts a week at that day's 00:00."""
    pd = timedelta(days=ctx.period_days)
    ref = reset_at or reset_ref(ctx)
    if ref is not None:
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=ctx.data_tz)
        return ref + ((ts - ref) // pd) * pd
    for f in sorted(ctx.data_dir.glob("week-*.csv"), reverse=True):
        try:
            d0 = datetime.strptime(f.stem[5:], "%Y-%m-%d").replace(tzinfo=ctx.data_tz)
        except ValueError:
            continue
        if d0 <= ts < d0 + pd:
            return d0
        break
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def upsert_week_row(ctx, ts, pct, reset_at=None):
    """Write a successful reading into <data_dir>/week-<start>.csv (one row per day, latest reading wins),
    the same file format usage_record.py writes, so manual and sampler readings reach the history charts.
    Call with usage_lock.write_lock held."""
    ws = _period_start(ctx, ts, reset_at)
    day = max(1, min(ctx.period_days, int((ts - ws).total_seconds() // 86400) + 1))
    path = ctx.data_dir / f"week-{ws.astimezone(ctx.data_tz):%Y-%m-%d}.csv"
    rows = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fp:
            rows = [r for r in csv.DictReader(fp) if r.get("day")]
    v = float(pct)
    new = {"day": str(day), "date": f"{ts.astimezone(ctx.data_tz):%Y-%m-%d}", "pct": str(int(v)) if v.is_integer() else f"{v:g}",
           "note": f"{ts.astimezone(ctx.data_tz):%H:%M}"}
    rows = sorted([r for r in rows if int(r["day"]) != day] + [new], key=lambda r: int(r["day"]))
    tmp = path.with_suffix(".csv.tmp")
    tmp.write_text("day,date,pct,note\n" + "".join(f"{r['day']},{r['date']},{r['pct']},{r['note']}\n" for r in rows),
                   encoding="utf-8")
    os.replace(tmp, path)


def take_sample(ctx, pct=None, source="sampler", fail_reason=None, reset_at=None, phase=None, draw_lock=None):
    """One reading -> samples.csv + shared ledger (usage_ledger.py).
    `pct` given = a manual reading (no Grok Build launch); `reset_at` (aware datetime) optionally
    goes with it and is cached to state.json.
    Shares <data_dir>/.read.lock with the sampler loop and manual refresh.
    A failed read is recorded as 'unavailable' (never 0%, never a guessed reset) and raises RuntimeError.
    `phase(name)` is called with "read" and "draw" (the page shows it during a refresh); the result has
    `timings` = seconds spent reading and drawing (also printed to the log)."""
    say = phase or (lambda _p: None)
    t_start = time.monotonic()
    say("read")
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
                if isinstance(reset_at, datetime):
                    try:
                        save_manual_reset(ctx, reset_at, "grok")
                    except Exception as exc:
                        print(f"Warning: reset cache write failed: {exc}", file=sys.stderr)
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
            if pct is not None:
                try:
                    upsert_week_row(ctx, ts, pct, reset_at)
                except Exception as exc:
                    print(f"Warning: week file write failed: {exc}", file=sys.stderr)
            try:
                import usage_ledger
                led = usage_ledger.record_hud(ctx.data_dir, ctx.cfg, pct, reset_at, source=source, error=err, tz=ctx.tz,
                                              lang=ctx.lang, exclude_last=True, now=ts)
            except Exception as exc:
                print(f"Warning: ledger write failed: {exc}", file=sys.stderr)
    finally:
        lk.release()
    t_read = time.monotonic()
    say("draw")
    with (draw_lock or threading.RLock()):
        for fn in (render_clock, render_history):  # failure-safe re-render
            try:
                fn(ctx)
            except Exception as exc:
                print(f"Warning: {fn.__name__} failed: {exc}", file=sys.stderr)
    timings = {"read": round(t_read - t_start, 2), "draw": round(time.monotonic() - t_read, 2)}
    print(f"sample ({source}): read {timings['read']:.1f} s, draw {timings['draw']:.1f} s", file=sys.stderr)
    if pct is None:
        raise RuntimeError("source read failed" if ctx.privacy else f"unavailable: {err}")
    return {"ok": True, "timestamp": ts.isoformat(timespec="seconds"), "pct": float(pct),
            "reset_at": reset_at.isoformat(timespec="minutes") if reset_at else None,
            "new_alerts": (led or {}).get("_newAlerts", []), "timings": timings}


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
    busy = threading.Lock(); state = {"last_refresh": 0.0, "ck": {}, "upd_at": 0.0, "upd": None, "updating": False, "phase": None}
    upd_lock = threading.Lock()
    boot = f"{os.getpid()}-{time.time():.0f}"  # changes on every start: the page reloads once a new server answers
    port_box = {"port": 0}
    token = local_guard.new_token()
    token_file = ctx.data_dir / "history_server.token"

    draw_lock = threading.RLock()  # one drawing pass at a time (refresh cycle or an on-demand palette)

    def regen():
        with draw_lock:
            for fn in (render_clock, render_history):
                try:
                    fn(ctx)
                except Exception as exc:
                    print(f"Warning: {exc}", file=sys.stderr)

    def palette_render(d):
        """POST /api/palette/render {"name": ...}: draw that palette's images (cached until the next refresh)."""
        name = str(d.get("name") or "")
        clock = d.get("clock")
        if name not in usage_palette.names():
            return 400, {"ok": False, "error": "unknown palette"}
        pat = d.get("patterns", False)
        if not isinstance(pat, bool):
            return 400, {"ok": False, "error": "patterns must be true or false"}
        mode = d.get("mode")
        if clock is not None and clock not in CLOCK_RANGES:
            return 400, {"ok": False, "error": "unknown clock range"}
        if mode is not None and mode not in CLOCK_MODES:
            return 400, {"ok": False, "error": "unknown clock layout"}
        with draw_lock:
            try:
                return 200, render_palette(ctx, name, clock, mode, pat)
            except Exception as exc:
                print(f"Warning: palette '{name}' render failed: {exc}", file=sys.stderr)
                return 500, {"ok": False, "error": "render failed"}
    regen()  # always rebuild on start, so an updated version's page shows right away

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

    WRITE_PATHS = ("/api/checkin", "/api/refresh", "/api/manual", "/api/update/check", "/api/update/apply", "/api/update/settings",
                   "/api/palette/render", "/api/patterns", "/api/display-offset", "/api/lang", "/api/tz")

    def pick_setting(key, d):
        """POST /api/lang {"lang": "auto"|"zh-TW"|"zh-CN"|"en"|"ja"|"ko"|"numeric"} or /api/tz {"tz": "auto"|<IANA zone>}:
        saved as "lang" / "tz" in usage_config.json (or <data_dir>/page_settings.json); charts and page are redrawn
        before this returns. A --lang / --tz flag given at start still wins ("locked": true)."""
        v = d.get(key)
        if key == "lang" and v not in PAGE_LANGS:
            return 400, {"ok": False, "error": "unknown language"}
        if key == "tz" and not (v == "auto" or (v in PAGE_ZONES or valid_zone(v))):
            return 400, {"ok": False, "error": "unknown time zone"}
        try:
            with usage_lock.write_lock(ctx.data_dir):
                save_page_setting(ctx.cfg_path, ctx.data_dir, key, v)
            with draw_lock:
                ctx.reload()
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"could not save the setting: {exc}"}
        regen()
        return 200, {"ok": True, key: v, "locked": ctx.lang_locked if key == "lang" else ctx.tz_locked}

    def offset_setting(d):
        """POST /api/display-offset {"offset": -2..2}: saved as "display_offset" in usage_config.json (or
        <data_dir>/page_settings.json); the charts and page are redrawn with it before this returns."""
        v = d.get("offset")
        if isinstance(v, bool) or not isinstance(v, int) or not usage_offset.MIN <= v <= usage_offset.MAX:
            return 400, {"ok": False, "error": "offset must be an integer from -2 to 2"}
        try:
            with usage_lock.write_lock(ctx.data_dir):
                where = save_page_setting(ctx.cfg_path, ctx.data_dir, "display_offset", v)
            if ctx.cfg_path and Path(where) == Path(ctx.cfg_path):
                ctx.cfg = dict(ctx.cfg, display_offset=v)
            with draw_lock:
                ctx.display_offset = v
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"could not save the setting: {exc}"}
        regen()
        return 200, {"ok": True, "display_offset": v}

    def patterns_setting(d):
        """POST /api/patterns {"on": true|false}: the page's "Add patterns" switch, saved as "patterns" in
        usage_config.json (or <data_dir>/page_settings.json); the next refresh draws the default charts with it."""
        if not isinstance(d.get("on"), bool):
            return 400, {"ok": False, "error": "on must be true or false"}
        try:
            with usage_lock.write_lock(ctx.data_dir):
                where = save_page_setting(ctx.cfg_path, ctx.data_dir, "patterns", d["on"])
            if ctx.cfg_path and Path(where) == Path(ctx.cfg_path):
                ctx.cfg = dict(ctx.cfg, patterns=d["on"])
            ctx.patterns = d["on"] or bool(_theme_file(ctx.theme_path).get("patterns"))
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"could not save the setting: {exc}"}
        return 200, {"ok": True, "patterns": ctx.patterns}

    def update_public(r):
        r = dict(r)
        if ctx.privacy:  # commit subjects and git messages may name services: hash and counts only
            r["detail"] = ""
            if r.get("latest"):
                r["latest"] = {"hash": r["latest"].get("hash"), "subject": ""}
        return r

    def update_check():
        """POST /api/update/check -> self_update.check() (git fetch origin main, then compare with HEAD)."""
        import self_update
        if not upd_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "busy"}
        try:
            wait = UPDATE_CHECK_COOLDOWN - (time.time() - state["upd_at"])
            if wait > 0:
                if state["upd"] is not None:
                    return 200, dict(state["upd"], cached=True)
                return 429, {"ok": False, "error": "cooldown", "retry_in": int(wait) + 1}
            state["upd_at"] = time.time()
            state["upd"] = update_public(self_update.check(HERE))
            self_update.save_last(ctx.data_dir, state["upd"], state["upd_at"])
            return 200, state["upd"]
        finally:
            upd_lock.release()

    def update_auto_loop():
        """Automatic check (unless "update_check": "off"): shortly after start and then about once a day.
        A restart within the day only re-compares with what was fetched last time (no network)."""
        import self_update
        last, _ = self_update.load_last(ctx.data_dir)
        state["upd_at"] = min(last, time.time())
        time.sleep(UPDATE_AUTO_FIRST_DELAY)
        first = True
        while True:
            try:
                if self_update.auto_enabled(ctx.cfg, ctx.data_dir) and not state["updating"]:
                    due = self_update.auto_due(state["upd_at"])
                    if (due or first) and upd_lock.acquire(blocking=False):
                        try:
                            r = update_public(self_update.check(HERE, fetch=due))
                            if due:
                                state["upd_at"] = time.time()
                                self_update.save_last(ctx.data_dir, r, state["upd_at"])
                            state["upd"] = r
                        finally:
                            upd_lock.release()
                    first = False
            except Exception as exc:
                print(f"Warning: update check: {exc}", file=sys.stderr)
            time.sleep(600)

    def update_info():
        """GET /api/update/state: the switch and the last result (read-only, never runs git)."""
        import self_update
        return {"ok": True, "auto": self_update.auto_enabled(ctx.cfg, ctx.data_dir), "result": state["upd"],
                "checked_at": datetime.fromtimestamp(state["upd_at"], ctx.tz).isoformat(timespec="minutes") if state["upd_at"] else None}

    def update_settings(d):
        """POST /api/update/settings {"auto": true|false} -> saved, survives restarts."""
        import self_update
        if not isinstance(d.get("auto"), bool):
            return 400, {"ok": False, "error": "auto must be true or false"}
        try:
            with usage_lock.write_lock(ctx.data_dir):
                where = self_update.set_auto(ctx.cfg_path, ctx.data_dir, d["auto"])
            if ctx.cfg_path and Path(where) == Path(ctx.cfg_path):
                ctx.cfg = dict(ctx.cfg, **{self_update.SETTING_KEY: "auto" if d["auto"] else "off"})
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"could not save the setting: {exc}"}
        return 200, {"ok": True, "auto": self_update.auto_enabled(ctx.cfg, ctx.data_dir), "file": "" if ctx.privacy else where.name}

    def update_apply(client_ip):
        """POST /api/update/apply -> start the detached updater (fast-forward only), loopback clients only."""
        import self_update
        if ctx.host not in ("127.0.0.1", "localhost", "::1") or client_ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return 403, {"ok": False, "error": "updates can only be started from this computer"}
        if not upd_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "busy"}
        try:
            if state["updating"] or self_update.updater_busy(ctx.data_dir):
                return 409, {"ok": False, "error": "busy"}
            r = self_update.check(HERE, fetch=False)  # uses what the last check fetched
            if not r.get("can_update"):
                return 409, dict(update_public(r), ok=False, error=r.get("state"))
            cfg = str(Path(ctx.cfg_path).resolve()) if ctx.cfg_path else None
            if not ctx.port:  # random port: ask the next start to reuse this one (see the end of serve)
                (ctx.data_dir / "update.port").write_text(str(port_box["port"]), encoding="utf-8")
            if not self_update.spawn(HERE, ctx.data_dir, cfg, wait_pid=os.getpid()):
                return 500, {"ok": False, "error": "could not start the updater"}
            state["updating"] = True
            return 200, {"ok": True, "boot": boot, "behind": r.get("behind"), "log": "update.log"}
        except Exception as exc:
            return 500, {"ok": False, "error": "updater failed" if ctx.privacy else f"could not start the updater: {exc}"}
        finally:
            upd_lock.release()

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
            return 200, take_sample(ctx, pct, source="manual", reset_at=reset_at, draw_lock=draw_lock)
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"manual entry failed: {exc}"}
        finally:
            busy.release()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct="application/json; charset=utf-8", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
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
            if u.path.endswith(".png") and (len(parts) == 1 or (len(parts) == 3 and parts[0] == "pal" and is_dir_key(parts[1]))) \
                    and all(x and x not in (".", "..") for x in parts):
                p = ctx.hist_dir.joinpath(*parts)
                # versioned palette images (?v=<render id>) may be cached: the page preloads them before swapping
                cache = {"Cache-Control": "private, max-age=86400"} if len(parts) == 3 and g("v") else None
                return self._send(200, p.read_bytes(), "image/png", extra=cache) if p.is_file() else self._send(404, {"ok": False, "error": "not found"})
            if u.path in ("/api/ledger", "/ledger.json"):
                lp = Path(ctx.ledger_path)
                return self._send(200, lp.read_bytes()) if lp.exists() else self._send(404, {"ok": False, "error": "no ledger yet; run: usage_history.py sample"})
            if u.path == "/api/status":
                return self._send(200, {"ok": True, "last_update": last_update(), "busy": busy.locked(), "timezone": ctx.zlabel(),
                                        "sample_interval_minutes": ctx.interval, "pct_source": "manual" if manual_mode(ctx) else "auto",
                                        "boot": boot, "updating": state["updating"],
                                        "phase": state["phase"] if busy.locked() else None})
            if u.path == "/api/update/state":
                return self._send(200, update_info())
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
            if u.path == "/api/update/check":
                return self._send(*update_check())
            if u.path == "/api/update/apply":
                return self._send(*update_apply(self.client_address[0]))
            if u.path == "/api/palette/render":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*palette_render(d))
            if u.path in ("/api/lang", "/api/tz"):
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*pick_setting(u.path[5:], d))
            if u.path == "/api/display-offset":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*offset_setting(d))
            if u.path == "/api/patterns":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*patterns_setting(d))
            if u.path == "/api/update/settings":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*update_settings(d))
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
                        return self._send(200, take_sample(ctx, source="refresh", phase=lambda p: state.__setitem__("phase", p),
                                                           draw_lock=draw_lock))
                    except Exception as exc:
                        return self._send(500, {"ok": False, "error": "source read failed" if ctx.privacy else f"refresh failed: {exc}"})
                finally:
                    state["phase"] = None
                    busy.release()
            self._send(404, {"ok": False, "error": "not found"})

        def log_message(self, *a):
            pass
    srv = None
    hint = ctx.data_dir / "update.port"  # left by "Update": come back on the same port so the open page reconnects
    if not ctx.port and hint.exists():
        try:
            want = int(hint.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            want = 0
        try:
            hint.unlink()
        except OSError:
            pass
        if 0 < want < 65536:
            try:
                srv = ThreadingHTTPServer((ctx.host, want), H)
            except OSError:
                srv = None
    if srv is None:
        srv = ThreadingHTTPServer((ctx.host, ctx.port), H)
    port_box["port"] = srv.server_address[1]
    url = f"http://{ctx.host}:{srv.server_address[1]}/"
    info = ctx.data_dir / "history_server.json"
    with usage_lock.write_lock(ctx.data_dir):
        local_guard.write_token_file(token_file, token)
        info.write_text(json.dumps({"pid": os.getpid(), "url": url, "token_file": str(token_file)}))
    print(json.dumps({"ok": True, "url": url, "token_file": str(token_file)}), flush=True)
    if not os.environ.get("USAGE_NO_AUTO_UPDATE_CHECK"):  # the tests set this: no git fetch of the real checkout
        threading.Thread(target=update_auto_loop, daemon=True).start()
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
