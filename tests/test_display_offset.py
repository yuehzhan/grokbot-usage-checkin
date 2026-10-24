"""Display offset (-2..+2 added to the shown weekly %) and the clock tab's rolling range windows.

Everything runs in scratch dirs with made-up samples; nothing touches real data.
"""
import argparse
import hashlib
import json
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server

import chart_hud  # noqa: E402
import usage_history as uh  # noqa: E402
import usage_offset as uo  # noqa: E402
from test_clock_palette import write_samples  # noqa: E402

ROWS = [{"day": "1", "date": "2026-01-01", "pct": "0.4", "note": ""}, {"day": "2", "date": "2026-01-02", "pct": "20", "note": ""}]


def ctx(data, config=None):
    a = argparse.Namespace(config=config, data_dir=str(data), tz="UTC", lang="en", theme=None, pace=None,
                           palette=None, privacy=False, host=None, port=None)
    return uh.Ctx(a)


def digest(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).glob("*.csv"))}


class OffsetValueTests(unittest.TestCase):
    def test_clamp_and_apply(self):
        for raw, want in ((0, 0), (1, 1), (-1, -1), (2, 2), (5, 2), (-9, -2), ("1", 1), (1.6, 2), (None, 0),
                          (True, 0), ("x", 0), (float("nan"), 0)):
            self.assertEqual(uo.clamp(raw), want, raw)
        self.assertEqual(uo.apply(40, 1), 41)
        self.assertEqual(uo.apply(0.4, -1), 0.0)  # never below 0
        self.assertEqual(uo.apply(100, 2), 102)  # no upper cap: over 100 % stays visible
        self.assertIsNone(uo.apply(None, 1))
        self.assertEqual((uo.tag(0), uo.tag(1), uo.tag(-2)), ("", "(+1)", "(\u22122)"))
        self.assertEqual((uo.suffix(0), uo.suffix(1), uo.suffix(-2)), ("", "-o+1", "-o-2"))
        self.assertEqual(uo.option_text(1, "zh-TW"), "+1（同桌面版）")
        self.assertEqual(uo.option_text(1, "en"), "+1 (match desktop app)")
        self.assertEqual(uo.option_text(0, "en"), "+0")
        self.assertEqual(uo.option_text(-1, "numeric"), "\u22121")

    def test_chart_hud_uses_shown_values(self):
        self.assertEqual(chart_hud.normalize_cfg({"display_offset": 7})["display_offset"], 2)
        self.assertEqual(chart_hud.normalize_cfg({})["display_offset"], 0)
        seen = {}

        def spy(fig, ax, data, cfg, L, pal, t_info):
            seen.update(pcts=list(data["pcts"]), latest=data["latest_pct"])
        d = scratch()
        try:
            with mock.patch.object(chart_hud, "draw_hero_and_stats", spy):
                chart_hud.render([dict(r) for r in ROWS], os.path.join(d, "a.png"), "2026-01-01", {"lang": "en", "display_offset": 1})
            self.assertEqual(seen, {"pcts": [1.4, 21.0], "latest": 21.0})
            with mock.patch.object(chart_hud, "draw_hero_and_stats", spy):
                chart_hud.render([dict(r) for r in ROWS], os.path.join(d, "b.png"), "2026-01-01", {"lang": "en", "display_offset": -1})
            self.assertEqual(seen["pcts"], [0.0, 19.0])
            self.assertEqual(ROWS[0]["pct"], "0.4")  # rows are not changed
            chart_hud.render([dict(r) for r in ROWS], os.path.join(d, "c.png"), "2026-01-01", {"lang": "en", "display_offset": 1})
            self.assertGreater(os.path.getsize(os.path.join(d, "c.png")), 10000)
        finally:
            shutil.rmtree(d)


class OffsetCtxTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()
        write_samples(str(self.data))
        (self.data / "week-2026-01-04.csv").write_text(
            "day,date,pct,note\n1,2026-01-04,0.4,\n2,2026-01-05,12.5,\n3,2026-01-06,30,\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_config_page_settings_and_cache_key(self):
        c = ctx(self.data)
        self.assertEqual((c.display_offset, c.pal_key), (0, "orange-teal"))
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"display_offset": 9, "patterns": True}), encoding="utf-8")
        c = ctx(self.data, str(cfg))
        self.assertEqual((c.display_offset, c.pal_key), (2, "orange-teal-pat-o+2"))
        self.assertEqual(uh._cycle_palettes(c), ["orange-teal-pat-o+2"])
        self.assertEqual(uh.strip_offset("gold-sky-pat-o-1"), "gold-sky-pat")
        self.assertTrue(uh.is_dir_key("gold-sky-o+1"))
        self.assertFalse(uh.is_dir_key("nope-o+1"))
        self.assertTrue(c.theme_for("gold-sky-pat-o+1")["patterns"])
        uh.save_page_setting(None, self.data, "display_offset", 1)
        self.assertEqual(ctx(self.data).display_offset, 1)
        self.assertEqual(ctx(self.data, str(cfg)).display_offset, 2)  # the config file wins

    def test_history_values_shown_and_csv_raw(self):
        weeks_raw = uh.load_weeks(ctx(self.data))
        before = digest(self.data)
        uh.save_page_setting(None, self.data, "display_offset", 1)
        c = ctx(self.data)
        weeks = uh.load_weeks(c)
        self.assertTrue(weeks)
        for a, b in zip(weeks_raw, weeks):
            self.assertEqual([round(x[2] - y[2], 6) for x, y in zip(b["days"], a["days"])], [1.0] * len(a["days"]))
            self.assertAlmostEqual(b["peak"], a["peak"] + 1)
        uh.render_history(c)
        self.assertEqual(digest(self.data), before)  # samples.csv and week CSVs untouched
        self.assertTrue((c.hist_dir / "pal" / "orange-teal-o+1" / "history-1m.png").exists())
        page = read_text(c.hist_dir / "index.html")
        self.assertIn('id="offsel"', page)
        self.assertIn("<option value='1' selected>+1 (match desktop app)</option>", page)
        self.assertIn('"osfx": "-o+1"', page)
        self.assertIn('"otag": "(+1)"', page)
        # the samples used for the clock (hourly increases) stay raw
        self.assertEqual(uh.load_samples(c), uh.load_samples(ctx(self.dir / "data")))


class ClockWindowTests(unittest.TestCase):
    def test_rolling_windows(self):
        d = Path(scratch())
        try:
            (d / "data").mkdir()
            c = ctx(d / "data")
            now = datetime(2026, 10, 24, 17, 0, tzinfo=timezone.utc)
            for k, n in (("1m", 30), ("3m", 90), ("6m", 180), ("1y", 365)):
                self.assertEqual(uh._clock_since(c, k, now), now - timedelta(days=n), k)
            self.assertIsNone(uh._clock_since(c, "all", now))
            self.assertEqual(uh._clock_since(c, "1d", now), now - timedelta(hours=24))
        finally:
            shutil.rmtree(d)


class OffsetServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        write_samples(data)
        cls.data = data
        cls.before = digest(data)
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

    def test_offset_endpoint(self):
        for bad in ({"offset": 3}, {"offset": "1"}, {"offset": True}, {"offset": 1.5}, {}):
            self.assertEqual(self._post("api/display-offset", bad)[0], 400, bad)
        self.assertEqual(self._post("api/display-offset", {"offset": 1}, token=False)[0], 403)
        code, j = self._post("api/display-offset", {"offset": 1})
        self.assertEqual((code, j.get("display_offset")), (200, 1))
        self.assertEqual(json.loads(read_text(os.path.join(self.data, "page_settings.json")))["display_offset"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.hist, "pal", "orange-teal-o+1", "history-1m.png")))
        code, j = self._post("api/palette/render", {"name": "gold-sky", "clock": "wk"})
        self.assertEqual((code, j.get("name")), (200, "gold-sky-o+1"))
        code, raw = request(self.url + "pal/gold-sky-o+1/clock-wk-ot.png?v=" + j["v"])
        self.assertEqual(code, 200)
        self.assertTrue(raw.startswith(b"\x89PNG"))
        code, raw = request(self.url)
        self.assertIn("<option value='1' selected>", raw.decode("utf-8"))
        code, j = self._post("api/display-offset", {"offset": 0})
        self.assertEqual((code, j.get("display_offset")), (200, 0))
        self.assertEqual(digest(self.data), self.before)  # stored readings stay raw


class ViewerTests(unittest.TestCase):
    def test_viewer_and_record_follow_the_setting(self):
        src = (Path(uh.__file__).parent / "usage_view.py").read_text(encoding="utf-8")
        self.assertIn("usage_offset.from_settings(state.cfg_data, state.data_dir)", src)
        self.assertIn("Math.max(0, +rawPct + OFFS.off)", src)
        rsrc = (Path(uh.__file__).parent / "usage_record.py").read_text(encoding="utf-8")
        self.assertIn('"display_offset": _display_offset(cfg_file_data, data_dir)', rsrc)
        d = Path(scratch())
        try:
            (d / "page_settings.json").write_text(json.dumps({"display_offset": -1}), encoding="utf-8")
            self.assertEqual(uo.from_settings({}, d), -1)
            self.assertEqual(uo.from_settings({"display_offset": 2}, d), 2)
        finally:
            shutil.rmtree(d)


class ReadmeTests(unittest.TestCase):
    def test_docs(self):
        root = Path(__file__).resolve().parent.parent
        en = (root / "README.md").read_text(encoding="utf-8")
        zh = (root / "README.zh-TW.md").read_text(encoding="utf-8")
        self.assertIn("`display_offset`", en)
        self.assertIn("`display_offset`", zh)
        self.assertIn("+1 (match desktop app)", en)
        self.assertIn("+1（同桌面版）", zh)
        self.assertIn("30 / 90 / 180 / 365 days", en)
        self.assertIn("30／90／180／365 天", zh)


if __name__ == "__main__":
    unittest.main()
