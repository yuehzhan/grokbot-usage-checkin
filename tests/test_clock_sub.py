"""The line under the clock toolbar follows the Hours menu: 24H keeps the v1.5.1 sentence; AM / PM / Last 12 h say what the
left dial shows and that 12 (not 0:00) is at the top. Strings in every language, the page's per-state texts, and the
client-side switch (the real PALETTE_JS under node)."""
import ast
import html
import json
import os
import re
import shutil
import subprocess
import unittest

from _helpers import read_text, request, scratch, start_server, stop_server
from test_clock_palette import write_samples
from test_stale_page import NODE, run

import usage_history as uh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V151 = "5204d0e"  # Release v1.5.1

EN_24 = "% increase per hour ({z}); 0:00 at top, clockwise. Left: last 24 hours. Right: colour = day (or week / month / quarter), see the legend under the dial; length = % increase in that hour"
ZH_24 = "每小時用量增加（{z}），0 時在上、順時針。左圖：近 24 小時。右圖：顏色 = 不同天（或週／月／季），對照下方圖例；長度 = 該小時用量增加"


class StringTests(unittest.TestCase):
    def test_every_language_has_the_texts(self):
        for lang, L in uh.STR.items():
            self.assertEqual(set(L["clock_sl"]), {"am", "pm"}, lang)
            self.assertIn("{z}", L["clock_sub"] if lang != "numeric" else "{z}", lang)
            if lang == "numeric":
                self.assertEqual((L["clock_sh"], L["clock_sa"], L["clock_sub"]), ("", "", ""))
                continue
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", L["clock_sh"])), ["l", "z"], lang)
            self.assertEqual(re.findall(r"\{(\w+)\}", L["clock_sa"]), ["h"], lang)
            self.assertNotEqual(L["clock_sl"]["am"], L["clock_sl"]["pm"], lang)
            self.assertNotEqual(L["clock_sh"], L["clock_sub"], lang)

    def test_24h_text_is_the_v151_sentence(self):
        self.assertEqual(uh.STR["en"]["clock_sub"], EN_24)
        self.assertEqual(uh.STR["zh-TW"]["clock_sub"], ZH_24)
        out = uh.clock_sub_texts(uh.STR["en"], "UTC+8")
        self.assertEqual(out["24"], EN_24.format(z="UTC+8"))
        try:
            old = subprocess.run(["git", "show", f"{V151}:usage_history_l10n.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
            oldstr = ast.literal_eval(re.search(r"^STR = (\{.*?^\})\n", old, re.S | re.M).group(1))
        except Exception:
            self.skipTest("v1.5.1 not in this checkout")
        for lang, t in oldstr.items():
            self.assertEqual(uh.STR[lang]["clock_sub"], t["clock_sub"], lang)

    def test_half_texts_en_and_zh_tw(self):
        en = uh.clock_sub_texts(uh.STR["en"], "UTC+8")
        self.assertEqual(en["am"], "% increase per hour (UTC+8); 12 at top, clockwise. Left: AM 12 hours (0:00–12:00). Right: the same 12 hours, colour = day (or week / month / quarter), see the legend under the dial; length = % increase in that hour")
        self.assertIn("Left: PM 12 hours (12:00–24:00).", en["pm"])
        self.assertIn("Left: last 12 hours (AM 0:00–12:00).", en["auto-am"])
        self.assertIn("Left: last 12 hours (PM 12:00–24:00).", en["auto-pm"])
        zh = uh.clock_sub_texts(uh.STR["zh-TW"], "UTC+8")
        self.assertEqual(zh["am"], "每小時用量增加（UTC+8），12 時在上、順時針。左圖：上午 12 小時（0:00–12:00）。右圖：同樣的 12 小時，顏色 = 不同天（或週／月／季），對照下方圖例；長度 = 該小時用量增加")
        self.assertIn("左圖：下午 12 小時（12:00–24:00）。", zh["pm"])
        self.assertIn("左圖：近 12 小時（上午 0:00–12:00）。", zh["auto-am"])
        self.assertIn("左圖：近 12 小時（下午 12:00–24:00）。", zh["auto-pm"])
        for lang in uh.STR:  # no placeholder left over, 24H is never mentioned in a half text
            for k, t in uh.clock_sub_texts(uh.STR[lang], "Z").items():
                self.assertNotRegex(t, r"\{\w+\}", (lang, k))
                if k != "24" and lang != "numeric":
                    self.assertNotIn("24 ", t.replace("12:00–24:00", "").replace("24 Uhr", "").replace("24 h", "").replace("24:00", ""), (lang, k))
                    self.assertNotIn("0:00 at top", t)


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        write_samples(data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url = info["url"]

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def test_page_shows_24h_and_carries_all_states(self):
        page = request(self.url)[1].decode("utf-8")
        m = re.search(r"<div class='sub' id='clksub' data-s=\"([^\"]*)\">([^<]*)</div>", page)
        self.assertIsNotNone(m)
        subs = json.loads(html.unescape(m.group(1)))
        self.assertEqual(set(subs), {"24", "am", "pm", "auto-am", "auto-pm"})
        self.assertEqual(html.unescape(m.group(2)), subs["24"])  # first paint = the 24H sentence
        self.assertIn("Left: last 24 hours.", subs["24"])
        self.assertIn("Left: AM 12 hours (0:00–12:00).", subs["am"])
        self.assertEqual(len(set(subs.values())), 5)


@unittest.skipUnless(NODE, "node not installed")
class ClientSwitchTests(unittest.TestCase):
    ACT = ("var L = get('cldlist').opts, rec = function () { subs.push(get('clksub').textContent); };"
           "L[2].onclick(); setTimeout(rec, 30);"                          # AM
           "setTimeout(function () { L[3].onclick(); }, 40); setTimeout(rec, 70);"   # PM
           "setTimeout(function () { L[0].onclick(); }, 80); setTimeout(rec, 110);"  # back to 24 h
           "setTimeout(function () { L[1].onclick(); }, 120); setTimeout(rec, 150);")  # Last 12 h (the server says AM)

    def test_follows_the_hours_menu(self):
        r = run([200, 200, 200], act=self.ACT, reply=["am", "pm", "am"])
        self.assertEqual(r["subs"], ["TAM", "TPM", "T24", "TAUTOAM"])

    def test_each_state(self):
        for pick, reply, want in ((1, "am", "TAUTOAM"), (1, "pm", "TAUTOPM"), (2, "am", "TAM"), (3, "pm", "TPM")):
            r = run([200], act=f"get('cldlist').opts[{pick}].onclick();", reply=[reply])
            self.assertEqual(r["sub"], want, (pick, reply))
        r = run([200, 200], act="var L = get('cldlist').opts; L[2].onclick(); setTimeout(function () { L[0].onclick(); }, 30);", reply=["am"])
        self.assertEqual(r["sub"], "T24")  # back to 24H: the original sentence

    def test_failed_pick_keeps_the_text(self):
        r = run([500], act="get('cldlist').opts[2].onclick();")
        self.assertEqual(r["sub"], "T24")


if __name__ == "__main__":
    unittest.main()
