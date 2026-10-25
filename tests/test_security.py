"""Local server hardening: token on writes, POST only, Host / Origin checks.

Servers run in scratch dirs on a random port; nothing touches real data.
"""
import json
import os
import shutil
import stat
import unittest
from urllib.parse import urlparse

from _helpers import read_text, request, scratch, start_server, stop_server

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


if __name__ == "__main__":
    unittest.main()
