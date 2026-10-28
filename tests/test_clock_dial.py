"""Usage clock 時制 menu: 24H (default, unchanged) / AM (hours 0-11) / PM (hours 12-23) on a 12-hour clock face."""
import json
import math
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from _helpers import read_text, request, scratch, start_server, stop_server
from test_clock_palette import make_ctx, write_samples

import usage_history as uh

UTC = timezone.utc


def tables():
    return {k: v for k, v in uh.STR.items()}


class StringTests(unittest.TestCase):
    def test_every_language_has_the_menu(self):
        for lang, L in tables().items():
            self.assertTrue(L["clock_dl"], lang)
            self.assertEqual(set(L["clock_do"]), set(uh.CLOCK_DIALS), lang)
            self.assertTrue(L["clock_hl"]["am"] and L["clock_hl"]["pm"] and L["clock_hl"]["am"] != L["clock_hl"]["pm"], lang)
            self.assertEqual(sorted(re.findall(r"\{(\w+)", L["clock_hp"])), ["h", "s"], lang)
        self.assertEqual(uh.STR["zh-TW"]["clock_dl"], "時制")
        self.assertEqual(list(uh.STR["en"]["clock_do"]), ["24", "auto", "am", "pm"])  # menu order
        self.assertEqual(list(uh.CLOCK_DIALS), ["24", "auto", "am", "pm"])
        self.assertEqual(list(uh.STR["zh-TW"]["clock_do"].values()), ["近 24 小時", "近 12 小時", "上午 12 小時", "下午 12 小時"])

    def test_numeric_has_no_words(self):
        L = uh.STR["numeric"]
        for t in [L["clock_dl"], L["clock_hp"]] + list(L["clock_do"].values()) + list(L["clock_hl"].values()):
            t = re.sub(r"\{\w+\}", "", t)
            self.assertIsNone(re.search(r"[A-Za-z]", t), t)
            self.assertFalse(any(ord(c) >= 0x2E80 for c in t), t)


class GeometryTests(unittest.TestCase):
    def test_files_and_json_route(self):
        self.assertEqual(uh.clock_file("1d"), "clock-1d.png")
        self.assertEqual(uh.clock_file("wk", "ot"), "clock-wk-ot.png")
        self.assertEqual(uh.clock_file("1d", None, "am"), "clock-1d-am.png")
        self.assertEqual(uh.clock_file("1m", "sd", "pm"), "clock-1m-sd-pm.png")
        self.assertEqual(uh.clock_file("wk", "ot", "24"), "clock-wk-ot.png")
        for ok in ("clock-1d.json", "clock-1d-am.json", "clock-wk-ot-pm.json"):
            self.assertTrue(uh.CLOCK_JSON_RE.match(ok), ok)
        for bad in ("clock-1d-xm.json", "clock-wk-ot-am-pm.json", "clock-wk-am.json"):
            self.assertFalse(uh.CLOCK_JSON_RE.match(bad), bad)

    def test_twelve_hour_face(self):
        t = lambda h, m=0: datetime(2026, 10, 27, h, m, tzinfo=UTC)
        self.assertAlmostEqual(uh._clock_theta(t(6)), math.pi / 2)              # 24H: 6:00 at the right
        self.assertAlmostEqual(uh._clock_theta(t(0), "am"), 0)                  # 12 at the top
        self.assertAlmostEqual(uh._clock_theta(t(12), "pm"), 0)
        self.assertAlmostEqual(uh._clock_theta(t(3), "am"), math.pi / 2)        # 3 o'clock at the right
        self.assertAlmostEqual(uh._clock_theta(t(15), "pm"), math.pi / 2)
        self.assertAlmostEqual(uh._clock_theta(t(9, 30), "am"), 2 * math.pi * 9.5 / 12)
        self.assertEqual(uh.dial_hours("am"), list(range(12)))
        self.assertEqual(uh.dial_hours("pm"), list(range(12, 24)))
        self.assertEqual(uh.dial_hours("24"), list(range(24)))


