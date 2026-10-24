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
