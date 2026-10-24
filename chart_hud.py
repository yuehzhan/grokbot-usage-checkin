"""
chart_hud.py - Multi-Panel Sci-Fi HUD Weekly Usage Telemetry Dashboard Renderer

USAGE
-----
Positional CLI (drop-in compatible with chart.py / record.py):
    python3 chart_hud.py <week_csv> <out_png> <week_start_date YYYY-MM-DD>

Manual single-reading or override mode (renders even if CSV does not exist):
    python3 chart_hud.py [week_csv] [out_png] [week_start_date] --pct 73 [--day 5]

With optional customization flags:
    python3 chart_hud.py usage.csv out.png 2026-10-17 \
        --theme themes/light.json \
        --lang auto \
        --privacy \
        --title "Usage Dashboard" \
        --pace 14 \
        --bg-color "#000000" \
        --accent-color "#7FE7FF" \
        --palette orange-teal \
        --baseline-color "#9AA4AE" \
        --under-color "#23C4AE" \
        --over-color "#FFA51F" \
        --text-color "#E8F4FF"

Python API (used by usage_record.py):
    import chart_hud
    chart_hud.render(rows, out_png, week_start, cfg)

LANGUAGES (`--lang`) & PRIVACY MODE (`--privacy`)
-------------------------------------------------
- `auto` (default): Detects system locale (`zh-TW`, `zh-CN`, `ja`, `ko`, or `en`).
- `en`, `zh-TW`, `zh-CN`, `ja`, `ko`: Explicit language selection.
- `numeric` / `--privacy`: Discreet, language-neutral render using numbers, `%`, `D1..D7`,
  plain date range `"MM-DD → MM-DD"`, timestamp-only footer, symbols (`▲`, `▼`, `Δ`, `↻`, `→`),
  and color-only legends with zero words or product/timezone identifiers.
- Custom overlays: Place a JSON file at `locales/<lang>.json` next to `chart_hud.py` to add or
  override any locale (missing keys fall back to `BASE_STRINGS` in English).
"""

import argparse, ctypes, csv, json, locale, os, re, sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import to_hex, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch, Rectangle, Wedge

FIG_W, FIG_H = 1800.0, 1500.0
Y_CAP, Y_MAX = 109.0, 125.0

FONT_CANDIDATES = {
    "zh-TW": [
        "Noto Sans CJK TC", "Microsoft JhengHei", "PingFang TC", "Heiti TC", "Noto Serif CJK TC",
        "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK JP",
    ],
    "zh-CN": [
        "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "Heiti SC", "Noto Serif CJK SC",
        "Noto Sans CJK TC", "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK JP",
    ],
    "ja": [
        "Noto Sans CJK JP", "Yu Gothic", "Meiryo", "Hiragino Sans", "Hiragino Kaku Gothic ProN",
        "Noto Serif CJK JP", "Noto Sans CJK TC", "Noto Sans CJK SC", "Microsoft JhengHei", "Microsoft YaHei",
    ],
    "ko": [
        "Noto Sans CJK KR", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Serif CJK KR",
        "Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans CJK JP",
    ],
    "en": ["DejaVu Sans", "Segoe UI", "Arial", "Helvetica"],
    "numeric": ["DejaVu Sans", "Segoe UI", "Arial", "Helvetica"],
}

# Colour rule (same in every tool): orange = over the baseline, the palette's cool colour = under,
# grey = the baseline line itself. Colours come from usage_palette.py unless set explicitly.
DEFAULT_CFG = {
    "palette": "orange-teal",
    "custom_colors": None,  # with "palette": "custom": {"under": "#RRGGBB", "over": "#RRGGBB"}
    "baseline_color": None,
    "under_color": None,
    "over_color": None,
    "bg_color": "#000000",
    "text_color": None,
    "accent_color": None,
    "lang": "auto",
    "privacy": False,
    "pace": 14,
    "title": None,
    "tz": "local",
    "reset_hm": "00:00",
    "reset_at": None,  # exact next reset to show (ISO, e.g. the 'Resets:' value from /usage); else start + 7 days
    "patterns": None,  # non-colour cues (hatching, dashed over-line, ▲/▼); off unless asked for ("Add patterns")
    "display_offset": 0,  # -2..+2 added to the shown weekly % (see usage_offset.py); the CSV stays raw
}

BASE_STRINGS = {
    "font": "DejaVu Sans",
    "hud_top": "WEEKLY USAGE // TELEMETRY",
    "default_title": "Grok Bot Usage & Check-in",
    "title_custom": "{title} Weekly Usage",
    "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "tab_cum": "Cumulative",
    "tab_daily": "Daily",
    "tab_week": "Week",
    "hero_title": "WEEKLY USAGE",
    "day_of_7": "Day {d} / 7",
    "gauge_base": "Baseline {base:.0f}%",
    "tag_proj_over": "▲ over",
    "tag_proj_under": "▼ within",
    "tag_avg_over": "▲ faster",
    "tag_avg_under": "▼ slower",
    "tag_exhaust": "▲ running out",
    "vs_under": "{n:.0f}% under baseline",
    "vs_over": "{n:.0f}% over baseline",
    "vs_even": "On baseline",
    "vs_under_note": "plenty left",
    "vs_over_note": "using it fast",
    "vs_even_note": "right on pace",
    "exhaust_main": "Runs out early, ~{left:.1f} days left",
    "exhausted_now": "Weekly quota used up",
    "proj_exhaust_val": "Quota ~{left:.1f}d left",
    "proj_exhaust_val_short": "~{left:.1f}d left",
    "proj_exhaust_sub": "Quota runs out early · raw {proj:.0f}%",
    "proj_exhaust_chart": "out in ~{left:.1f}d",
    "exhaust_main_h": "Runs out early, about {hours} h left",
    "proj_exhaust_val_h": "Quota: about {hours} h left",
    "proj_exhaust_val_short_h": "About {hours} h left",
    "proj_exhaust_chart_h": "out in about {hours} h",
    "stat_proj_exhaust": "Projected run-out",
    "ring_left": "{rem:.0f}% left",
    "on_pace": "ON PACE",
    "ahead_pace": "AHEAD OF PACE",
    "burn_rate": "burn rate {rate:.1f}%/day",
    "hits_100": "Hits 100% ~Day {day:.1f}",
    "within_cap": "7d est. {proj:.0f}% (within cap)",
    "stat_vs_base": "vs Baseline",
    "stat_vs_base_sub": "Day {data_d} baseline: {base}%",
    "stat_rem": "Remaining",
    "stat_rem_sub": "of 100% weekly cap",
    "over_cap_note": "+{over:.0f}% over cap",
    "stat_avg": "Avg / Day",
    "stat_avg_sub": "target {pace}% / day",
    "stat_proj": "Projected (7d)",
    "stat_proj_sub": "7d baseline: {base7}%",
    "stat_reset": "Reset In",
    "stat_reset_sub": "{reset_date}",
    "countdown_short": "{days}d {hours}h",
    "countdown_hm": "{hours}h {mins:02d}m",
    "countdown_m": "{mins}m",
    "reset_short": "Reset",
    "stat_budget": "Daily Budget Left",
    "stat_budget_sub": "over {d_rem}d to reset (incl. today)",
    "cum_title": "Cumulative Usage vs. Baseline",
    "ylabel_cum": "Cumulative (%)",
    "day_fmt": "Day {d}",
    "limit_100": "Limit 100%",
    "baseline_line": "Baseline {pace}%/day",
    "under_base": "Under Baseline",
    "over_base": "Over Baseline",
    "proj_label": "proj {val:.0f}%",
    "no_data": "No Data",
    "bar_title": "Daily Usage",
    "ylabel_bar": "Daily (%)",
    "daily_base_line": "Daily baseline {pace}%",
    "under_short": "Under",
    "over_short": "Over",
    "gap_fmt": "{g}d",
    "log_title": "Daily Log",
    "log_col_day": "DAY / NOTE",
    "log_col_cum": "CUMULATIVE",
    "log_col_inc": "DAILY",
    "future_note": "scheduled",
    "footer": "generated {now} ({tz_label})  ·  pace {pace}%/day  ·  cap 100%",
}

