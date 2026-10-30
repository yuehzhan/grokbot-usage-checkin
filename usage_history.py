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
  breakdown [FILE|-]      Import a usage breakdown snapshot (JSON file or stdin) into <data_dir>/breakdown/; no argument: show the newest

Files written: samples.csv (timestamp ISO-8601 with UTC offset, pct), checkins.csv
(timestamp, name, note), breakdown/*.json (usage breakdown snapshots, see usage_breakdown.py), history/* (history/pal/<palette>/* for the page's palette switcher),
sampler.pid, history_server.json, history_server.token (mode 600), sampler.log.
Every CSV/JSON write holds <data_dir>/.write.lock (usage_lock.py).
"""
import argparse, base64, calendar, csv, html, json, math, os, re, signal, subprocess, sys, threading, time
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
import usage_breakdown  # noqa: E402  (usage breakdown snapshots saved by the user's Grok Bot)
import usage_breakdown_ui  # noqa: E402  (Built-in tab: HTML/CSS/JS)
import usage_disk  # noqa: E402  (Built-in tab: free disk space bar)
import usage_history_l10n as l10n  # noqa: E402  (page strings for es / pt / fr / de / id)
import proc_util  # noqa: E402  (Windows-safe "is this pid alive?")

INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 15, 1440, 60
INTERVAL_CHOICES = (15, 30, 60, 120)  # the page's Read interval menu (another configured value is listed too)
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
        "clock_dl": "Hours", "clock_do": {"24": "Last 24 h", "auto": "Last 12 h", "am": "AM 12 h", "pm": "PM 12 h"},
        "clock_hl": {"am": "AM 0:00–12:00", "pm": "PM 12:00–24:00"}, "clock_hp": "{h} · {s}",
        "clock_face": {"am": "AM", "pm": "PM"}, "clock_tg": "{t} ({h})", "clock_1h": {"am": "Usage Clock — AM 12 hours", "pm": "Usage Clock — PM 12 hours"}, "clock_1a": "Usage Clock — last 12 hours ({h})",
        "clock_wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "clock_day": "{wd} {md}", "clock_wkl": "Wk {i} {a}–{b}",
        "clock_mon": "{mon} {y}", "clock_q": "Q{q} {y}", "clock_today": "{l} (today)", "clock_curp": "{l} (now)",
        "clock_note_g": "Times in {z} · colour = day (or week / month / quarter), see the legend · length = % increase in that hour · long gaps skipped",
        "clock_peak": "Peak {h}:00–{h2}:00  avg +{v:.1f}%/h  ({n} intervals)", "clock_none": "No valid intervals yet",
        "collecting": "Collecting data ({n}/{m})", "clock_note": "Times in {z} · length/color = avg % increase in that hour · resets counted from new value · long gaps skipped",
        "ck_legend": "Check-ins ({n})", "last": "Last update: {t} ({z})", "last_short": "Last update: {t}", "refresh": "⟳ Refresh", "loading": "Loading…", "refreshing": "⟳ Refreshing… {s} s",
        "ph_read": "Reading usage from Grok Build…", "ph_draw": "Drawing charts…",
        "range": "Range", "name": "Name", "all": "All", "note": "Note", "time": "Time", "recent": "Recent check-ins",
        "per_name": "Per name", "top_hour": "Most common hour", "none_ck": "No check-ins yet", "ck_btn": "Check in",
        "ck_hint": "Open this page through the local server to check in here, or run: usage_history.py checkin <name> [note] --category <id>",
        "category": "Category", "per_cat": "Per category", "count": "Check-ins", "share": "Share", "last_ck": "Last",
        "week": "Week start", "days": "Days", "peak": "Peak", "pace": "Pace", "at100": "100%", "yes": "yes",
        "month": "Month", "amount": "Amount", "weekly_detail": "Weekly detail", "conn_err": "Cannot reach local server",
        "clock_sh": "% increase per hour ({z}); 12 at top, clockwise. Left: {l}. Right: the same 12 hours, colour = day (or week / month / quarter), see the legend under the dial; length = % increase in that hour", "clock_sl": {"am": "AM 12 hours (0:00–12:00)", "pm": "PM 12 hours (12:00–24:00)"}, "clock_sa": "last 12 hours ({h})", "clock_sub": "% increase per hour ({z}); 0:00 at top, clockwise. Left: last 24 hours. Right: colour = day (or week / month / quarter), see the legend under the dial; length = % increase in that hour", "no_clock": "Collecting data — clock charts appear after sampling starts",
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
        "clock_dl": "時制", "clock_do": {"24": "近 24 小時", "auto": "近 12 小時", "am": "上午 12 小時", "pm": "下午 12 小時"},
        "clock_hl": {"am": "上午 0:00–12:00", "pm": "下午 12:00–24:00"}, "clock_hp": "{h}　{s}",
        "clock_face": {"am": "上午", "pm": "下午"}, "clock_tg": "{t}（{h}）", "clock_1h": {"am": "用量時鐘 — 上午 12 小時", "pm": "用量時鐘 — 下午 12 小時"}, "clock_1a": "用量時鐘 — 近 12 小時（{h}）",
        "clock_wd": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"], "clock_day": "{md} {wd}", "clock_wkl": "第{i}週 {a}–{b}",
        "clock_mon": "{y}年{m}月", "clock_q": "{y} Q{q}", "clock_today": "{l}（今天）", "clock_curp": "{l}（目前）",
        "clock_note_g": "時刻為 {z} · 顏色 = 不同天（或週／月／季），對照圖例 · 長度 = 該小時用量增加 · 間隔過長略過",
        "clock_peak": "尖峰 {h}:00–{h2}:00　平均 +{v:.1f}%/小時　（{n} 個區間）", "clock_none": "尚無有效區間",
        "collecting": "資料收集中（{n}/{m}）", "clock_note": "時刻為 {z} · 長度/顏色 = 該小時平均用量增加 · 週重置以新值計 · 間隔過長略過",
        "ck_legend": "打卡（{n} 次）", "last": "最後更新：{t}（{z}）", "last_short": "最後更新：{t}", "refresh": "⟳ 重新整理", "loading": "讀取中…", "refreshing": "⟳ 更新中… {s} 秒",
        "ph_read": "正在從 Grok Build 讀取用量…", "ph_draw": "正在產生圖表…",
        "range": "範圍", "name": "名字", "all": "全部", "note": "備註", "time": "時間", "recent": "最近打卡", "per_name": "各名字統計",
        "top_hour": "最常時段", "none_ck": "尚無打卡", "ck_btn": "打卡",
        "ck_hint": "以本機服務開啟此頁才能在這裡打卡；或執行：usage_history.py checkin <名字> [備註] --category <類別id>",
        "category": "類別", "per_cat": "各類別統計", "count": "次數", "share": "占比", "last_ck": "最近一次",
        "week": "週起始", "days": "天數", "peak": "峰值", "pace": "基準", "at100": "達 100%", "yes": "是",
        "month": "月份", "amount": "金額", "weekly_detail": "每週明細", "conn_err": "無法連線到本機服務",
        "clock_sh": "每小時用量增加（{z}），12 時在上、順時針。左圖：{l}。右圖：同樣的 12 小時，顏色 = 不同天（或週／月／季），對照下方圖例；長度 = 該小時用量增加", "clock_sl": {"am": "上午 12 小時（0:00–12:00）", "pm": "下午 12 小時（12:00–24:00）"}, "clock_sa": "近 12 小時（{h}）", "clock_sub": "每小時用量增加（{z}），0 時在上、順時針。左圖：近 24 小時。右圖：顏色 = 不同天（或週／月／季），對照下方圖例；長度 = 該小時用量增加", "no_clock": "資料收集中 — 開始取樣後才會產生時鐘圖",
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
    "clock_dl": "时制", "clock_do": {"24": "近 24 小时", "auto": "近 12 小时", "am": "上午 12 小时", "pm": "下午 12 小时"},
    "clock_1h": {"am": "用量时钟 — 上午 12 小时", "pm": "用量时钟 — 下午 12 小时"}, "clock_1a": "用量时钟 — 近 12 小时（{h}）", "clock_tg": "{t}（{h}）",
    "clock_wd": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"], "clock_wkl": "第{i}周 {a}–{b}", "clock_curp": "{l}（当前）",
    "clock_sh": "每小时用量增加（{z}），12 时在上、顺时针。左图：{l}。右图：同样的 12 小时，颜色 = 不同天（或周／月／季），对照下方图例；长度 = 该小时用量增加", "clock_sl": {"am": "上午 12 小时（0:00–12:00）", "pm": "下午 12 小时（12:00–24:00）"}, "clock_sa": "近 12 小时（{h}）", "clock_sub": "每小时用量增加（{z}），0 时在上、顺时针。左图：近 24 小时。右图：颜色 = 不同天（或周／月／季），对照下方图例；长度 = 该小时用量增加",
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
    "clock_dl": "12/24", "clock_do": {"24": "24", "auto": "12", "am": "0–11", "pm": "12–23"},
    "clock_hl": {"am": "0:00–12:00", "pm": "12:00–24:00"}, "clock_hp": "{h} · {s}",
    "clock_face": {"am": "0–11", "pm": "12–23"}, "clock_tg": "{t} ({h})", "clock_1h": {"am": "◷ 0–11", "pm": "◷ 12–23"}, "clock_1a": "◷ 12 ({h})",
    "clock_wd": ["1", "2", "3", "4", "5", "6", "7"], "clock_day": "{md}", "clock_wkl": "{i}: {a}–{b}", "clock_mon": "{y}-{m:02d}", "clock_q": "{y} Q{q}",
    "clock_today": "{l} ●", "clock_curp": "{l} ●", "clock_note_g": "", "clock_peak": "▲ {h}–{h2}  +{v:.1f}  ({n})", "clock_none": "∅",
    "collecting": "{n}/{m}", "clock_note": "", "ck_legend": "✓ {n}", "last": "↻ {t}", "last_short": "↻ {t}", "refresh": "↻", "loading": "…", "refreshing": "… {s}", "ph_read": "", "ph_draw": "",
    "range": "", "name": "", "all": "∗", "note": "", "time": "", "recent": "✓", "per_name": "∑", "top_hour": "▲",
    "none_ck": "∅", "ck_btn": "✓", "ck_hint": "", "week": "", "days": "D", "peak": "▲", "pace": "→", "at100": "100",
    "yes": "▲", "month": "", "amount": "$", "weekly_detail": "", "conn_err": "×", "clock_sh": "", "clock_sl": {"am": "", "pm": ""}, "clock_sa": "", "clock_sub": "", "no_clock": "∅", "hour_fmt": "{h}",
})

LATIN_LANGS = l10n.LANGS  # es, pt, fr, de, id: page strings in usage_history_l10n, merged over English


def _over_en(en, tr):
    """English table with a translation merged over it, one level deep for nested dicts (clock_rt, opts,
    back_states, ...), so a key missing from the translation still shows in English instead of failing."""
    out = dict(en)
    for k, v in tr.items():
        out[k] = dict(en[k], **v) if isinstance(v, dict) and isinstance(en.get(k), dict) else v
    return out


for _lg in LATIN_LANGS:
    STR[_lg] = _over_en(STR["en"], l10n.STR[_lg])


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
        self.anim_locked = getattr(a, "anim", None) is not None
        self.anim_setting = page_anim(gp("anim", "anim"))
        # <data_dir>/mascot.svg (optional, user's own image): adds a 'custom' choice; None when missing / invalid
        self.mascot_custom = custom_mascot_uri(self.data_dir)
        self.mascot_setting = page_mascot(gp("mascot", "mascot"), custom=bool(self.mascot_custom))  # page menu only (no CLI flag)
        self.seasonal_setting = page_seasonal(gp("seasonal", "seasonal"))  # seasonal accessories on the built-in mascot
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
        # read interval: config "sample_interval_minutes", else the page's Read interval menu (page_settings.json)
        iv = cfg.get("sample_interval_minutes")
        if iv is None:
            iv = _read_page_setting(self.data_dir, "sample_interval_minutes")
        self.interval = self._interval(INTERVAL_DEFAULT if iv is None else iv)
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
        # "custom" + custom_colors {"under", "over"} -> 'custom-<under><over>' (the key carries the colours)
        self.custom_colors = cfg.get("custom_colors") if isinstance(cfg.get("custom_colors"), dict) else None
        self.palette = usage_palette.resolve(g("palette", "palette", usage_palette.DEFAULT), self.custom_colors)
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
        return usage_checkins_ui.for_lang(self.lang)

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
        lab = utc_label(dt)
        if self.privacy:
            return lab
        return lab if self.tz_spec.lower() in ("local", "auto", "", "utc") else f"{self.tz_spec} {lab}"

    def disp(self, dt):
        """aware -> display tz; naive is treated as machine-local (how week CSVs are written)."""
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.astimezone(self.tz)


# menu order: Auto, the languages that were there first, then the newer ones by native name, and the
# numeric (word-free) option always last, shown as just "123456" in every language; add new languages to
# _MENU_LANGS, never after numeric
_MENU_LANGS = ("zh-TW", "zh-CN", "en", "ja", "ko", "id", "de", "es", "fr", "pt")
PAGE_LANGS = ("auto",) + tuple(k for k in _MENU_LANGS if k != "numeric") + ("numeric",)
NUMERIC_LABEL = "123456"
LANG_NAMES = {"zh-TW": "繁體中文", "zh-CN": "简体中文", "en": "English", "ja": "日本語", "ko": "한국어", "numeric": NUMERIC_LABEL,
              **l10n.NAMES}
PAGE_ZONES = ("Asia/Taipei", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Tokyo", "Asia/Seoul", "Asia/Singapore",
              "Asia/Bangkok", "Asia/Kolkata", "Asia/Dubai", "Australia/Sydney", "Pacific/Auckland", "Europe/London",
              "Europe/Paris", "Europe/Berlin", "Europe/Moscow", "America/Sao_Paulo", "America/New_York", "America/Chicago",
              "America/Denver", "America/Los_Angeles", "Pacific/Honolulu", "UTC")
PICK_STR = {  # labels of the language / time zone menus and their "auto" option, in every page language
    "zh-TW": {"lang": "語言", "tz": "時區", "auto": "自動（跟系統）", "locked": "已由啟動參數指定",
              "anim": "儀表動畫", "off": "關", "once": "只跑一次", "continuous": "持續", "applying": "套用中…",
              "interval": "讀取間隔", "minutes": "{n} 分鐘", "manual_only": "手動輸入模式：不會自動讀取"},
    "zh-CN": {"lang": "语言", "tz": "时区", "auto": "自动（跟随系统）", "locked": "已由启动参数指定",
              "anim": "仪表动画", "off": "关", "once": "只跑一次", "continuous": "持续", "applying": "应用中…",
              "interval": "读取间隔", "minutes": "{n} 分钟", "manual_only": "手动输入模式：不会自动读取"},
    "en": {"lang": "Language", "tz": "Time zone", "auto": "Auto (system)", "locked": "Set by a command-line flag",
           "anim": "Gauge animation", "off": "Off", "once": "Once", "continuous": "Continuous", "applying": "Applying…",
           "interval": "Read interval", "minutes": "{n} min", "manual_only": "Manual entry mode: no automatic reads"},
    "ja": {"lang": "言語", "tz": "タイムゾーン", "auto": "自動（システム）", "locked": "起動オプションで指定済み",
           "anim": "ゲージ演出", "off": "オフ", "once": "1回だけ", "continuous": "連続", "applying": "適用中…",
           "interval": "読み取り間隔", "minutes": "{n} 分", "manual_only": "手動入力モード：自動読み取りなし"},
    "ko": {"lang": "언어", "tz": "시간대", "auto": "자동(시스템)", "locked": "실행 옵션으로 지정됨",
           "anim": "게이지 애니메이션", "off": "끄기", "once": "한 번만", "continuous": "계속", "applying": "적용 중…",
           "interval": "읽기 간격", "minutes": "{n}분", "manual_only": "수동 입력 모드: 자동 읽기 없음"},
    "numeric": {"lang": "Aa", "tz": "◷", "auto": "auto", "locked": "CLI",
                "anim": "◔", "off": "0", "once": "1", "continuous": "∞", "applying": "…",
                "interval": "Δt", "minutes": "{n}′", "manual_only": "M"},
}

# Title mascot menu: label, button name and the option names (same order as MASCOT_CHOICES; 'custom' is listed
# before 'off' only when <data_dir>/mascot.svg is a usable SVG); "seasonal" / "sopts": the Seasonal accessories menu
MASCOT_STR = {
    'zh-TW': {"label": '小圓', "aria": '小圓，點一下眨眼', "opts": {'auto': '自動（主題強調色）', 'brown': '棕', 'black': '黑', 'grey': '灰', 'teal': '青綠', 'orange': '橘', 'pink': '粉紅', 'purple': '紫', 'blue': '藍', 'green': '綠', 'maroon': '酒紅', 'custom': '自訂', 'off': '關（隱藏）'}, "seasonal": '節日裝扮', "sopts": {'auto': '自動（依日期）', 'off': '關'}},
    'zh-CN': {"label": '小圆', "aria": '小圆，点一下眨眼', "opts": {'auto': '自动（主题强调色）', 'brown': '棕', 'black': '黑', 'grey': '灰', 'teal': '青绿', 'orange': '橙', 'pink': '粉红', 'purple': '紫', 'blue': '蓝', 'green': '绿', 'maroon': '酒红', 'custom': '自定义', 'off': '关（隐藏）'}, "seasonal": '节日装扮', "sopts": {'auto': '自动（按日期）', 'off': '关'}},
    'en': {"label": 'Mascot', "aria": 'Mascot, click to blink', "opts": {'auto': 'Auto (theme accent)', 'brown': 'Brown', 'black': 'Black', 'grey': 'Grey', 'teal': 'Teal', 'orange': 'Orange', 'pink': 'Pink', 'purple': 'Purple', 'blue': 'Blue', 'green': 'Green', 'maroon': 'Maroon', 'custom': 'Custom', 'off': 'Off (hidden)'}, "seasonal": 'Seasonal', "sopts": {'auto': 'Auto (by date)', 'off': 'Off'}},
    'ja': {"label": 'マスコット', "aria": 'マスコット（クリックでまばたき）', "opts": {'auto': '自動（テーマのアクセント色）', 'brown': 'ブラウン', 'black': 'ブラック', 'grey': 'グレー', 'teal': 'ティール', 'orange': 'オレンジ', 'pink': 'ピンク', 'purple': 'パープル', 'blue': 'ブルー', 'green': 'グリーン', 'maroon': 'マルーン', 'custom': 'カスタム', 'off': 'オフ（非表示）'}, "seasonal": '季節の飾り', "sopts": {'auto': '自動（日付で）', 'off': 'オフ'}},
    'ko': {"label": '마스코트', "aria": '마스코트, 클릭하면 눈을 깜빡여요', "opts": {'auto': '자동(테마 강조색)', 'brown': '갈색', 'black': '검정', 'grey': '회색', 'teal': '청록', 'orange': '주황', 'pink': '분홍', 'purple': '보라', 'blue': '파랑', 'green': '초록', 'maroon': '적갈색', 'custom': '사용자 지정', 'off': '끄기(숨김)'}, "seasonal": '시즌 장식', "sopts": {'auto': '자동(날짜별)', 'off': '끄기'}},
    'numeric': {"label": '●', "aria": '●', "opts": {'auto': 'auto', 'brown': '1', 'black': '2', 'grey': '3', 'teal': '4', 'orange': '5', 'pink': '6', 'purple': '7', 'blue': '8', 'green': '9', 'maroon': '10', 'custom': '*', 'off': '0'}, "seasonal": '✦', "sopts": {'auto': 'auto', 'off': '0'}},
}
for _lg in LATIN_LANGS:
    PICK_STR[_lg] = _over_en(PICK_STR["en"], l10n.PICK[_lg])
    MASCOT_STR[_lg] = _over_en(MASCOT_STR["en"], l10n.MASCOT[_lg])
# Title mascot: an original round blob drawn as inline SVG to the right of the page title. 'auto' follows the theme
# accent (var(--accent), so it also follows the palette menu); the fixed colours are body hexes; 'off' hides it.
# Eyes are dark or light, whichever reads better on the body; hidden in privacy mode (like the GitHub mark).
MASCOT_CHOICES = ("auto", "brown", "black", "grey", "teal", "orange", "pink", "purple", "blue", "green", "maroon", "off")
MASCOT_DEFAULT = "off"
MASCOT_COLORS = {"brown": "#9A6A45", "black": "#2A2C31", "grey": "#A3ACB6", "teal": "#2CC5B0", "orange": "#FFA94D",
                 "pink": "#F6A6CF", "purple": "#A98BF5", "blue": "#5FA8F5", "green": "#5CCB7E",
                 "maroon": "#A8182E"}
MASCOT_EYE_DARK, MASCOT_EYE_LIGHT, MASCOT_EYE_BLACK = "#1B1F24", "#FFF8F0", "#050506"


MASCOT_CUSTOM = "custom"  # the user's own <data_dir>/mascot.svg (never in git, so self-update leaves it alone)
MASCOT_FILE = "mascot.svg"
MASCOT_MAX_BYTES = 200 * 1024


def page_mascot(v, custom=False):
    """A mascot setting as stored: one of MASCOT_CHOICES ('none'/'hidden'/false read as off), or 'custom' when the
    user's mascot.svg is usable (custom=True; otherwise 'custom' falls back to off); else the default."""
    v = str(v if v is not None else "").strip().lower()
    v = {"none": "off", "hidden": "off", "false": "off", "gray": "grey", "default": "auto"}.get(v, v)
    if v == MASCOT_CUSTOM:
        return MASCOT_CUSTOM if custom else "off"
    return v if v in MASCOT_CHOICES else MASCOT_DEFAULT


def mascot_choices(ctx):
    """The menu's options: MASCOT_CHOICES, plus 'custom' (right before 'off') when mascot.svg is usable."""
    if not getattr(ctx, "mascot_custom", None):
        return MASCOT_CHOICES
    return MASCOT_CHOICES[:-1] + (MASCOT_CUSTOM,) + MASCOT_CHOICES[-1:]


_SVG_PROLOG = re.compile(r"\A(?:\s+|<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE\s+svg[^>\[]*>)*", re.S | re.I)


def custom_mascot_uri(data_dir):
    """<data_dir>/mascot.svg as a data:image/svg+xml;base64 URI for an <img> (never inlined into the page, so any
    script in it cannot run), or None when it is missing, larger than MASCOT_MAX_BYTES, not UTF-8 text, has no <svg>
    root element, or declares entities (<!ENTITY>, internal DOCTYPE subsets)."""
    p = Path(data_dir) / MASCOT_FILE
    try:
        if not p.is_file() or p.stat().st_size > MASCOT_MAX_BYTES:
            return None
        with open(p, "rb") as fh:
            raw = fh.read(MASCOT_MAX_BYTES + 1)
    except OSError:
        return None
    if not raw or len(raw) > MASCOT_MAX_BYTES:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if "\x00" in text or re.search(r"<!ENTITY", text, re.I):
        return None
    body = text[_SVG_PROLOG.match(text).end():]
    if not re.match(r"<svg[\s/>]", body) or not re.search(r"</svg\s*>\s*\Z|\A<svg[^>]*/>\s*\Z", body):
        return None
    return "data:image/svg+xml;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _rel_lum(hexcol):
    h = hexcol.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def mascot_eyes(body):
    """(eye, highlight, mouth) for a body colour. Eyes are always dark with a small white highlight (no light
    'looking up' eyes, no rim, no outline); on a body too dark for that (black) the eyes and mouth go near-pure black."""
    lb = _rel_lum(body)
    if (lb + 0.05) / (_rel_lum(MASCOT_EYE_DARK) + 0.05) >= 3:
        return MASCOT_EYE_DARK, "#FFFFFF", MASCOT_EYE_DARK
    return MASCOT_EYE_BLACK, "#FFFFFF", MASCOT_EYE_BLACK


