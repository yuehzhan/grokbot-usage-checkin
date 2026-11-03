"""User-selectable forecast sensitivity: presets (usage_sensitivity.py), blend_rate / rate_model parameters,
persistence (usage_config.json / page_settings.json), the toolbar menu in every language, POST /api/forecast-sensitivity
and the redrawn charts. Everything runs in scratch dirs with made-up numbers."""
import argparse
import json
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server
from test_clock_palette import write_samples
from test_lang_tz import ctx
from test_pace_rate import END, PCTS, ROWS, START, hourly_samples, pts

import chart_hud
import usage_history as uh
import usage_rate
import usage_sensitivity as us

TPE = ZoneInfo("Asia/Taipei")
T54 = START + timedelta(hours=54)


def legacy_blend_rate(points, t_now, pct_now, t_from, window_h=24.0):
    """Frozen copy of blend_rate as released in 1.12.2 (before presets): the reference for the default."""
    def naive(t):
        return t.replace(tzinfo=None) if getattr(t, "tzinfo", None) is not None else t
    t_now, t_from = naive(t_now), naive(t_from)
    elapsed_h = max(12.0, (t_now - t_from).total_seconds() / 3600.0)
    wtd = max(0.0, float(pct_now)) / (elapsed_h / 24.0)
    ps = []
    for t, v in points or ():
        try:
            tt = naive(t)
            if v is not None and t_from <= tt <= t_now:
                ps.append((tt, float(v)))
        except (TypeError, ValueError):
            continue
    ps.sort(key=lambda x: x[0])
    out = {"rate": wtd, "wtd": wtd, "recent": None, "weight": 0.0, "elapsed_h": elapsed_h}
    if not ps:
        return out
    ws = t_now - timedelta(hours=window_h)
    before = [p for p in ps if p[0] <= ws]
    inside = [p for p in ps if p[0] > ws]
    base = before[-1] if before else (inside[0] if inside else None)
    win = ([base] if before else []) + inside
    if base is None or len(win) < 3:
        return out
    span_h = (t_now - base[0]).total_seconds() / 3600.0
    if span_h < 6.0:
        return out
    recent = max(0.0, float(pct_now) - base[1]) / (span_h / 24.0)
    weight = 0.5 * min(1.0, span_h / window_h)
    rate = weight * recent + (1.0 - weight) * wtd
    rate = min(max(rate, 0.5 * wtd), 2.0 * wtd)
    out.update({"rate": rate, "recent": recent, "weight": weight})
    return out


# one case per branch: steady, accelerating, slowing, one-off jump, partial coverage, early, too few, too short a span
CASES = [
    (pts((0, 0.0), (30, 30.0), (42, 42.0), (54, 54.0)), 54, 54.0),
    (pts((0, 0.0), (30, 6.0), (42, 30.0), (54, 54.0)), 54, 54.0),
    (pts((0, 0.0), (30, 54.0), (42, 54.0), (54, 54.0)), 54, 54.0),
    (pts((0, 0.0), (30, 0.0), (42, 0.0), (54, 54.0)), 54, 54.0),
    (pts((0, 0.0), (6, 10.0), (10, 20.0), (14, 30.0)), 14, 30.0),
    (pts((0, 0.0), (1, 3.0)), 1, 3.0),
    (pts((0, 0.0), (53, 53.0)), 54, 54.0),
    (pts((52.0, 50.0), (52.5, 52.0), (53.5, 53.0)), 54, 54.0),
    ([(START + timedelta(hours=h), h * 0.37 + (h % 5) * 0.2) for h in range(0, 100)], 99, 40.0),
    ([], 54, 54.0),
]


