"""Mid-week reset (a plan upgrade resets the weekly pool to ~0% while the week window stays): the average, the
end-of-week projection, the baseline and the chart's dashed projection count from the reset, not the week start.

Data from the real case: week 10-24 01:41 -> 10-31 01:41 (UTC+8), pace 14%/day; 10-25 01:00 = 75%,
10-25 05:00 = 84%, Premium -> Super upgrade ~17:06, 10-26 03:05 = 6%."""
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from _helpers import scratch
from test_gauge_anim import spy_render

import chart_hud
import grok_usage_reader
import usage_rate
import usage_record as ur

TPE = ZoneInfo("Asia/Taipei")
START = datetime(2026, 10, 24, 1, 41, tzinfo=TPE)
END = datetime(2026, 10, 31, 1, 41, tzinfo=TPE)
ROWS = [{"day": "1", "date": "2026-10-25", "pct": "75", "note": "01:00"},
        {"day": "2", "date": "2026-10-25", "pct": "84", "note": "05:00"},
        {"day": "3", "date": "2026-10-26", "pct": "6", "note": "03:05"}]
PCTS = [75.0, 84.0, 6.0]
# reset estimated halfway between the last reading before the drop and the first one after it
RESET_AT = datetime(2026, 10, 25, 16, 2, 30, tzinfo=TPE)
NOW = datetime(2026, 10, 26, 3, 5, tzinfo=TPE)
ELAPSED = (NOW - RESET_AT).total_seconds() / 86400.0  # ~0.46 d (11 h 02 m)
LEFT = (END - NOW).total_seconds() / 86400.0  # ~4.94 d
AVG = 6.0 / ELAPSED  # ~13.0 %/day (pool restarted at 0% at the reset)
PROJ = 6.0 + AVG * LEFT  # ~70.4 %
CFG = {"pace": 14, "tz": "Asia/Taipei", "reset_hm": "01:41", "reset_at": "2026-10-31T01:41+08:00"}


def render_week(rows, out, cfg):
    real = chart_hud.render
    with mock.patch.object(chart_hud, "render", lambda r, o, ws, c: real(r, o, "2026-10-24", c)):
        return spy_render(rows, out, cfg)


