"""Version, CHANGELOG notes and one-click rollback (self_update.py).

Every repository here is local and temporary (a bare "remote" plus clones). The real checkout is never changed.
"""
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from _helpers import ROOT, read_text, request, scratch, start_server
from test_update import g, commit

import self_update  # noqa: E402
import usage_history  # noqa: E402

LOG = """# Changelog

## [1.2.0] - 2026-11-02

### Added
- Second feature <script>alert(1)</script>\u202e

## [1.1.0] - 2026-10-25

### Fixed
- First fix.

## [1.0.0] - 2026-10-24

First state.

[1.2.0]: https://example.invalid/1.2.0
"""


class VersionNotesTests(unittest.TestCase):
    def test_repo_version_and_changelog_agree(self):
        v = read_text(os.path.join(ROOT, "VERSION")).strip()
        self.assertIsNotNone(self_update.parse_version(v))
        self.assertEqual(usage_history.APP_VERSION, v)
        for name in ("CHANGELOG.md", "CHANGELOG.zh-TW.md"):
            entries = self_update.parse_changelog(read_text(os.path.join(ROOT, name)))
            self.assertEqual(entries[0][0], self_update.parse_version(v), name)  # newest first, matches VERSION
            self.assertEqual([e[0] for e in entries], sorted([e[0] for e in entries], reverse=True))

    def test_notes_between(self):
        t = self_update.notes_between(LOG, "1.1.0")
        self.assertTrue(t.startswith("v1.2.0 (2026-11-02)\n\nAdded:\n・Second feature"), t)
        self.assertNotRegex(t, r"(?m)^#|^- |`")
        self.assertNotIn("First fix", t)
        self.assertNotIn("example.invalid", t)  # link references are not part of an entry
        self.assertNotIn("\u202e", t)          # bidi / control characters removed
        self.assertIn("<script>", t)            # kept as text: the page shows it with textContent, never as HTML
        both = self_update.notes_between(LOG, "1.0.0")
        self.assertIn("First fix", both)
        self.assertNotIn("First state", both)
        self.assertEqual(self_update.notes_between(LOG, "1.2.0"), "")
        self.assertIn("Second feature", self_update.notes_between(LOG, None))  # unknown version: newest only
        self.assertNotIn("First fix", self_update.notes_between(LOG, None))
        long = "## [2.0.0] - x\n" + "- line\n" * 2000
        self.assertLessEqual(len(self_update.notes_between(long, "1.0.0")), self_update.NOTES_MAX_CHARS)

    def test_plain_text_formatting(self):
        md = ("## [1.3.0] - 2026-12-01\n\n### 新增\n- 版本號（`VERSION`）顯示在 **網頁** 上，見 [README](README.md)\n"
              "  第二行接在同一點。\n- *斜體* 與 __粗體__\n\n### Fixed:\n- `a` and [b][ref]\n")
        zh = self_update.notes_between(md, "1.2.0", cjk=True)
        self.assertEqual(zh, "v1.3.0（2026-12-01）\n\n新增：\n・版本號（VERSION）顯示在 網頁 上，見 README第二行接在同一點。\n"
                             "・斜體 與 粗體\n\nFixed：\n・a and b")
        en = self_update.notes_between(md, "1.2.0")
        self.assertTrue(en.startswith("v1.3.0 (2026-12-01)\n\n新增:"), en)
        for name, cjk in (("CHANGELOG.md", False), ("CHANGELOG.zh-TW.md", True)):  # the real files come out clean
            t = self_update.notes_between(read_text(os.path.join(ROOT, name)), "1.0.0", cjk=cjk)
            self.assertNotRegex(t, r"(?m)^#|^\s*- |`|\]\(|\*\*", name)
            self.assertIn("・", t)

    def test_page_renders_notes_as_text(self):
        js = usage_history.UPDATE_JS
        self.assertIn("nt.textContent=", js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("/api/update/rollback", js)
        self.assertIn("confirm(f(S.back_confirm", js)


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.remote = self.dir / "remote.git"
        g(self.dir, "init", "-q", "--bare", str(self.remote))
        g(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.dev, self.app = self.dir / "dev", self.dir / "app"
        g(self.dir, "clone", "-q", str(self.remote), str(self.dev))
        g(self.dev, "checkout", "-q", "-b", "main")
        commit(self.dev, "VERSION", "1.1.0\n", "v1.1.0")
        commit(self.dev, "CHANGELOG.md", LOG.replace("## [1.2.0] - 2026-11-02\n\n### Added\n- Second feature <script>alert(1)</script>\u202e\n\n", ""), "log")
        g(self.dev, "push", "-q", "origin", "main")
        g(self.dir, "clone", "-q", str(self.remote), str(self.app))
        self.v1 = g(self.app, "rev-parse", "HEAD")
        commit(self.dev, "VERSION", "1.2.0\n", "v1.2.0")
        commit(self.dev, "CHANGELOG.md", LOG, "log 1.2.0")
        commit(self.dev, "CHANGELOG.zh-TW.md", "## [1.2.0] - 2026-11-02\n\n- 第二個功能\n\n## [1.1.0] - 2026-10-25\n\n- 舊\n", "zh log")
        g(self.dev, "push", "-q", "origin", "main")
        self.v2 = g(self.dev, "rev-parse", "HEAD")
        self.data = self.app / "usage-data"   # gitignored in the real project; untracked here
        self.data.mkdir()
        (self.data / "samples.csv").write_text("keep me\n", encoding="utf-8")
        (self.app / "usage_config.json").write_text('{"tz": "UTC"}\n', encoding="utf-8")
        Path(self.app, ".git", "info", "exclude").write_text("usage-data/\nusage_config.json\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def runner(self, calls):
        def run(argv, timeout, say, cwd):
            calls.append(argv)
            if "pull" in argv:
                return self_update._run_step(argv, timeout, say, cwd)
            return True
        return run

    def update(self):
        self.assertEqual(self_update.check(self.app)["state"], self_update.BEHIND)
        calls = []
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            self.assertTrue(self_update.run(self.app, self.data, delay=0, runner=self.runner(calls)))
        return calls

    def test_changelog_since_uses_fetched_ref_and_language(self):
        self_update.check(self.app)  # git fetch: the notes come from origin/main, no other request
        en = self_update.changelog_since(self.app, "en")
        self.assertIn("Second feature", en)
        self.assertNotIn("First fix", en)
        self.assertEqual(self_update.changelog_since(self.app, "zh-TW"), "v1.2.0（2026-11-02）\n\n・第二個功能")
        self.assertIn("第二個功能", self_update.changelog_since(self.app, "zh-CN"))
        self.assertIn("Second feature", self_update.changelog_since(self.app, "ja"))  # no ja file: English
        self.assertEqual(self_update.read_version(self.app), "1.1.0")
        self.assertEqual(self_update.read_version(self.app, "origin/main"), "1.2.0")

    def test_update_records_previous_then_rollback_restores_it(self):
        self.assertEqual(self_update.rollback_check(self.app, self.data)["state"], self_update.NO_PREVIOUS)
        self.update()
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), self.v2)
        prev = json.loads((self.data / self_update.PREV_FILE).read_text(encoding="utf-8"))
        self.assertEqual((prev["commit"], prev["branch"], prev["version"]), (self.v1, "main", "1.1.0"))
        r = self_update.rollback_check(self.app, self.data)
        self.assertTrue(r["can_rollback"], r)
        self.assertEqual(r["version"], "1.1.0")
        calls = []
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            self.assertTrue(self_update.run(self.app, self.data, delay=0, runner=self.runner(calls), rollback=True))
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), self.v1)
        self.assertEqual(g(self.app, "rev-parse", "--abbrev-ref", "HEAD"), "main")  # no detached HEAD
        self.assertEqual(Path(self.app, "VERSION").read_text(encoding="utf-8"), "1.1.0\n")
        self.assertEqual((self.data / "samples.csv").read_text(encoding="utf-8"), "keep me\n")
        self.assertEqual((self.app / "usage_config.json").read_text(encoding="utf-8"), '{"tz": "UTC"}\n')
        names = [("stop" if "stop" in c else "start" if "start" in c else "?") for c in calls]
        self.assertEqual(names, ["stop", "start"])
        self.assertIn("rollback finished", (self.data / "update.log").read_text(encoding="utf-8"))
        self.assertFalse((self.data / self_update.PREV_FILE).exists())
        self.assertFalse((self.data / "update.lock").exists())
        # future updates still work: a plain fast-forward again
        r = self_update.check(self.app)
        self.assertEqual((r["state"], r["behind"]), (self_update.BEHIND, 3))
        self.update()
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), self.v2)

    def test_rollback_refuses_local_edits_and_keeps_them(self):
        self.update()
        Path(self.app, "VERSION").write_text("my edit\n", encoding="utf-8")
        r = self_update.rollback_check(self.app, self.data)
        self.assertEqual((r["state"], r["can_rollback"]), (self_update.LOCAL_CHANGES, False))
        calls = []
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            self.assertFalse(self_update.run(self.app, self.data, delay=0, runner=self.runner(calls), rollback=True))
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), self.v2)
        self.assertEqual(Path(self.app, "VERSION").read_text(encoding="utf-8"), "my edit\n")
        self.assertTrue(any("start" in c for c in calls))  # the dashboard comes back anyway
        self.assertIn("not rolling back: local_changes", (self.data / "update.log").read_text(encoding="utf-8"))

    def test_rollback_states(self):
        self.update()
        prev = self.data / self_update.PREV_FILE
        rec = json.loads(prev.read_text(encoding="utf-8"))
        prev.write_text(json.dumps(dict(rec, commit=self.v2)), encoding="utf-8")
        self.assertEqual(self_update.rollback_check(self.app, self.data)["state"], self_update.ALREADY)
        prev.write_text(json.dumps(dict(rec, commit="0" * 40)), encoding="utf-8")
        self.assertEqual(self_update.rollback_check(self.app, self.data)["state"], self_update.MISSING)
        prev.write_text(json.dumps(dict(rec, commit="; rm -rf /")), encoding="utf-8")  # not a hash: ignored
        self.assertEqual(self_update.rollback_check(self.app, self.data)["state"], self_update.NO_PREVIOUS)
        commit(self.dev, "side.py", "x\n", "unrelated")
        g(self.dev, "checkout", "-q", "--orphan", "other")
        commit(self.dev, "o.py", "o\n", "orphan")
        other = g(self.dev, "rev-parse", "HEAD")
        g(self.dev, "push", "-q", "origin", "other")
        g(self.app, "fetch", "-q", "origin", "other")
        prev.write_text(json.dumps(dict(rec, commit=other)), encoding="utf-8")
        self.assertEqual(self_update.rollback_check(self.app, self.data)["state"], self_update.NOT_ANCESTOR)
        prev.write_text(json.dumps(rec), encoding="utf-8")
        g(self.app, "checkout", "-q", "-b", "mine")
        self.assertEqual(self_update.rollback_check(self.app, self.data)["state"], self_update.WRONG_BRANCH)

    def test_windows_rollback_uses_the_ps1_scripts(self):
        self.update()
        calls = []
        with mock.patch.object(self_update, "use_windows_scripts", return_value=True):
            self.assertTrue(self_update.run(self.app, self.data, delay=0, runner=self.runner(calls), rollback=True))
        self.assertEqual([Path(c[-1] if c[-1].endswith(".ps1") else c[-2]).name for c in calls],
                         ["stop.ps1", "install.ps1", "start.ps1"])  # same scripts as an update on Windows
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), self.v1)


