"""Gauge animation setting + overlay data, stat-card wording, exhaust days and the single day index (made-up data)."""
import argparse
import json
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from _helpers import read_text, request, scratch, start_server, stop_server
from test_clock_palette import write_samples

import chart_hud
import usage_history as uh

ROWS = [{"day": "1", "date": "2026-01-01", "pct": "18", "note": ""}, {"day": "2", "date": "2026-01-02", "pct": "41", "note": ""},
        {"day": "3", "date": "2026-01-03", "pct": "62", "note": ""}]


def ctx(data, config=None, anim=None):
    a = argparse.Namespace(config=config, data_dir=str(data), tz="UTC", lang="en", theme=None, pace=None,
                           palette=None, privacy=False, host=None, port=None, anim=anim)
    return uh.Ctx(a)


def write_week(data, vals=(9, 21, 30)):
    c = ctx(data)
    ws = uh._period_start(c, datetime.now(timezone.utc))
    lines = ["day,date,pct,note\n"] + [f"{i + 1},{(ws + timedelta(days=i)).astimezone(c.tz):%Y-%m-%d},{v},\n" for i, v in enumerate(vals)]
    Path(data, f"week-{ws.astimezone(c.tz):%Y-%m-%d}.csv").write_text("".join(lines), encoding="utf-8")


def spy_render(rows, out, cfg):
    import matplotlib.axes
    made, real = [], matplotlib.axes.Axes.text

    def spy(self, x, y, s, *a, **k):
        t = real(self, x, y, s, *a, **k)
        made.append((self, t))
        return t
    with mock.patch.object(matplotlib.axes.Axes, "text", spy):
        meta = chart_hud.render(rows, out, "2026-01-01", cfg)
    # final text of every artist still on its axes (a card may swap to shorter wording after measuring)
    seen = [(t.get_text(), t.get_gid()) for ax, t in made if t in ax.texts]
    return seen, meta


