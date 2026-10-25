"""The page's Read interval menu (row 2 of the toolbar, after Gauge animation) and POST /api/interval.
Everything runs in scratch dirs with made-up samples; nothing touches real data.
"""
import argparse
import json
import os
import re
import shutil
import unittest
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server
import usage_history as uh  # noqa: E402
from test_clock_palette import write_samples  # noqa: E402
from test_lang_tz import ctx  # noqa: E402

LABELS = {"zh-TW": ("讀取間隔", "15 分鐘"), "zh-CN": ("读取间隔", "15 分钟"), "en": ("Read interval", "15 min"),
          "ja": ("読み取り間隔", "15 分"), "ko": ("읽기 간격", "15분"), "numeric": ("Δt", "15′")}


def page(c):
    uh.render_history(c)
    return read_text(c.hist_dir / "index.html")


def ivsel(p):
    m = re.search(r'<select id="ivsel"[^>]*>(.*?)</select>', p)
    return m.group(0), re.findall(r"<option value='(\d+)'( selected)?>([^<]*)</option>", m.group(1))


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.data = self.dir / "data"
        self.data.mkdir()
        write_samples(str(self.data))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_menu_in_all_languages_after_anim(self):
        for lang, (label, opt) in LABELS.items():
            p = page(ctx(self.data, lang=lang))
            tact = p[p.index('id="tact"'):]
            self.assertLess(tact.index('id="animsel"'), tact.index('id="ivsel"'), lang)
            self.assertLess(tact.index('id="ivsel"'), tact.index('id="manualbtn"'), lang)
            self.assertIn(f'<label class="pal" id="ivl">{label} <select id="ivsel"', p, lang)
            sel, opts = ivsel(p)
            self.assertEqual([o[0] for o in opts], ["15", "30", "60", "120"], lang)
            self.assertEqual(opts[0][2], opt, lang)
            self.assertEqual([o[0] for o in opts if o[1]], ["60"], lang)  # default 60 is shown as current
            for a in ('data-api="/api/interval"', 'data-f="minutes"', 'data-live="1"', 'data-srv="1"'):
                self.assertIn(a, sel)
            self.assertNotIn("data-locked", sel)

    def test_js_live_select_does_not_touch_gauge(self):
        self.assertIn("if(live&&s.id==='animsel'&&window.usageGaugeMode)", uh.PICK_JS)
        self.assertIn("if(!live||s.dataset.srv)", uh.PICK_JS)  # opened as a file: the menu needs the server

    def test_other_configured_value_is_listed(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"sample_interval_minutes": 45}), encoding="utf-8")
        _, opts = ivsel(page(ctx(self.data, str(cfg), lang="en")))
        self.assertEqual([o[0] for o in opts], ["15", "30", "45", "60", "120"])
        self.assertEqual([o[0] for o in opts if o[1]], ["45"])
        cfg.write_text(json.dumps({"sample_interval_minutes": 5}), encoding="utf-8")  # still clamped to 15..1440
        self.assertEqual(ctx(self.data, str(cfg)).interval, 15)
        cfg.write_text(json.dumps({"sample_interval_minutes": 99999}), encoding="utf-8")
        c = ctx(self.data, str(cfg), lang="en")
        self.assertEqual(c.interval, 1440)
        self.assertIn(("1440", " selected", "1440 min"), ivsel(page(c))[1])

    def test_page_setting_used_and_config_wins(self):
        uh.save_page_setting(None, self.data, "sample_interval_minutes", 30)
        self.assertEqual(ctx(self.data).interval, 30)
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"lang": "en"}), encoding="utf-8")
        self.assertEqual(ctx(self.data, str(cfg)).interval, 30)  # config without the key: page setting
        cfg.write_text(json.dumps({"sample_interval_minutes": 120}), encoding="utf-8")
        c = ctx(self.data, str(cfg))
        self.assertEqual(c.interval, 120)
        uh.save_page_setting(str(cfg), self.data, "sample_interval_minutes", 15)
        c.reload()
        self.assertEqual(c.interval, 15)

    def test_manual_mode_disabled(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"pct_source": "manual"}), encoding="utf-8")
        sel, _ = ivsel(page(ctx(self.data, str(cfg), lang="zh-TW")))
        self.assertIn("data-locked='1' title='手動輸入模式：不會自動讀取'", sel)

    def test_privacy_mode_numeric_labels(self):
        a = argparse.Namespace(config=None, data_dir=str(self.data), tz=None, lang="en", theme=None, pace=None,
                               palette=None, privacy=True, host=None, port=None)
        for c in (uh.Ctx(a), ctx(self.data, lang="numeric")):
            p = page(c)
            self.assertIn('<label class="pal" id="ivl">Δt <select id="ivsel"', p)
            self.assertNotIn("Read interval", p)
            self.assertEqual(ivsel(p)[1][0][2], "15′")


class IntervalServerTests(unittest.TestCase):
    def start(self, cfg=None):
        argv = ["usage_history.py", "serve", "--data-dir", str(self.data), "--port", "0"]
        if cfg:
            argv[2:2] = ["--config", str(cfg)]
        self.proc, info = start_server(argv, str(self.data / "history_server.json"))
        self.url, self.token = info["url"], read_text(info["token_file"])

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

    def _post(self, body, token=True):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        code, raw = request(self.url + "api/interval", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_saves_to_page_settings(self):
        self.start()
        for bad in ({"minutes": 10}, {"minutes": 1441}, {"minutes": True}, {"minutes": "abc"}, {"minutes": 30.5},
                    {"minutes": None}, {}):
            self.assertEqual(self._post(bad)[0], 400, bad)
        self.assertEqual(self._post({"minutes": 30}, token=False)[0], 403)
        self.assertEqual(request(self.url + "api/interval")[0], 405)
        self.assertFalse((self.data / uh.PAGE_SETTINGS).exists())
        code, j = self._post({"minutes": "30"})  # the page sends the option value as a string
        self.assertEqual((code, j), (200, {"ok": True, "minutes": 30}))
        self.assertEqual(json.loads((self.data / uh.PAGE_SETTINGS).read_text(encoding="utf-8"))["sample_interval_minutes"], 30)
        self.assertEqual(ctx(self.data).interval, 30)
        self.assertIn("<option value='30' selected>", read_text(self.data / "history" / "index.html"))

    def test_saves_into_existing_config(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"lang": "en", "pace": 14}), encoding="utf-8")
        self.start(cfg)
        self.assertEqual(self._post({"minutes": 120}), (200, {"ok": True, "minutes": 120}))
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")), {"lang": "en", "pace": 14, "sample_interval_minutes": 120})

    def test_manual_mode_rejected(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"pct_source": "manual"}), encoding="utf-8")
        self.start(cfg)
        code, j = self._post({"minutes": 30})
        self.assertEqual((code, j.get("code")), (400, "manual_mode"))
        self.assertNotIn("sample_interval_minutes", json.loads(cfg.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
