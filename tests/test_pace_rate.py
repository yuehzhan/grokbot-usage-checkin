"""The pace behind the run-out label / dashed projection of the weekly cumulative chart (usage_rate.blend_rate):
hours since the reset (not whole days), recent 24 h blended in, graceful fallbacks, clamped, and one rate everywhere."""
import os
import shutil
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from _helpers import fixed_datetime, scratch
from test_gauge_anim import spy_render

import chart_hud
import usage_rate

TPE = ZoneInfo("Asia/Taipei")
START = datetime(2026, 10, 30, 9, 0, tzinfo=TPE)  # week start (reset time)
END = START + timedelta(days=7)
# The case from the report: 22% / 46% on days 1-2, day 3 still in progress at +8% (54%).
ROWS = [{"day": "1", "date": "2026-10-30", "pct": "22", "note": "21:00"},
        {"day": "2", "date": "2026-10-31", "pct": "46", "note": "21:00"},
        {"day": "3", "date": "2026-11-01", "pct": "54", "note": "15:00"}]
PCTS = [22.0, 46.0, 54.0]
NOW = datetime(2026, 11, 1, 15, 0, tzinfo=TPE)  # 54 h after the start = 2.25 days, not 3 days


def hourly_samples():
    """Real-looking hourly readings: +22 in the first 12 h, idle night, +24 in the next 12 h, +8 in 6 h on day 3."""
    out = []
    for h in range(0, 55):
        d, r = divmod(h, 24)
        if d == 0:
            v = 22 * min(1.0, r / 12.0)
        elif d == 1:
            v = 22 + 24 * min(1.0, r / 12.0)
        else:
            v = 46 + 8 * min(1.0, r / 6.0)
        out.append((START + timedelta(hours=h), round(v, 2)))
    return out


def pts(*pairs):
    return [(START + timedelta(hours=h), v) for h, v in pairs]


