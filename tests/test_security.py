"""Local server hardening: token on writes, POST only, Host / Origin checks, loopback bind, path traversal,
XSS, malformed input, secrets in tracked files.

Servers run in scratch dirs on a random port; nothing touches real data. Known gaps found by these tests are
fixed in v1.12.0 (see the CHANGELOG entry); their tests are ordinary tests now.
"""
import ast
import errno
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from _helpers import ROOT, read_text, request, scratch, start_server, stop_server

import local_guard  # noqa: E402


class GuardUnitTests(unittest.TestCase):
    def test_host_allowed(self):
        ok = local_guard.host_allowed
        for h in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "127.0.0.1", "LOCALHOST:8765"):
            self.assertTrue(ok(h, 8765), h)
        for h in ("evil.example:8765", "127.0.0.1:9999", "", None, "127.0.0.1.nip.io:8765", "localhost.evil:8765"):
            self.assertFalse(ok(h, 8765), h)
        self.assertTrue(ok("mybox.lan:8765", 8765, ["mybox.lan"]))

    def test_check_write(self):
        tok = local_guard.new_token()
        base = {"Host": "127.0.0.1:8765"}
        self.assertFalse(local_guard.check_write(base, 8765, tok)[0])
        self.assertEqual(local_guard.check_write(dict(base, **{"X-Usage-Token": "x"}), 8765, tok)[1], 403)
        good = dict(base, **{"X-Usage-Token": tok})
        self.assertTrue(local_guard.check_write(good, 8765, tok)[0])
        self.assertTrue(local_guard.check_write(dict(good, Origin="http://localhost:8765"), 8765, tok)[0])
        for bad in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Referer": "https://evil.example/x"},
                    {"Sec-Fetch-Site": "cross-site"}):
            self.assertFalse(local_guard.check_write(dict(good, **bad), 8765, tok)[0], bad)

    @unittest.skipIf(os.name == "nt", "POSIX file modes (Windows has no chmod 600)")
    def test_token_file_private(self):
        d = scratch()
        try:
            p = os.path.join(d, "x.token")
            local_guard.write_token_file(p, "abc")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(local_guard.read_token_file(p), "abc")
            local_guard.remove_token_file(p, "other")  # only removes its own token
            self.assertTrue(os.path.exists(p))
            local_guard.remove_token_file(p, "abc")
            self.assertFalse(os.path.exists(p))
        finally:
            shutil.rmtree(d)

    def test_inject_token(self):
        page = local_guard.inject_token("<html><head><title>x</title></head></html>", "T0K")
        self.assertIn('<meta name="usage-token" content="T0K">', page)


class ClockTooltipXssTests(unittest.TestCase):
    """Check-in notes and names reach the clock tooltips only through textContent / createTextNode."""

    def test_tooltip_js_never_parses_html(self):
        import usage_history as uh
        js = uh.CLOCKTIP_JS
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(bad, js)
        self.assertIn("textContent", js)
        self.assertIn("it.note", js)

    def test_tooltip_json_escapes_lt(self):
        import argparse
        import usage_history as uh
        d = scratch()
        try:
            hist = os.path.join(d, "history")
            os.makedirs(hist)
            spot = {"spots": [{"x": 1, "y": 2, "more": 0, "items": [{"n": "</script><b>", "note": "<!--<script>", "t": "", "z": "",
                                                                      "c": "#FFFFFF", "cats": ["<i>"], "k": "point", "ts": 0}]}]}
            for f in ("clock-1d.json", "clock-wk-ot.json"):
                with open(os.path.join(hist, f), "w", encoding="utf-8") as fh:
                    json.dump(spot, fh)
            ctx = uh.Ctx(argparse.Namespace(config=None, data_dir=d, tz="UTC", lang="en", theme=None, pace=None, palette=None,
                                            privacy=False, host=None, port=None))
            out = uh.clock_tips_json(ctx)
            self.assertNotIn("<", out)
            self.assertEqual(json.loads(out)["spots"]["wk"]["spots"][0]["items"][0]["n"], "</script><b>")
        finally:
            shutil.rmtree(d)


class _ServerCase:
    """Mixin: subclasses set self.url, self.token, self.write_path, self.write_body."""

    def h(self, **extra):
        host = urlparse(self.url).netloc
        return dict({"Host": host}, **extra)

    def test_get_write_rejected(self):
        code, _ = request(self.url + self.write_path.lstrip("/") + "?name=x&pct=5")
        self.assertEqual(code, 405)

    def test_post_without_token_rejected(self):
        code, _ = request(self.url + self.write_path.lstrip("/"), "POST",
                          {"Content-Type": "application/json"}, self.write_body)
        self.assertEqual(code, 403)
        code, _ = request(self.url + self.write_path.lstrip("/"), "POST",
                          {"Content-Type": "application/json", "X-Usage-Token": "wrong"}, self.write_body)
        self.assertEqual(code, 403)

    def test_foreign_host_rejected(self):
        code, _ = request(self.url, headers={"Host": "attacker.example:%d" % urlparse(self.url).port})
        self.assertEqual(code, 421)
        code, _ = request(self.url + self.write_path.lstrip("/"), "POST",
                          {"Host": "attacker.example", "X-Usage-Token": self.token}, self.write_body)
        self.assertEqual(code, 421)

    def test_cross_site_origin_rejected(self):
        code, _ = request(self.url + self.write_path.lstrip("/"), "POST",
                          {"Content-Type": "application/json", "X-Usage-Token": self.token,
                           "Origin": "http://attacker.example"}, self.write_body)
        self.assertEqual(code, 403)

    def test_page_carries_token(self):
        code, body = request(self.url)
        self.assertEqual(code, 200)
        self.assertIn(('<meta name="usage-token" content="%s">' % self.token).encode(), body)

    @unittest.skipIf(os.name == "nt", "POSIX file modes (Windows has no chmod 600)")
    def test_token_file_mode(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.token_file).st_mode), 0o600)