class WordingTests(unittest.TestCase):
    def test_vs_base_text(self):
        zh = dict(chart_hud.BASE_STRINGS, **chart_hud.TRANSLATIONS["zh-TW"])
        self.assertEqual(chart_hud.vs_base_text(-16, zh)[:2], ("落後基準 16%", "用量充裕"))
        self.assertEqual(chart_hud.vs_base_text(5, zh)[:2], ("超前基準 5%", "消耗較快"))
        self.assertEqual(chart_hud.vs_base_text(0.2, zh)[2], "even")
        self.assertEqual(chart_hud.vs_base_text(-16, zh, is_num=True)[:2], ("-16%", ""))
        for lang in ("zh-CN", "ja", "ko", "numeric"):
            for k in ("vs_under", "vs_over", "vs_even", "exhaust_main", "proj_exhaust_val", "proj_exhaust_chart"):
                self.assertIn(k, chart_hud.TRANSLATIONS[lang], (lang, k))

    def test_exhaust_days(self):
        self.assertAlmostEqual(chart_hud.exhaust_days(62, 62 / 3), 38 / (62 / 3))
        self.assertIsNone(chart_hud.exhaust_days(10, 0))
        self.assertEqual(chart_hud.exhaust_days(120, 40), 0.0)

    def test_cycle_days_add_up(self):
        for rem_h in (1, 23.9, 24, 24.1, 4 * 24 + 1, 7 * 24):
            day, left = chart_hud.cycle_days(int(rem_h * 3600), 2)
            self.assertEqual(day + left, 8, rem_h)
            self.assertEqual(left, -(-int(rem_h * 3600) // 86400))
        self.assertEqual(chart_hud.cycle_days(None, 3), (3, 5))
        self.assertEqual(chart_hud.cycle_days(0, 9), (7, 1))

    def test_render_texts(self):
        d = scratch()
        try:
            seen, meta = spy_render(ROWS, os.path.join(d, "a.png"), {"lang": "zh-TW"})
            texts = [t for t, _ in seen]
            hero = [t for t, g in seen if g == "hero_exhaust"]
            left = 38 / (62 / 3)
            self.assertEqual(hero, [f"預估額度提前耗盡，剩約 {left:.1f} 天"])
            pv = [t for t, g in seen if g == "stat_val_3"]
            # 「額度剩約 N 天」 when it fits beside the tag, else 「剩約 N 天」 with 「額度提前耗盡」 on the small line
            self.assertIn(pv, ([f"額度剩約 {left:.1f} 天"], [f"剩約 {left:.1f} 天"]))
            self.assertTrue([t for t, g in seen if g == "stat_sub_3"][0].startswith("額度提前耗盡"))
            self.assertIn("［▲即將用罄］", texts)
            self.assertTrue(any(t.startswith("額度提前耗盡 · 原始預估 ") for t in texts), texts)
            self.assertFalse(any(t.startswith("預估 ") and t.endswith("%") for t in texts))
            self.assertIn("超前基準 20%", texts)
            self.assertTrue(any(t.startswith("（消耗較快）") for t in texts))
            inc = [t for t, g in seen if g and g.startswith("log_inc_")]
            self.assertEqual(inc, ["+18%", "+23%", "+21%"])  # no ▲/▼ beside the increments
            self.assertFalse(any("基準線 每天" in t for t in texts))  # floating chart label removed
            self.assertTrue(any(t.startswith("距重置 ") and t.endswith(" 天（含今天）") for t in texts), texts)
            self.assertFalse(any(t.startswith("剩餘 ") and "含今天" in t for t in texts))
            self.assertEqual(meta["pct"], 62.0)
            self.assertTrue(os.path.exists(os.path.join(d, "a.png")))
            # display offset reaches the daily log's cumulative column
            seen, meta = spy_render(ROWS, os.path.join(d, "b.png"), {"lang": "en", "display_offset": 1})
            self.assertIn("63%", [t for t, _ in seen])
            self.assertEqual((meta["pct"], meta["raw"], meta["off"]), (63.0, 62.0, 1))
            seen, _ = spy_render(ROWS, os.path.join(d, "c.png"), {"lang": "en", "patterns": True})
            self.assertEqual([t for t, g in seen if g and g.startswith("log_inc_")], ["▲+18%", "▲+23%", "▲+21%"])
        finally:
            shutil.rmtree(d)

    def test_days_left_wording_is_distinct(self):
        # "until the allowance runs out" vs "until reset" must not read the same on the cards
        want = {"en": ("Quota", "to reset"), "zh-TW": ("額度", "距重置"), "zh-CN": ("额度", "距重置"),
                "ja": ("枠", "リセットまで"), "ko": ("한도", "초기화까지")}
        for lang, (quota, reset) in want.items():
            L = chart_hud.BASE_STRINGS if lang == "en" else chart_hud.TRANSLATIONS[lang]
            self.assertIn(quota, L["proj_exhaust_val"], lang)
            self.assertNotIn(quota, L["proj_exhaust_val_short"], lang)
            self.assertNotEqual(L["proj_exhaust_val_short"], L["proj_exhaust_val"], lang)
            self.assertIn(quota, L["proj_exhaust_sub"], lang)
            self.assertIn(reset, L["stat_budget_sub"], lang)
            self.assertNotIn(quota, L["stat_budget_sub"], lang)
        self.assertEqual(chart_hud.TRANSLATIONS["zh-TW"]["exhaust_main"], "預估額度提前耗盡，剩約 {left:.1f} 天")

    def test_ledger_strings(self):
        self.assertEqual(uh.LEDGER_STR["zh-TW"]["none"], "尚無歷史明細（首次背景記錄後啟用）")
        self.assertIn("24 小時內用量激增 +{p}%", uh.LEDGER_STR["zh-TW"]["spike"])
        self.assertNotIn("點", uh.LEDGER_STR["zh-TW"]["spike"])


class AnimSettingTests(unittest.TestCase):
    def setUp(self):
        self.d = scratch()
        self.data = os.path.join(self.d, "data")
        os.makedirs(self.data)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_values_and_precedence(self):
        self.assertEqual([uh.page_anim(v) for v in (None, "", "OFF", "loop", "continuous", "x")],
                         ["once", "once", "off", "continuous", "continuous", "once"])
        self.assertEqual(ctx(self.data).anim_setting, "once")
        uh.save_page_setting(None, self.data, "anim", "off")
        c = ctx(self.data)
        self.assertEqual((c.anim_setting, c.anim_locked), ("off", False))
        cfgp = os.path.join(self.d, "usage_config.json")
        Path(cfgp).write_text(json.dumps({"data_dir": self.data, "anim": "continuous"}), encoding="utf-8")
        self.assertEqual(ctx(self.data, config=cfgp).anim_setting, "continuous")
        c = ctx(self.data, config=cfgp, anim="off")
        self.assertEqual((c.anim_setting, c.anim_locked), ("off", True))
        self.assertNotIn("anim", c.key_suffix)

    def test_page_has_overlay_and_menu(self):
        write_samples(self.data)
        write_week(self.data)
        c = ctx(self.data)
        uh.render_history(c)
        meta = json.loads(read_text(os.path.join(self.data, "history", "week-current.json")))
        self.assertEqual(meta["pct"], 30.0)
        for k in ("ring", "num", "bar", "col"):
            self.assertIn(k, meta)
        page = read_text(os.path.join(self.data, "history", "index.html"))
        for s in ("id=\"animsel\"", "data-api=\"/api/anim\"", "data-live=\"1\"", "<option value='once' selected>Once</option>",
                  "id=\"gaugedata\"", "usageGaugePreview", "prefers-reduced-motion", "visibilitychange", "class='gwrap'",
                  "id=\"applying\"", "Applying…", "usageLedgerOffset"):
            self.assertIn(s, page, s)
        c = ctx(self.data, anim="continuous")
        uh.rewrite_page(c)
        page = read_text(os.path.join(self.data, "history", "index.html"))
        self.assertIn("<option value='continuous' selected>Continuous</option>", page)
        self.assertIn("data-live=\"1\" data-locked='1'", page)


class ToolbarTests(unittest.TestCase):
    def setUp(self):
        self.data = scratch()

    def tearDown(self):
        shutil.rmtree(self.data)

    def test_two_rows_and_units(self):
        write_samples(self.data)
        write_week(self.data)
        uh.render_history(ctx(self.data))
        page = read_text(os.path.join(self.data, "history", "index.html"))
        i_set, i_act = page.index('id="tset"'), page.index('id="tact"')
        # row 1 = settings, row 2 = actions
        for k in ('id="last"', 'id="psel"', 'id="patl"', 'id="offl"', 'id="langl"', 'id="tzl"'):
            self.assertTrue(i_set < page.index(k) < i_act, k)
        for k in ('id="animl"', 'id="manualbtn"', 'id="notifybtn"', 'id="refresh"', 'id="updbtn"', 'id="updautol"'):
            self.assertGreater(page.index(k), i_act, k)
        # Roll back sits right after Check for updates, inside the same no-wrap unit, before the auto-check switch
        order = [page.index(k) for k in ('id="refresh"', 'id="ugrp"', 'id="updbtn"', 'id="updback"', 'id="updautol"', 'id="ghlink"')]
        self.assertEqual(order, sorted(order))
        grp = page[page.index('<span class="ugrp" id="ugrp">'):page.index('id="updautol"')]
        self.assertIn('id="updback" hidden', grp)  # still hidden until a previous version exists
        self.assertIn("</button></span>", grp)
        self.assertEqual(page.count('id="updback"'), 1)
        self.assertNotIn('id="updback"', page[page.index('id="urow"'):])
        self.assertIn(".upd .ugrp{display:inline-flex", page)
        # Auto-check switch and GitHub icon: one no-wrap unit, icon right after the switch
        ag = page[page.index('<span class="agrp" id="agrp">'):]
        ag = ag[:ag.index("</span>", ag.index("</a>"))]
        self.assertLess(ag.index('id="updautol"'), ag.index('id="ghlink"'))
        self.assertIn(".upd .agrp{display:inline-flex", page)
        for k in (".upd .trow{display:flex;flex-wrap:wrap", ".upd .trow>*{flex:0 0 auto;white-space:nowrap}",
                  ".upd [hidden]{display:none!important}"):
            self.assertIn(k, page, k)
        # the toolbar shows the time only; the zone stays in the tooltip (and in the Time zone menu beside it)
        self.assertRegex(page, r'<span id="last" title="Last update: \d\d:\d\d \([^"]+\)">Last update: \d\d:\d\d</span>')
        self.assertIn("Auto-check for updates", page)

    def test_github_link(self):
        write_samples(self.data)
        write_week(self.data)
        uh.render_history(ctx(self.data))
        page = read_text(os.path.join(self.data, "history", "index.html"))
        i = page.index('id="ghlink"')
        self.assertGreater(i, page.index('id="updautol"'))  # end of row 2, right after the auto-check switch
        self.assertLess(i, page.index("</div>", i))
        a = page[page.rindex("<a ", 0, i):page.index("</a>", i) + 4]
        for k in ('href="https://github.com/yuehzhan/grokbot-usage-checkin"', 'target="_blank"', 'rel="noopener noreferrer"',
                  'title="View on GitHub"', 'aria-label="View on GitHub"', f'<path d="{uh.GH_MARK_PATH}"/>', 'aria-hidden="true"'):
            self.assertIn(k, a, k)
        self.assertNotRegex(re.sub(r"<[^>]+>", "", a), r"\S")  # icon only, no text beside the mark
        self.assertNotIn("filter", a)
        css = uh.gh_css(True)
        self.assertIn("color:rgba(255,255,255,0.6)", css)
        self.assertIn(".ghlink:hover,.ghlink:focus-visible{color:#FFFFFF}", css)
        self.assertNotRegex(css, r"shadow|gradient|filter|accent")
        self.assertIn("color:#6E7781", uh.gh_css(False))
        self.assertEqual(uh.gh_link_html("numeric"), "")
        for lang, t in (("zh-TW", "前往 GitHub 專案頁"), ("ja", "GitHub リポジトリを開く"), ("en", "View on GitHub")):
            self.assertIn(f'aria-label="{t}"', uh.gh_link_html(lang))
        # the mark is the unmodified Octicons mark-github 16px path (viewBox 0 0 16 16)
        self.assertTrue(uh.GH_MARK_PATH.startswith("M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656"))
        self.assertTrue(uh.GH_MARK_PATH.endswith("c.266-.265.47-.5.657-.656"))

    def test_number_cover_spans_drawn_box(self):
        # the sweep's cover over the static big number must span the number's drawn box in every language
        self.assertIn("var nt=Math.min(N.y,cy-0.42*N.size)-3,nb=Math.max(N.y+N.h,cy+0.42*N.size)+3;", uh.GAUGE_JS)
        self.assertIn("y:nt,width:N.cover_w,height:nb-nt", uh.GAUGE_JS)
        self.assertNotIn("height:0.84*N.size", uh.GAUGE_JS)


class AnimServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(cls.data)
        write_samples(cls.data)
        write_week(cls.data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", cls.data, "--port", "0"],
                                      os.path.join(cls.data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def _post(self, body, token=True, host=None):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        if host:
            h["Host"] = host
        code, raw = request(self.url + "api/anim", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_anim_endpoint(self):
        for bad in ({"anim": "spin"}, {"anim": 1}, {"anim": None}, {}):
            self.assertEqual(self._post(bad)[0], 400, bad)
        self.assertEqual(self._post({"anim": "off"}, token=False)[0], 403)
        self.assertIn(self._post({"anim": "off"}, host="evil.example")[0], (403, 421))
        self.assertEqual(request(self.url + "api/anim")[0], 405)
        self.assertFalse(os.path.exists(os.path.join(self.data, "page_settings.json")))
        code, j = self._post({"anim": "continuous"})
        self.assertEqual((code, j.get("anim"), j.get("locked")), (200, "continuous", False))
        saved = json.loads(read_text(os.path.join(self.data, "page_settings.json")))
        self.assertEqual(saved["anim"], "continuous")
        page = request(self.url)[1].decode("utf-8")
        self.assertIn("<option value='continuous' selected>", page)
        code, raw = request(self.url + "week-current.json")
        self.assertEqual(code, 200)
        self.assertIn("ring", json.loads(raw))
        self.assertEqual(request(self.url + "pal/..%2F/week-current.json")[0], 404)


if __name__ == "__main__":
    unittest.main()


class RunOutBeforeResetTests(unittest.TestCase):
    """Day 1 of 7 at 74% (74%/day): the quota runs out about 0.35 days after the reading, well before the reset."""
    ROWS1 = [{"day": "1", "date": "2026-01-01", "pct": "74", "note": ""}]

    def test_model(self):
        ro = chart_hud.run_out(1, 74.0)
        self.assertTrue(ro["exhausts"])
        self.assertAlmostEqual(ro["proj_7d"], 518.0)
        self.assertAlmostEqual(ro["left"], 26 / 74)
        self.assertAlmostEqual(ro["cross_x"], 0.5 + 26 / 74)  # day ~1.35
        self.assertFalse(chart_hud.run_out(3, 42.0)["exhausts"])
        self.assertIsNone(chart_hud.run_out(3, 42.0)["cross_x"])
        self.assertEqual(chart_hud.left_ctx(26 / 74)["hours"], 8)
        L = dict(chart_hud.BASE_STRINGS, **chart_hud.TRANSLATIONS["zh-TW"])
        self.assertEqual(chart_hud.run_out_text(L, "proj_exhaust_chart", 26 / 74), "約 8 小時後耗盡")
        self.assertEqual(chart_hud.run_out_text(L, "proj_exhaust_chart", 1.5), "約 1.5 天後耗盡")  # a day or more: days
        self.assertEqual(chart_hud.run_out_text(chart_hud.BASE_STRINGS, "proj_exhaust_chart", 26 / 74), "out in about 8 h")
        for lang in ("zh-CN", "ja", "ko", "numeric"):
            for k in ("exhaust_main_h", "proj_exhaust_val_h", "proj_exhaust_val_short_h", "proj_exhaust_chart_h", "stat_proj_exhaust"):
                self.assertIn(k, chart_hud.TRANSLATIONS[lang], (lang, k))

    def _render(self, lang):
        import matplotlib.axes
        d = scratch()
        lines, real = [], matplotlib.axes.Axes.plot

        def spy(self, *a, **k):
            r = real(self, *a, **k)
            if k.get("gid") == "proj_line":
                lines.append(r[0])
            return r
        try:
            with mock.patch.object(matplotlib.axes.Axes, "plot", spy):
                seen, meta = spy_render(self.ROWS1, os.path.join(d, "r.png"), {"lang": lang})
        finally:
            shutil.rmtree(d)
        return seen, meta, lines

    def test_chart_crosses_cap_where_the_card_says(self):
        for lang, title, val, chart in (("zh-TW", "預估耗盡時間", "8 小時", "約 8 小時後耗盡"),
                                        ("en", "Projected run-out", "8 h", "out in about 8 h")):
            seen, meta, lines = self._render(lang)
            # single source of truth: the chart's crossing x is the card's run-out estimate
            self.assertAlmostEqual(meta["card_left_days"], 26 / 74)
            self.assertAlmostEqual(meta["proj_cross_x"], 0.5 + meta["card_left_days"])
            self.assertEqual(len(lines), 1)
            xd, yd = lines[0].get_xdata(), lines[0].get_ydata()
            self.assertAlmostEqual(xd[0], 0.5)
            self.assertAlmostEqual(yd[0], 74.0)
            self.assertAlmostEqual(xd[-1], meta["proj_cross_x"])  # stops at the cap, not at day 7
            self.assertAlmostEqual(yd[-1], 100.0)
            self.assertAlmostEqual((yd[-1] - yd[0]) / (xd[-1] - xd[0]), 74.0)  # the real slope: 74%/day
            texts = [t for t, _ in seen]
            self.assertIn(title, texts, lang)
            self.assertNotIn(chart_hud.TRANSLATIONS.get(lang, chart_hud.BASE_STRINGS).get("stat_proj", chart_hud.BASE_STRINGS["stat_proj"]), texts)
            self.assertIn(val, [t for t, g in seen if g == "stat_val_3"][0], lang)
            self.assertTrue(any(val in t for t, g in seen if g == "hero_exhaust"), lang)
            self.assertNotIn(" 0.4 ", " ".join(texts))  # under a day: hours, not 0.4 days

    def test_label_sits_at_the_crossing(self):
        import matplotlib.axes
        d = scratch()
        made, real = [], matplotlib.axes.Axes.annotate

        def spy(self, *a, **k):
            r = real(self, *a, **k)
            made.append((k.get("gid"), k.get("xy")))
            return r
        try:
            with mock.patch.object(matplotlib.axes.Axes, "annotate", spy):
                chart_hud.render(self.ROWS1, os.path.join(d, "l.png"), "2026-01-01", {"lang": "zh-TW"})
        finally:
            shutil.rmtree(d)
        xy = [xy for g, xy in made if g == "proj_label"]
        self.assertEqual(len(xy), 1)
        self.assertAlmostEqual(xy[0][0], 0.5 + 26 / 74)
        self.assertAlmostEqual(xy[0][1], 100.0)

    def test_not_exhausting_keeps_projected_total(self):
        d = scratch()
        try:
            seen, meta = spy_render([{"day": "3", "date": "2026-01-03", "pct": "30", "note": ""}], os.path.join(d, "n.png"), {"lang": "zh-TW"})
        finally:
            shutil.rmtree(d)
        texts = [t for t, _ in seen]
        self.assertIn("預估週末總量", texts)
        self.assertNotIn("預估耗盡時間", texts)
        self.assertIn("70%", [t for t, g in seen if g == "stat_val_3"])
        self.assertNotIn("proj_cross_x", meta)


class TrimmedDecimalsTests(unittest.TestCase):
    """Derived rates and averages: one decimal only when it is not a whole number (74.0% -> 74%, 10.8% stays)."""

    def test_helpers(self):
        self.assertEqual(chart_hud.trim_num(74.0), "74")
        self.assertEqual(chart_hud.trim_num(10.8), "10.8")
        self.assertEqual(chart_hud.trim_num(10.04), "10")
        self.assertEqual(chart_hud.trim_num(-0.01), "0")
        zh = dict(chart_hud.BASE_STRINGS, **chart_hud.TRANSLATIONS["zh-TW"])
        self.assertEqual(chart_hud.sfmt(zh["burn_rate"], rate=74.0), "消耗速率 74%/天")
        self.assertEqual(chart_hud.sfmt(zh["burn_rate"], rate=14.2), "消耗速率 14.2%/天")
        self.assertEqual(chart_hud.sfmt(chart_hud.BASE_STRINGS["burn_rate"], rate=74.0), "burn rate 74%/day")
        self.assertEqual(chart_hud.sfmt("{a:+.0f} {n:d} {s}", a=3.4, n=4, s="x"), "+3 4 x")  # other specs unchanged
        self.assertEqual(chart_hud.sfmt("{a:+.1f}", a=2.0), "+2")

    def test_cards_drop_trailing_zero(self):
        d = scratch()
        try:
            for lang, rate in (("zh-TW", "消耗速率 74%/天"), ("en", "burn rate 74%/day")):
                seen, _ = spy_render([{"day": "1", "date": "2026-01-01", "pct": "74", "note": ""}], os.path.join(d, f"{lang}.png"), {"lang": lang})
                texts = [t for t, _ in seen]
                self.assertIn("74%", [t for t, g in seen if g == "stat_val_2"], lang)  # 平均每日用量 like 剩餘可用額度: 74%, not 74.0%
                self.assertIn(rate, texts, lang)
                self.assertFalse([t for t in texts if re.search(r"\d\.0(?:%|d\b| 天|/)", t)], lang)
            seen, _ = spy_render(ROWS, os.path.join(d, "x.png"), {"lang": "en"})
            self.assertIn("20.7%", [t for t, g in seen if g == "stat_val_2"])  # 62 / 3: the decimal stays
        finally:
            shutil.rmtree(d)