class DrawTests(unittest.TestCase):
    """AM keeps only hours 0-11 (PM 12-23): bars, check-in dots, peak, average and n all describe the shown half."""
    NOW = datetime(2026, 10, 27, 23, 59, tzinfo=UTC)

    def setUp(self):
        self.dir = scratch()
        self.ctx = make_ctx(self.dir)
        t, v, self.s = datetime(2026, 10, 27, 0, 0, tzinfo=UTC), 0.0, []
        while t <= self.NOW:
            self.s.append((t, v))
            v += 2.0 if t.hour == 10 else (5.0 if t.hour == 21 else (1.0 if t.hour < 12 else 3.0))
            t += timedelta(hours=1)
        self.cks = [{"t": datetime(2026, 10, 27, 9, 30, tzinfo=UTC), "name": "demo-a", "note": "", "cats": []},
                    {"t": datetime(2026, 10, 27, 15, 0, tzinfo=UTC), "name": "demo-b", "note": "", "cats": []}]

    def tearDown(self):
        shutil.rmtree(self.dir)

    def draw(self, dial, key="1d", title="t"):
        import matplotlib.axes
        import matplotlib.figure
        texts, ticks, bars = [], [], []
        rt, rl, rb = matplotlib.figure.Figure.text, matplotlib.axes.Axes.set_xticklabels, matplotlib.axes.Axes.bar

        def st(self, x, y, s, *a, **k):
            texts.append(s)
            return rt(self, x, y, s, *a, **k)

        def sl(self, labels, *a, **k):
            ticks.append([str(x) for x in labels])
            return rl(self, labels, *a, **k)

        def sb(self, x, h, *a, **k):
            bars.append((list(x) if hasattr(x, "__len__") else [x], list(h) if hasattr(h, "__len__") else [h]))
            return rb(self, x, h, *a, **k)
        path = os.path.join(self.dir, uh.clock_file(key, "ot" if key != "1d" else None, dial))
        with mock.patch.object(matplotlib.figure.Figure, "text", st), mock.patch.object(matplotlib.axes.Axes, "set_xticklabels", sl), \
                mock.patch.object(matplotlib.axes.Axes, "bar", sb):
            uh.draw_clock(self.ctx, self.s, self.cks, {"demo-a": "#FF0000", "demo-b": "#00FF00"}, path, title,
                          self.NOW - timedelta(hours=24), self.NOW, "UTC", key, "ot", dial)
        spots = json.loads(read_text(path[:-4] + ".json"))["spots"]
        return texts, ticks[-1], bars, spots

    def test_24h_is_unchanged(self):
        texts, ticks, bars, spots = self.draw("24")
        self.assertEqual(ticks[0], "0")
        self.assertEqual(len(ticks), 8)
        self.assertEqual(len(bars[0][0]), 24)
        self.assertEqual(len(spots), 2)
        self.assertIn("Peak 21:00–22:00  avg +5%/h  (23 intervals)", texts)
        self.assertTrue(any(t.endswith("· n=23") for t in texts))

    def test_am_and_pm_show_one_half(self):
        for dial, peak, n, ck in (("am", "Peak 10:00–11:00  avg +2%/h  (12 intervals)", 12, "demo-a"),
                                  ("pm", "Peak 21:00–22:00  avg +5%/h  (11 intervals)", 11, "demo-b")):
            texts, ticks, bars, spots = self.draw(dial)
            half = uh.STR["en"]["clock_hl"][dial]
            self.assertEqual(ticks, ["12"] + [str(i) for i in range(1, 12)], dial)
            self.assertEqual(len(bars[0][0]), 12, dial)  # 12 slots, the other half is not folded in
            self.assertIn(f"{half} · {peak}", texts, dial)
            span = {"am": "10/27 00:00–12:00", "pm": "10/26 23:59–24:00 + 10/27 12:00–23:59"}[dial]  # last 24 h ∩ the half
            self.assertIn(f"{span} (UTC) · n={n}", texts, dial)
            self.assertEqual([s["items"][0]["n"] for s in spots], [ck], dial)
        heights = self.draw("am")[2][0][1]
        self.assertEqual(max(heights), 2.0)  # PM's 5%/h never reaches the AM dial
        self.assertEqual(heights[10], 2.0)

    def test_range_dial_groups_by_half(self):
        texts, ticks, bars, spots = self.draw("pm", key="wk")
        self.assertEqual(ticks[0], "12")
        slots = {round(float(x), 6) for xs, _ in bars for x in xs}
        self.assertLessEqual(len(slots), 12)  # overlay: one bar per period and hour, all on the 12 slots
        self.assertTrue(all(x < 2 * math.pi for x in slots))
        self.assertTrue(any("PM 12:00–24:00 · Peak 21:00–22:00" in t for t in texts))

    def test_half_on_the_face(self):
        import matplotlib.axes
        for lang, dial, want in (("en", "am", "AM"), ("en", "pm", "PM"), ("zh-TW", "am", "上午"), ("zh-TW", "pm", "下午"),
                                 ("numeric", "am", "0–11"), ("numeric", "pm", "12–23"), ("en", "24", None)):
            self.ctx = make_ctx(self.dir, lang=lang) if lang != "numeric" else make_ctx(self.dir, privacy=True)
            seen, real = [], matplotlib.axes.Axes.text

            def spy(ax, x, y, s, *a, **k):
                t = real(ax, x, y, s, *a, **k)
                seen.append((t.get_gid(), s, ax.get_rorigin() if hasattr(ax, "get_rorigin") else None))
                return t
            with mock.patch.object(matplotlib.axes.Axes, "text", spy):
                self.draw(dial)
            face = [(s, ro) for g, s, ro in seen if g == "clock_face"]
            if want is None:
                self.assertEqual(face, [])  # 24H: no marker, no hub
            else:
                self.assertEqual([s for s, _ in face], [want], (lang, dial))
                self.assertLess(face[0][1], 0)  # bars start at the hub that carries the marker, none is covered

    def test_title_names_the_half(self):
        want = {("en", "1d", "am"): "Usage Clock — AM 12 hours", ("zh-TW", "1d", "am"): "用量時鐘 — 上午 12 小時",
                ("zh-TW", "1d", "pm"): "用量時鐘 — 下午 12 小時", ("en", "1d", "auto"): "Usage Clock — last 12 hours (PM)",
                ("zh-TW", "1d", "auto"): "用量時鐘 — 近 12 小時（下午）", ("zh-CN", "1d", "auto"): "用量时钟 — 近 12 小时（下午）",
                ("zh-TW", "wk", "pm"): "用量時鐘 — 本週（下午）", ("zh-TW", "1m", "am"): "用量時鐘 — 近一月（上午）",
                ("zh-TW", "wk", "auto"): "用量時鐘 — 本週（下午）", ("en", "wk", "am"): "Usage Clock — this week (AM)"}
        for lang in ("en", "zh-TW", "zh-CN", "es", "pt", "fr", "de", "id", "numeric"):
            for key in ("1d", "wk", "1m", "3m", "6m", "1y", "all"):
                self.ctx = make_ctx(self.dir, lang=lang) if lang != "numeric" else make_ctx(self.dir, privacy=True)
                L = self.ctx.L
                base = uh._clock_title(self.ctx, key)
                for dial in ("24", "am", "pm", "auto"):  # NOW is 23:59, so auto = pm
                    texts = self.draw(dial, key, base)[0]
                    half = "pm" if dial == "auto" else dial
                    if dial == "24":
                        exp = base
                    elif key != "1d":  # range dial: "… this week (AM)"
                        exp = L["clock_tg"].format(t=base, h=L["clock_face"][half])
                    elif dial == "auto":  # 近 12 小時 (上午 / 下午)
                        exp = L["clock_1a"].format(h=L["clock_face"][half])
                    else:  # the left dial covers 12 hours, so the title says 12 hours (never 24)
                        exp = L["clock_1h"][dial]
                    self.assertIn(exp, texts, (lang, key, dial))
                    if dial == "24":  # v1.5.1 title, untouched
                        self.assertFalse([t for t in texts if t.startswith(base) and t != base], (lang, key))
                    if (lang, key, dial) in want:
                        self.assertIn(want[(lang, key, dial)], texts)
                    if key == "1d" and dial != "24":
                        self.assertNotIn(base, texts, (lang, dial))  # not the 24-hour title
                    if lang == "numeric" and dial != "24" and key == "1d":
                        self.assertIsNone(re.search(r"[A-Za-z\u4e00-\u9fff]", exp), exp)

    def test_left_dial_subtitle_spans(self):
        z = timezone(timedelta(hours=8)); L = uh.STR["en"]
        at = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=z)
        sp = lambda now, dial: uh._half_spans(L, now - timedelta(hours=24), now, dial)
        self.assertEqual(sp(at(28, 2, 37), "am"), ["10/27 02:37–12:00", "10/28 00:00–02:37"])  # two stretches, 12 h in all
        self.assertEqual(sp(at(28, 2, 37), "pm"), ["10/27 12:00–24:00"])
        self.assertEqual(sp(at(28, 15, 0), "pm"), ["10/27 15:00–24:00", "10/28 12:00–15:00"])
        self.assertEqual(sp(at(28, 15, 0), "am"), ["10/28 00:00–12:00"])
        self.assertEqual(sp(at(28, 12, 0), "am"), ["10/28 00:00–12:00"])
        self.assertEqual(sp(at(28, 12, 0), "pm"), ["10/27 12:00–24:00"])
        for now in (at(28, 2, 37), at(28, 15, 0), at(28, 11, 59), at(28, 0, 0)):
            for dial in ("am", "pm"):
                tot = 0
                for t in sp(now, dial):
                    a, b = t.split(" ", 1)[1].split("–")
                    f = lambda x: int(x[:2]) * 60 + int(x[3:])
                    tot += f(b) - f(a)
                self.assertEqual(tot, 720, (now, dial))
        self.assertEqual(uh._half_spans(uh.STR["de"], at(27, 2, 0), at(28, 2, 0), "pm"), ["27.10. 12:00–24:00"])  # date as the language writes it

    def test_face_strings_every_language(self):
        for lang, L in uh.STR.items():
            self.assertTrue(L.get("clock_face", {}).get("am") and L["clock_face"].get("pm"), lang)
        for v in uh.STR["numeric"]["clock_face"].values():
            self.assertIsNone(re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]", v), v)  # numeric: no words
        self.assertEqual(uh.STR["ja"]["clock_face"], uh.STR["en"]["clock_face"]) if "ja" in uh.STR else None

    def test_zh_tw_half_label(self):
        self.ctx = make_ctx(self.dir, lang="zh-TW")
        texts, *_ = self.draw("am")
        self.assertTrue(any(t.startswith("上午 0:00–12:00　尖峰 10:00–11:00") for t in texts), texts)

    def test_auto_is_exactly_the_resolved_half(self):  # NOW is 23:59 -> PM
        for lang in ("en", "zh-TW"):
            self.ctx = make_ctx(self.dir, lang=lang)
            for key in ("1d", "wk"):
                a, pm = self.draw("auto", key, "t"), self.draw("pm", key, "t")
                if key == "1d":  # only the left dial's title differs (近 12 小時（下午） vs 下午 12 小時); the drawing is the same
                    self.assertEqual(a[0][2:], pm[0][2:], (lang, key))
                    self.assertNotEqual(a[0][1], pm[0][1])
                else:
                    self.assertEqual(a[0], pm[0], (lang, key))  # same title suffix, subtitle, summary
                self.assertEqual((a[1], a[2], a[3]), (pm[1], pm[2], pm[3]))
                self.assertTrue(any(("（下午）" in t or "(PM)" in t) for t in a[0]), a[0])

    def test_bad_dial_falls_back_to_24(self):
        _, ticks, bars, _ = self.draw("xx")
        self.assertEqual(len(bars[0][0]), 24)


