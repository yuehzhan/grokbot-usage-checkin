"""Windows-specific pieces that can be checked on any OS.

* vt_screen: ConPTY sends cursor moves instead of spaces; the replayed screen must read like tmux capture-pane.
* manual_entry: the values typed on the page / CLI.
* proc_util: pid checks (Windows must not use os.kill(pid, 0), which sends CTRL_C there).
* usage_lock: the msvcrt branch, with a stand-in msvcrt module.
* service.py: start / status / stop in a scratch folder (manual mode, so Grok Build is never started).
"""
import builtins
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from _helpers import ROOT, request, scratch

import grok_usage_reader  # noqa: E402
import manual_entry  # noqa: E402
import proc_util  # noqa: E402
import vt_screen  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "screens")


def conpty_style(text):
    """Re-encode a plain screen the way ConPTY tends to: absolute positioning, CUF for runs of spaces."""
    out = ["\x1b[?1049h\x1b[2J\x1b[H"]
    for y, line in enumerate(text.splitlines()):
        out.append(f"\x1b[{y + 1};1H")
        i = 0
        while i < len(line):
            if line[i] == " ":
                j = i
                while j < len(line) and line[j] == " ":
                    j += 1
                out.append(f"\x1b[{j - i}C")
                i = j
            else:
                out.append(line[i])
                i += 1
        out.append("\x1b[K")
    out.append("\x1b[?25l\x1b]0;grok\x07")
    return "".join(out)


class VtScreenTests(unittest.TestCase):
    def test_cursor_forward_keeps_words_apart(self):
        s = vt_screen.Screen(40, 5)
        s.feed("Weekly\x1b[1Climit")
        self.assertEqual(s.text().splitlines()[0], "Weekly limit")

    def test_conpty_replay_classifies_like_tmux(self):
        for name, want in (("ok_usage.txt", "ok"), ("ok_wide_panel.txt", "ok"), ("update_prompt.txt", "update_prompt"),
                           ("login_required.txt", "login_required")):
            with open(os.path.join(FIX, name), encoding="utf-8") as fh:
                plain = fh.read()
            s = vt_screen.Screen(200, 50)
            stream = conpty_style(plain)
            for k in range(0, len(stream), 7):  # split escape sequences across chunks
                s.feed(stream[k:k + 7])
            state_plain, pct_plain, _ = grok_usage_reader.classify_screen(plain)
            state, pct, _ = grok_usage_reader.classify_screen(s.text())
            self.assertEqual(state, want, name)
            self.assertEqual((state, pct), (state_plain, pct_plain), name)

    def test_reset_line_survives_replay(self):
        with open(os.path.join(FIX, "ok_usage.txt"), encoding="utf-8") as fh:
            plain = fh.read()
        s = vt_screen.Screen(200, 50)
        s.feed(conpty_style(plain).encode("utf-8"))
        self.assertIn("Resets: October 31, 09:00", s.text())

    def test_answers_terminal_queries(self):
        s = vt_screen.Screen(80, 24)
        s.feed("\x1b[5;10H\x1b[6n\x1b[c")
        self.assertEqual(s.take_responses(), ["\x1b[5;10R", "\x1b[?1;2c"])
        self.assertEqual(s.take_responses(), [])

    def test_erase_and_overwrite(self):
        s = vt_screen.Screen(20, 3)
        s.feed("hello world\r\x1b[2Kbye\n\x1b[1;1H\x1b[J")
        self.assertEqual(s.text().strip(), "")
        s.feed("ab\x08c")
        self.assertEqual(s.text().splitlines()[0], "ac")

    def test_wide_chars_and_alt_screen(self):
        s = vt_screen.Screen(20, 3)
        s.feed("main")
        s.feed("\x1b[?1049h用量 42%")
        self.assertIn("42%", s.text())
        s.feed("\x1b[?1049l")
        self.assertEqual(s.text().splitlines()[0], "main")


class ManualEntryTests(unittest.TestCase):
    tz = timezone(timedelta(hours=2))
    now = datetime(2026, 10, 24, 11, 0, tzinfo=tz)  # a Saturday

    def test_pct(self):
        self.assertEqual(manual_entry.parse_pct("42"), 42.0)
        self.assertEqual(manual_entry.parse_pct(" 42.5 % "), 42.5)
        self.assertEqual(manual_entry.parse_pct(0), 0.0)
        for bad in ("", "abc", "-1", "201", "nan", None):
            with self.assertRaises(manual_entry.ManualError, msg=repr(bad)):
                manual_entry.parse_pct(bad)

    def test_reset_forms(self):
        f = manual_entry.parse_reset_input
        want = datetime(2026, 10, 31, 9, 0, tzinfo=self.tz)
        for text in ("Sat 09:00", "Oct 31, 09:00", "Resets: October 31, 09:00", "2026-10-31 09:00",
                     "2026-10-31T09:00+02:00", "2026-10-24 09:00"):  # last one is past -> next period
            self.assertEqual(f(text, self.now, self.tz), want, text)
        self.assertIsNone(f("", self.now, self.tz))
        for bad in ("someday", "2026-12-31 09:00", "x" * 80):
            with self.assertRaises(manual_entry.ManualError, msg=bad):
                f(bad, self.now, self.tz)


