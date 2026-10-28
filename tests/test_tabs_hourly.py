"""Header tabs: 本週總覽 (default) + 每日增量 (per day / per hour from real readings); 累計趨勢 removed."""
import json
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from _helpers import NEEDS_CJK, has_cjk_font, read_text, scratch
from test_clock_palette import make_ctx, write_samples
from test_gauge_anim import spy_render

import chart_hud
import usage_history as uh

LANGS = ["en"] + list(chart_hud.TRANSLATIONS)
NEW_KEYS = ("unit_day", "unit_hour", "inc_title", "hourly_title", "ylabel_hour", "hourly_base_line", "hourly_note",
            "hourly_sparse", "hourly_span", "hourly_gap_leg", "hourly_empty", "hourly_log_title", "hourly_col_time",
            "hourly_col_inc", "hourly_reset")
TZ = ZoneInfo("Asia/Taipei")


def table(lang):
    return chart_hud.BASE_STRINGS if lang == "en" else chart_hud.TRANSLATIONS[lang]


def fields(s):
    return sorted(re.findall(r"\{(\w+)", str(s)))


class StringTests(unittest.TestCase):
    def test_cumulative_tab_removed_everywhere(self):
        for lang in LANGS:
            self.assertNotIn("tab_cum", table(lang), lang)
            for k in ("tab_week", "tab_daily"):
                self.assertTrue(str(table(lang)[k]).strip(), (lang, k))
        with open(chart_hud.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for word in ("tab_cum", "累計趨勢", "累计趋势"):
            self.assertNotIn(word, src)

    def test_new_strings_in_every_language_with_same_fields(self):
        for lang in LANGS:
            for k in NEW_KEYS:
                self.assertIn(k, table(lang), (lang, k))
                self.assertEqual(fields(table(lang)[k]), fields(chart_hud.BASE_STRINGS[k]), (lang, k))
        self.assertEqual(chart_hud.TRANSLATIONS["zh-TW"]["tab_week"], "本週總覽")
        self.assertEqual(chart_hud.TRANSLATIONS["zh-TW"]["tab_daily"], "每日增量")
        self.assertEqual(chart_hud.TRANSLATIONS["zh-TW"]["unit_hour"], "每小時")

    def test_numeric_has_no_words(self):
        num = chart_hud.TRANSLATIONS["numeric"]
        for k in NEW_KEYS + ("tab_week", "tab_daily"):
            text = re.sub(r"\{[^}]*\}", "", str(num[k]))
            self.assertIsNone(re.search(r"[A-Za-z]{2,}", text), (k, num[k]))
            self.assertFalse(any(ord(c) >= 0x2E80 for c in text), (k, num[k]))


def pts(*items):
    return [(datetime(2026, 10, 27, h, m, tzinfo=TZ), v) for h, m, v in items]


class HourlyIncrementTests(unittest.TestCase):
    S, E = datetime(2026, 10, 24, 1, 41, tzinfo=TZ), datetime(2026, 10, 31, 1, 41, tzinfo=TZ)

    def test_hourly_rows_use_real_reading_times(self):
        hb = chart_hud.hourly_increments(pts((10, 0, 20), (11, 0, 22), (12, 0, 22.5)), self.S, self.E, TZ)
        self.assertEqual(hb["readings"], 3)
        self.assertEqual([(r["t0"].hour, r["t1"].hour, r["inc"], r["gap"]) for r in hb["rows"]], [(10, 11, 2.0, False), (11, 12, 0.5, False)])
        self.assertEqual(hb["step_min"], 60.0)
        self.assertFalse(hb["rich"])

    def test_sub_hour_readings_merge_into_their_clock_hour(self):
        hb = chart_hud.hourly_increments(pts((10, 0, 20), (10, 23, 21), (10, 30, 21.5), (11, 0, 23), (11, 30, 24)), self.S, self.E, TZ)
        r0 = hb["rows"][0]
        self.assertEqual((r0["t0"].strftime("%H:%M"), r0["t1"].strftime("%H:%M"), r0["inc"], r0["n"]), ("10:00", "11:00", 3.0, 3))
        self.assertEqual(hb["rows"][1]["inc"], 1.0)

    def test_gap_stays_one_span_and_nothing_is_interpolated(self):
        hb = chart_hud.hourly_increments(pts((8, 0, 10), (9, 0, 11), (14, 0, 19), (15, 0, 20)), self.S, self.E, TZ)
        gaps = [r for r in hb["rows"] if r["gap"]]
        self.assertEqual(len(hb["rows"]), 3)  # 8-9, 9-14 (one span), 14-15: no invented 10/11/12/13 rows
        self.assertEqual((gaps[0]["t0"].hour, gaps[0]["t1"].hour, gaps[0]["inc"], gaps[0]["span_h"]), (9, 14, 8.0, 5.0))

    def test_reset_unavailable_offset_and_period(self):
        samples = pts((8, 0, 80), (9, 0, None), (10, 0, 82), (11, 0, 3)) + [(datetime(2026, 10, 20, 9, 0, tzinfo=TZ), 50.0)]
        hb = chart_hud.hourly_increments(samples, self.S, self.E, TZ)
        self.assertEqual(hb["readings"], 3)  # unavailable reading and the one before the period are skipped
        last = hb["rows"][-1]
        self.assertTrue(last["reset"])
        self.assertEqual(last["inc"], 3.0)  # the drop counts the new value, never a negative bar
        hb2 = chart_hud.hourly_increments(pts((10, 0, 20), (11, 0, 22)), self.S, self.E, TZ, offset=2)
        self.assertEqual((hb2["rows"][0]["inc"], hb2["rows"][0]["pct"]), (2.0, 24.0))

    def test_rich_needs_many_hours(self):
        many = [(self.S + timedelta(hours=h), float(h)) for h in range(1, 20)]
        self.assertTrue(chart_hud.hourly_increments(many, self.S, self.E, TZ)["rich"])
        few = [(self.S + timedelta(days=d), 10.0 * d) for d in range(1, 4)]
        hb = chart_hud.hourly_increments(few, self.S, self.E, TZ)
        self.assertFalse(hb["rich"])
        self.assertTrue(all(r["gap"] for r in hb["rows"]))

    def test_hotspots_match_two_tabs(self):
        H = chart_hud.hotspots()
        self.assertEqual(set(H["tabs"]), {"week", "daily"})
        self.assertEqual(set(H["units"]), {"day", "hour"})
        wk, dl = H["tabs"]["week"], H["tabs"]["daily"]
        self.assertLess(wk[0], dl[0])
        for l, t, w, h in list(H["tabs"].values()) + list(H["units"].values()):
            self.assertTrue(0 <= l and l + w <= 100 and 0 <= t and t + h <= 100)


class RenderTests(unittest.TestCase):
    ROWS = [{"day": "1", "date": "2026-01-01", "pct": "18", "note": "10:00"}, {"day": "2", "date": "2026-01-02", "pct": "41", "note": "10:00"}]

    def _samples(self, n=30):
        t0 = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
        return [(t0 + timedelta(hours=h), round(h * 1.3, 1)) for h in range(n)]

    def _render(self, view, lang="en", samples=None):
        d = scratch()
        try:
            return spy_render(self.ROWS, os.path.join(d, "r.png"), {"lang": lang, "view": view, "tz": "UTC",
                                                                   "samples": self._samples() if samples is None else samples})
        finally:
            shutil.rmtree(d)

    def test_header_has_two_tabs_in_every_view(self):
        for view in ("week", "daily", "hourly"):
            seen, meta = self._render(view)
            texts = [t for t, _ in seen]
            self.assertEqual(meta["view"], view)
            self.assertIn("Week", texts)
            self.assertIn("Daily", texts)
            self.assertNotIn("Cumulative", texts)
            self.assertEqual(("Per day" in texts, "Per hour" in texts), (view != "week", view != "week"))

    def test_views_show_their_content(self):
        texts = [t for t, _ in self._render("week")[0]]
        self.assertIn("Cumulative Usage vs. Baseline", texts)
        texts = [t for t, _ in self._render("daily")[0]]
        self.assertIn("Daily Increments", texts)
        self.assertNotIn("Cumulative Usage vs. Baseline", texts)
        seen, meta = self._render("hourly")
        texts = [t for t, _ in seen]
        self.assertIn("Hourly Increments", texts)
        self.assertIn("Hourly Log", texts)
        self.assertTrue(meta["inc"]["rich"])
        self.assertTrue(any(re.match(r"01-0\d \d\d:00 → \d\d:00$", t) for t in texts))  # real reading times

    def test_light_use_says_so(self):
        seen, meta = self._render("hourly", samples=[])
        self.assertFalse(meta["inc"]["rich"])
        self.assertTrue(any(t.startswith("Not enough hourly readings") for t, _ in seen))

    def test_bad_view_falls_back_to_week(self):
        self.assertEqual(self._render("cumulative")[1]["view"], "week")

    @unittest.skipUnless(has_cjk_font(), NEEDS_CJK)
    def test_zh_tw_labels(self):
        texts = [t for t, _ in self._render("hourly", lang="zh-TW")[0]]
        for s in ("本週總覽", "每日增量", "每日", "每小時", "每小時增量", "每小時紀錄"):
            self.assertIn(s, texts)
        self.assertNotIn("累計趨勢", texts)

    def test_numeric_privacy_draws_no_words(self):
        seen, _ = self._render("hourly", lang="numeric")
        for t, _g in seen:
            self.assertIsNone(re.search(r"[A-Za-z]{3,}", t), t)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.data = scratch()

    def tearDown(self):
        shutil.rmtree(self.data)

    def _page(self, **kw):
        write_samples(self.data)
        # a week that began three days ago (UTC), so the readings in it do not depend on the hour the test runs
        # (with no week file the period would start at today's 00:00 UTC and hold fewer than 12 readings in the morning)
        d0 = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        with open(os.path.join(self.data, f"week-{d0}.csv"), "w", encoding="utf-8") as fh:
            fh.write("day,date,pct,note\n1,%s,9,\n" % d0)
        c = make_ctx(self.data, **kw)
        uh.render_history(c)
        return read_text(os.path.join(self.data, "history", "index.html"))

    def test_views_and_buttons(self):
        page = self._page()
        hist = os.path.join(self.data, "history")
        for f in uh.WEEK_VIEW_FILES:
            self.assertTrue(os.path.isfile(os.path.join(hist, f)), f)
        self.assertIn("<div class='gwrap' data-wv='week'>", page)  # the overview opens first, not hidden
        self.assertIn("<div class='gwrap' data-wv='daily' hidden>", page)
        self.assertIn("<div class='gwrap' data-wv='hourly' hidden>", page)
        self.assertRegex(page, r"src='week-hourly\.png\?v=\d+'")
        self.assertIn("data-go='week' aria-pressed='true' aria-label='Week'", page)
        self.assertIn("data-go='inc' aria-pressed='false' aria-label='Daily'", page)
        self.assertIn("data-go='hourly' aria-pressed='true' aria-label='Per hour'", page)
        self.assertIn("usage.incUnit", page)
        m = re.search(r"data-inc=\"([^\"]*)\"", page)
        self.assertTrue(json.loads(uh.html.unescape(m.group(1)))["rich"])  # hourly samples -> opens per hour
        meta = json.loads(read_text(os.path.join(hist, "week-current.json")))
        self.assertEqual(meta["view"], "week")
        self.assertIn("ring", meta)

    @unittest.skipUnless(has_cjk_font(), NEEDS_CJK)
    def test_zh_tw_page(self):
        page = self._page(lang="zh-TW")
        for s in ("aria-label='本週總覽'", "aria-label='每日增量'", "aria-label='每日'", "aria-label='每小時'"):
            self.assertIn(s, page)
        self.assertNotIn("累計趨勢", page)

    def test_privacy_page_uses_symbols(self):
        page = self._page(privacy=True)
        for s in ("aria-label='7/7'", "aria-label='Δ'", "aria-label='Δ/d'", "aria-label='Δ/h'"):
            self.assertIn(s, page)
        self.assertNotIn("aria-label='Week'", page)


if __name__ == "__main__":
    unittest.main()


STUB = r"""
var store = %(store)s, W = ['week', 'daily', 'hourly'].map(function (v) {
  return {v: v, hidden: v !== 'week', getAttribute: function (k) { return k === 'data-wv' ? v : null; },
          querySelector: function () { return null; }};
});
var handlers = {};
var sec = {getAttribute: function (k) { return k === 'data-inc' ? %(inc)s : null; },
           querySelectorAll: function () { return W; }, querySelector: function () { return null; },
           addEventListener: function (t, f) { handlers[t] = f; }};
global.document = {getElementById: function (id) { return id === 'p-wk' ? sec : null; }};
global.localStorage = {getItem: function (k) { return k in store ? store[k] : null; }, setItem: function (k, v) { store[k] = String(v); }};
function btn(go) { return {getAttribute: function (k) { return k === 'data-go' ? go : 'false'; }, closest: function () { return this; }}; }
function shown() { return W.filter(function (w) { return !w.hidden; }).map(function (w) { return w.v; }); }
var out = {};
%(js)s
out.load = shown();
%(clicks)s.forEach(function (g) { var b = btn(g); handlers.click({target: b}); });
out.after = shown(); out.store = store;
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RememberTabTests(unittest.TestCase):
    """本週總覽 / 每日增量: the last main tab is remembered per browser (usage.wkTab); first visit = 本週總覽."""

    def run_js(self, store=None, rich=True, clicks=()):
        import subprocess
        js = STUB % {"store": json.dumps(store or {}), "inc": json.dumps(json.dumps({"rich": rich})), "js": uh.WKVIEW_JS,
                     "clicks": json.dumps(list(clicks))}
        return json.loads(subprocess.run([shutil.which("node"), "-e", js], capture_output=True, text=True, check=True).stdout)

    def test_first_visit_opens_the_overview(self):
        self.assertEqual(self.run_js()["load"], ["week"])
        self.assertEqual(self.run_js({"usage.incUnit": "hourly"})["load"], ["week"])  # the unit alone never switches tabs
        self.assertEqual(self.run_js({"usage.wkTab": "bogus"})["load"], ["week"])

    def test_last_tab_and_unit_are_restored(self):
        r = self.run_js(clicks=["inc", "hourly"])
        self.assertEqual(r["after"], ["hourly"])
        self.assertEqual((r["store"]["usage.wkTab"], r["store"]["usage.incUnit"]), ("inc", "hourly"))
        again = self.run_js(r["store"])  # next visit: straight to 每日增量 per hour
        self.assertEqual(again["load"], ["hourly"])
        r = self.run_js({"usage.wkTab": "inc", "usage.incUnit": "daily"}, clicks=["week"])
        self.assertEqual(r["load"], ["daily"])
        self.assertEqual(r["store"]["usage.wkTab"], "week")
        self.assertEqual(self.run_js(r["store"])["load"], ["week"])

    def test_inc_without_unit_follows_the_data(self):
        self.assertEqual(self.run_js({"usage.wkTab": "inc"}, rich=True)["load"], ["hourly"])
        self.assertEqual(self.run_js({"usage.wkTab": "inc"}, rich=False)["load"], ["daily"])

    def test_page_uses_local_storage(self):
        self.assertIn("usage.wkTab", uh.WKVIEW_JS)
        self.assertNotIn("sessionStorage", uh.WKVIEW_JS)
