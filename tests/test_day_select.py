"""每日增量 (per day / per hour): linked selection between the chart's columns and the log's cards.

The picture is one PNG, so chart_hud draws the selection hidden and writes two sprites (what changes between "nothing" and
"everything" selected); the page clips them to the chosen column / card. These tests check the picture side (default =
today / the newest hour, pixel-exact composites, bars untouched, minimum hourly band), the page (click areas, pending
days selectable, default, no stored selection, no new text) and the click logic (run with node)."""
import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

import matplotlib.axes
import numpy as np
from matplotlib.patches import Rectangle
from PIL import Image

from unittest import mock

from _helpers import fixed_datetime, read_text, scratch
from test_clock_palette import make_ctx, write_samples
from test_hourly_gap import ROWS as HOURLY_ROWS, samples as hourly_samples

import chart_hud
import usage_history as uh

DAILY_ROWS = [{"day": str(d), "date": f"2026-10-{23 + d}", "pct": str(p), "note": ""} for d, p in ((1, 12), (2, 30), (3, 41), (4, 70), (5, 74))]


# The daily fixtures are the week of 2026-10-24 (UTC) and "today" is its day 5. The chart takes "today" from the clock, so a test run
# on any other day used to get a different default selection: pin the clock the chart sees (only the chart; page tests use real time).
PINNED_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def pinned_clock():
    return mock.patch.object(chart_hud, "datetime", fixed_datetime(PINNED_NOW))


def render(view, d, name="w", **cfg):
    out = os.path.join(d, f"{name}-{view}.png")
    rows, ws, extra = (DAILY_ROWS, "2026-10-24", {}) if view == "daily" else (HOURLY_ROWS, "2026-01-01", {"samples": hourly_samples()})
    with pinned_clock():
        meta = chart_hud.render(rows, out, ws, dict({"lang": "en", "view": view, "tz": "UTC"}, **extra, **cfg))
    return out, meta


def px(path):
    return np.asarray(Image.open(path).convert("RGBA")).astype(int)


def clip_to(img, rect):
    """Only the pixels of img inside rect (% of the picture), like the page's clip-path: inset()."""
    h, w = img.shape[:2]
    l, t, rw, rh = rect
    out = np.zeros_like(img)
    x0, x1, y0, y1 = int(round(l / 100 * w)), int(round((l + rw) / 100 * w)), int(round(t / 100 * h)), int(round((t + rh) / 100 * h))
    out[y0:y1, x0:x1] = img[y0:y1, x0:x1]
    return out


def over(base, sprite):
    a = sprite[..., 3:4] / 255.0
    out = base.copy()
    out[..., :3] = np.rint(sprite[..., :3] * a + base[..., :3] * (1 - a))
    return out


def composite(d, view, meta, k):
    """What the page shows when item k is selected: the saved picture, the "off" sprite, the "on" sprite clipped twice."""
    sel = meta["sel"]
    out = px(os.path.join(d, f"w-{view}.png"))
    out = over(out, px(os.path.join(d, f"w-{view}-off.png")))
    on = px(os.path.join(d, f"w-{view}-on.png"))
    out = over(out, clip_to(on, (sel.get("clips") or sel["cols"])[k]))
    if sel["rows"][k] and not sel.get("strip"):  # (the per-hour list is a scrolling picture of its own, not part of the chart)
        out = over(out, clip_to(on, sel["rows"][k]))
    return out


