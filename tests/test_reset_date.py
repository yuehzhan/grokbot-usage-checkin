"""Next reset shown as an explicit local date (10月31日（週六）01:41 / Sat, Oct 31, 01:41), taken exactly from /usage.

Scratch dirs and made-up values only; Grok Build is never started (read_usage is mocked).
"""
import argparse
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import NEEDS_CJK, has_cjk_font, scratch

import chart_hud  # noqa: E402
import grok_usage_reader  # noqa: E402
import usage_dates as ud  # noqa: E402
import usage_history as uh  # noqa: E402
import usage_ledger as ul  # noqa: E402
import usage_record as ur  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
RELATIVE = ("下週", "下周", "next week", "Next week")


class FormatTests(unittest.TestCase):
    def test_formats(self):
        d = datetime(2026, 10, 31, 1, 41, tzinfo=TPE)
        self.assertEqual(ud.reset_date(d, "zh-TW"), "10月31日（週六）01:41")
        self.assertEqual(ud.reset_date(d, "zh-CN"), "10月31日（周六）01:41")
        self.assertEqual(ud.reset_date(d, "en"), "Sat, Oct 31, 01:41")
        self.assertEqual(ud.reset_date(d, "numeric"), "10/31 01:41")
        # converted to the local zone only when the value carries a zone
        self.assertEqual(ud.reset_date(datetime(2026, 10, 30, 17, 41, tzinfo=timezone.utc), "en", TPE), "Sat, Oct 31, 01:41")
        self.assertEqual(ud.reset_date(datetime(2026, 10, 31, 1, 41), "en", timezone.utc), "Sat, Oct 31, 01:41")
        self.assertEqual(ud.reset_date(None, "en"), "")

    def test_ledger_and_page_texts(self):
        d = datetime(2026, 10, 30, 17, 41, tzinfo=timezone.utc)
        want = {"en": "resets Sat, Oct 31, 01:41 (UTC+8)", "zh-TW": "重置 10月31日（週六）01:41（UTC+8）",
                "zh-CN": "重置 10月31日（周六）01:41（UTC+8）", "numeric": "↻ 10/31 01:41"}
        for lang, text in want.items():
            self.assertEqual(ul._reset_text(ul.TEXT[lang], d, timezone(timedelta(hours=8)), lang == "numeric"), text)
        for lang in ("en", "zh-TW", "zh-CN", "numeric"):
            self.assertIn("{date}", uh.LEDGER_STR[lang]["reset"])
            self.assertNotIn("{wd}", uh.LEDGER_STR[lang]["reset"])
        self.assertIn("function RD(", uh.LEDGER_JS)
        self.assertIn("date:RD(w,S.dl)", uh.LEDGER_JS)
        self.assertEqual(chart_hud.BASE_STRINGS["stat_reset_sub"], "{reset_date}")
        for lang in ("zh-TW", "zh-CN", "ja", "ko", "numeric"):
            self.assertIn("{reset_date}", chart_hud.TRANSLATIONS[lang]["stat_reset_sub"])
        blob = json.dumps([uh.LEDGER_STR, ul.TEXT], ensure_ascii=False)
        for w in RELATIVE:
            self.assertNotIn(w, blob)


class ExactValueTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(scratch())

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_ledger_keeps_value_as_given(self):
        now = datetime(2026, 10, 24, 18, 0, tzinfo=TPE)
        odd = datetime(2026, 10, 20, 1, 41, tzinfo=TPE)  # already past: still shown as given
        led = ul.record_hud(self.d, {}, 40.0, reset_at=odd, tz=TPE, lang="zh-CN", now=now)
        self.assertEqual(led["resetsAt"], "2026-10-20T01:41:00+08:00")

    def test_ledger_never_guesses(self):
        # ledger contract (README): resetsAt is only what /usage (or a typed-in reset) gave, else null
        now = datetime(2026, 10, 24, 18, 0, tzinfo=TPE)
        (self.d / "state.json").write_text(json.dumps({"reset_at": "2026-10-03T01:41+08:00"}), encoding="utf-8")
        led = ul.record_hud(self.d, {}, 40.0, reset_at=None, tz=TPE, now=now)
        self.assertIsNone(led["resetsAt"])

    @unittest.skipUnless(has_cjk_font(), NEEDS_CJK)
    def test_chart_hud_shows_given_reset(self):
        seen = {}

        def spy(fig, ax, data, cfg, L, pal, t_info):
            seen.update(t_info)
        rows = [{"day": "1", "date": "2026-10-24", "pct": "10", "note": ""}]
        cfgs = (({"lang": "zh-TW", "tz": "Asia/Taipei", "reset_hm": "09:00", "reset_at": "2026-10-29T01:41+08:00"}, "10月29日（週四）01:41"),
                ({"lang": "en", "tz": "Asia/Taipei", "reset_hm": "09:00", "reset_at": "2026-10-28T17:41:00+00:00"}, "Thu, Oct 29, 01:41"),
                ({"lang": "en", "tz": "Asia/Taipei", "reset_hm": "09:00"}, "Sat, Oct 31, 09:00"),  # fallback: start + 7 days
                ({"lang": "numeric", "privacy": True, "tz": "Asia/Taipei", "reset_hm": "09:00"}, "10/31 09:00"))
        for cfg, want in cfgs:
            with mock.patch.object(chart_hud, "draw_hero_and_stats", spy):
                chart_hud.render([dict(r) for r in rows], str(self.d / "a.png"), "2026-10-24", cfg)
            self.assertEqual(seen["reset_date"], want, cfg)
        with mock.patch.object(chart_hud, "draw_hero_and_stats", spy):
            chart_hud.render([dict(r) for r in rows], str(self.d / "a.png"), "2026-10-24",
                             {"lang": "en", "tz": "Asia/Taipei", "reset_at": "2020-01-04T01:41+08:00"})
        self.assertEqual(seen["reset_date"], "Sat, Jan 4, 01:41")  # past value: shown as is, countdown 0
        self.assertEqual(seen["countdown_short"], chart_hud.format_duration(0, L=chart_hud.BASE_STRINGS))

    def _record(self, reset_at, extra=()):
        cfg = self.d / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        seen, self.err = {}, io.StringIO()

        def fake_render(*a, **k):
            seen.update(next(x for x in list(a) + list(k.values()) if isinstance(x, dict) and "reset_hm" in x))
        argv = ["--pct", "40", "--reset-source", "auto", "--config", str(cfg), "--data-dir", str(self.d),
                "--tz", "Asia/Taipei", "--no-open", "--format", "json", *extra]
        out = io.StringIO()
        with mock.patch.object(grok_usage_reader, "read_usage", return_value={"pct": 40.0, "reset_at": reset_at}), \
                mock.patch.object(chart_hud, "render", fake_render), redirect_stdout(out), redirect_stderr(self.err):
            with self.assertRaises(SystemExit) as cm:
                ur.main(argv)
        self.assertEqual(cm.exception.code, 0, self.err.getvalue() + out.getvalue())
        return json.loads(out.getvalue().strip().splitlines()[-1]), seen

    def test_record_uses_usage_value_exactly(self):
        # an odd value from /usage (past, not on the usual hour): displayed exactly, never rolled forward
        odd = datetime.now(TPE).replace(second=0, microsecond=0) - timedelta(days=2, minutes=17)
        summary, render_cfg = self._record(odd)
        self.assertEqual(summary["reset_source"], "grok")
        self.assertEqual(summary["reset_at"], odd.isoformat(timespec="minutes"))
        self.assertEqual(render_cfg["reset_at"], odd.isoformat(timespec="minutes"))
        self.assertEqual(render_cfg["display_offset"], 0)
        self.assertEqual(summary["reset_in_hours"], 0)
        self.assertIn(f"({ud.reset_date(odd, 'en')})", summary["summary_text"])
        st = json.loads((self.d / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["reset_at"], odd.isoformat(timespec="minutes"))
        led = json.loads(next(self.d.rglob("*ledger*.json")).read_text(encoding="utf-8"))
        self.assertEqual(datetime.fromisoformat(led["resetsAt"]), odd)

    def test_record_converts_zone_only(self):
        utc = (datetime.now(timezone.utc) + timedelta(days=3)).replace(second=0, microsecond=0)
        summary, render_cfg = self._record(utc)
        self.assertEqual(datetime.fromisoformat(summary["reset_at"]), utc)
        self.assertEqual(summary["reset_at"], utc.astimezone(TPE).isoformat(timespec="minutes"))

    def test_record_fallback_without_usage_reset(self):
        summary, render_cfg = self._record(None, ("--reset-anchor", "Sat 01:41"))
        self.assertEqual(summary["reset_source"], "cli")
        r = datetime.fromisoformat(summary["reset_at"])
        self.assertEqual((r.weekday(), r.hour, r.minute), (5, 1, 41))
        self.assertGreater(r, datetime.now(TPE) - timedelta(minutes=1))

    def test_history_hud_reset(self):
        a = argparse.Namespace(config=None, data_dir=str(self.d), tz="Asia/Taipei", lang="zh-TW", theme=None, pace=None,
                               palette=None, privacy=False, host=None, port=None)
        ctx = uh.Ctx(a)
        now = datetime(2026, 10, 24, 18, 0, tzinfo=TPE)
        self.assertIsNone(uh.shown_reset(ctx, now))
        (self.d / "state.json").write_text(json.dumps({"reset_at": "2026-10-23T01:41+08:00", "updated_at": "2026-10-24T09:00+08:00"}),
                                           encoding="utf-8")
        self.assertEqual(uh.shown_reset(ctx, now), "2026-10-23T01:41+08:00")  # exactly as /usage gave it
        self.assertIsNone(uh.shown_reset(ctx, now + timedelta(days=8)))  # /usage has not given one for a period


class ReadmeTests(unittest.TestCase):
    def test_docs(self):
        root = Path(__file__).resolve().parent.parent
        en = (root / "README.md").read_text(encoding="utf-8")
        zh = (root / "README.zh-TW.md").read_text(encoding="utf-8")
        self.assertIn("`Sat, Oct 31, 01:41`", en)
        self.assertIn("`10月31日（週六）01:41`", zh)
        self.assertIn("not\nrolled forward or corrected", en)
        self.assertIn("不重算、不往後推", zh)


if __name__ == "__main__":
    unittest.main()
