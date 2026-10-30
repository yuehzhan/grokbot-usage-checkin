"""Built-in tab: free-disk-space bar (thresholds, config, fake-disk hook, page data, privacy). Synthetic numbers only."""
import json
import os
import re
import shutil
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from _helpers import scratch
from test_clock_palette import make_ctx

import usage_breakdown as ub
import usage_breakdown_ui as bui
import usage_disk as ud
import usage_history as uh

G = ud.GB
TZ = ZoneInfo("Asia/Taipei")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def lv(free, total=128, cfg=None):
    """Level of `free` GB on a `total` GB disk with the default (or given) config."""
    return ud.level(free, *ud.thresholds(cfg or {}, total))


class LevelTests(unittest.TestCase):
    def test_defaults_are_23_and_16_percent_of_the_total(self):
        self.assertEqual((ud.WARN_PCT, ud.DANGER_PCT), (23.0, 16.0))
        self.assertEqual(ud.thresholds({}, 100), (23.0, 16.0))
        self.assertEqual(ud.thresholds(None, 100), (23.0, 16.0))
        w, d = ud.thresholds({}, 128)                       # about 29.4 GB and 20.5 GB on a 128 GB disk
        self.assertAlmostEqual(w, 29.44)
        self.assertAlmostEqual(d, 20.48)
        w, d = ud.thresholds({}, 512)                       # they scale with the disk
        self.assertAlmostEqual(w, 117.76)
        self.assertAlmostEqual(d, 81.92)

    def test_normal_orange_red_on_128_gb(self):
        for free, want in ((128, "ok"), (64, "ok"), (45.5, "ok"), (30, "ok"), (27, "warn"), (25, "warn"), (20.6, "warn"),
                           (20, "danger"), (15, "danger"), (5, "danger"), (0, "danger")):
            self.assertEqual(lv(free), want, free)

    def test_boundaries_under_means_strictly_less(self):
        self.assertEqual(lv(23, 100), "ok")             # exactly 23 % of the total: not under it
        self.assertEqual(lv(22.99, 100), "warn")
        self.assertEqual(lv(23.01, 100), "ok")
        self.assertEqual(lv(16, 100), "warn")           # exactly 16 %: not under 16 %, but under 23 %
        self.assertEqual(lv(15.99, 100), "danger")
        self.assertEqual(lv(16.01, 100), "warn")

    def test_boundaries_in_bytes(self):
        pd = lambda free_bytes, total=100: ud.page_data(reading=(free_bytes, total * G))["level"]  # noqa: E731
        self.assertEqual(pd(23 * G), "ok")
        self.assertEqual(pd(23 * G - 1), "warn")
        self.assertEqual(pd(16 * G), "warn")
        self.assertEqual(pd(16 * G - 1), "danger")
        self.assertEqual(pd(46 * G, 200), "ok")         # same percentages on a bigger disk
        self.assertEqual(pd(46 * G - 1, 200), "warn")
        self.assertEqual(pd(32 * G, 200), "warn")
        self.assertEqual(pd(32 * G - 1, 200), "danger")

    def test_same_free_space_different_disk_sizes(self):
        self.assertEqual(lv(60, 128), "ok")
        self.assertEqual(lv(60, 512), "danger")         # 12 % free of a 512 GB disk
        self.assertEqual(lv(60, 256), "ok")             # 23.4 % free
        self.assertEqual(lv(50, 256), "warn")           # 19.5 % free

    def test_percentages_from_config(self):
        self.assertEqual(ud.thresholds({"disk_warn_pct": 50, "disk_danger_pct": "25"}, 200), (100.0, 50.0))
        self.assertEqual(lv(90, 200, {"disk_warn_pct": 50, "disk_danger_pct": 25}), "warn")
        self.assertEqual(lv(49, 200, {"disk_warn_pct": 50, "disk_danger_pct": 25}), "danger")
        self.assertEqual(ud.thresholds({"disk_warn_pct": 500}, 100)[0], 100.0)      # capped at 100 %

    def test_explicit_gb_keys_still_win(self):
        self.assertEqual(ud.thresholds({"disk_warn_gb": 50, "disk_danger_gb": "40"}, 128), (50.0, 40.0))
        self.assertEqual(ud.thresholds({"disk_warn_gb": 50, "disk_danger_gb": 40, "disk_warn_pct": 90, "disk_danger_pct": 80}, 128), (50.0, 40.0))
        self.assertEqual(ud.thresholds({"disk_warn_gb": 30, "disk_danger_gb": 20}, 512), (30.0, 20.0))   # old configs behave as before
        self.assertEqual(ud.level(45, 50, 40), "warn")
        self.assertEqual(ud.level(39, 50, 40), "danger")
        self.assertEqual(ud.level(50, 50, 40), "ok")
        self.assertEqual(ud.page_data({"disk_warn_gb": 50, "disk_danger_gb": 40}, (45 * G, 128 * G))["level"], "warn")
        w, d = ud.thresholds({"disk_warn_gb": 40}, 128)                              # one key set: the other stays a percentage
        self.assertEqual(w, 40.0)
        self.assertAlmostEqual(d, 20.48)

    def test_bad_config_falls_back(self):
        for bad in (None, "lots", -5, 0, float("nan"), float("inf"), True, [], {}):
            for keys in (("disk_warn_gb", "disk_danger_gb"), ("disk_warn_pct", "disk_danger_pct")):
                self.assertEqual(ud.thresholds({keys[0]: bad, keys[1]: bad}, 100), (23.0, 16.0), (bad, keys))
        self.assertEqual(ud.thresholds([1, 2], 100), (23.0, 16.0))
        self.assertEqual(ud.thresholds({"disk_warn_gb": 10}, 100), (10.0, 10.0))      # danger is never above warn
        self.assertEqual(ud.thresholds({"disk_danger_pct": 50}, 100), (23.0, 23.0))


