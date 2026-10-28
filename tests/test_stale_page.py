"""A page loaded before the server restarted holds the old per-start token: POST /api/palette/render answers 403 and,
before this fix, the Hours / Range / Layout / Fill menus silently snapped back to their defaults. The page now keeps
the pick and reloads once; a second 403 within a minute is reported as a failure (no reload loop)."""
import json
import os
import re
import shutil
import unittest

from _helpers import read_text, request, scratch, start_server, stop_server
from test_clock_palette import write_samples

import usage_history as uh

NODE = shutil.which("node")

# Just enough DOM to run the real PALETTE_JS: every element is an inert stub, <li role=option> items are real lists.
HARNESS = r"""
var store = %(store)s, sess = %(sess)s, calls = [], reloads = 0, now = 1000000;
function El(id) { this.id = id; this.dataset = {}; this.style = {setProperty: function () {}}; this.children = []; this.classList = {toggle: function () {}, add: function () {}, remove: function () {}};
  Object.defineProperty(this, 'textContent', {configurable: true, get: function () { return this.children.map(function (c) { return c.t || ''; }).join(''); }, set: function (v) { this.children = v ? [{t: String(v)}] : []; }});
  this.innerHTML = ''; this.hidden = false; this.value = ''; this.checked = false; }
El.prototype = {setAttribute: function () {}, removeAttribute: function () {}, getAttribute: function (k) { return this.dataset.orig; }, appendChild: function (c) { this.children.push(c); },
  querySelectorAll: function (q) { return this.opts || []; }, addEventListener: function () {}, focus: function () {}, contains: function () { return false; }, scrollIntoView: function () {}};
var els = {};
function get(id) { return els[id] || (els[id] = new El(id)); }
['clkbtn', 'clklist', 'clobtn', 'clolist', 'clfbtn', 'clflist', 'cldbtn', 'cldlist', 'pallist', 'palbtn'].forEach(function (id) { get(id); });
function opts(list, vals) { get(list).opts = vals.map(function (v) { var o = new El(list + '-' + v); o.dataset.v = v; o.dataset.orig = ''; return o; }); }
opts('cldlist', ['24', 'auto', 'am', 'pm']); opts('clklist', ['wk', '1m']); opts('clolist', ['ov', 'sd']); opts('clflist', ['t', 's']); opts('pallist', ['orange-teal', 'gold-sky']);
get('paldata').textContent = JSON.stringify({default: 'orange-teal', base: 'orange-teal', pat: false, osfx: '', light: false, alias: {}, cc: {under: '#112233', over: '#445566'}, bg: '#000000', accent: '#00ffcc', under: '#00ffcc', over: '#ffaa00',
  fixed: {}, pf: false, s: {busy: 'busy', fail: 'FAILED', file: 'file'},
  table: {'orange-teal': {dark: {over: '#fa0', under: '#0fc', baseline: '#888', notice: '#a0f'}, light: {over: '#fa0', under: '#0fc', baseline: '#888', notice: '#a0f'}},
          'gold-sky': {dark: {over: '#fa0', under: '#0cf', baseline: '#888', notice: '#a0f'}, light: {over: '#fa0', under: '#0cf', baseline: '#888', notice: '#a0f'}}}});
get('clksub').dataset.s = JSON.stringify({'24': 'T24', am: 'TAM', pm: 'TPM', 'auto-am': 'TAUTOAM', 'auto-pm': 'TAUTOPM'}); get('clksub').textContent = 'T24';
var imgs = [get('clk1img'), get('clkimg')]; imgs[0].dataset.f = 'clock-1d.png'; imgs[1].dataset.f = 'clock-wk-ot.png';
imgs.forEach(function (i) { i.src = 'data:orig'; i.getAttribute = function () { return 'data:orig'; }; });
get('clkimg').nodeName = 'IMG';
var document = {getElementById: get, querySelector: function (q) { return /usage-token/.test(q) ? {content: 'old-token'} : null; },
  querySelectorAll: function (q) { return /img\[data-f\]/.test(q) ? imgs : []; }, createElement: function () { return new El('x'); }, addEventListener: function () {},
  createTextNode: function (t) { return {t: t}; }, documentElement: new El('html')};
var localStorage = {getItem: function (k) { return k in store ? store[k] : null; }, setItem: function (k, v) { store[k] = String(v); }};
var sessionStorage = {getItem: function (k) { return k in sess ? sess[k] : null; }, setItem: function (k, v) { sess[k] = String(v); }};
var location = {protocol: 'http:', reload: function () { reloads++; }};
Date.now = function () { return now; };
function Image() { var me = this; Object.defineProperty(this, 'src', {set: function (u) { setTimeout(function () { me.onload(); }, 0); }}); }
var window = {}, status = %(status)s, reply = %(reply)s;
function fetch(u, o) { calls.push(u); var st = status.shift(); return Promise.resolve({status: st, json: function () { return st === 200 ? Promise.resolve({ok: true, v: 'g1', dial: reply.shift() || 'am'}) : Promise.resolve({ok: false, error: 'x'}); }}); }
%(js)s
var subs = [];
var done = function () { console.log(JSON.stringify({reloads: reloads, store: store, sess: sess, calls: calls, msg: get('palmsg').textContent, sub: get('clksub').textContent, subs: subs, cur: get('cldcur').innerHTML})); };
%(act)s
setTimeout(done, 250);
"""