class PresetTests(unittest.TestCase):
    def test_preset_values(self):
        self.assertEqual(us.KEYS, ("standard", "sensitive", "very_sensitive"))
        self.assertEqual(us.DEFAULT, "standard")
        self.assertEqual(us.params("standard"), {"window_h": 24.0, "max_weight": 0.5, "min_span_h": 6.0})
        self.assertEqual(us.params("sensitive"), {"window_h": 12.0, "max_weight": 0.5, "min_span_h": 3.0})
        self.assertEqual(us.params("very_sensitive"), {"window_h": 6.0, "max_weight": 0.6, "min_span_h": 2.0})
        # the standard preset is today's constants
        self.assertEqual(us.params("standard"), {"window_h": usage_rate.RECENT_WINDOW_H,
                                                 "max_weight": usage_rate.RECENT_MAX_WEIGHT,
                                                 "min_span_h": usage_rate.RECENT_MIN_SPAN_H})
        self.assertEqual(usage_rate.RATE_CLAMP, (0.5, 2.0))  # the clamp is shared by all presets

    def test_unknown_values_mean_standard(self):
        for bad in (None, "", 3, True, [], {}, "turbo", "SENSITIVE ", "very-sensitive "):
            expect = "sensitive" if bad == "SENSITIVE " else ("very_sensitive" if bad == "very-sensitive " else "standard")
            self.assertEqual(us.clamp(bad), expect, bad)
        self.assertEqual(us.params("nope"), us.params("standard"))

    def test_suffix(self):
        self.assertEqual([us.suffix(k) for k in us.KEYS], ["", "-Fsensitive", "-Fvery_sensitive"])

    def test_from_settings_config_then_page_settings(self):
        d = Path(scratch())
        try:
            self.assertEqual(us.from_settings({}, d), "standard")
            (d / "page_settings.json").write_text(json.dumps({"forecast_sensitivity": "sensitive"}), encoding="utf-8")
            self.assertEqual(us.from_settings({}, d), "sensitive")
            self.assertEqual(us.from_settings({"forecast_sensitivity": "very_sensitive"}, d), "very_sensitive")
            self.assertEqual(us.from_settings({"forecast_sensitivity": "junk"}, d), "standard")
            (d / "page_settings.json").write_text("not json", encoding="utf-8")
            self.assertEqual(us.from_settings({}, d), "standard")
        finally:
            shutil.rmtree(d)


