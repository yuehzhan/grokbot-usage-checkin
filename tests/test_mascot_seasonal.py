"""Seasonal accessories on the built-in title mascot: date windows (incl. boundaries and the New Year wrap), one
accessory per window, the Seasonal auto / off setting, privacy, never on the custom mascot.svg, reduced motion.

Everything runs in scratch dirs with made-up EXAMPLE readings; nothing touches real data.
"""
import json
import os
import re
import shutil
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server
from test_clock_palette import write_samples
from test_gauge_anim import write_week
from test_mascot import GOOD_SVG, make_ctx

import usage_history as uh  # noqa: E402

WINDOWS = {"newyear": ((12, 31), (1, 2)), "valentine": ((2, 13), (2, 15)), "childrensday": ((5, 1), (5, 5)),
           "halloween": ((10, 25), (11, 1)), "christmas": ((12, 20), (12, 26))}
# one day inside each window, plus days just outside
INSIDE = {"newyear": (2027, 1, 1), "valentine": (2027, 2, 14), "childrensday": (2027, 5, 3),
          "halloween": (2026, 10, 31), "christmas": (2026, 12, 25)}
OUTSIDE = [(2026, 12, 30), (2027, 1, 3), (2027, 2, 12), (2027, 2, 16), (2027, 4, 30), (2027, 5, 6),
           (2026, 10, 24), (2026, 11, 2), (2026, 12, 19), (2026, 12, 27), (2026, 7, 1), (2026, 3, 15)]


def at(y, m, d, h=12):
    return lambda self: datetime(y, m, d, h, 0, tzinfo=timezone.utc)


class SeasonDates(unittest.TestCase):
    def test_windows_table(self):
        self.assertEqual({n: (a, b) for n, a, b in uh.MASCOT_SEASONS}, WINDOWS)
        self.assertEqual(set(uh.MASCOT_ACCESSORIES), set(WINDOWS))

    def test_boundaries(self):
        for name, (a, b) in WINDOWS.items():
            for y in (2026, 2027, 2028):  # 2028: leap year
                first, last = date(y, *a), date(y, *b)
                self.assertEqual(uh.mascot_season(first), name, first)
                self.assertEqual(uh.mascot_season(last), name, last)
                self.assertIsNone(uh.mascot_season(first - timedelta(days=1)), first - timedelta(days=1))
                self.assertIsNone(uh.mascot_season(last + timedelta(days=1)), last + timedelta(days=1))

    def test_new_year_wraps(self):
        for d in (date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2), date(2028, 12, 31), date(2029, 1, 1)):
            self.assertEqual(uh.mascot_season(d), "newyear", d)
        self.assertEqual(uh.mascot_season(date(2026, 12, 30)), None)
        self.assertEqual(uh.mascot_season(date(2027, 1, 3)), None)
        self.assertEqual(uh.mascot_season(date(2026, 12, 26)), "christmas")  # the two December windows don't touch

    def test_whole_year_one_accessory_per_day(self):
        d, seen = date(2027, 1, 1), {}
        while d.year == 2027:
            s = uh.mascot_season(d)
            if s:
                seen[s] = seen.get(s, 0) + 1
            d += timedelta(days=1)
        self.assertEqual(seen, {"newyear": 3, "valentine": 3, "childrensday": 5, "halloween": 8, "christmas": 7})

    def test_datetime_and_display_zone(self):
        # 2026-12-31 15:30 UTC is still Dec 31 in New York but already Jan 1 in Taipei: both New Year;
        # 2026-12-30 20:00 UTC is Dec 30 in UTC (no accessory) but Dec 31 in Taipei (New Year)
        inst = datetime(2026, 12, 30, 20, 0, tzinfo=timezone.utc)
        self.assertIsNone(uh.mascot_season(inst))
        data = scratch()
        try:
            class FakeDT(datetime):
                @classmethod
                def now(cls, tz=None):
                    return inst.astimezone(tz) if tz else inst.replace(tzinfo=None)
            with mock.patch.object(uh, "datetime", FakeDT):
                tpe = make_ctx(data)
                tpe.tz = tpe._tz("Asia/Taipei")  # the display zone (page Time zone menu / --tz)
                self.assertEqual(uh.mascot_accessory(tpe), "newyear")
                utc = make_ctx(data)
                self.assertIsNone(uh.mascot_accessory(utc))
        finally:
            shutil.rmtree(data)

    def test_setting_values(self):
        self.assertEqual(uh.SEASONAL_DEFAULT, "auto")
        self.assertEqual(uh.SEASONAL_CHOICES, ("auto", "off"))
        for v, want in ((None, "auto"), ("", "auto"), ("AUTO", "auto"), ("off", "off"), (" Off ", "off"),
                        (False, "off"), ("none", "off"), ("hidden", "off"), (True, "auto"), ("on", "auto"),
                        ("christmas", "auto"), ("<x>", "auto")):
            self.assertEqual(uh.page_seasonal(v), want, v)


