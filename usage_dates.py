#!/usr/bin/env python3
"""Explicit date + time for the next reset (stdlib only), e.g. 10月31日（週六）01:41 / Sat, Oct 31, 01:41.

Used wherever the next reset is shown, instead of relative wording (next week / weekday only).
"""
from datetime import datetime

WD = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "zh-TW": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
    "zh-CN": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
    "ja": ["月", "火", "水", "木", "金", "土", "日"],
    "ko": ["월", "화", "수", "목", "금", "토", "일"],
}
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def lang_key(lang):
    s = str(lang or "en")
    if s == "numeric":
        return s
    if s.startswith("zh"):
        return "zh-TW" if s in ("zh-TW", "zh-HK", "zh-MO") or "hant" in s.lower() else "zh-CN"
    return s if s in WD else "en"


def reset_date(dt, lang="en", tz=None):
    """`dt` (aware or naive datetime) as an explicit local date + time, e.g. 10月31日（週六）01:41."""
    if not isinstance(dt, datetime):
        return ""
    if tz is not None and dt.tzinfo is not None:
        dt = dt.astimezone(tz)
    lg, hm, i = lang_key(lang), f"{dt:%H:%M}", dt.weekday()
    if lg == "numeric":
        return f"{dt:%m/%d} {hm}"
    if lg in ("zh-TW", "zh-CN"):
        return f"{dt.month}月{dt.day}日（{WD[lg][i]}）{hm}"
    if lg == "ja":
        return f"{dt.month}月{dt.day}日（{WD[lg][i]}）{hm}"
    if lg == "ko":
        return f"{dt.month}월 {dt.day}일 ({WD[lg][i]}) {hm}"
    return f"{WD['en'][i]}, {MON[dt.month - 1]} {dt.day}, {hm}"


# The same formats for page JavaScript: RD(w, k) where w is a Date already shifted so its UTC fields are local time.
JS = ("function RD(w,k){var p=n=>String(n).padStart(2,'0'),m=w.getUTCMonth(),d=w.getUTCDate(),i=(w.getUTCDay()+6)%7,"
      "hm=p(w.getUTCHours())+':'+p(w.getUTCMinutes()),W=" + __import__("json").dumps(WD, ensure_ascii=False) + ","
      "M=" + __import__("json").dumps(MON) + ";"
      "if(k==='numeric')return p(m+1)+'/'+p(d)+' '+hm;"
      "if(k==='zh-TW'||k==='zh-CN'||k==='ja')return (m+1)+'月'+d+'日（'+W[k][i]+'）'+hm;"
      "if(k==='ko')return (m+1)+'월 '+d+'일 ('+W.ko[i]+') '+hm;"
      "return W.en[i]+', '+M[m]+' '+d+', '+hm;}")