class BlendRateTests(unittest.TestCase):
    def test_default_arguments_equal_the_released_numbers_bit_for_bit(self):
        for points, h, pct in CASES:
            t = START + timedelta(hours=h)
            old = legacy_blend_rate(points, t, pct, START)
            self.assertEqual(usage_rate.blend_rate(points, t, pct, START), old, (h, pct))
            self.assertEqual(usage_rate.blend_rate(points, t, pct, START, **us.params("standard")), old, (h, pct))
            self.assertEqual(usage_rate.blend_rate(points, t, pct, START, window_h=24.0), old)  # old signature still works

    def test_rate_model_default_equals_standard_equals_old(self):
        for samples in (None, hourly_samples()):
            base = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=samples)
            for s in (None, "standard", "junk"):
                self.assertEqual(usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=samples,
                                                       sensitivity=s), base)
            old = legacy_blend_rate([(usage_rate.row_time(r, START), p) for r, p in zip(ROWS, PCTS)]
                                    + list(samples or ()) + [(START, 0.0)], T54, 54.0, START)
            self.assertEqual(base["rate_day"], old["rate"])

    def test_sensitive_uses_a_12_hour_window(self):
        # 0 -> 30 over 42 h, then 30 -> 54 over the last 12 h: 48 %/day in the last 12 h, 24 %/day in the last 24 h
        points = pts((0, 0.0), (30, 24.0), (42, 30.0), (48, 42.0), (54, 54.0))
        std = usage_rate.blend_rate(points, T54, 54.0, START, **us.params("standard"))
        sen = usage_rate.blend_rate(points, T54, 54.0, START, **us.params("sensitive"))
        self.assertAlmostEqual(std["recent"], (54.0 - 24.0) / 1.0)  # base = last reading at/before t-24h (h=30): 24 h
        self.assertAlmostEqual(sen["recent"], (54.0 - 30.0) / 0.5)  # base = h=42: 12 h -> 48 %/day
        self.assertAlmostEqual(sen["weight"], 0.5)               # window fully covered: the max weight
        self.assertGreater(sen["rate"], std["rate"])

    def test_very_sensitive_uses_a_6_hour_window_and_weight_0_6(self):
        points = pts((0, 0.0), (30, 24.0), (42, 30.0), (48, 42.0), (50, 46.0), (54, 54.0))
        vs = usage_rate.blend_rate(points, T54, 54.0, START, **us.params("very_sensitive"))
        self.assertAlmostEqual(vs["recent"], (54.0 - 42.0) / 0.25)  # base = h=48 (6 h before): 48 %/day
        self.assertAlmostEqual(vs["weight"], 0.6)
        wtd = 54.0 / 2.25
        self.assertAlmostEqual(vs["rate"], 0.6 * 48.0 + 0.4 * wtd)
        sen = usage_rate.blend_rate(points, T54, 54.0, START, **us.params("sensitive"))
        self.assertGreater(vs["weight"], sen["weight"])

    def test_min_span_scales_with_the_preset(self):
        # no reading before the window: the base is the first reading inside it, so the span is what the readings cover
        four_h = pts((50.0, 40.0), (51.0, 44.0), (52.0, 50.0), (54.0, 54.0))      # covers 4 h
        two_half = pts((51.5, 44.0), (52.5, 50.0), (53.5, 53.0), (54.0, 54.0))    # covers 2.5 h
        short = pts((52.5, 50.0), (53.0, 51.0), (53.5, 53.0), (54.0, 54.0))       # covers 1.5 h
        used = lambda points, key: usage_rate.blend_rate(points, T54, 54.0, START, **us.params(key))["recent"] is not None
        self.assertEqual([used(four_h, k) for k in us.KEYS], [False, True, True])   # standard needs 6 h, sensitive 3 h
        self.assertEqual([used(two_half, k) for k in us.KEYS], [False, False, True])  # very sensitive needs 2 h
        self.assertEqual([used(short, k) for k in us.KEYS], [False, False, False])
        # below the minimum the result is exactly the week-to-date rate
        r = usage_rate.blend_rate(two_half, T54, 54.0, START, **us.params("sensitive"))
        self.assertEqual((r["rate"], r["weight"]), (r["wtd"], 0.0))

    def test_partial_coverage_weight_uses_the_preset_window_and_max(self):
        points = pts((50.0, 40.0), (51.0, 44.0), (52.0, 50.0), (54.0, 54.0))  # 4 h of readings
        sen = usage_rate.blend_rate(points, T54, 54.0, START, **us.params("sensitive"))
        vs = usage_rate.blend_rate(points, T54, 54.0, START, **us.params("very_sensitive"))
        self.assertAlmostEqual(sen["weight"], 0.5 * 4 / 12)
        self.assertAlmostEqual(vs["weight"], 0.6 * 4 / 6)

    def test_clamp_applies_to_every_preset(self):
        points = pts((0, 0.0), (30, 0.0), (42, 0.0), (48, 0.0), (52, 0.0), (54, 54.0))  # a one-off jump
        for key in us.KEYS:
            r = usage_rate.blend_rate(points, T54, 54.0, START, **us.params(key))
            self.assertLessEqual(r["rate"], 2.0 * r["wtd"] + 1e-9, key)
            self.assertGreaterEqual(r["rate"], 0.5 * r["wtd"] - 1e-9, key)
        idle = pts((0, 0.0), (30, 54.0), (42, 54.0), (48, 54.0), (52, 54.0), (54, 54.0))
        for key in us.KEYS:
            r = usage_rate.blend_rate(idle, T54, 54.0, START, **us.params(key))
            self.assertAlmostEqual(r["rate"], 0.5 * r["wtd"], msg=key)


class RateModelTests(unittest.TestCase):
    def test_presets_change_the_projection_consistently(self):
        samples = hourly_samples()
        res = {k: usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=samples, sensitivity=k)
               for k in us.KEYS}
        for k, m in res.items():
            p = us.params(k)
            self.assertLessEqual(m["rate_info"]["weight"], p["max_weight"], k)
            self.assertAlmostEqual(m["proj_rate"], 54.0 + m["rate_day"] * m["rate_info"]["rem_days"])
        self.assertEqual(res["standard"], usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=samples))
        self.assertEqual(len({round(m["rate_day"], 6) for m in res.values()}), 3)  # three different forecasts
        self.assertAlmostEqual(res["standard"]["rate_info"]["recent"], 20.0)
        self.assertAlmostEqual(res["sensitive"]["rate_info"]["weight"], 0.5)
        self.assertAlmostEqual(res["very_sensitive"]["rate_info"]["weight"], 0.6)

    def test_proj_k_follows_the_preset_weight(self):
        for k in us.KEYS:
            m = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=hourly_samples(), sensitivity=k)
            ri = m["rate_info"]
            self.assertAlmostEqual(ri["proj_k"], 1.0 + (1.0 - ri["weight"]) * ri["rem_days"] / (ri["elapsed_h"] / 24.0))