def mascot_auto_vars(accent):
    """:root vars for the 'auto' mascot (body = the accent): eyes / mouth by the same rule as the named colours
    (mascot_eyes), never the button text colour --on-accent, which is white on dark accents. PALETTE_JS repeats the
    rule when the palette menu moves the accent."""
    from matplotlib.colors import to_hex
    try:
        eye, _hl, mouth = mascot_eyes(to_hex(accent))
    except ValueError:
        eye, mouth = MASCOT_EYE_DARK, MASCOT_EYE_DARK
    return f"--m-eye:{eye};--m-mouth:{mouth}"


MASCOT_SVG = ('<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">'
              '<path class="mbody" d="M20 4.5C30.2 4.5 37 11.6 37 21.2 37 30.4 30 36 20 36S3 30.4 3 21.2C3 11.6 9.8 4.5 20 4.5Z"/>'
              '<ellipse class="mshine" cx="12.6" cy="11.4" rx="4" ry="2.3" transform="rotate(-32 12.6 11.4)"/>'
              '<ellipse class="mblush" cx="9.6" cy="25.4" rx="2.6" ry="1.5"/><ellipse class="mblush" cx="30.4" cy="25.4" rx="2.6" ry="1.5"/>'
              '<g class="meye"><ellipse cx="14.3" cy="20.4" rx="2.7" ry="3.2"/><circle class="mhl" cx="15.2" cy="19.2" r="1"/></g>'
              '<g class="meye"><ellipse cx="25.7" cy="20.4" rx="2.7" ry="3.2"/><circle class="mhl" cx="26.6" cy="19.2" r="1"/></g>'
              '<path class="mmouth" d="M18.2 25.3q1.8 1.6 3.6 0"/></svg>')


# Seasonal accessories on the built-in mascot (never on the user's own mascot.svg): original simple vector art drawn
# in the same 0 0 40 40 box (hats / streamers reach a little past it; the svg is overflow:visible, so the title
# layout does not move). Fixed colours of their own, readable on light and dark pages. Picked from the dashboard's
# display date (ctx.now(), the page's time zone) when the page is built; 'seasonal' auto (default) / off.
SEASONAL_CHOICES = ("auto", "off")
SEASONAL_DEFAULT = "auto"
# (name, first (month, day), last (month, day)), both days included; a window may wrap over New Year
MASCOT_SEASONS = (("newyear", (12, 31), (1, 2)), ("valentine", (2, 13), (2, 15)), ("childrensday", (5, 1), (5, 5)),
                  ("halloween", (10, 25), (11, 1)), ("christmas", (12, 20), (12, 26)))


def page_seasonal(v):
    """A seasonal setting as stored: 'auto' or 'off' (false / none / hidden read as off); else the default."""
    v = str(v if v is not None else "").strip().lower()
    v = {"none": "off", "hidden": "off", "false": "off", "0": "off", "on": "auto", "true": "auto", "1": "auto"}.get(v, v)
    return v if v in SEASONAL_CHOICES else SEASONAL_DEFAULT


def mascot_season(d):
    """The seasonal accessory for a date (a date or datetime, already in the display zone), or None."""
    md = (d.month, d.day)
    for name, a, b in MASCOT_SEASONS:
        if (a <= md <= b) if a <= b else (md >= a or md <= b):
            return name
    return None


def _spark(x, y, r, cols, n=14, delay=0, trail=None):
    """A firework burst: n thin streaks shooting out from (x, y) with an empty centre (no flower heart), lengths
    varying, solid (colour 1) and dotted spark trails (colour 2) in turn, each with a tiny bright spark dot at the
    tip (colour 3). trail=(x0, y0): a dotted rising trail from (x0, y0) up to just below the burst."""
    out = []
    a_col, d_col, t_col = cols
    for i in range(n):
        a = 2 * math.pi * i / n - math.pi / 2
        c, s_ = math.cos(a), math.sin(a)
        ln = r * (1.0, 0.7, 0.92, 0.6, 0.86, 0.74, 0.98, 0.64, 0.8)[i % 9]
        p = lambda f: f"{x + c * ln * f:.2f} {y + s_ * ln * f:.2f}"
        # solid streaks and dotted spark trails in turn, one warm gold family, so no ring of petals forms
        dot = f'" stroke="{a_col}' if i % 2 == 0 else f'" stroke="{d_col}" stroke-dasharray=".1 1.05'
        out.append(f'<path d="M{p(.3)}L{p(.86)}{dot}"/>'
                   f'<circle cx="{x + c * ln:.2f}" cy="{y + s_ * ln:.2f}" r=".52" fill="{t_col}" stroke="none"/>')
    if trail:
        x0, y0 = trail
        out.insert(0, f'<path d="M{x0} {y0}Q{x0 + (x - x0) * 0.2:.2f} {(y0 + y) / 2:.2f} {x:.2f} {y + r * 0.3:.2f}" stroke="{d_col}" '
                      'stroke-width=".8" stroke-dasharray=".15 1.3"/>')
    st = f' style="animation-delay:{delay}s"' if delay else ""
    return f'<g class="mspark"{st} fill="none" stroke-width=".75" stroke-linecap="round">{"".join(out)}</g>'


def _sparkler(x, y, r, cols, delay=0):
    """A sparkler: a bright point with fine spiky sparks of uneven length and angle shooting out of it."""
    rays = ((0, 1), (27, .55), (51, .85), (80, .5), (104, .95), (133, .6), (160, .8), (187, .45), (209, .9),
            (238, .55), (262, 1), (290, .5), (313, .8), (338, .6))
    out = []
    for i, (deg, f) in enumerate(rays):
        a = math.radians(deg)
        out.append(f'<path d="M{x + math.cos(a) * r * .12:.2f} {y + math.sin(a) * r * .12:.2f}L{x + math.cos(a) * r * f:.2f} '
                   f'{y + math.sin(a) * r * f:.2f}" stroke="{cols[i % len(cols)]}"/>')
    st = f' style="animation-delay:{delay}s"' if delay else ""
    return (f'<g class="mspark"{st} fill="none" stroke-width=".65" stroke-linecap="round">{"".join(out)}'
            f'<circle cx="{x}" cy="{y}" r=".55" fill="#FFFFFF" stroke="none"/></g>')


def _twinkle(x, y, r, col, delay=0):
    """A four-point twinkle star."""
    k = r * 0.28
    st = f' style="animation-delay:{delay}s"' if delay else ""
    return (f'<path class="mspark"{st} fill="{col}" d="M{x} {y - r}Q{x + k:.2f} {y - k:.2f} {x + r} {y}Q{x + k:.2f} {y + k:.2f} {x} {y + r}'
            f'Q{x - k:.2f} {y + k:.2f} {x - r} {y}Q{x - k:.2f} {y - k:.2f} {x} {y - r}Z"/>')


def _carp(y, body, fin, scale=1.0):
    """One koinobori carp hanging from the pole at x=34.6, flying to the right (mouth ring at the pole)."""
    return (f'<g transform="translate(34.6 {y}) scale({scale})">'
            f'<path d="M0 -2.1Q5.2 -3.2 8.6 -1.5L11.4 -3.1L10.2 0L11.4 3.1L8.6 1.5Q5.2 3.2 0 2.1Z" fill="{body}" stroke="#00000033" stroke-width=".35"/>'
            f'<path d="M3.6 -2.4Q4.6 0 3.6 2.4M6.2 -2.2Q7.1 0 6.2 2.2" fill="none" stroke="{fin}" stroke-width=".7" stroke-linecap="round"/>'
            f'<ellipse cx=".5" cy="0" rx=".7" ry="2.1" fill="#FFFFFF"/>'
            f'<circle cx="2.2" cy="-.5" r="1" fill="#FFFFFF"/><circle cx="2.35" cy="-.5" r=".5" fill="#1B1F24"/></g>')


HEART = "M0 3.6C-5.6 0-5.6-5.2-2.6-5.2-1.2-5.2-.3-4.3 0-3.2.3-4.3 1.2-5.2 2.6-5.2 5.6-5.2 5.6 0 0 3.6Z"

MASCOT_ACCESSORIES = {
    # New Year: two classic gold firework bursts (thin gold / amber streaks with pale gold spark tips, one with a
    # dotted rising trail), a gold sparkler and a twinkle
    "newyear": (_spark(4.6, 3.6, 6.2, ("#E8960E", "#F7B52A", "#FFC933"), n=16, trail=(1.2, 18))
                + _spark(35.6, 2.4, 5.2, ("#F2A81D", "#EE7F1C", "#FFC933"), n=13, delay=.5)
                + _sparkler(39.6, 15, 3.8, ("#F7B52A", "#FFD84D", "#EE7F1C"), delay=1)
                + _twinkle(21, -4, 1.4, "#F7B52A", delay=.8)),
    # Valentine's Day: a pink-red heart floating at the upper right, with a tiny one beside it
    "valentine": (f'<g transform="translate(34.4 7.2) rotate(14)"><path d="{HEART}" fill="#FF4D73" stroke="#C2184A" stroke-width=".7" stroke-linejoin="round"/>'
                  '<ellipse cx="-2.6" cy="-3" rx="1.2" ry=".75" fill="#FFFFFF" opacity=".75" transform="rotate(-30 -2.6 -3)"/></g>'
                  f'<g transform="translate(28.6 -.6) rotate(-12) scale(.42)"><path d="{HEART}" fill="#FF8FB0" stroke="#C2184A" stroke-width="1.2"/></g>'),
    # Children's Day: a small pole with a gold ball and two carp streamers (red, blue) flying to the right
    "childrensday": ('<path d="M34.6 -4.4V23" stroke="#C08A55" stroke-width="1.3" stroke-linecap="round"/>'
                     '<circle cx="34.6" cy="-4.8" r="1.4" fill="#FFC83D" stroke="#B8860B" stroke-width=".35"/>'
                     '<g class="mwave">' + _carp(.8, "#F0453A", "#FFD0C8", 1.12) + '</g>'
                     '<g class="mwave" style="animation-delay:.6s">' + _carp(8.2, "#2F7CF0", "#CFE3FF", .96) + '</g>'),
    # Halloween: a little jack-o'-lantern worn as a hat (orange, green stem, dark carved face)
    "halloween": ('<g transform="rotate(-6 20 4)">'
                  '<path d="M20 -2.4C25.6 -4 31.4 -1.4 31.4 3.8 31.4 8.6 26.2 10.2 20 9.8 13.8 10.2 8.6 8.6 8.6 3.8 8.6 -1.4 14.4 -4 20 -2.4Z" fill="#FF8A1F" stroke="#B4510C" stroke-width=".7"/>'
                  '<path d="M14.6 -1.8Q10.8 3.8 14.4 9.6M25.4 -1.8Q29.2 3.8 25.6 9.6M20 -2.2V-.6" fill="none" stroke="#D8640F" stroke-width=".8" stroke-linecap="round"/>'
                  '<path d="M19.6 -2.2Q19.2 -5.2 21.6 -6.4" fill="none" stroke="#3E8E3A" stroke-width="1.8" stroke-linecap="round"/>'
                  '<path d="M21.2 -4.4Q24.6 -6.8 26 -3.8Q23.4 -2.8 21.2 -4.4Z" fill="#5CB84A"/>'
                  '<g fill="#3A1C06"><path d="M14.6 3.6L16.6 .4L18.2 3.6Z"/><path d="M21.8 3.6L23.4 .4L25.4 3.6Z"/>'
                  '<path d="M14.4 5.2Q20 9.6 25.6 5.2L24.1 5.9L23 5.1L21.6 6.2L20 5.3L18.4 6.2L17 5.1L15.9 5.9Z"/></g></g>'),
    # Christmas: a red Santa hat with a white brim and a pom-pom flopping to the right
    "christmas": ('<path d="M8.6 9.4Q10.6 -.8 21 -2.6Q30.6 -4 35 2.6L33.4 3.8Q30 1.4 28.4 4.8L31.4 9.4Z" fill="#E0303A" stroke="#A3141F" stroke-width=".6" stroke-linejoin="round"/>'
                  '<path d="M14 2.6Q19 -1.6 25.8 -1.6" fill="none" stroke="#FF6A70" stroke-width="1" stroke-linecap="round" opacity=".7"/>'
                  '<rect x="6.6" y="7" width="26.8" height="5.4" rx="2.7" fill="#FFFFFF" stroke="#C9CDD6" stroke-width=".6" transform="rotate(-5 20 9.7)"/>'
                  '<circle cx="35.4" cy="3.4" r="2.8" fill="#FFFFFF" stroke="#C9CDD6" stroke-width=".6"/>'),
}


def mascot_accessory_svg(name):
    """The overlay svg for one accessory (same viewBox as MASCOT_SVG, decorative only)."""
    return (f'<svg class="macc" data-s="{name}" viewBox="0 0 40 40" aria-hidden="true" focusable="false">'
            f'{MASCOT_ACCESSORIES[name]}</svg>')


def mascot_accessory(ctx):
    """The seasonal accessory's name for the page being built, or None (setting off, privacy, or no season today)."""
    if ctx.privacy or getattr(ctx, "seasonal_setting", SEASONAL_DEFAULT) == "off":
        return None
    return mascot_season(ctx.now())


def mascot_css():
    """Mascot styles: colour variables per option, the in-place blink (eyes scaleY, ~200 ms), reduced-motion off."""
    # the title stays an inline h1 and the mascot flows right after its last word, so a title that wraps on a phone
    # keeps the mascot next to the text (not pushed to the far edge); same 26px font so 'middle' centres it on the text
    out = [".hrow{margin:4px 0 8px;font-size:26px;font-weight:700} .hrow h1{display:inline;margin:0;font-size:inherit}",
           " .mascot{forced-color-adjust:none;display:inline-block;vertical-align:middle;position:relative;top:-2px;width:34px;height:34px;padding:0;margin:0 0 0 10px;border:0;border-radius:50%;background:none;"
           "cursor:pointer;-webkit-tap-highlight-color:transparent;--mb:var(--accent);--me:var(--m-eye,#1B1F24);--mh:#FFFFFF;--ms:transparent;--mm:var(--m-mouth,#1B1F24)}",
           " .mascot svg{display:block;width:100%;height:100%;overflow:visible} .mascot .mbody{fill:var(--mb);stroke:var(--ms);stroke-width:.8}",
           " .mascot .mshine{fill:#FFFFFF;opacity:.22} .mascot .mblush{fill:#FF9EBB;opacity:.4} .mascot .meye ellipse{fill:var(--me)}",
           " .mascot .mhl{fill:var(--mh)} .mascot .mmouth{fill:none;stroke:var(--mm);stroke-width:1.3;stroke-linecap:round}",
           " .mascot .meye{transform-box:fill-box;transform-origin:center}"]
    for k, body in MASCOT_COLORS.items():
        eye, hl, mouth = mascot_eyes(body)
        out.append(f" .mascot[data-c={k}]{{--mb:{body};--me:{eye};--mh:{hl};--mm:{mouth}}}")
    out.append(" .mascot.blink .meye{animation:mblink .2s ease-in-out} @keyframes mblink{0%,100%{transform:scaleY(1)}50%{transform:scaleY(.1)}}"
               " @media (max-width:480px){.mascot{width:26px;height:26px;margin-left:7px;top:-1px}}"
               # seasonal accessories: fixed colours; the sparkles twinkle and the carp streamers sway (both stop with reduced motion)
               " .mascot svg.macc{position:absolute;left:0;top:0;pointer-events:none} .mascot .mspark,.mascot .mwave{transform-box:fill-box}"
               " .mascot .mspark{transform-origin:center;animation:mspark 1.8s ease-in-out infinite}"
               " .mascot .mwave{transform-origin:left center;animation:mwave 2.4s ease-in-out infinite}"
               " @keyframes mspark{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.55;transform:scale(.72)}}"
               " @keyframes mwave{0%,100%{transform:skewY(0) scaleX(1)}50%{transform:skewY(5deg) scaleX(.94)}}"
               " @media (prefers-reduced-motion: reduce){.mascot .meye,.mascot.blink .meye{animation:none!important;transition:none!important}}"
               " @media (prefers-reduced-motion: reduce){.mascot .mspark,.mascot .mwave{animation:none!important;transition:none!important}}"
               # the user's own mascot.svg: an <img> in the same slot (same size); the built-in blob is hidden then
               " .mascot .mcustom{display:none;width:100%;height:100%;object-fit:contain;border-radius:0}"
               " .mascot[data-c=custom] svg{display:none} .mascot[data-c=custom] .mcustom{display:block} .mascot[data-c=custom]{border-radius:0}"
               # MASCOT_JS repaints the blob (and a still accessory) on a <canvas>: browser auto dark modes (Chrome's
               # Auto Dark Mode for Web Contents, ...) lighten dark inline-SVG fills and even small image icons, not canvas
               " .mascot .mcv{position:absolute;left:-20%;top:-25%;width:140%;height:140%;pointer-events:none}"
               " .mascot.mcv-on svg.mflat{opacity:0} .mascot[data-c=custom] .mcv{display:none}")
    return "".join(out)


def mascot_html(ctx, lk):
    """The mascot button (empty in privacy mode, like the GitHub mark)."""
    if ctx.privacy:
        return ""
    S = MASCOT_STR.get(lk) or MASCOT_STR["en"]
    v = ctx.mascot_setting
    uri = getattr(ctx, "mascot_custom", None)
    # the user's SVG only ever goes in as an <img src="data:image/svg+xml;base64,..."> (scripts in it do not run)
    img = f'<img class="mcustom" src="{html.escape(uri)}" alt="" draggable="false">' if uri else ""
    # the seasonal accessory: a second svg laid over the built-in blob in the same 40x40 box (hidden with it when
    # Custom is picked, since every svg in the button is; the button itself is hidden when the mascot is off)
    acc = mascot_accessory(ctx)
    svg = MASCOT_SVG + (mascot_accessory_svg(acc) if acc else "")
    ds = f' data-s="{acc}"' if acc else ""
    return (f'<button type="button" class="mascot" id="mascot" data-c="{v}"{ds} aria-label="{html.escape(S["aria"])}" '
            f'title="{html.escape(S["aria"])}"{" hidden" if v == "off" else ""}>{svg}{img}</button>')


# Blink in place: click / tap, mouse hover, Enter / Space (a real button), and an idle blink every 10-20 s while the
# tab is visible. prefers-reduced-motion: no idle blinks and no animation. The menu calls window.usageMascot(v).
MASCOT_JS = r"""(function(){var b=document.getElementById('mascot'),s=document.getElementById('mascotsel');if(!b)return;
var mq=window.matchMedia?matchMedia('(prefers-reduced-motion: reduce)'):null,t=0,u=0;function rm(){return !!(mq&&mq.matches);}
function blink(){if(rm()||b.hidden)return;b.classList.remove('blink');void b.offsetWidth;b.classList.add('blink');clearTimeout(u);u=setTimeout(function(){b.classList.remove('blink');},260);if(fr.c){draw('c');setTimeout(function(){draw('o');},200);}}
function idle(){clearTimeout(t);if(document.hidden||rm())return;t=setTimeout(function(){blink();idle();},10000+Math.random()*10000);}
function set(v){if(!/^[a-z]{1,12}$/.test(String(v)))return;b.dataset.c=v;b.hidden=v==='off';var l=document.getElementById('seasonl');if(l)l.hidden=v==='off'||v==='custom';paint();}
b.addEventListener('click',blink);b.addEventListener('pointerenter',function(e){if(e.pointerType==='mouse')blink();});
document.addEventListener('visibilitychange',idle);if(mq&&mq.addEventListener)mq.addEventListener('change',idle);
// paint on a canvas (auto dark modes recolour inline-SVG fills and small image icons, not canvas pixels): the svgs are
// flattened with their computed colours (so 'auto' and the palette menu still apply), drawn eyes-open and eyes-closed,
// and the svgs stay in place, transparent, as the fallback. Animated accessories (sparkles, streamers) stay live svg.
var cv=document.createElement('canvas'),fr={},gen=0,PR=['fill','stroke','stroke-width','opacity','fill-opacity','stroke-opacity','stroke-linecap','stroke-linejoin','stroke-dasharray','display'];
cv.className='mcv';cv.setAttribute('aria-hidden','true');b.appendChild(cv);
function flat(src,closed){var c=src.cloneNode(true),a=src.querySelectorAll('*'),z=c.querySelectorAll('*');
for(var i=0;i<a.length;i++){var cs=getComputedStyle(a[i]),st='';PR.forEach(function(p){var v=cs.getPropertyValue(p);if(v)st+=p+':'+v+';';});
if(closed&&a[i].classList.contains('meye')){var e=a[i].querySelector('ellipse'),x=e.getAttribute('cx'),y=e.getAttribute('cy');z[i].setAttribute('transform','translate('+x+' '+y+') scale(1 .1) translate(-'+x+' -'+y+')');}
z[i].setAttribute('style',st);z[i].removeAttribute('class');}
['x','y','width','height'].forEach(function(k,j){c.setAttribute(k,j<2?0:40);});c.setAttribute('overflow','visible');c.removeAttribute('class');c.removeAttribute('style');return c;}
function svgs(){return [].filter.call(b.querySelectorAll('svg'),function(x){return !x.querySelector('.mspark,.mwave');});}
function draw(k){var im=fr[k],w=b.clientWidth,d=window.devicePixelRatio||1;if(!im||!im.complete||!w)return;
cv.width=Math.round(w*1.4*d);cv.height=Math.round(b.clientHeight*1.4*d);var x=cv.getContext('2d');x.clearRect(0,0,cv.width,cv.height);x.drawImage(im,0,0,cv.width,cv.height);
svgs().forEach(function(g){g.classList.add('mflat');});b.classList.add('mcv-on');}
function paint(){if(b.hidden||b.dataset.c==='custom'||!window.XMLSerializer||!window.URL||!URL.createObjectURL)return;var n=++gen,left=2,L=svgs();
['o','c'].forEach(function(k){var o=document.createElementNS(L[0].namespaceURI,'svg');o.setAttribute('viewBox','-8 -10 56 56');o.setAttribute('width','112');o.setAttribute('height','112');
L.forEach(function(g){o.appendChild(flat(g,k==='c'));});var url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(o)],{type:'image/svg+xml'})),im=new Image();
im.onload=function(){URL.revokeObjectURL(url);if(n!==gen)return;fr[k]=im;if(--left===0)draw(b.classList.contains('blink')?'c':'o');};im.onerror=function(){URL.revokeObjectURL(url);};im.setAttribute('src',url);});}
if(window.MutationObserver)new MutationObserver(paint).observe(document.documentElement,{attributes:true,attributeFilter:['style','class']});
window.addEventListener('resize',function(){draw('o');});
window.usageMascot=set;if(s)set(s.value);else paint();idle();})();"""