class RollbackEndpointTests(unittest.TestCase):
    """POST /api/update/rollback has the same token / Host / Origin protection as the other writes."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        cls.cfg = os.path.join(cls.dir, "cfg.json")
        with open(cls.cfg, "w", encoding="utf-8") as fh:
            json.dump({"pct_source": "manual", "tz": "UTC", "update_check": "off"}, fh)
        cls.proc, info = start_server(["usage_history.py", "serve", "--config", cls.cfg, "--data-dir", data, "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url = info["url"]
        cls.token = read_text(info["token_file"])

    @classmethod
    def tearDownClass(cls):
        from _helpers import stop_server
        stop_server(cls.proc)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_guarded_and_nothing_to_roll_back(self):
        p = self.url + "api/update/rollback"
        self.assertEqual(request(p)[0], 405)
        self.assertEqual(request(p, "POST", {}, b"")[0], 403)
        self.assertEqual(request(p, "POST", {"X-Usage-Token": "wrong"}, b"")[0], 403)
        self.assertEqual(request(p, "POST", {"X-Usage-Token": self.token, "Origin": "http://attacker.example"}, b"")[0], 403)
        code, body = request(p, "POST", {"X-Usage-Token": self.token}, b"")  # scratch data dir: no saved version
        self.assertEqual(code, 409, body)
        self.assertEqual(json.loads(body)["error"], self_update.NO_PREVIOUS)
        j = json.loads(request(self.url + "api/update/state")[1])
        self.assertIsNone(j["previous"])
        self.assertEqual(j["version"], usage_history.APP_VERSION)


class PageTests(unittest.TestCase):
    def test_strings(self):
        for lang in ("en", "zh-TW"):
            S = usage_history.UPDATE_STR[lang]
            self.assertIn("{v}", S["back_confirm"])
            self.assertTrue({"no_previous", "already_there", "local_changes", "not_ancestor", "missing_commit"} <= set(S["back_states"]))
        self.assertEqual(usage_history.UPDATE_STR["zh-TW"]["back"], "回到上一版")
        self.assertEqual(usage_history.UPDATE_STR["en"]["back"], "Roll back to previous version")
        self.assertEqual(usage_history.UPDATE_STR["numeric"]["notes"], "")  # privacy view shows no notes


if __name__ == "__main__":
    unittest.main()
