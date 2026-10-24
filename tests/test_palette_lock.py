"""Palette presets and the shared write lock."""
import os
import shutil
import threading
import time
import unittest

from _helpers import scratch

import usage_lock
import usage_palette as up

BANNED_NEGATIVE = {"#8B4513", "#A52A2A", "#000000", "#808080", "#FF0000", "#00FFFF", "#D62728"}


class PaletteTests(unittest.TestCase):
    def test_presets_and_default(self):
        self.assertEqual(up.names(), ["orange-teal", "orange-blue", "colorblind", "amber-purple", "pink-green", "gold-sky", "orange-purple",
                                      "magenta-teal", "yellow-indigo", "peach-mint", "tangerine-cyan", "rose-lime", "sunflower-navy",
                                      "high-contrast"])
        for n in up.names():
            for lang in ("en", "zh-TW", "zh-CN"):
                self.assertTrue(up.LABELS[n][lang], (n, lang))
        self.assertEqual(up.normalize("nonsense"), "orange-teal")
        self.assertEqual(up.normalize("Okabe-Ito"), "colorblind")
        self.assertEqual(up.get("colorblind")["under"], "#56B4E9")
        for n in up.names():
            for light in (False, True):
                p = up.get(n, light)
                self.assertEqual(set(p), {"over", "under", "baseline", "notice"})
                self.assertNotIn(p["under"].upper(), BANNED_NEGATIVE)
                self.assertNotIn(p["over"].upper(), BANNED_NEGATIVE)

    def test_arrows(self):
        self.assertEqual(up.arrow(3), "▲")
        self.assertEqual(up.arrow(-2), "▼")
        self.assertTrue(up.fmt_delta(5).startswith("▲"))

    def test_css_and_js(self):
        self.assertIn("--under", up.css_vars("orange-blue"))
        self.assertIn("colorblind", up.js_table())


class LockTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_reentrant_and_exclusive(self):
        with usage_lock.write_lock(self.dir):
            with usage_lock.write_lock(self.dir):  # same thread: no deadlock
                pass
        self.assertTrue(os.path.exists(usage_lock.lock_path_for(self.dir)))
        order = []

        def other():
            with usage_lock.write_lock(self.dir):
                order.append("other")

        with usage_lock.write_lock(self.dir):
            t = threading.Thread(target=other)
            t.start()
            time.sleep(0.3)
            order.append("first")
        t.join(5)
        self.assertEqual(order, ["first", "other"])


if __name__ == "__main__":
    unittest.main()