class BlendRateTests(unittest.TestCase):
    def test_hours_not_whole_days(self):
        # 54% after 54 h = 24 %/day; the day-index model said 54 / 3 = 18 %/day
        r = usage_rate.blend_rate([], START + timedelta(hours=54), 54.0, START)
        self.assertAlmostEqual(r["wtd"], 24.0)
        self.assertAlmostEqual(r["rate"], 24.0)
        self.assertIsNone(r["recent"])

    def test_few_readings_fall_back_to_week_to_date(self):
        t = START + timedelta(hours=54)
        for few in ([], pts((0, 0.0), (53, 53.0)), pts((30, 30.0), (53, 53.0))):  # fewer than three readings
            r = usage_rate.blend_rate(few, t, 54.0, START)
            self.assertEqual((r["rate"], r["weight"], r["recent"]), (r["wtd"], 0.0, None), few)
        # readings that all sit within the last hour span too little to say anything about the pace
        r = usage_rate.blend_rate(pts((52.0, 50.0), (52.5, 52.0), (53.5, 53.0)), t, 54.0, START)
        self.assertIsNone(r["recent"])

    def test_recent_pace_is_weighted_in(self):
        t = START + timedelta(hours=54)
        # last 24 h: 30% -> 54% = 24 %/day; steady week: 24 %/day -> unchanged
        r = usage_rate.blend_rate(pts((0, 0.0), (30, 30.0), (42, 42.0), (54, 54.0)), t, 54.0, START)
        self.assertAlmostEqual(r["rate"], 24.0)
        self.assertAlmostEqual(r["weight"], 0.5)
        # accelerating: only 6% in the first 30 h, then 48% in the last 24 h = 48 %/day recent, 24 %/day to date
        r = usage_rate.blend_rate(pts((0, 0.0), (30, 6.0), (42, 30.0), (54, 54.0)), t, 54.0, START)
        self.assertAlmostEqual(r["recent"], 48.0)
        self.assertAlmostEqual(r["rate"], 0.5 * 48.0 + 0.5 * 24.0)  # 36: faster than the plain average
        # slowing down: idle for 24 h -> pulled down, but clamped at half the week-to-date rate
        r = usage_rate.blend_rate(pts((0, 0.0), (30, 54.0), (42, 54.0), (54, 54.0)), t, 54.0, START)
        self.assertAlmostEqual(r["recent"], 0.0)
        self.assertAlmostEqual(r["rate"], 0.5 * r["wtd"])  # 0.5 * 0 + 0.5 * 24 = 12 = the lower clamp exactly

    def test_clamped_between_half_and_double_the_week_to_date_rate(self):
        t = START + timedelta(hours=54)
        r = usage_rate.blend_rate(pts((0, 0.0), (30, 0.0), (42, 0.0), (54, 54.0)), t, 54.0, START)  # a one-off jump
        self.assertLessEqual(r["rate"], 2.0 * r["wtd"])
        self.assertGreaterEqual(r["rate"], 0.5 * r["wtd"])
        lo, hi = usage_rate.RATE_CLAMP
        self.assertEqual((lo, hi), (0.5, 2.0))

    def test_partial_window_coverage_lowers_the_recent_weight(self):
        t = START + timedelta(hours=14)  # only 14 h into the week: the window is not fully covered
        r = usage_rate.blend_rate(pts((0, 0.0), (6, 10.0), (10, 20.0), (14, 30.0)), t, 30.0, START)
        self.assertAlmostEqual(r["weight"], 0.5 * 14 / 24)

    def test_early_reading_does_not_blow_up(self):
        # 3% one hour after the reset: not 72 %/day (the 12 h floor), and bounded
        r = usage_rate.blend_rate(pts((0, 0.0), (1, 3.0)), START + timedelta(hours=1), 3.0, START)
        self.assertAlmostEqual(r["rate"], 6.0)

    def test_ignores_none_future_and_mixed_timezone_points(self):
        t = START + timedelta(hours=54)
        junk = pts((10, None), (60, 99.0)) + [(datetime(2026, 11, 1, 1, 0), 40.0)]  # None, after now, naive
        a = usage_rate.blend_rate(junk + pts((0, 0.0), (30, 30.0), (42, 42.0), (54, 54.0)), t, 54.0, START)
        self.assertAlmostEqual(a["rate"], 24.0)  # no exception, junk did not change a steady 24 %/day


class RateModelTests(unittest.TestCase):
    def test_example_rows_only(self):
        m = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3)
        self.assertAlmostEqual(m["avg_day"], 18.0)  # the plain average stays what it was
        self.assertAlmostEqual(m["proj"], 126.0)
        self.assertAlmostEqual(m["rate_info"]["elapsed_h"], 54.0)
        self.assertAlmostEqual(m["rate_info"]["wtd"], 24.0)
        self.assertGreater(m["rate_day"], 20.0)
        old_left = (100 - 54) / 18.0
        new_left = (100 - 54) / m["rate_day"]
        self.assertAlmostEqual(old_left, 2.56, places=2)
        self.assertLess(new_left, old_left - 0.3)  # clearly sooner than ~2.6 days
        self.assertAlmostEqual(m["proj_rate"], 54.0 + m["rate_day"] * (END - NOW).total_seconds() / 86400.0)

    def test_example_with_hourly_samples(self):
        m = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=hourly_samples())
        self.assertAlmostEqual(m["rate_info"]["recent"], 20.0)  # 34 -> 54 ... measured over the last 24 h
        self.assertAlmostEqual(m["rate_day"], 0.5 * 20.0 + 0.5 * 24.0)
        self.assertAlmostEqual((100 - 54) / m["rate_day"], 46 / 22.0)  # ~2.09 d instead of ~2.56 d

    def test_rows_without_a_reading_time_keep_the_plain_model(self):
        rows = [dict(r, note="") for r in ROWS]
        m = usage_rate.rate_model(rows, PCTS, START, END, 14, latest_day=3)
        self.assertNotIn("rate_day", m)
        ro = chart_hud.run_out(3, 54.0, m)
        self.assertEqual((ro["avg_day"], ro["rate_day"], ro["proj_7d"]), (18.0, 18.0, 126.0))

    def test_no_week_start_or_rows_is_safe(self):
        self.assertNotIn("rate_day", usage_rate.rate_model([], [], None, None, 14))
        self.assertNotIn("rate_day", usage_rate.rate_model(ROWS, PCTS, None, None, 14, latest_day=3))