TRANSLATIONS: Dict[str, Dict[str, object]] = {
    "zh-TW": {
        "font": "Noto Sans CJK TC",
        "default_title": "用量儀表板",
        "title_custom": "{title} 本週用量監控",
        "weekdays": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
        "tab_cum": "累計趨勢",
        "tab_daily": "每日增量",
        "tab_week": "本週總覽",
        "hero_title": "本週累計用量",
        "day_of_7": "第 {d} / 7 天",
        "gauge_base": "基準線 {base:.0f}%",
        "tag_proj_over": "▲超標",
        "tag_proj_under": "▼未超標",
        "tag_avg_over": "▲快於基準",
        "tag_avg_under": "▼慢於基準",
        "tag_exhaust": "▲即將用罄",
        "vs_under": "落後基準 {n:.0f}%",
        "vs_over": "超前基準 {n:.0f}%",
        "vs_even": "與基準相同",
        "vs_under_note": "用量充裕",
        "vs_over_note": "消耗較快",
        "vs_even_note": "剛好符合基準",
        "exhaust_main": "預估額度提前耗盡，剩約 {left:.1f} 天",
        "exhausted_now": "本週額度已用完",
        "proj_exhaust_val": "額度剩約 {left:.1f} 天",
        "proj_exhaust_val_short": "剩約 {left:.1f} 天",
        "proj_exhaust_sub": "額度提前耗盡 · 原始預估 {proj:.0f}%",
        "proj_exhaust_chart": "約 {left:.1f} 天後耗盡",
        "exhaust_main_h": "預估額度提前耗盡，剩約 {hours} 小時",
        "proj_exhaust_val_h": "額度剩約 {hours} 小時",
        "proj_exhaust_val_short_h": "剩約 {hours} 小時",
        "proj_exhaust_chart_h": "約 {hours} 小時後耗盡",
        "stat_proj_exhaust": "預估耗盡時間",
        "ring_left": "剩餘 {rem:.0f}%",
        "on_pace": "符合基準配速",
        "ahead_pace": "超前基準配速",
        "burn_rate": "消耗速率 {rate:.1f}%/天",
        "hits_100": "預估第 {day:.1f} 天達 100%",
        "within_cap": "7天預估 {proj:.0f}% (未超標)",
        "stat_vs_base": "比基準差異",
        "stat_vs_base_sub": "第{data_d}天基準: {base}%",
        "stat_rem": "剩餘可用額度",
        "stat_rem_sub": "每週上限 100%",
        "over_cap_note": "超額 +{over:.0f}%",
        "stat_avg": "平均每日用量",
        "stat_avg_sub": "基準 每天{pace}%",
        "stat_proj": "預估週末總量",
        "stat_proj_sub": "7天基準: {base7}%",
        "stat_reset": "距離重置",
        "stat_reset_sub": "{reset_date}",
        "countdown_short": "{days}天 {hours}小時",
        "countdown_hm": "{hours}小時 {mins:02d}分",
        "countdown_m": "{mins}分",
        "reset_short": "已重置",
        "stat_budget": "剩餘日均配額",
        "stat_budget_sub": "距重置 {d_rem} 天（含今天）",
        "cum_title": "本週累計用量 vs. 基準線",
        "ylabel_cum": "累計用量 (%)",
        "day_fmt": "第{d}天",
        "limit_100": "上限 100%",
        "baseline_line": "基準線 每天{pace}%",
        "under_base": "低於基準",
        "over_base": "高於基準",
        "proj_label": "預估 {val:.0f}%",
        "no_data": "無資料",
        "bar_title": "每日用量",
        "ylabel_bar": "單日 (%)",
        "daily_base_line": "每日基準 {pace}%",
        "under_short": "低於基準",
        "over_short": "高於基準",
        "gap_fmt": "{g}天",
        "log_title": "每日紀錄",
        "log_col_day": "天數 / 紀錄時間",
        "log_col_cum": "累計",
        "log_col_inc": "單日增量",
        "future_note": "待紀錄",
        "footer": "產生時間 {now}（{tz_label}）  ·  基準 每天{pace}%  ·  上限 100%",
    },
    "zh-CN": {
        "font": "Noto Sans CJK SC",
        "default_title": "用量仪表板",
        "title_custom": "{title} 本周用量监控",
        "weekdays": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        "tab_cum": "累计趋势",
        "tab_daily": "每日用量",
        "tab_week": "本周总览",
        "hero_title": "本周累计用量",
        "day_of_7": "第 {d} / 7 天",
        "gauge_base": "基准线 {base:.0f}%",
        "tag_proj_over": "▲超标",
        "tag_proj_under": "▼未超标",
        "tag_avg_over": "▲快于基准",
        "tag_avg_under": "▼慢于基准",
        "tag_exhaust": "▲即将用尽",
        "vs_under": "落后基准 {n:.0f}%",
        "vs_over": "超前基准 {n:.0f}%",
        "vs_even": "与基准相同",
        "vs_under_note": "用量充裕",
        "vs_over_note": "消耗较快",
        "vs_even_note": "刚好符合基准",
        "exhaust_main": "预计额度提前耗尽，剩约 {left:.1f} 天",
        "exhausted_now": "本周额度已用完",
        "proj_exhaust_val": "额度剩约 {left:.1f} 天",
        "proj_exhaust_val_short": "剩约 {left:.1f} 天",
        "proj_exhaust_sub": "额度提前耗尽 · 原始预计 {proj:.0f}%",
        "proj_exhaust_chart": "约 {left:.1f} 天后耗尽",
        "exhaust_main_h": "预计额度提前耗尽，剩约 {hours} 小时",
        "proj_exhaust_val_h": "额度剩约 {hours} 小时",
        "proj_exhaust_val_short_h": "剩约 {hours} 小时",
        "proj_exhaust_chart_h": "约 {hours} 小时后耗尽",
        "stat_proj_exhaust": "预计耗尽时间",
        "ring_left": "剩余 {rem:.0f}%",
        "on_pace": "符合基准",
        "ahead_pace": "超前基准",
        "burn_rate": "日均消耗 {rate:.1f}%/天",
        "hits_100": "预计第 {day:.1f} 天达 100%",
        "within_cap": "预计7天 {proj:.0f}% (未超限)",
        "stat_vs_base": "对比基准",
        "stat_vs_base_sub": "第{data_d}天基准: {base}%",
        "stat_rem": "剩余额度",
        "stat_rem_sub": "每周上限 100%",
        "over_cap_note": "超限 +{over:.0f}%",
        "stat_avg": "日均用量",
        "stat_avg_sub": "目标 每天{pace}%",
        "stat_proj": "预计周末总量",
        "stat_proj_sub": "7天基准: {base7}%",
        "stat_reset": "重置倒计时",
        "stat_reset_sub": "{reset_date}",
        "countdown_short": "{days}天 {hours}小时",
        "countdown_hm": "{hours}小时 {mins:02d}分",
        "countdown_m": "{mins}分",
        "reset_short": "已重置",
        "stat_budget": "剩余日均可用",
        "stat_budget_sub": "距重置 {d_rem} 天（含今天）",
        "cum_title": "累计用量 vs. 基准线",
        "ylabel_cum": "累计用量 (%)",
        "day_fmt": "第{d}天",
        "limit_100": "上限 100%",
        "baseline_line": "基准线 每天{pace}%",
        "under_base": "低于基准",
        "over_base": "高于基准",
        "proj_label": "预计 {val:.0f}%",
        "no_data": "无数据",
        "bar_title": "每日用量",
        "ylabel_bar": "单日 (%)",
        "daily_base_line": "每日基准 {pace}%",
        "under_short": "低于基准",
        "over_short": "高于基准",
        "gap_fmt": "{g}天",
        "log_title": "每日记录",
        "log_col_day": "天数 / 记录时间",
        "log_col_cum": "累计",
        "log_col_inc": "单日增量",
        "future_note": "待记录",
        "footer": "生成于 {now}（{tz_label}）  ·  基准 每天{pace}%  ·  上限 100%",
    },
    "ja": {
        "font": "Noto Sans CJK JP",
        "default_title": "使用量ダッシュボード",
        "title_custom": "{title} 週間使用量モニター",
        "weekdays": ["月", "火", "水", "木", "金", "土", "日"],
        "tab_cum": "累計推移",
        "tab_daily": "日別使用量",
        "tab_week": "週間概要",
        "hero_title": "週間累計使用量",
        "day_of_7": "{d} / 7 日目",
        "gauge_base": "基準線 {base:.0f}%",
        "tag_proj_over": "▲超過",
        "tag_proj_under": "▼範囲内",
        "tag_avg_over": "▲基準より速い",
        "tag_avg_under": "▼基準より遅い",
        "tag_exhaust": "▲まもなく上限",
        "vs_under": "基準より {n:.0f}% 少ない",
        "vs_over": "基準より {n:.0f}% 多い",
        "vs_even": "基準どおり",
        "vs_under_note": "余裕あり",
        "vs_over_note": "消費が速め",
        "vs_even_note": "基準ペース",
        "exhaust_main": "枠が早めに尽きる見込み・残り約{left:.1f}日",
        "exhausted_now": "今週の枠を使い切りました",
        "proj_exhaust_val": "枠の残り約{left:.1f}日",
        "proj_exhaust_val_short": "残り約{left:.1f}日",
        "proj_exhaust_sub": "枠が早期に上限 · 予測値 {proj:.0f}%",
        "proj_exhaust_chart": "約{left:.1f}日で上限",
        "exhaust_main_h": "枠が早めに尽きる見込み・残り約{hours}時間",
        "proj_exhaust_val_h": "枠の残り約{hours}時間",
        "proj_exhaust_val_short_h": "残り約{hours}時間",
        "proj_exhaust_chart_h": "約{hours}時間で上限",
        "stat_proj_exhaust": "上限到達の予測",
        "ring_left": "残り {rem:.0f}%",
        "on_pace": "基準ペース内",
        "ahead_pace": "基準ペース超過",
        "burn_rate": "消費速度 {rate:.1f}%/日",
        "hits_100": "約{day:.1f}日目に100%到達",
        "within_cap": "7日予測 {proj:.0f}% (上限内)",
        "stat_vs_base": "基準比",
        "stat_vs_base_sub": "{data_d}日目基準: {base}%",
        "stat_rem": "残り枠",
        "stat_rem_sub": "週間上限 100%",
        "over_cap_note": "上限超過 +{over:.0f}%",
        "stat_avg": "日平均使用量",
        "stat_avg_sub": "目標 毎日{pace}%",
        "stat_proj": "週末予測 (7日)",
        "stat_proj_sub": "7日基準: {base7}%",
        "stat_reset": "リセットまで",
        "stat_reset_sub": "{reset_date}",
        "countdown_short": "{days}日 {hours}時間",
        "countdown_hm": "{hours}時間{mins:02d}分",
        "countdown_m": "{mins}分",
        "reset_short": "リセット済",
        "stat_budget": "残り1日あたり枠",
        "stat_budget_sub": "リセットまで{d_rem}日（今日を含む）",
        "cum_title": "週間累計使用量 vs. 基準線",
        "ylabel_cum": "累計 (%)",
        "day_fmt": "{d}日目",
        "limit_100": "上限 100%",
        "baseline_line": "基準線 毎日{pace}%",
        "under_base": "基準以下",
        "over_base": "基準超過",
        "proj_label": "予測 {val:.0f}%",
        "no_data": "データなし",
        "bar_title": "日別使用量",
        "ylabel_bar": "日別 (%)",
        "daily_base_line": "日別基準 {pace}%",
        "under_short": "基準以下",
        "over_short": "基準超過",
        "gap_fmt": "{g}日",
        "log_title": "日別ログ",
        "log_col_day": "日 / メモ",
        "log_col_cum": "累計",
        "log_col_inc": "増加量",
        "future_note": "予定",
        "footer": "生成 {now}（{tz_label}）  ·  基準 毎日{pace}%  ·  上限 100%",
    },
    "ko": {
        "font": "Noto Sans CJK KR",
        "default_title": "사용량 대시보드",
        "title_custom": "{title} 주간 사용량 모니터",
        "weekdays": ["월", "화", "수", "목", "금", "토", "일"],
        "tab_cum": "누적 추이",
        "tab_daily": "일별 사용량",
        "tab_week": "주간 요약",
        "hero_title": "주간 누적 사용량",
        "day_of_7": "{d} / 7 일차",
        "gauge_base": "기준선 {base:.0f}%",
        "tag_proj_over": "▲초과",
        "tag_proj_under": "▼기준 이내",
        "tag_avg_over": "▲기준보다 빠름",
        "tag_avg_under": "▼기준보다 느림",
        "tag_exhaust": "▲곧 소진",
        "vs_under": "기준보다 {n:.0f}% 적음",
        "vs_over": "기준보다 {n:.0f}% 많음",
        "vs_even": "기준과 같음",
        "vs_under_note": "여유 있음",
        "vs_over_note": "소진 빠름",
        "vs_even_note": "기준 속도",
        "exhaust_main": "한도 조기 소진 예상, 약 {left:.1f}일 남음",
        "exhausted_now": "이번 주 한도 소진",
        "proj_exhaust_val": "한도 약 {left:.1f}일 남음",
        "proj_exhaust_val_short": "약 {left:.1f}일 남음",
        "proj_exhaust_sub": "한도 조기 소진 · 예측 {proj:.0f}%",
        "proj_exhaust_chart": "약 {left:.1f}일 후 소진",
        "exhaust_main_h": "한도 조기 소진 예상, 약 {hours}시간 남음",
        "proj_exhaust_val_h": "한도 약 {hours}시간 남음",
        "proj_exhaust_val_short_h": "약 {hours}시간 남음",
        "proj_exhaust_chart_h": "약 {hours}시간 후 소진",
        "stat_proj_exhaust": "소진 예상 시점",
        "ring_left": "남음 {rem:.0f}%",
        "on_pace": "기준 속도 유지",
        "ahead_pace": "기준 속도 초과",
        "burn_rate": "소진 속도 {rate:.1f}%/일",
        "hits_100": "약 {day:.1f}일차 100% 도달",
        "within_cap": "7일 예상 {proj:.0f}% (한도 내)",
        "stat_vs_base": "기준 대비",
        "stat_vs_base_sub": "{data_d}일차 기준: {base}%",
        "stat_rem": "남은 한도",
        "stat_rem_sub": "주간 상한 100%",
        "over_cap_note": "상한 초과 +{over:.0f}%",
        "stat_avg": "일평균 사용량",
        "stat_avg_sub": "목표 매일 {pace}%",
        "stat_proj": "주말 예상 (7일)",
        "stat_proj_sub": "7일 기준: {base7}%",
        "stat_reset": "초기화까지",
        "stat_reset_sub": "{reset_date}",
        "countdown_short": "{days}일 {hours}시간",
        "countdown_hm": "{hours}시간 {mins:02d}분",
        "countdown_m": "{mins}분",
        "reset_short": "초기화됨",
        "stat_budget": "남은 일일 예산",
        "stat_budget_sub": "초기화까지 {d_rem}일 (오늘 포함)",
        "cum_title": "주간 누적 사용량 vs. 기준선",
        "ylabel_cum": "누적 (%)",
        "day_fmt": "{d}일차",
        "limit_100": "상한 100%",
        "baseline_line": "기준선 매일 {pace}%",
        "under_base": "기준 이하",
        "over_base": "기준 초과",
        "proj_label": "예상 {val:.0f}%",
        "no_data": "데이터 없음",
        "bar_title": "일별 사용량",
        "ylabel_bar": "일별 (%)",
        "daily_base_line": "일별 기준 {pace}%",
        "under_short": "기준 이하",
        "over_short": "기준 초과",
        "gap_fmt": "{g}일",
        "log_title": "일별 로그",
        "log_col_day": "일차 / 메모",
        "log_col_cum": "누적",
        "log_col_inc": "증가량",
        "future_note": "예정",
        "footer": "생성 {now} ({tz_label})  ·  기준 매일 {pace}%  ·  상한 100%",
    },
    # Language-neutral / privacy symbol-and-number-only mode (zero words, no service/tz names)
    "numeric": {
        "font": "DejaVu Sans",
        "hud_top": "",
        "default_title": "",
        "title_custom": "",
        "weekdays": ["", "", "", "", "", "", ""],
        "tab_cum": "∑",
        "tab_daily": "Δ",
        "tab_week": "7/7",
        "hero_title": "∑ %",
        "day_of_7": "{d}/7",
        "gauge_base": "{base:.0f}%",
        "tag_proj_over": "▲",
        "tag_proj_under": "▼",
        "tag_avg_over": "▲",
        "tag_avg_under": "▼",
        "tag_exhaust": "▲",
        "vs_under": "{n:.0f}%",
        "vs_over": "{n:.0f}%",
        "vs_even": "0%",
        "vs_under_note": "",
        "vs_over_note": "",
        "vs_even_note": "",
        "exhaust_main": "→ 100% · ≈{left:.1f}d",
        "exhausted_now": "≥ 100%",
        "proj_exhaust_val": "≈{left:.1f}d",
        "proj_exhaust_val_short": "≈{left:.1f}d",
        "proj_exhaust_sub": "{proj:.0f}%",
        "proj_exhaust_chart": "100% ≈{left:.1f}d",
        "exhaust_main_h": "→ 100% · ≈{hours}h",
        "proj_exhaust_val_h": "≈{hours}h",
        "proj_exhaust_val_short_h": "≈{hours}h",
        "proj_exhaust_chart_h": "100% ≈{hours}h",
        "stat_proj_exhaust": "→ 100%",
        "ring_left": "{rem:.0f}% ↓",
        "on_pace": "▼ ≤",
        "ahead_pace": "▲ >",
        "burn_rate": "Ø {rate:.1f}%",
        "hits_100": "→ 100% @ D{day:.1f}",
        "within_cap": "→ 7/7: {proj:.0f}%",
        "stat_vs_base": "Δ",
        "stat_vs_base_sub": "D{data_d}: {base}%",
        "stat_rem": "100% −",
        "stat_rem_sub": "/ 100%",
        "over_cap_note": "+{over:.0f}% > 100%",
        "stat_avg": "Ø",
        "stat_avg_sub": "{pace}%",
        "stat_proj": "→ 7/7",
        "stat_proj_sub": "7×{pace}% = {base7}%",
        "stat_reset": "↻",
        "stat_reset_sub": "@ {reset_date}",
        "countdown_short": "↻ {days}d {hours}h",
        "countdown_hm": "↻ {hours}h {mins:02d}m",
        "countdown_m": "↻ {mins}m",
        "reset_short": "↻ 0m",
        "stat_budget": "÷",
        "stat_budget_sub": "/ {d_rem}",
        "cum_title": "∑ %",
        "ylabel_cum": "%",
        "day_fmt": "D{d}",
        "limit_100": "100%",
        "baseline_line": "{pace}%",
        "under_base": "",
        "over_base": "",
        "proj_label": "→ {val:.0f}%",
        "no_data": "--",
        "bar_title": "Δ %",
        "ylabel_bar": "Δ%",
        "daily_base_line": "",
        "under_short": "",
        "over_short": "",
        "gap_fmt": "×{g}",
        "log_title": "D1 – D7",
        "log_col_day": "D",
        "log_col_cum": "∑%",
        "log_col_inc": "Δ%",
        "future_note": "--",
        "footer": "{now}",
    },
}