class HistoryServerTests(_ServerCase, unittest.TestCase):
    write_path = "/api/checkin"
    write_body = json.dumps({"name": "tester", "category": "code"}).encode()

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url, cls.token_file = info["url"], info["token_file"]
        cls.token = read_text(cls.token_file)
        cls.data = data

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def _post(self, d, form=False):
        h = {"X-Usage-Token": self.token, "Origin": self.url.rstrip("/"),
             "Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        return request(self.url + "api/checkin", "POST", h, d.encode() if form else json.dumps(d).encode())

    def test_session_start_end_categories_and_search(self):
        code, body = self._post({"name": "ok-user", "phase": "start", "categories": ["review", "code"], "note": "audit"})
        self.assertEqual(code, 200, body)
        r = json.loads(body)["checkin"]
        self.assertEqual(r["categories"], ["review", "code"])
        self.assertTrue(r["id"])
        code, body = self._post({"phase": "end", "id": r["id"], "status": "interrupted", "note": "power cut"})
        self.assertEqual(code, 200, body)
        code, body = request(self.url + "api/checkins?category=code")
        res = json.loads(body)
        mine = [s for s in res["sessions"] if s["id"] == r["id"]]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["state"], "interrupted")
        self.assertEqual(mine[0]["end_note"], "power cut")
        # read-only search: grouped by day, keyword over note + category label (AND)
        code, body = request(self.url + "api/checkins/search?q=AUDIT%20review")
        self.assertEqual(code, 200)
        res = json.loads(body)
        self.assertTrue(res["days"] and any(s["id"] == r["id"] for d in res["days"] for s in d["sessions"]))
        self.assertIn("usage_rise", res["days"][0])
        code, body = request(self.url + "api/checkins/search?q=audit%20nomatchword")
        self.assertEqual(json.loads(body)["count"], 0)
        self.assertEqual(request(self.url + "api/checkins/search?q=x", headers={"Host": "evil.example"})[0], 421)

    def test_too_many_categories_and_repeated_param(self):
        code, body = self._post({"name": "multi", "categories": "web,code,image,files"})
        self.assertEqual(code, 400, body)
        code, body = self._post("name=formuser&category=web&category=image&phase=point", form=True)
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["checkin"]["categories"], ["web", "image"])

    def test_clock_tip_json_paths(self):
        hist = os.path.join(self.data, "history")
        os.makedirs(hist, exist_ok=True)
        with open(os.path.join(hist, "clock-1d.json"), "w", encoding="utf-8") as fh:
            fh.write('{"spots": []}')
        code, body = request(self.url + "clock-1d.json")
        self.assertEqual((code, json.loads(body)), (200, {"spots": []}))
        for bad in ("pal/../clock-1d.json", "pal/x/../../clock-1d.json", "clock-1d.json.bak", "history_server.json",
                    "serve.token", "checkins.csv", "clock-..json"):
            self.assertEqual(request(self.url + bad)[0], 404, bad)

    def test_refresh_get_rejected(self):
        self.assertEqual(request(self.url + "api/refresh?pct=50")[0], 405)


class ViewServerTests(_ServerCase, unittest.TestCase):
    write_path = "/api/refresh"
    write_body = b"{}"

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        cls.proc, info = start_server(["usage_view.py", "--no-open", "--refresh-mode", "manual", "--data-dir", data,
                                       "--pct-source", "manual"], None)
        cls.url, cls.token_file = info["url"], info["token_file"]
        cls.token = read_text(cls.token_file)

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def test_pct_override_needs_debug(self):
        code, body = request(self.url + "api/refresh?pct=50", "POST", {"X-Usage-Token": self.token})
        self.assertEqual(code, 400)
        self.assertIn(b"debug", body)


# --------------------------------------------------------------------------------------------------------------
# Added in the security test suite (v1.11.1 base): bind address, route coverage, traversal, XSS, malformed input,
# static checks of the tracked files.
# --------------------------------------------------------------------------------------------------------------
_RESET_ERRNOS = (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED)


def _is_reset(exc):
    """True for a connection the server closed on us while we were still sending: broken pipe / reset, bare or wrapped in URLError."""
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        exc = exc.reason
    return isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)) or \
        (isinstance(exc, OSError) and exc.errno in _RESET_ERRNOS)


def request_or_reset(url, method="GET", headers=None, body=None):
    """Like ``request`` but a server that rejects a big body and closes before we finished sending it is a valid outcome:
    returns ``(None, exc)`` for a broken pipe / connection reset (EPIPE, ECONNRESET), ``(status, body)`` otherwise."""
    try:
        return request(url, method, headers, body)
    except (urllib.error.URLError, OSError) as e:
        if _is_reset(e):
            return None, e
        raise


def raw_http(port, payload, timeout=5.0, host="127.0.0.1"):
    """Send raw bytes to 127.0.0.1:port; return the status line, or None when nothing came back in `timeout` s
    (or the server closed / reset the connection before we could read its answer)."""
    with socket.create_connection((host, port), timeout=timeout) as sk:
        sk.settimeout(timeout)
        try:
            sk.sendall(payload)
            data = sk.recv(4096)
        except socket.timeout:
            return None
        except OSError as e:
            if _is_reset(e):
                return None
            raise
    return data.split(b"\r\n", 1)[0].decode("latin-1") if data else ""


