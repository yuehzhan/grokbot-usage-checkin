"""Language and time zone menus on the dashboard page, and the arrows on the avg / projection cards.

Everything runs in scratch dirs with made-up samples; nothing touches real data.
"""
import argparse
import hashlib
import json
import os
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server

import chart_hud  # noqa: E402
import usage_history as uh  # noqa: E402
from test_clock_palette import write_samples  # noqa: E402

ROWS = [{"day": "1", "date": "2026-01-01", "pct": "3", "note": ""}, {"day": "2", "date": "2026-01-02", "pct": "5", "note": ""}]


def ctx(data, config=None, lang=None, tz=None):
    a = argparse.Namespace(config=config, data_dir=str(data), tz=tz, lang=lang, theme=None, pace=None,
                           palette=None, privacy=False, host=None, port=None)
    return uh.Ctx(a)


def digest(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).glob("*.csv"))}


class ValueTests(unittest.TestCase):
    def test_lang_values(self):
        for v in uh.PAGE_LANGS:
            self.assertEqual(uh.page_lang(v), v)
        self.assertEqual((uh.page_lang(None), uh.page_lang(""), uh.page_lang("JA")), ("auto", "auto", "ja"))
        self.assertEqual(set(uh.LANG_NAMES), set(uh.PAGE_LANGS) - {"auto"})
        for lang in ("zh-TW", "zh-CN", "en", "ja", "ko", "numeric"):
            self.assertEqual(set(uh.PICK_STR[lang]), {"lang", "tz", "auto", "locked", "anim", "off", "once", "continuous", "applying"}, lang)

    def test_zone_values(self):
        for z in uh.PAGE_ZONES:
            self.assertTrue(uh.valid_zone(z), z)
            self.assertRegex(uh.tz_option_label(z), r"^UTC[+-]\d+(:\d\d)?$")
        self.assertIn("UTC", uh.PAGE_ZONES)
        for bad in ("Mars/Olympus", "", None, 8, "../etc/passwd", "/etc/localtime", "Asia\\Tokyo", "A" * 80):
            self.assertFalse(uh.valid_zone(bad), bad)
        jan = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.assertEqual(uh.tz_option_label("Asia/Taipei", jan), "UTC+8")
        self.assertEqual(uh.tz_option_label("Asia/Kolkata", jan), "UTC+5:30")
        self.assertEqual(uh.tz_option_label("America/New_York", jan), "UTC-5")
        self.assertEqual(uh.tz_option_label("America/New_York", datetime(2026, 7, 1, tzinfo=timezone.utc)), "UTC-4")
        self.assertEqual((uh.page_tz(None), uh.page_tz("local"), uh.page_tz("Asia/Tokyo")), ("auto", "auto", "Asia/Tokyo"))


class CtxTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()
        write_samples(str(self.data))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_page_settings_persist_and_precedence(self):
        c = ctx(self.data)
        self.assertEqual((c.lang_setting, c.tz_setting, c.key_suffix), ("auto", "auto", ""))
        uh.save_page_setting(None, self.data, "lang", "ja")
        uh.save_page_setting(None, self.data, "tz", "Asia/Tokyo")
        saved = json.loads((self.data / uh.PAGE_SETTINGS).read_text(encoding="utf-8"))
        self.assertEqual((saved["lang"], saved["tz"]), ("ja", "Asia/Tokyo"))
        c = ctx(self.data)
        self.assertEqual((c.lang, c.tz_spec, c.lang_locked, c.tz_locked), ("ja", "Asia/Tokyo", False, False))
        # a config file wins over page_settings.json, and the menus then write into the config
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"lang": "ko", "pace": 14}), encoding="utf-8")
        c = ctx(self.data, str(cfg))
        self.assertEqual((c.lang, c.tz_spec), ("ko", "Asia/Tokyo"))
        uh.save_page_setting(str(cfg), self.data, "tz", "Europe/London")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")), {"lang": "ko", "pace": 14, "tz": "Europe/London"})
        self.assertEqual(ctx(self.data, str(cfg)).tz_spec, "Europe/London")
        # an explicit CLI flag still wins and locks the menu
        c = ctx(self.data, str(cfg), lang="en", tz="UTC")
        self.assertEqual((c.lang, c.tz_spec, c.lang_locked, c.tz_locked), ("en", "UTC", True, True))
        self.assertEqual(c.key_suffix, "")  # fixed for the life of the process: not part of the key

    def test_numeric_turns_privacy_on(self):
        uh.save_page_setting(None, self.data, "lang", "numeric")
        c = ctx(self.data)
        self.assertTrue(c.privacy)
        self.assertEqual(c.lang, "numeric")

    def test_cache_key_has_lang_and_tz(self):
        uh.save_page_setting(None, self.data, "lang", "ja")
        uh.save_page_setting(None, self.data, "tz", "Asia/Tokyo")
        c = ctx(self.data)
        self.assertEqual(c.key_suffix, "-Lja-Zasia_tokyo")
        self.assertTrue(c.pal_key.endswith("-Lja-Zasia_tokyo"), c.pal_key)
        uh.save_page_setting(None, self.data, "display_offset", 1)
        c = ctx(self.data)
        self.assertTrue(c.pal_key.endswith("-o+1-Lja-Zasia_tokyo"), c.pal_key)
        self.assertEqual(uh.strip_offset("gold-sky-pat-o+1-Lzh_tw-Zamerica_new_york"), "gold-sky-pat")
        self.assertEqual(uh.strip_offset("gold-sky-Zutc"), "gold-sky")
        self.assertTrue(uh.is_dir_key("gold-sky-Lnumeric"))
        self.assertTrue(uh.is_dir_key("gold-sky-pat-o-2-Len-Zeurope_london"))
        self.assertFalse(uh.is_dir_key("nope-Lja"))
        self.assertNotEqual(ctx(self.data).pal_key, ctx(self.data, lang="en", tz="UTC").pal_key)

    def test_labels_follow_zone_but_data_zone_does_not(self):
        uh.save_page_setting(None, self.data, "tz", "Asia/Tokyo")
        c = ctx(self.data)
        t = datetime(2026, 1, 15, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(c.disp(t).hour, 9)
        self.assertEqual(c.zlabel(c.disp(t)), "Asia/Tokyo UTC+9")
        local = datetime.now().astimezone().utcoffset()
        self.assertEqual(c.data_tz.utcoffset(t.replace(tzinfo=None)) if hasattr(c.data_tz, "utcoffset") else None,
                         local)  # week files keep the machine zone when only the page menu changed
        c = ctx(self.data, tz="UTC")
        self.assertEqual((c.disp(t).hour, str(c.data_tz)), (0, "UTC"))

    def test_page_has_menus_and_data_stays_raw(self):
        before = digest(self.data)
        uh.save_page_setting(None, self.data, "lang", "zh-TW")
        uh.save_page_setting(None, self.data, "tz", "Asia/Taipei")
        c = ctx(self.data)
        uh.render_history(c)
        self.assertEqual(digest(self.data), before)
        page = read_text(c.hist_dir / "index.html")
        for s in ('id="langsel"', 'id="tzsel"', 'data-api="/api/lang"', 'data-api="/api/tz"', "語言", "時區",
                  "<option value='zh-TW' selected>繁體中文</option>", "<option value='ja'>日本語</option>",
                  "<option value='ko'>한국어</option>", "<option value='zh-CN'>简体中文</option>",
                  "<option value='Asia/Taipei' selected>Asia/Taipei (UTC+8)</option>", '"osfx": "-Lzh_tw-Zasia_taipei"'):
            self.assertIn(s, page, s)
        self.assertNotIn("data-locked", page)
        self.assertTrue((c.hist_dir / "pal" / c.pal_key / "history-1m.png").exists())
        c = ctx(self.data, lang="en", tz="UTC")
        uh.render_history(c)
        page = read_text(c.hist_dir / "index.html")
        self.assertEqual(page.count("data-locked='1'"), 2)
        self.assertIn("Language", page)


class ArrowTests(unittest.TestCase):
    def test_avg_and_projection_always_have_arrows(self):
        """The ▲/▼ of the daily-average and projection cards lives in a small tag beside the number."""
        seen = []
        import matplotlib.axes
        real = matplotlib.axes.Axes.text

        def spy(self, x, y, s, *a, **k):
            seen.append((str(s), k.get("gid")))
            return real(self, x, y, s, *a, **k)
        d = scratch()
        try:
            for i, (pat, pct) in enumerate(((False, "5"), (False, "60"), (True, "5"), (False, "20"))):
                seen.clear()
                rows = [dict(r) for r in ROWS]
                rows[1]["pct"] = pct
                with mock.patch.object(matplotlib.axes.Axes, "text", spy):
                    chart_hud.render(rows, os.path.join(d, f"{i}.png"), "2026-01-01", {"lang": "en", "patterns": pat})
                tags = {g: t for t, g in seen if g in ("stat_tag_2", "stat_tag_3")}
                vals = {g: t for t, g in seen if g in ("stat_val_2", "stat_val_3")}
                self.assertEqual(len(tags), 2, (pat, seen))
                avg = float(pct) / 2
                self.assertEqual(tags["stat_tag_2"], "[▲ faster]" if avg > 14 else "[▼ slower]")
                proj = avg * 7
                want = "[▲ running out]" if proj > 100 else ("[▲ over]" if proj > 98 else "[▼ within]")
                self.assertEqual(tags["stat_tag_3"], want, (pct, tags))
                for v in vals.values():  # the numbers themselves carry no arrow
                    self.assertFalse(v.startswith(("▲", "▼")), v)
        finally:
            shutil.rmtree(d)

    def test_tag_texts(self):
        self.assertEqual(chart_hud.stat_tag("▼未超標"), "［▼未超標］")
        self.assertEqual(chart_hud.stat_tag("▲ over"), "[▲ over]")
        zh = chart_hud.TRANSLATIONS["zh-TW"]
        self.assertEqual((zh["tag_proj_over"], zh["tag_proj_under"], zh["tag_avg_over"], zh["tag_avg_under"], zh["tag_exhaust"]),
                         ("▲超標", "▼未超標", "▲快於基準", "▼慢於基準", "▲即將用罄"))
        for lang in ("zh-CN", "ja", "ko", "numeric"):
            for k in ("tag_proj_over", "tag_proj_under", "tag_avg_over", "tag_avg_under", "tag_exhaust"):
                self.assertIn(k, chart_hud.TRANSLATIONS[lang], (lang, k))
        self.assertEqual(chart_hud.TRANSLATIONS["numeric"]["tag_avg_over"], "▲")

    def test_source_has_no_pattern_condition(self):
        src = (Path(chart_hud.__file__)).read_text(encoding="utf-8")
        self.assertIn('L["tag_avg_over" if avg_day > pace else "tag_avg_under"]', src)
        self.assertIn('L["tag_proj_over" if proj_over else "tag_proj_under"]', src)


class GaugeLegendTests(unittest.TestCase):
    """The legend under the weekly ring: a line in the baseline-tick colour + today's baseline."""
    def _legend(self, days, cfg):
        import matplotlib.axes
        real_text, real_plot = matplotlib.axes.Axes.text, matplotlib.axes.Axes.plot
        seen = {}

        def text(self, x, y, s, *a, **k):
            if k.get("gid") == "gauge_base_legend":
                seen["text"] = str(s)
            return real_text(self, x, y, s, *a, **k)

        def plot(self, *a, **k):
            if k.get("gid") == "gauge_base_line":
                seen["colour"] = k.get("color")
            elif k.get("lw") == 3.0 and "tick" not in seen:
                seen["tick"] = k.get("color")  # the first 3-px line drawn is the ring's baseline tick
            return real_plot(self, *a, **k)
        rows = [{"day": str(i), "date": f"2026-01-0{i}", "pct": str(6 * i), "note": ""} for i in range(1, days + 1)]
        d = scratch()
        try:
            with mock.patch.object(matplotlib.axes.Axes, "text", text), mock.patch.object(matplotlib.axes.Axes, "plot", plot):
                chart_hud.render(rows, os.path.join(d, "g.png"), "2026-01-01", dict(cfg))
        finally:
            shutil.rmtree(d)
        return seen

    def test_label_has_current_baseline(self):
        for days, want in ((1, "14%"), (2, "28%"), (7, "98%")):
            self.assertEqual(self._legend(days, {"lang": "zh-TW"})["text"], "基準線 " + want)
        self.assertEqual(self._legend(3, {"lang": "en"})["text"], "Baseline 42%")
        self.assertEqual(self._legend(3, {"lang": "zh-CN"})["text"], "基准线 42%")
        self.assertEqual(self._legend(2, {"lang": "en", "pace": 10})["text"], "Baseline 20%")
        self.assertEqual(self._legend(3, {"lang": "en", "privacy": True})["text"], "42%")
        self.assertEqual(self._legend(4, {"lang": "numeric"})["text"], "56%")

    def test_line_uses_tick_colour(self):
        for cfg in ({"lang": "en"}, {"lang": "en", "palette": "gold-sky"}):
            seen = self._legend(3, cfg)
            self.assertTrue(seen.get("colour"), cfg)
            self.assertEqual(seen["colour"], seen.get("tick"), cfg)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        write_samples(data)
        cls.data = data
        cls.before = digest(data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])
        cls.hist = os.path.join(data, "history")

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def _post(self, path, body, token=True, host=None):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        if host:
            h["Host"] = host
        code, raw = request(self.url + path, "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_lang_and_tz_endpoints(self):
        for bad in ({"lang": "xx"}, {"lang": 1}, {"lang": None}, {}):
            self.assertEqual(self._post("api/lang", bad)[0], 400, bad)
        for bad in ({"tz": "Mars/Olympus"}, {"tz": "../../etc/passwd"}, {"tz": 9}, {}):
            self.assertEqual(self._post("api/tz", bad)[0], 400, bad)
        self.assertEqual(self._post("api/lang", {"lang": "ja"}, token=False)[0], 403)
        self.assertEqual(self._post("api/tz", {"tz": "Asia/Tokyo"}, token=False)[0], 403)
        self.assertIn(self._post("api/tz", {"tz": "Asia/Tokyo"}, host="evil.example")[0], (403, 421))
        self.assertFalse(os.path.exists(os.path.join(self.data, "page_settings.json")))
        code, j = self._post("api/lang", {"lang": "ja"})
        self.assertEqual((code, j.get("lang"), j.get("locked")), (200, "ja", False))
        code, j = self._post("api/tz", {"tz": "Asia/Tokyo"})
        self.assertEqual((code, j.get("tz"), j.get("locked")), (200, "Asia/Tokyo", False))
        saved = json.loads(read_text(os.path.join(self.data, "page_settings.json")))
        self.assertEqual((saved["lang"], saved["tz"]), ("ja", "Asia/Tokyo"))
        names = os.listdir(os.path.join(self.hist, "pal"))
        self.assertTrue(any(n.endswith("-Lja-Zasia_tokyo") for n in names), names)
        code, raw = request(self.url)
        page = raw.decode("utf-8")
        self.assertIn("<option value='ja' selected>", page)
        self.assertIn("<option value='Asia/Tokyo' selected>Asia/Tokyo (UTC+9)</option>", page)
        self.assertIn("Asia/Tokyo UTC+9", page)
        code, j = self._post("api/lang", {"lang": "auto"})
        code, j = self._post("api/tz", {"tz": "auto"})
        self.assertEqual(code, 200)
        self.assertEqual(digest(self.data), self.before)  # stored readings stay raw


if __name__ == "__main__":
    unittest.main()