class ChartTests(unittest.TestCase):
    def test_normalize_cfg(self):
        self.assertEqual(chart_hud.normalize_cfg({})["forecast_sensitivity"], "standard")
        self.assertEqual(chart_hud.normalize_cfg({"forecast_sensitivity": "very_sensitive"})["forecast_sensitivity"], "very_sensitive")
        self.assertEqual(chart_hud.normalize_cfg({"forecast_sensitivity": "bogus"})["forecast_sensitivity"], "standard")

    def test_render_passes_the_preset_to_the_rate_model(self):
        seen = []
        real = usage_rate.rate_model

        def spy(*a, **kw):
            seen.append(kw.get("sensitivity"))
            return real(*a, **kw)
        d = Path(scratch())
        try:
            with mock.patch.object(usage_rate, "rate_model", spy):
                for key in (None, "sensitive", "very_sensitive"):
                    cfg = {"lang": "en", "samples": hourly_samples()}
                    if key:
                        cfg["forecast_sensitivity"] = key
                    chart_hud.render(ROWS, str(d / "o.png"), "2026-10-30", cfg)
            self.assertEqual(seen, ["standard", "sensitive", "very_sensitive"])
        finally:
            shutil.rmtree(d)

    def test_gauge_projection_bar_follows_the_preset(self):
        d = Path(scratch())
        try:
            bar = {}
            # a quarter of the usage of the shared example, so the projection stays under 100% (the bar caps there)
            rows = [dict(r, pct=str(float(r["pct"]) / 4)) for r in ROWS]
            samples = [(t, v / 4) for t, v in hourly_samples()]
            for key in us.KEYS:
                meta = chart_hud.render(rows, str(d / f"{key}.png"), "2026-10-30",
                                        {"lang": "en", "samples": samples, "forecast_sensitivity": key})
                bar[key] = meta["bar"]["proj_r"]
            self.assertEqual(len(set(bar.values())), 3, bar)  # the gauge / progress bar projection moves with the preset
            default = chart_hud.render(rows, str(d / "d.png"), "2026-10-30", {"lang": "en", "samples": samples})
            self.assertEqual(default["bar"]["proj_r"], bar["standard"])
        finally:
            shutil.rmtree(d)

    def test_run_out_uses_the_chosen_pace(self):
        slow = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=hourly_samples(), sensitivity="standard")
        fast = usage_rate.rate_model(ROWS, PCTS, START, END, 14, latest_day=3, samples=hourly_samples(), sensitivity="very_sensitive")
        a, b = chart_hud.run_out(3, 54.0, slow), chart_hud.run_out(3, 54.0, fast)
        self.assertEqual(a["rate_day"], slow["rate_day"])
        self.assertEqual(b["rate_day"], fast["rate_day"])
        self.assertEqual(b["proj_7d"], fast["proj_rate"])


class CtxPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()
        write_samples(str(self.data))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_default_is_standard_and_key_is_unchanged(self):
        c = ctx(self.data)
        self.assertEqual((c.forecast_sensitivity, c.key_suffix), ("standard", ""))

    def test_round_trip_page_settings(self):
        for k in us.KEYS:
            uh.save_page_setting(None, self.data, "forecast_sensitivity", k)
            saved = json.loads((self.data / uh.PAGE_SETTINGS).read_text(encoding="utf-8"))
            self.assertEqual(saved["forecast_sensitivity"], k)
            self.assertEqual(ctx(self.data).forecast_sensitivity, k)

    def test_round_trip_config_file_keeps_other_keys_and_wins(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"lang": "ko", "pace": 14}), encoding="utf-8")
        uh.save_page_setting(None, self.data, "forecast_sensitivity", "sensitive")
        self.assertEqual(ctx(self.data, str(cfg)).forecast_sensitivity, "sensitive")  # config without the key: page setting
        uh.save_page_setting(str(cfg), self.data, "forecast_sensitivity", "very_sensitive")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")),
                         {"lang": "ko", "pace": 14, "forecast_sensitivity": "very_sensitive"})
        c = ctx(self.data, str(cfg))
        self.assertEqual(c.forecast_sensitivity, "very_sensitive")
        cfg.write_text(json.dumps({"forecast_sensitivity": "nonsense"}), encoding="utf-8")
        c.reload()
        self.assertEqual(c.forecast_sensitivity, "standard")

    def test_cache_key(self):
        uh.save_page_setting(None, self.data, "forecast_sensitivity", "sensitive")
        c = ctx(self.data)
        self.assertEqual(c.key_suffix, "-Fsensitive")
        self.assertTrue(c.pal_key.endswith("-Fsensitive"), c.pal_key)
        uh.save_page_setting(None, self.data, "forecast_sensitivity", "very_sensitive")
        uh.save_page_setting(None, self.data, "display_offset", 1)
        uh.save_page_setting(None, self.data, "lang", "ja")
        uh.save_page_setting(None, self.data, "tz", "Asia/Tokyo")
        c = ctx(self.data)
        self.assertEqual(c.key_suffix, "-o+1-Lja-Zasia_tokyo-Fvery_sensitive")
        self.assertEqual(uh.strip_offset("gold-sky-pat" + c.key_suffix), "gold-sky-pat")
        self.assertTrue(uh.is_dir_key("gold-sky-Fsensitive"))
        self.assertEqual(uh.strip_offset("gold-sky-Zutc-Fvery_sensitive"), "gold-sky")
        self.assertNotEqual(ctx(self.data).pal_key, ctx(self.data, lang="ja", tz="Asia/Tokyo").pal_key)

    def test_usage_record_reads_the_same_setting(self):
        import usage_record
        self.assertEqual(usage_record._forecast_sensitivity({}, self.data), "standard")
        uh.save_page_setting(None, self.data, "forecast_sensitivity", "sensitive")
        self.assertEqual(usage_record._forecast_sensitivity({}, self.data), "sensitive")
        self.assertEqual(usage_record._forecast_sensitivity({"forecast_sensitivity": "very_sensitive"}, self.data), "very_sensitive")


LABELS = {  # label, then the three option texts
    "zh-TW": ("預估靈敏度", ["標準（近 24 小時）", "靈敏（近 12 小時）", "非常靈敏（近 6 小時）"]),
    "zh-CN": ("预估灵敏度", ["标准（近 24 小时）", "灵敏（近 12 小时）", "非常灵敏（近 6 小时）"]),
    "en": ("Forecast sensitivity", ["Standard (last 24 h)", "Sensitive (last 12 h)", "Very sensitive (last 6 h)"]),
    "ja": ("予測の感度", ["標準（直近 24 時間）", "高感度（直近 12 時間）", "超高感度（直近 6 時間）"]),
    "ko": ("예측 민감도", ["표준(최근 24시간)", "민감(최근 12시간)", "매우 민감(최근 6시간)"]),
    "numeric": ("∿", ["1", "2", "3"]),
}


def page(c):
    uh.render_history(c)
    return read_text(c.hist_dir / "index.html")