class _SafeFormatValue:
    def __format__(self, format_spec: str) -> str:
        if not format_spec:
            return ""
        if format_spec[-1] in "dfgeFGE%":
            try:
                return format(0, format_spec)
            except Exception:
                return "0"
        return ""

    def __str__(self) -> str:
        return ""


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return _SafeFormatValue()


def trim_num(x, nd=1):
    """A derived rate or average with ``nd`` decimals, without a trailing ".0": 74.0 -> "74", 10.8 -> "10.8"."""
    t = f"{float(x):.{nd}f}"
    if "." in t and not t.split(".", 1)[1].strip("0"):
        t = t.split(".", 1)[0]
    return "0" if t == "-0" else t


class _Num:
    """A number in a template: ``{rate:.1f}`` keeps one decimal only when it is not a whole number
    (``{x:.0f}``, ``{n:d}``, ``{v:+.0f}`` and the rest work as usual)."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __format__(self, spec):
        m = re.fullmatch(r"([+ ]?)\.([1-9])f", spec or "")
        if m:
            t = trim_num(self.v, int(m.group(2)))
            return (m.group(1) if not t.startswith("-") else "") + t
        return format(self.v, spec)

    def __str__(self):
        return str(self.v)


def sfmt(template: object, **kwargs) -> str:
    """Safely format a locale template string without ever raising KeyError or ValueError.
    Fixed-decimal fields drop a trailing ".0" (see ``_Num``)."""
    s = str(template)
    kwargs = {k: (_Num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in kwargs.items()}
    try:
        return s.format_map(_SafeFormatDict(kwargs))
    except Exception:
        return s


def format_duration(
    seconds: float,
    L: Optional[Dict[str, object]] = None,
    prefix: str = "",
    reset_text: str = "0m",
) -> str:
    """
    Format a duration in seconds unambiguously:
    - >= 86400s (>= 1 day): '2d 10h' (or localized L['countdown_short'])
    - 3600s .. 86399s (1h .. <1d): '9h 05m' (or localized L['countdown_hm'])
    - 1s .. 3599s (< 1h): '45m' (or localized L['countdown_m'])
    - <= 0s: reset_text (or localized L['reset_short'])
    """
    sec = int(seconds)
    if sec <= 0:
        if L is not None and "reset_short" in L:
            return str(L["reset_short"])
        return f"{prefix}{reset_text}" if prefix else reset_text

    if sec >= 86400:
        days = sec // 86400
        hours = (sec % 86400) // 3600
        if L is not None and "countdown_short" in L:
            return sfmt(L["countdown_short"], days=days, hours=hours, mins=0)
        return f"{prefix}{days}d {hours}h"

    if sec >= 3600:
        hours = sec // 3600
        mins = (sec % 3600) // 60
        if L is not None and "countdown_hm" in L:
            return sfmt(L["countdown_hm"], days=0, hours=hours, mins=mins)
        return f"{prefix}{hours}h {mins:02d}m"

    mins = max(1, sec // 60)
    if L is not None and "countdown_m" in L:
        return sfmt(L["countdown_m"], days=0, hours=0, mins=mins)
    return f"{prefix}{mins}m"


def _normalize_locale_tag(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return None
    cleaned = str(tag).split(".")[0].split("@")[0].strip().replace("-", "_").lower()
    if not cleaned or cleaned in ("c", "posix"):
        return None
    if cleaned in ("zh_tw", "zh_hk", "zh_mo") or "hant" in cleaned:
        return "zh-TW"
    if cleaned in ("zh_cn", "zh_sg") or "hans" in cleaned or cleaned == "zh" or cleaned.startswith("zh_"):
        return "zh-CN"
    if cleaned == "ja" or cleaned.startswith("ja_"):
        return "ja"
    if cleaned == "ko" or cleaned.startswith("ko_"):
        return "ko"
    return "en"


def detect_lang(env: Optional[Dict[str, str]] = None) -> str:
    """Detect system language and normalize to 'zh-TW', 'zh-CN', 'ja', 'ko', or 'en'."""
    environ = env if env is not None else os.environ

    if sys.platform == "win32" and env is None:
        try:
            windll = getattr(ctypes, "windll", None)
            if windll is not None:
                lang_id = windll.kernel32.GetUserDefaultUILanguage()
                win_tag = locale.windows_locale.get(lang_id)
                norm = _normalize_locale_tag(win_tag)
                if norm:
                    return norm
        except Exception:
            pass

    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = environ.get(var)
        norm = _normalize_locale_tag(val)
        if norm:
            return norm

    try:
        loc_tag = locale.getlocale()[0]
        norm = _normalize_locale_tag(loc_tag)
        if norm:
            return norm
    except Exception:
        pass

    return "en"


def load_locale_overlay(lang: str) -> Optional[Dict[str, object]]:
    """Load optional locale overlay JSON from locales/<lang>.json next to chart_hud.py."""
    loc_file = Path(__file__).resolve().parent / "locales" / f"{lang}.json"
    if loc_file.is_file():
        try:
            with loc_file.open(encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            print(f"Warning: failed to load locale file '{loc_file}': {exc}", file=sys.stderr)
    return None


def resolve_tz(tz_cfg):
    """Return (tzinfo, spec) for 'local'/None/'auto' (machine local) or an IANA name."""
    if tz_cfg is None or (isinstance(tz_cfg, str) and tz_cfg.strip().lower() in ("", "local", "auto")):
        return datetime.now().astimezone().tzinfo, "local"
    if isinstance(tz_cfg, str):
        try:
            return ZoneInfo(tz_cfg.strip()), tz_cfg.strip()
        except Exception:
            print(f"Warning: invalid timezone '{tz_cfg}', using machine local timezone.", file=sys.stderr)
            return datetime.now().astimezone().tzinfo, "local"
    return tz_cfg, "local"


def tz_offset_label(dt, spec="local", privacy=False):
    """'UTC+2' (local / privacy) or 'Area/City UTC+9' for an explicit IANA zone; uses dt's real offset."""
    off = dt.utcoffset() or timedelta(0)
    m = int(off.total_seconds() // 60)
    sign = "+" if m >= 0 else "-"
    m = abs(m)
    lab = f"UTC{sign}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")
    if privacy or spec in (None, "", "local") or str(spec).upper() in ("UTC", "ETC/UTC", "GMT"):
        return lab
    return f"{spec} {lab}"


def blend_color(c1, c2, t):
    r1, g1, b1 = to_rgb(c1)
    r2, g2, b2 = to_rgb(c2)
    return to_hex((r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t))


def normalize_cfg(cfg=None, theme_path=None):
    merged = dict(DEFAULT_CFG)
    t_path = theme_path or (cfg.get("theme") if cfg else None)
    if t_path:
        with open(t_path, encoding="utf-8") as tf:
            t_data = json.load(tf)
        for k in merged:
            if k in t_data and t_data[k] is not None:
                merged[k] = t_data[k]
    if cfg:
        for k, v in cfg.items():
            if k in merged and v is not None:
                merged[k] = v

    raw_lang = str(merged.get("lang") or "auto").strip()
    is_priv = bool(merged.get("privacy")) or (raw_lang.lower() == "numeric")
    merged["privacy"] = is_priv

    if is_priv:
        merged["lang"] = "numeric"
    elif raw_lang.lower() == "auto":
        merged["lang"] = detect_lang()
    else:
        ext_overlay = load_locale_overlay(raw_lang)
        if raw_lang != "en" and raw_lang not in TRANSLATIONS and ext_overlay is None:
            print(f"Warning: unknown language '{raw_lang}', falling back to 'en'.", file=sys.stderr)
            merged["lang"] = "en"
        else:
            merged["lang"] = raw_lang

    pace_val = merged["pace"]
    merged["pace"] = int(pace_val) if float(pace_val).is_integer() else float(pace_val)
    merged["display_offset"] = _offset_mod().clamp(merged.get("display_offset"))
    return merged


def _sibling(name):
    try:
        return __import__(name)
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        return __import__(name)