class PageDataTests(unittest.TestCase):
    def test_values(self):
        d = ud.page_data(reading=(27 * G, 128 * G))
        self.assertEqual((d["free"], d["total"], d["pct"], d["level"]), (27.0, 128, 21.1, "warn"))
        self.assertEqual((d["warn_gb"], d["danger_gb"]), (29.44, 20.48))

    def test_shown_free_never_rounds_up_over_a_threshold(self):
        d = ud.page_data(reading=(int(29.439 * G), 128 * G))
        self.assertEqual((d["free"], d["level"]), (29.4, "warn"))
        d = ud.page_data(reading=(int(20.479 * G), 128 * G))
        self.assertEqual((d["free"], d["level"]), (20.4, "danger"))
        d = ud.page_data(reading=(int(22.99 * G), 100 * G))
        self.assertEqual((d["free"], d["level"]), (22.9, "warn"))

    def test_unreadable_or_empty_disk(self):
        self.assertIsNone(ud.page_data(reading=(0, 0)))
        with mock.patch.object(ud, "read", return_value=None):
            self.assertIsNone(ud.page_data())

    def test_danger_colour_is_one_constant(self):
        d = ud.page_data(reading=(15 * G, 128 * G))
        self.assertEqual(d["colors"], {"warn": ud.DISK_WARN_COLOR, "danger": ud.DISK_DANGER_COLOR})
        self.assertNotEqual(ud.DISK_WARN_COLOR, ud.DISK_DANGER_COLOR)
        src = read_text(ud.__file__)
        self.assertEqual(len(re.findall(r"#E5484D", src, re.I)), 1)           # written once, everything else reads the constant
        for part in (bui.CSS, bui.JS):
            self.assertNotIn(ud.DISK_DANGER_COLOR, part)
            self.assertNotIn(ud.DISK_WARN_COLOR, part)

    def test_does_not_use_the_chart_state_colours(self):
        self.assertNotIn("--over", bui.JS.split("function disk()")[1].split("function draw()")[0])
        self.assertNotIn("--under", bui.CSS)


