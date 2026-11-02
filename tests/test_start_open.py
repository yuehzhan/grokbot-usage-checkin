"""service.py start --open: the page is opened after every successful start (also when already running),
only once the viewer answers, with fallbacks when ``webbrowser`` fails; start.ps1 / start-dashboard.sh show the URL.

No real browser is ever launched: webbrowser / os.startfile / subprocess.Popen are replaced by recorders.
"""
import http.server
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import service  # noqa: E402

URL = "http://127.0.0.1:6484/"


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404 if self.path == "/missing" else 200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class WaitHttpTests(unittest.TestCase):
    def test_ready_when_server_answers_even_with_error_status(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_address[1]
            self.assertTrue(service.wait_http(f"http://127.0.0.1:{port}/", 5))
            self.assertTrue(service.wait_http(f"http://127.0.0.1:{port}/missing", 5))  # 404 = it is up
        finally:
            srv.shutdown()
            srv.server_close()

    def test_not_ready_when_nothing_listens(self):
        s = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
        port = s.server_address[1]
        s.server_close()
        self.assertFalse(service.wait_http(f"http://127.0.0.1:{port}/", 0.5))


class OpenPageTests(unittest.TestCase):
    def setUp(self):
        self.wait = mock.patch.object(service, "wait_http", return_value=True)
        self.wait.start()
        self.addCleanup(self.wait.stop)

    def test_webbrowser_success(self):
        with mock.patch("webbrowser.open", return_value=True) as wb:
            r = service.open_page(URL)
        wb.assert_called_once_with(URL)
        self.assertEqual((r["opened"], r["method"]), (True, "webbrowser"))

    def test_waits_for_the_server_before_opening(self):
        order = []
        with mock.patch.object(service, "wait_http", side_effect=lambda *a: order.append("wait") or True), \
                mock.patch("webbrowser.open", side_effect=lambda u: order.append("open") or True):
            service.open_page(URL)
        self.assertEqual(order, ["wait", "open"])

    def test_falls_back_when_webbrowser_returns_false(self):
        popen = mock.Mock()
        with mock.patch("webbrowser.open", return_value=False), mock.patch.object(service.subprocess, "Popen", popen), \
                mock.patch.object(service.os, "name", "posix"), mock.patch.object(service.sys, "platform", "linux"):
            r = service.open_page(URL)
        self.assertEqual((r["opened"], r["method"]), (True, "xdg-open"))
        self.assertEqual(popen.call_args[0][0], ["xdg-open", URL])

    def test_falls_back_when_webbrowser_raises(self):
        popen = mock.Mock()
        with mock.patch("webbrowser.open", side_effect=RuntimeError("boom")), \
                mock.patch.object(service.subprocess, "Popen", popen), \
                mock.patch.object(service.os, "name", "posix"), mock.patch.object(service.sys, "platform", "darwin"):
            r = service.open_page(URL)
        self.assertEqual((r["opened"], r["method"]), (True, "open"))
        self.assertEqual(popen.call_args[0][0], ["open", URL])

    def test_windows_fallback_order_startfile_then_cmd_start(self):
        calls = []
        startfile = mock.Mock(side_effect=lambda u: calls.append(("startfile", u)))
        with mock.patch("webbrowser.open", return_value=False), mock.patch.object(service.os, "name", "nt"), \
                mock.patch.object(service.os, "startfile", startfile, create=True):
            r = service.open_page(URL)
        self.assertEqual((r["opened"], r["method"]), (True, "startfile"))
        self.assertEqual(calls, [("startfile", URL)])
        popen = mock.Mock()
        with mock.patch("webbrowser.open", return_value=False), mock.patch.object(service.os, "name", "nt"), \
                mock.patch.object(service.os, "startfile", mock.Mock(side_effect=OSError("no handler")), create=True), \
                mock.patch.object(service.subprocess, "Popen", popen):
            r = service.open_page(URL)
        self.assertEqual((r["opened"], r["method"]), (True, "cmd-start"))
        self.assertEqual(popen.call_args[0][0], ["cmd", "/c", "start", "", URL])

    def test_everything_fails_is_reported_not_raised(self):
        with mock.patch("webbrowser.open", return_value=False), \
                mock.patch.object(service.subprocess, "Popen", side_effect=OSError("nope")), \
                mock.patch.object(service.os, "name", "posix"):
            r = service.open_page(URL)
        self.assertEqual(r["opened"], False)

    def test_only_plain_loopback_urls_reach_an_opener(self):
        for bad in (None, "", "file:///etc/passwd", "http://example.com/", "http://127.0.0.1:1/ & calc",
                    'http://127.0.0.1:1/"x', "javascript:alert(1)", "http://127.0.0.1.evil.com:80/"):
            with mock.patch("webbrowser.open", return_value=True) as wb:
                r = service.open_page(bad)
            self.assertFalse(r["opened"], bad)
            wb.assert_not_called()
        for good in (URL, "http://localhost:8080/", "http://127.0.0.1:6484"):
            with mock.patch("webbrowser.open", return_value=True):
                self.assertTrue(service.open_page(good)["opened"], good)


class StartOpensTests(unittest.TestCase):
    """start(): opens on a fresh start AND when already running; not without --open."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)

    def _status(self, running=True, url=URL):
        return {"ok": True, "data_dir": str(self.d), "config": None, "running": running, "supervisor_pid": 1 if running else None,
                "server_pid": 2 if running else None, "sampler_pid": None, "url": url, "logs": []}

    def test_already_running_opens_existing_url(self):
        with mock.patch.object(service, "status", return_value=self._status()), \
                mock.patch.object(service, "open_page", return_value={"opened": True, "method": "webbrowser", "ready": True}) as op:
            r = service.start(None, self.d, open_browser=True)
        op.assert_called_once_with(URL)
        self.assertEqual((r["ok"], r["note"], r["url"], r["opened"]), (True, "already running", URL, True))

    def test_already_running_waits_for_a_url_that_is_not_written_yet(self):
        seq = [self._status(url=None), self._status(url=None), self._status()]
        with mock.patch.object(service, "status", side_effect=seq), mock.patch.object(service.time, "sleep"), \
                mock.patch.object(service, "open_page", return_value={"opened": True}) as op:
            r = service.start(None, self.d, open_browser=True)
        op.assert_called_once_with(URL)
        self.assertEqual(r["url"], URL)

    def test_already_running_without_open_flag_does_not_open(self):
        with mock.patch.object(service, "status", return_value=self._status()), \
                mock.patch.object(service, "open_page") as op:
            r = service.start(None, self.d, open_browser=False)
        op.assert_not_called()
        self.assertNotIn("opened", r)

    def test_fresh_start_opens_once_the_url_is_known(self):
        seq = [self._status(running=False, url=None), self._status(running=False, url=None), self._status()]
        with mock.patch.object(service, "status", side_effect=seq), mock.patch.object(service, "cleanup"), \
                mock.patch.object(service.time, "sleep"), mock.patch.object(service.subprocess, "Popen") as popen, \
                mock.patch.object(service, "open_page", return_value={"opened": True}) as op:
            r = service.start(None, self.d, open_browser=True)
        popen.assert_called_once()
        op.assert_called_once_with(URL)
        self.assertTrue(r["ok"])

    def test_fresh_start_without_open_flag_and_failed_start(self):
        with mock.patch.object(service, "status", return_value=self._status(running=False, url=None)), \
                mock.patch.object(service, "cleanup"), mock.patch.object(service.subprocess, "Popen"), \
                mock.patch.object(service, "open_page") as op:
            r = service.start(None, self.d, open_browser=True, wait=0)
        op.assert_not_called()
        self.assertFalse(r["ok"])


class LauncherTests(unittest.TestCase):
    def test_start_ps1_shows_url_respects_nobrowser_and_keeps_window_open(self):
        text = (ROOT / "start.ps1").read_text(encoding="utf-8")
        self.assertIn("$NoBrowser", text)
        self.assertIn("if (-not $NoBrowser) { $argsList += '--open' }", text)
        self.assertIn("Write-Host \"    $url\"", text)
        self.assertIn("Start-Process -FilePath $url", text)  # second try when service.py could not open it
        self.assertIn("desktop_notify.py", text)
        self.assertIn("Start-Sleep", text)
        self.assertTrue((ROOT / "desktop_notify.py").exists())
        # no wait and no toast when the caller asked for no browser (self-update runs it hidden with -NoBrowser)
        block = text[text.index("if (-not $NoBrowser -and $url)"):]
        self.assertLess(block.index("desktop_notify.py"), block.index("Start-Sleep"))

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "needs bash")
    def test_start_dashboard_sh_prints_the_url(self):
        d = Path(tempfile.mkdtemp())
        try:
            shutil.copy(ROOT / "start-dashboard.sh", d / "start-dashboard.sh")
            (d / ".venv" / "bin").mkdir(parents=True)
            py = d / ".venv" / "bin" / "python"
            py.write_text('#!/bin/sh\necho \'{"ok": true, "url": "http://127.0.0.1:6484/", "note": "already running"}\'\n')
            py.chmod(py.stat().st_mode | stat.S_IXUSR)
            p = subprocess.run(["bash", str(d / "start-dashboard.sh")], capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("http://127.0.0.1:6484/", p.stdout)
        finally:
            shutil.rmtree(d, True)


if __name__ == "__main__":
    unittest.main()
