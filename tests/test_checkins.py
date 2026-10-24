"""Check-in categories, start/end sessions, multi-select and day-grouped search (scratch files only)."""
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone

from _helpers import scratch

import usage_categories as uc
import usage_checkins as ck

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


class CategoryTests(unittest.TestCase):
    def test_defaults_order_and_labels(self):
        c = uc.load(None)
        self.assertEqual(c.ids[:3], ["web", "code", "break"])
        self.assertEqual(c.ids[-1], "other")
        self.assertEqual(c.label("image", "zh-TW"), "畫圖")
        self.assertEqual(c.label("image"), "Image generation")

    def test_unknown_and_missing_become_other(self):
        c = uc.load(None)
        self.assertEqual(c.normalize("nope"), "other")
        self.assertEqual(c.normalize(""), "other")
        self.assertEqual(ck.parse_categories(None, c), ["other"])

    def test_config_forms(self):
        self.assertEqual(uc.load("code, web:Browsing").ids, ["code", "web", "other"])
        self.assertEqual(uc.load(["review", {"id": "ops", "label": "Ops"}]).label("ops"), "Ops")
        self.assertEqual(uc.load(["Bad Id!", "code"]).ids, ["code", "other"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        self.path = os.path.join(self.dir, "checkins.csv")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def add(self, *a, **k):
        return ck.add(self.path, self.dir, *a, **k)

    def test_legacy_rows_are_points(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("timestamp,name,note\n2026-03-01T10:00:00+00:00,old,hello\n")
        ev = ck.read(self.path)
        self.assertEqual((ev[0]["phase"], ev[0]["cats"]), ("point", ["other"]))
        self.add("new", "point", "code", now=T0)  # header upgraded in place, old row still readable
        with open(self.path, encoding="utf-8") as fh:
            self.assertTrue(fh.readline().startswith("timestamp,name,note,category,id,phase,status"))
        self.assertEqual([e["name"] for e in ck.read(self.path)], ["old", "new"])

    def test_multi_select_store_and_limit(self):
        r = self.add("a", "start", ["code", "writing", "code"], now=T0)
        self.assertEqual(r["categories"], ["code", "writing"])
        with open(self.path, encoding="utf-8") as fh:
            self.assertIn("code|writing", fh.read())
        with self.assertRaises(ck.CheckinError) as cm:
            self.add("a", "start", "web,code,image,files", now=T0)
        self.assertEqual(cm.exception.code, "too_many_categories")
        self.assertEqual(self.add("b", "point", "web|image", now=T0)["categories"], ["web", "image"])

    def test_end_by_id_and_by_latest(self):
        s1 = self.add("a", "start", "code", now=T0)
        s2 = self.add("a", "start", "web", now=T0 + timedelta(minutes=5))
        self.add("", "end", session_id=s1["id"], status="interrupted", note="phone", now=T0 + timedelta(minutes=30))
        e = self.add("a", "end", now=T0 + timedelta(minutes=50))  # latest open for the name
        self.assertEqual(e["id"], s2["id"])
        ss = {s["id"]: s for s in ck.sessions(ck.read(self.path), now=T0 + timedelta(hours=1))}
        self.assertEqual(ss[s1["id"]]["state"], "interrupted")
        self.assertEqual(ss[s1["id"]]["duration_s"], 1800)
        self.assertEqual(ss[s1["id"]]["end_note"], "phone")
        self.assertEqual(ss[s2["id"]]["state"], "done")
        with self.assertRaises(ck.CheckinError):
            self.add("a", "end", now=T0 + timedelta(hours=1))  # nothing open any more

    def test_end_prefers_matching_categories(self):
        s1 = self.add("a", "start", "code", now=T0)
        self.add("a", "start", "web", now=T0 + timedelta(minutes=1))
        self.assertEqual(self.add("a", "end", "code", now=T0 + timedelta(minutes=9))["id"], s1["id"])

    def test_running_vs_open_after_limit(self):
        self.add("a", "start", "code", now=T0)
        st = lambda h: ck.sessions(ck.read(self.path), now=T0 + timedelta(hours=h))[0]["state"]  # noqa: E731
        self.assertEqual(st(1), "running")
        self.assertEqual(st(7), "open")
        self.assertEqual(ck.sessions(ck.read(self.path), now=T0 + timedelta(hours=7), open_limit_hours=8)[0]["state"],
                         "running")

    def test_usage_delta(self):
        s = self.add("a", "start", "code", now=T0)
        self.add("", "end", session_id=s["id"], now=T0 + timedelta(hours=2))
        sess = ck.sessions(ck.read(self.path), now=T0 + timedelta(hours=3))
        samples = [(T0 - timedelta(minutes=10), 20.0), (T0 + timedelta(hours=2, minutes=5), 26.0)]
        self.assertEqual(ck.attach_usage(sess, samples)[0]["usage"]["delta"], 6.0)
        self.assertEqual(ck.attach_usage(sess, [])[0]["usage"]["status"], "unavailable")
        self.assertEqual(ck.attach_usage(sess, [(T0, 20.0), (T0 + timedelta(hours=2), None)])[0]["usage"]["status"],
                         "unavailable")
        reset = [(T0, 90.0), (T0 + timedelta(hours=2), 3.0)]
        self.assertEqual(ck.attach_usage(sess, reset)[0]["usage"]["reason"], "weekly reset in between")

    def test_summary_overlap_and_total_once(self):
        s = self.add("a", "start", ["code", "writing"], now=T0)
        self.add("", "end", session_id=s["id"], now=T0 + timedelta(hours=1))
        s = self.add("b", "start", "code", now=T0)
        self.add("", "end", session_id=s["id"], status="interrupted", now=T0 + timedelta(minutes=30))
        sm = ck.summarize(ck.sessions(ck.read(self.path), now=T0 + timedelta(hours=2)))
        self.assertEqual(sm["by_category"]["code"]["total_s"], 5400)
        self.assertEqual(sm["by_category"]["writing"]["total_s"], 3600)
        self.assertEqual(sm["by_category"]["code"]["interrupted"], 1)
        self.assertEqual(sm["total_s"], 5400)  # not 9000
        sess = ck.sessions(ck.read(self.path), now=T0 + timedelta(hours=2))
        self.assertEqual(len([x for x in sess if ck.matches(x, ["writing", "web"])]), 1)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        p = os.path.join(self.dir, "c.csv")
        for i, (name, cats, note, day) in enumerate([("ann", "code", "Fix 登入 bug", 0), ("ben", "research|web", "read docs", 0),
                                                     ("ann", "writing", "weekly notes", 1)]):
            t = T0 + timedelta(days=day, minutes=10 * i)
            s = ck.add(p, self.dir, name, "start", cats, note, now=t)
            ck.add(p, self.dir, "", "end", session_id=s["id"], now=t + timedelta(minutes=30))
        self.sess = ck.sessions(ck.read(p), now=T0 + timedelta(days=3))
        self.lab = uc.load(None).label

    def tearDown(self):
        shutil.rmtree(self.dir)

    def q(self, q, **k):
        return [s["name"] + ":" + s["note"] for s in ck.search(self.sess, q, self.lab, **k)]

    def test_keywords_and_case_cjk(self):
        self.assertEqual(self.q("FIX 登入"), ["ann:Fix 登入 bug"])
        self.assertEqual(self.q("fix docs"), [])  # AND
        self.assertEqual(self.q("web browsing"), ["ben:read docs"])  # category label
        self.assertEqual(len(self.q("")), 3)

    def test_category_and_date_filters(self):
        self.assertEqual(self.q("", categories=["writing", "code"]), ["ann:Fix 登入 bug", "ann:weekly notes"])
        self.assertEqual(self.q("ann", since=T0 + timedelta(days=1)), ["ann:weekly notes"])
        self.assertEqual(self.q("", until=T0 + timedelta(hours=12)), ["ann:Fix 登入 bug", "ben:read docs"])

    def test_group_by_day_with_actual_rise(self):
        samples = [(T0 - timedelta(minutes=30), 10.0), (T0 + timedelta(hours=1), 14.0), (T0 + timedelta(hours=5), 19.0),
                   (T0 + timedelta(days=1, hours=2), 25.0)]
        days = ck.group_by_day(ck.search(self.sess, "", self.lab), samples, timezone.utc)
        self.assertEqual([d["date"] for d in days], ["2026-03-03", "2026-03-02"])  # newest first
        d0 = days[1]
        self.assertEqual(d0["count"], 2)
        self.assertEqual(d0["total_s"], 3600)
        self.assertEqual(d0["usage_rise"], 9.0)  # day's actual rise 10 -> 19, not a sum of sessions
        js = ck.day_to_json(d0, self.lab)
        self.assertEqual(js["sessions"][0]["category_labels"], ["Code"])
        self.assertIsNone(ck.group_by_day(ck.search(self.sess, "", self.lab), [], timezone.utc)[0]["usage_rise"])


if __name__ == "__main__":
    unittest.main()