class AutoTests(unittest.TestCase):
    """近 12 小時 ('auto') is AM before 12:00 local time and PM from 12:00 on, resolved at render time (cache keyed by the half)."""

    def test_resolve_dial(self):
        z = timezone(timedelta(hours=8))
        for h, m, want in ((11, 59, "am"), (12, 0, "pm"), (0, 0, "am"), (23, 59, "pm"), (0, 1, "am"), (12, 1, "pm")):
            self.assertEqual(uh.resolve_dial("auto", datetime(2026, 10, 28, h, m, tzinfo=z)), want, (h, m))
        for d in ("24", "am", "pm"):  # the other (old stored) values are untouched
            self.assertEqual(uh.resolve_dial(d, datetime(2026, 10, 28, 5, 0, tzinfo=z)), d)

    def test_auto_draws_the_current_half_and_flips(self):
        d = scratch()
        try:
            t, rows = datetime(2026, 10, 25, 0, 0, tzinfo=UTC), []
            while t <= datetime(2026, 10, 28, 13, 0, tzinfo=UTC):
                rows.append(f"{t:%Y-%m-%dT%H:%M}:00+00:00,{(t - datetime(2026, 10, 25, tzinfo=UTC)).total_seconds() / 3600 * 0.5:.1f}\n")
                t += timedelta(hours=1)
            with open(os.path.join(d, "samples.csv"), "w", encoding="utf-8") as fh:
                fh.write("timestamp,pct\n" + "".join(rows))
            ctx = make_ctx(d)
            pal = ctx.hist_dir / "pal"

            for h, m, half in ((11, 59, "am"), (12, 0, "pm"), (0, 0, "am")):
                ctx.now = lambda h=h, m=m: datetime(2026, 10, 28, h, m, tzinfo=UTC)
                out = uh.render_palette(ctx, "gold-sky", "wk", "ot", False, "auto")
                self.assertTrue(out["ok"], out)
                self.assertEqual(out["dial"], half, (h, m))
                self.assertTrue((pal / "gold-sky" / f"clock-1d-auto-{half}.png").exists(), (h, m))  # own title, own file
                self.assertTrue((pal / "gold-sky" / f"clock-1d-auto-{half}.json").exists(), (h, m))
                self.assertFalse((pal / "gold-sky" / f"clock-1d-{half}.png").exists(), (h, m))  # explicit AM / PM not drawn
                self.assertTrue((pal / "gold-sky" / f"clock-wk-ot-{half}.png").exists(), (h, m))  # both dials follow
            self.assertTrue(uh.render_palette(ctx, "gold-sky", "wk", "ot", False, "auto")["cached"])  # same half again: nothing redrawn
            self.assertEqual(uh.render_palette(ctx, "gold-sky", "wk", "ot", False, "pm")["dial"], "pm")
            self.assertTrue((pal / "gold-sky" / "clock-1d-pm.png").exists())  # explicit PM: a separate file from auto-pm
            self.assertEqual(uh.render_palette(ctx, "gold-sky", "wk", "ot", False, "24")["dial"], "24")
            self.assertEqual(uh.render_palette(ctx, "gold-sky", "wk", "ot", False, "nope"), {"ok": False, "error": "unknown clock dial"})
        finally:
            shutil.rmtree(d)