def _explicit_reset(value, tz):
    """cfg['reset_at'] as a datetime shown as given (only moved to `tz` when it carries a zone), else None."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")) if value else None
        except ValueError:
            dt = None
    if dt is None:
        return None
    return dt.astimezone(tz) if (dt.tzinfo is not None and tz is not None) else dt


def _offset_mod():
    try:
        import usage_offset
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import usage_offset
    return usage_offset


def parse_config():
    p = argparse.ArgumentParser(description="Render futuristic multi-panel HUD weekly usage dashboard.")
    p.add_argument("week_csv", nargs="?", default="usage.csv")
    p.add_argument("out_png", nargs="?", default=None)
    p.add_argument("week_start_date", nargs="?", default=None)
    p.add_argument("--pct", type=float, default=None, help="Manual single reading or day override (%%)")
    p.add_argument("--day", type=int, default=None, help="Day number (1..7) for --pct")
    p.add_argument("--theme", default=None, help="Path to JSON theme file")
    p.add_argument("--palette", default=None, help="Colour preset (22, see README): orange-teal (default), orange-blue, ..., teal-mono, ...; or custom (config custom_colors)")
    p.add_argument("--baseline-color", default=None)
    p.add_argument("--under-color", default=None)
    p.add_argument("--over-color", default=None)
    p.add_argument("--bg-color", default=None)
    p.add_argument("--text-color", default=None)
    p.add_argument("--accent-color", default=None)
    p.add_argument("--reset-at", default=None, help="Exact next reset to show (ISO); default: week start + 7 days")
    p.add_argument("--lang", default=None, help="Language: auto (default), en, zh-TW, zh-CN, ja, ko, numeric")
    p.add_argument("--privacy", action="store_true", default=None, help="Discreet privacy mode (word-free numeric render)")
    p.add_argument("--pace", type=float, default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--tz", default=None, help="Display timezone: 'local' (default, machine timezone) or an IANA name")
    p.add_argument("--reset-hm", default=None, help="Reset time HH:MM in the display timezone (default 00:00)")
    args = p.parse_args()

    cli_overrides = {
        k: getattr(args, k)
        for k in (
            "palette", "baseline_color", "under_color", "over_color", "bg_color",
            "text_color", "accent_color", "lang", "privacy", "pace", "title", "tz", "reset_hm", "reset_at",
        )
    }
    cfg = normalize_cfg(cli_overrides, theme_path=args.theme)
    return args, cfg


OVER_LS = (0, (5, 2.5))  # dashed "over" line segments when patterns are on
HATCH = "///"


def _leg_style(pal, over):
    """Legend line style that matches the chart: dashed + ▲ for over, solid + ▼ for under (patterns only)."""
    if not pal.get("patterns"):
        return {}
    return {"ls": OVER_LS if over else "-", "marker": "^" if over else "v", "markersize": 7}


def glow_plot(ax, x, y, color, is_light, lw=2.6, ls="-", zorder=4):
    layers = ((lw * 2.6, 0.045), (lw * 1.6, 0.09)) if is_light else ((lw * 3.8, 0.06), (lw * 2.4, 0.13), (lw * 1.5, 0.24))
    for w, a in layers:
        ax.plot(x, y, color=color, lw=w, ls=ls, alpha=a, solid_capstyle="round", zorder=zorder - 1)
    ax.plot(x, y, color=color, lw=lw, ls=ls, alpha=1.0, solid_capstyle="round", zorder=zorder)


def vs_base_text(diff, L, is_num=False):
    """Stat-card wording for the gap to the baseline: (value, note, state) with state 'over'/'under'/'even'.
    Plain words, no arrow: e.g. 「落後基準 16%」 + 「用量充裕」, or 「超前基準 5%」 + 「消耗較快」."""
    if abs(diff) < 0.5:
        state = "even"
    else:
        state = "over" if diff > 0 else "under"
    if is_num:
        return ("0%" if state == "even" else f"{diff:+.0f}%"), "", state
    val = sfmt(L.get(f"vs_{state}", "{n:.0f}%"), n=abs(diff))
    note = str(L.get(f"vs_{state}_note", "") or "")
    return val, note, state


def exhaust_days(latest_pct, avg_day):
    """Days until 100% at the current daily rate (remaining % / average % per day), or None when not burning."""
    if avg_day <= 0:
        return None
    return max(0.0, 100.0 - latest_pct) / avg_day


def run_out(latest_day, latest_pct):
    """The one projection model shared by the stat cards and the cumulative chart: the average rate so far
    (latest % / days elapsed), the 7-day projection, and, when it passes 100%, the days left until 100% and the
    chart x where the dashed line crosses the cap (readings sit at x = day - 0.5)."""
    d = max(1, int(latest_day))
    avg = float(latest_pct) / d
    proj = avg * 7.0
    left = exhaust_days(latest_pct, avg)
    out = avg > 0 and proj > 100.0
    return {"avg_day": avg, "proj_7d": proj, "exhausts": out, "left": left if out else None,
            "cross_x": (d - 0.5 + left) if out else None}


def left_ctx(left):
    """Format values for the run-out wording: under one day it is said in hours (「約 8 小時」 / "about 8 h")."""
    left = max(0.0, float(left or 0.0))
    return {"left": left, "hours": max(1, int(round(left * 24.0)))}


def run_out_text(L, key, days_left, **ctx):
    """``L[key]`` with the days left, or ``L[key + "_h"]`` (hours) when less than one day is left."""
    lc = left_ctx(days_left)
    k = f"{key}_h" if lc["left"] < 1.0 and L.get(f"{key}_h") else key
    return sfmt(L[k], **dict(ctx, **lc))


def cycle_days(rem_sec, latest_day, period_days=7):
    """One day index for the whole page. While the cycle runs, today is day (period - ceil(time to reset / 24h) + 1)
    and the days left (today included) are ceil(time to reset / 24h); so 「第 3 / 7 天」 and 「剩餘 5 天」 always add
    up to period + 1. Outside the cycle (or without a reset time) the day of the latest reading is used."""
    if rem_sec is not None and 0 < rem_sec <= period_days * 86400:
        left = int(np.ceil(rem_sec / 86400.0))
        return period_days + 1 - left, left
    d = int(min(max(int(latest_day), 1), period_days))
    return d, period_days + 1 - d


def stat_tag(txt):
    """Small bracketed tag beside a stat number: 「［▼未超標］」 (full-width brackets for CJK), "[▼ within]" otherwise."""
    txt = str(txt or "")
    if not txt:
        return ""
    return f"［{txt}］" if any(ord(ch) > 0x2E80 for ch in txt) else f"[{txt}]"


STAT_LONG_MIN_FS = 15.0  # below this the long projection wording falls back to the short one


def _place_tag(fig, ax_hud, val_t, tag, right_px, y_px, gid):
    """Draw (text, colour) tag right of the value text; shrink tag, then value, until both fit before right_px."""
    txt, col = tag
    if not txt:
        return None
    sc = FIG_W / max(1.0, fig.bbox.width)
    try:
        r = fig.canvas.get_renderer()
        ext = lambda t: t.get_window_extent(renderer=r)
    except Exception:
        return None
    t = ax_hud.text(0, y_px, txt, color=col, fontsize=10.5, fontweight="bold", va="center", ha="left", gid=gid)
    for _ in range(40):
        vx1 = ext(val_t).x1 * sc
        t.set_x(vx1 + 8.0)
        if vx1 + 8.0 + ext(t).width * sc <= right_px:
            break
        if t.get_fontsize() > 8.0:
            t.set_fontsize(t.get_fontsize() - 0.5)
        elif val_t.get_fontsize() > 11.0:
            val_t.set_fontsize(val_t.get_fontsize() - 0.5)
        else:
            break
    return t


def _fit_text(t, max_w, fig, min_size=9.0):
    """Shrink a text artist's font until it fits max_w pixels (figure px)."""
    try:
        r = fig.canvas.get_renderer()
        while t.get_fontsize() > min_size and t.get_window_extent(renderer=r).width * (FIG_W / max(1.0, fig.bbox.width)) > max_w:
            t.set_fontsize(t.get_fontsize() - 0.5)
    except Exception:
        pass
    return t


def _gauge_meta(fig, ax_gauge, hero, otag, pal, track_col, hero_col, data, cfg, bar, latest_base, hx, hw):
    """Geometry + colours of the hero gauge in image pixels (SVG orientation, y down), so the served page can lay
    an animated overlay exactly over the static PNG. The PNG itself is never changed by this."""
    sc = FIG_W / max(1.0, fig.bbox.width)
    try:
        ax_gauge.apply_aspect()
        (x0, y0), (x1, _y1) = ax_gauge.transData.transform([(0.0, 0.0), (1.0, 0.0)])
        unit = (x1 - x0) * sc
        gcx, gcy = x0 * sc, FIG_H - y0 * sc
    except Exception:
        unit, gcx, gcy = 90.0, (0.032 + 0.066) * FIG_W, FIG_H - (0.728 + 0.074) * FIG_H
    try:
        bb = hero.get_window_extent(renderer=fig.canvas.get_renderer())
        nx, ny, nw, nh = bb.x0 * sc, FIG_H - bb.y1 * sc, bb.width * sc, bb.height * sc
    except Exception:
        nx, ny, nw, nh = hero.get_position()[0], FIG_H - hero.get_position()[1] - 30.0, 140.0, 60.0
    pb_x0, pb_y, pb_w, pb_h, used_r, proj_r, base_r = bar
    rows = data.get("rows") or []
    raw = float(rows[-1]["pct"]) if rows else 0.0
    pace = float(cfg.get("pace") or 14.0)
    return {
        "v": 1, "w": FIG_W, "h": FIG_H,
        "pct": round(float(data["latest_pct"]), 3), "raw": round(raw, 3),
        "off": int(cfg.get("display_offset") or 0), "base": round(float(latest_base), 3),
        "pace": pace, "day": int(data["latest_day"]),
        "ring": {"cx": round(gcx, 2), "cy": round(gcy, 2), "u": round(unit, 3),
                 "r_in": 0.78, "r_out": 1.0, "glow_in": 0.76, "glow_out": 1.04},
        "num": {"x": round(nx, 1), "y": round(ny, 1), "w": round(nw, 1), "h": round(nh, 1),
                "size": round(28.5 * 150.0 / 72.0, 2), "tag": otag or "",
                "cover_w": round((hx + hw - 0.012) * FIG_W - nx, 1)},
        "bar": {"x": round(pb_x0, 1), "y": round(FIG_H - pb_y - pb_h, 1), "w": round(pb_w, 1), "h": pb_h,
                "proj_r": round(proj_r, 4), "base_r": round(base_r, 4)},
        "col": {"hero": hero_col, "over": pal["purple"], "under": pal["green"], "track": track_col,
                "border": pal["card_border"], "fill": pal["card_fill"], "base": pal["orange"], "dim": pal["text_dim"]},
    }


def draw_card(ax_hud, x0, y0, w, h, pal, accent=None, radius=14.0, brackets=False):
    px, py, pw, ph = x0 * FIG_W, y0 * FIG_H, w * FIG_W, h * FIG_H
    card = FancyBboxPatch(
        (px, py), pw, ph,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=pal["card_fill"], edgecolor=pal["card_border"], lw=1.1, zorder=2,
    )
    ax_hud.add_patch(card)
    if accent:
        ax_hud.plot([px + radius, px + pw - radius], [py + ph, py + ph], color=accent, lw=2.2, alpha=0.88, zorder=3)
    if brackets:
        dx, dy = 18.0, 18.0
        x1, y1 = px + pw, py + ph
        for sx, sy in (
            ([px, px, px + dx], [py + dy, py, py]),
            ([x1 - dx, x1, x1], [py, py, py + dy]),
            ([px, px, px + dx], [y1 - dy, y1, y1]),
            ([x1 - dx, x1, x1], [y1, y1, y1 - dy]),
        ):
            ax_hud.plot(sx, sy, color=accent or pal["accent"], lw=1.8, alpha=0.85, zorder=4)


def draw_header(ax_hud, cfg, L, pal, t_info):
    is_num = bool(cfg.get("privacy")) or (cfg.get("_eff_lang") == "numeric")
    raw_title = cfg.get("title")
    if is_num:
        # Privacy/numeric mode: plain date range "MM-DD → MM-DD" only, no user --title or product words
        main_title = t_info["short_range_str"]
    elif not raw_title or raw_title in ("Usage Dashboard", "Grok Bot Usage & Check-in", L["default_title"]):
        main_title = str(L["default_title"])
    else:
        main_title = sfmt(L["title_custom"], title=raw_title, pace=cfg["pace"])

    hud_top_txt = str(L.get("hud_top") or "")
    if not is_num and hud_top_txt:
        ax_hud.text(0.025 * FIG_W, 0.978 * FIG_H, hud_top_txt, color=pal["accent"], alpha=0.88, fontsize=9.5, va="top", fontweight="bold")
    ax_hud.text(0.025 * FIG_W, 0.948 * FIG_H, main_title, color=pal["text"], fontsize=20, va="center", fontweight="bold")
    ax_hud.text(0.025 * FIG_W, 0.919 * FIG_H, t_info["range_str"], color=pal["text_dim"], fontsize=11.5, va="center", fontweight="bold")

    tx0, ty0, tw, th = 0.685, 0.924, 0.290, 0.044
    draw_card(ax_hud, tx0, ty0, tw, th, pal, radius=10.0)
    tabs = [str(L["tab_cum"]), str(L["tab_daily"]), str(L["tab_week"])]
    seg_w = tw / 3.0
    for idx, t_lbl in enumerate(tabs):
        sx = tx0 + idx * seg_w
        if idx == 2:
            pill = FancyBboxPatch(
                ((sx + 0.003) * FIG_W, (ty0 + 0.004) * FIG_H),
                (seg_w - 0.006) * FIG_W, (th - 0.008) * FIG_H,
                boxstyle="round,pad=0,rounding_size=8",
                facecolor=blend_color(pal["card_fill"], pal["accent"], 0.22),
                edgecolor=pal["accent"], lw=1.2, zorder=3,
            )
            ax_hud.add_patch(pill)
            ax_hud.text((sx + seg_w * 0.5) * FIG_W, (ty0 + th * 0.5) * FIG_H, t_lbl, color=pal["text"], fontsize=11, fontweight="bold", ha="center", va="center", zorder=4)
        else:
            ax_hud.text((sx + seg_w * 0.5) * FIG_W, (ty0 + th * 0.5) * FIG_H, t_lbl, color=pal["text_dim"], fontsize=11, fontweight="bold", ha="center", va="center", zorder=4)

    ax_hud.plot([0.025 * FIG_W, 0.975 * FIG_W], [0.903 * FIG_H, 0.903 * FIG_H], color=pal["card_border"], lw=1.1)
    ax_hud.plot([0.025 * FIG_W, 0.220 * FIG_W], [0.903 * FIG_H, 0.903 * FIG_H], color=pal["accent"], lw=2.4)