# Gauge "rev up" animation on the served page (the PNG never changes): off, once (default) or continuous
ANIM_MODES = ("off", "once", "continuous")
ANIM_DEFAULT = "once"


def page_anim(v):
    """An animation setting as stored: 'off' | 'once' | 'continuous' ('loop' is read as continuous); else the default."""
    v = str(v if v is not None else "").strip().lower()
    v = {"loop": "continuous", "on": "continuous", "none": "off", "false": "off"}.get(v, v)
    return v if v in ANIM_MODES else ANIM_DEFAULT


def _slug(v):
    return "".join(c if c.isalnum() else "_" for c in str(v)).lower()


def page_lang(v):
    """A language setting as stored ('auto', 'zh-TW', ..., 'numeric'); anything else counts as 'auto'."""
    v = str(v or "auto").strip()
    for k in PAGE_LANGS:
        if v.lower() == k.lower():
            return k
    base = v.replace("_", "-").split("-")[0].lower()
    if base in LATIN_LANGS:  # 'pt-BR', 'es_419', 'de-AT' from a config -> the page language
        return base
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


def breakdown_page_data(ctx):
    """Data of the Built-in tab: the newest usage breakdown snapshot (and the previous one for the small delta)."""
    snaps = usage_breakdown.load(ctx.data_dir, ctx.tz)
    newest = usage_breakdown.latest_and_previous(snaps)[0]   # its optional disk fields (the bot's own computer) feed the disk bar
    return usage_breakdown_ui.page_data(snaps, ctx.lang, ctx.tz, ctx.privacy, usage_disk.page_data(ctx.cfg, snap=newest))


def breakdown_import(ctx, text):
    """Paste / import path: save a snapshot given as JSON text, then rebuild the page. Returns a result dict."""
    try:
        snap, path = usage_breakdown.import_text(ctx.data_dir, text, ctx.tz, ctx.now())
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        rewrite_page(ctx)
    except Exception as exc:
        print(f"Warning: {exc}", file=sys.stderr)
    return {"ok": True, "file": path.name, "captured_at": snap["captured_at"], "period": snap["period"]}


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
# 時制: "24" = the whole day on a 24-slot dial (0:00 at the top, default); "am" / "pm" = only hours 0-11 / 12-23 on a
# 12-hour clock face (12 at the top). The other half is left out, never folded onto the shown one. "auto" (近 12 小時)
# is not a drawing of its own: it picks "am" (local hour < 12) or "pm" at render time and then is exactly that half.
CLOCK_DIALS = ("24", "auto", "am", "pm")


AUTO_FILES = ("auto-am", "auto-pm")  # the left dial of 近 12 小時 is its own file (its title differs from an explicit AM / PM)


def dial_for(dial, now, key):
    """The dial token that names a PNG: the resolved half, 'auto-am' / 'auto-pm' for the left dial of 近 12 小時."""
    half = resolve_dial(dial, now)
    return f"auto-{half}" if dial == "auto" and key == "1d" else half


def resolve_dial(dial, now):
    """'auto' -> 'am' before 12:00 local time, 'pm' from 12:00 on; every other value is returned as is."""
    return ("am" if now.hour < 12 else "pm") if dial == "auto" else dial


def dial_hours(dial):
    return list(range(12)) if dial == "am" else (list(range(12, 24)) if dial == "pm" else list(range(24)))