class RunOutSyncTests(unittest.TestCase):
    def test_label_line_and_projection_share_one_rate(self):
        m = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=hourly_samples())
        ro = chart_hud.run_out(3, 54.0, m)
        self.assertEqual(ro["rate_day"], m["rate_day"])
        self.assertAlmostEqual(ro["left"], 46.0 / m["rate_day"])
        self.assertAlmostEqual(ro["cross_x"], 2.5 + ro["left"])
        self.assertAlmostEqual(ro["proj_7d"], m["proj_rate"])
        self.assertTrue(ro["exhausts"])
        # the dashed line from (2.5, 54%) to (cross_x, 100%) has exactly the rate's slope
        self.assertAlmostEqual((100.0 - 54.0) / (ro["cross_x"] - 2.5), m["rate_day"])

    def test_rendered_chart_uses_the_blended_rate(self):
        import matplotlib.axes
        lines, real = [], matplotlib.axes.Axes.plot

        def spy(self, *a, **k):
            r = real(self, *a, **k)
            if k.get("gid") == "proj_line":
                lines.append(r[0])
            return r
        d = scratch()
        cfg = {"pace": 14, "tz": "Asia/Taipei", "reset_hm": "09:00", "reset_at": END.isoformat(timespec="minutes"),
               "lang": "en", "samples": hourly_samples()}
        real_render = chart_hud.render
        try:
            with mock.patch.object(chart_hud, "render", lambda r, o, ws, c: real_render(r, o, "2026-10-30", c)), \
                    mock.patch.object(chart_hud, "datetime", fixed_datetime(NOW)), \
                    mock.patch.object(matplotlib.axes.Axes, "plot", spy):
                seen, meta = spy_render(ROWS, os.path.join(d, "r.png"), cfg)
        finally:
            shutil.rmtree(d)
        rate = 0.5 * 20.0 + 0.5 * 24.0
        self.assertAlmostEqual(meta["card_left_days"], 46.0 / rate)
        self.assertAlmostEqual(meta["proj_cross_x"], 2.5 + 46.0 / rate)
        self.assertEqual(len(lines), 1)
        xd, yd = lines[0].get_xdata(), lines[0].get_ydata()
        self.assertAlmostEqual((yd[-1] - yd[0]) / (xd[-1] - xd[0]), rate)
        texts = [t for t, _ in seen]
        self.assertIn("Runs out early, ~2.1 days left", texts)  # was ~2.6 days with the plain average
        self.assertIn("~2.1d left", texts)
        self.assertIn("burn rate 22%/day", texts)  # the pace the projection uses
        self.assertIn("18%", texts)  # the Avg / Day card keeps the plain average


