"""Built-in tab: Grok Bot's built-in usage report saved as JSON snapshots in <data_dir>/breakdown/ (synthetic data only)."""
import json
import os
import re
import shutil
import time
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from _helpers import ROOT, read_text, request, scratch, start_server, stop_server
from test_clock_palette import make_ctx

import usage_breakdown as ub
import usage_breakdown_ui as bui
import usage_history as uh

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 22, 21, 20, tzinfo=TZ)
RESET = datetime(2026, 10, 23, 9, 0, tzinfo=TZ)


def sample(at=NOW, wk=48.5, gb=41.2, **over):
    s = {"captured_at": at.isoformat(), "period": "this_week", "plan": "Demo plan", "weekly_used_pct": wk, "grokbot_used_pct": gb,
         "shared_with_other_apps": True, "resets_at": RESET.isoformat(), "extra_usage_source": "additional usage credits",
         "by_bot": [{"name": "Helper A", "share": 46}, {"name": "Helper B", "share": "<1%"}],
         "by_activity": [{"name": "Chats with the user", "share": 71},
                         {"name": "Routines", "share": 19, "routines": [
                             {"name": "Daily digest", "share": 9, "runs_per_week": 7}, {"name": "Other routines", "share": 4}]},
                         {"name": "Bot-to-Bot", "share": 8}, {"name": "Group chats", "share": 2}],
         "by_day": [{"date": "2026-10-21", "share": 14}], "notes": "Demo note."}
    s.update(over)
    return s