def fcsel(p):
    m = re.search(r'<select id="fcsel"[^>]*>(.*?)</select>', p)
    return m.group(0), re.findall(r"<option value='(\w+)'( selected)?>([^<]*)</option>", m.group(1))


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()
        write_samples(str(self.data))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_menu_present_in_settings_row_in_every_language(self):
        for lang, (label, opts) in LABELS.items():
            p = page(ctx(self.data, lang=lang))
            sel, found = fcsel(p)
            self.assertIn(f'id="fcl"', p, lang)
            self.assertRegex(p, rf'<label class="pal" id="fcl" title="[^"]+">{re.escape(label)} <select id="fcsel"', lang)
            self.assertEqual([o[0] for o in found], list(us.KEYS), lang)
            self.assertEqual([o[2] for o in found], opts, lang)
            self.assertEqual([o[0] for o in found if o[1]], ["standard"], lang)  # default is shown as current
            for a in ('data-api="/api/forecast-sensitivity"', 'data-f="sensitivity"', 'data-k="usage.forecastSensitivity"'):
                self.assertIn(a, sel, lang)
            self.assertNotIn("data-live", sel)  # a change redraws the charts and reloads the page
            # settings row (before the actions row), right after the time zone menu
            self.assertLess(p.index('id="tzsel"'), p.index('id="fcsel"'), lang)
            self.assertLess(p.index('id="fcsel"'), p.index('id="animsel"'), lang)

    def test_every_page_language_has_the_labels(self):
        for lang in uh.PICK_STR:
            for k in ("sens", "sens_tip", "sens_standard", "sens_sensitive", "sens_very_sensitive"):
                self.assertTrue(uh.PICK_STR[lang][k].strip(), (lang, k))
        for lang in uh.PAGE_LANGS:
            if lang != "auto":
                self.assertIn(lang, uh.PICK_STR, lang)

    def test_selected_option_follows_the_setting(self):
        uh.save_page_setting(None, self.data, "forecast_sensitivity", "very_sensitive")
        p = page(ctx(self.data, lang="zh-TW"))
        self.assertEqual([o[0] for o in fcsel(p)[1] if o[1]], ["very_sensitive"])
        self.assertIn('"osfx": "-Fvery_sensitive"', p)

    def test_generic_menu_script_handles_it(self):
        self.assertIn("select[data-api]", uh.PICK_JS)
        self.assertIn("/api/forecast-sensitivity", read_text(uh.__file__))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()
        write_samples(str(self.data))
        self.proc = None

    def tearDown(self):
        if self.proc:
            stop_server(self.proc)
        shutil.rmtree(self.dir)

    def start(self, cfg=None):
        argv = ["usage_history.py", "serve", "--data-dir", str(self.data), "--port", "0"]
        if cfg:
            argv[2:2] = ["--config", str(cfg)]
        self.proc, info = start_server(argv, str(self.data / "history_server.json"))
        self.url, self.token = info["url"], read_text(info["token_file"])

    def post(self, body, token=True):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        code, raw = request(self.url + "api/forecast-sensitivity", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_post_validates_saves_and_redraws(self):
        self.start()
        for bad in ({"sensitivity": "turbo"}, {"sensitivity": 2}, {"sensitivity": None}, {"sensitivity": True}, {}):
            self.assertEqual(self.post(bad)[0], 400, bad)
        self.assertEqual(self.post({"sensitivity": "sensitive"}, token=False)[0], 403)
        self.assertEqual(request(self.url + "api/forecast-sensitivity")[0], 405)
        self.assertFalse((self.data / uh.PAGE_SETTINGS).exists())
        code, j = self.post({"sensitivity": "sensitive"})
        self.assertEqual((code, j), (200, {"ok": True, "sensitivity": "sensitive"}))
        saved = json.loads((self.data / uh.PAGE_SETTINGS).read_text(encoding="utf-8"))
        self.assertEqual(saved["forecast_sensitivity"], "sensitive")
        p = read_text(self.data / "history" / "index.html")
        self.assertEqual([o[0] for o in fcsel(p)[1] if o[1]], ["sensitive"])  # page was redrawn with the new preset
        code, j = self.post({"sensitivity": "standard"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads((self.data / uh.PAGE_SETTINGS).read_text(encoding="utf-8"))["forecast_sensitivity"], "standard")

    def test_post_writes_into_the_config_file_when_there_is_one(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"lang": "en", "pace": 14}), encoding="utf-8")
        self.start(cfg)
        self.assertEqual(self.post({"sensitivity": "very_sensitive"})[0], 200)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")),
                         {"lang": "en", "pace": 14, "forecast_sensitivity": "very_sensitive"})
        self.assertFalse((self.data / uh.PAGE_SETTINGS).exists())


if __name__ == "__main__":
    unittest.main()
