"""Colour-blind preset: Okabe-Ito colours plus non-colour cues (hatching, dashed over-line, ▲/▼)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import usage_palette as up  # noqa: E402
import chart_hud  # noqa: E402


def contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


ROWS = [{"day": str(d), "date": f"2026-01-0{d}", "pct": str(p)} for d, p in zip(range(1, 6), (5, 12, 28, 63, 81))]


class PatternFlagTests(unittest.TestCase):
    def test_flag_only_for_colorblind(self):
        self.assertEqual(up.PATTERNS, {"colorblind"})
        self.assertTrue(up.patterns("colorblind"))
        self.assertTrue(up.patterns("okabe-ito"))
        for n in up.names():
            if n != "colorblind":
                self.assertFalse(up.patterns(n), n)
        t = up.js_table()
        self.assertTrue(t["colorblind"]["patterns"])
        self.assertFalse(t["orange-teal"]["patterns"])
        self.assertEqual(up.HATCH, "///")

    def test_dark_under_contrast(self):
        self.assertGreaterEqual(contrast(up.get("colorblind")["under"], "#000000"), 4.5)
        self.assertGreaterEqual(contrast(up.get("colorblind", True)["under"], "#F4F4F0"), 4.5)

    def test_chart_cfg(self):
        self.assertIn("patterns", chart_hud.DEFAULT_CFG)
        self.assertEqual(chart_hud._leg_style({"patterns": False}, True), {})
        self.assertEqual(chart_hud._leg_style({"patterns": True}, True)["marker"], "^")
        self.assertEqual(chart_hud._leg_style({"patterns": True}, False)["ls"], "-")
        with tempfile.TemporaryDirectory() as d:
            th = Path(d) / "t.json"
            th.write_text(json.dumps({"patterns": True}), encoding="utf-8")
            self.assertTrue(chart_hud.normalize_cfg({"palette": "orange-teal"}, theme_path=str(th))["patterns"])
            for pal in ("colorblind", "orange-teal"):
                out = Path(d) / f"{pal}.png"
                chart_hud.render(ROWS, str(out), "2026-01-01", {"lang": "en", "palette": pal, "pace": 14})
                self.assertGreater(out.stat().st_size, 10000)


class ThemeOptInTests(unittest.TestCase):
    def test_ctx_theme_and_page(self):
        import argparse
        import usage_history as uh
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "data"
            data.mkdir()

            def ctx(palette, theme=None):
                a = argparse.Namespace(config=None, data_dir=str(data), tz="UTC", lang="en", theme=theme, pace=None,
                                       palette=palette, privacy=False, host=None, port=None)
                return uh.Ctx(a)
            self.assertTrue(ctx("colorblind").theme["patterns"])
            self.assertFalse(ctx("orange-teal").theme["patterns"])
            th = Path(d) / "t.json"
            th.write_text(json.dumps({"patterns": True}), encoding="utf-8")
            self.assertTrue(ctx("orange-teal", str(th)).theme["patterns"])
            src = Path(uh.__file__).read_text(encoding="utf-8")
            self.assertIn('html[data-patterns="1"] .badge:not(.note)', src)
            self.assertIn("dataset.patterns=(P.pf||P.table[n].patterns)", src)
            vsrc = (Path(uh.__file__).parent / "usage_view.py").read_text(encoding="utf-8")
            self.assertIn('html[data-patterns="1"] .val-over:not(.arr)::before', vsrc)


class ReadmeTests(unittest.TestCase):
    def test_docs(self):
        root = Path(__file__).resolve().parent.parent
        en = (root / "README.md").read_text(encoding="utf-8")
        zh = (root / "README.zh-TW.md").read_text(encoding="utf-8")
        self.assertIn("`#3C8FD6` / `#0072B2`", en)
        self.assertIn("`#3C8FD6` / `#0072B2`", zh)
        self.assertIn('"patterns": true', en)
        self.assertIn('"patterns": true', zh)
        self.assertIn("10–60 seconds", en)
        self.assertIn("10–60 秒", zh)


if __name__ == "__main__":
    unittest.main()