def run(status, store=None, sess=None, act="get('cldlist').opts[1].onclick();", reply=()):
    import subprocess
    js = HARNESS % {"store": json.dumps(store or {}), "sess": json.dumps(sess or {}), "status": json.dumps(status), "js": uh.PALETTE_JS, "act": act, "reply": json.dumps(list(reply))}
    p = subprocess.run([NODE, "-e", js], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node not installed")
class StaleTokenJsTests(unittest.TestCase):
    def test_stale_token_reloads_once_and_keeps_the_pick(self):
        r = run([403])
        self.assertEqual(r["reloads"], 1)
        self.assertEqual(r["store"]["usage.clockDial"], "auto")  # the pick survives the reload, so Last 12 h is applied after it
        self.assertEqual(len(r["calls"]), 1)
        self.assertIn("usage.stale", r["sess"])

    def test_second_403_is_a_failure_not_a_loop(self):
        r = run([403], sess={"usage.stale": "999990"})  # reloaded 10 ms ago and still refused
        self.assertEqual(r["reloads"], 0)
        self.assertEqual(r["msg"], "FAILED")
        self.assertNotIn("usage.clockDial", r["store"])  # nothing saved for a pick that failed
        r = run([403], sess={"usage.stale": "1"})  # long ago: allowed again
        self.assertEqual(r["reloads"], 1)

    def test_other_errors_do_not_reload(self):
        for st in (500, 400, 404):
            r = run([st])
            self.assertEqual((r["reloads"], r["msg"]), (0, "FAILED"), st)

    def test_good_token_applies_the_pick(self):
        r = run([200])
        self.assertEqual(r["reloads"], 0)
        self.assertEqual(r["store"]["usage.clockDial"], "auto")
        self.assertEqual(r["msg"], "")


class StaleTokenServerTests(unittest.TestCase):
    """What a browser asks for after picking each Hours option: the render call and the PNGs it then loads."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        write_samples(data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def _render(self, dial, token=None):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json", "X-Usage-Token": token or self.token}
        body = json.dumps({"name": "orange-teal", "patterns": False, "clock": "wk", "mode": "ot", "dial": dial}).encode()
        code, raw = request(self.url + "api/palette/render", "POST", h, body)
        return code, (json.loads(raw) if raw.strip().startswith(b"{") else {})

    def test_every_hours_option_renders_and_its_images_load(self):
        for dial in uh.CLOCK_DIALS:
            code, j = self._render(dial)
            self.assertEqual(code, 200, (dial, j))
            half = j["dial"]
            left = {"24": "clock-1d.png", "am": "clock-1d-am.png", "pm": "clock-1d-pm.png", "auto": f"clock-1d-auto-{half}.png"}[dial]
            right = "clock-wk-ot.png" if half == "24" else f"clock-wk-ot-{half}.png"
            for f in (left, right):
                code, png = request(self.url + f"pal/orange-teal/{f}?v={j['v']}")
                self.assertEqual((code, png[:4]), (200, b"\x89PNG"), (dial, f))

    def test_old_page_token_is_refused_with_403(self):
        code, j = self._render("auto", token="token-of-a-previous-server-process")
        self.assertEqual(code, 403)
        self.assertFalse(j.get("ok"))

    def test_page_ships_the_recovery(self):
        page = request(self.url)[1].decode("utf-8")
        self.assertIn("function revive(", page)
        self.assertIn("x.status===403", page)
        self.assertEqual(re.findall(r"id='cld-(\w+)' data-v", page), ["24", "auto", "am", "pm"])


if __name__ == "__main__":
    unittest.main()