class ParseTests(unittest.TestCase):
    def test_pct_is_lenient(self):
        self.assertEqual(ub.pct(53), (53.0, False))
        self.assertEqual(ub.pct("53%"), (53.0, False))
        self.assertEqual(ub.pct("61.1% used"), (61.1, False))
        self.assertEqual(ub.pct("61,5 %"), (61.5, False))
        self.assertEqual(ub.pct("<1%"), (0.5, True))
        self.assertEqual(ub.pct("< 1 %"), (0.5, True))
        self.assertEqual(ub.pct(250, 0, 100), (100.0, False))
        self.assertEqual(ub.pct(-4, 0, 100), (0.0, False))
        for bad in (None, True, "n/a", "", [], {}, float("nan"), float("inf")):
            self.assertEqual(ub.pct(bad)[0], None, bad)

    def test_normalize_full_snapshot(self):
        s = ub.normalize(sample(), TZ)
        self.assertEqual((s["period"], s["plan"], s["weekly_used_pct"], s["grokbot_used_pct"]), ("this_week", "Demo plan", 48.5, 41.2))
        self.assertIs(s["shared_with_other_apps"], True)
        self.assertEqual(datetime.fromisoformat(s["resets_at"]), RESET)
        self.assertEqual([(b["name"], b["share"], b["lt1"]) for b in s["by_bot"]], [("Helper A", 46.0, False), ("Helper B", 0.5, True)])
        self.assertEqual([a["kind"] for a in s["by_activity"]], ["chat", "routine", "bot_to_bot", "group"])
        r = s["by_activity"][1]["routines"]
        self.assertEqual([(x["name"], x["kind"], x["runs_per_week"]) for x in r], [("Daily digest", "routine", 7.0), ("Other routines", "other_routines", None)])
        self.assertEqual(s["by_day"], [{"date": "2026-10-21", "share": 14.0, "lt1": False}])
        self.assertEqual(s["notes"], "Demo note.")

    def test_missing_fields_and_odd_shapes(self):
        s = ub.normalize({"weekly_used_pct": "12%"}, TZ, NOW)
        self.assertEqual(s["weekly_used_pct"], 12.0)
        self.assertEqual((s["by_bot"], s["by_activity"], s["by_day"], s["plan"], s["resets_at"]), ([], [], None, None, None))
        self.assertEqual(datetime.fromisoformat(s["captured_at"]), NOW)  # missing captured_at -> the fallback time
        m = ub.normalize({"by_bot": {"Helper A": "60%", "Helper B": 5}, "by_activity": {"Routines": {"share": 5, "routines": {"Daily digest": 5}}}},
                         TZ, NOW)
        self.assertEqual([(b["name"], b["share"]) for b in m["by_bot"]], [("Helper A", 60.0), ("Helper B", 5.0)])
        self.assertEqual(m["by_activity"][0]["routines"][0]["name"], "Daily digest")
        p = ub.normalize({"by_bot": [["Helper A", "7%"], "junk", 4, {"share": 3}], "period": "Last 7 days", "shared_with_other_apps": "no"}, TZ, NOW)
        self.assertEqual([b["name"] for b in p["by_bot"]], ["Helper A"])
        self.assertEqual(p["period"], "last_7_days")
        self.assertIs(p["shared_with_other_apps"], False)
        runs = ub.normalize({"by_activity": [{"name": "Routines", "share": 8, "routines": [{"name": "Daily digest", "share": 8, "runs": "about 7 runs"}]}]}, TZ, NOW)
        self.assertEqual(runs["by_activity"][0]["routines"][0]["runs_per_week"], 7.0)

    def test_bad_values_drop_one_field_not_the_snapshot(self):
        s = ub.normalize({"captured_at": "yesterday-ish", "weekly_used_pct": "lots", "grokbot_used_pct": 30, "resets_at": "someday", "by_bot": "nope"}, TZ, NOW)
        self.assertEqual((s["weekly_used_pct"], s["grokbot_used_pct"], s["by_bot"]), (None, 30.0, []))
        self.assertEqual((s["resets_at"], s["resets_text"]), (None, "someday"))
        for junk in (None, [], "x", 5, {}, {"notes": "only a note"}, {"plan": ""}):
            self.assertIsNone(ub.normalize(junk, TZ, NOW), junk)

    def test_times(self):
        d = ub.parse_time("2026-10-31T01:41:00Z", TZ)
        self.assertEqual(d, datetime(2026, 10, 31, 1, 41, tzinfo=timezone.utc))
        self.assertEqual(ub.parse_time("2026-10-31T01:41:00", TZ), datetime(2026, 10, 31, 1, 41, tzinfo=TZ))  # naive = dashboard zone
        self.assertEqual(ub.parse_time("Saturday Oct 31 01:41", TZ, NOW), datetime(2026, 10, 31, 1, 41, tzinfo=TZ))
        self.assertEqual(ub.parse_time("Oct 31, 2026 9:05 PM", TZ, NOW), datetime(2026, 10, 31, 21, 5, tzinfo=TZ))
        self.assertEqual(ub.parse_time("Jan 2 08:00", TZ, datetime(2026, 12, 30, tzinfo=TZ)), datetime(2027, 1, 2, 8, 0, tzinfo=TZ))
        for bad in (None, "", "soon", "Foo 31 01:41"):
            self.assertIsNone(ub.parse_time(bad, TZ), bad)

    def test_activity_kinds(self):
        k = lambda n: ub.normalize({"by_activity": [{"name": n, "share": 1}]}, TZ, NOW)["by_activity"][0]["kind"]  # noqa: E731
        self.assertEqual([k("Chats with the user"), k("Routines"), k("Bot-to-Bot"), k("Group chats"), k("Something new")],
                         ["chat", "routine", "bot_to_bot", "group", "other"])
        # a routine whose own name looks like an activity stays a plain routine
        r = ub.normalize({"by_activity": [{"name": "Routines", "share": 5, "routines": [{"name": "Group chat digest", "share": 5}, {"name": "Not from a routine", "share": 1}]}]},
                         TZ, NOW)["by_activity"][0]["routines"]
        self.assertEqual([x["kind"] for x in r], ["routine", "not_routine"])

    def test_item_limit(self):
        s = ub.normalize({"by_bot": [{"name": f"Helper {i}", "share": 1} for i in range(500)]}, TZ, NOW)
        self.assertEqual(len(s["by_bot"]), ub.MAX_ITEMS)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_write_never_overwrites_and_load_sorts(self):
        s1, s2 = ub.normalize(sample(NOW - timedelta(days=1), 40, 30), TZ), ub.normalize(sample(NOW), TZ)
        p2 = ub.write_snapshot(self.dir, s2); p1 = ub.write_snapshot(self.dir, s1); p3 = ub.write_snapshot(self.dir, s2)
        self.assertEqual(len({p1, p2, p3}), 3)
        self.assertTrue(p2.name.endswith("Z.json") and p3.name.endswith("-2.json"))
        got = ub.load(self.dir, TZ)
        self.assertEqual([g["weekly_used_pct"] for g in got], [40.0, 48.5, 48.5])  # oldest first by captured_at, not by file name or mtime
        self.assertEqual(os.listdir(ub.breakdown_dir(self.dir)).count("x"), 0)
        self.assertFalse([f for f in os.listdir(ub.breakdown_dir(self.dir)) if f.endswith(".tmp")])

    def test_load_skips_junk(self):
        d = ub.breakdown_dir(self.dir); d.mkdir(parents=True)
        (d / "a.json").write_text("{not json", encoding="utf-8")
        (d / "b.json").write_text("[1, 2]", encoding="utf-8")
        (d / "c.json").write_text(json.dumps({"notes": "nothing to show"}), encoding="utf-8")
        (d / "big.json").write_text(json.dumps(sample(notes="x" * (ub.MAX_BYTES + 10))), encoding="utf-8")
        (d / "d.txt").write_text(json.dumps(sample()), encoding="utf-8")
        (d / "bom.json").write_text("\ufeff" + json.dumps(sample(captured_at="")), encoding="utf-8")  # editors' BOM; no time -> file time
        got = ub.load(self.dir, TZ)
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(datetime.fromisoformat(got[0]["captured_at"]).timestamp(), time.time(), delta=60)
        self.assertEqual(ub.load(os.path.join(self.dir, "nowhere"), TZ), [])

    def test_latest_and_previous(self):
        a = ub.normalize(sample(NOW - timedelta(days=1), 40, 30), TZ)
        b = ub.normalize(sample(NOW), TZ)
        self.assertEqual(ub.latest_and_previous([a, b]), (b, a))
        self.assertEqual(ub.latest_and_previous([b]), (b, None))
        self.assertEqual(ub.latest_and_previous([]), (None, None))
        other = ub.normalize(sample(NOW - timedelta(hours=2), period="last_30_days"), TZ)  # another period is not compared
        self.assertEqual(ub.latest_and_previous([other, b])[1], None)
        old = ub.normalize(sample(NOW - timedelta(days=8), resets_at=(RESET - timedelta(days=7)).isoformat()), TZ)  # last week's snapshot
        self.assertEqual(ub.latest_and_previous([old, b])[1], None)

    def test_import_text(self):
        snap, path = ub.import_text(self.dir, "Here you go:\n```json\n" + json.dumps(sample()) + "\n```\nbye", TZ, NOW)
        self.assertEqual(snap["plan"], "Demo plan")
        self.assertTrue(path.exists())
        _, p2 = ub.import_text(self.dir, "text before " + json.dumps(sample(plan="Other")) + " text after", TZ, NOW)
        self.assertNotEqual(path, p2)
        for bad in ("", "no json at all", "{}", '{"notes": "x"}', "[1]"):
            with self.assertRaises(ValueError, msg=bad):
                ub.import_text(self.dir, bad, TZ, NOW)
        self.assertEqual(len(os.listdir(ub.breakdown_dir(self.dir))), 2)