def tracked_files():
    """Files tracked by git (empty list outside a checkout, e.g. a zip download)."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [f for f in out.decode("utf-8").split("\0") if f and os.path.isfile(os.path.join(ROOT, f))]


def non_loopback_ip():
    """This machine's LAN address (no packet is sent), or None."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sk.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
        ip = sk.getsockname()[0]
    except OSError:
        return None
    finally:
        sk.close()
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def source_of(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class HistoryServerSecurityTests(unittest.TestCase):
    """One real history server (default settings: loopback, random port, scratch data dir)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(cls.data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", cls.data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(cls.data, "history_server.json"))
        cls.url, cls.token_file = info["url"], info["token_file"]
        cls.token = read_text(cls.token_file)
        cls.port = urlparse(cls.url).port

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def post(self, path, body, **extra):
        h = {"X-Usage-Token": self.token, "Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        h.update(extra)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        return request(self.url + path.lstrip("/"), "POST", h, data)

    def alive(self):
        self.assertEqual(request(self.url + "api/status")[0], 200, "server stopped answering")
        self.assertIsNone(self.proc.poll(), "server process exited")

    # ---- bind address
    def test_default_bind_is_loopback(self):
        self.assertEqual(urlparse(self.url).hostname, "127.0.0.1")
        ip = non_loopback_ip()
        if ip is None:
            self.skipTest("no non-loopback address on this machine")
        with self.assertRaises(OSError):
            socket.create_connection((ip, self.port), timeout=3).close()

    def test_non_loopback_host_refused_without_flag(self):
        for argv in (["usage_history.py", "serve", "--host", "0.0.0.0", "--port", "0"],
                     ["usage_view.py", "--host", "0.0.0.0", "--no-open"]):
            d = scratch()
            try:
                env = dict(os.environ, MPLBACKEND="Agg", USAGE_NO_AUTO_UPDATE_CHECK="1")
                r = subprocess.run([sys.executable] + argv + ["--data-dir", d], cwd=ROOT, env=env, capture_output=True,
                                   text=True, timeout=60)
            finally:
                shutil.rmtree(d)
            self.assertEqual(r.returncode, 2, argv)
            self.assertIn("allow-remote", r.stdout + r.stderr)

    def test_remote_mode_still_needs_token_and_host(self):
        """--allow-remote on a non-default address (127.0.0.2 stays on this machine): writes need the token, foreign Host is refused."""
        d = scratch()
        data = os.path.join(d, "data")
        os.makedirs(data)
        try:
            try:
                proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0",
                                           "--host", "127.0.0.2", "--allow-remote"], os.path.join(data, "history_server.json"), wait=30)
            except RuntimeError:
                self.skipTest("127.0.0.2 is not available here")
            try:
                url = info["url"]
                port = urlparse(url).port
                self.assertEqual(request(url + "api/status", headers={"Host": "attacker.example:%d" % port})[0], 421)
                self.assertEqual(request(url + "api/status")[0], 200)
                body = json.dumps({"name": "x"}).encode()
                self.assertEqual(request(url + "api/checkin", "POST", {"Content-Type": "application/json"}, body)[0], 403)
                self.assertEqual(request(url + "api/checkin", "POST", {"X-Usage-Token": "wrong"}, body)[0], 403)
                self.assertEqual(request(url + "api/checkin", "POST", {"X-Usage-Token": read_text(info["token_file"]),
                                                                       "Origin": "http://attacker.example"}, body)[0], 403)
            finally:
                stop_server(proc)
        finally:
            shutil.rmtree(d)

    # ---- every write route is guarded
    def test_all_post_routes_are_in_write_paths(self):
        src = source_of("usage_history.py")
        m = re.search(r"WRITE_PATHS = \((.*?)\)\n", src, re.S)
        guarded = set(re.findall(r'"(/api/[a-z/-]+)"', m.group(1)))
        post = src[src.index("def do_POST"):src.index("def log_message")]
        routed = set(re.findall(r'"(/api/[a-z/-]+)"', post))
        self.assertGreaterEqual(len(guarded), 16)
        self.assertEqual(routed - guarded, set(), "a POST route is handled without the token check")

    def test_every_write_route_rejects_missing_wrong_token_and_get(self):
        src = source_of("usage_history.py")
        paths = sorted(set(re.findall(r'"(/api/[a-z/-]+)"', re.search(r"WRITE_PATHS = \((.*?)\)\n", src, re.S).group(1))))
        for p in paths:
            u = self.url + p.lstrip("/")
            self.assertEqual(request(u, "POST", {"Content-Type": "application/json"}, b"{}")[0], 403, p)
            self.assertEqual(request(u, "POST", {"X-Usage-Token": "x" * 43}, b"{}")[0], 403, p)
            self.assertEqual(request(u, "POST", {"X-Usage-Token": self.token + "x"}, b"{}")[0], 403, p)
            self.assertEqual(request(u, "POST", {"X-Usage-Token": self.token, "Sec-Fetch-Site": "cross-site"}, b"{}")[0], 403, p)
            self.assertEqual(request(u)[0], 405, p)
        self.alive()

    def test_token_is_random_and_long_enough(self):
        import local_guard
        toks = {local_guard.new_token() for _ in range(50)}
        self.assertEqual(len(toks), 50)
        self.assertTrue(all(len(t) >= 40 for t in toks))
        self.assertGreaterEqual(len(self.token), 40)

    def test_token_not_in_api_responses_or_other_files(self):
        for p in ("api/status", "api/checkins", "api/update/state", "api/ledger"):
            code, body = request(self.url + p)
            self.assertNotIn(self.token.encode(), body, p)
        for root, _dirs, files in os.walk(self.data):
            for f in files:
                if f == os.path.basename(self.token_file) or f.endswith(".token"):
                    continue
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(self.token.encode(), fh.read(), f)

    # ---- methods
    def test_unexpected_methods_rejected(self):
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "HEAD", "CONNECT"):
            for path in ("", "api/checkin", "api/status"):
                code, _ = request(self.url + path, m, {"X-Usage-Token": self.token}, b"{}" if m in ("PUT", "PATCH", "DELETE") else None)
                self.assertIn(code, (405, 501), (m, path))
        self.alive()

    # ---- path traversal
    def test_traversal_on_static_routes(self):
        os.makedirs(os.path.join(self.data, "history"), exist_ok=True)
        with open(os.path.join(self.data, "outside.png"), "wb") as fh:
            fh.write(b"\x89PNG-secret")
        with open(os.path.join(self.data, "history", "clock-1d.png"), "wb") as fh:
            fh.write(b"\x89PNG-ok")
        self.assertEqual(request(self.url + "clock-1d.png"), (200, b"\x89PNG-ok"))
        bad = ["../outside.png", "%2e%2e/outside.png", "%2e%2e%2foutside.png", "..%2foutside.png", "pal/../outside.png",
               "pal/%2e%2e/outside.png", "pal/x/../../outside.png", "....//outside.png", "..%5coutside.png", "%252e%252e/outside.png",
               "..;/outside.png", "clock-1d.png%00.png", "pal//outside.png", "/etc/passwd.png", "%2fetc%2fpasswd.png",
               "..\\outside.png", "outside.png/../clock-1d.png/../../x.png", "../../../../../../../../etc/passwd",
               "%2e%2e/%2e%2e/etc/passwd", "../history_server.json", "..%2fhistory_server.json", "../samples.csv", "../checkins.csv",
               "../usage_config.json", "../view_server.token", "history_server.json", "samples.csv", "checkins.csv",
               "../usage_history.py", "usage_history.py", ".git/config", ".env"]
        for b in bad:
            code, body = request(self.url + b)
            self.assertIn(code, (400, 404), b)
            self.assertNotIn(b"PNG-secret", body, b)
            self.assertNotIn(self.token.encode(), body, b)
        # raw request lines (a client may not normalise the path) and an absolute-form target
        for target in ("/../outside.png", "/pal/../../outside.png", "/%2e%2e/outside.png", "http://127.0.0.1:%d/../outside.png" % self.port):
            line = raw_http(self.port, ("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % (target, self.port)).encode())
            self.assertTrue(line and line.split()[1] in ("400", "404"), (target, line))
        self.alive()

    def test_ledger_route_serves_only_the_configured_file(self):
        code, body = request(self.url + "api/ledger?path=/etc/passwd&file=../../etc/passwd")
        self.assertNotIn(b"root:", body)

    # ---- malformed / oversized input
    def test_bad_query_params_do_not_crash(self):
        qs = ["from=%ZZ&to=%00", "from=2026-13-45&to=9999-99-99", "from=" + "9" * 5000, "q=" + "a" * 100000, "q=%FF%FE%C0%AF",
              "category=" + "&category=".join(["x"] * 2000), "q=%00%00", "name=" + "n" * 70000, "from=-1&to=-1", "&&&===&=&",
              "q=" + quote("<script>alert(1)</script>"), "categories=a,b,c,d,e,f", "from=2026-01-01&to=2025-01-01"]
        for path in ("api/checkins", "api/checkins/search"):
            for q in qs:
                code, body = request_or_reset(self.url + path + "?" + q)  # a >64 KB request line is cut off by the server (reset)
                if code is not None:
                    self.assertLess(code, 500, (path, q[:40]))
                self.assertEqual(request(self.url + "api/status")[0], 200)
        self.alive()

    def test_long_request_line_and_header_flood(self):
        line = raw_http(self.port, b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        self.assertTrue(line is None or line.split()[1] in ("414", "400", "404") or line == "", line)
        hdrs = "".join("X-%d: v\r\n" % i for i in range(300))
        line = raw_http(self.port, ("GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n%s\r\n" % (self.port, hdrs)).encode())
        self.assertTrue(line is None or line.split()[1] in ("431", "400", "404", "200"), line)
        self.alive()

    def test_bad_bodies_are_400_not_500(self):
        bodies = [b"", b"{", b"[]", b'"str"', b"null", b"123", b"\xff\xfe\x00", b"{" + b'"a":' * 5000, b"x" * 100000,
                  json.dumps({"name": "x" * 100000}).encode(), json.dumps({"categories": ["a"] * 100000}).encode()]
        h = {"X-Usage-Token": self.token, "Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        for b in bodies:
            code, _ = request_or_reset(self.url + "api/checkin", "POST", h, b)  # a big body may be cut off by the server (reset)
            if code is not None:
                self.assertIn(code, (200, 400, 429), b[:20])
        for b in (b"{", b"[]", b"null", b"\xff", b'{"pct":"abc"}', b'{"pct":1e999}', b'{"pct":-5}', b'{"pct":"NaN"}', b'{"pct":[]}'):
            code, _ = self.post("api/manual", b)
            self.assertEqual(code, 400, b)
        self.alive()

    def test_wrong_value_types_in_settings(self):
        weird = [[], {}, None, 1, 1e999, True, "x" * 5000, "../../etc/passwd", "\x00", {"a": [1]}, ["en"]]
        cases = {"api/lang": "lang", "api/tz": "tz", "api/anim": "anim", "api/mascot": "mascot", "api/seasonal": "seasonal",
                 "api/interval": "minutes", "api/display-offset": "offset", "api/patterns": "on", "api/palette/render": "palette"}
        for path, key in cases.items():
            for v in weird:
                code, _ = self.post(path, {key: v})
                self.assertLess(code, 500, (path, v if not isinstance(v, str) else v[:12]))
        self.alive()
        ps = os.path.join(self.data, "page_settings.json")
        if os.path.exists(ps):
            self.assertNotIn("passwd", read_text(ps))

    def test_checkin_field_limits(self):
        code, body = self.post("api/checkin", {"name": "n" * 500, "note": "x" * 5000, "category": "code", "phase": "point"})
        self.assertEqual(code, 200, body)
        r = json.loads(body)["checkin"]
        self.assertLessEqual(len(r["name"]), 40)
        self.assertLessEqual(len(r["note"]), 200)

    def test_oversized_body_is_capped(self):
        """The server answers 400 without reading the 2 MB body; the client may see that 400, or (race: the server closes
        while we are still sending) a broken pipe / reset. Either is "capped"; an answer must be a 4xx."""
        h = {"X-Usage-Token": self.token, "Origin": self.url.rstrip("/"), "Content-Type": "application/json"}
        code, _ = request_or_reset(self.url + "api/checkin", "POST", h, b'{"name":"cap","note":"' + b"y" * 2_000_000 + b'"}')
        if code is not None:
            self.assertEqual(code, 400)
        self.alive()

    def test_non_numeric_content_length(self):
        for cl in (b"abc", b"1e3", b"", b"99999999999999999999"):
            line = raw_http(self.port, b"POST /api/checkin HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Usage-Token: %s\r\n"
                            b"Content-Length: %s\r\nConnection: close\r\n\r\n{}" % (self.port, self.token.encode(), cl), timeout=5)
            self.assertTrue(line is None or not line.split()[1].startswith("5"), (cl, line))
        self.alive()

    def test_negative_content_length_does_not_hang(self):
        """``rfile.read(-1)`` would wait until the client closes: a negative Content-Length is refused with 400 at once."""
        for path in ("api/checkin", "api/manual"):
            line = raw_http(self.port, b"POST /%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Usage-Token: %s\r\n"
                            b"Content-Length: -1\r\n\r\n" % (path.encode(), self.port, self.token.encode()), timeout=4)
            self.assertIsNotNone(line, "no answer within 4 s (handler thread blocks on rfile.read(-1))")
            self.assertEqual(line.split()[1], "400", line)

    def test_slow_clients_do_not_block_others(self):
        socks = [socket.create_connection(("127.0.0.1", self.port), timeout=3) for _ in range(20)]
        try:
            for sk in socks:
                sk.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1")  # never finished
            self.alive()
        finally:
            for sk in socks:
                sk.close()

    # ---- headers
    def test_security_headers(self):
        import urllib.request
        for path in ("", "api/status", "api/checkins"):
            with urllib.request.urlopen(self.url + path, timeout=30) as r:
                h = r.headers
                self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)
                self.assertEqual(h.get("Referrer-Policy"), "same-origin", path)
                self.assertEqual(h.get("Cache-Control"), "no-store", path)
                self.assertNotIn("Access-Control-Allow-Origin", h, path)  # no CORS: other sites cannot read the answers
        with urllib.request.urlopen(self.url + "api/checkins", timeout=30) as r:
            self.assertTrue(r.headers.get("Content-Type", "").startswith("application/json"))

    def test_page_cannot_be_framed(self):
        import urllib.request
        for path in ("", "api/status"):
            with urllib.request.urlopen(self.url + path, timeout=30) as r:
                h = r.headers
                self.assertEqual(h.get("X-Frame-Options"), "DENY", path)
                self.assertIn("frame-ancestors 'none'", h.get("Content-Security-Policy") or "", path)

    def test_png_route_serves_only_plain_file_names(self):
        """A name with a backslash or colon is not served (Windows would read ``..\\x.png`` as a parent folder),
        even if a file with that odd name exists in the history folder."""
        hist = os.path.join(self.data, "history")
        os.makedirs(hist, exist_ok=True)
        odd = os.path.join(hist, "a\\b.png")
        with open(odd, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        try:
            for name in ("a\\b.png", "..\\..\\x.png", "C:\\x.png", "x:y.png", "pal/..\\x.png"):
                line = raw_http(self.port, ("GET /%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
                                            % (name, self.port)).encode(), timeout=5)
                self.assertEqual(line.split()[1], "404", (name, line))
            for name in ("..%5Cx.png", "%2e%2e%2fx.png", "pal/..%5Cx.png"):
                self.assertEqual(request(self.url + name)[0], 404, name)
        finally:
            os.remove(odd)
        import usage_history as uh
        for bad in ("", ".", "..", "a\\b", "c:x", "a\x00b"):
            self.assertFalse(uh._plain_name(bad), repr(bad))
        self.assertTrue(uh._plain_name("history-1m.png"))

    def test_cors_preflight_gets_no_allow_headers(self):
        code, _ = request(self.url + "api/checkin", "OPTIONS", {"Origin": "http://attacker.example",
                                                                 "Access-Control-Request-Method": "POST"})
        self.assertIn(code, (405, 501))

    # ---- XSS: stored values reach the page only as escaped JSON
    def test_hostile_checkin_values_are_escaped_in_page(self):
        code, base = request(self.url)
        self.assertEqual(code, 200)
        payloads = ['<img src=x onerror=alert(1)>', '</script><script>alert(2)</script>', '"><svg/onload=alert(3)>']
        for i, pl in enumerate(payloads):
            code, body = self.post("api/checkin", {"name": pl[:40], "note": pl, "category": "code", "phase": "point"})
            self.assertEqual(code, 200, body)
        _, page = request(self.url)
        page = page.decode("utf-8")
        base = base.decode("utf-8")
        self.assertEqual(page.count("<script"), base.count("<script"))
        self.assertEqual(page.count("</script>"), base.count("</script>"))
        for raw in ("<img src=x", "<svg/onload", "<script>alert"):
            self.assertNotIn(raw, page)
        self.assertIn("\\u003cimg src=x", page)  # present, but only inside the JSON data blob
        # the JSON API answers as JSON (never HTML), with the text intact
        code, body = request(self.url + "api/checkins")
        self.assertIn(b"onerror=alert(1)", body)
        self.assertNotIn(b"<html", body.lower())

    def test_hostile_search_query_is_not_reflected_as_html(self):
        import urllib.request
        q = "<script>alert(1)</script>"
        with urllib.request.urlopen(self.url + "api/checkins/search?q=" + quote(q), timeout=30) as r:
            ct = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8")
        self.assertTrue(ct.startswith("application/json"), ct)
        self.assertEqual(json.loads(body)["query"], q)
        code, body = request(self.url + "no-such-page?x=" + quote(q))
        self.assertNotIn(b"<script>alert", body)

    def test_hostile_breakdown_names_are_escaped(self):
        import usage_breakdown as ub
        from zoneinfo import ZoneInfo
        evil = "<img src=x onerror=alert(1)>"
        snap = {"captured_at": datetime.now(timezone.utc).isoformat(), "period": "this_week", "plan": evil, "weekly_used_pct": 10,
                "grokbot_used_pct": 5, "by_bot": [{"name": evil, "share": 5}],
                "by_activity": [{"name": "</script><script>alert(2)</script>", "share": 5}], "notes": evil}
        ub.import_text(__import__("pathlib").Path(self.data), json.dumps(snap), ZoneInfo("UTC"), datetime.now(ZoneInfo("UTC")))
        _, page = request(self.url)
        page = page.decode("utf-8")
        self.assertIn("\\u003cimg src=x", page)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<script>alert", page)

    def test_custom_mascot_svg_cannot_run_script(self):
        """A custom mascot is only ever an <img data: URI> (an SVG shown that way cannot run script); entities are refused."""
        import usage_history as uh
        d = scratch()
        try:
            with open(os.path.join(d, uh.MASCOT_FILE), "w", encoding="utf-8") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
            uri = uh.custom_mascot_uri(d)
            self.assertTrue(uri.startswith("data:image/svg+xml;base64,"))
            for bad in ('<!DOCTYPE svg [<!ENTITY x "y">]><svg>&x;</svg>', "<html><svg/></html>", "x" * (uh.MASCOT_MAX_BYTES + 1), "<svg>\x00</svg>"):
                with open(os.path.join(d, uh.MASCOT_FILE), "w", encoding="utf-8") as fh:
                    fh.write(bad)
                self.assertIsNone(uh.custom_mascot_uri(d), bad[:20])
            os.remove(os.path.join(d, uh.MASCOT_FILE))
            self.assertIsNone(uh.custom_mascot_uri(d))
        finally:
            shutil.rmtree(d)


class MalformedDataFileTests(unittest.TestCase):
    """Damaged or hostile CSV / JSON in the data folder must not take the server down."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(os.path.join(cls.data, "breakdown"))
        junk = (b"timestamp,name,note,category,id,phase,status\n"
                b"not-a-date,x,y,code,s1,start,\n"
                b"2026-10-01T10:00:00+08:00,\x00nul,\"unterminated,code,s2,start,\n")
        with open(os.path.join(cls.data, "checkins.csv"), "wb") as fh:
            fh.write(junk)
        with open(os.path.join(cls.data, "samples.csv"), "wb") as fh:
            fh.write(b"timestamp,pct\nnope,nope\n2026-10-01T10:00:00+08:00,999999\n,\n")
        with open(os.path.join(cls.data, "breakdown", "bad.json"), "wb") as fh:
            fh.write(b"\xff{not json")
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", cls.data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(cls.data, "history_server.json"))
        cls.url = info["url"]

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def test_server_survives_malformed_files(self):
        for p in ("api/status", "api/checkins", "api/checkins/search?q=x"):
            self.assertLess(request(self.url + p)[0], 500, p)
        code, _ = request(self.url)
        self.assertLess(code, 500)
        self.assertIsNone(self.proc.poll())

    def test_read_checkins_skips_bad_rows(self):
        import usage_checkins
        rows = usage_checkins.read(os.path.join(self.data, "checkins.csv"))
        self.assertTrue(all(r["name"] for r in rows))

    def test_oversized_csv_field_does_not_raise(self):
        """A single CSV field above Python's default 131072-character limit used to make ``csv`` raise ``_csv.Error``;
        the limit is raised and a row the csv module still cannot parse is skipped. The API caps name 40, note 200."""
        import usage_checkins
        d = scratch()
        try:
            p = os.path.join(d, "checkins.csv")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("timestamp,name,note,category,id,phase,status\n2026-10-01T10:00:00+08:00,n,%s,code,s1,point,\n" % ("z" * 300000))
            rows = usage_checkins.read(p)
            self.assertLessEqual(len(rows[0]["note"]), 200)
        finally:
            shutil.rmtree(d)

    def test_field_above_the_raised_limit_skips_only_that_row(self):
        import usage_checkins
        d = scratch()
        try:
            p = os.path.join(d, "checkins.csv")
            big = "z" * (usage_checkins.CSV_FIELD_LIMIT + 10)
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("timestamp,name,note,category,id,phase,status\n"
                         "2026-10-01T10:00:00+08:00,bad,%s,code,s1,point,\n"
                         "2026-10-01T11:00:00+08:00,good,ok,code,s2,point,\n" % big)
            self.assertEqual([r["name"] for r in usage_checkins.read(p)], ["good"])
        finally:
            shutil.rmtree(d)


class OversizedCsvFieldServerTests(unittest.TestCase):
    def test_oversized_samples_field_does_not_break_checkin_api(self):
        """Same cause as above, seen through the server: /api/checkins used to drop the connection."""
        d = scratch()
        data = os.path.join(d, "data")
        os.makedirs(data)
        try:
            with open(os.path.join(data, "samples.csv"), "wb") as fh:
                fh.write(b"timestamp,pct\n" + b"9" * 300000 + b",1\n")
            proc, info = start_server(["usage_history.py", "serve", "--data-dir", data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(data, "history_server.json"))
            try:
                self.assertEqual(request(info["url"] + "api/status")[0], 200)  # the process itself is fine
                self.assertEqual(request(info["url"] + "api/checkins")[0], 200)
            finally:
                stop_server(proc)
        finally:
            shutil.rmtree(d)


class CsvFormulaInjectionTests(unittest.TestCase):
    """The dashboard has no CSV export, but checkins.csv is a plain file people open in a spreadsheet."""

    def test_formula_like_values_are_neutralised_in_checkins_csv(self):
        """A name / note that starts with = + - @ (or tab / CR) gets a leading single quote in checkins.csv, so a
        spreadsheet opening that file does not treat it as a formula; the dashboard shows the text as typed."""
        import csv
        import usage_checkins
        d = scratch()
        try:
            p = os.path.join(d, "checkins.csv")
            usage_checkins.add(p, d, "=HYPERLINK(\"http://x\")", "point", ["code"], "@SUM(1+1)")
            with open(p, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            for r in rows:
                for k in ("name", "note"):
                    self.assertFalse(r[k][:1] in ("=", "+", "-", "@", "\t", "\r"), (k, r[k]))
            self.assertEqual(rows[0]["name"], "'=HYPERLINK(\"http://x\")")
            ev = usage_checkins.read(p)[0]  # read back: the quote is not shown
            self.assertEqual((ev["name"], ev["note"]), ("=HYPERLINK(\"http://x\")", "@SUM(1+1)"))
        finally:
            shutil.rmtree(d)

    def test_formula_quote_round_trips_exactly(self):
        import usage_checkins as uc
        for v in ("=1", "+1", "-1", "@a", "'=1", "'-x", "'plain", "plain", "a=1", "'", "", "-"):
            self.assertEqual(uc.unquote(uc.csv_safe(v)), v, v)
            self.assertFalse(uc.csv_safe(v).startswith(uc.FORMULA_START), v)

    def test_end_row_keeps_formula_like_name_closable(self):
        import csv
        import usage_checkins
        d = scratch()
        try:
            p = os.path.join(d, "checkins.csv")
            usage_checkins.add(p, d, "=cmd", "start", ["code"], "")
            usage_checkins.add(p, d, "=cmd", "end", None, "-done")
            events = usage_checkins.read(p)
            self.assertEqual([(e["name"], e["phase"]) for e in events], [("=cmd", "start"), ("=cmd", "end")])
            self.assertEqual(events[1]["note"], "-done")
            self.assertEqual(usage_checkins.open_sessions(events), [])
            with open(p, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    self.assertFalse(r["name"].startswith(usage_checkins.FORMULA_START))
        finally:
            shutil.rmtree(d)

    def test_csv_writer_keeps_values_in_their_own_cell(self):
        import csv
        import usage_checkins
        d = scratch()
        try:
            p = os.path.join(d, "checkins.csv")
            usage_checkins.add(p, d, 'a,"b"', "point", ["code"], 'x\ny,"z"')
            with open(p, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            self.assertNotIn("\n", rows[0]["note"])
        finally:
            shutil.rmtree(d)


class ViewServerSecurityTests(unittest.TestCase):
    """usage_view.py (the single-chart viewer)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(cls.data)
        cls.proc, info = start_server(["usage_view.py", "--no-open", "--refresh-mode", "manual", "--data-dir", cls.data,
                                       "--pct-source", "manual"], None)
        cls.url, cls.token_file = info["url"], info["token_file"]
        cls.token = read_text(cls.token_file)
        cls.port = urlparse(cls.url).port

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def alive(self):
        self.assertEqual(request(self.url + "api/status")[0], 200)

    def test_binds_to_loopback(self):
        self.assertEqual(urlparse(self.url).hostname, "127.0.0.1")
        ip = non_loopback_ip()
        if ip is None:
            self.skipTest("no non-loopback address on this machine")
        with self.assertRaises(OSError):
            socket.create_connection((ip, self.port), timeout=3).close()

    def test_unexpected_methods_and_routes(self):
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "HEAD"):
            self.assertIn(request(self.url + "api/refresh", m, {"X-Usage-Token": self.token})[0], (405, 501), m)
        for p in ("..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/etc/passwd", "image/../../x", "api/../../x", "../usage_view.py", "usage_view.py",
                  "view_server.token"):
            code, body = request(self.url + p)
            self.assertIn(code, (400, 404), p)
            self.assertNotIn(self.token.encode(), body)
        self.alive()

    def test_image_route_ignores_path_and_bad_palette(self):
        for q in ("palette=../../etc/passwd", "palette=%00", "palette=" + "a" * 10000, "palette[]=x", "path=/etc/passwd&file=/etc/passwd"):
            code, body = request(self.url + "image?" + q)
            self.assertIn(code, (200, 404), q)
            self.assertNotIn(b"root:", body)
        self.alive()

    def test_bad_post_bodies(self):
        h = {"X-Usage-Token": self.token, "Origin": self.url.rstrip("/")}
        for ct, b in (("application/json", b"{"), ("application/json", b"[]"), ("application/json", b"\xff\xfe"),
                      ("application/x-www-form-urlencoded", b"pct=%ZZ&pct="), ("text/plain", b"x" * 200000)):
            code, _ = request_or_reset(self.url + "api/refresh", "POST", dict(h, **{"Content-Type": ct}), b)
            if code is not None:
                self.assertLess(code, 500, (ct, b[:10]))
        self.alive()

    def test_non_numeric_content_length_does_not_kill_server(self):
        raw_http(self.port, b"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Usage-Token: %s\r\nContent-Length: abc\r\n"
                 b"Connection: close\r\n\r\n" % (self.port, self.token.encode()), timeout=5)
        self.alive()

    def test_negative_content_length_does_not_hang(self):
        line = raw_http(self.port, b"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Usage-Token: %s\r\n"
                        b"Content-Length: -1\r\n\r\n" % (self.port, self.token.encode()), timeout=4)
        self.assertIsNotNone(line)
        self.assertEqual(line.split()[1], "400", line)

    def test_oversized_content_length_is_413(self):
        """A declared body above the 1 MB limit is refused before anything is read (nothing is sent after the headers)."""
        for cl in (b"1048577", b"2000000", b"99999999999999999999"):
            line = raw_http(self.port, b"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Usage-Token: %s\r\n"
                            b"Content-Length: %s\r\nConnection: close\r\n\r\n" % (self.port, self.token.encode(), cl), timeout=4)
            self.assertIsNotNone(line, cl)
            self.assertEqual(line.split()[1], "413", (cl, line))
        self.alive()

    def test_body_below_the_limit_is_still_read(self):
        h = {"X-Usage-Token": self.token, "Origin": self.url.rstrip("/"), "Content-Type": "text/plain"}
        code, _ = request(self.url + "api/refresh", "POST", h, b"x" * 200000)
        self.assertLess(code, 500)
        self.assertNotEqual(code, 413)

    def test_security_headers(self):
        import urllib.request
        for path in ("", "api/status", "image"):
            try:
                r = urllib.request.urlopen(self.url + path, timeout=30)
            except urllib.error.HTTPError as e:  # /image is 404 until a chart exists: the headers are the same
                r = e
            with r:
                self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff", path)
                self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer", path)
                self.assertEqual(r.headers.get("Cache-Control"), "no-store", path)

    def test_configured_title_is_html_escaped(self):
        """``title`` from usage_config.json is escaped in the viewer page (only the owner of the config can set it)."""
        d = scratch()
        try:
            data = os.path.join(d, "data")
            os.makedirs(data)
            cfg = os.path.join(d, "cfg.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"title": "<script>alert(1)</script>"}, fh)
            proc, info = start_server(["usage_view.py", "--no-open", "--refresh-mode", "manual", "--data-dir", data,
                                       "--pct-source", "manual", "--config", cfg], None)
            try:
                _, page = request(info["url"])
            finally:
                stop_server(proc)
            self.assertNotIn(b"<script>alert(1)</script>", page)
            self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", page)
        finally:
            shutil.rmtree(d)


class LauncherAndRepoStaticTests(unittest.TestCase):
    """Checks of the tracked files (skipped outside a git checkout)."""

    def test_launchers_do_not_open_the_network(self):
        names = ("start-dashboard.sh", "start.ps1", "stop.ps1", "service.py", "Install-Mac.command", "Install-Windows.cmd", "register-task.ps1")
        for n in names:
            text = source_of(n)
            for bad in ("--allow-remote", "0.0.0.0", "--host", "history_host"):
                self.assertNotIn(bad, text, (n, bad))

    def test_example_config_is_loopback_and_debug_off(self):
        cfg = json.loads(source_of("usage_config.example.json"))
        self.assertEqual(cfg["history_host"], "127.0.0.1")
        self.assertFalse(cfg["debug_pct_override"])
        self.assertEqual(cfg["history_allowed_hosts"], [])
        self.assertEqual(cfg["view_allowed_hosts"], [])

    def test_no_dangerous_calls_in_python_sources(self):
        bad_os = {"system", "popen"}
        for f in tracked_files() or [n for n in os.listdir(ROOT) if n.endswith(".py")]:
            if not f.endswith(".py") or f.startswith("tests/"):
                continue
            tree = ast.parse(source_of(f), f)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Name):
                        self.assertNotIn(fn.id, ("eval", "exec"), "%s:%d" % (f, node.lineno))
                    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "os":
                        self.assertNotIn(fn.attr, bad_os, "%s:%d" % (f, node.lineno))
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            self.assertFalse(isinstance(kw.value, ast.Constant) and kw.value.value is True, "%s:%d shell=True" % (f, node.lineno))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                    for m in mods:
                        self.assertNotIn(m.split(".")[0], ("pickle", "marshal", "shelve"), "%s:%d" % (f, node.lineno))

    def test_page_scripts_do_not_parse_user_html(self):
        """Check-in, breakdown and clock-tooltip scripts build the page with textContent only."""
        import usage_breakdown_ui
        import usage_checkins_ui
        for js in (usage_checkins_ui.JS, usage_breakdown_ui.JS if hasattr(usage_breakdown_ui, "JS") else ""):
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
                self.assertNotIn(bad, js, bad)

    def test_gitignore_covers_runtime_secrets(self):
        ignore = source_of(".gitignore").split()
        for pat in ("*.token", "history_server.json", "usage_config.json", "usage-data/", "data/", "*.log", "*.pid"):
            self.assertIn(pat, ignore)

    def test_no_secret_or_runtime_files_tracked(self):
        files = tracked_files()
        if not files:
            self.skipTest("not a git checkout")
        for f in files:
            base = os.path.basename(f)
            self.assertFalse(base.endswith((".token", ".pem", ".key", ".log", ".pid", ".csv", ".bak")), f)
            self.assertNotIn(base, ("history_server.json", "usage_config.json", "service.json", ".env", "state.json"), f)
            self.assertFalse(f.startswith(("usage-data/", "data/")) and not f.endswith(".md"), f)

    def test_no_secrets_or_personal_emails_in_tracked_text_files(self):
        files = tracked_files()
        if not files:
            self.skipTest("not a git checkout")
        secret = re.compile(r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
                            r"|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|[Bb]earer [A-Za-z0-9._~+/-]{20,})")
        assigned = re.compile(r"(?:api[_-]?key|secret|passwd|password|token)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']")
        email = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
        ok_mail = re.compile(r"(@example\.(com|org|net|invalid)$|@localhost$|\.invalid$|@users\.noreply\.github\.com$|^noreply@)", re.I)
        for f in files:
            if f.lower().endswith((".png", ".gif", ".jpg", ".ico")):
                continue
            with open(os.path.join(ROOT, f), "rb") as fh:
                raw = fh.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for m in secret.finditer(text):
                self.fail("secret-looking text in %s: %s..." % (f, m.group(0)[:6]))
            if not f.startswith("tests/"):  # tests invent fake tokens on purpose
                for m in assigned.finditer(text):
                    self.fail("hard-coded credential-looking assignment in %s: %s..." % (f, m.group(0)[:8]))
            for m in email.finditer(text):
                self.assertTrue(ok_mail.search(m.group(0)), "email address in %s" % f)

    def test_server_logs_stay_quiet(self):
        """Request handlers do not log request lines (they would carry query text) and nothing prints the token."""
        for f in ("usage_history.py", "usage_view.py"):
            text = source_of(f)
            self.assertRegex(text, r"def log_message\(self, \*?\w+[^)]*\):\s*(\n\s*)?(pass|return)")
            for line in text.splitlines():
                if "print(" in line or "logging." in line:
                    self.assertNotRegex(line, r"\{[^}]*\btoken\b[^}]*\}|\+\s*(self\.)?token\b", f + ": " + line.strip()[:60])


if __name__ == "__main__":
    unittest.main()