class UnifiedProjectionTests(unittest.TestCase):
    """usage_record's summary, the page's cards and the chart show one projection."""

    def test_proj_k_is_how_a_display_offset_moves_the_projection(self):
        # the page adds a display offset (sh points) to every cumulative %: proj moves by sh * proj_k
        sh = 2.0
        m = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=hourly_samples())
        shifted_rows = [dict(r) for r in ROWS]
        m2 = usage_rate.rate_model(shifted_rows, [p + sh for p in PCTS], START, END, 14, latest_day=3,
                                   samples=[(t, v + sh) for t, v in hourly_samples()])
        self.assertAlmostEqual(m2["proj_rate"] - m["proj_rate"], sh * m["rate_info"]["proj_k"], places=2)

    def test_summary_projection_equals_the_chart_projection(self):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout
        from pathlib import Path

        import grok_usage_reader
        import usage_record as ur
        d = Path(scratch())
        try:
            cfg = d / "cfg.json"
            cfg.write_text(json.dumps({"pace": 14}), encoding="utf-8")
            reset = datetime(2026, 10, 31, 1, 41, tzinfo=TPE)
            last = None
            for pct, date, tm in ((22, "2026-10-24", "21:00"), (46, "2026-10-25", "21:00"), (54, "2026-10-26", "15:00")):
                argv = ["--pct", str(pct), "--reset-source", "auto", "--config", str(cfg), "--data-dir", str(d),
                        "--tz", "Asia/Taipei", "--no-open", "--no-render", "--format", "json", "--date", date, "--time", tm]
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(grok_usage_reader, "read_usage", return_value={"pct": float(pct), "reset_at": reset}), \
                        redirect_stdout(out), redirect_stderr(err):
                    with self.assertRaises(SystemExit) as cm:
                        ur.main(argv)
                self.assertEqual(cm.exception.code, 0, err.getvalue())
                last = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(last["day"], 3)
            self.assertEqual(last["avg_per_day"], 18.0)  # the Avg / Day card keeps the plain average
            start = reset - timedelta(days=7)
            m = usage_rate.rate_model(
                [{"day": "1", "date": "2026-10-24", "note": "21:00"}, {"day": "2", "date": "2026-10-25", "note": "21:00"},
                 {"day": "3", "date": "2026-10-26", "note": "15:00"}], PCTS, start, reset, 14, latest_day=3)
            self.assertAlmostEqual(last["projected_end_pct"], round(m["proj_rate"], 1))
            self.assertNotAlmostEqual(last["projected_end_pct"], 126.0, places=0)  # not the old 54 / 3 x 7
            self.assertAlmostEqual(last["proj_k"], round(m["rate_info"]["proj_k"], 4))
            self.assertAlmostEqual(chart_hud.run_out(3, 54.0, m)["proj_7d"], last["projected_end_pct"], places=1)
        finally:
            shutil.rmtree(d)

    def test_page_uses_the_server_side_factor_and_keeps_privacy_payload_small(self):
        import json
        from pathlib import Path

        import usage_view
        src = (Path(usage_view.__file__)).read_text(encoding="utf-8")
        self.assertIn("d.proj_k", src)
        self.assertIn("sh * (projK != null ? projK :", src)  # fallback to the old formula for an older summary
        d = Path(scratch())
        try:
            raw = {"ok": True, "day": 3, "days": 7, "value": 54.0, "projected": 158.0, "proj_k": 3.3, "avg": 18.0,
                   "mw": {"days": 1.0, "left": 2.0, "at": "secret"}}
            (d / "latest.json").write_text(json.dumps(raw), encoding="utf-8")
            st = usage_view.ViewerState.__new__(usage_view.ViewerState)
            st.summary_path, st.data_dir, st.is_privacy = d / "latest.json", d, True
            with mock.patch.multiple(usage_view.ViewerState, resolve_current_image_path=mock.Mock(return_value=None),
                                     get_next_refresh_iso=mock.Mock(return_value=None),
                                     get_next_refresh_in_sec=mock.Mock(return_value=None),
                                     get_cooldown_remaining=mock.Mock(return_value=0)):
                st.refresh_mode, st.refresh_interval_minutes, st.is_refreshing, st.cooldown_seconds = "manual", 60, False, 0
                p = st.build_latest_payload()
                self.assertEqual((p["projected"], p["proj_k"]), (158.0, 3.3))
                self.assertEqual(p["mw"], {"days": 1.0, "left": 2.0})
                st.is_privacy = False
                self.assertEqual(st.build_latest_payload()["proj_k"], 3.3)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
