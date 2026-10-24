"""Unit tests for desktop_notify.py and usage_ledger.py (stdlib unittest; nothing is executed or shown).

Run:  python3 -m unittest discover -s tests -v
"""
import base64
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import desktop_notify as dn  # noqa: E402
import usage_ledger as ul  # noqa: E402

TZ = timezone(timedelta(hours=-5))  # any fixed offset works; tests never assume the host zone
NOW = datetime(2030, 3, 6, 12, 0, tzinfo=TZ)


def which_of(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


class NotifierTests(unittest.TestCase):
    EVIL = "it's 95% $(rm -rf ~) `x` \"q\" ‘a’ <b>&amp;\n;exit"

    def decode(self, cmd):
        i = cmd.index("-EncodedCommand")
        return base64.b64decode(cmd[i + 1]).decode("utf-16-le")

    def test_windows_command_is_encoded_and_quoted(self):
        cmd = dn.build_windows_command("Usage", self.EVIL, exe="powershell.exe")
        self.assertEqual(cmd[0], "powershell.exe")
        self.assertIn("-NoProfile", cmd)
        self.assertTrue(all(self.EVIL not in a for a in cmd))  # message is never a raw argv item
        script = self.decode(cmd)
        m = re.search(r"\$m=('(?:[^']|'')*');", script)
        self.assertIsNotNone(m, script)
        lit = m.group(1)[1:-1]
        self.assertNotRegex(lit.replace("''", ""), r"'")  # every quote inside the literal is doubled
        self.assertNotIn("\n", script)
        self.assertIn("BurntToast", script)
        self.assertIn("CreateTextNode($m)", script)

    def test_ps_quote_typographic(self):
        self.assertEqual(dn.ps_quote("a’b"), "'a’’b'")

    def test_macos_and_linux_argv(self):
        mac = dn.build_macos_command("T", self.EVIL)
        self.assertEqual(mac[0], "osascript")
        self.assertEqual(mac[-3], "--")
        self.assertEqual(mac[-2:], ["T", dn.clean(self.EVIL, dn.MAX_MSG)])
        self.assertTrue(all(self.EVIL[:5] not in a for a in mac[:-1]))  # message only as data
        lin = dn.build_linux_command("T", "M")
        self.assertEqual(lin, ["notify-send", "--app-name", "Grok Bot Usage & Check-in", "--", "T", "M"])

    def test_pick_backend(self):
        self.assertEqual(dn.pick_backend("win32", which_of("powershell"))[0], "windows")
        self.assertEqual(dn.pick_backend("win32", which_of("pwsh")), ("windows", "/usr/bin/pwsh"))
        self.assertEqual(dn.pick_backend("win32", which_of()), (None, "no powershell"))
        self.assertEqual(dn.pick_backend("darwin", which_of("osascript"))[0], "macos")
        self.assertEqual(dn.pick_backend("darwin", which_of()), (None, "no osascript"))
        self.assertEqual(dn.pick_backend("linux", which_of("notify-send"))[0], "linux")
        self.assertEqual(dn.pick_backend("linux", which_of()), (None, "no notifier"))

    def test_notify_noop_without_notifier(self):
        old = os.environ.get("PATH")
        try:
            os.environ["PATH"] = tempfile.mkdtemp()  # empty PATH: no notify-send / osascript / powershell
            self.assertTrue(dn.notify("T", "M").startswith("noop:"))
        finally:
            os.environ["PATH"] = old
        self.assertEqual(dn.notify("T", "M", enabled=False), "noop:disabled")

    def test_clean_strips_control_chars(self):
        self.assertEqual(dn.clean("a\x07b\nc", 10), "a b c")
        self.assertEqual(len(dn.clean("x" * 500, dn.MAX_MSG)), dn.MAX_MSG)


def h(n):
    return NOW - timedelta(hours=n)


class SpikeTests(unittest.TestCase):
    def test_normal_jump(self):
        r = ul.compute_spike([(h(24.2), 30), (h(12), 40)], NOW, 55, None, 20)
        self.assertTrue(r["flag"]); self.assertEqual(r["points"], 25.0)
        self.assertEqual(ul._parse(r["since"]), h(24.2))

    def test_below_threshold(self):
        r = ul.compute_spike([(h(25), 30)], NOW, 45, None, 20)
        self.assertFalse(r["flag"]); self.assertEqual(r["points"], 15.0)

    def test_drop_means_reset(self):
        # 80 -> 5 dropped (reset ~10h ago), so the baseline is 0 at the reset, not 80
        r = ul.compute_spike([(h(25), 80), (h(12), 82), (h(8), 5)], NOW, 30, None, 20)
        self.assertTrue(r["flag"]); self.assertEqual(r["points"], 30.0)
        self.assertEqual(ul._parse(r["since"]), h(10))

    def test_period_start_within_24h(self):
        r = ul.compute_spike([(h(30), 90)], NOW, 22, h(6), 20)
        self.assertTrue(r["flag"]); self.assertEqual(r["points"], 22.0)

    def test_no_baseline(self):
        r = ul.compute_spike([(h(3), 10)], NOW, 50, None, 20)
        self.assertFalse(r["flag"]); self.assertIsNone(r["points"])

    def test_baseline_too_old(self):
        r = ul.compute_spike([(h(40), 10)], NOW, 50, None, 20)
        self.assertIsNone(r["points"])

    def test_unavailable_never_spikes(self):
        self.assertFalse(ul.compute_spike([(h(25), 0)], NOW, None, None, 20)["flag"])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "ledger.json")
        self.sent = []

    def upd(self, pct, now=NOW, **kw):
        return ul.update(self.path, pct=pct, samples=kw.pop("samples", ()), tz=TZ, now=now, lang=kw.pop("lang", "en"),
                         notify=lambda t, m: self.sent.append((t, m)) or "sent:test", **kw)

    def log(self):
        p = os.path.join(self.d, "alerts.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_ok_fields_and_reset_in_display_tz(self):
        reset = datetime(2030, 3, 8, 14, 0, tzinfo=timezone.utc)
        led = self.upd(42, reset_at=reset)
        with open(self.path, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertNotIn("_newAlerts", on_disk)
        self.assertEqual(on_disk["status"], "ok")
        self.assertEqual(on_disk["resetsAt"], "2030-03-08T09:00:00-05:00")
        self.assertEqual(on_disk["asOf"], "2030-03-06T12:00:00-05:00")
        self.assertEqual(on_disk["lastGood"]["weeklyPercentUsed"], 42.0)
        self.assertEqual(led["alerts"], [])

    def test_unavailable_never_zero(self):
        self.upd(37)
        for i in range(3):
            led = self.upd(None, now=NOW + timedelta(hours=i + 1), error="timeout")
        self.assertEqual(led["status"], "unavailable")
        self.assertIsNone(led["weeklyPercentUsed"]); self.assertIsNone(led["resetsAt"])
        self.assertEqual(led["lastGood"]["weeklyPercentUsed"], 37.0)
        self.assertEqual(led["consecutiveFailures"], 3)
        self.assertEqual([a["kind"] for a in led["alerts"]], ["unavailable"])
        self.assertEqual(len(self.sent), 1)

    def test_unavailable_below_threshold_is_quiet(self):
        self.upd(None, error="x"); self.upd(None, now=NOW + timedelta(hours=1), error="x")
        self.assertEqual(self.sent, []); self.assertEqual(self.log(), [])

    def test_budget_dedupe_once_per_day(self):
        self.upd(91, cfg={"over_budget_percent": 90})
        self.upd(93, now=NOW + timedelta(hours=1), cfg={"over_budget_percent": 90})
        self.assertEqual(len(self.sent), 1); self.assertEqual(len(self.log()), 1)
        self.assertIn("91%", self.sent[0][1]); self.assertIn("reset unknown", self.sent[0][1])
        self.upd(95, now=NOW + timedelta(days=1), cfg={"over_budget_percent": 90})
        self.assertEqual(len(self.sent), 2)  # new day -> new alert id

    def test_spike_alert_and_disable(self):
        led = self.upd(60, samples=[(h(24), 30)], cfg={"desktop_notify": False})
        self.assertTrue(led["spike"]["flag"])
        self.assertEqual(self.sent, [])  # toast disabled ...
        self.assertEqual(self.log()[0]["notify"], "noop:disabled")  # ... but still logged once

    def test_read_freshness(self):
        self.upd(10)
        self.assertTrue(ul.read(self.path, 6, now=NOW + timedelta(hours=5))["fresh"])
        self.assertFalse(ul.read(self.path, 6, now=NOW + timedelta(hours=7))["fresh"])
        self.assertFalse(ul.read(os.path.join(self.d, "missing.json"))["fresh"])


if __name__ == "__main__":
    unittest.main()