def draw_hero_and_stats(fig, ax_hud, data, cfg, L, pal, t_info):
    is_num = bool(cfg.get("privacy")) or (cfg.get("_eff_lang") == "numeric")
    pace = cfg["pace"]
    base7 = 7 * pace
    latest_day = data["latest_day"]
    latest_pct = data["latest_pct"]
    latest_base = pace * latest_day
    latest_diff = latest_pct - latest_base
    over = latest_diff > 0
    hero_col = pal["purple"] if over else pal["green"]

    rem_pct = max(0.0, 100.0 - latest_pct)
    over_cap = latest_pct > 100.0
    over_val = max(0.0, latest_pct - 100.0)
    ro = run_out(latest_day, latest_pct)  # same model as the chart's dashed projection
    avg_day, proj_7d = ro["avg_day"], ro["proj_7d"]
    proj_over = proj_7d > base7
    d_rem = max(1, t_info["days_rem_stat"])
    daily_budget = rem_pct / d_rem
    eta_day = (100.0 / avg_day) if avg_day > 0 else 7.0
    left_days = ro["left"] if ro["exhausts"] else exhaust_days(latest_pct, avg_day)
    t_info.setdefault("meta", {})["card_left_days"] = ro["left"]

    fmt_ctx = {
        "reset_wd": t_info.get("reset_wd", ""),
        "reset_date": t_info.get("reset_date", ""),
        "tz_label": t_info.get("tz_label_reset", t_info.get("tz_label", "")),
        "d": t_info.get("day_now", latest_day),
        "data_d": latest_day,
        "day": eta_day,
        "pace": pace,
        "base": latest_base,
        "base7": base7,
        "rem": rem_pct,
        "over": over_val,
        "rate": avg_day,
        "proj": proj_7d,
        "val": proj_7d,
        "d_rem": d_rem,
        "left": left_days or 0.0,
        "now": t_info["now_str"],
        "reset_hm": t_info["reset_hm"],
    }

    hx, hy, hw, hh = 0.025, 0.696, 0.330, 0.194
    draw_card(ax_hud, hx, hy, hw, hh, pal, accent=hero_col, radius=14.0, brackets=True)

    ax_gauge = fig.add_axes([0.032, 0.728, 0.132, 0.148], facecolor="none", zorder=5)
    ax_gauge.set_xlim(-1.22, 1.22)
    ax_gauge.set_ylim(-1.22, 1.22)
    ax_gauge.set_aspect("equal")
    ax_gauge.axis("off")

    track_col = blend_color(pal["card_fill"], pal["accent"], 0.18 if pal["is_light"] else 0.14)
    ax_gauge.add_patch(Wedge((0, 0), 1.0, 0, 360, width=0.22, facecolor=track_col, edgecolor=pal["card_border"], lw=0.7))
    ax_gauge.add_patch(Wedge((0, 0), 1.06, 0, 360, width=0.02, facecolor=pal["accent"], alpha=0.35))

    fill_ratio = min(max(latest_pct, 0.0), 100.0) / 100.0
    if fill_ratio > 0:
        theta1, theta2 = 90.0 - 360.0 * fill_ratio, 90.0
        ax_gauge.add_patch(Wedge((0, 0), 1.04, theta1, theta2, width=0.28, facecolor=hero_col, alpha=0.18))
        ax_gauge.add_patch(Wedge((0, 0), 1.0, theta1, theta2, width=0.22, facecolor=hero_col, alpha=0.95))

    base_ratio = min(max(latest_base, 0.0), 100.0) / 100.0
    base_ang = np.deg2rad(90.0 - 360.0 * base_ratio)
    ax_gauge.plot([0.78 * np.cos(base_ang), 1.10 * np.cos(base_ang)], [0.78 * np.sin(base_ang), 1.10 * np.sin(base_ang)], color=pal["orange"], lw=3.0, zorder=6)

    ax_gauge.text(0, 0.12, sfmt(L["day_of_7"], **fmt_ctx), color=pal["text"], fontsize=12 if is_num else 11, fontweight="bold", ha="center", va="center")
    ax_gauge.text(0, -0.22, sfmt(L["ring_left"], **fmt_ctx), color=pal["text_dim"], fontsize=9.8 if is_num else 9.2, fontweight="bold", ha="center", va="center")
    # legend for the moving baseline tick: a short line in the tick colour + "Baseline 28%" (today's baseline),
    # centred under the ring, between the ring and the progress bar
    gl_y = (hy + 0.0295) * FIG_H
    gl_cx = (0.032 + 0.066) * FIG_W
    gl_txt = sfmt(L.get("gauge_base", "{base:.0f}%"), **fmt_ctx)
    seg, gap = 22.0, 6.0  # ax_hud data units are pixels
    gl_t = ax_hud.text(gl_cx, gl_y, gl_txt, color=pal["text_dim"], fontsize=8.4, fontweight="bold",
                       ha="left", va="center", zorder=6, gid="gauge_base_legend")
    try:
        bb = gl_t.get_window_extent(renderer=fig.canvas.get_renderer())
        tw = bb.width * (FIG_W / max(1.0, fig.bbox.width))
    except Exception:
        tw = 7.0 * len(gl_txt)
    x0 = gl_cx - (seg + gap + tw) / 2.0
    gl_t.set_x(x0 + seg + gap)
    ax_hud.plot([x0, x0 + seg], [gl_y, gl_y], color=pal["orange"], lw=3.0, solid_capstyle="butt", zorder=6,
                gid="gauge_base_line")

    rx = (hx + 0.146) * FIG_W
    ax_hud.text(rx, (hy + 0.170) * FIG_H, str(L["hero_title"]), color=pal["text_dim"], fontsize=10.5 if is_num else 9.8, fontweight="bold", va="center")
    hero = ax_hud.text(rx, (hy + 0.130) * FIG_H, f"{latest_pct:.0f}%", color=hero_col, fontsize=28.5, fontweight="bold", va="center")
    otag = _offset_mod().tag(cfg.get("display_offset"))
    if otag:  # small neutral marker: the shown % includes the display offset
        ax_hud.annotate(otag, xy=(1, 0.5), xycoords=hero, xytext=(5, 0), textcoords="offset points",
                        color=pal["text_dim"], fontsize=10, fontweight="bold", va="center", ha="left")
    ax_hud.text(rx, (hy + 0.095) * FIG_H, sfmt(L["burn_rate"], **fmt_ctx), color=pal["text_dim"], fontsize=9.8, fontweight="bold", va="center")

    pill_txt = str(L["ahead_pace"] if over else L["on_pace"])
    if not pill_txt.startswith(("▲", "▼")):
        pill_txt = ("▲ " if over else "▼ ") + pill_txt
    ax_hud.text(
        rx, (hy + 0.064) * FIG_H, pill_txt,
        color=hero_col, fontsize=10 if is_num else 9.2, fontweight="bold", ha="left", va="center",
        bbox=dict(
            boxstyle="round,pad=0.28",
            facecolor=blend_color(pal["card_fill"], hero_col, 0.18),
            edgecolor=hero_col, lw=1.2,
        ),
        zorder=5,
    )

    if avg_day > 0 and proj_7d > 100.0:
        # projection beyond 100%: say when the quota runs out instead of a 392%-style number
        key = "exhausted_now" if latest_pct >= 100.0 else "exhaust_main"
        _fit_text(ax_hud.text(rx, (hy + 0.036) * FIG_H, run_out_text(L, key, left_days, **fmt_ctx), color=pal["purple"], fontsize=9.2, fontweight="bold", va="center",
                              gid="hero_exhaust"), (hx + hw - 0.010) * FIG_W - rx, fig, 7.0)
    else:
        ax_hud.text(rx, (hy + 0.036) * FIG_H, sfmt(L["within_cap"], **fmt_ctx), color=pal["green"], fontsize=9.2, fontweight="bold", va="center")

    pb_x0 = (hx + 0.018) * FIG_W
    pb_w = (hw - 0.036) * FIG_W
    pb_y = (hy + 0.015) * FIG_H
    pb_h = 5.5
    used_r = min(1.0, max(0.0, latest_pct / 100.0))
    proj_r = min(1.0, max(used_r, proj_7d / 100.0))
    base_r = min(1.0, max(0.0, latest_base / 100.0))

    ax_hud.add_patch(Rectangle((pb_x0, pb_y), pb_w, pb_h, facecolor=pal["card_border"], edgecolor="none", zorder=3))
    if proj_r > used_r:
        ax_hud.add_patch(Rectangle((pb_x0 + pb_w * used_r, pb_y), pb_w * (proj_r - used_r), pb_h, facecolor=hero_col, alpha=0.30, edgecolor="none", zorder=4))
    if used_r > 0:
        ax_hud.add_patch(Rectangle((pb_x0, pb_y), pb_w * used_r, pb_h, facecolor=hero_col, alpha=0.95, edgecolor="none", zorder=5))
    bx_tick = pb_x0 + pb_w * base_r
    ax_hud.plot([bx_tick, bx_tick], [pb_y - 2.5, pb_y + pb_h + 2.5], color=pal["orange"], lw=2.2, zorder=6)

    t_info.setdefault("meta", {}).update(_gauge_meta(fig, ax_gauge, hero, otag, pal, track_col, hero_col, data, cfg,
                                                     (pb_x0, pb_y, pb_w, pb_h, used_r, proj_r, base_r), latest_base, hx, hw))

    budget_val_str = f"{trim_num(daily_budget)}%" if is_num else f"{trim_num(daily_budget)}%/d"
    reset_sub_str = f"@ {t_info['reset_date']}" if is_num else sfmt(L["stat_reset_sub"], **fmt_ctx)

    vs_val, vs_note, _vs_state = vs_base_text(latest_diff, L, is_num)
    vs_sub = sfmt(L["stat_vs_base_sub"], **fmt_ctx)
    if vs_note:
        cjk = any(ord(ch) > 0x2E80 for ch in vs_note)
        vs_sub = f"（{vs_note}）{vs_sub}" if cjk else f"({vs_note}) {vs_sub}"
    exhaust = ro["exhausts"]
    # the ▲/▼ of these two cards sits in a small tag beside the number, e.g. 「61%  ［▼未超標］」
    if exhaust:
        proj_val = run_out_text(L, "proj_exhaust_val", left_days, **fmt_ctx)
        # 「額度剩約 N 天」 when it fits beside the tag, else the short 「剩約 N 天」 (the small line still says 「額度提前耗盡」)
        proj_alt = run_out_text(L, "proj_exhaust_val_short" if L.get("proj_exhaust_val_short") else "proj_exhaust_val", left_days, **fmt_ctx)
        proj_sub = sfmt(L["proj_exhaust_sub"], **fmt_ctx)
        proj_tag = (stat_tag(L["tag_exhaust"]), pal["purple"])
    else:
        proj_val = f"{proj_7d:.0f}%"
        proj_alt = None
        proj_sub = sfmt(L["stat_proj_sub"], **fmt_ctx)
        proj_tag = (stat_tag(L["tag_proj_over" if proj_over else "tag_proj_under"]), pal["purple"] if proj_over else pal["green"])
    avg_tag = (stat_tag(L["tag_avg_over" if avg_day > pace else "tag_avg_under"]), pal["purple"] if avg_day > pace else pal["green"])
    stats = [
        (
            str(L["stat_vs_base"]),
            vs_val,
            pal["purple"] if latest_diff > 0 else pal["green"],
            pal["purple"] if latest_diff > 0 else pal["green"],
            vs_sub,
            pal["text_dim"],
        ),
        (
            str(L["stat_rem"]),
            "0%" if over_cap else f"{rem_pct:.0f}%",
            pal["purple"] if over_cap else pal["green"],
            pal["purple"] if over_cap else pal["green"],
            sfmt(L["over_cap_note"], **fmt_ctx) if over_cap else sfmt(L["stat_rem_sub"], **fmt_ctx),
            pal["purple"] if over_cap else pal["text_dim"],
        ),
        (
            str(L["stat_avg"]),
            f"{trim_num(avg_day)}%",
            pal["purple"] if avg_day > pace else pal["green"],
            pal["purple"] if avg_day > pace else pal["green"],
            sfmt(L["stat_avg_sub"], **fmt_ctx),
            pal["text_dim"],
            avg_tag,
        ),
        (
            str(L.get("stat_proj_exhaust") or L["stat_proj"]) if exhaust else str(L["stat_proj"]),  # title matches the value
            proj_val,
            pal["purple"] if proj_over else pal["green"],
            pal["purple"] if proj_over else pal["green"],
            proj_sub,
            pal["text_dim"],
            proj_tag,
            proj_alt,
        ),
        (
            str(L["stat_reset"]),
            t_info["countdown_short"],
            pal["text"],
            pal["accent"],
            reset_sub_str,
            pal["text_dim"],
        ),
        (
            str(L["stat_budget"]),
            budget_val_str,
            pal["text"],
            pal["accent"],
            sfmt(L["stat_budget_sub"], **fmt_ctx),
            pal["text_dim"],
        ),
    ]

    sx0, sy_top, sy_bot = 0.370, 0.799, 0.696
    cw, ch, gap_x = 0.193, 0.091, 0.013
    for idx, (st_title, st_val, val_col, acc_col, st_sub, sub_col, *st_tag) in enumerate(stats):
        row, col = divmod(idx, 3)
        cx = sx0 + col * (cw + gap_x)
        cy = sy_top if row == 0 else sy_bot
        draw_card(ax_hud, cx, cy, cw, ch, pal, accent=acc_col, radius=11.0)
        ax_hud.text((cx + 0.014) * FIG_W, (cy + 0.070) * FIG_H, st_title, color=pal["text_dim"], fontsize=11.5 if is_num else 10.5, fontweight="bold", va="center")
        vt = _fit_text(ax_hud.text((cx + 0.014) * FIG_W, (cy + 0.041) * FIG_H, st_val, color=val_col, fontsize=19, fontweight="bold", va="center",
                                   gid=f"stat_val_{idx}"), (cw - 0.024) * FIG_W, fig, 11.0)
        if st_tag:
            tag_right, tag_y = (cx + cw - 0.016) * FIG_W, (cy + 0.041) * FIG_H
            tt = _place_tag(fig, ax_hud, vt, st_tag[0], tag_right, tag_y, f"stat_tag_{idx}")
            alt = st_tag[1] if len(st_tag) > 1 else None
            if alt and alt != st_val and vt.get_fontsize() < STAT_LONG_MIN_FS:
                # the long wording only squeezes in at a tiny size: use the short one instead
                if tt is not None:
                    tt.remove()
                vt.set_text(alt)
                vt.set_fontsize(19)
                _fit_text(vt, (cw - 0.024) * FIG_W, fig, 11.0)
                _place_tag(fig, ax_hud, vt, st_tag[0], tag_right, tag_y, f"stat_tag_{idx}")
        _fit_text(ax_hud.text((cx + 0.014) * FIG_W, (cy + 0.015) * FIG_H, st_sub, color=sub_col, fontsize=9.5, fontweight="bold", va="center",
                              gid=f"stat_sub_{idx}"), (cw - 0.022) * FIG_W, fig, 7.0)


