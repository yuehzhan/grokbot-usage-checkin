"""每日增量 log list sort toggle: 最舊在上 (default, the list as it always was) / 最新在上 (reversed), per day and per hour.

The list is drawn into the picture, so the newest-first list is a second set of the same pictures. These tests check the
picture side (both orders, both views, the selection geometry and the mask in both, strings in every language), the page
(four views, the toggle, default order, files) and the choice's persistence (WKVIEW_JS run with node). Synthetic data only."""
import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from _helpers import read_text, scratch
from test_clock_palette import make_ctx, write_samples
from test_day_select import DAILY_ROWS, composite, pinned_clock, px, render

import chart_hud
import usage_history as uh


def ys(meta):
    """Top edge (% of the picture) of every list row, in item order (day / hour index)."""
    return [r[1] if r else None for r in meta["sel"]["rows"]]


class StringTests(unittest.TestCase):
    def test_every_language_has_both_labels(self):
        self.assertEqual((chart_hud.BASE_STRINGS["order_old"], chart_hud.BASE_STRINGS["order_new"]), ("Oldest first", "Newest first"))
        self.assertEqual((chart_hud.TRANSLATIONS["zh-TW"]["order_old"], chart_hud.TRANSLATIONS["zh-TW"]["order_new"]), ("最舊在上", "最新在上"))
        for lang, tab in chart_hud.TRANSLATIONS.items():
            self.assertTrue(tab.get("order_old") and tab.get("order_new"), lang)
            self.assertNotEqual(tab["order_old"], tab["order_new"], lang)
            if lang != "numeric":
                self.assertNotEqual(tab["order_old"], "Oldest first", f"{lang} is not translated")

    def test_privacy_numeric_labels_are_symbols(self):
        n = chart_hud.TRANSLATIONS["numeric"]
        for k in ("order_old", "order_new"):
            self.assertIsNone(re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", n[k]), n[k])

    def test_toggle_shows_the_current_order_with_an_arrow(self):
        L = chart_hud.BASE_STRINGS
        self.assertEqual(chart_hud.order_text(L, "old"), "↓ Oldest first")
        self.assertEqual(chart_hud.order_text(L, "new"), "↑ Newest first")
        N = chart_hud.TRANSLATIONS["numeric"]
        self.assertEqual((chart_hud.order_text(N, "old"), chart_hud.order_text(N, "new")), ("↓", "↑"))

    def test_hotspot_is_small_and_inside_the_log_card_for_every_language(self):
        for lang in ["en"] + list(chart_hud.TRANSLATIONS):
            L = dict(chart_hud.BASE_STRINGS, **chart_hud.TRANSLATIONS.get(lang, {}))
            l, t, w, h = chart_hud.hotspots(L)["order"]
            self.assertTrue(70 < l and l + w < 98 and 0 < t < 40, (lang, l, t, w, h))  # the right-hand card, in its header
            self.assertLess(w, 16, lang)  # small
            self.assertLess(h, 4, lang)


class PictureOrderTests(unittest.TestCase):
    def setUp(self):
        self.d = scratch()
        self.addCleanup(shutil.rmtree, self.d)

    def test_daily_default_is_newest_first_and_old_reverses_it(self):
        _, default = render("daily", self.d, name="a", sel_sprites=True)
        _, new = render("daily", self.d, name="c", sel_sprites=True, list_order="new")
        _, old = render("daily", self.d, name="b", sel_sprites=True, list_order="old")
        self.assertEqual(default["sel"]["rows"], new["sel"]["rows"])
        a, b = ys(old), ys(new)
        self.assertEqual(a, sorted(a))  # day 1 at the top
        self.assertEqual(b, sorted(b, reverse=True))  # the last day at the top
        self.assertEqual(sorted(a), sorted(b))  # the same seven slots
        self.assertEqual(new["sel"]["i"], old["sel"]["i"])  # today is the same day in both
        self.assertEqual(len(set(map(tuple, new["sel"]["rows"]))), 7)
        self.assertEqual((default["sel"]["order"], new["sel"]["order"], old["sel"]["order"]), ("new", "new", "old"))
        self.assertEqual(np.abs(px(os.path.join(self.d, "a-daily.png")) - px(os.path.join(self.d, "c-daily.png"))).max(), 0)
        self.assertGreater(np.abs(px(os.path.join(self.d, "b-daily.png")) - px(os.path.join(self.d, "c-daily.png"))).max(), 0)

    def test_hourly_default_is_newest_first_and_old_reverses_it(self):
        _, default = render("hourly", self.d, name="a", sel_sprites=True)
        _, old = render("hourly", self.d, name="b", sel_sprites=True, list_order="old")
        _, new = render("hourly", self.d, name="c", sel_sprites=True, list_order="new")
        a, b = ys(old), ys(new)
        self.assertEqual(a, sorted(a))  # the oldest hour at the top
        self.assertEqual(b, sorted(b, reverse=True))  # the newest hour at the top
        self.assertEqual(ys(default), b)
        self.assertEqual(old["sel"]["i"], new["sel"]["i"])  # the default selection is still the newest hour
        self.assertEqual(old["sel"]["i"], len(old["sel"]["rows"]) - 1)
        self.assertEqual((default["sel"]["order"], old["sel"]["order"], new["sel"]["order"]), ("new", "old", "new"))
        for f in "abc":
            self.assertTrue(os.path.isfile(os.path.join(self.d, f"{f}-hourly-rows.png")), f)
        s0, s1, s2 = (px(os.path.join(self.d, f"{f}-hourly-rows.png")) for f in "abc")
        self.assertEqual(s1.shape, s2.shape)
        self.assertEqual(np.abs(s0 - s2).max(), 0)  # the default strip is the newest-first one
        self.assertGreater(np.abs(s1 - s2).max(), 0)

    def test_unknown_order_means_newest_first(self):
        _, a = render("daily", self.d, name="a", sel_sprites=True, list_order="sideways")
        _, b = render("daily", self.d, name="b", sel_sprites=True)
        self.assertEqual(a["sel"]["rows"], b["sel"]["rows"])
        self.assertEqual(a["sel"]["order"], "new")

    def test_overview_list_is_not_reordered(self):
        a, _ = render("week", self.d, name="a")
        b, _ = render("week", self.d, name="b", list_order="new")
        self.assertEqual(np.abs(px(a) - px(b)).max(), 0)

    def test_the_toggle_is_drawn_in_the_picture_only_in_the_two_lists(self):
        for view in ("daily", "hourly"):
            a, _ = render(view, self.d, name="o", list_order="old")
            b, _ = render(view, self.d, name="n")
            im1, im2 = px(a), px(b)
            h, w = im1.shape[:2]
            l, t, rw, rh = chart_hud.hotspots(chart_hud.BASE_STRINGS)["order"]
            box = (slice(int(h * t / 100) - 2, int(h * (t + rh) / 100) + 3), slice(int(w * l / 100) - 2, int(w * (l + rw) / 100) + 3))
            self.assertGreater(np.abs(im1[box] - im2[box]).max(), 40, view)  # the label / arrow changes inside its own box

    def test_selection_mask_follows_the_rows_in_newest_first(self):
        """default picture + sprites clipped to column k and card k == the picture drawn with day k selected (daily, reversed)."""
        _, meta = render("daily", self.d, sel_sprites=True, list_order="new")
        for k in (0, 3, 6):
            d2 = scratch()
            self.addCleanup(shutil.rmtree, d2)
            want, m = render("daily", d2, sel_day=k + 1, list_order="new")
            got, ref = composite(self.d, "daily", meta, k), px(want)
            wrong = (np.abs(got - ref).max(axis=2) > 6).mean()
            self.assertLess(wrong, 0.0005, f"day {k + 1}: {wrong:.5f} of the pixels differ")
            self.assertEqual(m["sel"]["i"], k)

    def test_today_highlight_is_the_selected_row_in_both_orders(self):
        for order in ("old", "new"):
            d = scratch()
            self.addCleanup(shutil.rmtree, d)
            _, meta = render("daily", d, sel_sprites=True, list_order=order)
            i = meta["sel"]["i"]
            plain = px(render("daily", d, name="x", list_order=order)[0])
            marked = px(render("daily", d, name="y", sel_day=(i + 2 if i < 6 else 1), list_order=order)[0])
            h, w = plain.shape[:2]
            r = meta["sel"]["rows"][i]
            box = (slice(int(h * r[1] / 100), int(h * (r[1] + r[3]) / 100)), slice(int(w * r[0] / 100), int(w * (r[0] + r[2]) / 100)))
            self.assertGreater(np.abs(plain[box] - marked[box]).max(), 30, order)  # the outline sits at today's own row

    def test_every_language_renders_both_orders_without_the_pill_covering_the_title(self):
        for lang in ["en"] + [k for k in chart_hud.TRANSLATIONS if k != "numeric"]:
            L = dict(chart_hud.BASE_STRINGS, **chart_hud.TRANSLATIONS.get(lang, {}))
            _, meta = render("daily", self.d, name=lang, lang=lang, list_order="new")
            self.assertEqual(meta["order"], "new", lang)
            box = chart_hud.hotspots(L)["order"]
            self.assertGreater(box[0], 70, lang)

    def test_small_samples_render_in_both_orders(self):
        for order in ("old", "new"):
            out = os.path.join(self.d, f"{order}.png")
            with pinned_clock():
                chart_hud.render(DAILY_ROWS[:1], out, "2026-10-24", {"lang": "zh-TW", "view": "daily", "tz": "UTC", "list_order": order, "sel_sprites": True})
            self.assertTrue(os.path.isfile(out))


class PageTests(unittest.TestCase):
    def setUp(self):
        self.data = scratch()
        self.addCleanup(shutil.rmtree, self.data)

    def page(self, **kw):
        write_samples(self.data)
        d0 = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        with open(os.path.join(self.data, f"week-{d0}.csv"), "w", encoding="utf-8") as fh:
            fh.write("day,date,pct,note\n1,%s,9,\n" % d0)
        self.ctx = make_ctx(self.data, **kw)
        uh.render_history(self.ctx)
        self.hist = os.path.join(self.data, "history")
        return read_text(os.path.join(self.hist, "index.html"))

    @staticmethod
    def wrap(page, v):
        return re.search(r"<div class='gwrap' data-wv='%s' hidden>(.*?)(?=data-wv=|$)" % v, page, re.S).group(1)

    def test_four_views_each_with_the_toggle(self):
        page = self.page()
        self.assertEqual(re.findall(r"data-wv='([a-z-]+)'", page), ["week", "daily", "hourly", "daily-new", "hourly-new"])
        for v in ("daily", "hourly", "daily-new", "hourly-new"):
            w = self.wrap(page, v)
            m = re.findall(r"<button type='button' class='hot ord' data-go='order' aria-pressed='(\w+)' aria-label='([^']*)'", w)
            self.assertEqual(m, [("true" if v.endswith("-new") else "false", "Newest first")], v)
        self.assertNotIn("data-go='order'", re.search(r"data-wv='week'>(.*?)(?=data-wv='daily')", page, re.S).group(1))  # not on the overview
        self.assertIn("week-daily-new.png", self.wrap(page, "daily-new"))
        self.assertIn("week-hourly-new.png", self.wrap(page, "hourly-new"))

    def test_new_pictures_and_sprites_are_published_for_every_palette(self):
        self.page()
        files = list(uh.NEW_VIEW_FILES) + [f for f in uh.SEL_SPRITES if "-new-" in f] + [uh.HOURLY_ROWS_FILE_NEW]
        self.assertEqual(len([f for f in uh.SEL_SPRITES if "-new-" in f]), 4)
        for f in files:
            self.assertTrue(os.path.isfile(os.path.join(self.hist, f)), f)
            for pd in os.listdir(os.path.join(self.hist, "pal")):
                self.assertTrue(os.path.isfile(os.path.join(self.hist, "pal", pd, f)), (pd, f))

    def test_geometry_is_per_order(self):
        self.page()
        sel = json.loads(read_text(os.path.join(self.hist, "week-current.json")))["sel"]
        self.assertEqual(sorted(sel), ["daily", "daily-new", "hourly", "hourly-new"])
        for v in ("daily", "hourly"):
            self.assertEqual(sel[v]["i"], sel[v + "-new"]["i"])
            a, b = [r[1] for r in sel[v]["rows"]], [r[1] for r in sel[v + "-new"]["rows"]]
            self.assertEqual(a, sorted(a))
            self.assertEqual(b, sorted(b, reverse=True))

    def test_row_buttons_follow_the_list_top_to_bottom(self):
        page = self.page()
        for v, cls in (("daily", "dcard"), ("daily-new", "dcard"), ("hourly", "hrow"), ("hourly-new", "hrow")):
            w = self.wrap(page, v)
            tops = [float(t) for t in re.findall(r"class='dsel %s' [^>]*style='[^']*top:([\d.]+)%%" % cls, w)]
            self.assertTrue(len(tops) >= 5, v)
            self.assertEqual(tops, sorted(tops), v)  # Tab order = what is seen, top to bottom

    def test_labels_follow_the_page_language_and_privacy_has_none(self):
        page = self.page(lang="zh-TW")
        self.assertIn("aria-label='最新在上'", self.wrap(page, "daily"))
        self.assertNotRegex(self.page(privacy=True).split("data-wv='daily-new'")[1], r"aria-label='[A-Za-z ]*(Newest|Oldest)")

    def test_no_toggle_without_the_newest_first_pictures(self):
        self.page()
        for f in uh.NEW_VIEW_FILES:
            os.remove(os.path.join(self.hist, f))
        uh.rewrite_page(self.ctx)
        page = read_text(os.path.join(self.hist, "index.html"))
        self.assertNotIn("data-go='order'", page)
        self.assertEqual(re.findall(r"data-wv='([a-z-]+)'", page), ["week", "daily", "hourly"])


STUB = r"""
var store = %(store)s;
global.localStorage = {getItem: function (k) { return k in store ? store[k] : null; }, setItem: function (k, v) { store[k] = String(v); }};
function Btn(go, pressed) { this.go = go; this.pressed = pressed; this.focused = false; }
Btn.prototype.getAttribute = function (k) { return k === 'data-go' ? this.go : k === 'aria-pressed' ? this.pressed : null; };
Btn.prototype.focus = function () { this.focused = true; };
Btn.prototype.closest = function () { return this; };
function Card(i, pressed) { this.i = i; this.clicked = 0; this.pressed = pressed; }
Card.prototype.getAttribute = function (k) { return k === 'data-i' ? String(this.i) : null; };
Card.prototype.click = function () { this.clicked++; };
function Wrap(v, sel) { this.v = v; this.hidden = true; this.cards = [0, 1, 2, 3].map(function (i) { return new Card(i, i === sel); }); this.btns = {}; }
Wrap.prototype.getAttribute = function (k) { return k === 'data-wv' ? this.v : null; };
Wrap.prototype.btn = function (go) { return this.btns[go] || (this.btns[go] = new Btn(go, 'false')); };
Wrap.prototype.querySelector = function (s) {
  var m;
  if ((m = /^\[data-i\]\[aria-pressed='true'\]$/.exec(s))) return this.cards.filter(function (c) { return c.pressed; })[0] || null;
  if ((m = /^\[data-i='(\d+)'\]$/.exec(s))) return this.cards[+m[1]];
  if ((m = /^button\.hot(\[aria-pressed='true'\])?\[data-go='(\w+)'\]$/.exec(s))) return this.btn(m[2]);
  return null;
};
var names = %(names)s, W = names.map(function (v) { return new Wrap(v, %(sel)s); });
var handler = null;
var sec = {querySelectorAll: function (s) { return s === '[data-wv]' ? W : []; }, getAttribute: function () { return null; },
  querySelector: function (s) { var m = /^\[data-wv='([\w-]+)'\]$/.exec(s); return m ? W.filter(function (w) { return w.v === m[1]; })[0] || null : null; },
  addEventListener: function (t, f) { if (t === 'click') handler = f; }};
global.document = {getElementById: function (id) { return id === 'p-wk' ? sec : null; }};
%(js)s
function shown() { return W.filter(function (w) { return !w.hidden; }).map(function (w) { return w.v; }); }
function click(go, pressed) { handler({target: {closest: function () { return new Btn(go, pressed || 'false'); }}}); }
var out = {};
%(steps)s
console.log(JSON.stringify(out));
"""
VIEWS = ["week", "daily", "hourly", "daily-new", "hourly-new"]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PersistenceTests(unittest.TestCase):
    def run_js(self, steps, store=None, names=VIEWS, sel=2):
        js = STUB % {"store": json.dumps(store or {}), "names": json.dumps(names), "sel": sel, "js": uh.WKVIEW_JS, "steps": steps}
        r = subprocess.run([shutil.which("node"), "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_default_is_newest_first_for_both_views(self):
        out = self.run_js("out.a = shown(); out.s = store;", {"usage.wkTab": "inc"})
        self.assertEqual(out["a"], ["daily-new"])
        self.assertNotIn("usage.listOrder", out["s"])  # nothing is stored until the reader chooses
        out = self.run_js("out.a = shown(); out.s = store;", {"usage.wkTab": "inc", "usage.incUnit": "hourly"})
        self.assertEqual(out["a"], ["hourly-new"])

    def test_stored_oldest_first_is_restored_on_load_for_both_views(self):
        for unit in ("daily", "hourly"):
            out = self.run_js("out.a = shown();", {"usage.wkTab": "inc", "usage.incUnit": unit, "usage.listOrder": "old"})
            self.assertEqual(out["a"], [unit])
            out = self.run_js("out.a = shown();", {"usage.wkTab": "inc", "usage.incUnit": unit, "usage.listOrder": "new"})
            self.assertEqual(out["a"], [unit + "-new"])

    def test_unknown_stored_value_means_newest_first(self):
        out = self.run_js("out.a = shown();", {"usage.wkTab": "inc", "usage.listOrder": "sideways"})
        self.assertEqual(out["a"], ["daily-new"])

    def test_toggle_flips_the_view_and_saves_the_choice(self):
        out = self.run_js("out.a = shown(); click('order', 'true'); out.b = shown(); out.s1 = store['usage.listOrder']; click('order');"
                          "out.c = shown(); out.s2 = store['usage.listOrder']; out.f = W[3].btn('order').focused;", {"usage.wkTab": "inc"})
        self.assertEqual((out["a"], out["b"], out["c"]), (["daily-new"], ["daily"], ["daily-new"]))
        self.assertEqual((out["s1"], out["s2"]), ("old", "new"))
        self.assertTrue(out["f"])  # keyboard focus stays on the toggle

    def test_choice_survives_a_reload(self):
        out = self.run_js("click('order', 'true'); out.s = store;", {"usage.wkTab": "inc", "usage.incUnit": "hourly"})
        self.assertEqual(out["s"]["usage.listOrder"], "old")
        again = self.run_js("out.a = shown();", out["s"])  # the next page load reads what was stored
        self.assertEqual(again["a"], ["hourly"])

    def test_unit_switch_keeps_the_order_and_stores_the_plain_unit(self):
        out = self.run_js("click('hourly'); out.a = shown(); out.u = store['usage.incUnit']; click('daily'); out.b = shown();"
                          "click('week'); out.c = shown(); click('inc'); out.d = shown(); out.t = store['usage.wkTab'];", {"usage.wkTab": "inc"})
        self.assertEqual(out["a"], ["hourly-new"])
        self.assertEqual(out["u"], "hourly")  # the unit key is unchanged by the order (same key as before)
        self.assertEqual((out["b"], out["c"], out["d"]), (["daily-new"], ["week"], ["daily-new"]))
        out = self.run_js("click('hourly'); out.a = shown(); click('daily'); out.b = shown(); click('week'); click('inc'); out.d = shown();",
                          {"usage.wkTab": "inc", "usage.listOrder": "old"})
        self.assertEqual((out["a"], out["b"], out["d"]), (["hourly"], ["daily"], ["daily"]))

    def test_the_chosen_day_stays_chosen_in_the_other_order(self):
        out = self.run_js("click('order', 'true'); out.n = W[3].cards.map(function (c) { return c.clicked; }); out.m = W[1].cards.map(function (c) { return c.clicked; });",
                          {"usage.wkTab": "inc"}, sel=2)
        self.assertEqual(out["n"], [0, 0, 0, 0])
        self.assertEqual(out["m"], [0, 0, 1, 0])

    def test_without_the_newest_first_pictures_the_stored_order_is_ignored(self):
        out = self.run_js("out.a = shown();", {"usage.wkTab": "inc", "usage.listOrder": "new"}, names=["week", "daily", "hourly"])
        self.assertEqual(out["a"], ["daily"])
        out = self.run_js("out.a = shown();", {"usage.wkTab": "inc"}, names=["week", "daily", "hourly"])
        self.assertEqual(out["a"], ["daily"])

    def test_uses_the_existing_localstorage_namespace(self):
        self.assertIn("'usage.listOrder'", uh.WKVIEW_JS)
        self.assertIn("'usage.incUnit'", uh.WKVIEW_JS)
        self.assertNotIn("sessionStorage", uh.WKVIEW_JS)


if __name__ == "__main__":
    unittest.main()