class UiDataTests(unittest.TestCase):
    def snaps(self):
        return [ub.normalize(sample(NOW - timedelta(days=1), 41.0, 34.6), TZ), ub.normalize(sample(), TZ)]

    def test_page_data_values_and_delta(self):
        d = bui.page_data(self.snaps(), "en", TZ)["snap"]
        self.assertEqual((d["plan"], d["weekly"], d["gb"], d["shared"]), ("Demo plan", 48.5, 41.2, True))
        self.assertEqual((d["dw"], d["dg"]), (7.5, 6.6))
        self.assertEqual(d["asof"], "Thu, Oct 22, 21:20")
        self.assertEqual(d["reset"], "Fri, Oct 23, 09:00")
        self.assertEqual([a["n"] for a in d["acts"]], ["Chats with the user", "Routines", "Bot-to-Bot", "Group chats"])
        self.assertEqual([(r["n"], r["runs"]) for r in d["acts"][1]["rs"]], [("Daily digest", 7.0), ("Other routines", None)])
        self.assertEqual(d["period"], "This week")
        self.assertIsNone(bui.page_data(self.snaps()[1:], "en", TZ)["snap"]["dw"])  # no previous snapshot: no delta
        self.assertIsNone(bui.page_data([], "en", TZ)["snap"])

    def test_zh_labels_follow_the_page_language(self):
        d = bui.page_data(self.snaps(), "zh-TW", TZ)["snap"]
        self.assertEqual([a["n"] for a in d["acts"]], ["和你的對話", "排程", "機器人之間", "群組聊天"])
        self.assertEqual(d["acts"][1]["rs"][1]["n"], "其他排程")
        self.assertEqual(d["period"], "本週")
        self.assertIn("週四", d["asof"])

    def test_privacy_hides_names_and_free_text(self):
        d = bui.page_data(self.snaps(), "en", TZ, privacy=True)
        text = json.dumps(d, ensure_ascii=False)
        for secret in ("Helper A", "Helper B", "Daily digest", "Demo plan", "Demo note", "additional usage credits", "Chats", "Routines", "Bot-to"):
            self.assertNotIn(secret, text, secret)
        s = d["snap"]
        self.assertEqual([b["n"] for b in s["bots"]], ["#1", "#2"])
        self.assertEqual([r["n"] for r in s["acts"][1]["rs"]], ["#1", "…"])
        self.assertEqual((s["plan"], s["extra"], s["notes"]), (None, None, ""))
        self.assertEqual(s["weekly"], 48.5)          # numbers stay
        self.assertEqual(d["prompt"], "")
        self.assertTrue(re.fullmatch(r"[^A-Za-z]*", "".join(d["L"][k] for k in ("tab", "by_bot", "by_act", "weekly", "gb", "resets", "in"))
                                     .replace("{t}", "")), "privacy strings are symbols only")

    def test_unknown_activity_name_is_shown_but_hidden_in_privacy(self):
        s = ub.normalize({"by_activity": [{"name": "Secret project chat", "share": 3}]}, TZ, NOW)
        self.assertEqual(bui.page_data([s], "en", TZ)["snap"]["acts"][0]["n"], "Secret project chat")
        self.assertEqual(bui.page_data([s], "en", TZ, True)["snap"]["acts"][0]["n"], "…")