def draw_cumulative_panel(fig, ax_hud, data, cfg, L, pal):
    is_num = bool(cfg.get("privacy")) or (cfg.get("_eff_lang") == "numeric")
    pace = cfg["pace"]
    base7 = 7 * pace
    days, pcts, xs, ys_plot = data["days"], data["pcts"], data["xs"], data["ys_plot"]
    latest_day = data["latest_day"]
    latest_pct = data["latest_pct"]

    cx, cy, cw, ch = 0.025, 0.336, 0.650, 0.344
    draw_card(ax_hud, cx, cy, cw, ch, pal, accent=pal["accent"], radius=14.0)
    ax_hud.text((cx + 0.018) * FIG_W, (cy + ch - 0.019) * FIG_H, str(L["cum_title"]), color=pal["text"], fontsize=13, fontweight="bold", va="center")

    ax = fig.add_axes([0.078, 0.368, 0.580, 0.252], facecolor=pal["card_fill"], zorder=3)
    ax.set_xlim(0, 7)
    ax.set_ylim(0, Y_MAX)

    if is_num:
        legend_handles = [
            Line2D([0], [0], color=pal["orange"], lw=3.0, label=""),
            Line2D([0], [0], color=pal["green"], lw=3.4, label="", **_leg_style(pal, False)),
            Line2D([0], [0], color=pal["purple"], lw=3.4, label="", **_leg_style(pal, True)),
        ]
        ax.legend(
            handles=legend_handles, loc="lower right", bbox_to_anchor=(1.0, 1.015),
            ncol=3, frameon=False, fontsize=9.0, handlelength=2.4, handletextpad=0.0, columnspacing=1.2,
            borderaxespad=0.0,
        )
    else:
        legend_handles = [
            Line2D([0], [0], color=pal["orange"], lw=2.4, label=sfmt(L["baseline_line"], pace=pace, base7=base7)),
            Line2D([0], [0], color=pal["green"], lw=2.8, label=("▼ " + str(L["under_base"])).strip(), **_leg_style(pal, False)),
            Line2D([0], [0], color=pal["purple"], lw=2.8, label=("▲ " + str(L["over_base"])).strip(), **_leg_style(pal, True)),
        ]
        ax.legend(
            handles=legend_handles, loc="lower right", bbox_to_anchor=(1.0, 1.012),
            ncol=3, frameon=False, fontsize=9.0, labelcolor=pal["text"], handlelength=1.4, columnspacing=1.0,
            borderaxespad=0.0,
        )

    for y_grid in range(0, 101, 20):
        ax.axhline(y_grid, color=pal["grid"], alpha=0.28 if pal["is_light"] else 0.18, lw=0.8, ls=":")

    for n in range(1, 8):
        c_bg = pal["cell_odd"] if n % 2 == 1 else pal["cell_even"]
        ax.add_patch(Rectangle((n - 1, 0), 1.0, Y_MAX, facecolor=c_bg, edgecolor="none", zorder=0))

    for div_x in range(1, 7):
        ax.axvline(div_x, color=pal["grid"], alpha=0.45 if pal["is_light"] else 0.32, lw=1.0, ls="-", zorder=2)

    ax.add_patch(Rectangle((0, 0), 7.0, Y_MAX, fill=False, edgecolor=pal["accent"], alpha=0.55, lw=1.4, zorder=5, clip_on=False))

    ax.axhline(100, color=pal["text"], alpha=0.65 if pal["is_light"] else 0.72, lw=1.4, ls="--", zorder=3)
    _ro = run_out(latest_day, latest_pct)
    if _ro["exhausts"] and latest_day < 7 and latest_pct < 100.0 and _ro["cross_x"] < 4.5:
        # the run-out label sits at the left part of the cap line: the 「上限 100%」 label moves to the right end
        ax.text(6.92, 101.8, str(L["limit_100"]), color=pal["text"], alpha=0.88, fontsize=10, fontweight="bold", va="bottom", ha="right", zorder=6)
    else:
        ax.text(0.10, 101.8, str(L["limit_100"]), color=pal["text"], alpha=0.88, fontsize=10, fontweight="bold", va="bottom", zorder=6)

    bx = np.array([0.0, 7.0])
    by = pace * (bx + 0.5)
    glow_plot(ax, bx, by, color=pal["orange"], is_light=pal["is_light"], lw=2.5, zorder=4)
    ax.scatter([n - 0.5 for n in range(1, 8)], [pace * n for n in range(1, 8)], color=pal["orange"], s=22, alpha=0.85, zorder=4)

    # no floating 「基準線 每天N%」 label on the chart itself: it could cover data points; the top legend names the line

    for i in range(1, len(xs)):
        seg_over = pcts[i] > pace * days[i]
        seg_col = pal["purple"] if seg_over else pal["green"]
        seg_ls = OVER_LS if (pal["patterns"] and seg_over) else "-"
        glow_plot(ax, xs[i - 1 : i + 1], ys_plot[i - 1 : i + 1], color=seg_col, is_light=pal["is_light"], lw=3.2, ls=seg_ls, zorder=5)

    day_set = set(days)
    for m_day in range(1, latest_day):
        if m_day not in day_set:
            mx = m_day - 0.5
            avoid_ys = [pace * m_day]
            for i in range(1, len(xs)):
                if xs[i - 1] < mx < xs[i]:
                    t = (mx - xs[i - 1]) / (xs[i] - xs[i - 1])
                    avoid_ys.append(ys_plot[i - 1] + t * (ys_plot[i] - ys_plot[i - 1]))
            my = max([52.0, 26.0, 76.0, 16.0, 88.0], key=lambda cy_i: min(abs(cy_i - ay) for ay in avoid_ys))
            ax.text(
                mx, my, str(L["no_data"]), color=pal["text_dim"], alpha=0.65, fontsize=10, fontweight="bold",
                ha="center", va="center", bbox=dict(boxstyle="round,pad=0.18", facecolor=pal["card_fill"], edgecolor="none", alpha=0.75), zorder=5,
            )

    prev_lbl_x = -99.0
    prev_lbl_y = -99.0
    for x, d, p, yp in zip(xs, days, pcts, ys_plot):
        diff = p - pace * d
        pt_col = pal["purple"] if diff > 0 else pal["green"]
        m_style = ("^" if diff > 0 else "v") if pal["patterns"] else ("^" if p > 100.0 else "o")
        ax.scatter([x], [yp], color=pt_col, s=160, alpha=0.20, marker=m_style, zorder=6)
        ax.scatter([x], [yp], color=pt_col, edgecolor=pal["text"], linewidth=1.1, s=64, marker=m_style, zorder=7)

        if diff > 0:
            if yp > Y_CAP - 3.0:
                offset_pt, ha_align, va_align = (-2, -13), "center", "top"
                est_lbl_y = yp - 9.0
            else:
                offset_pt, ha_align, va_align = (0, 10), "center", "bottom"
                est_lbl_y = yp + 8.0
            if (x - prev_lbl_x) <= 1.05 and abs(est_lbl_y - prev_lbl_y) < 6.5:
                if va_align == "top":
                    offset_pt, ha_align, va_align = (0, 8), "center", "bottom"
                    est_lbl_y = yp + 7.5
                else:
                    offset_pt, ha_align, va_align = (-2, -13), "center", "top"
                    est_lbl_y = yp - 9.0
        else:
            if yp < 13.0:
                offset_pt, ha_align, va_align = (10, -1), "left", "center"
                est_lbl_y = yp
            else:
                offset_pt, ha_align, va_align = (0, -11), "center", "top"
                est_lbl_y = yp - 8.0

        prev_lbl_x, prev_lbl_y = x, est_lbl_y
        ax.annotate(
            f"{p:.0f}% ({'▲' if diff > 0 else '▼'}{diff:+.0f})", xy=(x, yp), textcoords="offset points", xytext=offset_pt,
            ha=ha_align, va=va_align, fontsize=9.8, color=pt_col, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.16", facecolor=pal["card_fill"], edgecolor=pt_col, alpha=0.92, lw=0.9),
            zorder=8,
        )

    ro = run_out(latest_day, latest_pct)
    if days and latest_day < 7 and ro["exhausts"] and latest_pct < 100.0:
        # runs out before the reset: the dashed line keeps the real slope and stops where it meets the 100% cap
        cx_, left = ro["cross_x"], ro["left"]
        proj_col = pal["purple"]
        ax.plot([xs[-1], cx_], [ys_plot[-1], 100.0], color=proj_col, ls="--", lw=2.0, alpha=0.55, zorder=4, gid="proj_line")
        ax.scatter([cx_], [100.0], facecolors=pal["card_fill"], edgecolors=proj_col, s=58, lw=1.6, alpha=0.90, zorder=6, gid="proj_point")
        proj_txt = run_out_text(L, "proj_exhaust_chart", left, val=ro["proj_7d"], proj=ro["proj_7d"])
        right = cx_ > 4.5  # near the right edge the label ends at the point, otherwise it starts there
        ax.annotate(
            proj_txt, xy=(cx_, 100.0), textcoords="offset points", xytext=(-8 if right else 8, 9),
            color=proj_col, fontsize=9.5, fontweight="bold", ha="right" if right else "left", va="bottom", gid="proj_label",
            bbox=dict(boxstyle="round,pad=0.16", facecolor=pal["card_fill"], edgecolor=proj_col, alpha=0.88, lw=0.8), zorder=7,
        )
        data.setdefault("chart_meta", {})["proj_cross_x"] = cx_
    elif days and latest_day < 7:
        avg_day, proj_7d = ro["avg_day"], ro["proj_7d"]
        proj_y = min(max(proj_7d, 0.0), Y_CAP)
        proj_col = pal["purple"] if proj_7d > base7 else pal["green"]
        ax.plot([xs[-1], 6.5], [ys_plot[-1], proj_y], color=proj_col, ls="--", lw=2.0, alpha=0.55, zorder=4, gid="proj_line")
        ax.scatter([6.5], [proj_y], facecolors=pal["card_fill"], edgecolors=proj_col, s=58, lw=1.6, alpha=0.90, zorder=6, gid="proj_point")
        proj_off = (0, 10) if (proj_7d > base7 and proj_y <= Y_CAP - 6.0) else (0, -11)
        proj_va = "bottom" if proj_off[1] > 0 else "top"
        ax.annotate(
            sfmt(L["proj_label"], val=proj_7d, proj=proj_7d, pace=pace, base7=base7),
            xy=(6.5, proj_y), textcoords="offset points", xytext=proj_off,
            color=proj_col, fontsize=9.5, fontweight="bold", ha="center", va=proj_va, gid="proj_label",
            bbox=dict(boxstyle="round,pad=0.16", facecolor=pal["card_fill"], edgecolor=proj_col, alpha=0.88, lw=0.8), zorder=7,
        )

    ax.set_xticks([n - 0.5 for n in range(1, 8)])
    ax.set_xticklabels([sfmt(L["day_fmt"], d=n, day=n) for n in range(1, 8)], fontsize=11, color=pal["text"], fontweight="bold")
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_yticklabels([f"{v}%" for v in (0, 20, 40, 60, 80, 100)], fontsize=10.5, color=pal["text"])
    ax.tick_params(axis="x", length=0, pad=6)
    ax.tick_params(axis="y", colors=pal["text"], length=3)
    ax.set_ylabel(str(L["ylabel_cum"]), fontsize=11.5, color=pal["text"], fontweight="bold", labelpad=6)
    for spine in ax.spines.values():
        spine.set_visible(False)