class ReadTests(unittest.TestCase):
    def test_real_read_uses_shutil_disk_usage(self):
        with mock.patch("usage_disk.shutil.disk_usage", return_value=shutil._ntuple_diskusage(128 * G, 100 * G, 28 * G)) as m, \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ud.ENV_FREE, None)
            self.assertEqual(ud.read(), (28 * G, 128 * G))
            self.assertEqual(m.call_count, 1)

    def test_falls_back_to_root_when_home_fails(self):
        calls = []

        def du(p):
            calls.append(p)
            if len(calls) == 1:
                raise OSError("no home")
            return shutil._ntuple_diskusage(100 * G, 50 * G, 50 * G)
        with mock.patch("usage_disk.shutil.disk_usage", side_effect=du):
            os.environ.pop(ud.ENV_FREE, None)
            self.assertEqual(ud.read(), (50 * G, 100 * G))
        self.assertEqual(len(calls), 2)

    def test_all_fail_gives_none(self):
        with mock.patch("usage_disk.shutil.disk_usage", side_effect=OSError("x")):
            os.environ.pop(ud.ENV_FREE, None)
            self.assertIsNone(ud.read())

    def test_fake_hook(self):
        with mock.patch.dict(os.environ, {ud.ENV_FREE: "27"}):
            self.assertEqual(ud.read(), (27 * G, 128 * G))
        with mock.patch.dict(os.environ, {ud.ENV_FREE: "15", ud.ENV_TOTAL: "256"}):
            self.assertEqual(ud.read(), (15 * G, 256 * G))
        with mock.patch.dict(os.environ, {ud.ENV_FREE: "0"}):
            self.assertEqual(ud.read(), (0, 128 * G))
        with mock.patch.dict(os.environ, {ud.ENV_FREE: "banana"}), mock.patch("usage_disk.shutil.disk_usage",
                                                                               return_value=shutil._ntuple_diskusage(10 * G, 5 * G, 5 * G)):
            self.assertEqual(ud.read(), (5 * G, 10 * G))   # a bad hook value is ignored: the real disk is read

    def test_module_makes_no_network_calls(self):
        src = read_text(ud.__file__)
        for word in ("socket", "urllib", "http", "requests", "subprocess"):
            self.assertNotRegex(src, rf"^\s*(import|from)\s+{word}", word)


def snap_with(free=None, total=None, at="2026-10-30T09:00:00+08:00", **extra):
    s = {"captured_at": at, "disk_free_gb": free, "disk_total_gb": total}
    s.update(extra)
    return s


