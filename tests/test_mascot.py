"""Title mascot: the inline SVG blob next to the page title, its colour menu, privacy / Off, blink + reduced motion.

Everything runs in scratch dirs with made-up EXAMPLE readings; nothing touches real data.
"""
import argparse
import base64
import colorsys
import json
import os
import re
import shutil
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import ROOT, read_text, request, scratch, start_server, stop_server
from test_clock_palette import write_samples
from test_gauge_anim import write_week

import usage_history as uh  # noqa: E402


def make_ctx(data, lang="en", privacy=False, theme=None):
    a = argparse.Namespace(config=None, data_dir=str(data), tz="UTC", lang=lang, theme=theme, pace=None,
                           palette=None, privacy=privacy, host=None, port=None, anim=None)
    return uh.Ctx(a)


def lum(h):
    return uh._rel_lum(h)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class MascotPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = scratch()
        write_samples(cls.data, days=8)
        write_week(cls.data)
        uh.render_history(make_ctx(cls.data))
        cls.page = cls.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data)

    @classmethod
    def read(cls):
        return read_text(os.path.join(cls.data, "history", "index.html"))

    def rebuild(self, **kw):
        uh.rewrite_page(make_ctx(self.data, **kw))
        return self.read()

    def button(self, page):
        m = re.search(r'<button type="button" class="mascot" id="mascot"[^>]*>(.*?)</button>', page, re.S)
        return m

    def test_hidden_by_default(self):
        self.assertEqual(uh.MASCOT_DEFAULT, "off")  # opt-in: hidden until picked in the menu
        m = self.button(self.page)
        self.assertIsNotNone(m)
        tag = m.group(0).split(">", 1)[0]
        self.assertIn('data-c="off"', tag)
        self.assertIn(" hidden", tag)
        self.assertIn('aria-label="Mascot, click to blink"', tag)
        self.assertIn('<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">', m.group(1))
        self.assertEqual(m.group(1).count('class="meye"'), 2)
        # inline with the title: same flex row, right after the h1
        self.assertRegex(self.page, r'<div class="hrow" id="hrow"><h1>Grok Bot Usage &amp; Check-in</h1><button type="button" class="mascot"')
        self.assertIn(uh.MASCOT_JS, self.page)

    def test_setting_options(self):
        m = re.search(r'<label class="pal" id="mascotl">Mascot <select id="mascotsel"([^>]*)>(.*?)</select></label>', self.page, re.S)
        self.assertIsNotNone(m)
        for a in ('data-api="/api/mascot"', 'data-k="usage.mascot"', 'data-f="mascot"', 'data-live="1"'):
            self.assertIn(a, m.group(1))
        vals = re.findall(r"<option value='(\w+)'", m.group(2))
        self.assertEqual(tuple(vals), uh.MASCOT_CHOICES)
        self.assertEqual(vals, ["auto", "brown", "black", "grey", "teal", "orange", "pink", "purple", "blue", "green", "maroon", "off"])
        self.assertIn("<option value='auto'>Auto (theme accent)</option>", m.group(2))
        self.assertIn("<option value='off' selected>Off (hidden)</option>", m.group(2))
        # toolbar row 2, right after Read interval (next to the other page-only display menu, Gauge animation)
        row2 = self.page.split('id="tact"', 1)[1]
        self.assertIn('id="mascotsel"', row2)
        self.assertLess(row2.index('id="ivsel"'), row2.index('id="mascotsel"'))
        self.assertLess(row2.index('id="mascotsel"'), row2.index('id="applying"'))
        # the shared menu code applies it live
        self.assertIn("s.id==='mascotsel'&&window.usageMascot", uh.PICK_JS)

    def test_localized(self):
        page = self.rebuild(lang="zh-TW")
        self.assertIn('id="mascotl">小圓 <select', page)
        for t in ("自動（主題強調色）", ">棕<", ">青綠<", ">粉紅<", ">關（隱藏）<"):
            self.assertIn(t, page)
        self.assertIn('aria-label="小圓，點一下眨眼"', page)
        for lk, S in uh.MASCOT_STR.items():
            self.assertEqual(set(S["opts"]), set(uh.MASCOT_CHOICES) | {uh.MASCOT_CUSTOM}, lk)
            self.assertTrue(S["label"] and S["aria"], lk)

    def test_privacy_hides_it(self):
        page = self.rebuild(privacy=True)
        self.assertNotIn('id="mascot"', page)
        self.assertNotIn('id="mascotsel"', page)
        self.assertNotIn(uh.MASCOT_JS, page)
        self.assertNotIn('id="ghlink"', page)  # hidden the same way as the GitHub mark

    def test_off_hides_it(self):
        p = os.path.join(self.data, uh.PAGE_SETTINGS)
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"mascot": "off"}, fh)
            page = self.rebuild()
            tag = self.button(page).group(0).split(">", 1)[0]
            self.assertIn('data-c="off"', tag)
            self.assertIn(" hidden", tag)
            self.assertIn("<option value='off' selected>", page)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"mascot": "brown"}, fh)
            page = self.rebuild()
            self.assertIn('data-c="brown"', page)
            self.assertNotIn(" hidden", self.button(page).group(0).split(">", 1)[0])
        finally:
            os.remove(p)
        self.assertIn("b.hidden=v==='off'", uh.MASCOT_JS)  # the menu hides it at once too

    def test_setting_values(self):
        self.assertEqual(uh.page_mascot(None), "off")
        self.assertEqual(uh.page_mascot("Teal"), "teal")
        self.assertEqual(uh.page_mascot("gray"), "grey")
        self.assertEqual(uh.page_mascot("none"), "off")
        self.assertEqual(uh.page_mascot("maroon"), "maroon")
        self.assertEqual(uh.page_mascot("red"), "off")
        self.assertEqual(uh.page_mascot("crimson"), "off")
        self.assertEqual(uh.page_mascot("<x>"), "off")
        self.assertEqual(uh.page_mascot("auto"), "auto")

    def test_auto_follows_theme_accent(self):
        c = make_ctx(self.data)
        self.assertEqual(c.palette, "orange-teal")
        ac = c.theme["accent_color"]
        self.assertEqual(ac, c.theme["under_color"])  # orange/teal -> teal accent -> teal mascot
        self.assertIn(f"--accent:{ac};", self.page)
        self.assertIn(".mascot{forced-color-adjust:none;display:inline-block;", self.page)
        # eyes / mouth come from --m-eye / --m-mouth (mascot_eyes rule), never from the button text colour --on-accent
        self.assertIn("--mb:var(--accent);--me:var(--m-eye,#1B1F24);--mh:#FFFFFF;", uh.mascot_css())
        self.assertIn("--mm:var(--m-mouth,#1B1F24)", uh.mascot_css())
        self.assertNotIn("var(--on-accent)", uh.mascot_css())
        eye, _hl, mouth = uh.mascot_eyes(ac)
        self.assertIn(f"--m-eye:{eye};--m-mouth:{mouth};", self.page)
        self.assertIn("o['--m-eye']=o['--m-mouth']=", uh.PALETTE_JS)  # the palette menu moves the accent and the mascot with it
        # a theme that pins its accent: the mascot follows that one
        p = os.path.join(self.data, uh.PAGE_SETTINGS)
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"mascot": "auto"}, fh)
            page = self.rebuild(theme=os.path.join(ROOT, "themes", "muted.json"))
        finally:
            os.remove(p)
        self.assertIn("--accent:#6B93A8;", page)
        self.assertIn('data-c="auto"', page)

    def test_auto_eyes_dark_on_dark_accent(self):
        # v1.4.1 fix: a dark accent made --on-accent white, and the 'auto' mascot's eyes and mouth turned white with it
        th = os.path.join(self.data, "dark-teal.json")
        with open(th, "w", encoding="utf-8") as fh:
            json.dump({"bg_color": "#000000", "text_color": "#E8F4FF", "accent_color": "#006D5B"}, fh)
        p = os.path.join(self.data, uh.PAGE_SETTINGS)
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"mascot": "auto"}, fh)
            page = self.rebuild(theme=th)
        finally:
            os.remove(p)
            os.remove(th)
        self.assertIn("--on-accent:#FFFFFF;", page)  # the primary button keeps white text on the dark accent
        root = re.search(r":root\{--over[^}]*\}", page).group(0)
        self.assertIn(f"--m-eye:{uh.MASCOT_EYE_BLACK};--m-mouth:{uh.MASCOT_EYE_BLACK};", root)
        for acc in ("#006D5B", "#2A2C31", "#0B8A7E", "#E8D9C0", "#23C4AE", "#FFFFFF"):
            eye, hl, mouth = uh.mascot_eyes(acc)
            self.assertIn(f"--m-eye:{eye};--m-mouth:{mouth}", uh.mascot_auto_vars(acc))
            self.assertIn(eye, (uh.MASCOT_EYE_DARK, uh.MASCOT_EYE_BLACK))
        self.assertIn(f"--m-eye:{uh.MASCOT_EYE_DARK}", uh.mascot_auto_vars("teal"))  # named colours too

    def test_auto_eyes_js_matches_python(self):
        # the palette menu's JS rule gives the same eyes as mascot_eyes() for every palette's accent (dark + light)
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        import subprocess
        m = re.search(r"function rgb\(h\).*?\n", uh.PALETTE_JS)
        lm = re.search(r"function lum\(h\).*?\n", uh.PALETTE_JS)
        rule = re.search(r"o\['--m-eye'\]=o\['--m-mouth'\]=([^;]*);", uh.PALETTE_JS).group(1)
        accs = sorted({v["under"] for pal in uh.usage_palette.PALETTES.values() for v in pal.values()}
                      | set(uh.MASCOT_COLORS.values()) | {"#006D5B", "#E8D9C0", "#000000", "#FFFFFF", "#565B63"})
        js = (m.group(0) + lm.group(0) + "var A=" + json.dumps(accs) + ";console.log(JSON.stringify(A.map(function(a){return "
              + rule + ";})));")
        out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [uh.mascot_eyes(a)[0] for a in accs])

    def test_survives_browser_auto_dark(self):
        # v1.4.1 fix: Chrome's Auto Dark Mode (and dark-mode extensions) lightened every dark inline-SVG fill, so Black
        # turned white with grey eyes and dark eyes went pale; the page now declares its scheme and the mascot is
        # repainted on a canvas, which auto dark leaves alone (the svgs stay as the fallback underneath)
        self.assertIn('<meta name="color-scheme" content="dark">', self.page)
        self.assertIn(":root{color-scheme:dark}", self.page)
        light = self.rebuild(theme=os.path.join(ROOT, "themes", "light.json"))
        self.assertIn('<meta name="color-scheme" content="light">', light)
        self.rebuild()
        js = uh.MASCOT_JS
        for part in ("createElement('canvas')", "getComputedStyle(", "XMLSerializer", "createObjectURL(", "drawImage(", "new MutationObserver(paint)",
                     "draw('c')", "scale(1 .1)", ".mspark,.mwave"):
            self.assertIn(part, js)
        css = uh.mascot_css()
        self.assertIn(".mascot .mcv{position:absolute;left:-20%;top:-25%;width:140%;height:140%;pointer-events:none}", css)
        self.assertIn(".mascot.mcv-on svg.mflat{opacity:0}", css)
        self.assertIn(".mascot[data-c=custom] .mcv{display:none}", css)
        self.assertIn("forced-color-adjust:none", css)
        # blink still respects reduced motion before touching the canvas
        self.assertIn("function blink(){if(rm()||b.hidden)return;", js)

    def test_colours_readable(self):
        for k, body in uh.MASCOT_COLORS.items():
            eye, hl, mouth = uh.mascot_eyes(body)
            self.assertIn(eye, (uh.MASCOT_EYE_DARK, uh.MASCOT_EYE_BLACK), k)  # always dark eyes, never light ones
            self.assertEqual(hl, "#FFFFFF", k)
            self.assertEqual(mouth, eye, k)  # mouth matches the eyes (dark), also on black
            self.assertIn(f".mascot[data-c={k}]{{--mb:{body};--me:{eye};--mh:{hl};--mm:{mouth}", uh.mascot_css())
            r, g, b = (int(body[i:i + 2], 16) / 255 for i in (1, 3, 5))
            h, s, v = colorsys.rgb_to_hsv(r, g, b)
            if k != "maroon":  # only the explicit Maroon option may be red-ish
                self.assertFalse(s > 0.35 and (h * 360 < 15 or h * 360 > 345), k)
        self.assertEqual(set(uh.MASCOT_COLORS) | {"auto", "off"}, set(uh.MASCOT_CHOICES))
        self.assertEqual(uh.mascot_eyes(uh.MASCOT_COLORS["black"])[0], uh.MASCOT_EYE_BLACK)
        self.assertEqual(uh.mascot_eyes(uh.MASCOT_COLORS["brown"])[0], uh.MASCOT_EYE_DARK)
        self.assertNotIn("--mr", uh.mascot_css())  # no eye rim
        self.assertEqual(uh.mascot_eyes(uh.MASCOT_COLORS["teal"])[0], uh.MASCOT_EYE_DARK)

    def test_blink_and_reduced_motion(self):
        css, js = uh.mascot_css(), uh.MASCOT_JS
        self.assertIn("@media (prefers-reduced-motion: reduce){.mascot .meye,.mascot.blink .meye{animation:none!important", css)
        self.assertIn("transform-box:fill-box;transform-origin:center", css)
        self.assertIn("animation:mblink .2s", css)
        self.assertIn("scaleY(.1)", css)
        self.assertNotIn("translate", css)  # blinks in place only
        for s in ("prefers-reduced-motion: reduce", "document.hidden", "visibilitychange", "10000+Math.random()*10000",
                  "'click'", "pointerenter"):
            self.assertIn(s, js)
        self.assertIn("if(rm()||b.hidden)return", js)
        self.assertIn("if(document.hidden||rm())return", js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("@media (prefers-reduced-motion: reduce){.mascot", self.page)

    def test_no_external_urls(self):
        for part in (uh.MASCOT_SVG, uh.mascot_css(), uh.MASCOT_JS):
            for bad in ("http", "://", "url(", "@import", "<image", "<img", "href", "src=", "fetch(", "xlink", "@font-face"):
                self.assertNotIn(bad, part, bad)
        # the canvas repaint loads only a blob of the page's own flattened svg, nothing from outside
        self.assertEqual(uh.MASCOT_JS.count("setAttribute('src',"), 1)
        self.assertIn("setAttribute('src',url)", uh.MASCOT_JS)
        self.assertIn("url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(o)]", uh.MASCOT_JS)
        m = self.button(self.page)
        self.assertNotRegex(m.group(0), r"https?:|url\(|href")


GOOD_SVG = ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- my own mascot -->\n'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#C33"/></svg>\n')
EVIL_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="alert(1)">'
            '<script>window.PWNED_MARKER=1</script><circle cx="5" cy="5" r="4"/></svg>')


