"""Clock tab: hover / tap tooltips for the check-in dots (clock_hotspots + the page's overlay data).

Everything runs in scratch dirs with made-up EXAMPLE check-ins; nothing touches real data.
"""
import json
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import scratch

import usage_history as uh  # noqa: E402
from test_clock_palette import make_ctx, write_samples  # noqa: E402

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


def write_checkins(data, rows):
    """rows: (datetime, name, note, categories, phase)."""
    with open(os.path.join(data, "checkins.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write("timestamp,name,note,category,id,phase,status\n")
        for i, (t, name, note, cats, phase) in enumerate(sorted(rows, key=lambda r: r[0])):
            fh.write(f'{t.isoformat()},{name},"{note}",{cats},ex{i},{phase},\n')


def day(d, h, m):
    return (NOW - timedelta(days=d)).replace(hour=h, minute=m)


class HotspotTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        write_samples(self.dir, days=8)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def draw(self, ctx, key="wk"):
        now, zl, s, ck = uh._clock_inputs(ctx)
        cks, _, colors = ck
        png = os.path.join(self.dir, "c.png")
        uh.draw_clock(ctx, s, cks, colors, png, "t", now - timedelta(days=7, hours=1), now, zl, key, "ot")
        with open(os.path.join(self.dir, "c.json"), encoding="utf-8") as fh:
            return png, json.load(fh), colors

    def test_positions_hit_the_drawn_dots(self):
        import matplotlib.image as mpimg
        from matplotlib.colors import to_hex
        write_checkins(self.dir, [(day(1, 3, 0), "Brown", "EXAMPLE", "code", "point"),
                                  (day(1, 9, 30), "Black", "", "web", "start"),
                                  (day(2, 15, 10), "Grey", "", "", "point"),
                                  (day(2, 20, 45), "Aqua", "", "", "point")])
        png, d, colors = self.draw(make_ctx(self.dir))
        img = mpimg.imread(png)
        self.assertEqual((d["h"], d["w"]), img.shape[:2])
        self.assertEqual(len(d["spots"]), 4)
        for s in d["spots"]:
            px = img[int(round(s["y"] / 100 * d["h"])), int(round(s["x"] / 100 * d["w"]))]
            self.assertEqual(to_hex(px[:3]).upper(), s["items"][0]["c"].upper(), s)
        # 03:00 is right of centre and above the 09:30 dot; 20:45 is left of centre
        by = {s["items"][0]["n"]: s for s in d["spots"]}
        self.assertGreater(by["Brown"]["x"], 50); self.assertLess(by["Aqua"]["x"], 50)
        self.assertLess(by["Brown"]["y"], by["Black"]["y"])
        # keyboard order: clockwise from 0:00
        self.assertEqual([s["items"][0]["n"] for s in d["spots"]], ["Brown", "Black", "Grey", "Aqua"])

    def test_same_time_on_several_days_is_one_spot(self):
        rows = [(day(i, 13, 22), "Brown", f"EXAMPLE {i}", "daily", "point") for i in range(1, 7)]
        rows += [(day(1, 13, 22), "Red", "", "", "point")]  # same time, another ring: a separate spot
        write_checkins(self.dir, rows)
        _, d, _ = self.draw(make_ctx(self.dir))
        self.assertEqual(len(d["spots"]), 2)
        b = next(s for s in d["spots"] if s["items"][0]["n"] == "Brown")
        self.assertEqual(len(b["items"]), 6); self.assertEqual(b["more"], 0)
        ts = [i["ts"] for i in b["items"]]
        self.assertEqual(ts, sorted(ts, reverse=True))  # newest first
        self.assertEqual(b["items"][0]["t"], f"{day(1, 13, 22):%m-%d %H:%M}")
        self.assertEqual(b["items"][0]["z"], "UTC+0")

    def test_cap_and_more(self):
        write_checkins(self.dir, [(day(i % 7, 8, 0) - timedelta(minutes=i // 7), "Grey", "", "", "point") for i in range(11)])
        _, d, _ = self.draw(make_ctx(self.dir))
        self.assertEqual(len(d["spots"]), 1)
        self.assertEqual(len(d["spots"][0]["items"]), uh.CLOCK_TIP_MAX)
        self.assertEqual(d["spots"][0]["more"], 11 - uh.CLOCK_TIP_MAX)

    def test_labels_kind_and_categories(self):
        write_checkins(self.dir, [(day(1, 10, 0), "Black", "EXAMPLE", "code|review", "start"),
                                  (day(1, 18, 0), "Black", "", "", "point")])
        _, d, _ = self.draw(make_ctx(self.dir, "zh-TW"))
        a, b = (s["items"][0] for s in d["spots"])
        self.assertEqual((a["k"], a["cats"], a["note"]), ("start", ["代碼", "檢查驗收"], "EXAMPLE"))
        self.assertEqual((b["k"], b["cats"]), ("point", ["其他"]))

    def test_privacy_alias_and_no_notes(self):
        write_checkins(self.dir, [(day(2, 9, 0), "Brown", "EXAMPLE secret", "code", "point"),
                                  (day(1, 11, 0), "Black", "EXAMPLE secret 2", "web", "start")])
        ctx = make_ctx(self.dir, privacy=True)
        _, d, _ = self.draw(ctx)
        items = [i for s in d["spots"] for i in s["items"]]
        self.assertEqual(sorted(i["n"] for i in items), ["#1", "#2"])
        self.assertEqual(next(i for i in items if i["t"].endswith("09:00"))["n"], "#1")  # same order as public_alias
        self.assertTrue(all(i["note"] == "" for i in items))
        self.assertEqual(next(i for i in items if i["n"] == "#1")["cats"], [str(ctx.categories.ids.index("code") + 1)])
        self.assertNotIn("Brown", json.dumps(d)); self.assertNotIn("secret", json.dumps(d))

    def test_page_embeds_spots_escaped(self):
        note = "EXAMPLE </script><img src=x onerror=alert(1)> & \\u003c"
        write_checkins(self.dir, [(NOW - timedelta(minutes=30), "Grey", note, "", "point")])
        with open(os.path.join(self.dir, "state.json"), "w", encoding="utf-8") as fh:  # the reset week covers the last 2 days
            json.dump({"reset_at": (NOW - timedelta(days=2)).isoformat()}, fh)
        ctx = make_ctx(self.dir)
        uh.render_clock(ctx)
        uh.render_history(ctx)
        self.assertTrue((ctx.hist_dir / "clock-1d.json").exists() and (ctx.hist_dir / "clock-wk-ot.json").exists())
        page = (ctx.hist_dir / "index.html").read_text(encoding="utf-8")
        m = re.search(r'<script type="application/json" id="clocktips">(.*?)</script>', page, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("<", m.group(1))  # a note can never close the tag
        data = json.loads(m.group(1))
        for k in ("1d", "wk"):
            self.assertEqual(data["spots"][k]["spots"][0]["items"][0]["note"], note)
        self.assertNotIn("<img src=x", page)

    def test_sidecar_names_served(self):
        ok = uh.CLOCK_JSON_RE.match
        for f in ("clock-1d.json", "clock-wk-ot.json", "clock-1m-sd.json", "clock-all-os.json"):
            self.assertTrue(ok(f), f)
        for f in ("clock-../x.json", "history.json", "clock-1d.png", "clock-wk-ot.json.bak", "state.json", "clock-wk-zz.json"):
            self.assertFalse(ok(f), f)


if __name__ == "__main__":
    unittest.main()
