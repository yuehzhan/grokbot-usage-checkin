"""★ palettes (cvd_safe), the "Add patterns" page switch and the old palette name kept as an alias.

Everything runs in scratch dirs with made-up samples; nothing touches real data.
"""
import argparse
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server

import chart_hud  # noqa: E402
import usage_history as uh  # noqa: E402
import usage_palette as up  # noqa: E402
from test_clock_palette import write_samples  # noqa: E402

STARRED = {"orange-deepblue", "sunflower-navy", "safety-ultramarine", "tangerine-violet", "amber-cobalt"}
BANNED = ("colorblind", "color-blind", "colour-blind", "colourblind", "色盲", "色弱")
ROWS = [{"day": str(d), "date": f"2026-01-0{d}", "pct": str(p)} for d, p in zip(range(1, 6), (5, 12, 28, 63, 81))]


def contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def visible(page):
    """Text a person can see or hear: no <script>/<style> bodies, but tag attributes (title, aria-label) kept."""
    page = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", page, flags=re.S | re.I)
    return page.lower()


def ctx(data, palette=None, theme=None, config=None, privacy=False, lang="en"):
    a = argparse.Namespace(config=config, data_dir=str(data), tz="UTC", lang=lang, theme=theme, pace=None,
                           palette=palette, privacy=privacy, host=None, port=None)
    return uh.Ctx(a)


class StarFlagTests(unittest.TestCase):
    def test_names_alias_and_flags(self):
        self.assertEqual(len(up.names()), 17)
        self.assertEqual(up.names()[-3:], ["safety-ultramarine", "tangerine-violet", "amber-cobalt"])
        for old in ("colorblind", "Colorblind", "okabe-ito", "colour-blind"):
            self.assertEqual(up.normalize(old), "orange-deepblue", old)
        self.assertEqual({n for n in up.names() if up.cvd_safe(n)}, STARRED)
        self.assertTrue(up.cvd_safe("colorblind"))
        t = up.js_table()
        for n in up.names():
            self.assertEqual(t[n]["cvd_safe"], n in STARRED, n)
            self.assertNotIn("patterns", t[n])
            for lang in ("en", "zh-TW", "zh-CN"):
                self.assertNotIn("★", up.LABELS[n][lang])  # the star is data, not part of the name
                self.assertEqual(up.label(n, lang).endswith(" ★"), n in STARRED, (n, lang))
        self.assertEqual(up.label("orange-deepblue", "zh-TW"), "橘／深藍 ★")
        self.assertEqual(up.label("orange-deepblue", "zh-CN"), "橙／深蓝 ★")
        self.assertEqual(up.label("orange-deepblue", "en"), "Orange / deep blue ★")
        self.assertEqual(up.label("amber-cobalt", "zh-TW"), "琥珀橘／鈷藍 ★")
        self.assertEqual(up.STAR_NOTE["zh-TW"], "★ = 對比較強，各種色覺都容易分辨")
        self.assertEqual(up.HATCH, "///")

    def test_star_palette_colours(self):
        for n in STARRED:
            for light, bg in ((False, "#0B0F14"), (True, "#F4F4F0")):
                p = up.get(n, light)
                self.assertGreaterEqual(contrast(p["under"], bg), 3.0, (n, light))
                self.assertGreaterEqual(contrast(p["over"], bg), 3.0, (n, light))
                self.assertEqual(len({p["over"], p["under"], p["notice"], p["baseline"]}), 4, (n, light))
        self.assertEqual(up.get("orange-deepblue")["under"], "#3171C4")

    def test_key_scheme(self):
        self.assertEqual(up.key("gold-sky", True), "gold-sky-pat")
        self.assertEqual(up.key("gold-sky", False), "gold-sky")
        self.assertEqual(up.split_key("gold-sky-pat"), ("gold-sky", True))
        self.assertEqual(up.split_key("gold-sky"), ("gold-sky", False))
        self.assertTrue(up.is_key("amber-cobalt-pat"))
        self.assertFalse(up.is_key("nope-pat"))

    def test_chart_patterns_only_when_asked(self):
        self.assertFalse(chart_hud.normalize_cfg({"palette": "orange-deepblue"})["patterns"])
        self.assertTrue(chart_hud.normalize_cfg({"palette": "orange-teal", "patterns": True})["patterns"])
        self.assertEqual(chart_hud._leg_style({"patterns": True}, True)["marker"], "^")
        with tempfile.TemporaryDirectory() as d:
            for pal in ("orange-deepblue", "amber-cobalt"):
                out = Path(d) / f"{pal}.png"
                chart_hud.render(ROWS, str(out), "2026-01-01", {"lang": "en", "palette": pal, "pace": 14, "patterns": True})
                self.assertGreater(out.stat().st_size, 10000)


class CtxPatternTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_default_off_and_sources(self):
        c = ctx(self.data, "colorblind")
        self.assertEqual(c.palette, "orange-deepblue")
        self.assertFalse(c.patterns)
        self.assertFalse(c.theme["patterns"])
        self.assertEqual(c.pal_key, "orange-deepblue")
        self.assertTrue(c.theme_for("gold-sky-pat")["patterns"])
        th = self.dir / "t.json"
        th.write_text(json.dumps({"patterns": True}), encoding="utf-8")
        c = ctx(self.data, "orange-teal", theme=str(th))
        self.assertTrue(c.patterns and c.theme["patterns"])
        self.assertEqual(c.pal_key, "orange-teal-pat")
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"palette": "colorblind", "patterns": True, "lang": "en"}), encoding="utf-8")
        c = ctx(self.data, config=str(cfg))
        self.assertEqual((c.palette, c.patterns, c.pal_key), ("orange-deepblue", True, "orange-deepblue-pat"))
        self.assertEqual(uh._cycle_palettes(c), ["orange-deepblue-pat"])

    def test_save_page_setting(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"palette": "gold-sky", "lang": "en"}), encoding="utf-8")
        self.assertEqual(uh.save_page_setting(str(cfg), self.data, "patterns", True), cfg)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")), {"palette": "gold-sky", "lang": "en", "patterns": True})
        p = uh.save_page_setting(None, self.data, "patterns", True)
        self.assertEqual(p.name, "page_settings.json")
        self.assertTrue(ctx(self.data).patterns)
        uh.save_page_setting(None, self.data, "patterns", False)
        self.assertFalse(ctx(self.data).patterns)

    def test_page_text(self):
        write_samples(str(self.data))
        for lang, privacy in (("en", False), ("zh-TW", False), ("zh-CN", False), ("en", True)):
            c = ctx(self.data, "colorblind", lang=lang, privacy=privacy)
            uh.render_history(c)
            page = read_text(os.path.join(c.hist_dir, "index.html"))
            for w in BANNED:
                self.assertNotIn(w, visible(page), (lang, privacy, w))
            self.assertIn('id="patchk"', page)
            if privacy:
                self.assertIn("▨", page)
            else:
                self.assertIn({"en": "Add patterns", "zh-TW": "加上紋路", "zh-CN": "加上纹路"}[lang], page)
                self.assertIn(html_escape(up.STAR_NOTE[lang]), page)
                self.assertIn(html_escape(up.label("orange-deepblue", lang)), page)
        src = Path(uh.__file__).read_text(encoding="utf-8")
        self.assertIn("localStorage.setItem(PKEY,p?'1':'0')", src)
        self.assertIn("n=P.alias[n]||n", src)
        self.assertIn('html[data-patterns="1"] .badge:not(.note)', src)


def html_escape(s):
    import html
    return html.escape(s)


class PatternServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        write_samples(data)
        cls.data = data
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])
        cls.hist = os.path.join(data, "history")

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def _post(self, path, body, token=True):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        code, raw = request(self.url + path, "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_patterns_switch_is_saved(self):
        self.assertEqual(self._post("api/patterns", {"on": "yes"})[0], 400)
        self.assertEqual(self._post("api/patterns", {"on": True}, token=False)[0], 403)
        code, j = self._post("api/patterns", {"on": True})
        self.assertEqual((code, j.get("patterns")), (200, True))
        self.assertTrue(json.loads(read_text(os.path.join(self.data, "page_settings.json")))["patterns"])
        code, j = self._post("api/patterns", {"on": False})
        self.assertEqual((code, j.get("patterns")), (200, False))
        self.assertFalse(json.loads(read_text(os.path.join(self.data, "page_settings.json")))["patterns"])

    def test_hatched_render_is_lazy_and_cached(self):
        self.assertEqual(self._post("api/palette/render", {"name": "amber-cobalt", "patterns": 1})[0], 400)
        code, j = self._post("api/palette/render", {"name": "amber-cobalt", "patterns": True, "clock": "wk"})
        self.assertEqual(code, 200, j)
        self.assertTrue(j["ok"] and not j["cached"])
        self.assertTrue(os.path.exists(os.path.join(self.hist, "pal", "amber-cobalt-pat", "clock-wk-ot.png")))
        self.assertTrue(self._post("api/palette/render", {"name": "amber-cobalt", "patterns": True, "clock": "wk"})[1]["cached"])
        code, raw = request(self.url + "pal/amber-cobalt-pat/clock-wk-ot.png?v=" + j["v"])
        self.assertEqual(code, 200)
        self.assertTrue(raw.startswith(b"\x89PNG"))


class ReadmeTests(unittest.TestCase):
    def test_docs(self):
        root = Path(__file__).resolve().parent.parent
        en = (root / "README.md").read_text(encoding="utf-8")
        zh = (root / "README.zh-TW.md").read_text(encoding="utf-8")
        for doc in (en, zh):
            self.assertIn("https://daltonlens.org/", doc)
            self.assertIn("CIEDE2000", doc)
            self.assertIn('"patterns": true', doc)
            for n in STARRED:
                self.assertIn(f"`{n}`", doc)
        self.assertIn("加上紋路", zh)
        self.assertIn("Add patterns", en)
        self.assertIn("色盲", zh)
        self.assertIn("本週／近一月／近一季／近半年／近一年／全部", zh)
        self.assertIn("This week / Last month / Last quarter", en)
        self.assertIn("10–60 seconds", en)
        self.assertIn("10–60 秒", zh)


class ClockRangeLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(uh.STR["zh-TW"]["clock_ro"], {"wk": "本週", "1m": "近一月", "3m": "近一季", "6m": "近半年", "1y": "近一年", "all": "全部"})
        self.assertEqual(uh.STR["zh-CN"]["clock_ro"], {"wk": "本周", "1m": "近一月", "3m": "近一季", "6m": "近半年", "1y": "近一年", "all": "全部"})
        self.assertEqual(uh.STR["en"]["clock_ro"], {"wk": "This week", "1m": "Last month", "3m": "Last quarter", "6m": "Last 6 months",
                                                     "1y": "Last year", "all": "All"})
        self.assertEqual(uh.STR["zh-TW"]["clock_rt"]["wk"], "本週")
        self.assertEqual(uh.STR["en"]["clock_ro"]["wk"], "This week")


if __name__ == "__main__":
    unittest.main()