def draw_daily_bars_panel(fig, ax_hud, data, cfg, L, pal):
    is_num = bool(cfg.get("privacy")) or (cfg.get("_eff_lang") == "numeric")
    pace = cfg["pace"]
    base7 = 7 * pace
    inc_map = data["inc_map"]
    latest_day = data["latest_day"]

    cx, cy, cw, ch = 0.025, 0.048, 0.650, 0.272
    draw_card(ax_hud, cx, cy, cw, ch, pal, accent=pal["accent"], radius=14.0)
    ax_hud.text((cx + 0.018) * FIG_W, (cy + ch - 0.020) * FIG_H, str(L["bar_title"]), color=pal["text"], fontsize=13, fontweight="bold", va="center")

    ax = fig.add_axes([0.078, 0.080, 0.580, 0.190], facecolor=pal["card_fill"], zorder=3)
    ax.set_xlim(0, 7)

    max_inc = max([v["raw_inc"] for v in inc_map.values()] + [pace * 2.0, 28.0])
    bar_ymax = max(32.0, min(115.0, max_inc * 1.28))
    ax.set_ylim(0, bar_ymax)

    if is_num:
        legend_handles = [
            Patch(facecolor=pal["green"], edgecolor=pal["green"], label=""),
            Patch(facecolor=pal["purple"], edgecolor=pal["text"] if pal["patterns"] else pal["purple"], hatch=HATCH if pal["patterns"] else None, label=""),
            Line2D([0], [0], color=pal["orange"], lw=2.4, ls="--", label=""),
        ]
        ax.legend(
            handles=legend_handles, loc="lower right", bbox_to_anchor=(1.0, 1.015),
            ncol=3, frameon=False, fontsize=9.0, handlelength=2.2, handletextpad=0.0, columnspacing=1.2,
            borderaxespad=0.0,
        )
    else:
        legend_handles = [
            Line2D([0], [0], color=pal["green"], lw=6, label=("▼ " + str(L["under_short"])).strip()),
            (Patch(facecolor=pal["purple"], edgecolor=pal["text"], hatch=HATCH, label=("▲ " + str(L["over_short"])).strip()) if pal["patterns"]
             else Line2D([0], [0], color=pal["purple"], lw=6, label=("▲ " + str(L["over_short"])).strip())),
            Line2D([0], [0], color=pal["orange"], lw=2.2, ls="--", label=sfmt(L["daily_base_line"], pace=pace, base7=base7)),
        ]
        ax.legend(
            handles=legend_handles, loc="lower right", bbox_to_anchor=(1.0, 1.015),
            ncol=3, frameon=False, fontsize=9.0, labelcolor=pal["text"], handlelength=1.4, columnspacing=1.0,
            borderaxespad=0.0,
        )

    step = 10 if bar_ymax <= 45 else (20 if bar_ymax <= 85 else 25)
    yticks = list(range(0, int(bar_ymax), step))
    for yg in yticks:
        ax.axhline(yg, color=pal["grid"], alpha=0.25 if pal["is_light"] else 0.16, lw=0.8, ls=":")

    for n in range(1, 8):
        c_bg = pal["cell_odd"] if n % 2 == 1 else pal["cell_even"]
        ax.add_patch(Rectangle((n - 1, 0), 1.0, bar_ymax, facecolor=c_bg, edgecolor="none", zorder=0))

    for div_x in range(1, 7):
        ax.axvline(div_x, color=pal["grid"], alpha=0.45 if pal["is_light"] else 0.32, lw=1.0, ls="-", zorder=2)

    ax.add_patch(Rectangle((0, 0), 7.0, bar_ymax, fill=False, edgecolor=pal["accent"], alpha=0.55, lw=1.4, zorder=5, clip_on=False))

    ax.axhline(pace, color=pal["orange"], alpha=0.90, lw=1.8, ls="--", zorder=4)

    bw = 0.52
    for d in range(1, 8):
        bx = d - 0.5
        if d in inc_map:
            info = inc_map[d]
            raw_inc = info["raw_inc"]
            disp_h = max(0.0, min(raw_inc, bar_ymax * 0.88))
            gap = info["gap"]
            over = raw_inc > pace * gap
            b_col = pal["purple"] if over else pal["green"]
            fill_col = blend_color(pal["card_fill"], b_col, 0.72 if pal["is_light"] else 0.62)
            cap_col = blend_color(b_col, "#FFFFFF", 0.35)

            ax.add_patch(Rectangle((bx - bw * 0.58, 0), bw * 1.16, disp_h, facecolor=b_col, alpha=0.14, edgecolor="none", zorder=2))
            ax.add_patch(Rectangle((bx - bw * 0.5, 0), bw, disp_h, facecolor=fill_col, edgecolor=b_col, lw=1.4, zorder=3))
            if pal["patterns"] and over:
                ax.add_patch(Rectangle((bx - bw * 0.5, 0), bw, disp_h, facecolor="none", edgecolor=b_col, hatch=HATCH, lw=0, zorder=3.5))
            ax.plot([bx - bw * 0.5, bx + bw * 0.5], [disp_h, disp_h], color=cap_col, lw=3.0, solid_capstyle="butt", zorder=4)

            val_txt = f"{raw_inc:+.0f}% ({sfmt(L['gap_fmt'], g=gap)})" if gap > 1 else f"{raw_inc:+.0f}%"
            if pal["patterns"]:
                val_txt = ("▲ " if over else "▼ ") + val_txt
            near_below_base = (pace - max(4.5, bar_ymax * 0.15)) <= disp_h <= (pace + 0.4)
            if near_below_base and disp_h >= bar_ymax * 0.18:
                ax.annotate(
                    val_txt, xy=(bx, disp_h), textcoords="offset points", xytext=(0, -6),
                    color=pal["text"], fontsize=9.6 if gap > 1 else 9.8, fontweight="bold", ha="center", va="top",
                    bbox=dict(boxstyle="round,pad=0.14", facecolor=pal["card_fill"], edgecolor=b_col, alpha=0.90, lw=0.8),
                    zorder=6,
                )
            elif near_below_base:
                ax.annotate(
                    val_txt, xy=(bx, pace), textcoords="offset points", xytext=(0, 5),
                    color=b_col, fontsize=9.6 if gap > 1 else 9.8, fontweight="bold", ha="center", va="bottom",
                    bbox=dict(boxstyle="round,pad=0.14", facecolor=pal["card_fill"], edgecolor=b_col, alpha=0.94, lw=0.8),
                    zorder=6,
                )
            else:
                ax.annotate(
                    val_txt, xy=(bx, disp_h), textcoords="offset points", xytext=(0, 5),
                    color=b_col, fontsize=9.6 if gap > 1 else 9.8, fontweight="bold", ha="center", va="bottom",
                    bbox=dict(boxstyle="round,pad=0.12", facecolor=pal["card_fill"], edgecolor="none", alpha=0.88),
                    zorder=6,
                )
        elif d < latest_day:
            ax.text(bx, bar_ymax * 0.45, str(L["no_data"]), color=pal["text_dim"], alpha=0.55, fontsize=9.5, fontweight="bold", ha="center", va="center", zorder=4)

    ax.set_xticks([n - 0.5 for n in range(1, 8)])
    ax.set_xticklabels([sfmt(L["day_fmt"], d=n, day=n) for n in range(1, 8)], fontsize=11, color=pal["text"], fontweight="bold")
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{v}%" for v in yticks], fontsize=10.5, color=pal["text"])
    ax.tick_params(axis="x", length=0, pad=6)
    ax.tick_params(axis="y", colors=pal["text"], length=3)
    ax.set_ylabel(str(L["ylabel_bar"]), fontsize=11.5, color=pal["text"], fontweight="bold", labelpad=6)
    for spine in ax.spines.values():
        spine.set_visible(False)


def draw_daily_log_panel(ax_hud, data, cfg, L, pal, t_info):
    is_num = bool(cfg.get("privacy")) or (cfg.get("_eff_lang") == "numeric")
    pace = cfg["pace"]
    inc_map = data["inc_map"]
    row_map = data["row_map"]
    latest_day = data["latest_day"]

    lx, ly, lw, lh = 0.690, 0.048, 0.285, 0.632
    draw_card(ax_hud, lx, ly, lw, lh, pal, accent=pal["accent"], radius=14.0)

    ax_hud.text((lx + 0.018) * FIG_W, (ly + lh - 0.025) * FIG_H, str(L["log_title"]), color=pal["text"], fontsize=13, fontweight="bold", va="center")

    hy = (ly + lh - 0.058) * FIG_H
    x_day = (lx + 0.018) * FIG_W
    x_cum = (lx + lw - 0.088) * FIG_W
    x_inc = (lx + lw - 0.018) * FIG_W
    ax_hud.text(x_day, hy, str(L["log_col_day"]), color=pal["text_dim"], fontsize=9.5, fontweight="bold", va="center")
    ax_hud.text(x_cum, hy, str(L["log_col_cum"]), color=pal["text_dim"], fontsize=9.5, fontweight="bold", ha="right", va="center")
    ax_hud.text(x_inc, hy, str(L["log_col_inc"]), color=pal["text_dim"], fontsize=9.5, fontweight="bold", ha="right", va="center")
    ax_hud.plot([(lx + 0.015) * FIG_W, (lx + lw - 0.015) * FIG_W], [hy - 14.0, hy - 14.0], color=pal["card_border"], lw=1.0)

    top_y = ly + lh - 0.080
    row_h = (lh - 0.095) / 7.0
    bar_x0 = (lx + 0.020) * FIG_W
    bar_w_max = (lw - 0.040) * FIG_W

    for d in range(1, 8):
        ry_top = top_y - (d - 1) * row_h
        ry_mid = (ry_top - row_h * 0.5) * FIG_H
        ry_bot = (ry_top - row_h) * FIG_H

        d_date_str = t_info["day_dates"].get(d, f"D{d}")
        if d in row_map:
            r = row_map[d]
            info = inc_map[d]
            p = data.get("pct_map", {}).get(d, float(r["pct"]))  # shown value (display offset applied)
            raw_inc = info["raw_inc"]
            gap = info["gap"]
            inc_over = raw_inc > pace * gap
            cum_over = p > pace * d
            inc_col = pal["purple"] if inc_over else pal["green"]
            cum_col = pal["purple"] if cum_over else pal["green"]

            raw_date = r.get("date", "").strip()
            mm_dd = raw_date[5:] if len(raw_date) >= 10 else d_date_str
            note = r.get("note", "").strip()
            if is_num and note and not re.match(r"^[\d:.\-\s/]+$", note):
                note = ""
            gap_suffix = f" · {sfmt(L['gap_fmt'], g=gap)}" if gap > 1 else ""
            sub_txt = f"{mm_dd}  {note}{gap_suffix}".strip()

            if d == latest_day:
                hl = FancyBboxPatch(
                    ((lx + 0.010) * FIG_W, ry_bot + 3.0), (lw - 0.020) * FIG_W, row_h * FIG_H - 6.0,
                    boxstyle="round,pad=0,rounding_size=7",
                    facecolor=blend_color(pal["card_fill"], cum_col, 0.10),
                    edgecolor=cum_col, alpha=0.85, lw=0.9, zorder=2,
                )
                ax_hud.add_patch(hl)

            ax_hud.text(x_day, ry_mid + 13.5, sfmt(L["day_fmt"], d=d, day=d), color=pal["text"], fontsize=12, fontweight="bold", va="center", zorder=4)
            ax_hud.text(x_day, ry_mid - 9.0, sub_txt, color=pal["text_dim"], fontsize=9.5, fontweight="bold", va="center", zorder=4)

            ax_hud.text(x_cum, ry_mid + 3.0, f"{p:.0f}%", color=pal["text"], fontsize=12.5, fontweight="bold", ha="right", va="center", zorder=4)
            # plain signed value; the colour carries over/under (pattern mode keeps a shape cue for colour-blind use)
            inc_txt = (("▲" if inc_over else "▼") if pal.get("patterns") else "") + f"{raw_inc:+.0f}%"
            ax_hud.text(x_inc, ry_mid + 3.0, inc_txt, color=inc_col, gid=f"log_inc_{d}", fontsize=12.5, fontweight="bold", ha="right", va="center", zorder=4)

            fill_w = bar_w_max * (min(max(p, 0.0), 100.0) / 100.0)
            ax_hud.plot([bar_x0, bar_x0 + bar_w_max], [ry_bot + 9.0, ry_bot + 9.0], color=pal["card_border"], lw=2.2, solid_capstyle="round", zorder=3)
            if fill_w > 0:
                ax_hud.plot([bar_x0, bar_x0 + fill_w], [ry_bot + 9.0, ry_bot + 9.0], color=cum_col, lw=2.4, solid_capstyle="round", zorder=4)
        else:
            is_missing = d < latest_day
            alpha_val = 0.55 if is_missing else 0.35
            note_lbl = str(L["no_data"] if is_missing else L["future_note"])
            ax_hud.text(x_day, ry_mid + 12.0, sfmt(L["day_fmt"], d=d, day=d), color=pal["text_dim"], alpha=alpha_val, fontsize=11.5, fontweight="bold", va="center", zorder=4)
            ax_hud.text(x_day, ry_mid - 9.5, f"{d_date_str}  {note_lbl}", color=pal["text_dim"], alpha=alpha_val, fontsize=9.5, va="center", zorder=4)
            ax_hud.text(x_cum, ry_mid + 1.0, "--", color=pal["text_dim"], alpha=alpha_val, fontsize=12, fontweight="bold", ha="right", va="center", zorder=4)
            ax_hud.text(x_inc, ry_mid + 1.0, "--", color=pal["text_dim"], alpha=alpha_val, fontsize=12, fontweight="bold", ha="right", va="center", zorder=4)

        if d < 7:
            ax_hud.plot([(lx + 0.015) * FIG_W, (lx + lw - 0.015) * FIG_W], [ry_bot, ry_bot], color=pal["card_border"], alpha=0.65, lw=0.8, zorder=3)