class ProcUtilTests(unittest.TestCase):
    def test_pid_alive(self):
        self.assertTrue(proc_util.pid_alive(os.getpid()))
        self.assertFalse(proc_util.pid_alive(0))
        self.assertFalse(proc_util.pid_alive(None))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(proc_util.pid_alive(p.pid))

    def test_looks_like_python_and_terminate(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            self.assertTrue(proc_util.looks_like_python(p.pid))
            proc_util.terminate(p.pid, timeout=5)
            p.wait(timeout=10)
        finally:
            if p.poll() is None:
                p.kill()

    def test_never_signal_zero_on_windows(self):
        if os.name == "nt":
            self.skipTest("POSIX-only check")
        with mock.patch("os.kill") as k:
            proc_util.pid_alive(os.getpid())
        self.assertTrue(all(c.args[1] == 0 for c in k.call_args_list))  # POSIX uses signal 0 only


class FakeMsvcrt(types.ModuleType):
    """Byte-range lock stand-in: one holder per file path, like msvcrt.locking on byte 0."""
    LK_NBLCK, LK_UNLCK = 2, 0

    def __init__(self):
        super().__init__("msvcrt")
        self.owner = {}
        self.calls = []
        self.mu = threading.Lock()

    def locking(self, fd, mode, nbytes):
        path = os.path.realpath(f"/proc/self/fd/{fd}") if os.path.exists("/proc/self/fd") else fd
        self.calls.append((mode, nbytes))
        with self.mu:
            if mode == self.LK_NBLCK:
                if self.owner.get(path, fd) != fd:
                    raise OSError(36, "Resource deadlock avoided")
                self.owner[path] = fd
            elif mode == self.LK_UNLCK:
                self.owner.pop(path, None)


class WindowsLockBranchTests(unittest.TestCase):
    def test_msvcrt_branch_exclusive_and_reentrant(self):
        if not os.path.exists("/proc/self/fd"):
            self.skipTest("needs /proc to map fds to paths")
        import usage_lock
        fake = FakeMsvcrt()
        real_import = builtins.__import__

        def no_fcntl(name, *a, **kw):
            if name == "fcntl":
                raise ImportError("no fcntl on Windows")
            return real_import(name, *a, **kw)

        d = scratch()
        try:
            with mock.patch.dict(sys.modules, {"msvcrt": fake}), mock.patch("builtins.__import__", no_fcntl):
                order = []
                with usage_lock.write_lock(d):
                    with usage_lock.write_lock(d):  # re-entrant in one thread
                        order.append("inner")

                    def other():
                        with usage_lock.write_lock(d, timeout=10):
                            order.append("other")
                    t = threading.Thread(target=other)
                    t.start()
                    time.sleep(0.3)
                    order.append("outer-done")
                t.join(10)
                self.assertEqual(order, ["inner", "outer-done", "other"])
                got = []
                with usage_lock.write_lock(d):
                    def blocked():
                        try:
                            with usage_lock.write_lock(d, timeout=0.3):
                                got.append("acquired")
                        except usage_lock.LockTimeout:
                            got.append("timeout")
                    t2 = threading.Thread(target=blocked)
                    t2.start()
                    t2.join(10)
                self.assertEqual(got, ["timeout"])
            self.assertTrue(all(n == 1 for _, n in fake.calls))
            self.assertIn(fake.LK_UNLCK, [m for m, _ in fake.calls])
        finally:
            shutil.rmtree(d)


class ServiceTests(unittest.TestCase):
    def test_start_manual_stop(self):
        d = scratch()
        cfg = os.path.join(d, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"pct_source": "manual", "reset_source": "manual", "reset_anchor": "Sat 09:00", "data_dir": "data",
                       "render_all_palettes": False, "desktop_notify": False, "tz": "UTC"}, fh)
        env = dict(os.environ, MPLBACKEND="Agg", USAGE_NO_AUTO_UPDATE_CHECK="1")
        run = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, "service.py")] + list(a) + ["--config", cfg],
                                        capture_output=True, text=True, timeout=120, env=env, cwd=d)
        try:
            r = run("start")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            info = json.loads(r.stdout)
            self.assertTrue(info["ok"])
            data = os.path.join(d, "data")  # relative data_dir = next to the config file
            self.assertTrue(os.path.exists(os.path.join(data, "service.json")))
            again = json.loads(run("start").stdout)
            self.assertTrue(again.get("already_running") or again.get("url") == info["url"], again)
            with open(os.path.join(data, "history_server.token"), encoding="utf-8") as fh:
                tok = fh.read().strip()
            url = info["url"].rstrip("/")
            hdr = {"X-Usage-Token": tok, "Content-Type": "application/json"}
            code, body = request(url + "/api/manual", "POST", hdr, json.dumps({"pct": "37", "reset": "Sat 09:00"}).encode())
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)["pct"], 37.0)
            code, _ = request(url + "/api/manual", "POST", hdr, b'{"pct": "500"}')
            self.assertEqual(code, 400)
            code, _ = request(url + "/api/manual", "POST", {"Content-Type": "application/json"}, b'{"pct": "37"}')
            self.assertEqual(code, 403)
            code, body = request(url + "/api/refresh", "POST", hdr, b"{}")
            self.assertEqual((code, json.loads(body).get("code")), (400, "manual_mode"))
            st = json.loads(run("status").stdout)
            self.assertTrue(st["running"])
            pids = [st["supervisor_pid"], st["server_pid"], st["sampler_pid"]]
            r = run("stop")
            self.assertEqual(r.returncode, 0, r.stderr)
            time.sleep(0.5)
            for p in pids:
                self.assertFalse(proc_util.pid_alive(p), p)
            for n in ("service.json", "service.pid", "history_server.json", "history_server.token", "sampler.pid"):
                self.assertFalse(os.path.exists(os.path.join(data, n)), n)
            self.assertFalse(json.loads(run("status").stdout)["running"])
        finally:
            run("stop")
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