class ServerTests(unittest.TestCase):
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

    def _render(self, body):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json", "X-Usage-Token": self.token}
        code, raw = request(self.url + "api/palette/render", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_page_has_the_menu_left_of_range(self):
        page = request(self.url)[1].decode("utf-8")
        bar = re.search(r"<div class='clkbar'>(<span class='pal psel' id='cldsel'>.*?)</div><div class='clocks'>", page, re.S)
        self.assertIsNotNone(bar)  # one toolbar above both cards, so wrapping never pushes one card lower than the other
        self.assertEqual(page.count("class='clkbar'"), 1)
        self.assertRegex(page, r"\.clocks\{align-items:start\}")
        self.assertLess(bar.group(1).index("id='cldsel'"), bar.group(1).index("id='clksel'"))
        for k in uh.CLOCK_DIALS:
            self.assertIn(f"id='cld-{k}' data-v='{k}'", page)
        self.assertIn("id='cld-24' data-v='24' aria-selected='true'", page)  # 24H is the default
        self.assertIn("id='clk1img'", page)
        self.assertIn("usage.clockDial", page)
        self.assertIn("r.x=j.dial", page)
        self.assertIn("-auto-", page)  # the left dial of 近 12 小時 has its own images
        self.assertTrue(uh.CLOCK_JSON_RE.match("clock-1d-auto-am.json") and uh.CLOCK_JSON_RE.match("clock-wk-ot-pm.json"))  # the page learns which half 'auto' resolved to from the render reply
        self.assertEqual(re.findall(r"id='cld-(\w+)' data-v", page), ["24", "auto", "am", "pm"])
        self.assertNotIn("clock-1d-am.png", os.listdir(self.hist))  # halves are drawn only when picked

    def test_lazy_half_dials(self):
        code, j = self._render({"name": "gold-sky", "clock": "wk", "mode": "ot", "dial": "am"})
        self.assertEqual(code, 200, j)
        d = os.path.join(self.hist, "pal", "gold-sky")
        for f in ("clock-1d-am.png", "clock-wk-ot-am.png", "clock-1d-am.json", "clock-1d.png"):
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)
        self.assertTrue(self._render({"name": "gold-sky", "clock": "wk", "mode": "ot", "dial": "am"})[1]["cached"])
        code, j2 = self._render({"name": "gold-sky", "clock": "1m", "mode": "sd", "dial": "pm"})
        self.assertFalse(j2["cached"])
        self.assertTrue(os.path.exists(os.path.join(d, "clock-1m-sd-pm.png")))
        self.assertTrue(os.path.exists(os.path.join(d, "clock-1d-pm.png")))
        for f in ("clock-1d-pm.png", "clock-1d-pm.json", "clock-1m-sd-pm.json"):
            code, raw = request(self.url + "pal/gold-sky/" + f + "?v=" + j["v"])
            self.assertEqual(code, 200, f)
        code, ja = self._render({"name": "gold-sky", "clock": "wk", "mode": "ot", "dial": "auto"})
        self.assertEqual(code, 200, ja)
        self.assertIn(ja["dial"], ("am", "pm"))  # the page reads which half 'auto' resolved to
        for f in (f"clock-1d-auto-{ja['dial']}.png", f"clock-1d-auto-{ja['dial']}.json", f"clock-wk-ot-{ja['dial']}.json"):
            self.assertEqual(request(self.url + "pal/gold-sky/" + f + "?v=" + ja["v"])[0], 200, f)
        self.assertEqual(self._render({"name": "gold-sky", "clock": "wk", "dial": "12"})[0], 400)
        self.assertEqual(self._render({"name": "gold-sky", "clock": "wk", "dial": 24})[0], 400)


if __name__ == "__main__":
    unittest.main()