class SnapshotDiskTests(unittest.TestCase):
    """Optional disk_free_gb / disk_total_gb in a Built-in snapshot (the bot's own computer) and how the bar picks a source."""
    LOCAL = (27 * G, 128 * G)

    def test_parse_fields(self):
        raw = {"captured_at": "2026-10-30T09:00:00+08:00", "weekly_used_pct": 10, "disk_free_gb": 27.5, "disk_total_gb": 128}
        s = ub.normalize(raw, TZ)
        self.assertEqual((s["disk_free_gb"], s["disk_total_gb"]), (27.5, 128.0))
        t = ub.normalize({"captured_at": "2026-10-30T09:00:00+08:00", "weekly_used_pct": 10, "disk_free_gb": "27,5 GB", "disk_total_gb": "128 GB"}, TZ)
        self.assertEqual((t["disk_free_gb"], t["disk_total_gb"]), (27.5, 128.0))

    def test_missing_or_odd_fields_are_harmless(self):
        base = {"captured_at": "2026-10-30T09:00:00+08:00", "weekly_used_pct": 10}
        for extra in ({}, {"disk_free_gb": 5}, {"disk_total_gb": 128}, {"disk_free_gb": "lots", "disk_total_gb": 128},
                      {"disk_free_gb": 200, "disk_total_gb": 128}, {"disk_free_gb": 5, "disk_total_gb": 0},
                      {"disk_free_gb": None, "disk_total_gb": None}, {"disk_free_gb": True, "disk_total_gb": [1]},
                      {"disk_free_gb": float("nan"), "disk_total_gb": float("inf")}, {"disk_free_gb": {}, "disk_total_gb": ""}):
            s = ub.normalize(dict(base, **extra), TZ)
            self.assertIsNotNone(s, extra)                                     # the snapshot itself is never dropped
            self.assertEqual((s["disk_free_gb"], s["disk_total_gb"]), (None, None), extra)
            self.assertEqual(s["weekly_used_pct"], 10.0)

    def test_old_snapshot_without_the_keys(self):
        old = {"captured_at": "2026-10-22T21:20:00+08:00", "period": "this_week", "weekly_used_pct": 40}   # a file written before this field
        self.assertIsNone(ud.from_snapshot(old))
        self.assertEqual(ud.page_data(None, self.LOCAL, old)["src"], "local")
        self.assertEqual(ud.page_data(None, self.LOCAL, None)["src"], "local")
        self.assertIsNone(ud.from_snapshot({}))

    def test_from_snapshot(self):
        free, total, ts = ud.from_snapshot(snap_with(27.5, 128))
        self.assertEqual((free, total), (27.5 * G, 128 * G))
        self.assertGreater(ts, 1.7e9)
        for bad in (snap_with(None, 128), snap_with(5, None), snap_with(5, 0), snap_with(-1, 128), snap_with(200, 128),
                    snap_with(5, 128, at="not a time"), {"disk_free_gb": 5, "disk_total_gb": 128}, "junk", 5):
            self.assertIsNone(ud.from_snapshot(bad), bad)

    def test_auto_prefers_the_report_only_on_another_machine(self):
        on_other_pc = snap_with(80, 512)                     # the dashboard's own disk is 128 GB, the box reports 512 GB -> different machine
        src, r, ts = ud.pick({}, self.LOCAL, on_other_pc)
        self.assertEqual((src, r), ("snapshot", (80 * G, 512 * G)))
        self.assertIsNotNone(ts)
        same = snap_with(20, 128)                            # same total size: same machine, the live local reading wins
        self.assertEqual(ud.pick({}, self.LOCAL, same)[:2], ("local", self.LOCAL))
        near = snap_with(27, 130)                            # within 5 %: same machine
        self.assertEqual(ud.pick({}, self.LOCAL, near)[0], "local")
        far = snap_with(27, 140)                             # 12 GB / 8.6 % apart: another machine
        self.assertEqual(ud.pick({}, self.LOCAL, far)[0], "snapshot")
        tiny = ud.pick({}, (int(1.5 * G), int(2.5 * G)), snap_with(1, 2))   # 0.5 GB apart: under the 1 GB floor, same machine
        self.assertEqual(tiny[0], "local")

    def test_disk_source_setting(self):
        other, same = snap_with(80, 512), snap_with(20, 128)
        self.assertEqual(ud.pick({"disk_source": "local"}, self.LOCAL, other)[0], "local")
        self.assertEqual(ud.pick({"disk_source": "snapshot"}, self.LOCAL, same)[0], "snapshot")
        self.assertEqual(ud.pick({"disk_source": "snapshot"}, self.LOCAL, None)[0], "local")       # nothing reported: local
        self.assertEqual(ud.pick({"disk_source": "bogus"}, self.LOCAL, other)[0], "snapshot")      # unknown value = auto
        self.assertEqual(ud.source({"disk_source": " SNAPSHOT "}), "snapshot")
        self.assertEqual((ud.source(None), ud.source({}), ud.source([1])), ("auto", "auto", "auto"))

    def test_fallbacks_when_one_side_is_missing(self):
        report = snap_with(80, 512)
        self.assertEqual(ud.pick({}, None, report)[0], "snapshot")                       # local unreadable: use the report
        self.assertEqual(ud.pick({"disk_source": "local"}, None, report)[0], "snapshot")
        self.assertEqual(ud.pick({}, None, None), (None, None, None))
        self.assertIsNone(ud.page_data(None, (0, 0), None))
        self.assertEqual(ud.page_data(None, (0, 0), report)["src"], "snapshot")

    def test_page_data_levels_use_the_chosen_reading(self):
        d = ud.page_data(None, self.LOCAL, snap_with(15, 512))     # local says 27 GB (warn); the box reports 15 GB (danger)
        self.assertEqual((d["src"], d["free"], d["total"], d["level"]), ("snapshot", 15.0, 512, "danger"))
        self.assertIsNotNone(d["ts"])
        d = ud.page_data(None, self.LOCAL, None)
        self.assertEqual((d["src"], d["ts"], d["level"]), ("local", None, "warn"))

    def test_through_the_page(self):
        tmp = scratch()
        try:
            at = datetime(2026, 10, 30, 9, 0, tzinfo=TZ)
            ub.write_snapshot(tmp, ub.normalize({"captured_at": at.isoformat(), "weekly_used_pct": 40, "disk_free_gb": 80, "disk_total_gb": 512}, TZ))
            ctx = make_ctx(tmp, "en")
            with mock.patch.dict(os.environ, {ud.ENV_FREE: "27"}):
                data = uh.breakdown_page_data(ctx)
                self.assertEqual((data["disk"]["src"], data["disk"]["free"], data["disk"]["total"]), ("snapshot", 80.0, 512))
                ctx.cfg = {"disk_source": "local"}
                self.assertEqual(uh.breakdown_page_data(ctx)["disk"]["src"], "local")
            # a newer snapshot without the fields: the bar falls back to the local reading (the newest snapshot decides)
            ub.write_snapshot(tmp, ub.normalize({"captured_at": (at + timedelta(days=1)).isoformat(), "weekly_used_pct": 41}, TZ))
            ctx.cfg = {}
            with mock.patch.dict(os.environ, {ud.ENV_FREE: "27"}):
                self.assertEqual(uh.breakdown_page_data(ctx)["disk"]["src"], "local")
        finally:
            shutil.rmtree(tmp)

    def test_page_shows_age_for_a_report_and_the_local_note_otherwise(self):
        self.assertIn("d.src==='snapshot'&&d.ts", bui.JS)
        self.assertIn("age(d.ts)", bui.JS)
        self.assertIn(".bd-disk-src", bui.CSS)

    def test_prompt_asks_for_the_disk_fields(self):
        for prompt in (bui.PROMPT_EN, bui.PROMPT_ZH):
            self.assertIn("disk_free_gb", prompt)
            self.assertIn("disk_total_gb", prompt)
            self.assertIn("df -h ~", prompt)
            self.assertNotIn("\n", prompt)
        # the example in the prompt is still a parseable snapshot
        m = re.search(r'(\{"captured_at".*?"notes":""\})', bui.PROMPT_EN)
        raw = json.loads(m.group(1).replace("<now, ISO 8601 with my UTC offset>", "2026-10-30T09:00:00+08:00")
                         .replace("<ISO 8601>", "2026-10-31T09:00:00+08:00").replace('"disk_free_gb":0,"disk_total_gb":0', '"disk_free_gb":27.5,"disk_total_gb":128'))
        s = ub.normalize(raw, TZ)
        self.assertEqual((s["disk_free_gb"], s["disk_total_gb"]), (27.5, 128.0))
        for name, prompt in (("README.md", bui.PROMPT_EN), ("README.md", bui.PROMPT_ZH), ("README.zh-TW.md", bui.PROMPT_EN), ("README.zh-TW.md", bui.PROMPT_ZH)):
            self.assertIn(prompt, read_text(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)), name)


