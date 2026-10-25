"""Grok Build screen reading: sample screens are classified; anything that is not a clear
usage screen becomes "unavailable" with a reason - never 0%. tmux is faked; grok is never run."""
import glob
import os
import shutil
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from _helpers import scratch

import grok_usage_reader as gr  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "screens")


def screen(name):
    return Path(FIX, name).read_text(encoding="utf-8")


class ClassifyTests(unittest.TestCase):
    def test_ok_screens(self):
        self.assertEqual(gr.classify_screen(screen("ok_usage.txt"))[:2], ("ok", 42.0))
        self.assertEqual(gr.classify_screen(screen("ok_wrapped_next_line.txt"))[:2], ("ok", 73.0))
        # wide, padded panel: empty bordered row between "Weekly limit" and the bar line
        self.assertEqual(gr.classify_screen(screen("ok_wide_panel.txt"))[:2], ("ok", 37.0))

    def test_failure_screens(self):
        expect = {"update_prompt.txt": "update_prompt", "login_required.txt": "login_required",
                  "wrapped_cut.txt": "wrapped", "unrecognized.txt": "unrecognized", "empty.txt": "empty"}
        for fn, state in expect.items():
            st, pct, detail = gr.classify_screen(screen(fn))
            self.assertEqual(st, state, fn)
            self.assertIsNone(pct, fn)
            self.assertTrue(detail, fn)

    def test_zero_elsewhere_is_not_a_reading(self):
        st, pct, _ = gr.classify_screen(screen("zero_trap.txt"))
        self.assertNotEqual(st, "ok")
        self.assertIsNone(pct)

    def test_every_fixture_has_expected_outcome(self):
        for f in glob.glob(os.path.join(FIX, "*.txt")):
            st, pct, _ = gr.classify_screen(Path(f).read_text(encoding="utf-8"))
            self.assertEqual(st == "ok", os.path.basename(f).startswith("ok_"), f)
            if st != "ok":
                self.assertIsNone(pct)

    def test_out_of_range(self):
        self.assertEqual(gr.classify_screen("Weekly limit  250%")[0], "unrecognized")


FAKE_TMUX = textwrap.dedent("""\
    #!/bin/sh
    echo "$*" >> "$FAKE_TMUX_LOG"
    case "$1" in
      capture-pane) cat "$FAKE_SCREEN" ;;
      list-sessions) printf 'grok_usage_999999999_1\\ngrok_usage_%s_2\\nother\\n' "$PPID" ;;
    esac
    exit 0
""")


@unittest.skipIf(os.name == "nt", "fake tmux is a POSIX shell script (Windows reads /usage through pywinpty)")
class FakeTmuxTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        self.tmux = os.path.join(self.dir, "tmux")
        Path(self.tmux).write_text(FAKE_TMUX)
        os.chmod(self.tmux, 0o755)
        self.log = os.path.join(self.dir, "log")
        Path(self.log).write_text("")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_reader(self, fixture, timeout=5):
        env = {"PATH": self.dir + os.pathsep + os.environ.get("PATH", ""), "FAKE_TMUX_LOG": self.log,
               "FAKE_SCREEN": os.path.join(FIX, fixture)}
        with mock.patch.dict(os.environ, env):
            return gr._read_via_tmux("/bin/true", timeout, Path(self.dir))

    def calls(self):
        return Path(self.log).read_text().splitlines()

    def test_ok_read_fixed_size_and_killed(self):
        res = self.run_reader("ok_usage.txt")
        self.assertEqual(res["pct"], 42.0)
        self.assertIsNotNone(res["reset_at"])
        new = [c for c in self.calls() if c.startswith("new-session")][0]
        self.assertIn("-x 200 -y 50", new)
        sess = new.split("-s ")[1].split()[0]
        self.assertTrue(any(c == f"kill-session -t {sess}" for c in self.calls()))

    def test_login_screen_is_unavailable_and_session_killed(self):
        with self.assertRaises(gr.ScreenReadError) as cm:
            self.run_reader("login_required.txt")
        self.assertEqual(cm.exception.reason, "login_required")
        self.assertTrue(any(c.startswith("kill-session -t grok_usage_") for c in self.calls()))

    def test_update_prompt_is_unavailable(self):
        with self.assertRaises(gr.ScreenReadError) as cm:
            self.run_reader("update_prompt.txt")
        self.assertEqual(cm.exception.reason, "update_prompt")

    def test_stale_session_cleanup(self):
        with mock.patch.dict(os.environ, {"FAKE_TMUX_LOG": self.log}):
            n = gr.cleanup_stale_sessions(self.tmux)
        self.assertEqual(n, 1)  # only the dead-pid session; live and foreign sessions stay
        self.assertIn("kill-session -t grok_usage_999999999_1", self.calls())
        self.assertFalse(any("other" in c for c in self.calls() if c.startswith("kill-session")))


class SampleRecordsUnavailableTests(unittest.TestCase):
    """take_sample(): a failed screen read writes 'unavailable' to samples.csv, never 0."""

    def test_failed_read(self):
        import usage_history
        d = scratch()
        try:
            a = usage_history.argparse.Namespace(config=None, data_dir=d, tz="UTC", lang="en", theme=None, pace=None,
                                                 privacy=None, host=None, port=None, palette=None)
            ctx = usage_history.Ctx(a)
            with mock.patch.object(gr, "read_usage", side_effect=gr.ScreenReadError("login_required", "sign-in requested")), \
                    mock.patch.object(usage_history, "render_clock", lambda c: None), \
                    mock.patch.object(usage_history, "render_history", lambda c: None):
                with self.assertRaises(RuntimeError) as cm:
                    usage_history.take_sample(ctx)
            self.assertIn("login_required", str(cm.exception))
            rows = Path(d, "samples.csv").read_text().splitlines()
            self.assertEqual(rows[-1].split(",")[1], "unavailable")
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
