"""Sampler: a failed ('unavailable') screen read is tried again after a short pause before the sampler waits for
the next interval, and a RecursionError from the reader becomes a normal 'unavailable' result.

Scratch dirs only; the Grok Build reader is replaced by a stub (no Grok Build, no network, synthetic numbers)."""
import argparse
import io
import os
import shutil
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import scratch

import grok_usage_reader as gr  # noqa: E402
import usage_history as uh  # noqa: E402


def make_ctx(data):
    a = argparse.Namespace(config=None, data_dir=data, tz="UTC", lang="en", theme=None, pace=None, palette=None,
                           privacy=False, host=None, port=None)
    return uh.Ctx(a)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ctx = make_ctx(self.dir)
        self.log = []
        self.sleeps = []
        for name in ("render_clock", "render_history"):
            p = mock.patch.object(uh, name, lambda c: None)
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        p = Path(self.dir, "samples.csv")
        return p.read_text(encoding="utf-8").splitlines()[1:] if p.exists() else []

    def run_with(self, side_effect, **kw):
        with mock.patch.object(gr, "read_usage", side_effect=side_effect) as m, redirect_stderr(io.StringIO()) as err:
            ok = uh.sample_with_retry(self.ctx, self.log.append, sleep=self.sleeps.append, **kw)
        return ok, m.call_count, err.getvalue()

    def test_default_pauses_are_short(self):
        self.assertEqual(uh.SAMPLE_RETRY_DELAYS, (20, 60))

    def test_first_try_works_no_retry(self):
        ok, calls, _ = self.run_with([{"pct": 42.0, "reset_at": None}])
        self.assertEqual((ok, calls, self.sleeps), (True, 1, []))
        self.assertEqual(len(self.rows()), 1)

    def test_transient_failure_then_success_records_only_the_success(self):
        ok, calls, _ = self.run_with([gr.ScreenReadError("timeout", "no weekly limit"), {"pct": 42.0, "reset_at": None}])
        self.assertEqual((ok, calls, self.sleeps), (True, 2, [20]))
        rows = self.rows()
        self.assertEqual(len(rows), 1)                      # the failed first try left nothing behind
        self.assertTrue(rows[0].endswith(",42"))
        self.assertTrue(any("retry 1/2 in 20 s" in m for m in self.log))
        self.assertTrue(any(m.startswith("sample failed: unavailable") for m in self.log))

    def test_two_failures_then_success_uses_both_pauses(self):
        ok, calls, _ = self.run_with([gr.ScreenReadError("timeout"), gr.ScreenReadError("empty"),
                                      {"pct": 7.0, "reset_at": None}])
        self.assertEqual((ok, calls, self.sleeps), (True, 3, [20, 60]))
        self.assertTrue(any("retry 2/2 in 60 s" in m for m in self.log))
        self.assertEqual(len(self.rows()), 1)

    def test_all_tries_fail_leaves_exactly_one_unavailable_row(self):
        ok, calls, _ = self.run_with(gr.ScreenReadError("timeout", "nothing"))
        self.assertEqual((ok, calls, self.sleeps), (False, 3, [20, 60]))
        rows = self.rows()
        self.assertEqual([r.split(",")[1] for r in rows], ["unavailable"])   # never 0
        self.assertEqual(sum(m.startswith("sample failed") for m in self.log), 3)

    def test_sign_in_problem_is_not_retried(self):
        ok, calls, _ = self.run_with(gr.ScreenReadError("login_required", "sign-in requested"))
        self.assertEqual((ok, calls, self.sleeps), (False, 1, []))
        self.assertEqual([r.split(",")[1] for r in self.rows()], ["unavailable"])

    def test_custom_pauses(self):
        ok, calls, _ = self.run_with(gr.ScreenReadError("timeout"), delays=(1,))
        self.assertEqual((ok, calls, self.sleeps), (False, 2, [1]))

    def test_recursion_error_is_a_normal_unavailable_with_a_clear_message(self):
        ok, calls, err = self.run_with(RecursionError("maximum recursion depth exceeded while calling a Python object"))
        self.assertEqual((ok, calls, self.sleeps), (False, 3, [20, 60]))   # retried like any other transient failure
        failed = [m for m in self.log if m.startswith("sample failed")]
        self.assertEqual(len(failed), 3)
        for m in failed:
            self.assertIn("recursion limit", m)
            self.assertNotIn("maximum recursion depth", m)
        self.assertIn("RecursionError while reading Grok Build", err)
        self.assertEqual([r.split(",")[1] for r in self.rows()], ["unavailable"])

    def test_recursion_error_then_success(self):
        ok, calls, _ = self.run_with([RecursionError("x"), {"pct": 70.0, "reset_at": None}])
        self.assertEqual((ok, calls, self.sleeps), (True, 2, [20]))
        self.assertTrue(self.rows()[0].endswith(",70"))


class TakeSampleFlagTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for name in ("render_clock", "render_history"):
            p = mock.patch.object(uh, name, lambda c: None)
            p.start()
            self.addCleanup(p.stop)

    def test_record_failure_false_writes_nothing_and_says_transient(self):
        ctx = make_ctx(self.dir)
        with mock.patch.object(gr, "read_usage", side_effect=gr.ScreenReadError("timeout")), \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(uh.ReadUnavailable) as cm:
                uh.take_sample(ctx, record_failure=False)
        self.assertTrue(cm.exception.transient)
        self.assertFalse(Path(self.dir, "samples.csv").exists())

    def test_default_still_records_and_raises_runtime_error(self):
        ctx = make_ctx(self.dir)
        with mock.patch.object(gr, "read_usage", side_effect=gr.ScreenReadError("timeout")), \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):            # ReadUnavailable is a RuntimeError: callers unchanged
                uh.take_sample(ctx)
        self.assertEqual(Path(self.dir, "samples.csv").read_text().splitlines()[-1].split(",")[1], "unavailable")


if __name__ == "__main__":
    unittest.main()