class StringTests(unittest.TestCase):
    def test_every_page_language_has_a_complete_table(self):
        fields = lambda s: sorted(re.findall(r"\{(\w+)\}", str(s)))  # noqa: E731
        for lang in [x for x in uh.PAGE_LANGS if x != "auto"]:
            t = bui.for_lang(lang)
            self.assertEqual(set(t), set(bui.EN), lang)
            for k, v in bui.EN.items():
                if lang != "numeric":
                    self.assertTrue(str(t[k]).strip() or k == "prompt", (lang, k))
                    self.assertEqual(fields(t[k]), fields(v), (lang, k)) if k != "prompt" else None
        self.assertEqual(bui.for_lang("zh-HK")["tab"], "內建")
        self.assertEqual(bui.for_lang("klingon"), bui.EN)

    def test_tab_label_is_built_in(self):
        self.assertEqual(bui.EN["tab"], "Built-in")
        self.assertEqual(bui.ZH_TW["tab"], "內建")
        self.assertEqual(bui.ZH_CN["tab"], "内置")
        # translated: no language except English keeps the English words
        for lang in ("zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):
            self.assertNotEqual(bui.for_lang(lang)["tab"], "Built-in", lang)
            self.assertNotEqual(bui.for_lang(lang)["empty_h"], bui.EN["empty_h"], lang)

    def test_never_a_state_colour(self):
        for part in (bui.CSS, bui.JS):
            self.assertNotIn("--over", part)       # orange means "over the baseline" and nothing else
            self.assertNotIn("--under", part)
            self.assertNotIn("innerHTML", part)    # names are only ever set with textContent
        self.assertIn("var(--notice)", bui.CSS)    # a stale snapshot is a notice (purple), not "bad"

    def test_prompt_in_readmes_matches_the_page(self):
        for name, prompt in (("README.md", bui.PROMPT_EN), ("README.zh-TW.md", bui.PROMPT_ZH)):
            self.assertIn(prompt, read_text_raw(os.path.join(ROOT, name)), name)
        # the prompt asks for the format this module reads: its example is parseable once the placeholders are filled
        m = re.search(r'(\{"captured_at".*?"notes":""\})', bui.PROMPT_EN)
        raw = json.loads(m.group(1).replace("<now, ISO 8601 with my UTC offset>", NOW.isoformat()).replace("<ISO 8601>", RESET.isoformat())
                         .replace('"weekly_used_pct":0', '"weekly_used_pct":10'))
        self.assertEqual(ub.normalize(raw, TZ)["weekly_used_pct"], 10.0)
        self.assertNotIn("\n", bui.PROMPT_EN)


def read_text_raw(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


class PageTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def page(self, lang="en", privacy=False, snaps=True):
        if snaps:
            ub.write_snapshot(self.dir, ub.normalize(sample(NOW - timedelta(days=1), 41.0, 34.6), TZ))
            ub.write_snapshot(self.dir, ub.normalize(sample(), TZ))
        ctx = make_ctx(self.dir, lang, privacy)
        uh.rewrite_page(ctx)
        return read_text_raw(os.path.join(self.dir, "history", "index.html"))

    def test_tab_sits_right_after_check_ins(self):
        for lang, label in (("en", "Built-in"), ("zh-TW", "內建")):
            page = self.page(lang, snaps=False)
            tabs = re.findall(r"<nav>(.*?)</nav>", page, re.S)[0]
            keys = re.findall(r"data-k='(\w+)'", tabs)
            self.assertEqual(keys[-2:], ["checkin", "breakdown"], lang)
            self.assertIn(f"<button data-k='breakdown'>{label}</button>", tabs)
            self.assertIn("id='p-breakdown'", page)

    def test_latest_snapshot_is_in_the_page(self):
        page = self.page()
        data = json.loads(re.search(r'id="bddata">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(data["snap"]["weekly"], 48.5)
        self.assertEqual([b["n"] for b in data["snap"]["bots"]], ["Helper A", "Helper B"])
        self.assertEqual(data["snap"]["dw"], 7.5)
        self.assertIn("function draw", page)

    def test_empty_state_has_the_routine_prompt(self):
        data = json.loads(re.search(r'id="bddata">(.*?)</script>', self.page(snaps=False), re.S).group(1))
        self.assertIsNone(data["snap"])
        self.assertEqual(data["prompt"], bui.PROMPT_EN)
        self.assertIn("breakdown", data["L"]["empty_2"])
        zh = json.loads(re.search(r'id="bddata">(.*?)</script>', self.page("zh-TW", snaps=False), re.S).group(1))
        self.assertEqual(zh["prompt"], bui.PROMPT_ZH)

    def test_privacy_page_has_no_names(self):
        page = self.page("en", privacy=True)
        for secret in ("Helper A", "Helper B", "Daily digest", "Demo plan", "Demo note", "additional usage credits", "Built-in"):
            self.assertNotIn(secret, page, secret)
        self.assertIn("<button data-k='breakdown'>▣</button>", page)
        self.assertNotIn(bui.PROMPT_EN[:40], page)

    def test_every_language_builds(self):
        for lang in [x for x in uh.PAGE_LANGS if x not in ("auto", "numeric")]:
            page = self.page(lang)
            self.assertIn(f"<button data-k='breakdown'>{bui.for_lang(lang)['tab']}</button>", page, lang)

    def test_no_snapshot_files_means_no_folder_is_created(self):
        self.page(snaps=False)
        self.assertFalse(os.path.exists(ub.breakdown_dir(self.dir)))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()
        self.argv = ["--data-dir", self.dir, "--tz", "Asia/Taipei", "--lang", "en"]

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_cli(self, *args):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                uh.main(["breakdown", *args, *self.argv])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, json.loads(out.getvalue())

    def test_import_file_then_show(self):
        p = os.path.join(self.dir, "in.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(sample(), fh)
        code, r = self.run_cli(p)
        self.assertEqual((code, r["ok"], r["period"]), (0, True, "this_week"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "breakdown", r["file"])))
        self.assertIn("bddata", read_text_raw(os.path.join(self.dir, "history", "index.html")))  # page rebuilt at once
        code, r = self.run_cli()
        self.assertEqual((code, r["snapshots"], r["latest"]["plan"]), (0, 1, "Demo plan"))

    def test_bad_input_changes_nothing(self):
        p = os.path.join(self.dir, "bad.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{oops")
        code, r = self.run_cli(p)
        self.assertEqual((code, r["ok"]), (2, False))
        code, r = self.run_cli(os.path.join(self.dir, "missing.json"))
        self.assertEqual((code, r["ok"]), (2, False))
        self.assertNotIn(self.dir, r["error"])  # no path leaks into the message
        self.assertFalse(os.path.exists(os.path.join(self.dir, "breakdown")))
        self.assertEqual(self.run_cli()[0], 2)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(cls.data)
        cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", cls.data, "--tz", "UTC", "--port", "0"],
                                      os.path.join(cls.data, "history_server.json"))
        cls.url = info["url"]

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def test_a_dropped_in_file_shows_on_the_next_page_load(self):
        code, body = request(self.url)
        self.assertEqual(code, 200)
        self.assertNotIn(b"Helper Z", body)
        d = os.path.join(self.data, "breakdown"); os.makedirs(d)
        with open(os.path.join(d, "mine.json"), "w", encoding="utf-8") as fh:  # a bot may use its own file name
            json.dump(sample(by_bot=[{"name": "Helper Z", "share": 9}]), fh)
        future = time.time() + 5
        os.utime(os.path.join(d, "mine.json"), (future, future))
        code, body = request(self.url)
        self.assertEqual(code, 200)
        self.assertIn(b"Helper Z", body)


if __name__ == "__main__":
    unittest.main()