class PictureTests(unittest.TestCase):
    def setUp(self):
        self.d = scratch()
        self.addCleanup(shutil.rmtree, self.d)

    def test_daily_default_is_today_and_covers_all_seven_days(self):
        _, meta = render("daily", self.d, sel_sprites=True)
        sel = meta["sel"]
        self.assertEqual(sel["i"], 4)  # day 5 of 7 is today (the cycle's day_now)
        self.assertEqual((len(sel["cols"]), len(sel["rows"])), (7, 7))
        self.assertTrue(all(sel["rows"]), "pending days have a card too")
        for sp in ("off", "on"):
            self.assertTrue(os.path.isfile(os.path.join(self.d, f"w-daily-{sp}.png")), sp)

    def test_no_sprites_unless_asked_and_the_overview_has_no_selection(self):
        _, meta = render("daily", self.d)
        self.assertEqual(sorted(os.listdir(self.d)), ["w-daily.png"])
        _, wk = render("week", self.d)
        self.assertNotIn("sel", wk)
        self.assertEqual(sorted(os.listdir(self.d)), ["w-daily.png", "w-week.png"])

    def test_sprites_make_exactly_the_picture_of_that_selection(self):
        """default picture + off sprite + on sprite clipped to column k and card k == the picture drawn with day k selected,
        for a recorded day and for pending days (mask and highlight alike)."""
        _, meta = render("daily", self.d, sel_sprites=True)
        for k in (0, 1, 5, 6):
            want, _m = render("daily", self.d, name=f"k{k}", sel_day=k + 1)
            got, ref = composite(self.d, "daily", meta, k), px(want)
            wrong = (np.abs(got - ref).max(axis=2) > 6).mean()
            self.assertLess(wrong, 0.0005, f"day {k + 1}: {wrong:.5f} of the pixels differ")
            self.assertEqual(_m["sel"]["i"], k)

    def test_selected_pending_day_keeps_its_card_text(self):
        d1, _ = render("daily", self.d, name="a", sel_day=5)
        d2, _ = render("daily", self.d, name="b", sel_day=7)
        a, b = px(d1), px(d2)
        # the text of day 6 / 7 cards is the same drawing; only the box around card 7 is added
        h, w = a.shape[:2]
        card6 = (slice(int(h * 0.80), int(h * 0.86)), slice(int(w * 0.70), int(w * 0.80)))  # the "Day 6" label
        self.assertLess(np.abs(a[card6] - b[card6]).max(), 8)
        self.assertGreater((np.abs(a - b).max(axis=2) > 6).sum(), 100)

    def test_mask_sits_behind_the_bars_and_does_not_recolour_them(self):
        made = []
        real = matplotlib.axes.Axes.add_patch

        def spy(self, p):
            made.append(p)
            return real(self, p)
        from unittest import mock
        with mock.patch.object(matplotlib.axes.Axes, "add_patch", spy):
            render("daily", self.d, sel_sprites=True)
        masks = [p for p in made if isinstance(p, Rectangle) and p.get_zorder() == 0.5]
        self.assertEqual(len(masks), 2)  # the whole-plot band (sprite source) and the default day's column
        for m in masks:
            self.assertAlmostEqual(m.get_alpha(), 0.12, 2)
            self.assertEqual(m.get_facecolor()[:3], (1.0, 1.0, 1.0))
        bars = [p for p in made if isinstance(p, Rectangle) and p.get_zorder() >= 3 and p.get_height() > 0]
        self.assertTrue(bars)
        # the sprite is only the band over the plot: the bars (drawn in front) are holes in it, and nothing in it is a bar colour
        sel = render("daily", self.d, name="m", sel_sprites=True)[1]["sel"]
        on = clip_to(px(os.path.join(self.d, "w-daily-on.png")), sel["cols"][1])  # day 2: a tall orange bar
        solid = on[on[..., 3] > 0]
        self.assertGreater(len(solid), 5000)
        h, w = on.shape[:2]
        base = px(os.path.join(self.d, "w-daily.png"))  # day 2 is not the selected day: no band in the saved picture
        ys, xs = np.nonzero(on[..., 3])
        grey = (on[ys, xs, :3].max(axis=1) - on[ys, xs, :3].min(axis=1)) <= 40
        # every coloured (bar) pixel of the sprite is the saved picture's own pixel: the band does not recolour a bar
        self.assertLess(np.abs(on[ys, xs, :3] - base[ys, xs, :3])[~grey].max(), 20)  # (anti-aliased bar edges pick up a little of the 12% band)
        self.assertLess(np.abs(on[ys, xs, :3] - base[ys, xs, :3])[grey].max(), 40)  # and the band itself is only ~12% white
        rect = sel["cols"][1]
        h, w = on.shape[:2]
        x0, x1 = int(rect[0] / 100 * w), int((rect[0] + rect[2]) / 100 * w)
        y0, y1 = int(rect[1] / 100 * h), int((rect[1] + rect[3]) / 100 * h)
        holes = (on[y0:y1, x0:x1, 3] == 0).sum()
        self.assertGreater(holes, 3000)  # the bar body

    def test_hourly_default_is_the_newest_row_and_its_band_is_visible(self):
        _, meta = render("hourly", self.d, sel_sprites=True)
        sel = meta["sel"]
        n = len(sel["cols"])
        self.assertGreater(n, 10)
        self.assertEqual(sel["i"], n - 1)  # the default is the newest hour (the list's order is the sort toggle's: newest first by default)
        shown = [(k, r) for k, r in enumerate(sel["rows"]) if r]
        self.assertEqual(len(shown), n)  # no row cap: every hour of the chart is in the list
        self.assertEqual(max(k for k, _ in shown), n - 1)
        self.assertEqual(min(shown, key=lambda kr: kr[1][1])[0], n - 1)  # topmost card: the newest hour (oldest first is the toggle's other order)
        self.assertIn("→", sel["labels"][n - 1])
        W = 1800
        for k in range(n):
            self.assertGreaterEqual(sel["clips"][k][2] / 100 * W, 11.5, k)  # at least a few px, whatever the hour
        thin = [k for k in range(n) if sel["cols"][k][2] / 100 * W < 8]
        self.assertTrue(thin, "single hours are thinner than the minimum band")
        for k in thin:  # centred on the hour's own column
            c0, c1 = sel["cols"][k][0] + sel["cols"][k][2] / 2, sel["clips"][k][0] + sel["clips"][k][2] / 2
            self.assertAlmostEqual(c0, c1, 2)

    def test_hourly_gap_row_band_spans_the_gap(self):
        _, meta = render("hourly", self.d, sel_sprites=True)
        sel = meta["sel"]
        wide = max(range(len(sel["cols"])), key=lambda k: sel["cols"][k][2])
        self.assertGreater(sel["cols"][wide][2], 2.0)  # the 22 h gap spans many hours
        self.assertEqual(sel["clips"][wide], sel["cols"][wide])  # already wider than the minimum: t0..t1 as is

    def test_hourly_sprites_make_the_picture_of_that_selection(self):
        _, meta = render("hourly", self.d, sel_sprites=True)
        n = len(meta["sel"]["cols"])
        for k in (n - 3, n - 1):
            want, _m = render("hourly", self.d, name=f"k{k}", sel_idx=k, sel_sprites=True)
            got, ref = composite(self.d, "hourly", meta, k), px(want)
            wrong = (np.abs(got - ref).max(axis=2) > 6).mean()
            self.assertLess(wrong, 0.0005, f"hour row {k}: {wrong:.5f} of the pixels differ")

    def test_hourly_and_daily_selection_are_independent(self):
        _, md = render("daily", self.d, sel_sprites=True, sel_idx=3)
        self.assertEqual(md["sel"]["i"], 4)
        _, mh = render("hourly", self.d, sel_sprites=True, sel_day=2)
        self.assertEqual(mh["sel"]["i"], len(mh["sel"]["cols"]) - 1)

    def test_hourly_without_enough_readings_has_no_selection(self):
        out = os.path.join(self.d, "x.png")
        meta = chart_hud.render(HOURLY_ROWS, out, "2026-01-01", {"lang": "en", "view": "hourly", "tz": "UTC", "samples": [], "sel_sprites": True})
        self.assertNotIn("sel", meta)
        self.assertEqual(sorted(os.listdir(self.d)), ["x.png"])