def clock_sub_texts(L, zl):
    """The line under the clock toolbar for every Hours state the page can show, keyed '24' / 'am' / 'pm' / 'auto-am' /
    'auto-pm' (the page picks one when the menu changes). 24H is the v1.5.1 sentence, character for character."""
    out = {"24": L["clock_sub"].format(z=zl)}
    for h in ("am", "pm"):
        out[h] = L["clock_sh"].format(z=zl, l=L["clock_sl"][h])
        out["auto-" + h] = L["clock_sh"].format(z=zl, l=L["clock_sa"].format(h=L["clock_hl"][h]))
    return out


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
            add(L["clock_day"].format(wd=L["clock_wd"][d.weekday()], md=_md(L, d)), d, e, d.date() == now.date())
            d = e
    elif key == "1m":
        try:
            ws = _period_start(ctx, st)
        except Exception:
            ws = st
        i = 1
        while ws < now:
            we = ws + timedelta(days=ctx.period_days)
            add(L["clock_wkl"].format(i=i, a=_md(L, max(ws, st)), b=_md(L, min(we, now) - timedelta(seconds=1))), ws, we, ws <= now < we)
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
                mon = L["months"][m - 1] if L.get("months") else calendar.month_abbr[m]
                lab = L["clock_mon"].format(mon=mon, y=y, m=m)
            elif unit == 3:
                lab = L["clock_q"].format(q=(m - 1) // 3 + 1, y=y)
            else:
                lab = str(y)
            add(lab, a, b, a <= now < b)
            y, m = y2, m2
    return out[-8:]


def _md(L, d):
    """Month/day as the language writes it: 10/27 (default), 27/10 (es, pt, fr, id), 27.10. (de)."""
    return L.get("md_fmt", "{m:02d}/{d:02d}").format(m=d.month, d=d.day)


def _half_spans(L, since, now, dial):
    """The stretches of [since, now] that fall in the AM (0-11) or PM (12-23) half, oldest first, as text:
    '10-27 02:37–12:00' (a stretch that ends at midnight reads 24:00). Over 24 h they add up to 12 hours."""
    lo, hi = (0, 12 * 60) if dial == "am" else (12 * 60, 24 * 60)
    out, day = [], since.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= now:
        a, b = max(since, day + timedelta(minutes=lo)), min(now, day + timedelta(minutes=hi))
        if b > a:
            mm = lambda t, d=day: int((t - d).total_seconds() // 60)
            f = lambda m: f"{m // 60:02d}:{m % 60:02d}"
            out.append(f"{_md(L, day)} {f(mm(a))}–{f(mm(b))}")
        day = (day + timedelta(days=1, hours=12)).replace(hour=0, minute=0, second=0, microsecond=0)  # next local midnight
    return out


def _clock_theta(t, dial="24"):
    if dial in ("am", "pm"):
        return (((t.hour % 12) + t.minute / 60) / 12) * 2 * math.pi
    return ((t.hour + t.minute / 60) / 24) * 2 * math.pi


CLOCK_TIP_GROUP_PX = 5.0  # dots closer than this (in PNG pixels; a dot is ~7 px wide) share one tooltip
CLOCK_TIP_MAX = 8         # items listed per tooltip (newest first); the rest become "+N more"


def utc_label(dt):
    """'UTC+8' / 'UTC-3:30' for an aware datetime (no zone names: also fine in privacy mode)."""
    off = dt.utcoffset() or timedelta(0)
    m = int(off.total_seconds() // 60); s = "+" if m >= 0 else "-"; m = abs(m)
    return f"UTC{s}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")


def clock_hotspots(ctx, fig, ax, marks, colors, names, now):
    """Hover / tap targets for the check-in dots of one clock PNG, in % of the image (so they follow the responsive
    <img>; the PNG is saved without bbox_inches, so figure pixels = image pixels). Dots within CLOCK_TIP_GROUP_PX of
    each other are one spot listing every check-in (newest first, at most CLOCK_TIP_MAX plus a 'more' count).
    Privacy mode: names become #1, #2 ... (public_alias) and notes are dropped, like the check-ins tab."""
    W, H = fig.get_size_inches() * fig.dpi
    ax.apply_aspect()  # a polar axes is squared inside its box at draw time; transData must see the final box
    alias = public_alias(ctx, names)
    pts = []
    for r, th, rad in marks:
        x, y = ax.transData.transform((th, rad))
        pts.append((float(x), float(H - y), r, th, rad))  # matplotlib's y runs up, image y runs down
    groups = []  # greedy, in drawing order: a dot joins the first group whose centre is close enough
    for x, y, r, th, rad in pts:
        for g in groups:
            if math.hypot(g["px"] - x, g["py"] - y) <= CLOCK_TIP_GROUP_PX:
                g["rows"].append(r); n = len(g["rows"])
                g["px"] += (x - g["px"]) / n; g["py"] += (y - g["py"]) / n
                break
        else:
            groups.append({"px": x, "py": y, "rows": [r], "o": (round(th, 6), rad)})
    out = []
    for g in groups:
        rows = sorted(g["rows"], key=lambda r: r["t"], reverse=True)
        items = []
        for r in rows[:CLOCK_TIP_MAX]:
            t = r["t"]
            items.append({"n": alias.get(r["name"], r["name"]), "c": colors.get(r["name"], CHECKIN_PALETTE[0]),
                          "t": f"{t:%m-%d %H:%M}" if t.year == now.year else f"{t:%Y-%m-%d %H:%M}", "z": utc_label(t),
                          "note": "" if ctx.privacy else (r.get("note") or ""),
                          "cats": [ctx.cat_label(c) for c in (r.get("cats") or [r.get("category")]) if c],
                          "k": "start" if r.get("phase") == "start" else "point", "ts": int(t.timestamp())})
        out.append({"x": round(100 * g["px"] / W, 3), "y": round(100 * g["py"] / H, 3), "items": items,
                    "more": max(0, len(rows) - CLOCK_TIP_MAX), "o": g["o"]})
    out.sort(key=lambda s: s.pop("o"))  # clockwise from 0:00, inner ring first (keyboard order)
    return {"w": int(round(W)), "h": int(round(H)), "spots": out}


def draw_clock(ctx, samples, cks, colors, path, title, since, now, zl, key="1d", mode="ot", dial="24"):
    import chart_hud  # trim_num / sfmt: one decimal only when needed
    plt = _plt(ctx)
    from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgba
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter
    T, L = ctx.theme, ctx.L
    cmap = LinearSegmentedColormap.from_list("hud", [T["under_color"], T["over_color"]])
    max_gap = max(2.0, 1.5 * ctx.interval / 60)
    auto = dial == "auto" or dial in AUTO_FILES  # 近 12 小時: the drawing of the half it resolves to, with its own left-dial title
    dial = dial[5:] if dial in AUTO_FILES else dial
    dial = resolve_dial(dial if dial in CLOCK_DIALS else "24", now)
    hrs = dial_hours(dial); S = len(hrs)  # slots on the dial; summaries count only the hours shown
    b, _n = hourly_rates(samples, since, max_gap)
    n = sum(len(b[h]) for h in hrs)
    avg = [(sum(b[h]) / len(b[h])) if b[h] else 0.0 for h in hrs]
    need = max(6, min(24, int(24 * 60 / ctx.interval)))
    if S < 24:
        need = max(3, need // 2)
    half = L["clock_hl"][dial] if S < 24 else ""
    periods = clock_periods(ctx, key, since, now, samples)
    series = []  # (label, colour, [24 values], has_data, is_current)
    if periods:
        cols = usage_palette.period_colors(T["palette"], T["is_light"], 8)
        for gi, (lab, a, e, cur) in enumerate(periods):
            gb, _gn = hourly_rates(samples, a, max_gap, e)
            series.append((lab, cols[gi % len(cols)], [(sum(gb[h]) / len(gb[h])) if gb[h] else 0.0 for h in hrs],
                           any(gb[h] for h in hrs), cur))
    fig = plt.figure(figsize=(7.2, 8.6), dpi=110, facecolor=T["bg_color"])
    if L["clock_eyebrow"]:
        fig.text(0.05, 0.972, L["clock_eyebrow"], color=T["accent_color"], fontsize=9.5, fontweight="bold", va="top")
    if S < 24:  # the title says what is drawn: the left dial covers 12 hours, the range dial is "… this week (AM)"
        if key != "1d":
            title = L["clock_tg"].format(t=title, h=L["clock_face"][dial])
        else:
            title = L["clock_1a"].format(h=L["clock_face"][dial]) if auto else L["clock_1h"][dial]
    title_t = fig.text(0.05, 0.94, title, color=T["text_color"], fontsize=18, fontweight="bold", va="center")
    st = since or (samples[0][0] if samples else now)
    if S < 24 and key == "1d":  # last 24 h ∩ this half: exactly 12 clock hours, as one or two stretches (never the 24 h range)
        span = " + ".join(_half_spans(L, st, now, dial))
        sub = span + ("" if ctx.privacy else f" ({zl})") + f" · n={n}"
    else:
        sub = f"{st:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M}" + ("" if ctx.privacy else f" ({zl})") + (f" · {half}" if half else "") + f" · n={n}"
    fig.text(0.05, 0.908, sub,
             color=T["dim"], fontsize=10, fontweight="bold", va="center")
    ax = fig.add_axes([0.14, 0.27, 0.72, 0.59], projection="polar", facecolor=T["card"])
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    w = 2 * math.pi / S; th = [(i + 0.5) * w for i in range(S)]
    vmax = max([max(avg), 1.0] + [max(v) for _, _, v, _, _ in series]); norm = Normalize(0, vmax)
    if not series:
        ax.bar(th, avg, width=w * 0.86, color=[cmap(norm(a)) for a in avg], edgecolor=T["border"], lw=0.6, alpha=0.95, zorder=3)
    elif mode == "sd":  # thin wedges side by side, one per period, oldest first (clockwise)
        g = len(series); ww = w * 0.86 / g
        for gi, (_, col, vals, _, _) in enumerate(series):
            ax.bar([i * w + w * 0.07 + (gi + 0.5) * ww for i in range(S)], vals, width=ww * 0.92, color=col, edgecolor=T["card"], lw=0.3, zorder=3)
    else:  # overlay: full-width bars per hour, longest drawn first so a shorter bar never hides a taller one
        for h in range(S):
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
    if S < 24:  # a traditional clock face: 12 at the top, then 1-11 clockwise
        ax.set_xticks([i * w for i in range(12)])
        ax.set_xticklabels([str(i or 12) for i in range(12)], color=T["text_color"], fontsize=12, fontweight="bold")
    else:
        ax.set_xticks([h * w for h in range(0, 24, 3)])
        ax.set_xticklabels([L["hour_fmt"].format(h=h) for h in range(0, 24, 3)], color=T["text_color"], fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{chart_hud.trim_num(v)}%")); ax.set_rlabel_position(7.5)
    ax.tick_params(axis="y", colors=T["dim"], labelsize=8); ax.grid(color=T["grid"], alpha=0.5, ls=":", lw=0.8)
    ax.spines["polar"].set_color(T["border"])
    ck = [r for r in cks if r["t"] <= now and (since is None or r["t"] >= since) and r["t"].hour in hrs]
    names = []
    for r in ck:
        if r["name"] not in names:
            names.append(r["name"])
    shown = names[:8]
    ax.set_ylim(0, vmax * (1.15 + 0.07 * len(shown)))
    if S < 24:  # 12-hour face: bars start at a small hub that carries the half (上午 / 下午), like a clock's AM / PM marker
        ax.set_rorigin(-vmax * (1.15 + 0.07 * len(shown)) * 0.2)
        ax.text(0.5, 0.5, L["clock_face"][dial], transform=ax.transAxes, color=T["text_color"], fontsize=12, fontweight="bold",
                ha="center", va="center", alpha=0.9, zorder=7, gid="clock_face")
    marks = []  # (check-in row, theta, r) of every dot drawn: the page's hover tooltips (clock_hotspots)
    for i, nm in enumerate(shown):
        rows = [r for r in ck if r["name"] == nm]
        tt = [_clock_theta(r["t"], dial) for r in rows]
        ax.scatter(tt, [vmax * (1.12 + 0.07 * i)] * len(tt), s=22, color=colors[nm], edgecolors=T["card"], linewidths=0.5, zorder=6,
                   label=(f"#{names.index(nm) + 1}" if ctx.privacy else nm))
        marks += [(r, a, vmax * (1.12 + 0.07 * i)) for r, a in zip(rows, tt)]
    _write_text(Path(path).with_suffix(".json"), json.dumps(clock_hotspots(ctx, fig, ax, marks, colors, names, now), ensure_ascii=False))
    if shown:
        leg = ax.legend(loc="lower left", bbox_to_anchor=(-0.16, -0.12), frameon=False, fontsize=8.5, labelcolor=T["text_color"],
                        title=L["ck_legend"].format(n=len(ck)), handletextpad=0.3)
        leg.get_title().set_color(T["dim"]); leg.get_title().set_fontsize(8.5)
    if not series:
        for h, a in enumerate(avg):
            if a > 0 and a >= vmax * 0.35:
                ax.text(th[h], a + vmax * 0.06, chart_hud.trim_num(a), color=T["text_color"], fontsize=8, ha="center", va="center", zorder=5)
    if n:
        top = max(range(S), key=lambda i: avg[i])
        summ = chart_hud.sfmt(L["clock_peak"], h=hrs[top], h2=hrs[top] + 1, v=float(avg[top]), n=n)
    else:
        summ = L["clock_none"]
    if half:  # say which half the peak / average / count describe
        summ = L["clock_hp"].format(h=half, s=summ)
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
        badge = fig.text(0.95, 0.94, L["collecting"].format(n=n, m=need), color=T["notice_color"], fontsize=11, fontweight="bold", ha="right", va="center",
                 bbox=dict(boxstyle="round,pad=0.35", facecolor=_blend(T["bg_color"], T["notice_color"], 0.12), edgecolor=T["notice_color"]))
        _clear_badge(fig, title_t, badge)
    fig.savefig(path, facecolor=T["bg_color"]); plt.close(fig)


def _clear_badge(fig, title_t, badge):
    """Keep a long (translated) clock title clear of the "collecting data" badge on its right: shrink the
    title a little (18 -> 16 pt at most); if it still runs into the badge, keep the title size and lift the
    badge to the eyebrow line instead (shrinking the title only if it is wider than the figure)."""
    r = fig.canvas.get_renderer()
    hits = lambda: title_t.get_window_extent(r).x1 > badge.get_window_extent(r).x0 - 6
    fs0 = fs = title_t.get_fontsize()
    while hits() and fs > fs0 - 2:
        fs -= 1
        title_t.set_fontsize(fs)
    if hits():
        title_t.set_fontsize(fs0)
        badge.set_y(0.976)
        fs = fs0
        while title_t.get_window_extent(r).x1 > fig.bbox.width * 0.97 and fs > 12:
            fs -= 1
            title_t.set_fontsize(fs)


CLOCK_JSON_RE = re.compile(r"^clock-(1d|[a-z0-9]{1,4}-(ot|os|sd))((?:-auto)?-am|(?:-auto)?-pm)?\.json$")  # check-in dot tooltips (clock_hotspots)
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


def clock_file(key, mode=None, dial="24"):
    sfx = f"-{dial}" if dial in ("am", "pm") + AUTO_FILES else ""
    return f"clock-1d{sfx}.png" if key == "1d" else f"clock-{key}-{mode or CLOCK_MODES[0]}{sfx}.png"


def _draw_clocks(ctx, names, now, zl, s, ck, items=(("1d", None), ("wk", "ot"))):
    cks, _, colors = ck
    for name, d in _palette_dirs(ctx, names):
        d.mkdir(parents=True, exist_ok=True)
        for key, mode, *dl in items:
            dial = dl[0] if dl else "24"
            _with_palette(ctx, name, lambda: draw_clock(ctx, s, cks, colors, d / clock_file(key, mode, dial), _clock_title(ctx, key),
                                                          _clock_since(ctx, key, now), now, zl, key, mode or "ot", dial))


def render_clock(ctx):
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    now, zl, s, ck = _clock_inputs(ctx)
    names = _cycle_palettes(ctx)
    _draw_clocks(ctx, names, now, zl, s, ck)
    _publish_default(ctx, ["clock-1d.png", clock_file("wk"), "clock-1d.json", clock_file("wk")[:-4] + ".json"])
    for d in [ctx.hist_dir] + [p for _, p in _palette_dirs(ctx)]:
        for old in ("clock-7d.png", "clock-all.png", "clock-wk.png"):  # names used by earlier versions
            if (d / old).exists():
                (d / old).unlink()
    _new_gen(ctx, names)
    return {"ok": True, "samples": len(s), "files": [str(ctx.hist_dir / "clock-1d.png"), str(ctx.hist_dir / clock_file("wk"))]}


def render_clock_range(ctx, name, key, mode="ot", dial="24"):
    """history/pal/<name>/clock-<key>-<mode>[-am|-pm].png for the clock tab's range / layout / 時制 menus (with am / pm,
    the 24-hour dial on the left is drawn for that half too); cached until the next refresh."""
    d = ctx.hist_dir / "pal" / name
    gen = _read_text(ctx.hist_dir / GEN_FILE)
    now0 = ctx.now()
    want = [(key, mode, dial_for(dial, now0, key))] + ([("1d", None, dial_for(dial, now0, "1d"))] if dial != "24" and key != "1d" else [])
    todo = []
    for k, m, dl in want:
        fn = clock_file(k, m, dl)
        if not (gen and (d / fn).exists() and _read_text(d / f"{GEN_FILE}-{fn[:-4]}") == gen):
            todo.append((k, m, dl))
    if not todo:
        return False
    now, zl, s, ck = _clock_inputs(ctx)
    _draw_clocks(ctx, [name], now, zl, s, ck, items=tuple(todo))
    if gen:
        for k, m, dl in todo:
            _write_text(d / f"{GEN_FILE}-{clock_file(k, m, dl)[:-4]}", gen)
    return True


WEEK_VIEW_FILES = ("week-current.png", "week-daily.png", "week-hourly.png")  # 本週總覽 / 每日增量 per day / per hour
SEL_SPRITES = tuple(f"week-{v}-{k}.png" for v in ("daily", "hourly") for k in ("off", "on"))  # linked selection (chart_hud.write_sel_sprites)
HOURLY_ROWS_FILE = "week-hourly-rows.png"  # every hour row of the per-hour list as one tall picture the page scrolls (chart_hud.write_hourly_strip)


def render_current_week(ctx, nowa, names=None):
    """'This week' tab: the current week's daily cumulative chart (chart_hud) for the given palettes."""
    out = ctx.hist_dir / "week-current.png"
    publish = names is None or ctx.pal_key in names
    try:
        ws = _period_start(ctx, nowa)
        f = ctx.data_dir / f"week-{ws.astimezone(ctx.tz):%Y-%m-%d}.csv"
        rows = [r for r in csv.DictReader(f.open(encoding="utf-8"))] if f.exists() else []
        if not rows:
            for d in ([ctx.hist_dir] if publish else []) + [dd for _, dd in _palette_dirs(ctx, names)]:
                for fn in WEEK_VIEW_FILES + SEL_SPRITES + (HOURLY_ROWS_FILE, "week-current.json"):
                    if (d / fn).exists():
                        (d / fn).unlink()
            return False
        import chart_hud
        # hourly readings narrow a mid-week reset's estimated time (usage_rate.py)
        samples = usage_checkins.load_samples(str(ctx.data_dir / "samples.csv"))
        for name, d in _palette_dirs(ctx, names):
            d.mkdir(parents=True, exist_ok=True)
            base, pat = usage_palette.split_key(strip_offset(name))
            hcfg = {"lang": "numeric" if ctx.privacy else ctx.lang, "privacy": ctx.privacy, "pace": ctx.pace,
                    "palette": base, "reset_hm": f"{ws.astimezone(ctx.tz):%H:%M}",
                    "accent_color": _theme_explicit_value(ctx.theme_path, "accent"),
                    "patterns": pat or bool(_theme_file(ctx.theme_path).get("patterns")),
                    "display_offset": ctx.display_offset, "reset_at": shown_reset(ctx), "tz": ctx.tz_spec,
                    "samples": samples}
            # 本週總覽 (default) + 每日增量 per day / per hour: same header and cards, the page switches between them
            sels = {}
            for view, fn in zip(("daily", "hourly"), WEEK_VIEW_FILES[1:]):
                vm = chart_hud.render(rows, str(d / fn), f"{ws.astimezone(ctx.tz):%Y-%m-%d}", dict(hcfg, view=view, sel_sprites=True))
                if (vm or {}).get("sel"):
                    sels[view] = vm["sel"]
                else:  # no selection this time (e.g. too few hourly readings): drop sprites left by an earlier render
                    for k in ("off", "on"):
                        (d / f"week-{view}-{k}.png").unlink(missing_ok=True)
                    if view == "hourly":
                        (d / HOURLY_ROWS_FILE).unlink(missing_ok=True)
            meta = chart_hud.render(rows, str(d / "week-current.png"), f"{ws.astimezone(ctx.tz):%Y-%m-%d}", dict(hcfg, view="week"))
            if sels:  # where the 每日增量 days / hours are on each picture, and which one is selected when the page opens
                meta["sel"] = sels
            # gauge geometry/colours for the page's animated overlay (the PNG stays the static export)
            _write_text(d / "week-current.json", json.dumps(meta or {}, ensure_ascii=False))
        if publish:
            _publish_default(ctx, list(WEEK_VIEW_FILES) + list(SEL_SPRITES) + [HOURLY_ROWS_FILE, "week-current.json"])
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


def rewrite_page(ctx):
    """Rebuild only index.html (no chart drawing), e.g. after a page-only switch such as the gauge animation."""
    ctx.hist_dir.mkdir(parents=True, exist_ok=True)
    nowa, zl, now, sums = _history_inputs(ctx)
    write_html(ctx, sums, now, nowa, zl, _read_text(ctx.hist_dir / GEN_FILE))
    return {"ok": True, "html": str(ctx.hist_dir / "index.html")}


def _palette_arg(v):
    v = str(v or "").strip()
    if usage_palette.known(v) or v.lower() == usage_palette.CUSTOM or usage_palette.is_custom(v):
        return v.lower() if usage_palette.is_custom(v) else v
    raise argparse.ArgumentTypeError(f"unknown palette '{v}'")


def palette_name_ok(name):
    """A preset name or a custom key ('custom-<under><over>', both #RRGGBB as 12 hex digits)."""
    return usage_palette.known(name) or usage_palette.is_custom(name)


CUSTOM_KEEP = 4  # custom colour pairs whose images stay cached (the configured one is always kept)


def _prune_custom(ctx, keep):
    """Every picked custom pair gets its own image folder: keep the newest few (plus the configured palette's)."""
    import shutil
    root = ctx.hist_dir / "pal"
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir() and d.name.startswith(usage_palette.CUSTOM + "-")
                       and d.name not in (keep, ctx.pal_key)), key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return
    for d in dirs[CUSTOM_KEEP - 1:]:
        shutil.rmtree(d, ignore_errors=True)


def render_palette(ctx, name, clock=None, mode=None, patterns=False, dial=None):
    """Draw one palette's full image set into history/pal/<key>/ when the page switches to it (plus, with
    clock=<range>, that clock-tab range); key = name, or name + '-pat' with patterns. Cached: nothing is drawn
    while the files match the latest render."""
    if not palette_name_ok(name):
        return {"ok": False, "error": "unknown palette"}
    if usage_palette.is_custom(name):
        name = usage_palette.normalize(name)
        _prune_custom(ctx, keep=ctx.dir_key(name, patterns))
    name = ctx.dir_key(name, patterns)
    if clock is not None and clock not in CLOCK_RANGES:
        return {"ok": False, "error": "unknown clock range"}
    if mode is not None and mode not in CLOCK_MODES:
        return {"ok": False, "error": "unknown clock layout"}
    if dial is not None and dial not in CLOCK_DIALS:
        return {"ok": False, "error": "unknown clock dial"}
    raw = dial or "24"
    dial = resolve_dial(raw, ctx.now())  # "auto" is today's half; the page learns which from the reply
    extra = clock is not None and (clock, mode or "ot", dial) != ("wk", "ot", "24")
    d = ctx.hist_dir / "pal" / name
    gen = _read_text(ctx.hist_dir / GEN_FILE)
    t0 = time.perf_counter()
    if gen and _read_text(d / GEN_FILE) == gen:
        drew = extra and render_clock_range(ctx, name, clock, mode or "ot", raw)
        out = {"ok": True, "name": name, "v": gen, "cached": not drew, "dial": dial}
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
        render_clock_range(ctx, name, clock, mode or "ot", raw)
    return {"ok": True, "name": name, "v": gen, "cached": False, "dial": dial, "seconds": round(time.perf_counter() - t0, 2)}


# Ledger status bar + browser notifications (Web Notifications API; permission is asked from a button,
# never on load). When served, polls /api/ledger every 3 minutes; each alert id (one per day) fires once
# (seen ids kept in localStorage).
LEDGER_JS = "(function(){var $=id=>document.getElementById(id),served=/^https?:$/.test(location.protocol),led=null,S=JSON.parse($('ledgerstr').textContent),OFF=S.off||0,adj=function(p){return p==null?p:Math.max(0,Math.round((+p+OFF)*10)/10);};\ntry{led=JSON.parse($('ledgerdata').textContent);}catch(e){}\nfunction f(t,o){return String(t).replace(/\\{(\\w+)\\}/g,function(_,k){return o[k]!=null?o[k]:'';});}\nfunction el(t,x,c){var e=document.createElement(t);if(x!=null)e.textContent=x;if(c)e.className=c;return e;}\nfunction parts(iso){if(!iso)return null;var d=new Date(iso);if(isNaN(d))return null;var m=/([+-])(\\d\\d):(\\d\\d)$/.exec(iso);\nvar off=m?(m[1]=='-'?-1:1)*(+m[2]*60+ +m[3]):0,w=new Date(d.getTime()+off*60000),p=n=>String(n).padStart(2,'0');\nreturn {wd:S.wd[(w.getUTCDay()+6)%7],t:p(w.getUTCMonth()+1)+'-'+p(w.getUTCDate())+' '+p(w.getUTCHours())+':'+p(w.getUTCMinutes()),\nhm:p(w.getUTCHours())+':'+p(w.getUTCMinutes()),date:RD(w,S.dl),z:m?'UTC'+m[1]+(+m[2])+(+m[3]?':'+m[3]:''):'UTC'};}\nfunction render(L){var box=$('ledger');box.textContent='';if(!L){box.appendChild(el('div',S.none,'info'));return;}\nvar a=parts(L.asOf),at=a?a.t+(S.showz?' ('+a.z+')':''):'?',r=parts(L.resetsAt);\nif(L.status!=='ok'){var w=el('div',null,'warn'),lg=L.lastGood,lp=lg&&parts(lg.asOf);w.appendChild(el('b',S.unavailable));\nw.appendChild(document.createTextNode(' · '+f(S.tried,{t:at})+(L.error&&!S.privacy?' · '+f(S.reason,{e:L.error}):'')+' · '+\n(lg&&lg.weeklyPercentUsed!=null?f(S.last_good,{p:adj(lg.weeklyPercentUsed),t:lp?lp.t:'?'}):S.no_last)+' · '+S.reset_unknown));box.appendChild(w);}\nelse{var i=el('div',null,'info');i.appendChild(el('b',f(S.now,{p:adj(L.weeklyPercentUsed)})));if(S.otag)i.appendChild(el('span',' '+S.otag,'otag'));\ni.appendChild(document.createTextNode(' · '+(r?f(S.reset,{wd:r.wd,hm:r.hm,date:r.date,z:r.z}):S.reset_unknown)+' · '+f(S.read_at,{t:at})));box.appendChild(i);}\nvar bs=el('div',null,'badges');\nif(L.spike&&L.spike.flag){var s=parts(L.spike.since);bs.appendChild(el('span',f(S.spike,{p:L.spike.points,t:s?s.t:'?'}),'badge'));}\nif(L.overBudget&&L.overBudget.flag)bs.appendChild(el('span',f(S.budget,{p:adj(L.weeklyPercentUsed),th:L.overBudget.threshold}),'badge'));\nif(L.status!=='ok'&&L.consecutiveFailures)bs.appendChild(el('span',f(S.fails,{n:L.consecutiveFailures}),'badge note'));\nvar age=a?(Date.now()-new Date(L.asOf).getTime())/3600000:null;\nif(age!=null&&age>(L.staleAfterHours||6))bs.appendChild(el('span',f(S.stale,{h:Math.floor(age)}),'badge note'));\nif(bs.children.length)box.appendChild(bs);}\nrender(led);\nwindow.usageLedgerOffset=function(o){OFF=o;S.otag=o?'('+(o>0?'+':'\\u2212')+Math.abs(o)+')':'';render(led);};\nvar nb=$('notifybtn'),KEY='usage.alertsSeen';\nfunction seen(){try{return JSON.parse(localStorage.getItem(KEY)||'[]');}catch(e){return [];}}\nfunction fire(L){if(!L||!L.alerts||!('Notification' in window)||Notification.permission!=='granted')return;\nvar s=seen(),ch=false;L.alerts.forEach(function(x){if(s.indexOf(x.id)>=0)return;try{new Notification(x.title,{body:x.message,tag:x.id});}catch(e){}s.push(x.id);ch=true;});\nif(ch){try{localStorage.setItem(KEY,JSON.stringify(s.slice(-60)));}catch(e){}}}\nfunction poll(){fetch('/api/ledger',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(L=>{if(L){led=L;render(L);fire(L);}}).catch(()=>{});}\nif(served){if('Notification' in window&&Notification.permission==='default'){nb.hidden=false;\nnb.onclick=function(){Notification.requestPermission().then(function(p){nb.hidden=(p!=='default');if(p==='granted')fire(led);});};}\nfire(led);poll();setInterval(poll,180000);}})();\n"
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
           "spike": "▲ +{p}% in 24h (since {t})", "budget": "● {p}% (≥ {th}%)", "fails": "{n} failed reads in a row",
           "stale": "data is {h}h old", "none": "No usage history details yet (available after the first background reading)", "notify": "Enable desktop notifications", "showz": True},
    "zh-TW": {"wd": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"], "now": "目前 {p}%", "reset": "重置 {date}（{z}）", "dl": "zh-TW",
              "reset_unknown": "重置時間未知", "read_at": "讀取於 {t}", "unavailable": "⚠ 無法取得目前用量", "tried": "嘗試於 {t}",
              "reason": "原因：{e}", "last_good": "最後成功 {p}%（{t}）", "no_last": "尚無成功紀錄",
              "spike": "▲ 24 小時內用量激增 +{p}%（自 {t}）", "budget": "● 已達 {p}%（門檻 {th}%）", "fails": "連續 {n} 次無法取得",
              "stale": "資料已 {h} 小時未更新", "none": "尚無歷史明細（首次背景記錄後啟用）", "notify": "開啟桌面通知", "showz": True},
    "numeric": {"wd": ["1", "2", "3", "4", "5", "6", "7"], "now": "{p}%", "reset": "↻ {date}", "dl": "numeric", "reset_unknown": "↻ ?", "read_at": "@ {t}",
                "unavailable": "∅", "tried": "@ {t}", "reason": "", "last_good": "✓ {p}% @ {t}", "no_last": "✓ —",
                "spike": "▲ +{p}% / 24h", "budget": "● {p}% ≥ {th}%", "fails": "∅ ×{n}", "stale": "⌛ {h}h", "none": "∅",
                "notify": "🔔", "showz": False, "privacy": True},
}
LEDGER_STR["zh-CN"] = dict(LEDGER_STR["zh-TW"], wd=["周一", "周二", "周三", "周四", "周五", "周六", "周日"], dl="zh-CN",
                           now="目前 {p}%", reset="重置 {date}（{z}）", reset_unknown="重置时间未知", read_at="读取于 {t}",
                           unavailable="⚠ 无法获取当前用量", tried="尝试于 {t}", reason="原因：{e}", last_good="最后成功 {p}%（{t}）",
                           no_last="尚无成功记录", spike="▲ 24 小时内用量激增 +{p}%（自 {t}）", budget="● 已达 {p}%（阈值 {th}%）",
                           fails="连续 {n} 次无法获取", stale="数据已 {h} 小时未更新", none="尚无历史明细（首次后台记录后启用）",
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
           "failed": "The update did not start: {e}", "auto": "Auto-check for updates",
           "notes": "What's new:", "back": "Roll back to previous version",
           "back_confirm": "Go back to the version you had before the last update ({v}) and restart the dashboard? Your data and settings are kept. You can update again later.",
           "back_running": "Rolling back… (about 1–3 minutes; the page reloads by itself)",
           "back_failed": "Could not roll back: {e}",
           "back_states": {"no_previous": "No previous version was saved.", "already_there": "This is already the previous version.",
                           "local_changes": "Program files in this folder were changed; roll back by hand (see the README) so your changes are not lost.",
                           "not_ancestor": "The saved version is not part of this folder's history; roll back by hand (see the README).",
                           "missing_commit": "The saved version is no longer in this folder; roll back by hand (see the README).",
                           "wrong_branch": "This folder is not on the main branch.", "not_git": "This folder was not installed with git clone."}},
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
              "failed": "更新沒有開始：{e}", "auto": "自動檢查更新",
              "notes": "這次更新的內容：", "back": "回到上一版",
              "back_confirm": "要回到上次更新前的版本（{v}）並重新啟動儀表板嗎？你的資料和設定都會保留，之後還可以再更新。",
              "back_running": "正在回到上一版…（大約 1–3 分鐘，完成後頁面會自動重新整理）",
              "back_failed": "無法回到上一版：{e}",
              "back_states": {"no_previous": "沒有存到上一版的紀錄。", "already_there": "目前已經是上一版。",
                              "local_changes": "這個資料夾裡的程式檔被改過；為了不蓋掉你的修改，請照 README 手動回復。",
                              "not_ancestor": "存下來的版本不在這個資料夾的歷史裡，請照 README 手動回復。",
                              "missing_commit": "這個資料夾裡已經找不到存下來的版本，請照 README 手動回復。",
                              "wrong_branch": "這個資料夾目前不在 main 分支。", "not_git": "這個資料夾不是用 git clone 安裝的。"}},
    "numeric": {"btn": "⇪", "go": "⇪ ↻", "checking": "…", "new": "⇪ {h} (−{n})", "up_to_date": "✓",
                "not_git": "× zip", "no_git": "× git", "no_remote": "× git", "fetch_failed": "× ⇅", "local_changes": "× ✎",
                "diverged": "× ⑂", "wrong_branch": "× ⑂", "git_error": "×", "cooldown": "⌛ {s}s", "busy": "↻ …",
                "confirm": "⇪ ↻ ?", "updating": "↻ …", "slow": "× log", "failed": "×", "auto": "⇪ ⏱", "privacy": True,
                "notes": "", "back": "⟲", "back_confirm": "⟲ {v} ?", "back_running": "⟲ …", "back_failed": "× {e}", "back_states": {}},
}
for _lg in LATIN_LANGS:
    MANUAL_STR[_lg] = _over_en(MANUAL_STR["en"], l10n.MANUAL[_lg])
    LEDGER_STR[_lg] = _over_en(LEDGER_STR["en"], l10n.LEDGER[_lg])
    UPDATE_STR[_lg] = _over_en(UPDATE_STR["en"], l10n.UPDATE[_lg])
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
if(window.usageApplying)usageApplying(true);if(window.usageLedgerOffset)usageLedgerOffset(v);if(window.usageGaugePreview)usageGaugePreview(v);
fetch('/api/display-offset',{method:'POST',headers:{'X-Usage-Token':T,'Content-Type':'application/json'},body:JSON.stringify({offset:v})})
.then(function(r){return r.json();}).then(function(j){if(j&&j.ok)location.reload();else fail();}).catch(fail);};
function fail(){s.disabled=false;if(window.usageApplying)usageApplying(false);}})();"""
# Language / time zone menus: same as the offset menu (browser: usage.lang / usage.tz; server: POST /api/lang,
# /api/tz, saved as "lang" / "tz" in usage_config.json or page_settings.json); the page reloads redrawn.
def _app_version():
    try:
        v = (HERE / "VERSION").read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return v if re.fullmatch(r"\d+(\.\d+){1,3}", v) else ""
    except (OSError, IndexError):
        return ""


APP_VERSION = _app_version()  # VERSION file (also the CHANGELOG headings)

# Link to the project page at the end of toolbar row 2. The mark is the Octicons "mark-github" 16px icon
# (MIT, (c) GitHub), inlined unmodified; per GitHub's logo policy it is shown in white/grey only, icon only,
# and only links to the repository. See "Third-party notices" in README.md.
REPO_URL = "https://github.com/yuehzhan/grokbot-usage-checkin"
GH_MARK_PATH = ("M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062"
                ".625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 "
                "1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 "
                "1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 "
                "0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 "
                "0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328"
                ".453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656")
GH_LABEL = {"zh-TW": "前往 GitHub 專案頁", "zh-CN": "前往 GitHub 项目页", "en": "View on GitHub",
            "ja": "GitHub リポジトリを開く", "ko": "GitHub 저장소 열기", **l10n.GH}


def gh_css(dark=True):
    """White/grey only: muted by default, full white (dark page) or dark grey (light page) on hover/focus."""
    rest, hover = ("rgba(255,255,255,0.6)", "#FFFFFF") if dark else ("#6E7781", "#424A53")
    return (" .ghlink{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;"
            f"color:{rest};text-decoration:none;transition:color .15s}}"
            f" .ghlink:hover,.ghlink:focus-visible{{color:{hover}}} .ghlink:focus-visible{{outline:2px solid currentColor;outline-offset:1px}}"
            " .ghlink svg{display:block;width:16px;height:16px;fill:currentColor}")


def gh_link_html(lang):
    """Icon-only link to the repository (no text next to the mark); not shown in the numeric/privacy view."""
    if lang == "numeric":
        return ""
    t = html.escape(GH_LABEL.get(lang) or GH_LABEL["en"])
    return (f'<a class="ghlink" id="ghlink" href="{REPO_URL}" target="_blank" rel="noopener noreferrer" title="{t}" aria-label="{t}">'
            f'<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="{GH_MARK_PATH}"/></svg></a>')


PICK_JS = r"""(function(){var T=(document.querySelector('meta[name="usage-token"]')||{}).content||'',served=/^https?:$/.test(location.protocol);
[].forEach.call(document.querySelectorAll('select[data-api]'),function(s){var K=s.dataset.k,F=s.dataset.f,live=!!s.dataset.live,A=window.usageApplying||function(){};
function apply(){if(live&&s.id==='animsel'&&window.usageGaugeMode)usageGaugeMode(s.value);if(live&&s.id==='mascotsel'&&window.usageMascot)usageMascot(s.value);}
if(s.dataset.locked){s.disabled=true;return;}
if(!served){if(!live||s.dataset.srv){try{localStorage.setItem(K,s.value);}catch(e){}s.disabled=true;return;}
try{var ls=localStorage.getItem(K);if(ls&&[].some.call(s.options,function(o){return o.value==ls;}))s.value=ls;}catch(e){}
s.onchange=function(){try{localStorage.setItem(K,s.value);}catch(e){}apply();};return;}
try{localStorage.setItem(K,s.value);}catch(e){}
s.onchange=function(){var v=s.value,b={};b[F]=v;s.disabled=true;try{localStorage.setItem(K,v);}catch(e){}if(!live)(window.usageApplying||A)(true);
fetch(s.dataset.api,{method:'POST',headers:{'X-Usage-Token':T,'Content-Type':'application/json'},body:JSON.stringify(b)})
.then(function(r){return r.json();}).then(function(j){if(j&&j.ok&&!live){location.reload();return;}s.disabled=false;if(j&&j.ok)apply();else (window.usageApplying||A)(false);})
.catch(function(){s.disabled=false;(window.usageApplying||A)(false);});};});})();"""
# Gauge "rev up" overlay (served page only; the PNG is the static export): an SVG laid over the hero gauge of
# week-current.png using the geometry chart_hud writes to week-current.json. Once: on open / refresh / offset change
# the ring sweeps 0 -> value with a small overshoot (~1 s) while the big % counts up and the progress bar fills, then
# the overlay fades away. Continuous: after the sweep the ring's leading edge idles (tiny CSS-transform jitter, no
# translucent glow block); the numbers never move, and the CSS animations pause while the tab is hidden. prefers-reduced-motion:
# no sweep and no jitter; an explicit Continuous keeps the leading edge still.
GAUGE_JS = r"""(function(){var $=function(i){return document.getElementById(i);},G={};try{G=JSON.parse($('gaugedata').textContent)||{};}catch(e){}
var NS='http://www.w3.org/2000/svg',served=/^https?:$/.test(location.protocol),K='usage.anim',M=G.meta,mode=G.mode||'once',
mq=window.matchMedia?matchMedia('(prefers-reduced-motion: reduce)'):null,RM=!!(mq&&mq.matches),
img=document.querySelector("#p-wk img[data-f='week-current.png']"),svg=null,raf=0,shown=M?M.pct:0;
window.usageApplying=function(on){var a=$('applying');if(a)a.hidden=!on;document.documentElement.classList.toggle('applying',!!on);};
if(!served&&!G.locked){try{var ls=localStorage.getItem(K);if(['off','once','continuous'].indexOf(ls)>=0)mode=ls;}catch(e){}}
function mk(t,a,p){var e=document.createElementNS(NS,t);for(var k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}
function C(){return 2*Math.PI*0.89*M.ring.u;}
function col(v){return v>M.base?M.col.over:M.col.under;}
function pt(r,ratio){var a=(-90+360*ratio)*Math.PI/180;return [M.ring.cx+r*M.ring.u*Math.cos(a),M.ring.cy+r*M.ring.u*Math.sin(a)];}
var E={};
function build(){if(!img||!M||!M.ring)return false;if(svg)return true;var R=M.ring,u=R.u,w=img.parentNode;
if(!w.classList.contains('gwrap')){w=document.createElement('div');w.className='gwrap';img.parentNode.insertBefore(w,img);w.appendChild(img);}
svg=mk('svg',{viewBox:'0 0 '+M.w+' '+M.h,'aria-hidden':'true',class:'gsvg'},w);
E.cover=mk('g',{class:'gcover'},svg);
mk('circle',{cx:R.cx,cy:R.cy,r:0.8975*u,fill:'none',stroke:M.col.fill,'stroke-width':0.30*u},E.cover);
mk('circle',{cx:R.cx,cy:R.cy,r:0.89*u,fill:'none',stroke:M.col.track,'stroke-width':0.22*u},E.cover);
var rot='rotate(-90 '+R.cx+' '+R.cy+')';
E.glow=mk('circle',{cx:R.cx,cy:R.cy,r:0.90*u,fill:'none','stroke-width':0.28*u,opacity:.18,transform:rot},E.cover);
E.arc=mk('circle',{cx:R.cx,cy:R.cy,r:0.89*u,fill:'none','stroke-width':0.22*u,opacity:.95,transform:rot},E.cover);
var b=pt(0.78,Math.min(1,Math.max(0,M.base/100))),b2=pt(1.10,Math.min(1,Math.max(0,M.base/100)));
mk('line',{x1:b[0],y1:b[1],x2:b2[0],y2:b2[1],stroke:M.col.base,'stroke-width':6.25},E.cover);
var N=M.num,cy=N.y+N.h/2;/* cover the PNG number's whole drawn box (from the meta, so any language/font) plus the counting text's em box */var nt=Math.min(N.y,cy-0.42*N.size)-3,nb=Math.max(N.y+N.h,cy+0.42*N.size)+3;mk('rect',{x:N.x-2,y:nt,width:N.cover_w,height:nb-nt,fill:M.col.fill,class:'gnumcover'},E.cover);
E.num=mk('text',{x:N.x,y:cy,'dominant-baseline':'central','font-size':N.size,'font-weight':700,class:'gnum'},E.cover);
E.tag=mk('tspan',{'font-size':20.8,dx:10,fill:M.col.dim},null);
var B=M.bar;mk('rect',{x:B.x-1,y:B.y-6,width:B.w+2,height:B.h+12,fill:M.col.fill},E.cover);
mk('rect',{x:B.x,y:B.y,width:B.w,height:B.h,fill:M.col.border},E.cover);
E.proj=mk('rect',{x:B.x,y:B.y,width:0,height:B.h,opacity:.30},E.cover);E.used=mk('rect',{x:B.x,y:B.y,width:0,height:B.h,opacity:.95},E.cover);
var bx=B.x+B.w*B.base_r;mk('line',{x1:bx,y1:B.y-5.2,x2:bx,y2:B.y+B.h+5.2,stroke:M.col.base,'stroke-width':4.6},E.cover);
E.idle=mk('g',{class:'gidle'},svg);E.jit=mk('g',{class:'gjit'},E.idle);
E.tip=mk('path',{fill:'none','stroke-width':0.22*u},E.jit);
document.addEventListener('visibilitychange',function(){svg.classList.toggle('paused',document.hidden);});
return true;}
function arcd(r,a0,a1){var p0=pt(r,a0),p1=pt(r,a1),lg=(a1-a0)>0.5?1:0;return 'M'+p0[0]+' '+p0[1]+' A'+r*M.ring.u+' '+r*M.ring.u+' 0 '+lg+' 1 '+p1[0]+' '+p1[1];}
function draw(v,num){var r=Math.min(1,Math.max(0,v/100)),c=col(num),cc=C();
[E.glow,E.arc].forEach(function(e){e.setAttribute('stroke',c);e.setAttribute('stroke-dasharray',(r*cc)+' '+cc);});
E.num.setAttribute('fill',c);E.num.textContent=Math.round(num)+'%';var tg=G.otag||'';if(tg){E.tag.textContent=tg;E.num.appendChild(E.tag);}
E.used.setAttribute('fill',c);E.used.setAttribute('width',M.bar.w*r);
var pr=Math.max(r,Math.min(1,M.bar.proj_r*(M.pct>0?num/M.pct:1)));E.proj.setAttribute('fill',c);E.proj.setAttribute('x',M.bar.x+M.bar.w*r);E.proj.setAttribute('width',M.bar.w*(pr-r));}
function idle(v){var r=Math.min(1,Math.max(0,v/100)),c=col(v),R=M.ring;
E.tip.setAttribute('d',arcd(0.89,Math.max(0,r-0.012),RM?r:r+0.006));E.tip.setAttribute('stroke',c);
E.jit.style.transformOrigin=R.cx+'px '+R.cy+'px';
svg.classList.toggle('still',RM);E.idle.style.display='';}
function ease(t){var s=1.2;t-=1;return t*t*((s+1)*t+s)+1;}
function sweep(from,to,ms,done){cancelAnimationFrame(raf);var t0=null;
function f(ts){if(t0==null)t0=ts;var t=Math.min(1,(ts-t0)/ms),e=ease(t),v=from+(to-from)*e,n=from+(to-from)*Math.min(1,e);
draw(v,to>=from?Math.min(n,to):Math.max(n,to));if(t<1)raf=requestAnimationFrame(f);else{draw(to,to);done&&done();}}
raf=requestAnimationFrame(f);}
function finish(v){if(mode==='continuous'){E.cover.style.display='none';idle(v);svg.style.opacity='';}
else{svg.classList.add('fade');setTimeout(function(){if(svg.classList.contains('fade')){svg.style.display='none';}},400);}}
function reset(){svg.style.display='';svg.classList.remove('fade');E.idle.style.display='none';E.cover.style.display='';}
function play(){if(!build())return;reset();if(mode==='off'){svg.style.display='none';return;}
if(RM){if(mode==='continuous'){E.cover.style.display='none';idle(shown);}else svg.style.display='none';return;}
draw(0,0);sweep(0,shown,1000,function(){finish(shown);});}
window.usageGaugeMode=function(m){mode=m;if(!served){try{localStorage.setItem(K,m);}catch(e){}}play();};
window.usageGaugePreview=function(off){if(!build())return;var v=Math.max(0,Math.round((M.raw+off)*10)/10),from=shown;shown=v;
G.otag=off?'('+(off>0?'+':'\u2212')+Math.abs(off)+')':'';reset();
if(RM||mode==='off'){draw(v,v);return;}sweep(from,v,450,function(){});};
if(img&&M&&served&&window.MutationObserver){new MutationObserver(function(){var s=img.getAttribute('src')||'';
if(/^data:/.test(s)||!/week-current\.png/.test(s))return;fetch(s.replace(/\.png(\?|$)/,'.json$1'),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;})
.then(function(j){if(j&&j.col&&M){M.col=j.col;if(svg&&E.idle.style.display!=='none')idle(shown);}}).catch(function(){});}).observe(img,{attributes:true,attributeFilter:['src']});}
if(mq&&mq.addEventListener)mq.addEventListener('change',function(e){RM=e.matches;play();});
play();})();"""
GAUGE_CSS = (".gwrap{position:relative} .gsvg{position:absolute;left:1px;top:1px;width:100%;height:auto;pointer-events:none;transition:opacity .35s}"
             " .gsvg.fade{opacity:0} .gjit{animation:gjit .46s linear infinite} .gbreath{opacity:.22;animation:gbr 2.6s ease-in-out infinite}"
             " .gsvg.still .gjit,.gsvg.still .gbreath{animation:none} .gsvg.still .gbreath{opacity:.4} .gsvg.paused *{animation-play-state:paused}"
             " @keyframes gjit{0%{transform:rotate(0)}18%{transform:rotate(.9deg)}36%{transform:rotate(-.35deg)}55%{transform:rotate(1.3deg)}"
             "74%{transform:rotate(.15deg)}88%{transform:rotate(.7deg)}100%{transform:rotate(0)}}"
             " @keyframes gbr{0%,100%{opacity:.18}50%{opacity:.55}}"
             " @media (prefers-reduced-motion: reduce){.gjit,.gbreath{animation:none!important}}"
             " html.applying section img{opacity:.55;transition:opacity .2s} .applying-msg{color:var(--accent);font-size:13px;font-weight:700}"
             " .upd{flex:1 1 100%;display:flex;flex-direction:column;align-items:stretch;gap:8px}"
             # two deliberate rows (settings / actions); each label+control is one unit that never shrinks or wraps
             # inside, so on a narrow window whole units move to the next line instead of characters stacking
             " .upd .trow{display:flex;flex-wrap:wrap;align-items:center;gap:8px 7px;min-width:0}"
             " .upd .trow>*{flex:0 0 auto;white-space:nowrap} .upd .trow>script{display:none}"
             " .upd label.pal{display:inline-flex;align-items:center;gap:4px} .upd .psel{gap:4px} .upd .pbtn{padding:4px 8px} .upd .btn{white-space:nowrap}"
             " .upd .trow{font-size:12.5px} .upd .trow select{font:inherit} #langsel{max-width:180px} #tzsel{max-width:170px} @media (max-width:1100px){.wrap{padding-left:14px;padding-right:14px}} .upd .btn{padding:5px 11px} #rmsg:empty{display:none}"
             # Check for updates + Roll back: one unit, so the roll-back button never wraps onto a line of its own
             " .upd .ugrp{display:inline-flex;align-items:center;gap:7px} .upd .ugrp:not(:has(>:not([hidden]))){display:none}"
             # Auto-check switch + GitHub icon: one unit, so the icon never wraps onto a line of its own
             " .upd .agrp{display:inline-flex;align-items:center;gap:7px} .upd .agrp:not(:has(>:not([hidden]))){display:none}"
             " .upd [hidden]{display:none!important}")
# Clock tab: hover / tap tooltips for the check-in dots (the PNG is unchanged; clock_hotspots() writes the spots).
# Vanilla JS, no network beyond the page's own clock-*.json (served page, ranges not embedded). User text is only ever
# set with textContent. Mouse: hover shows; click pins. Touch: tap toggles, a tap outside closes. Keyboard: the dots of
# one dial are one Tab stop (roving tabindex); arrows move clockwise, Enter / Space pins, Escape closes.
CLOCKTIP_STR = {
    "en": {"point": "Check-in", "start": "Session start", "more": "+{n} more", "n": "{n} check-in(s)"},
    "zh-TW": {"point": "單次打卡", "start": "工作段開始", "more": "還有 {n} 筆", "n": "{n} 筆打卡"},
    "zh-CN": {"point": "单次打卡", "start": "工作段开始", "more": "还有 {n} 条", "n": "{n} 条打卡"},
    "numeric": {"point": "·", "start": "▶", "more": "+{n}", "n": "✓ {n}"},
}
for _lg in LATIN_LANGS:
    CLOCKTIP_STR[_lg] = _over_en(CLOCKTIP_STR["en"], l10n.CLOCKTIP[_lg])
CLOCKTIP_JS = r"""(function(){var $=function(i){return document.getElementById(i);},D={};try{D=JSON.parse($('clocktips').textContent)||{};}catch(e){}
var S=D.s||{},MAX=D.max||8,uid=0,all=[];
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=String(x);return e;}
function fmt(t,n){return String(t||'').replace('{n}',n);}
function rk(f){var m=/^clock-(1d|[a-z0-9]+)(?:-(?:ot|os|sd))?(?:-auto)?(-[ap]m)?\.png$/.exec(f||'');return m?m[1]+(m[2]||''):null;}
function setup(img){var fig=img.parentNode,w=el('div','ctwrap');fig.insertBefore(w,img);w.appendChild(img);
var layer=el('div','ctlayer'),tip=el('div','cttip');tip.id='cttip'+(++uid);tip.setAttribute('role','tooltip');tip.hidden=true;layer.setAttribute('role','group');
w.appendChild(layer);w.appendChild(tip);var st={spots:[],btns:[],cur:[],pin:false,pt:'mouse',f:null},me={w:w,hide:hide,st:st};all.push(me);
function box(){return {x:img.clientLeft,y:img.clientTop,w:img.clientWidth,h:img.clientHeight};}
function hide(){tip.hidden=true;st.pin=false;st.cur.forEach(function(i){var b=st.btns[i];if(b){b.classList.remove('on');b.removeAttribute('aria-describedby');}});st.cur=[];w.classList.remove('ctpin');}
function build(j){hide();layer.textContent='';st.spots=(j&&j.spots)||[];st.btns=st.spots.map(function(s,i){var b=el('button','ctspot');b.type='button';
b.style.left=s.x+'%';b.style.top=s.y+'%';b.tabIndex=i?-1:0;var it=s.items[0]||{};
b.setAttribute('aria-label',(it.n||'')+' '+(it.t||'')+' · '+fmt(S.n,s.items.length+(s.more||0)));
b.onfocus=function(){if(!st.pin||st.cur.indexOf(i)<0)show([i],false);};
b.onblur=function(e){if(!st.pin&&!(e.relatedTarget&&w.contains(e.relatedTarget)))hide();};
b.onkeydown=function(e){var k=e.key,n=st.btns.length,to=null;if(k==='ArrowRight'||k==='ArrowDown')to=(i+1)%n;else if(k==='ArrowLeft'||k==='ArrowUp')to=(i-1+n)%n;
else if(k==='Home')to=0;else if(k==='End')to=n-1;else if(k==='Enter'||k===' '){e.preventDefault();if(st.pin)hide();else show([i],true);return;}
else if(k==='Escape'){if(!tip.hidden){e.preventDefault();hide();}return;}if(to!=null){e.preventDefault();st.btns.forEach(function(x,j){x.tabIndex=j===to?0:-1;});hide();st.btns[to].focus();}};
layer.appendChild(b);return b;});layer.hidden=!st.spots.length;}
function load(){var f=img.dataset.f||'',k=rk(f),src=img.getAttribute('src')||'';if(f===st.f)return;st.f=f;
if(D.spots&&D.spots[k]){build(D.spots[k]);return;}if(!k||/^data:/.test(src)){build(null);return;}
build(null);fetch(src.replace(/\.png(\?|$)/,'.json$1'),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;})
.then(function(j){if(img.dataset.f===f)build(j);}).catch(function(){});}
// nearest spot within r CSS px of the pointer; on a small (phone) dial, spots that overlap on screen are listed together
function near(e,r){var b=img.getBoundingClientRect(),c=box(),px=e.clientX-b.left-c.x,py=e.clientY-b.top-c.y,best=-1,bd=r;
var P=st.spots.map(function(s){return [s.x/100*c.w,s.y/100*c.h];});P.forEach(function(p,i){var d=Math.hypot(p[0]-px,p[1]-py);if(d<=bd){bd=d;best=i;}});
if(best<0)return [];var m=Math.max(4,Math.min(8,c.w*0.012)),out=[best];
P.forEach(function(p,i){if(i!==best&&Math.hypot(p[0]-P[best][0],p[1]-P[best][1])<m)out.push(i);});return out;}
function show(ix,pin){hide();if(!ix.length)return;var items=[],more=0;ix.forEach(function(i){var s=st.spots[i];items=items.concat(s.items);more+=s.more||0;});
items.sort(function(a,b){return b.ts-a.ts;});if(items.length>MAX){more+=items.length-MAX;items=items.slice(0,MAX);}
// check-ins that differ only in time (same person, kind, categories, note) share one block listing every time
var blk=[],by={};items.forEach(function(it){var id=JSON.stringify([it.n,it.k,it.cats,it.note,it.c]),b=by[id];if(!b){b=by[id]={it:it,t:[]};blk.push(b);}b.t.push(it);});
tip.textContent='';blk.forEach(function(b){var it=b.it,row=el('div','cti'),h=el('div','cth'),d=el('i','dot');d.style.background=/^#[0-9a-fA-F]{3,8}$/.test(it.c)?it.c:'';
h.appendChild(d);h.appendChild(el('b','ctn',it.n));h.appendChild(el('span','ctk',S[it.k]||S.point));row.appendChild(h);
var zs=b.t.map(function(x){return x.z||'';}),z1=zs.every(function(z){return z===zs[0];});
var tl=el('div','ctt');b.t.forEach(function(x,j){if(j)tl.appendChild(document.createTextNode(' · '));tl.appendChild(el('span',null,x.t+(!z1&&x.z?' ('+x.z+')':'')+(z1&&zs[0]&&j===b.t.length-1?' ('+zs[0]+')':'')));});row.appendChild(tl);
if(it.cats&&it.cats.length)row.appendChild(el('div','ctc',it.cats.join(' · ')));
if(it.note)row.appendChild(el('div','ctnote',it.note));tip.appendChild(row);});
if(more)tip.appendChild(el('div','ctmore',fmt(S.more,more)));
st.cur=ix;st.pin=!!pin;w.classList.toggle('ctpin',!!pin);ix.forEach(function(i){st.btns[i].classList.add('on');st.btns[i].setAttribute('aria-describedby',tip.id);});
tip.style.left='0px';tip.style.top='0px';tip.hidden=false;
// above the dot when it fits on screen, else below; kept inside the viewport (a phone shows the two dials side by side)
var s=st.spots[ix[0]],c=box(),R=w.getBoundingClientRect(),cx=c.x+s.x/100*c.w,cy=c.y+s.y/100*c.h,tw=tip.offsetWidth,th=tip.offsetHeight,
VW=document.documentElement.clientWidth||innerWidth,vx=Math.max(4,Math.min(VW-tw-4,R.left+cx-tw/2)),t=cy-th-12;
if(R.top+t<4&&R.top+cy+12+th<=innerHeight)t=cy+12;tip.style.left=(vx-R.left)+'px';tip.style.top=t+'px';}
w.addEventListener('pointerdown',function(e){st.pt=e.pointerType||'mouse';});
w.addEventListener('pointermove',function(e){if(e.pointerType&&e.pointerType!=='mouse')return;var ix=near(e,9);w.style.cursor=ix.length?'pointer':'';
if(st.pin)return;if(ix.length){if(ix.join()!==st.cur.join())show(ix,false);}else if(!tip.hidden)hide();});
w.addEventListener('pointerleave',function(e){w.style.cursor='';if(!st.pin&&(!e.pointerType||e.pointerType==='mouse'))hide();});
w.addEventListener('click',function(e){if(e.detail===0||tip.contains(e.target))return;var ix=near(e,st.pt==='mouse'?9:20);
if(!ix.length){hide();return;}if(st.pin&&ix.join()===st.cur.join()){hide();return;}show(ix,true);});
if(window.MutationObserver)new MutationObserver(load).observe(img,{attributes:true,attributeFilter:['src','data-f']});
window.addEventListener('resize',function(){if(!tip.hidden)hide();});load();}
window.addEventListener('scroll',function(){all.forEach(function(m){if(m.st.cur.length&&!m.st.pin)m.hide();});},{passive:true});
document.addEventListener('click',function(e){all.forEach(function(m){if(m.st.cur.length&&!m.w.contains(e.target))m.hide();});});
document.addEventListener('keydown',function(e){if(e.key==='Escape')all.forEach(function(m){if(m.st.pin)m.hide();});});
[].forEach.call(document.querySelectorAll('#p-clock img[data-f]'),setup);})();"""


def clocktip_css(card, tx, dim):
    return (".ctwrap{position:relative} .ctwrap>img{display:block} .ctlayer{position:absolute;left:1px;top:1px;right:1px;bottom:1px;pointer-events:none}"
            " .ctspot{position:absolute;width:14px;height:14px;margin:-7px 0 0 -7px;padding:0;border:0;border-radius:50%;background:transparent;pointer-events:none}"
            " .ctspot:focus{outline:none} .ctspot:focus-visible,.ctspot.on{box-shadow:0 0 0 2px var(--accent)}"
            f" .cttip{{position:absolute;z-index:15;box-sizing:border-box;width:max-content;max-width:min(300px,calc(100vw - 8px));background:{card};color:{tx};"
            "border:1px solid var(--bd);border-radius:8px;padding:6px 9px;font-size:12.5px;line-height:1.45;box-shadow:0 8px 24px rgba(0,0,0,.45);text-align:left}"
            " .cttip .cti+.cti{border-top:1px solid var(--bd);margin-top:4px;padding-top:4px} .cttip .cth{display:flex;align-items:center;gap:6px}"
            f" .cttip .ctk,.cttip .ctt,.cttip .ctc,.cttip .ctmore{{color:{dim}}} .cttip .ctk{{margin-left:auto;padding-left:10px;white-space:nowrap}}"
            " .cttip .ctt>span{white-space:nowrap} .cttip .ctnote{white-space:pre-wrap;overflow-wrap:anywhere} .cttip .ctmore{margin-top:4px;font-weight:700}")
PALETTE_JS = r"""(function(){var $=function(i){return document.getElementById(i);},P=JSON.parse($('paldata').textContent),KEY='usage.palette',PKEY='usage.patterns',pchk=$('patchk'),CK={r:'usage.clockRange',l:'usage.clockLayout',f:'usage.clockFill',d:'usage.clockDial'},D={r:'wk',l:'ov',f:'t',d:'24'},
served=/^https?:$/.test(location.protocol),btn=$('palbtn'),msg=$('palmsg'),V=P.light?'light':'dark',cur=P.base,pat=!!P.pat,CR={r:'wk',l:'ov',f:'t',d:'24'},seq=0,
TKN=(document.querySelector('meta[name="usage-token"]')||{}).content||'',clk=$('clkimg'),clk1=$('clk1img'),
imgs=[].slice.call(document.querySelectorAll('img[data-f]'));imgs.forEach(function(i){i.dataset.orig=i.getAttribute('src');i.dataset.f0=i.dataset.f;});
function rgb(h){h=String(h).replace('#','');if(h.length==3)h=h.replace(/./g,'$&$&');return [0,2,4].map(function(i){return parseInt(h.substr(i,2),16)||0;});}
function mix(a,b,t){var x=rgb(a),y=rgb(b);return '#'+x.map(function(v,i){return ('0'+Math.round(v+(y[i]-v)*t).toString(16)).slice(-2);}).join('');}
function lum(h){var c=rgb(h).map(function(v){v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);});return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2];}
function vars(n){var c=P.table[n][V],o={};['over','under','baseline','notice'].forEach(function(k){if(!P.fixed[k])o['--'+k]=c[k];});
var a=P.fixed.accent?P.accent:(P.fixed.under?P.under:c.under);o['--accent']=a;o['--bd']=mix(P.bg,a,0.25);o['--on-accent']=lum(a)>0.4?'#0B0F14':'#FFFFFF';o['--acc-hl']=lum(a)>0.4?'#FFFFFF':a;o['--m-eye']=o['--m-mouth']=(lum(a)+0.05)/(lum('#1B1F24')+0.05)>=3?'#1B1F24':'#050506';return o;}
function mode(c){return c.l==='sd'?'sd':'o'+c.f;}function same(a,b){return a.r===b.r&&a.l===b.l&&a.f===b.f&&a.d===b.d;}
function half(c){var d=c.x||c.d;return d==='auto'?(new Date().getHours()<12?'am':'pm'):d;}function dsfx(c,left){var d=half(c);return d==='am'||d==='pm'?(left&&c.d==='auto'?'-auto-':'-')+d:'';}
function fileOf(i,c){return i===clk?'clock-'+c.r+'-'+mode(c)+dsfx(c)+'.png':(i===clk1?'clock-1d'+dsfx(c,1)+'.png':i.dataset.f0);}
function key(n,p){return (p?n+'-pat':n)+P.osfx;}
// custom colours: the key carries the pair (custom-<under><over>); remembered in localStorage 'usage.customColors'
var CCK='usage.customColors',CC=P.cc,cpk=$('cpick'),ccu=$('ccu'),cco=$('cco'),cch=$('cchint');
function isC(n){return /^custom-[0-9a-f]{12}$/.test(String(n));}
function hx(v){return /^#[0-9a-fA-F]{6}$/.test(String(v));}
function ckey(){return 'custom-'+CC.under.slice(1).toLowerCase()+CC.over.slice(1).toLowerCase();}
function lv(n){return isC(n)?'custom':n;}
function hsv(h){var c=rgb(h).map(function(v){return v/255;}),mx=Math.max.apply(0,c),mn=Math.min.apply(0,c),d=mx-mn,hh=0;
if(d){hh=mx===c[0]?((c[1]-c[2])/d)%6:mx===c[1]?(c[2]-c[0])/d+2:(c[0]-c[1])/d+4;hh*=60;if(hh<0)hh+=360;}return [hh,mx?d/mx:0];}
function similar(a,b){var la=lum(a),lb=lum(b),cr=(Math.max(la,lb)+0.05)/(Math.min(la,lb)+0.05),x=hsv(a),y=hsv(b),dh=Math.abs(x[0]-y[0]);dh=Math.min(dh,360-dh);
return cr<1.6&&(dh<40||Math.min(x[1],y[1])<0.2);}
function cshow(n){if(!cpk)return;cpk.hidden=!isC(n);if(ccu)ccu.value=CC.under.toLowerCase();if(cco)cco.value=CC.over.toLowerCase();
if(cch)cch.hidden=!similar(CC.under,CC.over);var o=$('po-custom');if(o){var d=o.querySelectorAll('.dot');if(d.length>1){d[0].style.background=CC.over;d[1].style.background=CC.under;}}}
try{var sc=JSON.parse(localStorage.getItem(CCK)||'null');if(sc&&hx(sc.under)&&hx(sc.over))CC={under:sc.under.toUpperCase(),over:sc.over.toUpperCase()};}catch(e){}
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
var PL=listbox(btn,$('pallist'),$('palcur'),function(){return lv(cur);},function(v){go(v==='custom'?ckey():v,CR,true,pat);}),
CL=listbox($('clkbtn'),$('clklist'),$('clkcur'),function(){return CR.r;},function(v){go(cur,with_('r',v),true,pat);}),
LL=listbox($('clobtn'),$('clolist'),$('clocur'),function(){return CR.l;},function(v){go(cur,with_('l',v),true,pat);}),
FL=listbox($('clfbtn'),$('clflist'),$('clfcur'),function(){return CR.f;},function(v){go(cur,with_('f',v),true,pat);}),
DL=listbox($('cldbtn'),$('cldlist'),$('cldcur'),function(){return CR.d;},function(v){go(cur,with_('d',v),true,pat);});
function with_(k,v){var c={r:CR.r,l:CR.l,f:CR.f,d:CR.d};c[k]=v;return c;}
function marks(c){CL.mark(c.r);LL.mark(c.l);FL.mark(c.f);DL.mark(c.d);if($('clfsel'))$('clfsel').hidden=c.l==='sd';}
// images are fetched and decoded first, then colours and images change in the same task (no half-switched page)
function commit(n,r,us,p){var st=document.documentElement.style,o=vars(n);Object.keys(o).forEach(function(k){st.setProperty(k,o[k]);});
imgs.forEach(function(i,j){i.dataset.f=fileOf(i,r);i.src=us[j];});cur=n;pat=p;CR=r;cshow(n);PL.mark(lv(n));marks(r);if(pchk)pchk.checked=p;document.documentElement.dataset.palette=n;document.documentElement.dataset.patterns=p?'1':'';
document.documentElement.dataset.clock=r.r+'-'+mode(r)+dsfx(r);sub(r);}
// A page loaded before the server restarted still holds the old per-start token: the server answers 403 and the menu would
// snap back. Keep the pick, then reload once (a second 403 within a minute is a real failure and is reported as usual).
function revive(n,p,r){try{var t=+sessionStorage.getItem('usage.stale')||0;if(Date.now()-t<60000)return false;sessionStorage.setItem('usage.stale',String(Date.now()));
if(isC(n))localStorage.setItem(CCK,JSON.stringify(CC));localStorage.setItem(KEY,n);localStorage.setItem(PKEY,p?'1':'0');Object.keys(CK).forEach(function(k){localStorage.setItem(CK[k],r[k]);});
say('');location.reload();return true;}catch(e){return false;}}
// the line under the toolbar names what the left dial shows: one text per Hours state (24 / am / pm / auto-am / auto-pm)
var SUB=$('clksub'),SUBS={};try{SUBS=JSON.parse((SUB||{dataset:{}}).dataset.s||'{}');}catch(e){}
function sub(c){var d=half(c),t=SUBS[d==='am'||d==='pm'?(c.d==='auto'?'auto-':'')+d:'24'];if(SUB&&t!=null)SUB.textContent=t;}
function go(n,r,save,p){if(!P.table[n]&&!isC(n))n=P.base;p=!!(p||P.pf);var k=key(n,p);r={r:r.r,l:r.l,f:r.f,d:r.d};if(!clk||!CL.has(r.r))r.r=D.r;if(!LL.has(r.l))r.l=D.l;if(!FL.has(r.f))r.f=D.f;if(!DL.has(r.d))r.d=D.d;
var my=++seq,need=k!==P.default||!same(r,D);btn.setAttribute('aria-busy','true');if(need)say(P.s.busy,true);
var prep=(!need||!served)?Promise.resolve({ok:true,v:''}):fetch('/api/palette/render',{method:'POST',headers:{'X-Usage-Token':TKN,'Content-Type':'application/json'},
body:JSON.stringify({name:n,patterns:p,clock:r.r,mode:mode(r),dial:r.d})}).then(function(x){return x.json().then(function(j){if(x.status===403)j.stale=1;return j;},function(){return {ok:false,stale:x.status===403};});});
prep.then(function(j){if(my!==seq)return;if(j&&j.stale&&revive(n,p,r))return;if(!j||!j.ok)throw 0;r.x=j.dial||'';if(j.colors)P.table[n]=j.colors;if(!P.table[n])throw 0;var us=imgs.map(function(i){return src(i,k,r,j.v);});
return Promise.all(us.map(pre)).then(function(ok){if(my!==seq)return;if(ok.indexOf(false)>=0)throw 0;commit(n,r,us,p);say('');btn.removeAttribute('aria-busy');
if(save){try{if(isC(n))localStorage.setItem(CCK,JSON.stringify(CC));localStorage.setItem(KEY,n);localStorage.setItem(PKEY,p?'1':'0');Object.keys(CK).forEach(function(k){localStorage.setItem(CK[k],r[k]);});}catch(e){}}});}).catch(function(){if(my!==seq)return;btn.removeAttribute('aria-busy');PL.mark(lv(cur));marks(CR);cshow(cur);if(pchk)pchk.checked=pat;
say(served?P.s.fail:P.s.file);});}
// saved choices: palette name (P.alias maps names saved by older pages) and the "Add patterns" switch
var n=P.base,p=pat,r={r:D.r,l:D.l,f:D.f,d:D.d};try{n=localStorage.getItem(KEY)||P.base;var sp=localStorage.getItem(PKEY);if(sp==='1'||sp==='0')p=sp==='1';
Object.keys(CK).forEach(function(k){r[k]=localStorage.getItem(CK[k])||D[k];});}catch(e){}
n=P.alias[n]||n;if(n==='custom')n=ckey();if(!P.table[n]&&!(isC(n)&&served))n=P.base;p=!!(p||P.pf);document.documentElement.dataset.palette=P.base;document.documentElement.dataset.clock='wk-ot';
if(pchk){pchk.checked=pat;pchk.disabled=!!P.pf;pchk.onchange=function(){var on=pchk.checked;go(cur,CR,true,on);
if(served)fetch('/api/patterns',{method:'POST',headers:{'X-Usage-Token':TKN,'Content-Type':'application/json'},body:JSON.stringify({on:on})}).catch(function(){});};}
if(key(n,p)!==P.default||!same(r,D))go(n,r,false,p);
cshow(cur);function cpick(){if(!hx(ccu.value)||!hx(cco.value))return;CC={under:ccu.value.toUpperCase(),over:cco.value.toUpperCase()};
try{localStorage.setItem(CCK,JSON.stringify(CC));}catch(e){}cshow(ckey());go(ckey(),CR,true,pat);}
if(ccu&&cco){ccu.onchange=cco.onchange=cpick;ccu.oninput=cco.oninput=function(){if(cch)cch.hidden=!similar(ccu.value,cco.value);};if(!served)ccu.disabled=cco.disabled=true;}
window.usagePalette=function(v,save){go(v,CR,save,pat);};window.usagePatterns=function(on,save){go(cur,CR,save,on);};window.usageClockRange=function(v,save){go(cur,with_('r',v),save,pat);};
window.usageClockLayout=function(v,save){go(cur,with_('l',v),save,pat);};window.usageClockFill=function(v,save){go(cur,with_('f',v),save,pat);};window.usageClockDial=function(v,save){go(cur,with_('d',v),save,pat);};})();"""


# "Check for updates": asks the local server (POST /api/update/check); when GitHub has a newer version
# an "Update" button appears. The server also checks by itself about once a day (switch on the page,
# GET /api/update/state shows the last result). "Update" (POST /api/update/apply) starts the updater, then
# the page polls /api/status until a *new* server answers (different boot id) and reloads. Updating
# itself only ever happens after a click.
UPDATE_JS = """(function(){var $=id=>document.getElementById(id),S=JSON.parse($('updstr').textContent),
TK=(document.querySelector('meta[name="usage-token"]')||{}).content||'',cb=$('updbtn'),gb=$('updgo'),um=$('umsg'),ur=$('urow'),
ab=$('updauto'),al=$('updautol'),nb=$('updback'),nt=$('unotes'),prev=null,busy=false;
if(!/^https?:$/.test(location.protocol))return;
function f(t,o){return String(t).replace(/\\{(\\w+)\\}/g,function(_,k){return o[k]!=null?o[k]:'';});}
function say(t){um.textContent=t||'';ur.hidden=!t&&nt.hidden;}
function notes(j){var t=(!S.privacy&&j&&typeof j.notes==='string')?j.notes.slice(0,3200):'';nt.textContent=t?S.notes+'\\n'+t:'';nt.hidden=!t;if(t)ur.hidden=false;}
function post(p,b){return fetch(p,{method:'POST',headers:{'X-Usage-Token':TK,'Content-Type':'application/json'},body:b?JSON.stringify(b):''}).then(r=>r.json());}
function newer(j){var l=j.latest||{};return f(S['new'],{h:l.hash||'',s:S.privacy?'':(l.subject||''),n:j.behind}).replace(/\\s+(\\(|（)/,' $1');}
function show(j,quiet){gb.hidden=true;notes(j);var st=j.state||'git_error';
if(st==='behind'){say(newer(j));gb.hidden=false;return;}
if(quiet&&st!=='local_changes'&&st!=='diverged')return;
var m=S[st]||S.git_error;if((st==='local_changes'||st==='diverged')&&j.latest&&!S.privacy)m=newer(j)+' · '+m;
say(m+(j.detail&&!S.privacy?' ('+j.detail+')':''));}
function load(){if(busy)return;fetch('/api/update/state',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(j=>{if(!j||!j.ok||busy)return;
cb.hidden=false;al.hidden=false;ab.checked=!!j.auto;prev=j.previous||null;nb.hidden=!prev;if(j.auto&&j.result)show(j.result,true);}).catch(()=>{});}
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
wait(j.boot,S.updating);})
.catch(()=>{gb.disabled=cb.disabled=false;say(S.failed.replace('{e}',''));});};
function wait(old,msg){busy=true;gb.hidden=cb.hidden=nb.hidden=true;nt.hidden=true;say(msg);var t0=Date.now();
(function poll(){if(Date.now()-t0>600000){say(S.slow);busy=false;cb.hidden=false;cb.disabled=false;return;}
fetch('/api/status',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(s=>{if(s&&s.ok&&s.boot&&s.boot!==old){location.reload();return;}
setTimeout(poll,3000);}).catch(()=>{setTimeout(poll,3000);});})();}
nb.onclick=function(){if(!prev)return;if(!confirm(f(S.back_confirm,{v:prev.version?'v'+prev.version:prev.short})))return;
nb.disabled=gb.disabled=cb.disabled=true;
post('/api/update/rollback').then(j=>{if(!j.ok){nb.disabled=gb.disabled=cb.disabled=false;
say(j.error==='busy'?S.busy:f(S.back_failed,{e:S.privacy?'':((S.back_states||{})[j.error]||j.error||'')}));return;}
wait(j.boot,S.back_running);}).catch(()=>{nb.disabled=gb.disabled=cb.disabled=false;say(S.back_failed.replace('{e}',''));});};})();"""

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


# custom colours: the two pickers shown next to the palette menu when "Custom…" is chosen
CPICK_STR = {"en": {"under": "Under baseline", "over": "Over baseline",
                    "hint": "The two colours are close: the ▲ / ▼ tags and Add patterns still tell them apart"},
             "zh-TW": {"under": "低於基準", "over": "高於基準", "hint": "兩個顏色很接近：▲／▼ 標籤和「加上紋路」仍能分辨"},
             "zh-CN": {"under": "低于基准", "over": "高于基准", "hint": "两个颜色很接近：▲／▼ 标签和“加上纹路”仍能分辨"},
             "numeric": {"under": "▼", "over": "▲", "hint": "▲ ▼ ▨"}}
PAL_STR = {"en": {"busy": "Drawing charts…", "fail": "Could not draw this palette", "file": "Open the page through the dashboard server to switch palettes"},
           "zh-TW": {"busy": "正在產生圖表…", "fail": "無法產生這組配色", "file": "請透過儀表板伺服器開啟頁面才能切換配色"},
           "zh-CN": {"busy": "正在生成图表…", "fail": "无法生成这组配色", "file": "请通过仪表板服务器打开页面才能切换配色"},
           "numeric": {"busy": "…", "fail": "×", "file": "×"}}
for _lg in LATIN_LANGS:
    CPICK_STR[_lg] = _over_en(CPICK_STR["en"], l10n.CPICK[_lg])
    PAL_STR[_lg] = _over_en(PAL_STR["en"], l10n.PAL[_lg])


def clock_tips_json(ctx):
    """<script type=application/json> text for the clock tooltips: strings + the spots of the two embedded dials
    (by range key; dot positions do not depend on palette or layout). '<' is escaped so a note can never close the tag."""
    spots = {}
    for k in ("1d", "wk"):
        f = ctx.hist_dir / (clock_file(k)[:-4] + ".json")
        try:
            spots[k] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    lk = "numeric" if ctx.privacy else ("zh-CN" if ctx.lang == "zh-CN" else ("zh-TW" if ctx.lang.startswith("zh") else
                                                                          (ctx.lang if ctx.lang in LATIN_LANGS else "en")))
    return json.dumps({"s": CLOCKTIP_STR[lk], "max": CLOCK_TIP_MAX, "spots": spots}, ensure_ascii=False).replace("<", "\\u003c")


# 本週總覽 / 每日增量 (per day / per hour): the tabs are drawn in the PNGs (chart_hud); the page lays real buttons over
# them. The last main tab is remembered per browser ('usage.wkTab'; first visit = 本週總覽), and so is the per-day /
# per-hour choice ('usage.incUnit'), else per hour only when this week has enough hourly readings (heavy use).
WKVIEW_CSS = ("[data-wv] .hot{position:absolute;background:transparent;border:0;padding:0;margin:0;border-radius:9px;cursor:pointer}"
              " [data-wv] .hot[aria-pressed=true]{cursor:default} [data-wv] .hot:hover:not([aria-pressed=true]){background:rgba(127,127,127,.12)}"
              " [data-wv] .hot:focus-visible{outline:2px solid var(--accent);outline-offset:1px}")
WKVIEW_JS = r"""(function(){var sec=document.getElementById('p-wk');if(!sec)return;var W=[].slice.call(sec.querySelectorAll('[data-wv]'));if(W.length<2)return;
var D={};try{D=JSON.parse(sec.getAttribute('data-inc')||'{}')||{};}catch(e){}var UK='usage.incUnit',TK='usage.wkTab';
function has(v){return W.some(function(w){return w.getAttribute('data-wv')===v;});}
function unit(){var u=null;try{u=localStorage.getItem(UK);}catch(e){}return (u==='daily'||u==='hourly')&&has(u)?u:(D.rich&&has('hourly')?'hourly':'daily');}
function show(v,focus){if(!has(v))v='week';W.forEach(function(w){w.hidden=w.getAttribute('data-wv')!==v;});
try{localStorage.setItem(TK,v==='week'?'week':'inc');if(v!=='week')localStorage.setItem(UK,v);}catch(e){}
if(focus){var w=sec.querySelector("[data-wv='"+v+"']"),b=w&&w.querySelector("button.hot[aria-pressed='true'][data-go='"+focus+"']");if(b)b.focus();}}
sec.addEventListener('click',function(e){var b=e.target.closest?e.target.closest('button.hot'):null;if(!b||b.getAttribute('aria-pressed')==='true')return;
var g=b.getAttribute('data-go');show(g==='inc'?unit():g,g);});
var t=null;try{t=localStorage.getItem(TK);}catch(e){}if(t==='inc')show(unit());})();"""


# 每日增量 (per day / per hour): the linked selection. The chart's columns and the log's cards are real click areas laid
# over the picture; the selection itself is two transparent sprites (the pixels chart_hud changes between "nothing" and
# "everything" selected), clipped to the chosen column / card, so the faint band stays behind the bars and nothing else
# changes colour. Nothing is stored: a reload selects today (per day) / the newest hour (per hour) again.
SEL_CSS = ("[data-wv] .dsel{position:absolute;display:block;box-sizing:border-box;background:transparent;border:0;padding:0;margin:0;border-radius:9px;cursor:pointer}"
           " [data-wv] .dsel:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}"
           " [data-wv] img.ssprite{position:absolute;left:1px;top:1px;width:100%;height:auto;border:0;border-radius:0;pointer-events:none}"
           # the per-hour list: a scrollable viewport over the card holding every hour row (one tall picture + a button per row);
           # its scrollbar is always shown (not an overlay), a 16 px track on the card's right edge with a wide accent-coloured thumb
           " [data-wv] .hscroll{position:absolute;box-sizing:content-box;overflow-x:hidden;overflow-y:scroll;overscroll-behavior:contain;scrollbar-gutter:stable}"
           # (scrollbar-color would switch Chromium's own scrollbar styling off, so it is only for browsers without ::-webkit-scrollbar)
           " @supports not selector(::-webkit-scrollbar){[data-wv] .hscroll{scrollbar-width:auto;scrollbar-color:var(--accent) rgba(127,127,127,.28)}}"
           " [data-wv] .hscroll::-webkit-scrollbar{width:16px;background:rgba(127,127,127,.22)}"
           " [data-wv] .hscroll::-webkit-scrollbar-button{display:none}"
           " [data-wv] .hscroll::-webkit-scrollbar-track{background:rgba(127,127,127,.22);border-radius:8px;box-shadow:inset 0 0 0 1px rgba(127,127,127,.30)}"
           " [data-wv] .hscroll::-webkit-scrollbar-thumb{background:var(--accent);border:3px solid transparent;background-clip:padding-box;border-radius:8px;min-height:44px}"
           " [data-wv] .hscroll::-webkit-scrollbar-thumb:hover{background:var(--acc-hl);background-clip:padding-box}"
           " [data-wv] .hscroll::-webkit-scrollbar-thumb:active{background:var(--acc-hl);background-clip:padding-box;border-width:2px}"
           " [data-wv] .hstrip{position:relative}"
           " [data-wv] img.hrows{display:block;width:100%;height:auto;border:0;border-radius:0}"
           " [data-wv] .hrow{left:0;width:100%;border-radius:7px}"
           " [data-wv] .hrow::before{content:'';position:absolute;inset:1px 0;border:1px solid transparent;border-radius:7px;pointer-events:none}"
           " [data-wv] .hrow[aria-pressed=true]::before{border-color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent)}"
           " [data-wv] .hrow:hover:not([aria-pressed=true])::before{background:rgba(127,127,127,.12)}")
SEL_JS = r"""(function(){var sec=document.getElementById('p-wk');if(!sec)return;
function init(w){var img=w.querySelector('img[data-f]'),S=[].slice.call(w.querySelectorAll('img.ssprite')),off=S[0],onc=S[1],onr=S[2],I=[].slice.call(w.querySelectorAll('[data-i]'));
if(!img||S.length<3||!I.length)return;var d0=+off.getAttribute('data-sel'),cur=d0,ready=false,seq=0;
function url(k){var s=img.getAttribute('src')||'';return /week-(daily|hourly)\.png/.test(s)?s.replace(/week-(daily|hourly)\.png/,'week-$1-'+k+'.png'):'';}
function find(c,i){for(var j=0;j<I.length;j++)if(I[j].classList.contains(c)&&+I[j].getAttribute('data-i')===i)return I[j];return null;}
function box(e){var c=e.getAttribute('data-clip');return c?c.split(',').map(parseFloat):[parseFloat(e.style.left),parseFloat(e.style.top),parseFloat(e.style.width),parseFloat(e.style.height)];}
function clip(im,e){if(!e){im.hidden=true;return;}var r=box(e);im.style.clipPath='inset('+r[1]+'% '+(100-r[0]-r[2])+'% '+(100-r[1]-r[3])+'% '+r[0]+'%)';im.hidden=false;}
function mark(){I.forEach(function(e){if(e.getAttribute('aria-pressed')!==null)e.setAttribute('aria-pressed',+e.getAttribute('data-i')===cur?'true':'false');});}
function reveal(e){var c=e&&e.closest?e.closest('.hscroll'):null;if(!c)return;var a=e.getBoundingClientRect(),b=c.getBoundingClientRect();
if(a.top<b.top)c.scrollTop-=b.top-a.top;else if(a.bottom>b.bottom)c.scrollTop+=a.bottom-b.bottom;}
function render(){mark();reveal(find('hrow',cur));if(!ready||cur===d0){S.forEach(function(im){im.hidden=true;});return;}off.hidden=false;clip(onc,find('dcol',cur));clip(onr,find('dcard',cur));}
function load(){ready=false;S.forEach(function(im){im.hidden=true;});var a=url('off'),b=url('on'),my=++seq,n=0;if(!a||w.hidden)return;
[a,b].forEach(function(u){var im=new Image();im.onload=function(){if(my!==seq)return;if(++n===2){off.src=a;onc.src=b;onr.src=b;ready=true;render();}};im.src=u;});}
w.addEventListener('click',function(e){var t=e.target&&e.target.closest?e.target.closest('[data-i]'):null;if(!t)return;cur=+t.getAttribute('data-i');render();});
if(window.MutationObserver){new MutationObserver(load).observe(img,{attributes:true,attributeFilter:['src']});
new MutationObserver(function(){if(!w.hidden&&!ready)load();}).observe(w,{attributes:true,attributeFilter:['hidden']});}
mark();load();}
['daily','hourly'].forEach(function(v){var w=sec.querySelector("[data-wv='"+v+"']");if(w)init(w);});})();"""


def select_html(sel, label_of, strip_src=None):
    """The click areas of one 每日增量 view (the page's own buttons / areas over the picture; no new text: a card's
    accessible name is its day label or its time range). (html, selected index), or (None, None) without selection data.
    With sel["strip"] (the per-hour list) and strip_src, the rows are not cards on the chart picture but buttons over a tall
    picture (strip_src) inside a scrollable viewport on the card: every hour row of the chart is listed."""
    try:
        cols, rows, i0 = sel["cols"], sel["rows"], int(sel["i"])
        clips = sel.get("clips") or [None] * len(cols)
        if not cols or len(rows) != len(cols) or not 0 <= i0 < len(cols):
            return None, None
    except (KeyError, TypeError, ValueError):
        return None, None
    box = lambda r: f"left:{r[0]}%;top:{r[1]}%;width:{r[2]}%;height:{r[3]}%"
    out = [f"<img class='ssprite' alt='' hidden data-sel='{i0}'><img class='ssprite' alt='' hidden><img class='ssprite' alt='' hidden>"]
    for k, r in enumerate(cols):
        clip = f" data-clip='{','.join(str(v) for v in clips[k])}'" if clips[k] and clips[k] != r else ""
        out.append(f"<div class='dsel dcol' data-i='{k}' aria-hidden='true' style='{box(r)}'{clip}></div>")
    strip = sel.get("strip")
    if strip and strip_src:
        x, y, w, h = strip["box"]
        # the viewport is the list's width plus its 16 px scrollbar (the scrollbar takes its room from the content box)
        out.append(f"<div class='hscroll' style='left:{x}%;top:{y}%;width:calc({w}% + 16px);height:{h}%'><div class='hstrip'>"
                   f"<img class='hrows' alt='' loading='lazy' data-f='{html.escape(strip_src[0])}' src='{html.escape(strip_src[1])}'>")
        for k, r in enumerate(rows):
            if r:
                out.append(f"<button type='button' class='dsel hrow' data-i='{k}' aria-pressed='{'true' if k == i0 else 'false'}'"
                           f" aria-label='{html.escape(str(label_of(k)))}' style='{box(r)};--c:{strip['colors'][k]}'></button>")
        out.append("</div></div>")
        return "".join(out), i0
    for k, r in enumerate(rows if not strip else []):  # (strip row boxes are in the tall picture's coordinates, not the chart's)
        if r:
            out.append(f"<button type='button' class='dsel dcard' data-i='{k}' aria-pressed='{'true' if k == i0 else 'false'}'"
                       f" aria-label='{html.escape(str(label_of(k)))}' style='{box(r)}'></button>")
    return "".join(out), i0


def week_views_html(ctx, gmeta, b64):
    """The 本週總覽 pane: the overview image (embedded) plus the 每日增量 images when rendered (loaded from beside the
    page on first use), each with buttons over the tabs drawn in it. Labels follow the page language (numeric in privacy)."""
    import chart_hud
    lk = "numeric" if ctx.privacy else ctx.lang
    HL = dict(chart_hud.BASE_STRINGS, **chart_hud.TRANSLATIONS.get(lk, {}))
    H = chart_hud.hotspots()
    inc = (gmeta or {}).get("inc") or {}

    def btn(go, rect, label, on):
        l, t, w, h = rect
        return (f"<button type='button' class='hot' data-go='{go}' aria-pressed='{'true' if on else 'false'}' aria-label='{html.escape(str(label))}'"
                f" title='{html.escape(str(label))}' style='left:{l}%;top:{t}%;width:{w}%;height:{h}%'></button>")

    def tabs(on):
        return btn("week", H["tabs"]["week"], HL["tab_week"], on == "week") + btn("inc", H["tabs"]["daily"], HL["tab_daily"], on != "week")

    def units(on):
        return (btn("daily", H["units"]["day"], HL["unit_day"], on == "daily")
                + btn("hourly", H["units"]["hour"], HL["unit_hour"], on == "hourly"))
    wk = ctx.hist_dir / "week-current.png"
    have = all((ctx.hist_dir / f).exists() for f in WEEK_VIEW_FILES[1:])
    parts = [f"<div class='gwrap' data-wv='week'><img alt='' data-f='week-current.png' src='data:image/png;base64,{b64(wk)}'>"
             + (tabs("week") if have else "") + "</div>"]
    if have:
        for v, fn in zip(("daily", "hourly"), WEEK_VIEW_FILES[1:]):
            ver = int((ctx.hist_dir / fn).stat().st_mtime)
            sel = ((gmeta or {}).get("sel") or {}).get(v)
            day_label = lambda k: chart_hud.sfmt(HL["day_fmt"], d=k + 1, day=k + 1)
            hour_label = lambda k: (sel.get("labels") or [])[k] or ""
            rows_fn = HOURLY_ROWS_FILE
            rver = int((ctx.hist_dir / rows_fn).stat().st_mtime) if (ctx.hist_dir / rows_fn).exists() else None
            strip_src = (rows_fn, f"{rows_fn}?v={rver}") if v == "hourly" and rver is not None else None
            dsel, i0 = select_html(sel, day_label if v == "daily" else hour_label, strip_src) if sel else (None, None)
            parts.append(f"<div class='gwrap' data-wv='{v}' hidden><img alt='' loading='lazy' data-f='{fn}' src='{fn}?v={ver}'>"
                         + (dsel or "") + tabs(v) + units(v) + "</div>")
    dinc = html.escape(json.dumps({"rich": bool(inc.get("rich")), "hours": inc.get("hours")}), quote=True)
    return f"<section id='p-wk' class='on' data-inc=\"{dinc}\">" + "".join(parts) + "</section>"


def write_html(ctx, sums, now, nowa, zl, gen=""):
    L, T = ctx.L, ctx.theme
    latin = ctx.lang if ctx.lang in LATIN_LANGS else "en"  # es / pt / fr / de / id, else English (ja / ko too)
    MS = MANUAL_STR["numeric"] if ctx.privacy else (MANUAL_STR["zh-TW"] if ctx.lang.startswith("zh") else MANUAL_STR[latin])
    LS = LEDGER_STR["numeric"] if ctx.privacy else (LEDGER_STR[usage_dates.lang_key(ctx.lang)] if ctx.lang.startswith("zh") else LEDGER_STR[latin])
    US = UPDATE_STR["numeric"] if ctx.privacy else (UPDATE_STR["zh-TW"] if ctx.lang.startswith("zh") else UPDATE_STR[latin])
    try:
        ledger = json.loads(Path(ctx.ledger_path).read_text(encoding="utf-8"))
        ledger.pop("_newAlerts", None)
    except Exception:
        ledger = None
    ledjson = json.dumps(ledger, ensure_ascii=False).replace("<", "\\u003c")
    lsjson = json.dumps(dict(LS, off=ctx.display_offset, otag=usage_offset.tag(ctx.display_offset)), ensure_ascii=False).replace("<", "\\u003c")
    usjson = json.dumps(US, ensure_ascii=False).replace("<", "\\u003c")
    try:
        gmeta = json.loads((ctx.hist_dir / "week-current.json").read_text(encoding="utf-8")) if (ctx.hist_dir / "week-current.png").exists() else None
    except (OSError, ValueError):
        gmeta = None
    gjson = json.dumps({"meta": gmeta or None, "mode": ctx.anim_setting, "locked": ctx.anim_locked,
                        "otag": usage_offset.tag(ctx.display_offset)}, ensure_ascii=False).replace("<", "\\u003c")
    b64 = lambda p: base64.b64encode(p.read_bytes()).decode() if p.exists() else ""
    tabs, panes = [], []
    wk = ctx.hist_dir / "week-current.png"
    tabs.append(f"<button data-k='wk' class='on'>{html.escape(L['rwk'])}</button>")
    panes.append(week_views_html(ctx, gmeta, b64) if wk.exists() else f"<section id='p-wk' class='on'><div class='warn'>{html.escape(L['week_empty'])}</div></section>")
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
    imgs = clkbar = ""
    if (ctx.hist_dir / "clock-1d.png").exists() and (ctx.hist_dir / clock_file("wk")).exists():
        def menu(mid, label, opts, cur):
            rows = "".join(f"<li role='option' id='{mid}-{k}' data-v='{k}' aria-selected='{'true' if k == cur else 'false'}'><span>{html.escape(v)}</span></li>"
                           for k, v in opts)
            return (f"<span class='pal psel' id='{mid}sel'><span id='{mid}l'>{html.escape(label)}</span>"
                    f"<button type='button' class='pbtn' id='{mid}btn' aria-haspopup='listbox' aria-expanded='false' aria-labelledby='{mid}l {mid}cur'>"
                    f"<span id='{mid}cur'><span>{html.escape(dict(opts)[cur])}</span></span></button>"
                    f"<ul class='plist' id='{mid}list' role='listbox' tabindex='-1' aria-labelledby='{mid}l' hidden>{rows}</ul></span>")
        bar = (menu("cld", L["clock_dl"], [(k, L["clock_do"][k]) for k in CLOCK_DIALS], "24")
               + menu("clk", L["clock_rl"], [(k, L["clock_ro"][k]) for k in CLOCK_RANGES], "wk")
               + menu("clo", L["clock_ll"], [(k, L["clock_lo"][k]) for k in ("ov", "sd")], "ov")
               + menu("clf", L["clock_fl"], [(k, L["clock_fo"][k]) for k in ("t", "s")], "t"))
        clkbar = f"<div class='clkbar'>{bar}</div>"  # one full-width toolbar above both cards: wrapping never moves a card
        imgs = (f"<figure><img alt='' id='clk1img' data-f='clock-1d.png' src='data:image/png;base64,{b64(ctx.hist_dir / 'clock-1d.png')}'></figure>"
                f"<figure><img alt='' id='clkimg' data-f='{clock_file('wk')}' src='data:image/png;base64,{b64(ctx.hist_dir / clock_file('wk'))}'></figure>")
    tabs.append(f"<button data-k='clock'>{html.escape(L['clock_tab'])}</button>")
    subs = clock_sub_texts(L, zl)
    panes.append(f"<section id='p-clock'><div class='sub' id='clksub' data-s=\"{html.escape(json.dumps(subs, ensure_ascii=False), quote=True)}\">{html.escape(subs['24'])}</div>"
                 f"{clkbar}<div class='clocks'>{imgs or '<div class=warn>' + html.escape(L['no_clock']) + '</div>'}</div></section>")
    ctjson = clock_tips_json(ctx)
    ckdata = usage_checkins_ui.to_script_json(checkin_page_data(ctx, sums))
    rjs = json.dumps({k: L[k] for k in ("loading", "refresh", "conn_err", "refreshing", "ph_read", "ph_draw")}, ensure_ascii=False).replace("<", "\\u003c")
    tabs.append(f"<button data-k='checkin'>{html.escape(L['checkin_tab'])}</button>")
    panes.append(usage_checkins_ui.pane_html(ctx.ck_strings(), html.escape(L["ck_hint"])))
    bdstr = usage_breakdown_ui.for_lang(ctx.lang)  # the Built-in tab: Grok Bot's built-in usage report (newest snapshot)
    tabs.append(f"<button data-k='breakdown'>{html.escape(bdstr['tab'])}</button>")
    panes.append(usage_breakdown_ui.pane_html(bdstr))
    bddata = usage_breakdown_ui.to_script_json(breakdown_page_data(ctx))
    title = "" if ctx.privacy else L["title"]
    bg, tx, ac, card, bd, dim = T["bg_color"], T["text_color"], T["accent_color"], T["card"], T["border"], T["dim"]
    # palette switcher: config default, overridden per browser (localStorage 'usage.palette')
    fixed = {k: bool(v) for k, v in _theme_explicit(ctx.theme_path).items()}
    plang = "numeric" if ctx.privacy else ("zh-TW" if ctx.lang == "zh-TW" else ("zh-CN" if ctx.lang.startswith("zh") else latin))
    cu, co = usage_palette.parse_custom(ctx.palette) or (usage_palette.CUSTOM_DEFAULT["under"], usage_palette.CUSTOM_DEFAULT["over"])
    cc_now = {"under": cu, "over": co}
    paldata = json.dumps({"default": ctx.pal_key, "base": ctx.palette, "pat": ctx.patterns, "osfx": ctx.key_suffix, "light": T["is_light"],
                          "table": dict(usage_palette.js_table(), **({ctx.palette: {v: usage_palette.get(ctx.palette, v == "light") for v in ("dark", "light")}}
                                                                       if usage_palette.is_custom(ctx.palette) or ctx.palette in usage_palette.LEGACY else {})),
                          "cc": cc_now, "alias": {"colorblind": "orange-deepblue"}, "fixed": fixed,
                          "bg": bg, "accent": ac, "under": T["under_color"], "gen": gen, "pf": bool(_theme_file(ctx.theme_path).get("patterns")), "s": PAL_STR[plang]},
                         ensure_ascii=False).replace("<", "\\u003c")
    variant = "light" if T["is_light"] else "dark"

    def pal_row(i, n):
        c = usage_palette.PALETTES[n][variant]
        name = (str(i + 1) + (" " + usage_palette.STAR if usage_palette.cvd_safe(n) else "")) if plang == "numeric" else usage_palette.label(n, plang)
        return (f"<li role='option' id='po-{n}' data-v='{n}' aria-selected='{'true' if n == ctx.palette else 'false'}'>"
                f"<i class='dot' style='background:{c['over']}'></i><i class='dot' style='background:{c['under']}'></i>"
                f"<span>{html.escape(name)}</span></li>")
    pnames = usage_palette.names()
    palrows = [pal_row(i, n) for i, n in enumerate(pnames) if n not in usage_palette.MONO]
    if ctx.palette in usage_palette.LEGACY:  # configured legacy preset: listed (last in its section) so the menu shows it
        palrows.append(pal_row(len(pnames), ctx.palette))
    grp = "◐" if plang == "numeric" else usage_palette.MONO_GROUP[plang]
    if plang != "numeric":  # the ★ note closes the two-colour section; the solid colours follow under their header
        palrows.append(f"<li role='none' class='pnote' id='palnote'>{html.escape(usage_palette.STAR_NOTE[plang])}</li>")
    palrows.append(f"<li role='presentation' class='pgrp' id='palgrp'>{html.escape(grp)}</li>")  # solid colours
    palrows += [pal_row(pnames.index(n), n) for n in usage_palette.MONO]
    is_cc = usage_palette.is_custom(ctx.palette)
    cname = "✎" if plang == "numeric" else usage_palette.label(usage_palette.CUSTOM, plang)
    palrows.append(f"<li role='option' id='po-custom' data-v='custom' class='pcust' aria-selected='{'true' if is_cc else 'false'}'>"
                   f"<i class='dot' style='background:{co}'></i><i class='dot' style='background:{cu}'></i><span>{html.escape(cname)}</span></li>")
    sel_id = "custom" if is_cc else ctx.palette
    palcur = next(r for r in palrows if f"data-v='{sel_id}'" in r).split(">", 1)[1].rsplit("</li>", 1)[0]
    CP = CPICK_STR[plang]
    cpick_html = (f"<span class='pal cpick' id='cpick'{'' if is_cc else ' hidden'}><label>{html.escape(CP['under'])} <input type='color' id='ccu' value='{cu.lower()}'></label>"
                  f"<label>{html.escape(CP['over'])} <input type='color' id='cco' value='{co.lower()}'></label>"
                  f"<span class='cchint' id='cchint' role='status'{'' if usage_palette.similar(cu, co) else ' hidden'}>{html.escape(CP['hint'])}</span></span>")
    TB = l10n.TOOLBAR.get(plang, {})
    pal_label = {"numeric": "◐", "zh-TW": "配色", "zh-CN": "配色"}.get(plang, TB.get("pal", "Colours"))
    pat_label = {"numeric": "▨", "zh-TW": "加上紋路", "zh-CN": "加上纹路"}.get(plang, TB.get("pat", "Add patterns"))
    off_label = {"numeric": "±", "zh-TW": "顯示校正", "zh-CN": "显示校正"}.get(plang, TB.get("off", "Display offset"))
    off_opts = "".join(f"<option value='{o}'{' selected' if o == ctx.display_offset else ''}>{html.escape(usage_offset.option_text(o, plang))}</option>"
                       for o in range(usage_offset.MIN, usage_offset.MAX + 1))
    PK = PICK_STR["numeric" if ctx.privacy else (ctx.lang if ctx.lang in PICK_STR else plang)]
    lang_opts = "".join(f"<option value='{k}'{' selected' if k == ctx.lang_setting else ''}>{html.escape(PK['auto'] if k == 'auto' else LANG_NAMES[k])}</option>"
                        for k in PAGE_LANGS)
    zones = list(PAGE_ZONES) + ([ctx.tz_setting] if ctx.tz_setting not in PAGE_ZONES + ("auto",) else [])
    tz_opts = (f"<option value='auto'{' selected' if ctx.tz_setting == 'auto' else ''}>{html.escape(PK['auto'])} ({tz_option_label('local')})</option>"
               + "".join(f"<option value='{html.escape(z)}'{' selected' if z == ctx.tz_setting else ''}>{html.escape(z)} ({tz_option_label(z) if valid_zone(z) else '?'})</option>"
                         for z in zones))
    anim_opts = "".join(f"<option value='{m}'{' selected' if m == ctx.anim_setting else ''}>{html.escape(PK[m])}</option>" for m in ANIM_MODES)
    lock_attr = lambda on: f" data-locked='1' title='{html.escape(PK['locked'])}'" if on else ""
    # Read interval: how often the sampler reads /usage (next cycle); disabled in manual-entry mode (nothing is read)
    iv_opts = "".join(f"<option value='{m}'{' selected' if m == ctx.interval else ''}>{html.escape(PK['minutes'].format(n=m))}</option>"
                      for m in sorted(set(INTERVAL_CHOICES) | {ctx.interval}))
    iv_lock = f" data-locked='1' title='{html.escape(PK['manual_only'])}'" if manual_mode(ctx) else ""
    # toolbar row 1 (settings) gets language + time zone; row 2 (actions) gets the animation menu + Applying status
    pick_html = (f"<label class=\"pal\" id=\"langl\">{html.escape(PK['lang'])} <select id=\"langsel\" class=\"offsel\" data-api=\"/api/lang\" data-k=\"usage.lang\" data-f=\"lang\"{lock_attr(ctx.lang_locked)}>{lang_opts}</select></label>"
                 f"<label class=\"pal\" id=\"tzl\">{html.escape(PK['tz'])} <select id=\"tzsel\" class=\"offsel\" data-api=\"/api/tz\" data-k=\"usage.tz\" data-f=\"tz\"{lock_attr(ctx.tz_locked)}>{tz_opts}</select></label>")
    # Mascot menu (row 2, after Read interval, next to the other page-only display menu): applied at once; not in privacy mode
    MSK = MASCOT_STR.get(ctx.lang if ctx.lang in MASCOT_STR else plang) or MASCOT_STR["en"]
    m_opts = "".join(f"<option value='{m}'{' selected' if m == ctx.mascot_setting else ''}>{html.escape(MSK['opts'][m])}</option>" for m in mascot_choices(ctx))
    mascot_menu = "" if ctx.privacy else (
        f"<label class=\"pal\" id=\"mascotl\">{html.escape(MSK['label'])} <select id=\"mascotsel\" class=\"offsel\" data-api=\"/api/mascot\" "
        f"data-k=\"usage.mascot\" data-f=\"mascot\" data-live=\"1\">{m_opts}</select></label>")
    # Seasonal accessories menu (right after Mascot): auto / off; hidden while the mascot is off or Custom (the menu
    # above toggles it); a change rebuilds the page and reloads it, since the accessory is drawn at build time
    s_opts = "".join(f"<option value='{m}'{' selected' if m == ctx.seasonal_setting else ''}>{html.escape(MSK['sopts'][m])}</option>" for m in SEASONAL_CHOICES)
    if not ctx.privacy:
        mascot_menu += (f"<label class=\"pal\" id=\"seasonl\"{' hidden' if ctx.mascot_setting in ('off', MASCOT_CUSTOM) else ''}>{html.escape(MSK['seasonal'])} "
                        f"<select id=\"seasonsel\" class=\"offsel\" data-api=\"/api/seasonal\" data-k=\"usage.seasonal\" data-f=\"seasonal\">{s_opts}</select></label>")
    act_html = (f"<label class=\"pal\" id=\"animl\">{html.escape(PK['anim'])} <select id=\"animsel\" class=\"offsel\" data-api=\"/api/anim\" data-k=\"usage.anim\" data-f=\"anim\" data-live=\"1\"{lock_attr(ctx.anim_locked)}>{anim_opts}</select></label>"
                f"<label class=\"pal\" id=\"ivl\">{html.escape(PK['interval'])} <select id=\"ivsel\" class=\"offsel\" data-api=\"/api/interval\" data-k=\"usage.interval\" data-f=\"minutes\" data-live=\"1\" data-srv=\"1\"{iv_lock}>{iv_opts}</select></label>{mascot_menu}"
                 f"<span id=\"applying\" class=\"applying-msg\" role=\"status\" aria-live=\"polite\" hidden><span class=\"spin\"></span>{html.escape(PK['applying'])}</span>"
                 f"<script>{PICK_JS}</script>")
    onac, hl = _on_colour(ac), _blend(bg, tx, 0.14)
    # declare the page's own scheme: without it Chrome's Auto Dark Mode (and similar) lightens dark inline-SVG fills,
    # so the Black mascot turned white with grey eyes and dark eyes / mouths went pale on a dark page
    cs = 'light' if T.get('is_light') else 'dark'
    ff = '"Noto Sans CJK TC","Noto Sans TC","PingFang TC","Microsoft JhengHei","Segoe UI",sans-serif'
    if ctx.lang in LATIN_LANGS:  # Latin fonts first: CJK fonts draw ’ “ ” « » full-width; CJK text still falls back
        ff = '"Segoe UI","Noto Sans","Helvetica Neue",Arial,' + ff.replace(',"Segoe UI"', '')
    doc = f"""<!doctype html><html lang="{ {'zh-TW': 'zh-Hant', 'zh-CN': 'zh-Hans', 'ja': 'ja', 'ko': 'ko', **l10n.HTML_LANG}.get(ctx.lang, 'en') }" data-patterns="{'1' if T.get('patterns') else ''}"><head><meta charset="utf-8"><title>{html.escape(title or '↻')}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="{cs}"><style>