class CustomMascotTests(unittest.TestCase):
    """<data_dir>/mascot.svg: an extra 'custom' choice, shown only as an <img> data URI, never inlined."""

    def setUp(self):
        self.data = scratch()
        write_samples(self.data, days=8)
        write_week(self.data)
        uh.render_history(make_ctx(self.data))

    def tearDown(self):
        shutil.rmtree(self.data)

    def put(self, content, setting=None):
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(os.path.join(self.data, uh.MASCOT_FILE), mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as fh:
            fh.write(content)
        if setting:
            with open(os.path.join(self.data, uh.PAGE_SETTINGS), "w", encoding="utf-8") as fh:
                json.dump({"mascot": setting}, fh)

    def page(self, **kw):
        uh.rewrite_page(make_ctx(self.data, **kw))
        return read_text(os.path.join(self.data, "history", "index.html"))

    def options(self, page):
        m = re.search(r'<select id="mascotsel"[^>]*>(.*?)</select>', page, re.S)
        return re.findall(r"<option value='(\w+)'", m.group(1))

    def button(self, page):
        return re.search(r'<button type="button" class="mascot" id="mascot"[^>]*>.*?</button>', page, re.S).group(0)

    def test_option_only_when_file_exists(self):
        page = self.page()
        self.assertNotIn("custom", self.options(page))
        self.assertNotIn('class="mcustom"', page)
        self.assertNotIn("data:image/svg+xml", page)
        self.put(GOOD_SVG)
        page = self.page()
        vals = self.options(page)
        self.assertEqual(vals, list(uh.MASCOT_CHOICES[:-1]) + ["custom", "off"])
        self.assertIn("<option value='custom'>Custom</option>", page)
        self.assertIn("<option value='off' selected>", page)  # still off by default with the file there
        self.assertIn(" hidden", self.button(page).split(">", 1)[0])
        self.assertIn(">自訂<", self.page(lang="zh-TW"))

    def test_custom_rendered_as_img_data_uri(self):
        self.put(GOOD_SVG, "custom")
        page = self.page()
        btn = self.button(page)
        tag = btn.split(">", 1)[0]
        self.assertIn('data-c="custom"', tag)
        self.assertNotIn(" hidden", tag)
        self.assertIn("<option value='custom' selected>Custom</option>", page)
        m = re.search(r'<img class="mcustom" src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)" alt="" draggable="false">', btn)
        self.assertIsNotNone(m)
        self.assertEqual(base64.b64decode(m.group(1)).decode("utf-8"), GOOD_SVG)
        self.assertNotIn('fill="#C33"', page)  # the user's SVG markup is not in the DOM
        self.assertNotIn('viewBox="0 0 10 10"', page)
        # same slot / size as the built-in one; the built-in blob is hidden while custom is picked
        css = uh.mascot_css()
        self.assertIn(".mascot .mcustom{display:none;width:100%;height:100%;object-fit:contain", css)
        self.assertIn(".mascot[data-c=custom] svg{display:none} .mascot[data-c=custom] .mcustom{display:block}", css)
        self.assertIn(uh.MASCOT_SVG, btn)  # built-in kept so switching back in the menu is instant

    def test_script_never_inlined(self):
        self.put(EVIL_SVG, "custom")
        page = self.page()
        self.assertEqual(make_ctx(self.data).mascot_setting, "custom")  # valid SVG; it just can't run in an <img>
        for bad in ("PWNED_MARKER", "alert(1)", "onload=\"alert", 'viewBox="0 0 10 10"'):
            self.assertNotIn(bad, page, bad)
        self.assertNotIn("<script>window.", page)
        self.assertRegex(self.button(page), r'<img class="mcustom" src="data:image/svg\+xml;base64,')
        uri = make_ctx(self.data).mascot_custom
        self.assertNotRegex(uri, r"[<>\"' ]")

    def test_size_cap(self):
        pad = "<!--" + "x" * (uh.MASCOT_MAX_BYTES) + "-->"
        self.put(GOOD_SVG.replace("<svg", pad + "<svg"), "custom")
        c = make_ctx(self.data)
        self.assertIsNone(c.mascot_custom)
        self.assertEqual(c.mascot_setting, "off")
        page = self.page()
        self.assertNotIn("custom", self.options(page))
        self.assertNotIn("data:image/svg+xml", page)
        # right at the cap is fine
        body = GOOD_SVG.encode()
        self.put(body.replace(b"<svg", b"<!--" + b"y" * (uh.MASCOT_MAX_BYTES - len(body) - 7) + b"--><svg"))
        self.assertEqual(os.path.getsize(os.path.join(self.data, uh.MASCOT_FILE)), uh.MASCOT_MAX_BYTES)
        self.assertIsNotNone(make_ctx(self.data).mascot_custom)

    def test_rejects_non_svg(self):
        bad = [b"", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, b"<html><body>hi</body></html>", b"not an svg at all",
               b"<svg viewBox='0 0 1 1'>", b"\xff\xfe<\x00s\x00v\x00g\x00",
               b"<!DOCTYPE svg [<!ENTITY a 'aaaa'>]><svg>&a;</svg>",
               b"<div><svg></svg></div>", b"<svg></svg><script>x</script>"]
        for content in bad:
            self.put(content, "custom")
            c = make_ctx(self.data)
            self.assertIsNone(c.mascot_custom, content[:30])
            self.assertEqual(c.mascot_setting, "off", content[:30])
        for good in (b"\xef\xbb\xbf<svg xmlns='http://www.w3.org/2000/svg'/>", b"  <svg viewBox='0 0 1 1'></svg>",
                     b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd"><svg></svg>'):
            self.put(good)
            self.assertIsNotNone(make_ctx(self.data).mascot_custom, good[:30])

    def test_missing_file_falls_back_to_off(self):
        self.put(GOOD_SVG, "custom")
        self.assertEqual(make_ctx(self.data).mascot_setting, "custom")
        os.remove(os.path.join(self.data, uh.MASCOT_FILE))
        c = make_ctx(self.data)
        self.assertEqual((c.mascot_custom, c.mascot_setting), (None, "off"))
        page = self.page()
        tag = self.button(page).split(">", 1)[0]
        self.assertIn('data-c="off"', tag)
        self.assertIn(" hidden", tag)
        self.assertIn("<option value='off' selected>", page)
        self.assertEqual(uh.page_mascot("custom"), "off")
        self.assertEqual(uh.page_mascot("CUSTOM", custom=True), "custom")

    def test_privacy_hides_custom(self):
        self.put(GOOD_SVG, "custom")
        page = self.page(privacy=True)
        for s in ('id="mascot"', 'id="mascotsel"', "data:image/svg+xml", 'class="mcustom"'):
            self.assertNotIn(s, page)

    def test_built_in_unchanged_with_file(self):
        self.put(GOOD_SVG, "brown")
        btn = self.button(self.page())
        self.assertIn('data-c="brown"', btn)
        self.assertIn(uh.MASCOT_SVG, btn)  # the blob still shows; the img is display:none unless data-c=custom


class MascotServerTests(unittest.TestCase):
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
        code, raw = request(self.url + "api/mascot", "POST", h, json.dumps(body).encode())
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_mascot_endpoint(self):
        for bad in ({"mascot": "red"}, {"mascot": 1}, {}):
            self.assertEqual(self._post(bad)[0], 400, bad)
        self.assertEqual(self._post({"mascot": "off"}, token=False)[0], 403)
        self.assertEqual(request(self.url + "api/mascot")[0], 405)
        code, j = self._post({"mascot": "pink"})
        self.assertEqual((code, j.get("mascot")), (200, "pink"))
        self.assertEqual(json.loads(read_text(os.path.join(self.data, "page_settings.json")))["mascot"], "pink")
        page = read_text(os.path.join(self.data, "history", "index.html"))
        self.assertIn('data-c="pink"', page)
        self.assertIn("<option value='pink' selected>", page)

    def test_custom_endpoint(self):
        f = os.path.join(self.data, uh.MASCOT_FILE)
        self.assertEqual(self._post({"mascot": "custom"})[0], 400)  # no mascot.svg yet
        try:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(GOOD_SVG)
            code, j = self._post({"mascot": "custom"})
            self.assertEqual((code, j.get("mascot")), (200, "custom"))
            page = read_text(os.path.join(self.data, "history", "index.html"))
            self.assertIn('data-c="custom"', page)
            self.assertIn('<img class="mcustom" src="data:image/svg+xml;base64,', page)
        finally:
            os.remove(f)
            self._post({"mascot": "off"})


if __name__ == "__main__":
    unittest.main()