def lum(rgb):
    r, g, b = (float(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def cell_lum(img, rect, fx=0.3, fy=0.12):
    """Luminance of an empty spot of a column's cell: near the top of the plot, in the column's left third (no bar, label
    or grid line there). `rect` is the column's click area in % of the picture (what the page lays over the chart)."""
    h, w = img.shape[:2]
    x = int((rect[0] + rect[2] * fx) / 100 * w)
    y = int((rect[1] + rect[3] * fy) / 100 * h)
    return lum(img[y - 1:y + 2, x - 1:x + 2, :3].reshape(-1, 3).mean(axis=0))


class MaskIsDrawnTests(unittest.TestCase):
    """The selection band must really be in the picture (a missing / transparent / mispositioned band passed every other test):
    the band's artist exists, sits inside the plot, is visible with a real alpha in the saved default picture, and the
    selected column is measurably lighter (dark themes) / darker (light themes) than a column of the same cell parity."""
    LIGHT = {"bg_color": "#F4F4F0", "text_color": "#1E241B"}

    def setUp(self):
        self.d = scratch()
        self.addCleanup(shutil.rmtree, self.d)

    def made_patches(self, view, **cfg):
        from unittest import mock
        made, real = [], matplotlib.axes.Axes.add_patch

        def spy(ax, p):
            made.append((ax, p))
            return real(ax, p)
        with mock.patch.object(matplotlib.axes.Axes, "add_patch", spy):
            out, meta = render(view, self.d, sel_sprites=True, **cfg)
        return made, out, meta

    def test_default_day_band_exists_inside_the_plot_and_is_visible(self):
        for cfg in ({}, self.LIGHT):
            made, out, meta = self.made_patches("daily", **cfg)
            masks = [(ax, p) for ax, p in made if isinstance(p, Rectangle) and p.get_zorder() == 0.5]
            self.assertEqual(len(masks), 2)
            ax, cur = masks[1]  # the default day's own column (the first is the whole-plot band the sprite is cut from)
            x0, x1 = sorted(ax.get_xlim())  # (newest first runs the axis backwards)
            y0, y1 = ax.get_ylim()
            self.assertEqual(meta["sel"]["i"], 4)
            self.assertAlmostEqual(cur.get_x(), 4.0)          # day 5's column ...
            self.assertAlmostEqual(cur.get_width(), 1.0)      # ... exactly one column wide
            self.assertGreaterEqual(cur.get_x(), x0)
            self.assertLessEqual(cur.get_x() + cur.get_width(), x1)
            self.assertAlmostEqual(cur.get_height(), y1 - y0)  # full plot height
            self.assertTrue(cur.get_visible(), "the default day's band is hidden in the saved picture")
            self.assertGreaterEqual(cur.get_alpha(), 0.08)
            self.assertLessEqual(cur.get_alpha(), 0.2)
            self.assertLess(cur.get_zorder(), min(p.get_zorder() for _, p in made if isinstance(p, Rectangle) and p.get_zorder() >= 3))

    def test_default_day_column_is_lighter_than_its_neighbours_in_the_saved_picture(self):
        _, meta = render("daily", self.d, sel_sprites=True)
        img = px(os.path.join(self.d, "w-daily.png"))
        cols = meta["sel"]["cols"]
        sel = cell_lum(img, cols[4])
        for k in (0, 2, 6):  # same cell parity as day 5, not selected
            self.assertGreater(sel - cell_lum(img, cols[k]), 15, f"day 5 vs day {k + 1}")
        for k in (1, 3, 5):  # the other parity: the band is clearly more than the alternating shading
            self.assertGreater(sel - cell_lum(img, cols[k]), 15, f"day 5 vs day {k + 1}")

    def test_light_theme_band_is_darker_than_its_neighbours(self):
        _, meta = render("daily", self.d, sel_sprites=True, **self.LIGHT)
        img = px(os.path.join(self.d, "w-daily.png"))
        cols = meta["sel"]["cols"]
        for k in (0, 2, 6):
            self.assertLess(cell_lum(img, cols[4]) - cell_lum(img, cols[k]), -10, f"day 5 vs day {k + 1}")

    def test_moved_selection_shows_the_band_on_the_clicked_day_only(self):
        _, meta = render("daily", self.d, sel_sprites=True)
        sel = meta["sel"]
        for k in (1, 6):  # a recorded day and a pending one
            img = composite(self.d, "daily", meta, k)
            same = [j for j in range(7) if j != k and j % 2 == k % 2]
            for j in same:
                self.assertGreater(cell_lum(img, sel["cols"][k]) - cell_lum(img, sel["cols"][j]), 15, f"day {k + 1} vs day {j + 1}")
            self.assertLess(abs(cell_lum(img, sel["cols"][4]) - cell_lum(img, sel["cols"][2])), 4, "today's band is gone")

    def test_hourly_band_is_in_the_picture_and_lighter(self):
        made, out, meta = self.made_patches("hourly")
        sel = meta["sel"]
        masks = [p for _, p in made if isinstance(p, Rectangle) and p.get_zorder() == 0.5]
        self.assertEqual(len(masks), 2)
        self.assertTrue(masks[1].get_visible())
        self.assertGreater(masks[1].get_alpha(), 0.08)
        img = px(out)
        i = sel["i"]
        mid = lambda r: [r[0] + r[2] / 2, r[1], 0.0, r[3]]  # noqa: E731  a thin probe down the middle of a band / column
        shown = sel["clips"][i]  # the band shown: at least the minimum width, centred on the hour's column
        self.assertGreater(shown[2] / 100 * 1800, 11.5)
        for k in (i - 4, i - 8):  # other hours of the same cell parity
            self.assertGreater(cell_lum(img, mid(shown), fx=0.0) - cell_lum(img, mid(sel["cols"][k]), fx=0.0), 15, f"hour row {i} vs {k}")


def week_samples(hours=7 * 24):
    """One reading per hour for the whole week (2026-01-01 00:00 UTC on): 168 hour rows, the busy hours at 1-3 %."""
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    out, v = [], 1.0
    for h in range(hours):
        v += 0.2 + (h % 7) * 0.4 if 8 <= (h % 24) <= 22 else 0.1
        out.append((t0 + timedelta(hours=h), round(min(v, 99.0), 1)))
    return out


class HourlyListTests(unittest.TestCase):
    """The per-hour list is not limited to one day: every hour row of the 7-day chart is in it (newest first by default, oldest first with the sort toggle; scrolled by the page)."""

    @classmethod
    def setUpClass(cls):
        cls.d = scratch()
        cls.out = os.path.join(cls.d, "w-hourly.png")
        cls.meta = chart_hud.render(HOURLY_ROWS, cls.out, "2026-01-01", {"lang": "en", "view": "hourly", "tz": "UTC", "samples": week_samples(),
                                                                       "sel_sprites": True})
        cls.sel = cls.meta["sel"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def test_every_hour_of_the_chart_has_a_row(self):
        sel = self.sel
        n = len(sel["cols"])
        self.assertGreaterEqual(n, 160)  # a full week of hourly readings: ~168 rows, not the 14 / one day the card used to show
        self.assertEqual(len(sel["rows"]), n)
        self.assertTrue(all(sel["rows"]) and all(sel["labels"]))
        dates = {lb[:5] for lb in sel["labels"]}
        self.assertEqual(len(dates), 7, sorted(dates))  # all seven days are in the list, not only the newest one

    def test_list_is_newest_first_by_default_and_the_default_selection_is_the_newest_hour(self):
        sel = self.sel
        n = len(sel["rows"])
        tops = [sel["rows"][k][1] for k in range(n)]
        self.assertEqual(tops, sorted(tops, reverse=True))  # k = oldest .. newest; the newest sits at the top (see test_log_sort for oldest first)
        self.assertEqual(tops[n - 1], 0.0)
        self.assertEqual(sel["i"], n - 1)
        self.assertLess(sel["labels"][0], sel["labels"][n - 1])  # 'MM-DD HH:MM' sorts by time within the week

    def test_rows_tile_the_strip_picture_exactly(self):
        sel = self.sel
        n = len(sel["rows"])
        img = Image.open(os.path.join(self.d, "w-hourly-rows.png"))
        self.assertEqual(img.mode, "RGBA")
        w, h = img.size
        lay = chart_hud.hourly_list_layout()
        self.assertAlmostEqual(h / n, lay["row_h"] * chart_hud.FIG_H, delta=0.5)
        self.assertAlmostEqual(w, lay["width"] * chart_hud.FIG_W, delta=1)
        self.assertAlmostEqual(sel["strip"]["px"][1], h, delta=1)
        heights = {round(r[3], 3) for r in sel["rows"]}
        self.assertEqual(len(heights), 1)
        self.assertAlmostEqual(sum(r[3] for r in sel["rows"]), 100.0, delta=0.01)
        a = np.asarray(img)[..., 3]
        for k in (0, n // 2, n - 1):  # text is drawn in each row's own band (old, middle, newest)
            r = sel["rows"][k]
            y0, y1 = int(r[1] / 100 * h) + 2, int((r[1] + r[3]) / 100 * h) - 2
            self.assertGreater((a[y0:y1] > 0).sum(), 300, f"row {k} has no text")

    def test_viewport_is_the_cards_row_area_with_room_for_the_scrollbar(self):
        x, y, w, hh = self.sel["strip"]["box"]
        lx, ly, lw, lh = chart_hud.DAY_LOG_CARD
        self.assertGreater(x, lx * 100)
        self.assertLess(x + w, (lx + lw) * 100 - 1.0)  # the page adds a 16 px scrollbar to the right of it
        self.assertAlmostEqual(hh, 14 * ((lh - 0.095) / 14) * 100, 2)  # 14 rows are visible at once

    def test_the_card_itself_no_longer_draws_a_capped_list(self):
        """The saved chart carries the title and column heads only; the sprites do not contain any row box either."""
        on = px(os.path.join(self.d, "w-hourly-on.png"))
        x, y, w, hh = self.sel["strip"]["box"]
        H, W = on.shape[:2]
        region = on[int(y / 100 * H):int((y + hh) / 100 * H), int(x / 100 * W):int((x + w) / 100 * W)]
        self.assertEqual(int((region[..., 3] > 0).sum()), 0)

    def test_plain_png_export_still_lists_the_newest_rows(self):
        out = os.path.join(self.d, "plain.png")
        meta = chart_hud.render(HOURLY_ROWS, out, "2026-01-01", {"lang": "en", "view": "hourly", "tz": "UTC", "samples": week_samples()})
        self.assertEqual(sorted(f for f in os.listdir(self.d) if f.startswith("plain")), ["plain.png"])
        self.assertEqual(sum(1 for r in meta["sel"]["rows"] if r), 14)  # a static picture cannot scroll

    def test_chart_and_mask_work_for_every_hour_of_the_week(self):
        sel = self.sel
        n = len(sel["cols"])
        W = chart_hud.FIG_W
        for k in range(n):
            self.assertGreaterEqual(sel["clips"][k][2] / 100 * W, 11.5, k)
        # the band of the oldest hour of day 1 and of the newest one are inside the plot, at both ends of the 7-day axis
        first, last = sel["clips"][0][0], sel["clips"][n - 1][0]
        self.assertTrue(first > last if sel.get("order") == "new" else first < last)  # newest first: the oldest hour is at the right
        ax_l, _b, ax_w, _h = chart_hud.INC_AXES
        for k in (0, n - 1):
            self.assertGreaterEqual(sel["clips"][k][0], ax_l * 100 - 0.01)
            self.assertLessEqual(sel["clips"][k][0] + sel["clips"][k][2], (ax_l + ax_w) * 100 + 0.01)

    def test_old_hour_selection_picture_has_the_mask_and_matches_the_sprites(self):
        n = len(self.sel["cols"])
        for k in (0, 30, n // 2):
            want = os.path.join(self.d, f"k{k}-hourly.png")
            chart_hud.render(HOURLY_ROWS, want, "2026-01-01", {"lang": "en", "view": "hourly", "tz": "UTC", "samples": week_samples(), "sel_idx": k, "sel_sprites": True})
            got, ref = composite(self.d, "hourly", self.meta, k), px(want)
            self.assertLess((np.abs(got - ref).max(axis=2) > 6).mean(), 0.0005, f"hour {k}")
            # and the band really is there: lighter than the same place without a selection
            plain = px(os.path.join(self.d, "w-hourly.png"))
            c = self.sel["clips"][k]
            H, Wd = ref.shape[:2]
            x0, x1 = int(c[0] / 100 * Wd), int((c[0] + c[2]) / 100 * Wd)
            y0, y1 = int((c[1] + c[3] * 0.05) / 100 * H), int((c[1] + c[3] * 0.20) / 100 * H)
            diff = (ref[y0:y1, x0 + 1:x1 - 1, :3].astype(float).mean(axis=2) - plain[y0:y1, x0 + 1:x1 - 1, :3].astype(float).mean(axis=2)).mean()
            self.assertGreater(diff, 3, f"hour {k}")


class PageTests(unittest.TestCase):
    def setUp(self):
        self.data = scratch()
        self.addCleanup(shutil.rmtree, self.data)

    def page(self, **kw):
        write_samples(self.data)
        d0 = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")  # the week began 3 days ago: today is day 4
        with open(os.path.join(self.data, f"week-{d0}.csv"), "w", encoding="utf-8") as fh:
            fh.write("day,date,pct,note\n1,%s,9,\n" % d0)
        uh.render_history(make_ctx(self.data, **kw))
        self.hist = os.path.join(self.data, "history")
        return read_text(os.path.join(self.hist, "index.html"))

    @staticmethod
    def wrap(page, v):
        """The markup of one view's wrapper: from its opening tag to the next view's."""
        return re.search(r"<div class='gwrap' data-wv='%s' hidden>(.*?)(?=data-wv=|$)" % v, page, re.S).group(1)

    def test_sprites_and_geometry_are_published(self):
        self.page()
        for f in uh.SEL_SPRITES:
            self.assertTrue(os.path.isfile(os.path.join(self.hist, f)), f)
        pal_dirs = [p for p in os.listdir(os.path.join(self.hist, "pal"))]
        self.assertTrue(pal_dirs)
        for pd in pal_dirs:
            for f in uh.SEL_SPRITES:
                self.assertTrue(os.path.isfile(os.path.join(self.hist, "pal", pd, f)), (pd, f))
        meta = json.loads(read_text(os.path.join(self.hist, "week-current.json")))
        self.assertEqual(meta["sel"]["daily"]["i"], 3)
        self.assertEqual(len(meta["sel"]["daily"]["cols"]), 7)
        self.assertEqual(meta["sel"]["hourly"]["i"], len(meta["sel"]["hourly"]["cols"]) - 1)

    def test_every_day_is_clickable_and_today_is_selected(self):
        page = self.page()
        daily = self.wrap(page, "daily")
        cols = re.findall(r"<div class='dsel dcol' data-i='(\d)'", daily)
        cards = re.findall(r"<button type='button' class='dsel dcard' data-i='(\d)' aria-pressed='(true|false)' aria-label='([^']*)'", daily)
        self.assertEqual(cols, [str(i) for i in range(7)])
        self.assertEqual([c[0] for c in cards], [str(i) for i in range(7)])  # day 5, 6, 7 (pending) included
        self.assertEqual([c[0] for c in cards if c[1] == "true"], ["3"])  # today = day 4
        self.assertEqual([c[2] for c in cards], [f"Day {i}" for i in range(1, 8)])
        self.assertIn("data-sel='3'", daily)
        for bad in ("disabled", "aria-disabled", "tabindex='-1'", "pointer-events:none;'"):
            self.assertNotIn(bad, re.sub(r"<img class='ssprite'[^>]*>", "", daily), bad)

    def test_hourly_list_defaults_to_its_newest_row(self):
        page = self.page()
        hourly = self.wrap(page, "hourly")
        cards = re.findall(r"class='dsel hrow' data-i='(\d+)' aria-pressed='(true|false)' aria-label='([^']*)'", hourly)
        n = len(re.findall(r"class='dsel dcol'", hourly))
        self.assertEqual(len(cards), n)  # every hour of the chart has a row in the scrolling list
        self.assertEqual([c[0] for c in cards if c[1] == "true"], [str(n - 1)])
        self.assertIn(f"data-sel='{n - 1}'", hourly)
        self.assertIn("→", [c[2] for c in cards if c[1] == "true"][0])
        self.assertIn("data-clip=", hourly)  # single hours get a wider band than their column

    def test_hourly_list_is_a_scrollable_viewport_with_every_row_and_a_visible_scrollbar(self):
        page = self.page()
        hourly = self.wrap(page, "hourly")
        n = len(re.findall(r"class='dsel dcol'", hourly))
        self.assertGreater(n, 40)  # three days of hourly readings: far more than the 14 the card shows at once
        self.assertEqual(len(re.findall(r"class='dsel hrow'", hourly)), n)
        self.assertEqual(len(re.findall(r"<div class='hscroll' style='[^']*width:calc\([\d.]+% \+ 16px\)", hourly)), 1)
        self.assertIn("data-f='week-hourly-rows.png' src='week-hourly-rows.png?v=", hourly)
        self.assertNotIn("dcard", hourly)
        for pal in os.listdir(os.path.join(self.hist, "pal")):
            self.assertTrue(os.path.isfile(os.path.join(self.hist, "pal", pal, uh.HOURLY_ROWS_FILE)), pal)  # follows palette switches
        self.assertTrue(os.path.isfile(os.path.join(self.hist, uh.HOURLY_ROWS_FILE)))
        # rows carry their own colour for the selection box
        self.assertRegex(hourly, r"class='dsel hrow' data-i='0' aria-pressed='false' aria-label='\d\d-\d\d [^']*' style='[^']*--c:#[0-9a-f]{6}'")
        wk = self.wrap(page, "daily")
        self.assertNotIn("hscroll", wk)  # the per-day view is unchanged

    def test_scrollbar_css_is_always_visible_wide_and_themed(self):
        css = uh.SEL_CSS
        self.assertIn("overflow-y:scroll", css)  # a track is always there, never auto-hidden
        for bad in ("overlay", "scrollbar-width:none", "scrollbar-width:thin", "::-webkit-scrollbar{display:none", "::-webkit-scrollbar-thumb{display:none"):
            self.assertNotIn(bad, css, bad)
        self.assertRegex(css, r"\.hscroll::-webkit-scrollbar\{width:(1[4-9]|[2-9]\d)px")  # wide enough to grab
        self.assertRegex(css, r"::-webkit-scrollbar-thumb\{background:var\(--accent\)")  # theme accent on a grey track
        self.assertIn("::-webkit-scrollbar-track", css)
        self.assertIn("scrollbar-color:var(--accent)", css)  # Firefox
        page = self.page()
        self.assertIn(css, page)

    def test_click_areas_do_not_collide_with_the_tab_handler(self):
        page = self.page()
        for v in ("daily", "hourly"):
            w = self.wrap(page, v)
            for tag in re.findall(r"<(?:div|button)[^>]*class='dsel[^>]*>", w):
                self.assertNotIn("data-go", tag)
                self.assertNotRegex(tag, r"class='[^']*\bhot\b")
        self.assertIn("usage.wkTab", page)
        self.assertIn("usage.incUnit", page)

    def test_selection_is_not_stored(self):
        for word in ("localStorage", "sessionStorage", "cookie", "history.", "location"):
            self.assertNotIn(word, uh.SEL_JS)
        page = self.page()
        self.assertEqual(page.count(uh.SEL_JS), 1)

    def test_no_new_strings(self):
        """Labels reuse the day label / time range already drawn; the areas carry no text; nothing was added to the tables."""
        html_ = uh.select_html({"cols": [[0, 0, 1, 1]], "rows": [[0, 0, 1, 1]], "i": 0}, lambda k: "x")[0]
        self.assertNotRegex(html_, r">[^<]+<")
        for lang in ["en"] + list(chart_hud.TRANSLATIONS):
            tab = chart_hud.BASE_STRINGS if lang == "en" else chart_hud.TRANSLATIONS[lang]
            self.assertFalse([k for k in tab if "select" in k.lower() or k.startswith("sel_")], lang)
        for word in ("textContent", "innerText", "innerHTML", "alert(", "title"):
            self.assertNotIn(word, uh.SEL_JS)  # the script writes no text anywhere

    def test_privacy_labels_are_symbols(self):
        page = self.page(privacy=True)
        labels = re.findall(r"class='dsel dcard' data-i='\d+' aria-pressed='\w+' aria-label='([^']*)'", self.wrap(page, "daily"))
        self.assertEqual(len(labels), 7)
        for t in labels:
            self.assertIsNone(re.search(r"[A-Za-z]{2,}", t), t)

    def test_overview_has_no_selection(self):
        page = self.page()
        self.assertIn("<div class='gwrap' data-wv='week'>", page)
        wk = re.search(r"<div class='gwrap' data-wv='week'>(.*?)(?=data-wv='daily')", page, re.S).group(1)
        self.assertNotIn("dsel", re.sub(r"base64,[A-Za-z0-9+/=]+", "", wk))  # the overview has no selection (not counting the embedded picture's base64)


STUB = r"""
function El(attrs, cls) { this.a = attrs || {}; this.cls = cls || []; this.style = {}; this.hidden = false; this.src = ''; this.parent = null;
  this.classList = {contains: function (c) { return this.cls.indexOf(c) >= 0; }.bind(this)}; }
El.prototype.getAttribute = function (k) { return k in this.a ? this.a[k] : null; };
El.prototype.setAttribute = function (k, v) { this.a[k] = String(v); };
El.prototype.closest = function (sel) { if (sel === '.hscroll') return this.scroller || null; var e = this; while (e && !('data-i' in e.a)) e = e.parent; return e; };
El.prototype.getBoundingClientRect = function () { return this.rect || {top: 0, bottom: 0}; };
var img = new El({'data-f': 'week-%(v)s.png', src: 'pal/orange-teal/week-%(v)s.png?v=7'});
var sp = [new El({'data-sel': '%(d0)s'}), new El(), new El()];
var areas = %(areas)s.map(function (x) { var e = new El({'data-i': String(x.i)}, [x.c]); e.style = x.s;
  if (x.clip) e.a['data-clip'] = x.clip; if (x.c === 'dcard') e.a['aria-pressed'] = x.i == %(d0)s ? 'true' : 'false'; return e; });
var wrap = new El({'data-wv': '%(v)s'}); wrap.hidden = %(hidden)s; var handlers = {}, events = [];
wrap.querySelector = function (s) { return s === 'img[data-f]' ? img : null; };
wrap.querySelectorAll = function (s) { return s === 'img.ssprite' ? sp : s === '[data-i]' ? areas : []; };
wrap.addEventListener = function (t, f) { events.push(t); handlers[t] = f; };
areas.forEach(function (e) { e.parent = wrap; });
var sec = new El(); sec.querySelector = function (s) { return s === "[data-wv='%(v)s']" ? wrap : null; };
global.document = {getElementById: function (id) { return id === 'p-wk' ? sec : null; }};
var loaded = [], observers = [];
global.Image = function () { var me = this; Object.defineProperty(this, 'src', {set: function (v) { loaded.push(v); if (me.onload) me.onload(); }}); };
global.window = {MutationObserver: function (cb) { observers.push(cb); this.observe = function () {}; }};
global.MutationObserver = window.MutationObserver;
%(js)s
function state() { return {hidden: sp.map(function (s) { return s.hidden; }), src: sp.map(function (s) { return s.src; }),
  clip: sp.map(function (s) { return s.style.clipPath || null; }),
  pressed: areas.filter(function (e) { return e.cls.indexOf('dcard') >= 0; }).map(function (e) { return e.a['aria-pressed']; })}; }
function click(i, c) { var t = areas.filter(function (e) { return e.a['data-i'] == String(i) && e.cls.indexOf(c) >= 0; })[0]; handlers.click({target: t}); }
var out = {events: events, start: state()};
%(steps)s
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ClickLogicTests(unittest.TestCase):
    AREAS = ([{"i": i, "c": "dcol", "s": {"left": f"{i * 14}%", "top": "10%", "width": "14%", "height": "50%"}} for i in range(7)]
             + [{"i": i, "c": "dcard", "s": {"left": "70%", "top": f"{10 + i * 8}%", "width": "25%", "height": "8%"}} for i in range(7)])

    def run_js(self, steps, v="daily", d0=4, hidden="false", areas=None):
        js = STUB % {"v": v, "d0": d0, "hidden": hidden, "areas": json.dumps(areas or self.AREAS), "js": uh.SEL_JS, "steps": steps}
        r = subprocess.run([shutil.which("node"), "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_default_shows_nothing_extra_and_only_click_is_wired(self):
        out = self.run_js("")
        self.assertEqual(out["events"], ["click"])  # no hover / focus / key handler: hover shows only the existing tooltips
        self.assertEqual(out["start"]["hidden"], [True, True, True])  # the saved picture already shows today
        self.assertEqual(out["start"]["pressed"], ["false"] * 4 + ["true"] + ["false"] * 2)

    def test_click_moves_mask_and_card_together_and_back(self):
        out = self.run_js("click(1,'dcol'); out.a = state(); click(4,'dcard'); out.b = state();")
        a = out["a"]
        self.assertEqual(a["hidden"], [False, False, False])
        self.assertTrue(a["src"][0].endswith("pal/orange-teal/week-daily-off.png?v=7"))
        self.assertTrue(a["src"][1].endswith("week-daily-on.png?v=7") and a["src"][2].endswith("week-daily-on.png?v=7"))
        self.assertEqual(a["clip"][1], "inset(10% 72% 40% 14%)")  # the column
        self.assertEqual(a["clip"][2], "inset(18% 5% 74% 70%)")   # the same day's card
        self.assertEqual(a["pressed"], ["false", "true"] + ["false"] * 5)
        self.assertEqual(out["b"]["hidden"], [True, True, True])  # back on the default: the saved picture as is
        self.assertEqual(out["b"]["pressed"][4], "true")

    def test_a_card_click_selects_the_same_day(self):
        out = self.run_js("click(2,'dcard'); out.a = state();")
        self.assertEqual(out["a"]["clip"][1], "inset(10% 58% 40% 28%)")
        self.assertEqual(out["a"]["pressed"][2], "true")

    def test_pending_days_are_selectable_like_any_other(self):
        out = self.run_js("click(5,'dcol'); out.a = state(); click(6,'dcard'); out.b = state();")
        for k in ("a", "b"):
            self.assertEqual(out[k]["hidden"], [False, False, False], k)
        self.assertEqual(out["a"]["pressed"][5], "true")
        self.assertEqual(out["b"]["pressed"][6], "true")
        self.assertEqual(out["b"]["clip"][1], "inset(10% 2% 40% 84%)")

    def test_hourly_band_uses_the_wider_clip(self):
        areas = ([{"i": i, "c": "dcol", "s": {"left": f"{i}%", "top": "10%", "width": "0.3%", "height": "50%"}, "clip": f"{i - 0.2},10,0.7,50"} for i in range(1, 6)]
                 + [{"i": i, "c": "dcard", "s": {"left": "70%", "top": f"{10 + (5 - i) * 8}%", "width": "25%", "height": "8%"}} for i in range(1, 6)])
        out = self.run_js("click(2,'dcol'); out.a = state();", v="hourly", d0=5, areas=areas)
        self.assertEqual(out["a"]["clip"][1], "inset(10% 97.5% 40% 1.8%)")
        self.assertEqual(out["a"]["clip"][2], "inset(34% 5% 58% 70%)")
        self.assertTrue(out["a"]["src"][0].endswith("week-hourly-off.png?v=7"))

    def test_hourly_bar_click_scrolls_its_list_row_into_view_and_row_click_keeps_it(self):
        """A bar of an hour outside the list's viewport scrolls the list to that row (both ways); the 7-day axis is not limited."""
        areas = ([{"i": i, "c": "dcol", "s": {"left": f"{i}%", "top": "10%", "width": "0.3%", "height": "50%"}} for i in range(30)]
                 + [{"i": i, "c": "hrow", "s": {"left": "0%", "top": f"{(29 - i) * 3}%", "width": "100%", "height": "3%"}} for i in range(30)])
        steps = ("var sc = {scrollTop: 0, getBoundingClientRect: function () { return {top: 100, bottom: 200}; }};"  # a 100 px viewport, 10 px rows
                 "areas.forEach(function (e) { if (e.cls.indexOf('hrow') >= 0) { e.scroller = sc; (function (e) { Object.defineProperty(e, 'rect', {get: function () {"
                 " var t = 100 + (29 - e.a['data-i']) * 10 - sc.scrollTop; return {top: t, bottom: t + 10}; }}); })(e); } });"
                 "click(2,'dcol'); out.a = {s: sc.scrollTop, hidden: state().hidden}; click(29,'hrow'); out.b = sc.scrollTop;"
                 "click(15,'hrow'); out.c = sc.scrollTop; click(0,'dcol'); out.d = sc.scrollTop;")
        out = self.run_js(steps, v="hourly", d0=29, areas=areas)
        self.assertEqual(out["a"]["s"], 180)  # hour 2 is the 28th row (y 370..380): scrolled just far enough to end at the viewport's bottom
        self.assertEqual(out["a"]["hidden"], [False, False, True])  # the band is on that old hour (the list highlights its row itself, no row sprite)
        self.assertEqual(out["b"], 0)  # back to the newest row: scrolled to the top
        self.assertEqual(out["c"], 50)  # a half-hidden row is brought fully into view
        self.assertEqual(out["d"], 200)  # the oldest hour of the week: the list scrolled to its last row

    def test_palette_switch_reloads_the_sprites_and_hidden_view_loads_nothing(self):
        out = self.run_js("click(1,'dcol'); img.a.src = 'pal/orange-deepblue/week-daily.png?v=9'; img.getAttribute = function (k) { return k === 'src' ? 'pal/orange-deepblue/week-daily.png?v=9' : this.a[k] || null; };"
                          "observers[0](); out.a = state();")
        self.assertTrue(out["a"]["src"][0].endswith("pal/orange-deepblue/week-daily-off.png?v=9"))
        self.assertTrue(out["a"]["src"][1].endswith("pal/orange-deepblue/week-daily-on.png?v=9"))
        hid = self.run_js("out.n = loaded.length;", hidden="true")
        self.assertEqual(hid["n"], 0)  # nothing is fetched for the view that is not shown


if __name__ == "__main__":
    unittest.main()
