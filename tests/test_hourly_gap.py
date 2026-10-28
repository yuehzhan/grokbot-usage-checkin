"""Hourly view: a reading gap is drawn as a low neutral band at its average per hour (no tall over/under-coloured, dotted box)."""
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import matplotlib.axes
from matplotlib.patches import Rectangle
from matplotlib.colors import to_rgb

from _helpers import scratch

import chart_hud

ROWS = [{"day": "1", "date": "2026-01-01", "pct": "18", "note": "10:00"}, {"day": "2", "date": "2026-01-02", "pct": "41", "note": "10:00"}]


def samples():
    """14 hourly readings (+1%/h), then nothing for 22.1 h and +6%, then 4 more hourly readings: one reading gap."""
    t0 = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    out = [(t0 + timedelta(hours=h), 10.0 + h) for h in range(14)]
    t_gap = out[-1][0] + timedelta(hours=22, minutes=6)
    out += [(t_gap + timedelta(hours=h), 29.0 + h) for h in range(4)]
    return out


def gap_band(made):
    """The reading gap's bar: the one wide bar on the 168-hour axis."""
    for ax, p in made:
        if isinstance(p, Rectangle) and p.get_zorder() == 3 and ax.get_xlim()[1] == 168.0 and p.get_width() > 5:
            return ax, p
    raise AssertionError("no gap band drawn")


def render(view="hourly", **cfg):
    made = []
    real = matplotlib.axes.Axes.add_patch

    def spy(self, p):
        made.append((self, p))
        return real(self, p)
    d = scratch()
    try:
        with mock.patch.object(matplotlib.axes.Axes, "add_patch", spy):
            meta = chart_hud.render(ROWS, os.path.join(d, "r.png"), "2026-01-01",
                                    dict({"lang": "en", "view": view, "tz": "UTC", "samples": samples()}, **cfg))
    finally:
        shutil.rmtree(d)
    return made, meta


class GapBarTests(unittest.TestCase):
    def test_height_is_the_average_per_hour(self):
        hb = chart_hud.hourly_increments(samples(), datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 8, tzinfo=timezone.utc), timezone.utc, 0)
        gaps = [r for r in hb["rows"] if r["gap"]]
        self.assertEqual(len(gaps), 1)
        g = gaps[0]
        self.assertAlmostEqual(g["inc"], 6.0, 1)
        self.assertAlmostEqual(chart_hud.gap_bar_height(g), g["inc"] / g["span_h"], 6)
        self.assertLess(chart_hud.gap_bar_height(g), 0.5)
        ok = next(r for r in hb["rows"] if not r["gap"])
        self.assertEqual(chart_hud.gap_bar_height(ok), ok["inc"])

    def test_gap_patch_is_neutral_low_and_has_no_dots(self):
        made, _ = render()
        ax, band = gap_band(made)
        self.assertFalse(band.get_hatch())  # no dense '..' dots
        self.assertLess(band.get_height(), 0.5)  # not the whole 6% span
        self.assertLess(ax.get_ylim()[1], 6.0)  # the scale is not inflated by the gap's total
        self.assertLess(band.get_alpha() or 1, 1)
        edge = band.get_edgecolor()
        self.assertEqual(edge[3] if len(edge) > 3 else 0, 0)  # no edge outside pattern mode

    def test_gap_is_not_an_over_or_under_colour(self):
        made, _ = render()
        _, band = gap_band(made)
        face = band.get_facecolor()[:3]
        for pal in ("orange-teal", "orange-deepblue"):
            import usage_palette
            c = usage_palette.get(pal, False)
            for k in ("over", "under"):
                self.assertNotEqual(to_rgb(c[k]), tuple(round(v, 3) for v in face))
        # grey: its channels are close together (the dark card tint is slightly bluish at most)
        self.assertLess(max(face) - min(face), 0.12)

    def test_pattern_mode_uses_a_light_dashed_edge_not_dots(self):
        made, _ = render(patterns=True)
        _, band = gap_band(made)
        self.assertFalse(band.get_hatch())
        self.assertGreater(band.get_linewidth(), 0)

    def test_label_and_legend_keep_their_text(self):
        made, _ = render()
        ax, _band = gap_band(made)
        texts = [t.get_text() for t in ax.texts]
        self.assertTrue(any("+6.0%" in t and "22.1" in t for t in texts), texts)  # '+6.0% (over 22.1 h)'-style label stays
        leg = [t.get_text() for t in ax.get_legend().get_texts()]
        self.assertIn(str(chart_hud.BASE_STRINGS["hourly_gap_leg"]), leg)
        sw = next(h for h in ax.get_legend().legend_handles if h.get_label() == str(chart_hud.BASE_STRINGS["hourly_gap_leg"]))
        self.assertFalse(sw.get_hatch())


if __name__ == "__main__":
    unittest.main()