:root{{color-scheme:{cs}}}
body{{margin:0;background:{bg};color:{tx};font-family:{ff}}}
.wrap{{max-width:1060px;margin:0 auto;padding:20px}} .top{{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:1px}} h1{{margin:4px 0 8px;font-size:26px}}
.bar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}} .upd{{color:{dim};font-size:13px;display:flex;align-items:center;gap:10px}}
nav{{display:inline-flex;border:1px solid var(--bd);border-radius:10px;padding:4px;background:{card};margin:6px 0 14px}}
nav>button{{background:none;border:1px solid transparent;color:{dim};font:inherit;font-weight:700;font-size:15px;padding:6px 18px;border-radius:7px;cursor:pointer}}
nav>button.on{{color:{tx};border-color:var(--accent)}} section{{display:none}} section.on{{display:block}} img{{width:100%;border:1px solid var(--bd);border-radius:10px}}
:root{{--over:{T['over_color']};--under:{T['under_color']};--baseline:{T['baseline_color']};--notice:{T['notice_color']};--accent:{ac};--bd:{bd};--on-accent:{onac};--acc-hl:{'#FFFFFF' if onac != '#FFFFFF' else ac};{mascot_auto_vars(ac)};--card:{card}}}
a{{color:var(--accent)}} :focus-visible{{outline:2px solid var(--accent);outline-offset:2px}} input[type=checkbox],input[type=radio]{{accent-color:var(--accent)}}
nav>button:hover{{color:{tx};border-color:var(--bd)}} .btn:hover,#ckbtn:hover,#ckr button:hover{{border-color:var(--accent)}}
.warn{{border:1px solid var(--notice);color:var(--notice);border-radius:8px;padding:8px 12px;margin-bottom:10px;font-weight:700}}
.tables{{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-top:12px}} .clocks{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} figure{{margin:0}}
.clkbar{{display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:8px}} .clocks{{align-items:start}} .clkbar .plist{{min-width:8em}} .psel[hidden]{{display:none}}
table{{border-collapse:collapse;width:100%;background:{card};border:1px solid var(--bd);font-size:13px}} caption{{text-align:left;font-weight:700;padding:6px 0}}
th,td{{padding:5px 10px;border-bottom:1px solid var(--bd);text-align:left}} th{{color:{dim}}} .over{{color:var(--over);font-weight:700}} .under{{color:var(--under);font-weight:700}}
.sub{{color:{dim};font-size:13px;margin:6px 0}} .ckform{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}}
input,select{{background:{card};color:{tx};border:1px solid var(--bd);border-radius:6px;padding:6px 10px;font:inherit}}
.btn,#ckbtn,#ckr button{{background:{card};border:1px solid var(--bd);color:{tx};font:inherit;font-weight:700;padding:5px 14px;border-radius:7px;cursor:pointer}}
#ckr button.on,.btn{{border-color:var(--accent)}} .btn:disabled{{opacity:.6;cursor:wait}}
.btn.primary{{background:var(--accent);color:var(--on-accent);border-color:var(--accent);padding:6px 18px}} .btn.primary:hover{{filter:brightness(1.1)}}
.btn.updgo{{background:#A5F3FC;color:#06303A;border:2px solid #22D3EE;padding:6px 18px}} .btn.updgo:hover{{filter:brightness(1.06)}}
.urow{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:2px 0 10px;font-size:13px}} .unotes{{flex:1 1 100%;white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;padding:8px 10px;border:1px solid var(--bd);border-radius:8px;color:{dim};font-size:12.5px;line-height:1.5}}
.top .ver{{color:{dim};font-weight:400;letter-spacing:0;margin-left:6px}} .top .ver::after{{content:' · amber since 2026';color:#e8a33d;opacity:0;transition:opacity .5s;font-style:italic}} .top .ver:hover::after{{opacity:.5}} #umsg{{font-weight:700;color:{dim}}} #ckmsg,#rmsg,#mmsg{{color:var(--notice);font-weight:700}} [hidden]{{display:none!important}}
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
.plist li.pgrp{{cursor:default;color:{dim};font-size:12px;font-weight:700;letter-spacing:.04em;border-top:1px solid var(--bd);border-radius:0;margin-top:6px;padding:8px 10px 3px}}
.plist li.pcust{{border-top:1px solid var(--bd);border-radius:0 0 5px 5px;margin-top:4px}}
.cpick{{display:inline-flex;align-items:center;gap:8px}} .cpick label{{display:inline-flex;align-items:center;gap:4px}}
.cpick input[type=color]{{width:30px;height:22px;padding:0;border:1px solid var(--bd);border-radius:4px;background:{card};cursor:pointer}} .cchint{{color:{dim};font-size:12px;white-space:normal!important;max-width:24em}}
.plist li.pnote{{cursor:default;color:{dim};font-size:12px;line-height:1.45;border-top:1px solid var(--bd);border-radius:0;margin-top:4px;padding:7px 8px 4px;white-space:normal}}
.ptog{{display:inline-flex;align-items:center;gap:5px;cursor:pointer}} .ptog input{{margin:0}} .offsel{{padding:3px 6px}} .otag{{color:{dim};font-size:.85em;font-weight:400}}
{usage_checkins_ui.CSS}
{usage_breakdown_ui.CSS}
{GAUGE_CSS} {WKVIEW_CSS} {SEL_CSS} {clocktip_css(card, tx, dim)}{gh_css(_on_colour(bg) == "#FFFFFF")} {mascot_css()}
</style></head><body><div class="wrap"><div class="top">{html.escape(L['eyebrow'])}{f' <span class="ver" id="appver">v{APP_VERSION}</span>' if APP_VERSION else ''}</div><div class="hrow" id="hrow"><h1>{html.escape(title)}</h1>{mascot_html(ctx, ctx.lang if ctx.lang in MASCOT_STR else plang)}</div>
<div class="bar"><nav>{''.join(tabs)}</nav><div class="upd"><div class="trow tset" id="tset"><span id="last" title="{html.escape(L['last'].format(t=f'{now:%H:%M}', z=zl))}">{html.escape(L.get('last_short', L['last']).format(t=f'{now:%H:%M}', z=zl))}</span>
<span class="pal psel" id="psel"><span id="pall">{html.escape(pal_label)}</span><button type="button" class="pbtn" id="palbtn" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="pall palcur"><span id="palcur">{palcur}</span></button>
<ul class="plist" id="pallist" role="listbox" tabindex="-1" aria-labelledby="pall" hidden>{''.join(palrows)}</ul><span id="palmsg" role="status"></span></span>{cpick_html}
<label class="pal ptog" id="patl"><input type="checkbox" id="patchk"{" checked" if ctx.patterns else ""}> {html.escape(pat_label)}</label>
<label class="pal" id="offl">{html.escape(off_label)} <select id="offsel" class="offsel">{off_opts}</select></label><script>{OFFSET_JS}</script>{pick_html}</div>
<div class="trow tact" id="tact">{act_html}<button class="btn" id="manualbtn" hidden>{html.escape(MS['btn'])}</button><button class="btn" id="notifybtn" hidden>{html.escape(LS['notify'])}</button><button class="btn primary" id="refresh" hidden>{html.escape(L['refresh'])}</button><span id="rmsg" role="status" aria-live="polite"></span>
<span class="ugrp" id="ugrp"><button class="btn" id="updbtn" hidden>{html.escape(US['btn'])}</button><button class="btn" id="updback" hidden>{html.escape(US['back'])}</button></span><span class="agrp" id="agrp"><label class="pal" id="updautol" hidden><input type="checkbox" id="updauto"> {html.escape(US['auto'])}</label>{gh_link_html("numeric" if ctx.privacy else ctx.lang)}</span></div></div></div>
<div id="urow" class="urow" hidden><span id="umsg"></span><button class="btn updgo" id="updgo" hidden>{html.escape(US['go'])}</button>
<div id="unotes" class="unotes" role="region" aria-label="{html.escape(US['notes'] or US['go'])}" hidden></div></div>
<form id="manualform" class="ckform" hidden><label>{html.escape(MS['pct'])} <input id="mpct" type="number" min="0" max="200" step="0.1" required style="width:6em"> <span class="sub">{html.escape(MS.get('pct_note', ''))}</span></label>
<label>{html.escape(MS['reset'])} <input id="mreset" maxlength="64" placeholder="{html.escape(MS['reset_ph'])}" style="width:22em"></label>
<button class="btn" type="submit">{html.escape(MS['save'])}</button><span class="sub">{html.escape(MS['hint'])}</span><span id="mmsg"></span></form>
<div id="ledger"></div>{''.join(panes)}</div>
<script type="application/json" id="ckdata">{ckdata}</script><script>{usage_checkins_ui.JS}</script>
<script type="application/json" id="bddata">{bddata}</script><script>{usage_breakdown_ui.JS}</script>
<script type="application/json" id="ledgerdata">{ledjson}</script><script type="application/json" id="ledgerstr">{lsjson}</script><script>{LEDGER_JS}</script>
<script type="application/json" id="gaugedata">{gjson}</script><script>{GAUGE_JS}</script><script>{WKVIEW_JS}</script><script>{SEL_JS}</script>
<script type="application/json" id="paldata">{paldata}</script><script>{PALETTE_JS}</script>
<script type="application/json" id="clocktips">{ctjson}</script><script>{CLOCKTIP_JS}</script>
<script type="application/json" id="updstr">{usjson}</script><script>{UPDATE_JS}</script>
{'' if ctx.privacy else f'<script>{MASCOT_JS}</script>'}
<script>
var TK=(document.querySelector('meta[name="usage-token"]')||{{}}).content||'';
document.querySelectorAll('nav>button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav>button,section').forEach(e=>e.classList.remove('on'));
b.classList.add('on');document.getElementById('p-'+b.dataset.k).classList.add('on');if(history.replaceState)history.replaceState(null,'','#'+b.dataset.k);}});
(function(){{var $=id=>document.getElementById(id),served=/^https?:$/.test(location.protocol),T={rjs};
var rb=$('refresh'),rm=$('rmsg');
var mb=$('manualbtn'),mf=$('manualform'),mm=$('mmsg');
if(served)fetch('/api/status').then(r=>r.ok?r.json():null).then(j=>{{if(!j||!j.ok)return;mb.hidden=false;if(j.pct_source!=='manual')rb.hidden=false;
if(j.pct_source==='manual')mf.hidden=false;
if(j.last_update){{$('last').textContent=j.last_update_short||j.last_update;$('last').title=j.last_update;}}}}).catch(()=>{{}});
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


class ReadUnavailable(RuntimeError):
    """The Grok Build screen could not be read. `transient` = trying again in a minute may help (False for
    problems a retry cannot fix: sign-in requested, Grok Build / tmux / pywinpty missing)."""

    def __init__(self, msg, transient=True):
        super().__init__(msg)
        self.transient = transient


# Reasons (grok_usage_reader.ScreenReadError.reason) that a short wait cannot fix.
_PERMANENT_READ_REASONS = ("login_required", "no_tmux", "no_winpty", "not_found")
# Sampler: pauses before the 2nd and 3rd try of a failed read, before waiting for the next interval.
SAMPLE_RETRY_DELAYS = (20, 60)


def _recursion_where(exc):
    """'file.py:123 func <- ...' for the innermost frames of a RecursionError (code locations only, no data)."""
    import traceback
    try:
        fr = traceback.extract_tb(exc.__traceback__)[-3:]
        return " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(fr))
    except Exception:
        return "?"


def take_sample(ctx, pct=None, source="sampler", fail_reason=None, reset_at=None, phase=None, draw_lock=None,
                record_failure=True):
    """One reading -> samples.csv + shared ledger (usage_ledger.py).
    `pct` given = a manual reading (no Grok Build launch); `reset_at` (aware datetime) optionally
    goes with it and is cached to state.json.
    Shares <data_dir>/.read.lock with the sampler loop and manual refresh.
    A failed read is recorded as 'unavailable' (never 0%, never a guessed reset) and raises RuntimeError.
    `record_failure=False` (sampler retries): a failed read that a retry may fix is not written anywhere and
    raises ReadUnavailable at once, so only the last try of a series leaves an 'unavailable' row.
    `phase(name)` is called with "read" and "draw" (the page shows it during a refresh); the result has
    `timings` = seconds spent reading and drawing (also printed to the log)."""
    say = phase or (lambda _p: None)
    t_start = time.monotonic()
    say("read")
    lk = FileLock(ctx.data_dir / ".read.lock")
    if not lk.acquire(timeout=600):
        raise RuntimeError("read lock timeout")
    err = None
    transient = True
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
        except RecursionError as exc:
            # Seen once on a first launch right after installing Grok Build on Windows; a manual read a moment
            # later worked. Treated as an ordinary 'unavailable' read (never a crash, never 0%).
            print(f"Warning: RecursionError while reading Grok Build at {_recursion_where(exc)}", file=sys.stderr)
            pct, reset_at = None, None
            err = ("the Grok Build reader hit Python's recursion limit (usually temporary right after "
                   "installing or updating Grok Build; it is tried again)")
            transient = True
        except Exception as exc:
            pct, reset_at, err = None, None, str(exc) or type(exc).__name__
            transient = getattr(exc, "reason", None) not in _PERMANENT_READ_REASONS
        if pct is None and not record_failure and transient:   # a permanent failure is recorded at once
            raise ReadUnavailable("source read failed" if ctx.privacy else f"unavailable: {err}", transient)
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
        raise ReadUnavailable("source read failed" if ctx.privacy else f"unavailable: {err}", transient)
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


def sample_with_retry(ctx, logw, sleep=time.sleep, delays=None):
    """One scheduled read. When the screen read is 'unavailable' for a reason that may pass (Grok Build's first
    launch, pywinpty busy), try again after each pause in `delays` (default 20 s, 60 s), logging every retry,
    before the sampler waits for the next interval. Only the last try is recorded as 'unavailable'."""
    delays = SAMPLE_RETRY_DELAYS if delays is None else delays
    for attempt in range(len(delays) + 1):
        last = attempt == len(delays)
        try:
            logw(json.dumps(take_sample(ctx, record_failure=last), ensure_ascii=False))
            return True
        except Exception as exc:
            logw(f"sample failed: {exc}")
            if last or not getattr(exc, "transient", False):   # permanent problems were recorded at once
                return False
            logw(f"retry {attempt + 1}/{len(delays)} in {delays[attempt]} s (the read was unavailable; may be temporary)")
            sleep(delays[attempt])
    return False


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
            sample_with_retry(ctx, logw)
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
        u = "unavailable" if d["usage_rise"] is None else f"+{d['usage_rise']:g}%"
        print(f"\n{d['date']}  {d['count']} session(s) · {usage_checkins.fmt_duration(d['total_min'] * 60)} · usage {u}")
        for x in d["sessions"]:
            t1 = x["start"][11:16]
            t2 = ("–" + x["end"][11:16]) if x["end"] else ""
            ud = x["usage"]
            us = "—" if not ud else (f"{ud['delta']:+g}%" if ud["status"] == "ok" else "unavailable")
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
    try:  # the commit this server runs; a saved "previous version" equal to it is not offered
        import self_update as _su
        boot_head = _su.git(HERE, "rev-parse", "HEAD") if (HERE / ".git").exists() else None
    except Exception:
        boot_head = None
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
        if not palette_name_ok(name):
            return 400, {"ok": False, "error": "unknown palette"}
        pat = d.get("patterns", False)
        if not isinstance(pat, bool):
            return 400, {"ok": False, "error": "patterns must be true or false"}
        mode = d.get("mode")
        if clock is not None and clock not in CLOCK_RANGES:
            return 400, {"ok": False, "error": "unknown clock range"}
        if mode is not None and mode not in CLOCK_MODES:
            return 400, {"ok": False, "error": "unknown clock layout"}
        dial = d.get("dial")
        if dial is not None and dial not in CLOCK_DIALS:
            return 400, {"ok": False, "error": "unknown clock dial"}
        with draw_lock:
            try:
                out = render_palette(ctx, name, clock, mode, pat, dial)
                if out.get("ok") and usage_palette.is_custom(name):  # the page sets its CSS colours from these
                    out["colors"] = {v: usage_palette.get(name, v == "light") for v in ("dark", "light")}
                return 200, out
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

    def last_update_short():
        # the toolbar shows the time only (the zone is in the Time zone menu beside it); the full text is the tooltip
        p = ctx.hist_dir / "index.html"
        if not p.exists():
            return None
        t = datetime.fromtimestamp(p.stat().st_mtime, ctx.tz)
        return ctx.L.get("last_short", ctx.L["last"]).format(t=f"{t:%H:%M}", z=ctx.zlabel(t))

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
                   "/api/update/rollback", "/api/palette/render", "/api/patterns", "/api/display-offset", "/api/lang", "/api/tz", "/api/anim", "/api/mascot", "/api/seasonal", "/api/interval")

    def pick_setting(key, d):
        """POST /api/lang {"lang": "auto"|"zh-TW"|"zh-CN"|"en"|"ja"|"ko"|"numeric"|"id"|"de"|"es"|"fr"|"pt"} or /api/tz {"tz": "auto"|<IANA zone>}:
        saved as "lang" / "tz" in usage_config.json (or <data_dir>/page_settings.json); charts and page are redrawn
        before this returns. A --lang / --tz flag given at start still wins ("locked": true)."""
        v = d.get(key)
        if key == "anim" and v not in ANIM_MODES:
            return 400, {"ok": False, "error": "anim must be off, once or continuous"}
        if key == "mascot" and not (v in MASCOT_CHOICES or (v == MASCOT_CUSTOM and custom_mascot_uri(ctx.data_dir))):
            return 400, {"ok": False, "error": "unknown mascot colour"}
        if key == "seasonal" and v not in SEASONAL_CHOICES:
            return 400, {"ok": False, "error": "seasonal must be auto or off"}
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
        if key in ("anim", "mascot", "seasonal"):  # page-only switch: rebuild the page, no chart redraw
            with draw_lock:
                try:
                    rewrite_page(ctx)
                except Exception as exc:
                    print(f"Warning: {exc}", file=sys.stderr)
            if key == "mascot":
                return 200, {"ok": True, key: ctx.mascot_setting, "locked": False}
            if key == "seasonal":
                return 200, {"ok": True, key: ctx.seasonal_setting, "locked": False}
            return 200, {"ok": True, key: ctx.anim_setting, "locked": ctx.anim_locked}
        regen()
        return 200, {"ok": True, key: v, "locked": ctx.lang_locked if key == "lang" else ctx.tz_locked}

    def interval_setting(d):
        """POST /api/interval {"minutes": 15..1440}: saved as "sample_interval_minutes" in usage_config.json (or
        <data_dir>/page_settings.json); the sampler re-reads it every minute and uses it from its next cycle."""
        v = d.get("minutes")
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not INTERVAL_MIN <= v <= INTERVAL_MAX:
            return 400, {"ok": False, "error": f"minutes must be an integer from {INTERVAL_MIN} to {INTERVAL_MAX}"}
        if manual_mode(ctx):
            return 400, {"ok": False, "error": "pct_source is manual: nothing is read automatically", "code": "manual_mode"}
        try:
            with usage_lock.write_lock(ctx.data_dir):
                save_page_setting(ctx.cfg_path, ctx.data_dir, "sample_interval_minutes", v)
            with draw_lock:
                ctx.reload()
                rewrite_page(ctx)
        except Exception as exc:
            return 500, {"ok": False, "error": "write failed" if ctx.privacy else f"could not save the setting: {exc}"}
        return 200, {"ok": True, "minutes": ctx.interval}

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
        import self_update
        r = dict(r)
        r.pop("notes", None)
        if r.get("state") in ("behind", "local_changes", "diverged") and not ctx.privacy:
            try:  # English CHANGELOG.md of the fetched origin/main (git show: no extra network), plain text, capped
                r["notes"] = self_update.changelog_since(HERE)
            except Exception:
                r["notes"] = ""
        if ctx.privacy:  # commit subjects and git messages may name services: hash and counts only
            r["detail"] = ""
            if r.get("latest"):
                r["latest"] = {"hash": r["latest"].get("hash"), "subject": ""}
        return r

    def rollback_public():
        import self_update
        p = self_update.load_previous(ctx.data_dir)
        if not p or p["commit"] == boot_head:
            return None
        return {"short": p["commit"][:7], "version": p.get("version") or ""}

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
                "version": APP_VERSION, "previous": rollback_public(),
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

    def update_rollback(client_ip):
        """POST /api/update/rollback -> start the detached updater in rollback mode (back to update_prev.json)."""
        import self_update
        if ctx.host not in ("127.0.0.1", "localhost", "::1") or client_ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return 403, {"ok": False, "error": "rollback can only be started from this computer"}
        if not upd_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "busy"}
        try:
            if state["updating"] or self_update.updater_busy(ctx.data_dir):
                return 409, {"ok": False, "error": "busy"}
            r = self_update.rollback_check(HERE, ctx.data_dir)
            if not r.get("can_rollback"):
                return 409, {"ok": False, "error": r.get("state"), "detail": "" if ctx.privacy else r.get("detail", "")}
            cfg = str(Path(ctx.cfg_path).resolve()) if ctx.cfg_path else None
            if not ctx.port:
                (ctx.data_dir / "update.port").write_text(str(port_box["port"]), encoding="utf-8")
            if not self_update.spawn(HERE, ctx.data_dir, cfg, wait_pid=os.getpid(), rollback=True):
                return 500, {"ok": False, "error": "could not start the updater"}
            state["updating"] = True
            return 200, {"ok": True, "boot": boot, "version": r.get("version"), "short": r.get("short")}
        except Exception as exc:
            return 500, {"ok": False, "error": "updater failed" if ctx.privacy else f"could not start the rollback: {exc}"}
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
                if usage_breakdown.newest_mtime(ctx.data_dir) > p.stat().st_mtime:  # a snapshot was dropped in: page only
                    with draw_lock:
                        try:
                            rewrite_page(ctx)
                        except Exception as exc:
                            print(f"Warning: {exc}", file=sys.stderr)
                page = local_guard.inject_token(p.read_text(encoding="utf-8"), token)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            parts = u.path[1:].split("/")
            if (parts[-1] == "week-current.json" or CLOCK_JSON_RE.match(parts[-1])) and (len(parts) == 1 or (len(parts) == 3 and parts[0] == "pal" and is_dir_key(parts[1]))) \
                    and all(x and x not in (".", "..") for x in parts):
                p = ctx.hist_dir.joinpath(*parts)  # gauge overlay geometry / clock check-in tooltips (what the page already shows)
                return self._send(200, p.read_bytes()) if p.is_file() else self._send(404, {"ok": False, "error": "not found"})
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
                return self._send(200, {"ok": True, "last_update": last_update(), "last_update_short": last_update_short(), "busy": busy.locked(), "timezone": ctx.zlabel(),
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
            if u.path == "/api/update/rollback":
                return self._send(*update_rollback(self.client_address[0]))
            if u.path == "/api/palette/render":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*palette_render(d))
            if u.path in ("/api/lang", "/api/tz", "/api/anim", "/api/mascot", "/api/seasonal"):
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*pick_setting(u.path[5:], d))
            if u.path == "/api/interval":
                try:
                    n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 1000)).decode("utf-8") if n else ""
                    d = json.loads(raw or "{}")
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad json"})
                if not isinstance(d, dict):
                    return self._send(400, {"ok": False, "error": "bad json"})
                return self._send(*interval_setting(d))
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
                                   "status", "ledger", "breakdown"])
    p.add_argument("rest", nargs="*", help="checkin/start/end: NAME [NOTE...]; search: WORDS...; breakdown: FILE or - (stdin)")
    p.add_argument("--config"); p.add_argument("--data-dir", dest="data_dir"); p.add_argument("--tz")
    p.add_argument("--lang"); p.add_argument("--theme"); p.add_argument("--pace", type=float)
    p.add_argument("--anim", choices=ANIM_MODES, help="page gauge animation (default: config 'anim' or once)")
    p.add_argument("--palette", type=_palette_arg, metavar="PALETTE",
                   help="chart/page colours: a preset (" + ", ".join(usage_palette.names()) + "; legacy: high-contrast), or custom (colours from the config's "
                        "custom_colors) or custom-RRGGBBrrggbb (under, over); default: config 'palette' or orange-teal")
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
    elif a.cmd == "breakdown":  # import a usage breakdown snapshot (JSON file or stdin); no argument: show the newest
        if a.rest:
            try:
                text = sys.stdin.read() if a.rest[0] == "-" else Path(a.rest[0]).read_text(encoding="utf-8-sig")
            except OSError as exc:
                print(json.dumps({"ok": False, "error": f"cannot read file: {exc.strerror}"})); sys.exit(2)
            r = breakdown_import(ctx, text)
        else:
            snaps = usage_breakdown.load(ctx.data_dir, ctx.tz)
            r = {"ok": bool(snaps), "snapshots": len(snaps), "latest": snaps[-1] if snaps else None}
        print(json.dumps(r, ensure_ascii=False)); sys.exit(0 if r["ok"] else 2)
    elif a.cmd == "ledger":  # for assistants: exit 0 = fresh, 3 = stale or missing
        import usage_ledger
        r = usage_ledger.read(ctx.ledger_path, a.max_age_hours)
        r["path"] = ctx.ledger_path
        print(json.dumps(r, ensure_ascii=False)); sys.exit(0 if r["fresh"] else 3)


if __name__ == "__main__":
    main()