class ModelTests(unittest.TestCase):
    def test_detects_reset_and_midpoint(self):
        r = usage_rate.find_midweek_reset(ROWS, PCTS, START)
        self.assertEqual(r["index"], 2)
        self.assertEqual(r["at"], RESET_AT)
        self.assertEqual((r["before_pct"], r["after_pct"]), (84.0, 6.0))

    def test_rate_since_reset(self):
        m = usage_rate.rate_model(ROWS, PCTS, START, END, 14)
        self.assertAlmostEqual(m["elapsed_days"], ELAPSED)
        self.assertAlmostEqual(m["rem_days"], LEFT)
        self.assertAlmostEqual(m["avg_day"], AVG)
        self.assertAlmostEqual(round(m["avg_day"], 1), 13.0)
        self.assertAlmostEqual(m["proj"], PROJ)
        self.assertEqual(round(m["proj"]), 70)  # not the old 6% / 3 days x 7 = 14%
        self.assertAlmostEqual(m["baseline"], 14 * ELAPSED)  # ~6.4%, not 3 x 14 = 42%
        ro = chart_hud.run_out(3, 6.0, m)
        self.assertAlmostEqual(ro["avg_day"], AVG)
        self.assertAlmostEqual(ro["proj_7d"], PROJ)
        self.assertFalse(ro["exhausts"])

    def test_hourly_samples_narrow_the_reset_time(self):
        # hourly readings inside the row gap: 93% at 17:00, 1% at 17:06 -> midpoint 17:03 instead of 16:02
        samples = [(datetime(2026, 10, 25, 9, 0, tzinfo=TPE), 88.0), (datetime(2026, 10, 25, 12, 0, tzinfo=TPE), None),
                   (datetime(2026, 10, 25, 17, 0, tzinfo=TPE), 93.0), (datetime(2026, 10, 25, 17, 6, tzinfo=TPE), 1.0),
                   (datetime(2026, 10, 25, 22, 0, tzinfo=TPE), 3.0), (datetime(2026, 10, 27, 0, 0, tzinfo=TPE), 50.0)]
        at = datetime(2026, 10, 25, 17, 3, tzinfo=TPE)
        self.assertEqual(usage_rate.find_midweek_reset(ROWS, PCTS, START, samples=samples)["at"], at)
        m = usage_rate.rate_model(ROWS, PCTS, START, END, 14, samples=samples)
        el = (NOW - at).total_seconds() / 86400.0
        self.assertAlmostEqual(m["avg_day"], 6.0 / el)  # ~14.4 %/day
        self.assertEqual(round(m["proj"]), 77)
        # only a pre-drop sample (88% at 09:00) in the gap: the window shrinks to 09:00 -> 03:05, midpoint 18:02:30
        self.assertEqual(usage_rate.find_midweek_reset(ROWS, PCTS, START, samples=samples[:2])["at"],
                         datetime(2026, 10, 25, 18, 2, 30, tzinfo=TPE))
        # unusable readings only: the row midpoint stays
        self.assertEqual(usage_rate.find_midweek_reset(ROWS, PCTS, START, samples=samples[1:2])["at"], RESET_AT)
        naive = [(datetime(2026, 10, 25, 17, 0), 93.0)]
        self.assertEqual(usage_rate.find_midweek_reset(ROWS, PCTS, START, samples=naive)["at"], RESET_AT)

    def test_first_reading_after_reset_above_zero_still_detected(self):
        # detection is by the size of the drop, not by the value reaching 0
        self.assertIsNotNone(usage_rate.find_midweek_reset(ROWS, [75, 84, 30], START))
        self.assertIsNone(usage_rate.find_midweek_reset(ROWS, [75, 84, 80], START))  # small dip: a correction, not a reset

    def test_normal_week_unchanged(self):
        rows = [{"day": "1", "date": "2026-10-24", "pct": "18", "note": "12:00"},
                {"day": "3", "date": "2026-10-26", "pct": "62", "note": "12:00"}]
        m = usage_rate.rate_model(rows, [18.0, 62.0], START, END, 14)
        self.assertIsNone(m["reset"])
        self.assertAlmostEqual(m["avg_day"], 62 / 3)
        self.assertAlmostEqual(m["proj"], 62 / 3 * 7)
        self.assertEqual(m["baseline"], 42)
        self.assertEqual(chart_hud.run_out(3, 62.0, m), chart_hud.run_out(3, 62.0))

    def test_exhausts_after_reset(self):
        rows = ROWS[:2] + [{"day": "3", "date": "2026-10-26", "pct": "40", "note": "03:05"}]
        m = usage_rate.rate_model(rows, [75.0, 84.0, 40.0], START, END, 14)
        ro = chart_hud.run_out(3, 40.0, m)
        self.assertTrue(ro["exhausts"])
        self.assertAlmostEqual(ro["left"], 60.0 / m["avg_day"])
        self.assertAlmostEqual(ro["cross_x"], 2.5 + ro["left"])

    def test_row_time_fallback(self):
        self.assertEqual(usage_rate.row_time({"day": "2", "date": "2026-10-25", "note": ""}, START),
                         START + timedelta(days=1.5))
        self.assertEqual(usage_rate.row_time({"day": "2", "date": "2026-10-25", "note": "03:17 Grok Build"}, START),
                         datetime(2026, 10, 25, 3, 17, tzinfo=TPE))


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.d = scratch()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_cards_count_from_reset(self):
        for lang, label, vs in (("zh-TW", "週中重置後", "與基準相同"), ("en", "since mid-week reset", "On baseline")):
            seen, _ = render_week(ROWS, os.path.join(self.d, f"{lang}.png"), dict(CFG, lang=lang))
            val = {g: t for t, g in seen if g}
            self.assertEqual(val["stat_val_2"], "13%", lang)  # avg/day, was 2%
            self.assertEqual(val["stat_val_3"], "70%", lang)  # projection, was 14%
            self.assertEqual(val["stat_val_0"], vs, lang)  # vs baseline: ~6.4% since the reset, was 「落後基準 36%」
            self.assertIn(label, val["stat_sub_2"], lang)
            self.assertIn(label, val["stat_sub_3"], lang)
            self.assertIn("6.4%", val["stat_sub_0"], lang)
            self.assertEqual(val["stat_val_5"], "18.8%/d", lang)  # budget card unchanged
            self.assertEqual(val["log_inc_3"], "+6%", lang)  # day's increase from the ~0% restart, not -78%

    def test_hourly_samples_reach_the_cards(self):
        samples = [(datetime(2026, 10, 25, 17, 0, tzinfo=TPE), 93.0), (datetime(2026, 10, 25, 17, 6, tzinfo=TPE), 1.0)]
        seen, _ = render_week(ROWS, os.path.join(self.d, "s.png"), dict(CFG, lang="en", samples=samples))
        val = {g: t for t, g in seen if g}
        self.assertEqual(val["stat_val_2"], "14.4%")  # reset at ~17:03 instead of the 16:02 row midpoint
        self.assertEqual(val["stat_val_3"], "77%")

    def test_projection_line_starts_at_current_point(self):
        import matplotlib.axes
        lines, real = [], matplotlib.axes.Axes.plot

        def spy(self, *a, **k):
            r = real(self, *a, **k)
            if k.get("gid") == "proj_line":
                lines.append(r[0])
            return r
        with mock.patch.object(matplotlib.axes.Axes, "plot", spy):
            render_week(ROWS, os.path.join(self.d, "c.png"), dict(CFG, lang="zh-TW"))
        self.assertEqual(len(lines), 1)
        xd, yd = lines[0].get_xdata(), lines[0].get_ydata()
        self.assertAlmostEqual(xd[0], 2.5)
        self.assertAlmostEqual(yd[0], 6.0)
        self.assertAlmostEqual(yd[-1], PROJ)
        self.assertAlmostEqual(xd[-1], 6.5)  # ends at the day-7 point like in a normal week

    def test_normal_week_cards_unchanged(self):
        rows = [{"day": "1", "date": "2026-10-24", "pct": "18", "note": "12:00"},
                {"day": "2", "date": "2026-10-25", "pct": "41", "note": "12:00"},
                {"day": "3", "date": "2026-10-26", "pct": "62", "note": "12:00"}]
        seen, _ = render_week(rows, os.path.join(self.d, "n.png"), dict(CFG, lang="zh-TW"))
        val = {g: t for t, g in seen if g}
        self.assertEqual(val["stat_val_2"], "20.7%")
        self.assertNotIn("週中重置後", " ".join(t for t, _ in seen))
        self.assertIn("第3天基準: 42%", val["stat_sub_0"])


class RecordSummaryTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(scratch())

    def tearDown(self):
        shutil.rmtree(self.d)

    def _record(self, pct, date, time):
        cfg = self.d / "cfg.json"
        cfg.write_text(json.dumps({"pace": 14}), encoding="utf-8")
        argv = ["--pct", str(pct), "--reset-source", "auto", "--config", str(cfg), "--data-dir", str(self.d),
                "--tz", "Asia/Taipei", "--no-open", "--no-render", "--format", "json", "--date", date, "--time", time]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(grok_usage_reader, "read_usage", return_value={"pct": float(pct), "reset_at": END}), \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                ur.main(argv)
        self.assertEqual(cm.exception.code, 0, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_summary_after_reset(self):
        self._record(75, "2026-10-25", "01:00")
        p = self._record(84, "2026-10-25", "05:00")
        self.assertNotIn("midweek_reset", p)
        self.assertEqual(p["avg_per_day"], 42.0)  # normal week: 84 / day 2
        p = self._record(6, "2026-10-26", "03:05")
        self.assertEqual(p["day"], 3)
        self.assertAlmostEqual(p["avg_per_day"], round(AVG, 2))
        self.assertAlmostEqual(p["projected_end_pct"], round(PROJ, 1))
        self.assertAlmostEqual(p["baseline"], round(14 * ELAPSED, 4))
        self.assertEqual(p["status"], "on_pace")
        self.assertEqual(p["midweek_reset"]["at"], "2026-10-25T16:02+08:00")
        self.assertIn("since mid-week reset", p["summary_text"])


if __name__ == "__main__":
    unittest.main()
