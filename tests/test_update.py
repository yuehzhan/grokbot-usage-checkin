"""Check for updates / update in place (self_update.py).

Every repository here is local: a bare "remote" in a scratch dir plus clones of it. No network, and the
real checkout is never fetched, pulled or changed.
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from _helpers import ROOT, read_text, request, scratch, start_server

import self_update  # noqa: E402
import usage_history  # noqa: E402

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "init.defaultBranch=main",
       "-c", "commit.gpgsign=false"]


def g(cwd, *args):
    return subprocess.run(GIT + list(args), cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True).stdout.strip()


def commit(repo, name, text, msg):
    Path(repo, name).write_text(text, encoding="utf-8")
    g(repo, "add", name)
    g(repo, "commit", "-q", "-m", msg)


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class CheckTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())
        self.remote = self.dir / "remote.git"
        g(self.dir, "init", "-q", "--bare", str(self.remote))
        g(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.app = self.dir / "app"      # the user's install
        self.dev = self.dir / "dev"      # where new versions come from
        g(self.dir, "clone", "-q", str(self.remote), str(self.dev))
        g(self.dev, "checkout", "-q", "-b", "main")
        commit(self.dev, "app.py", "v1\n", "first version")
        g(self.dev, "push", "-q", "origin", "main")
        g(self.dir, "clone", "-q", str(self.remote), str(self.app))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def publish(self, msg="Second version"):
        commit(self.dev, "app.py", msg + "\n", msg)
        g(self.dev, "push", "-q", "origin", "main")
        return g(self.dev, "rev-parse", "--short", "HEAD")

    def test_up_to_date(self):
        r = self_update.check(self.app)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], self_update.UP_TO_DATE)
        self.assertEqual(r["behind"], 0)
        self.assertFalse(r["can_update"])

    def test_behind(self):
        self.publish("Add a button")
        h = self.publish("Fix the button")
        head = g(self.app, "rev-parse", "HEAD")
        r = self_update.check(self.app)
        self.assertEqual(r["state"], self_update.BEHIND)
        self.assertTrue(r["can_update"])
        self.assertEqual(r["behind"], 2)
        self.assertEqual(r["latest"], {"hash": h, "subject": "Fix the button"})
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), head)
        self.assertEqual(Path(self.app, "app.py").read_text(encoding="utf-8"), "v1\n")  # check never changes files

    def test_no_fetch_uses_last_fetch(self):
        self.publish()
        self.assertEqual(self_update.check(self.app, fetch=False)["state"], self_update.UP_TO_DATE)
        self.assertEqual(self_update.check(self.app)["state"], self_update.BEHIND)
        self.assertEqual(self_update.check(self.app, fetch=False)["state"], self_update.BEHIND)

    def test_local_changes_refused(self):
        self.publish()
        Path(self.app, "app.py").write_text("my edit\n", encoding="utf-8")
        r = self_update.check(self.app)
        self.assertEqual(r["state"], self_update.LOCAL_CHANGES)
        self.assertFalse(r["can_update"])
        self.assertIn("app.py", r["detail"])

    def test_untracked_files_are_fine(self):  # e.g. usage_config.json, .venv/ (data is gitignored anyway)
        self.publish()
        Path(self.app, "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self_update.check(self.app)["state"], self_update.BEHIND)

    def test_diverged_refused(self):
        self.publish()
        commit(self.app, "mine.txt", "x\n", "local commit")
        r = self_update.check(self.app)
        self.assertEqual(r["state"], self_update.DIVERGED)
        self.assertEqual((r["ahead"], r["behind"]), (1, 1))
        self.assertFalse(r["can_update"])

    def test_wrong_branch_refused(self):
        self.publish()
        g(self.app, "checkout", "-q", "-b", "other")
        self.assertEqual(self_update.check(self.app)["state"], self_update.WRONG_BRANCH)

    def test_not_a_repo(self):
        plain = self.dir / "zip-install"
        plain.mkdir()
        self.assertEqual(self_update.check(plain)["state"], self_update.NOT_GIT)
        inner = self.app / "sub"  # a ZIP extracted inside some other repository
        inner.mkdir()
        (inner / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
        self.assertEqual(self_update.check(inner)["state"], self_update.NOT_GIT)

    def test_git_missing(self):
        r = self_update.check(self.app, git_bin=str(self.dir / "no-such-git"))
        self.assertEqual(r["state"], self_update.NO_GIT)
        self.assertFalse(r["ok"])

    def test_fetch_failed(self):
        g(self.app, "remote", "set-url", "origin", str(self.dir / "gone.git"))
        r = self_update.check(self.app)
        self.assertEqual(r["state"], self_update.FETCH_FAILED)
        self.assertFalse(r["ok"])

    def test_no_remote(self):
        g(self.app, "remote", "remove", "origin")
        self.assertEqual(self_update.check(self.app)["state"], self_update.NO_REMOTE)

    def test_updater_fast_forwards_and_restarts(self):
        """run() with stop/start stubbed: pull --ff-only really runs; data is left alone."""
        h = self.publish("New page")
        data = self.app / "usage-data"
        data.mkdir()
        (data / "samples.csv").write_text("keep me\n", encoding="utf-8")
        self.assertEqual(self_update.check(self.app)["state"], self_update.BEHIND)
        calls = []

        def runner(argv, timeout, say, cwd):
            calls.append(argv)
            if "pull" in argv:
                return self_update._run_step(argv, timeout, say, cwd)
            return True
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            self.assertTrue(self_update.run(self.app, data, delay=0, runner=runner))
        self.assertEqual(g(self.app, "rev-parse", "--short", "HEAD"), h)
        self.assertEqual((data / "samples.csv").read_text(encoding="utf-8"), "keep me\n")
        names = [("stop" if "stop" in c else "pull" if "pull" in c else "start" if "start" in c else "?") for c in calls]
        self.assertEqual(names, ["stop", "pull", "start"])  # requirements.txt unchanged: no pip
        log = (data / "update.log").read_text(encoding="utf-8")
        self.assertIn("update finished", log)
        self.assertFalse((data / "update.lock").exists())

    def test_updater_starts_again_when_pull_fails(self):
        self.publish()
        self_update.check(self.app)
        data = self.dir / "data"
        calls = []

        def runner(argv, timeout, say, cwd):
            calls.append(argv)
            return "pull" not in argv
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            self.assertFalse(self_update.run(self.app, data, delay=0, runner=runner))
        self.assertTrue(any("start" in c for c in calls[-1:]))
        self.assertIn("ERROR: git pull --ff-only failed", (data / "update.log").read_text(encoding="utf-8"))

    def test_updater_refuses_local_changes_but_restarts(self):
        self.publish()
        self_update.check(self.app)
        Path(self.app, "app.py").write_text("my edit\n", encoding="utf-8")
        data = self.dir / "data"
        calls = []
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            self_update.run(self.app, data, delay=0, runner=lambda a, t, s, c: calls.append(a) or True)
        self.assertFalse(any("pull" in c for c in calls))
        self.assertTrue(any("start" in c for c in calls))
        self.assertEqual(Path(self.app, "app.py").read_text(encoding="utf-8"), "my edit\n")


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class LineEndingTests(unittest.TestCase):
    """A .ps1 blob committed with CRLF while .gitattributes says ``*.ps1 text eol=crlf`` (what a web upload does):
    git lists it as modified right after clone. That must not block updates; a real edit still must."""

    def setUp(self):
        self.dir = Path(scratch())
        self.remote = self.dir / "remote.git"
        g(self.dir, "init", "-q", "--bare", str(self.remote))
        g(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.dev, self.app = self.dir / "dev", self.dir / "app"
        g(self.dir, "clone", "-q", str(self.remote), str(self.dev))
        g(self.dev, "checkout", "-q", "-b", "main")
        (self.dev / "start.ps1").write_bytes(b"Write-Host hi\r\nexit 0\r\n")
        (self.dev / "app.py").write_text("v1\n", encoding="utf-8")
        g(self.dev, "add", ".")
        g(self.dev, "commit", "-q", "-m", "first version (CRLF blob)")
        (self.dev / ".gitattributes").write_text("* text=auto\n*.ps1 text eol=crlf\n", encoding="utf-8")
        g(self.dev, "add", ".gitattributes")
        g(self.dev, "commit", "-q", "-m", "attributes")
        g(self.dev, "push", "-q", "origin", "main")
        g(self.dir, "clone", "-q", str(self.remote), str(self.app))
        (self.app / "usage_config.json").write_text('{"lang": "en"}', encoding="utf-8")  # untracked, must survive
        st = (self.app / "start.ps1").stat()
        os.utime(self.app / "start.ps1", (st.st_atime + 5, st.st_mtime + 5))  # make git look at the content
        self.assertIn("start.ps1", g(self.app, "diff", "--name-only", "HEAD"))  # phantom-modified

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def publish(self, fix_ps1=True):
        if fix_ps1:
            (self.dev / "start.ps1").write_bytes(b"Write-Host hi\nexit 0\n")
        (self.dev / "app.py").write_text("v2\n", encoding="utf-8")
        g(self.dev, "add", "-A")
        g(self.dev, "commit", "-q", "-m", "second version")
        g(self.dev, "push", "-q", "origin", "main")
        return g(self.dev, "rev-parse", "HEAD")

    def update(self, pull_ok=None):
        data = self.app / "usage-data"
        data.mkdir(exist_ok=True)

        def runner(argv, timeout, say, cwd):
            if "pull" in argv:
                return self_update._run_step(argv, timeout, say, cwd) if pull_ok is None else pull_ok
            return True
        with mock.patch.object(self_update, "use_windows_scripts", return_value=False):
            ok = self_update.run(self.app, data, delay=0, runner=runner)
        return ok, (data / "update.log").read_text(encoding="utf-8")

    def assert_updated(self, head):
        self.assertEqual(g(self.app, "rev-parse", "HEAD"), head)
        self.assertEqual((self.app / "app.py").read_text(encoding="utf-8"), "v2\n")
        self.assertEqual((self.app / "usage_config.json").read_text(encoding="utf-8"), '{"lang": "en"}')
        attrs = self.app / ".git" / "info" / "attributes"
        self.assertNotIn("self_update", attrs.read_text(encoding="utf-8") if attrs.exists() else "")
        self.assertEqual(g(self.app, "stash", "list"), "")
        self.assertEqual(g(self.app, "rev-list", "--count", "origin/main..HEAD"), "0")  # no commits made

    def test_check_ignores_line_endings(self):
        self.publish()
        r = self_update.check(self.app)
        self.assertEqual(r["state"], self_update.BEHIND)
        self.assertTrue(r["can_update"])
        self.assertEqual(r["eol_only"], ["start.ps1"])
        self.assertEqual(self_update.changed_files(self.app), ([], ["start.ps1"]))

    def test_real_edit_still_blocks(self):
        self.publish()
        (self.app / "start.ps1").write_bytes(b"Write-Host mine\r\nexit 0\r\n")
        r = self_update.check(self.app)
        self.assertEqual(r["state"], self_update.LOCAL_CHANGES)
        self.assertIn("start.ps1", r["detail"])
        ok, log = self.update()
        self.assertFalse(ok)
        self.assertEqual((self.app / "start.ps1").read_bytes(), b"Write-Host mine\r\nexit 0\r\n")

    def test_updater_fast_forwards_when_upstream_fixes_the_file(self):
        head = self.publish(fix_ps1=True)
        self_update.check(self.app)
        ok, log = self.update()
        self.assertTrue(ok, log)
        self.assert_updated(head)
        self.assertEqual((self.app / "start.ps1").read_bytes().replace(b"\r\n", b"\n"), b"Write-Host hi\nexit 0\n")
        self.assertEqual(g(self.app, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertIn("line endings only", log)
        self.assertIn("restored", log)

    def test_updater_fast_forwards_when_upstream_still_has_crlf(self):
        head = self.publish(fix_ps1=False)
        self_update.check(self.app)
        ok, log = self.update()
        self.assertTrue(ok, log)
        self.assert_updated(head)

    def test_reset_keep_fallback_when_pull_refused(self):
        head = self.publish(fix_ps1=True)
        self_update.check(self.app)
        ok, log = self.update(pull_ok=False)
        self.assertTrue(ok, log)
        self.assertIn("git reset --keep origin/main", log)
        self.assertIn("reset --keep worked", log)
        self.assert_updated(head)


class PlanTests(unittest.TestCase):
    def test_only_fast_forward(self):
        for win in (True, False):
            steps = self_update.plan("/x/app", "/x/app/usage-data", windows=win, python="py")
            self.assertEqual([n for n, _ in steps], ["stop", "pull", "install", "start"])
            flat = " ".join(" ".join(a) for _, a in steps if a)
            self.assertIn("pull --ff-only origin main", flat)
            for bad in ("reset", "--force", "push", "clean", "stash", "checkout"):
                self.assertNotIn(bad, flat, (win, bad))

    def test_windows_uses_the_ps1_scripts(self):
        steps = dict(self_update.plan("C:/app", "C:/app/usage-data", windows=True, with_sampler=False))
        self.assertIn("stop.ps1", steps["stop"][-1])
        self.assertTrue(steps["install"][-1].endswith("install.ps1"))
        self.assertEqual(steps["start"][-2:], ["-NoBrowser", "-NoSampler"])
        self.assertIn("Bypass", steps["start"])

    def test_linux_uses_service_py(self):
        steps = dict(self_update.plan("/app", "/data", config="/app/c.json", windows=False, python="/usr/bin/python3",
                                      requirements_changed=False))
        self.assertEqual(steps["stop"][:3], ["/usr/bin/python3", str(Path("/app/service.py")), "stop"])
        self.assertIn("--data-dir", steps["start"])
        self.assertIsNone(steps["install"])
        steps = dict(self_update.plan("/app", "/data", windows=False, python="p"))
        self.assertEqual(steps["install"][:4], ["p", "-m", "pip", "install"])


class AutoSettingTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(scratch())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_on_and_off_values(self):
        self.assertTrue(self_update.auto_enabled({}, self.dir))
        self.assertTrue(self_update.auto_enabled({"update_check": "auto"}, self.dir))
        for v in ("off", "OFF", False, "false"):
            self.assertFalse(self_update.auto_enabled({"update_check": v}, self.dir), v)

    def test_switch_saved_in_config_keeps_other_keys(self):
        cfg = self.dir / "usage_config.json"
        cfg.write_text(json.dumps({"pct_source": "manual", "lang": "zh-TW"}), encoding="utf-8")
        self_update.set_auto(cfg, self.dir / "data", False)
        d = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(d, {"pct_source": "manual", "lang": "zh-TW", "update_check": "off"})
        self.assertFalse(self_update.auto_enabled(d, self.dir / "data"))
        self_update.set_auto(cfg, self.dir / "data", True)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["update_check"], "auto")

    def test_switch_without_config_uses_data_folder(self):
        data = self.dir / "data"
        p = self_update.set_auto(None, data, False)
        self.assertEqual(p, data / self_update.SETTING_FILE)
        self.assertFalse(self_update.auto_enabled({}, data))
        self.assertTrue(self_update.auto_enabled({"update_check": "auto"}, data))  # the config wins when it has the key

    def test_once_a_day(self):
        self.assertTrue(self_update.auto_due(0))
        now = 1_800_000_000
        self.assertFalse(self_update.auto_due(now - 3600, now))
        self.assertTrue(self_update.auto_due(now - 25 * 3600, now))
        self_update.save_last(self.dir, {"state": "up_to_date"}, now)
        self.assertEqual(self_update.load_last(self.dir), (now, {"state": "up_to_date"}))
        self.assertEqual(self_update.load_last(self.dir / "missing"), (0.0, None))


class PageTests(unittest.TestCase):
    def test_strings_complete(self):
        keys = set(usage_history.UPDATE_STR["en"])
        self.assertEqual(set(usage_history.UPDATE_STR["zh-TW"]), keys)
        self.assertTrue(keys <= set(usage_history.UPDATE_STR["numeric"]))
        self.assertEqual(usage_history.UPDATE_STR["zh-TW"]["btn"], "檢查更新")
        self.assertEqual(usage_history.UPDATE_STR["zh-TW"]["go"], "更新")
        for v in usage_history.UPDATE_STR["numeric"].values():  # privacy: no words, no service names
            self.assertNotRegex(str(v), r"(?i)grok|github|update|[\u4e00-\u9fff]")

    def test_page_has_buttons(self):
        d = scratch()
        try:
            data = os.path.join(d, "data")
            subprocess.run([os.sys.executable, os.path.join(ROOT, "usage_history.py"), "history", "--data-dir", data,
                            "--tz", "UTC", "--lang", "zh-TW"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=dict(os.environ, MPLBACKEND="Agg"))
            page = Path(data, "history", "index.html").read_text(encoding="utf-8")
            self.assertIn('id="updbtn"', page)
            self.assertIn(">檢查更新<", page)
            self.assertIn('class="btn updgo"', page)
            self.assertIn("/api/update/apply", page)
            self.assertIn('id="updauto"', page)
            self.assertIn("自動檢查更新", page)
            self.assertIn('class="btn primary" id="refresh"', page)
            self.assertIn("⟳ 重新整理", page)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EndpointGuardTests(unittest.TestCase):
    """The update endpoints are writes: POST + token only (no git runs in these requests)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        data = os.path.join(cls.dir, "data")
        os.makedirs(data)
        cls.cfg = os.path.join(cls.dir, "cfg.json")  # a scratch config: the switch must not edit a real one
        with open(cls.cfg, "w", encoding="utf-8") as fh:
            json.dump({"pct_source": "manual", "tz": "UTC"}, fh)
        cls.proc, info = start_server(["usage_history.py", "serve", "--config", cls.cfg, "--data-dir", data, "--port", "0"],
                                      os.path.join(data, "history_server.json"))
        cls.url = info["url"]
        cls.token = read_text(info["token_file"])

    @classmethod
    def tearDownClass(cls):
        from _helpers import stop_server
        stop_server(cls.proc)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_get_and_missing_token_rejected(self):
        for p in ("api/update/check", "api/update/apply", "api/update/settings"):
            self.assertEqual(request(self.url + p)[0], 405, p)
            self.assertEqual(request(self.url + p, "POST", {}, b"")[0], 403, p)
            self.assertEqual(request(self.url + p, "POST", {"X-Usage-Token": "wrong"}, b"")[0], 403, p)

    def test_auto_switch(self):
        code, body = request(self.url + "api/update/state")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["auto"])  # on by default
        h = {"X-Usage-Token": self.token, "Content-Type": "application/json"}
        self.assertEqual(request(self.url + "api/update/settings", "POST", h, b'{"auto": "maybe"}')[0], 400)
        code, body = request(self.url + "api/update/settings", "POST", h, b'{"auto": false}')
        self.assertEqual(code, 200, body)
        self.assertFalse(json.loads(request(self.url + "api/update/state")[1])["auto"])
        with open(self.cfg, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"pct_source": "manual", "tz": "UTC", "update_check": "off"})
        request(self.url + "api/update/settings", "POST", h, b'{"auto": true}')
        self.assertTrue(json.loads(request(self.url + "api/update/state")[1])["auto"])

    def test_status_has_boot_id(self):
        code, body = request(self.url + "api/status")
        self.assertEqual(code, 200)
        j = json.loads(body)
        self.assertTrue(j["boot"])
        self.assertFalse(j["updating"])


if __name__ == "__main__":
    unittest.main()