def draw_footer(ax_hud, cfg, L, pal, t_info):
    is_num = bool(cfg.get("privacy")) or (cfg.get("_eff_lang") == "numeric")
    if is_num:
        foot_txt = t_info["now_str"]
    else:
        foot_txt = sfmt(L["footer"], now=t_info["now_str"], pace=cfg["pace"], base7=7 * cfg["pace"], tz_label=t_info.get("tz_label", ""))
    ax_hud.text(0.5 * FIG_W, 0.020 * FIG_H, foot_txt, color=pal["text_dim"], alpha=0.75, fontsize=10, fontweight="bold", ha="center", va="center")


_CJK_FONTS_ADDED = False


def _add_cjk_fonts_once():
    """Register the Noto CJK files once per process: addfont() appends a duplicate entry and clears matplotlib's
    font lookup cache on every call, which made each chart slower than the one before in a long-running server."""
    global _CJK_FONTS_ADDED
    if _CJK_FONTS_ADDED:
        return
    _CJK_FONTS_ADDED = True
    known = {getattr(f, "fname", "") for f in font_manager.fontManager.ttflist}
    for f in font_manager.findSystemFonts():
        f_clean = Path(f).name.replace(" ", "")
        if ("NotoSansCJK" in f_clean or "NotoSerifCJK" in f_clean) and f not in known:
            try:
                font_manager.fontManager.addfont(f)
            except Exception:
                pass


def configure_fonts_and_locale(lang: str) -> str:
    _add_cjk_fonts_once()

    installed = {f.name for f in font_manager.fontManager.ttflist}
    candidates = FONT_CANDIDATES.get(lang, FONT_CANDIDATES["en"])
    chosen = next((c for c in candidates if c in installed), None)

    if lang not in ("en", "numeric") and chosen is None:
        all_cjk = [
            c for k in ("zh-TW", "zh-CN", "ja", "ko") for c in FONT_CANDIDATES[k]
        ]
        chosen = next((c for c in all_cjk if c in installed), None)
        if chosen is None:
            print(
                f"Warning: no CJK font installed for language '{lang}', falling back to 'en' to avoid missing glyphs.",
                file=sys.stderr,
            )
            lang = "en"
            candidates = FONT_CANDIDATES["en"]
            chosen = next((c for c in candidates if c in installed), "DejaVu Sans")

    en_fallbacks = [c for c in FONT_CANDIDATES["en"] if c in installed] or ["DejaVu Sans"]
    family_list = ([chosen] if chosen else []) + [c for c in candidates if c in installed and c != chosen] + en_fallbacks
    plt.rcParams["font.family"] = family_list
    plt.rcParams["axes.unicode_minus"] = False
    return lang


def render(rows, out_png, week_start, cfg=None):
    """Render the weekly usage HUD dashboard from `rows` (list of dicts with day,date,pct,note)."""
    cfg = normalize_cfg(cfg)
    eff_lang = configure_fonts_and_locale(cfg["lang"])
    cfg["_eff_lang"] = eff_lang
    ext_overlay = {} if eff_lang == "numeric" else (load_locale_overlay(eff_lang) or {})
    L = {**BASE_STRINGS, **TRANSLATIONS.get(eff_lang, {}), **ext_overlay}

    bg = cfg["bg_color"]
    bg_r, bg_g, bg_b = to_rgb(bg)
    is_light = (0.2126 * bg_r + 0.7152 * bg_g + 0.0722 * bg_b) > 0.5
    text_col = cfg["text_color"] if cfg["text_color"] else ("#1E241B" if is_light else "#E8F4FF")
    try:
        import usage_palette
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import usage_palette
    pset = usage_palette.get(usage_palette.resolve(cfg.get("palette"), cfg.get("custom_colors")), is_light)
    # accent (frame, eyebrow, gauge ring) follows the palette's cool colour unless accent_color is set
    accent_col = cfg.get("accent_color") or cfg.get("under_color") or pset["under"]
    # historical key names kept: "purple" = OVER colour, "green" = UNDER colour, "orange" = BASELINE colour
    green = cfg["under_color"] or pset["under"]
    purple = cfg["over_color"] or pset["over"]
    orange = cfg["baseline_color"] or pset["baseline"]

    card_fill = blend_color(bg, "#1A222D" if not is_light else text_col, 0.45 if not is_light else 0.05)
    pal = {
        "bg": bg,
        "is_light": is_light,
        "text": text_col,
        "text_dim": blend_color(bg, text_col, 0.58 if is_light else 0.62),
        "accent": accent_col,
        "grid": blend_color(card_fill, accent_col, 0.35 if is_light else 0.42),
        "green": green,
        "purple": purple,
        "orange": orange,
        "card_fill": card_fill,
        "card_border": blend_color(card_fill, accent_col, 0.24 if is_light else 0.22),
        "cell_odd": blend_color(card_fill, accent_col, 0.045 if is_light else 0.028),
        "cell_even": blend_color(card_fill, purple, 0.040 if is_light else 0.024),
        "patterns": bool(cfg.get("patterns")),
    }

    sorted_rows = sorted(rows, key=lambda r: int(r["day"]))
    wstart = week_start if week_start else (sorted_rows[0]["date"] if sorted_rows else "2026-01-01")

    days = [int(r["day"]) for r in sorted_rows]
    off = cfg["display_offset"]
    pcts = [_offset_mod().apply(float(r["pct"]), off) for r in sorted_rows]  # shown values; rows stay raw
    xs = [d - 0.5 for d in days]
    ys_plot = [min(max(p, 0.0), Y_CAP) for p in pcts]

    row_map = {int(r["day"]): r for r in sorted_rows}
    inc_map = {}
    for i, (d, p) in enumerate(zip(days, pcts)):
        prev_d = days[i - 1] if i > 0 else 0
        prev_p = pcts[i - 1] if i > 0 else 0.0
        inc_map[d] = {"raw_inc": p - prev_p, "gap": max(1, d - prev_d)}

    latest_day = days[-1] if days else 1
    latest_pct = pcts[-1] if pcts else 0.0
    data = {
        "rows": sorted_rows, "days": days, "pcts": pcts, "xs": xs, "ys_plot": ys_plot,
        "row_map": row_map, "inc_map": inc_map, "latest_day": latest_day, "latest_pct": latest_pct,
        "pct_map": dict(zip(days, pcts)),
    }

    tz, tz_spec = resolve_tz(cfg.get("tz"))

    reset_hm = str(cfg.get("reset_hm") or "00:00")
    try:
        r_hour, r_min = (int(x) for x in reset_hm.split(":", 1))
    except Exception:
        r_hour, r_min = 0, 0
        reset_hm = "00:00"

    now_dt = datetime.now(tz)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")
    day_dates = {}
    is_num = bool(cfg.get("privacy")) or (eff_lang == "numeric")
    tz_label = tz_offset_label(now_dt, tz_spec, is_num)
    reset_wd = ""
    reset_dt = None
    weekdays_list = L.get("weekdays") if isinstance(L.get("weekdays"), list) else BASE_STRINGS["weekdays"]
    try:
        start_dt = datetime.strptime(wstart, "%Y-%m-%d").replace(hour=r_hour, minute=r_min, tzinfo=tz)
        reset_dt = start_dt + timedelta(days=7)
        short_range_str = f"{start_dt.strftime('%m-%d')} → {reset_dt.strftime('%m-%d')}"
        wday = weekdays_list[start_dt.weekday()]
        r_wday = weekdays_list[reset_dt.weekday()]
        reset_wd = r_wday
        tz_label_reset = tz_offset_label(reset_dt, "local", is_num)  # offset only (fits the card)
        if is_num:
            range_str = f"{start_dt.strftime('%Y-%m-%d')} @ {reset_hm}  →  {reset_dt.strftime('%Y-%m-%d')} @ {reset_hm}"
        else:
            range_str = f"{start_dt.strftime('%Y-%m-%d')} {wday} {reset_hm}  ->  {reset_dt.strftime('%Y-%m-%d')} {r_wday} {reset_hm}"
        for d in range(1, 8):
            day_dates[d] = (start_dt + timedelta(days=d - 1)).strftime("%m-%d")
        cyc_rem = None
        if start_dt <= now_dt < reset_dt:
            cyc_rem = int((reset_dt - now_dt).total_seconds())
            countdown_short = format_duration(cyc_rem, L=L)
        else:
            countdown_short = format_duration(0, L=L)
    except Exception:
        short_range_str = f"{wstart[-5:]} → +7"
        wday = weekdays_list[5]
        reset_wd = wday
        tz_label_reset = tz_offset_label(now_dt, "local", is_num)
        if is_num:
            range_str = f"{wstart} @ {reset_hm}  →  +7 @ {reset_hm}"
        else:
            range_str = f"{wstart} {wday} {reset_hm}  ->  +7d {wday} {reset_hm}"
        countdown_short = format_duration(0, L=L)
        cyc_rem = None

    # The next reset to show: the exact value given (e.g. /usage 'Resets:'), never rolled or corrected; else start + 7 days.
    shown_reset = _explicit_reset(cfg.get("reset_at"), tz)
    if shown_reset is not None:
        cmp_now = now_dt if shown_reset.tzinfo is not None else now_dt.replace(tzinfo=None)
        rem_sec = max(0, int((shown_reset - cmp_now).total_seconds()))
        countdown_short = format_duration(rem_sec, L=L)
        cyc_rem = rem_sec if rem_sec > 0 else None
        reset_wd = weekdays_list[shown_reset.weekday()]
    else:
        shown_reset = reset_dt
    day_now, days_rem_stat = cycle_days(cyc_rem, latest_day)
    reset_date = _sibling("usage_dates").reset_date(shown_reset, "numeric" if is_num else eff_lang) if shown_reset else ""

    t_info = {
        "short_range_str": short_range_str,
        "range_str": range_str,
        "countdown_short": countdown_short,
        "days_rem_stat": days_rem_stat,
        "day_now": day_now,
        "day_dates": day_dates,
        "now_str": now_str,
        "reset_hm": reset_hm,
        "reset_wd": reset_wd,
        "reset_date": reset_date,
        "tz_label": tz_label,
        "tz_label_reset": tz_label_reset,
    }

    fig = plt.figure(figsize=(12, 10.0), dpi=150, facecolor=bg)
    ax_hud = fig.add_axes([0, 0, 1, 1], facecolor="none", zorder=1)
    ax_hud.set_xlim(0, FIG_W)
    ax_hud.set_ylim(0, FIG_H)
    ax_hud.axis("off")

    for y_scan in np.linspace(10, FIG_H - 10, 165):
        ax_hud.axhline(y_scan, color=accent_col, alpha=0.016 if is_light else 0.012, lw=0.6, zorder=1)

    draw_header(ax_hud, cfg, L, pal, t_info)
    draw_hero_and_stats(fig, ax_hud, data, cfg, L, pal, t_info)
    draw_cumulative_panel(fig, ax_hud, data, cfg, L, pal)
    draw_daily_bars_panel(fig, ax_hud, data, cfg, L, pal)
    draw_daily_log_panel(ax_hud, data, cfg, L, pal, t_info)
    draw_footer(ax_hud, cfg, L, pal, t_info)

    out_path = Path(out_png).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=150, facecolor=bg)
    plt.close(fig)
    meta = t_info.get("meta", {})
    meta.update(data.get("chart_meta", {}))
    return meta


def main():
    args, cfg = parse_config()
    rows = []
    csv_path = Path(args.week_csv) if args.week_csv else None
    if csv_path and csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as fp:
            rows = list(csv.DictReader(fp))
    elif args.pct is None and csv_path:
        with csv_path.open(encoding="utf-8", newline="") as fp:
            rows = list(csv.DictReader(fp))

    tz, _tz_spec = resolve_tz(cfg.get("tz"))

    now_dt = datetime.now(tz)
    wstart = args.week_start_date if args.week_start_date else (rows[0]["date"] if rows else now_dt.strftime("%Y-%m-%d"))

    if args.pct is not None:
        target_day = args.day if args.day is not None else (max(int(r["day"]) for r in rows) if rows else 1)
        target_day = max(1, min(7, int(target_day)))
        try:
            start_dt = datetime.strptime(wstart, "%Y-%m-%d")
            r_date = (start_dt + timedelta(days=target_day - 1)).strftime("%Y-%m-%d")
        except Exception:
            r_date = now_dt.strftime("%Y-%m-%d")
        pct_val = args.pct
        pct_str = str(int(pct_val)) if float(pct_val).is_integer() else f"{pct_val:g}"
        new_row = {"day": str(target_day), "date": r_date, "pct": pct_str, "note": now_dt.strftime("%H:%M")}
        rows = [r for r in rows if int(r["day"]) != target_day] + [new_row]

    out_png = args.out_png
    if not out_png:
        if cfg.get("privacy") or cfg.get("lang") == "numeric":
            out_png = f"chart-{now_dt.strftime('%Y%m%d')}.png"
        else:
            out_png = "usage-week.png"

    render(rows, out_png, wstart, cfg)


if __name__ == "__main__":
    main()
