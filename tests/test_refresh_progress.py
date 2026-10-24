"""Refresh progress: phases + timings from take_sample, phase in /api/status, elapsed counter on the page.

Scratch dirs only; no Grok Build launch (manual values)."""
import argparse
import json
import os
import shutil
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server

import usage_history as uh  # noqa: E402


def make_ctx(data, lang="en", privacy=False):
    a = argparse.Namespace(config=None, data_dir=data, tz="UTC", lang=lang, theme=None, pace=None, palette=None,
                           privacy=privacy, host=None, port=None)
    return uh.Ctx(a)


class TakeSamplePhaseTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_phases_and_timings(self):
        seen = []
        r = uh.take_sample(make_ctx(self.dir), 42, phase=seen.append)
        self.assertEqual(seen, ["read", "draw"])
        self.assertEqual(set(r["timings"]), {"read", "draw"})
        self.assertTrue(all(v >= 0 for v in r["timings"].values()))

    def test_failed_read_still_reports_phases(self):
        seen = []
        with self.assertRaises(RuntimeError):
            uh.take_sample(make_ctx(self.dir), phase=seen.append, fail_reason="simulated")
        self.assertEqual(seen, ["read", "draw"])


class PageTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _page(self, **kw):
        ctx = make_ctx(self.dir, **kw)
        uh.take_sample(ctx, 30)
        with open(os.path.join(self.dir, "history", "index.html"), encoding="utf-8") as fh:
            return fh.read()

    def test_elapsed_counter_strings(self):
        en = self._page()
        self.assertIn("Refreshing… {s} s", en)
        self.assertIn("Reading usage from Grok Build", en)
        self.assertIn("setInterval(show,1000)", en)
        self.assertIn("rb.disabled=true", en)
        self.assertIn('id="rmsg" role="status"', en)
        zh = self._page(lang="zh-TW")
        self.assertIn("⟳ 更新中… {s} 秒", zh)
        self.assertIn("正在產生圖表", zh)

    def test_privacy_has_no_service_name(self):
        page = self._page(privacy=True)
        self.assertIn('"refreshing": "… {s}"', page)
        self.assertNotIn("Grok Build", page)


class StatusPhaseTests(unittest.TestCase):
    def test_status_has_phase(self):
        d = scratch()
        try:
            data = os.path.join(d, "data")
            os.makedirs(data)
            proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
            try:
                code, body = request(info["url"] + "api/status")
                j = json.loads(body)
                self.assertEqual(code, 200)
                self.assertIn("phase", j)
                self.assertIsNone(j["phase"])  # idle
                tok = read_text(info["token_file"])
                code, body = request(info["url"] + "api/manual", "POST",
                                     {"X-Usage-Token": tok, "Content-Type": "application/json", "Origin": info["url"].rstrip("/")},
                                     json.dumps({"pct": "12"}).encode())
                self.assertEqual(code, 200, body)
                self.assertIn("timings", json.loads(body))
            finally:
                stop_server(proc)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