class StringTests(unittest.TestCase):
    def test_english_and_traditional_chinese_labels(self):
        self.assertEqual(bui.EN["disk"].format(f=27, t=128), "Bot computer disk free: 27 GB / 128 GB")
        self.assertEqual(bui.ZH_TW["disk"].format(f=27, t=128), "Bot 電腦硬碟剩餘 27 GB / 128 GB")
        self.assertEqual((bui.EN["disk_local"], bui.ZH_TW["disk_local"]), ("(this PC)", "（本機）"))
        self.assertNotEqual(bui.ZH_TW["disk_warn"], bui.EN["disk_warn"])
        for lang in ("zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):
            t = bui.for_lang(lang)
            for k in ("disk", "disk_warn", "disk_danger", "disk_local"):
                self.assertNotEqual(t[k], bui.EN[k], (lang, k))

    def test_label_names_the_bot_computer_in_every_language(self):
        for lang in ("en", "zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):
            t = bui.for_lang(lang)
            self.assertIn("Bot", t["disk"], lang)                      # the label says whose disk it is ...
            self.assertIn("{f}", t["disk"]); self.assertIn("{t}", t["disk"])
            self.assertTrue(t["disk_local"].strip(), lang)             # ... and the fallback note exists


class HelpPopoverTests(unittest.TestCase):
    TIPS = ["disk_help_%d" % i for i in range(1, 5)]
    LEAD = ["disk_help_0", "disk_help_local"]

    def test_every_builtin_language_has_a_title_and_four_tips(self):
        langs = [x for x in uh.PAGE_LANGS if x != "auto"]
        self.assertGreaterEqual(len(langs), 11)
        for lang in langs:
            t = bui.for_lang(lang)
            for k in ("disk_help_aria", "disk_help_title", *self.LEAD, *self.TIPS):
                self.assertTrue(str(t[k]).strip(), (lang, k))
            if lang not in ("en", "numeric"):   # translated, not left in English (the privacy table keeps English on purpose)
                for k in ("disk_help_title", *self.LEAD, *self.TIPS):
                    self.assertNotEqual(t[k], bui.EN[k], (lang, k))

    def test_english_and_traditional_chinese_text(self):
        self.assertEqual(bui.EN["disk_help_title"], "Low on disk space?")
        self.assertEqual(bui.ZH_TW["disk_help_title"], "硬碟空間快滿了？")
        self.assertIn("Disk Saver", bui.EN["disk_help_1"])
        self.assertIn("Disk Saver", bui.ZH_TW["disk_help_1"])
        self.assertEqual(bui.EN["disk_help_4"], "Don't Update or Reset the computer just to free space: Update removes installed apps and packages; Reset rebuilds from the last saved snapshot, so very recent changes may be lost.")
        self.assertEqual(bui.ZH_TW["disk_help_4"], "不要為了騰出空間就更新或重設電腦：更新會移除已安裝的應用程式和套件；重設會從上次儲存的快照重建，最近的變更可能會遺失。")
        self.assertEqual(bui.EN["disk_help_1"], "When the Bot computer's disk space is low or critically low, Grok Bot may create a Disk Saver bot to help free space. It doesn't delete anything without your confirmation.")
        self.assertEqual(bui.ZH_TW["disk_help_1"], "Bot 電腦硬碟空間偏低或嚴重不足時，Grok Bot 可能會建立 Disk Saver 來幫忙清理；沒有你確認，它不會刪除任何東西。")
        self.assertEqual(bui.EN["disk_help_2"], "Clear caches that rebuild themselves: browser temp/cache folders, pip/uv/npm download caches.")
        self.assertEqual(bui.EN["disk_help_3"], "Find big folders with:")          # the `du` command is appended as code
        self.assertNotIn("disk_help_5", bui.EN)                                  # exactly four tips

    def test_exactly_four_tips_in_the_page_script(self):
        self.assertIn("for(i=1;i<=4;i++)", bui.JS)
        self.assertIn("i===3&&d.du", bui.JS)                                      # the command sits under tip 3
        for lang in ("en", "zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):
            self.assertNotIn("disk_help_5", bui.for_lang(lang))
            for k in self.TIPS:                                                   # the old "move to cloud storage" tip is gone
                t = bui.for_lang(lang)[k].lower()
                for word in ("cloud", "雲端", "云端", "クラウド", "클라우드", "nube", "nuvem", "nuage", "wolke"):
                    self.assertNotIn(word, t, (lang, k, word))

    def test_first_line_says_the_bot_has_its_own_fixed_disk(self):
        self.assertEqual(bui.EN["disk_help_0"], "This is the disk space on the Bot's computer, separate from your PC. When it gets full, free up some space.")
        self.assertEqual(bui.ZH_TW["disk_help_0"], "這是 Bot 電腦的硬碟空間，跟你的電腦是分開的；快滿時請清理一下。")
        for lang in ("en", "zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):   # no claim about the disk being fixed / unexpandable / cloud
            t = bui.for_lang(lang)["disk_help_0"].lower()
            for word in ("cloud", "fixed", "expand", "雲端", "云端", "固定", "加購", "加购", "クラウド", "클라우드"):
                self.assertNotIn(word, t, (lang, word))
        for lang in ("en", "zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):
            self.assertEqual(re.findall(r"\d+", bui.for_lang(lang)["disk_help_0"]), [], lang)   # no size stated as universal
        self.assertIn("(this PC)", bui.EN["disk_help_local"])
        self.assertIn("（本機）", bui.ZH_TW["disk_help_local"])
        for part in ("bd-help-lead", "bd-help-local"):
            self.assertIn(part, bui.CSS)
        self.assertIn("T.disk_help_0", bui.JS)
        self.assertIn("d.src!=='snapshot'", bui.JS)                  # the "(this PC)" note only when the bar is the local reading

    def test_no_exact_threshold_claim_about_disk_saver(self):
        # Only what the official docs say: low / critically low, may create a Disk Saver bot, nothing deleted without confirmation.
        en = bui.EN["disk_help_1"]
        self.assertRegex(en, r"\bmay\b")
        self.assertRegex(en, r"low or critically low")
        self.assertRegex(en, r"doesn't delete anything without your confirmation")
        for lang in ("en", "zh-TW", "zh-CN", "ja", "ko", "es", "pt", "fr", "de", "id"):
            t = bui.for_lang(lang)["disk_help_1"]
            self.assertIn("Disk Saver", t.replace("Disk-Saver", "Disk Saver"), lang)
            self.assertEqual(re.findall(r"\d+", t), [], lang)                    # no GB trigger number, no 25-30 target
            self.assertNotRegex(t.lower(), r"\bgb\b|\bgo\b|threshold|exactly|usage|用量", lang)

    def test_du_command_is_a_constant_shown_as_code(self):
        self.assertEqual(ud.DU_COMMAND, "du -sh ~/* /workspace/* 2>/dev/null | sort -h | tail")
        self.assertEqual(ud.page_data(reading=(27 * G, 128 * G))["du"], ud.DU_COMMAND)
        self.assertNotIn("du -sh", "".join(bui.EN.values()))        # the command is not part of any translation
        self.assertIn("el('code',d.du)", bui.JS)                    # drawn as <code> with textContent
        self.assertIn(".bd-help-pop code", bui.CSS)

    def test_icon_and_popover_markup_and_aria(self):
        js = bui.JS
        for needle in ("'bd-help'", "'bd-help-pop'", "b.type='button'", "setAttribute('aria-expanded'", "setAttribute('aria-controls','bd-help-pop')",
                       "p.id='bd-help-pop'", "setAttribute('aria-label'", "p.hidden=true", "el('button','?'"):
            self.assertIn(needle, js, needle)
        self.assertIn("e.key==='Escape'", js)                          # Esc closes
        self.assertIn("addEventListener('click'", js)                  # click / tap outside closes
        self.assertIn("closest('.bd-diskrow')", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotRegex(js, r"https?://")                          # works offline: no external resources
        self.assertRegex(bui.CSS, r"\.bd-help\{[^}]*border-radius:50%[^}]*border:1\.5px solid currentColor")
        self.assertIn(".bd-help-pop{position:absolute", bui.CSS)

    def test_button_element_gives_enter_and_space(self):
        # a real <button type=button>: Enter and Space (and a touch tap) all fire its click handler, no extra key code needed
        self.assertIn("el('button','?'", bui.JS)
        self.assertNotIn("tabindex", bui.JS.lower())

    def test_page_carries_the_strings_and_the_script(self):
        tmp = scratch()
        try:
            ctx = make_ctx(tmp, "zh-TW")
            with mock.patch.dict(os.environ, {ud.ENV_FREE: "27"}):
                uh.rewrite_page(ctx)
            page = read_text(os.path.join(tmp, "history", "index.html"))
            data = json.loads(re.search(r'id="bddata">(.*?)</script>', page, re.S).group(1))
            self.assertEqual(data["L"]["disk_help_title"], "硬碟空間快滿了？")
            self.assertEqual(data["disk"]["du"], ud.DU_COMMAND)
            self.assertIn("function help(row,d)", page)
        finally:
            shutil.rmtree(tmp)

    def test_privacy_mode_keeps_the_tips(self):
        tmp = scratch()
        try:
            ctx = make_ctx(tmp, "en", True)
            with mock.patch.dict(os.environ, {ud.ENV_FREE: "27"}):
                uh.rewrite_page(ctx)
            page = read_text(os.path.join(tmp, "history", "index.html"))
            data = json.loads(re.search(r'id="bddata">(.*?)</script>', page, re.S).group(1))
            self.assertEqual(data["L"]["disk_help_title"], "Low on disk space?")
            self.assertEqual(data["disk"]["du"], ud.DU_COMMAND)
        finally:
            shutil.rmtree(tmp)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def data(self, lang="en", privacy=False, cfg=None, free="27"):
        ctx = make_ctx(self.dir, lang, privacy)
        if cfg is not None:
            ctx.cfg = cfg
        with mock.patch.dict(os.environ, {ud.ENV_FREE: free}):
            uh.rewrite_page(ctx)
        page = read_text(os.path.join(self.dir, "history", "index.html"))
        return page, json.loads(re.search(r'id="bddata">(.*?)</script>', page, re.S).group(1))

    def test_bar_is_in_the_page_data_even_without_a_snapshot(self):
        page, d = self.data("en", free="27")
        self.assertIsNone(d["snap"])
        self.assertEqual((d["disk"]["free"], d["disk"]["total"], d["disk"]["level"]), (27.0, 128, "warn"))
        self.assertIn("function disk()", page)
        self.assertIn("Bot computer disk free: {f} GB / {t} GB", page)

    def test_levels_through_the_page(self):
        for free, level in (("60", "ok"), ("27", "warn"), ("15", "danger"), ("30", "ok"), ("20.6", "warn"), ("20", "danger")):
            self.assertEqual(self.data(free=free)[1]["disk"]["level"], level, free)

    def test_traditional_chinese_page(self):
        _, d = self.data("zh-TW")
        self.assertEqual(d["L"]["disk"], "Bot 電腦硬碟剩餘 {f} GB / {t} GB")

    def test_privacy_keeps_the_numbers_but_uses_symbols(self):
        page, d = self.data("en", privacy=True, free="15")
        self.assertEqual((d["disk"]["free"], d["disk"]["level"]), (15.0, "danger"))
        self.assertEqual(d["L"]["disk"], "▤ {f} / {t} GB")
        self.assertRegex(d["L"]["disk_danger"], r"^[^A-Za-z]+$")
        self.assertNotIn("Bot computer disk free", page)

    def test_config_thresholds_reach_the_page(self):
        _, d = self.data(cfg={"disk_warn_gb": 50, "disk_danger_gb": 40}, free="45")
        self.assertEqual((d["disk"]["level"], d["disk"]["warn_gb"], d["disk"]["danger_gb"]), ("warn", 50.0, 40.0))

    def test_percent_thresholds_reach_the_page(self):
        _, d = self.data(free="27")                                  # default 23 % / 16 % of the (fake) 128 GB disk
        self.assertEqual((d["disk"]["level"], d["disk"]["warn_gb"], d["disk"]["danger_gb"]), ("warn", 29.44, 20.48))
        _, d = self.data(cfg={"disk_warn_pct": 30, "disk_danger_pct": 20}, free="27")
        self.assertEqual((d["disk"]["level"], d["disk"]["warn_gb"]), ("warn", 38.4))

    def test_example_config_uses_percentages(self):
        import json
        cfg = json.loads(read_text(os.path.join(os.path.dirname(ud.__file__), "usage_config.example.json")))
        self.assertEqual((cfg["disk_warn_pct"], cfg["disk_danger_pct"]), (23, 16))
        self.assertNotIn("disk_warn_gb", cfg)                        # a GB key in the example would override the percentages
        self.assertNotIn("disk_danger_gb", cfg)

    def test_unreadable_disk_hides_the_bar(self):
        ctx = make_ctx(self.dir)
        with mock.patch("usage_disk.read", return_value=None):
            self.assertIsNone(uh.breakdown_page_data(ctx)["disk"])


if __name__ == "__main__":
    unittest.main()
