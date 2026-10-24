"""Clock tab (24 h dial + range / layout menus) and lazily drawn palettes.

Everything runs in scratch dirs with made-up samples; nothing touches real data.
"""
import argparse
import json
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server

import usage_history as uh  # noqa: E402
import usage_palette as up  # noqa: E402


def write_samples(data, days=12):
    """Hourly readings ending at the current hour (UTC), reset to 0 every Sunday 09:00."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t, v, rows = now - timedelta(days=days), 0.0, []
    while t <= now:
        v = 0.0 if (t.weekday() == 6 and t.hour == 9) else min(100.0, v + (0.9 if 9 <= t.hour <= 22 else 0.2) + (t.hour % 5) * 0.1)
        rows.append(f"{t:%Y-%m-%dT%H:%M}:00+00:00,{v:.1f}\n")
        t += timedelta(hours=1)
    with open(os.path.join(data, "samples.csv"), "w", encoding="utf-8") as fh:
        fh.write("timestamp,pct\n" + "".join(rows))


def make_ctx(data, lang="en", privacy=False):
    a = argparse.Namespace(config=None, data_dir=data, tz="UTC", lang=lang, theme=None, pace=None, palette=None,
                           privacy=privacy, host=None, port=None)
    return uh.Ctx(a)


class PeriodColourTests(unittest.TestCase):
    def test_period_colours(self):
        for n in up.names():
            for light in (False, True):
                cols = up.period_colors(n, light)
                self.assertEqual(len(cols), 8, n)
                self.assertEqual(len({c.upper() for c in cols}), 8, n)
                self.assertNotIn(up.get(n, light)["over"].upper(), [c.upper() for c in cols], n)
                if n == "orange-deepblue":  # clock periods stay pure Okabe-Ito; the under blue is tuned separately
                    self.assertIn(cols[0], ("#56B4E9", "#0072B2"))
                else:
                    self.assertEqual(cols[0], up.get(n, light)["under"])
        okabe_ito = {"#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#000000"}
        self.assertTrue(set(up.period_colors("orange-deepblue")[:6]) <= okabe_ito)


class PeriodGroupTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        self.ctx = make_ctx(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_week_is_split_by_day(self):
        now = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)  # a Saturday
        since = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
        g = uh.clock_periods(self.ctx, "wk", since, now, [])
        self.assertEqual(len(g), 7)
        self.assertEqual(g[0][0], "Sun 10/18")
        self.assertEqual(g[-1][0], "Sat 10/24 (today)")
        self.assertTrue(g[-1][3] and not any(x[3] for x in g[:-1]))
        self.assertEqual(g[0][1], since)  # clipped to the range start
        self.assertTrue(all(a[2] <= b[1] for a, b in zip(g, g[1:])))  # oldest first, no overlap
        self.assertIsNone(uh.clock_periods(self.ctx, "1d", now - timedelta(hours=24), now, []))
        zh = make_ctx(self.dir, "zh-TW")
        self.assertEqual(uh.clock_periods(zh, "wk", since, now, [])[-1][0], "10/24 週六（今天）")

    def test_longer_ranges_are_grouped(self):
        now = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
        m1 = uh.clock_periods(self.ctx, "1m", now - timedelta(days=30), now, [])
        self.assertTrue(1 <= len(m1) <= 6)
        self.assertTrue(m1[0][0].startswith("Wk 1 "))
        q = uh.clock_periods(self.ctx, "3m", datetime(2026, 7, 24, 16, tzinfo=timezone.utc), now, [])
        self.assertEqual([x[0] for x in q], ["Jul 2026", "Aug 2026", "Sep 2026", "Oct 2026 (now)"])
        y = uh.clock_periods(self.ctx, "1y", datetime(2025, 10, 24, 16, tzinfo=timezone.utc), now, [])
        self.assertEqual(y[0][0], "Q4 2025")
        self.assertTrue(len(y) <= 5)
        old = [(datetime(2019, 3, 1, tzinfo=timezone.utc), 1.0)]
        a = uh.clock_periods(self.ctx, "all", None, now, old)
        self.assertEqual([x[0] for x in a][-1], "2026 (now)")  # by year once it would be more than 8 quarters
        self.assertTrue(len(a) <= 8)


class DrawOrderTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        write_samples(self.dir)
        self.ctx = make_ctx(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _bars(self, mode):
        import matplotlib.axes
        calls, orig = [], matplotlib.axes.Axes.bar

        def spy(ax, x, height, *a, **k):
            calls.append((x, height, k.get("zorder", 0), k.get("width")))
            return orig(ax, x, height, *a, **k)

        now, zl, s, ck = uh._clock_inputs(self.ctx)
        cks, _, colors = ck
        with mock.patch.object(matplotlib.axes.Axes, "bar", spy):
            uh.draw_clock(self.ctx, s, cks, colors, os.path.join(self.dir, f"c-{mode}.png"), "t", now - timedelta(days=7), now, zl, "wk", mode)
        self.assertTrue(os.path.getsize(os.path.join(self.dir, f"c-{mode}.png")) > 1000)
        return calls

    def test_overlay_draws_longest_first(self):
        for mode in ("ot", "os"):
            calls = self._bars(mode)
            self.assertTrue(len(calls) > 24)
            by_slot = {}
            for x, hgt, z, _ in calls:
                by_slot.setdefault(round(float(x), 6), []).append((z, float(hgt)))
            for bars in by_slot.values():
                hs = [h for _, h in sorted(bars)]
                self.assertEqual(hs, sorted(hs, reverse=True))  # higher zorder = shorter bar, so none is hidden

    def test_side_by_side_splits_each_slot(self):
        calls = self._bars("sd")
        self.assertTrue(4 <= len(calls) <= 8)  # one bar() call per period (24 thin wedges each)
        w = 2 * 3.141592653589793 / 24
        for _, _, _, width in calls:
            self.assertLess(width, w * 0.86 / 3)


class LazyPaletteServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        write_samples(data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])
        cls.hist = os.path.join(data, "history")

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def test_custom_colours_render(self):
        code, j = self._render({"name": "custom-336699ffcc00"}, token=False)
        self.assertEqual(code, 403)  # same token / Origin checks as every write
        for bad in ("custom", "custom-33669", "custom-33669gffcc00", "custom-336699ffcc00x"):
            self.assertEqual(self._render({"name": bad})[0], 400, bad)
        code, j = self._render({"name": "custom-336699ffcc00", "patterns": True})
        self.assertEqual(code, 200, j)
        self.assertEqual(j["name"], "custom-336699ffcc00-pat")
        self.assertEqual(j["colors"]["dark"]["over"], "#FFCC00")
        self.assertTrue(os.path.exists(os.path.join(self.hist, "pal", "custom-336699ffcc00-pat", "clock-wk-ot.png")))
        self.assertEqual(self._render({"name": "high-contrast"})[0], 200)  # legacy key still draws
        code, j = self._render({"name": "teal-mono"})
        self.assertEqual(code, 200)
        self.assertNotIn("colors", j)

    def _render(self, body, token=True):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        code, raw = request(self.url + "api/palette/render", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_default_files_and_page_controls(self):
        for f in ("clock-1d.png", "clock-wk-ot.png", "index.html"):
            self.assertTrue(os.path.exists(os.path.join(self.hist, f)), f)
        self.assertFalse(os.path.exists(os.path.join(self.hist, "clock-7d.png")))
        code, raw = request(self.url)
        page = raw.decode("utf-8")
        self.assertEqual(code, 200)
        for i in ("pallist", "clklist", "clolist", "clflist", "clkimg", "po-amber-cobalt", "clk-all", "clo-sd", "clf-s"):
            self.assertRegex(page, f"id=['\"]{i}['\"]", i)
        self.assertEqual(len(re.findall(r"id=['\"]po-", page)), len(up.names()) + 1)  # + "Custom…"
        # two-colour presets, then the ★ note, then the "Solid colours" header and its presets, then Custom… last
        lst = re.search(r"id=['\"]pallist['\"].*?</ul>", page, re.S).group(0)
        order = [lst.index(x) for x in ("po-amber-cobalt", "palnote", "palgrp", "po-teal-mono", "po-green-mono", "po-custom")]
        self.assertEqual(order, sorted(order))
        self.assertIn(">Solid colours<", lst)
        self.assertNotIn("po-high-contrast", lst)  # legacy preset: not offered
        self.assertRegex(page, r"id='cpick' hidden")  # colour pickers only shown for Custom…
        style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
        self.assertIn("--accent:", style)
        rest = re.sub(r":root\{[^}]*\}", "", style)
        under = up.get(up.DEFAULT)["under"]
        self.assertNotIn(under.lower(), rest.lower())  # chrome uses var(--accent), not the palette's hex
        self.assertNotIn(under.upper(), rest)

    def test_lazy_render_is_cached(self):
        code, j = self._render({"name": "gold-sky", "clock": "1m", "mode": "sd"})
        self.assertEqual(code, 200, j)
        self.assertTrue(j["ok"] and not j["cached"] and j["v"])
        d = os.path.join(self.hist, "pal", "gold-sky")
        for f in ("clock-1d.png", "clock-wk-ot.png", "clock-1m-sd.png", "history-1m.png"):
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)
        code, j2 = self._render({"name": "gold-sky", "clock": "1m", "mode": "sd"})
        self.assertTrue(j2["cached"])
        self.assertEqual(j2["v"], j["v"])
        code, j3 = self._render({"name": "gold-sky", "clock": "wk", "mode": "os"})  # palette cached, new layout drawn
        self.assertEqual(code, 200)
        self.assertTrue(os.path.exists(os.path.join(d, "clock-wk-os.png")))
        code, raw = request(self.url + "pal/gold-sky/clock-1m-sd.png?v=" + j["v"])
        self.assertEqual(code, 200)
        self.assertTrue(raw.startswith(b"\x89PNG"))

    def test_bad_requests(self):
        self.assertEqual(self._render({"name": "nope"})[0], 400)
        self.assertEqual(self._render({"name": "orange-deepblue", "clock": "2w"})[0], 400)
        self.assertEqual(self._render({"name": "orange-deepblue", "clock": "wk", "mode": "zz"})[0], 400)
        self.assertEqual(self._render({"name": "orange-deepblue"}, token=False)[0], 403)


if __name__ == "__main__":
    unittest.main()