class SeasonalPageTests(unittest.TestCase):
    def setUp(self):
        self.data = scratch()
        write_samples(self.data, days=8)
        write_week(self.data)
        self.settings({"mascot": "teal"})

    def tearDown(self):
        shutil.rmtree(self.data)

    def settings(self, d):
        with open(os.path.join(self.data, uh.PAGE_SETTINGS), "w", encoding="utf-8") as fh:
            json.dump(d, fh)

    def page(self, when, **kw):
        with mock.patch.object(uh.Ctx, "now", at(*when)):
            uh.rewrite_page(make_ctx(self.data, **kw))
        return read_text(os.path.join(self.data, "history", "index.html"))

    def button(self, page):
        return re.search(r'<button type="button" class="mascot" id="mascot"[^>]*>.*?</button>', page, re.S).group(0)

    def accessories(self, page):
        return re.findall(r'<svg class="macc" data-s="(\w+)"', page)

    def test_each_accessory_only_in_its_window(self):
        for name, when in INSIDE.items():
            page = self.page(when)
            btn = self.button(page)
            self.assertEqual(self.accessories(page), [name], when)
            self.assertIn(f'data-s="{name}"', btn.split(">", 1)[0])
            self.assertIn(uh.MASCOT_SVG + uh.mascot_accessory_svg(name), btn)  # laid over the unchanged blob
            for other in set(WINDOWS) - {name}:
                self.assertNotIn(uh.MASCOT_ACCESSORIES[other], page, (name, other))
        for when in OUTSIDE:
            page = self.page(when)
            self.assertEqual(self.accessories(page), [], when)
            self.assertNotIn('class="macc"', page, when)
            self.assertNotIn(" data-s=", self.button(page).split(">", 1)[0])

    def test_boundaries_on_the_page(self):
        for name, (a, b) in WINDOWS.items():
            y = 2026
            first = date(y, *a)
            last = date(y + 1 if b < a else y, *b)
            for d, want in ((first, [name]), (last, [name]), (first - timedelta(days=1), []), (last + timedelta(days=1), [])):
                self.assertEqual(self.accessories(self.page((d.year, d.month, d.day))), want, (name, d))

    def test_off_setting(self):
        self.settings({"mascot": "teal", "seasonal": "off"})
        for when in INSIDE.values():
            page = self.page(when)
            self.assertEqual(self.accessories(page), [])
            self.assertIn("<option value='off' selected>Off</option>", re.search(r'<select id="seasonsel".*?</select>', page, re.S).group(0))
        self.settings({"mascot": "teal", "seasonal": "auto"})
        self.assertEqual(self.accessories(self.page(INSIDE["christmas"])), ["christmas"])

    def test_menu(self):
        page = self.page(INSIDE["halloween"])
        m = re.search(r'<label class="pal" id="seasonl">Seasonal <select id="seasonsel"([^>]*)>(.*?)</select></label>', page, re.S)
        self.assertIsNotNone(m)
        for a in ('data-api="/api/seasonal"', 'data-k="usage.seasonal"', 'data-f="seasonal"'):
            self.assertIn(a, m.group(1))
        self.assertNotIn("data-live", m.group(1))  # drawn at build time: the change reloads the rebuilt page
        self.assertEqual(re.findall(r"<option value='(\w+)'", m.group(2)), ["auto", "off"])
        self.assertIn("<option value='auto' selected>Auto (by date)</option>", m.group(2))
        # right after the Mascot menu on toolbar row 2
        self.assertLess(page.index('id="mascotsel"'), page.index('id="seasonsel"'))
        self.assertLess(page.index('id="seasonsel"'), page.index('id="applying"'))
        zh = self.page(INSIDE["halloween"], lang="zh-TW")
        self.assertIn('id="seasonl">節日裝扮 <select', zh)
        self.assertIn(">自動（依日期）</option>", zh)
        for lk, S in uh.MASCOT_STR.items():
            self.assertEqual(set(S["sopts"]), set(uh.SEASONAL_CHOICES), lk)
            self.assertTrue(S["seasonal"], lk)

    def test_hidden_with_mascot_off(self):
        self.settings({})  # mascot off by default
        page = self.page(INSIDE["christmas"])
        tag = self.button(page).split(">", 1)[0]
        self.assertIn(" hidden", tag)  # the accessory sits inside the hidden button
        self.assertRegex(page, r'<label class="pal" id="seasonl" hidden>')
        self.assertIn("l.hidden=v==='off'||v==='custom'", uh.MASCOT_JS)  # the Mascot menu hides the Seasonal menu at once

    def test_privacy_hides_it(self):
        for when in INSIDE.values():
            page = self.page(when, privacy=True)
            for s in ('id="mascot"', 'class="macc"', 'id="seasonsel"', 'id="seasonl"'):
                self.assertNotIn(s, page)
            self.assertIsNone(uh.mascot_accessory(make_ctx(self.data, privacy=True)))

    def test_not_on_custom_image(self):
        with open(os.path.join(self.data, uh.MASCOT_FILE), "w", encoding="utf-8") as fh:
            fh.write(GOOD_SVG)
        self.settings({"mascot": "custom"})
        page = self.page(INSIDE["halloween"])
        btn = self.button(page)
        self.assertIn('data-c="custom"', btn)
        self.assertIn('<img class="mcustom"', btn)
        # the overlay is an svg in the button, and every svg there is hidden while Custom is picked
        self.assertIn(".mascot[data-c=custom] svg{display:none}", uh.mascot_css())
        self.assertNotIn("macc", btn.split('<img class="mcustom"', 1)[1])  # nothing drawn over or after the image
        self.assertRegex(page, r'<label class="pal" id="seasonl" hidden>')

    def test_reduced_motion_and_blink(self):
        css = uh.mascot_css()
        self.assertIn(".mascot .mspark{transform-origin:center;animation:mspark", css)
        self.assertIn(".mascot .mwave{transform-origin:left center;animation:mwave", css)
        self.assertIn("@media (prefers-reduced-motion: reduce){.mascot .mspark,.mascot .mwave{animation:none!important;transition:none!important}}", css)
        self.assertIn("@media (prefers-reduced-motion: reduce){.mascot .meye,.mascot.blink .meye{animation:none!important", css)  # unchanged
        # the overlay never takes the click / hover, so blinking keeps working; the eyes stay in the blob svg
        self.assertIn(".mascot svg.macc{position:absolute;left:0;top:0;pointer-events:none}", css)
        for name, art in uh.MASCOT_ACCESSORIES.items():
            self.assertNotIn("meye", art, name)
        self.assertIn("b.addEventListener('click',blink)", uh.MASCOT_JS)
        self.assertIn(".mascot.blink .meye{animation:mblink", css)
        self.assertIn('class="mspark"', uh.MASCOT_ACCESSORIES["newyear"])
        self.assertIn('class="mwave"', uh.MASCOT_ACCESSORIES["childrensday"])
        for name in ("valentine", "halloween", "christmas"):  # static art
            self.assertNotIn("mspark", uh.MASCOT_ACCESSORIES[name])
            self.assertNotIn("mwave", uh.MASCOT_ACCESSORIES[name])

    def test_art_is_self_contained(self):
        for name, art in uh.MASCOT_ACCESSORIES.items():
            svg = uh.mascot_accessory_svg(name)
            self.assertTrue(svg.startswith(f'<svg class="macc" data-s="{name}" viewBox="0 0 40 40" aria-hidden="true" focusable="false">'))
            for bad in ("http", "://", "url(", "<image", "<img", "href", "src=", "<script", "<text", "var(--"):
                self.assertNotIn(bad, svg, (name, bad))  # fixed colours, no theme variables, no external assets
            self.assertEqual(svg.count("<svg"), 1)
            self.assertTrue(re.search(r'fill="#[0-9A-F]{6}"|stroke="#[0-9A-F]{6}"', art), name)
        # fixed signature colours
        self.assertIn("#FF8A1F", uh.MASCOT_ACCESSORIES["halloween"])   # orange pumpkin
        self.assertIn("#E0303A", uh.MASCOT_ACCESSORIES["christmas"])   # red hat ...
        self.assertIn("#FFFFFF", uh.MASCOT_ACCESSORIES["christmas"])   # ... white brim
        self.assertIn("#FF4D73", uh.MASCOT_ACCESSORIES["valentine"])   # pink-red heart
        self.assertIn("#E8960E", uh.MASCOT_ACCESSORIES["newyear"])     # classic gold fireworks ...
        for c in ("#FF4D8D", "#FF5FA2", "#9B5CFF", "#B38CFF", "#33C4F5", "#3CC8F0", "#29B6F6"):
            self.assertNotIn(c, uh.MASCOT_ACCESSORIES["newyear"])      # ... no pink / purple / cyan (read as flowers)
        self.assertIn("#F0453A", uh.MASCOT_ACCESSORIES["childrensday"])  # red carp
        self.assertIn("#2F7CF0", uh.MASCOT_ACCESSORIES["childrensday"])  # blue carp


class SeasonalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(cls.data)
        write_samples(cls.data, days=8)
        write_week(cls.data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", cls.data, "--port", "0"],
                                      os.path.join(cls.data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def _post(self, body, token=True):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        if token:
            h["X-Usage-Token"] = self.token
        code, raw = request(self.url + "api/seasonal", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_seasonal_endpoint(self):
        for bad in ({"seasonal": "on"}, {"seasonal": "christmas"}, {"seasonal": 1}, {}):
            self.assertEqual(self._post(bad)[0], 400, bad)
        self.assertEqual(self._post({"seasonal": "off"}, token=False)[0], 403)
        self.assertEqual(request(self.url + "api/seasonal")[0], 405)
        code, j = self._post({"seasonal": "off"})
        self.assertEqual((code, j.get("seasonal")), (200, "off"))
        self.assertEqual(json.loads(read_text(os.path.join(self.data, "page_settings.json")))["seasonal"], "off")
        page = read_text(os.path.join(self.data, "history", "index.html"))
        self.assertIn("<option value='off' selected>Off</option>", page)
        self.assertNotIn('class="macc"', page)
        code, j = self._post({"seasonal": "auto"})
        self.assertEqual((code, j.get("seasonal")), (200, "auto"))
        self.assertEqual(json.loads(read_text(os.path.join(self.data, "page_settings.json")))["seasonal"], "auto")


if __name__ == "__main__":
    unittest.main()
