"""One-click installers: the Grok Build "auto" default editor (oneclick.py), the pct_source switch, and the
install.sh / Windows launchers checked without network (stub grok/curl, dry runs, temp folders only)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _helpers import ROOT, scratch

import oneclick  # noqa: E402


class TomlEditTests(unittest.TestCase):
    def test_empty_file_gets_ui_table(self):
        new = oneclick.set_auto("")
        self.assertEqual(oneclick.read_mode(new), "auto")
        self.assertIn('[ui]\npermission_mode = "auto"', new)

    def test_existing_value_replaced_comment_and_other_keys_kept(self):
        text = '# mine\n[ui]\nvim_mode = true\npermission_mode = "ask"  # was ask\n\n[other]\nx = 1\n'
        new = oneclick.set_auto(text)
        self.assertIn('permission_mode = "auto"  # was ask', new)
        self.assertIn("vim_mode = true", new)
        self.assertIn("[other]\nx = 1", new)
        self.assertEqual(new.count("permission_mode"), 1)

    def test_ui_table_without_key_gets_line_under_header(self):
        new = oneclick.set_auto("[ui]\nvim_mode = true\n[cli]\nauto_update = false\n")
        self.assertTrue(new.startswith('[ui]\npermission_mode = "auto"\nvim_mode = true'))
        self.assertIn("[cli]\nauto_update = false", new)

    def test_key_in_other_table_is_not_the_ui_key(self):
        text = '[cli]\npermission_mode = "ask"\n'
        self.assertIsNone(oneclick.read_mode(text))
        new = oneclick.set_auto(text)
        self.assertIn('[cli]\npermission_mode = "ask"', new)
        self.assertEqual(oneclick.read_mode(new), "auto")

    def test_dotted_top_level_key(self):
        new = oneclick.set_auto('ui.permission_mode = "ask"\n[cli]\nx = 1\n')
        self.assertTrue(new.startswith('ui.permission_mode = "auto"'))

    def test_legacy_keys_are_read(self):
        self.assertEqual(oneclick.read_mode("[ui]\nyolo = true\n"), "always-approve")
        self.assertEqual(oneclick.read_mode('[ui]\napproval_mode = "ask"\n'), "ask")
        # permission_mode wins over the legacy keys (Grok Build docs)
        self.assertEqual(oneclick.read_mode('[ui]\nyolo = true\npermission_mode = "auto"\n'), "auto")

    def test_crlf_kept(self):
        new = oneclick.set_auto('[ui]\r\npermission_mode = "ask"\r\n')
        self.assertEqual(new, '[ui]\r\npermission_mode = "auto"\r\n')

    def test_inline_table_refused(self):
        with self.assertRaises(ValueError):
            oneclick.set_auto('ui = { vim_mode = true }\n')

    def test_top_level_dotted_ui_keys_without_the_key_refused(self):
        # adding a [ui] table next to "ui.vim_mode = true" would be an invalid file
        with self.assertRaises(ValueError):
            oneclick.set_auto('ui.vim_mode = true\n[cli]\nx = 1\n')


class GrokAutoCliTests(unittest.TestCase):
    def setUp(self):
        self.home = scratch()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cfg = os.path.join(self.home, "config.toml")
        self.auth = os.path.join(self.home, "auth.json")
        with open(self.auth, "w", encoding="utf-8") as fh:
            fh.write('{"secret": "do-not-touch"}')

    def run_cli(self, *args):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "oneclick.py"), *args], capture_output=True,
                           text=True, env=dict(os.environ, GROK_HOME=self.home))
        return p.returncode, json.loads(p.stdout)

    def write(self, text):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self):
        with open(self.cfg, encoding="utf-8") as fh:
            return fh.read()

    def test_check_set_idempotent_and_restore(self):
        self.assertEqual(self.run_cli("grok-auto", "check")[0], 1)          # no file = ask
        code, out = self.run_cli("grok-auto", "set")
        self.assertEqual((code, out["changed"], out["mode"]), (0, True, "auto"))
        self.assertEqual(self.run_cli("grok-auto", "check"), (0, {"ok": True, "file": self.cfg, "mode": "auto",
                                                                   "auto": True}))
        self.assertFalse(self.run_cli("grok-auto", "set")[1]["changed"])    # second run: nothing to do
        self.write(self.read().replace('"auto"', '"ask"'))                   # switched off by hand / in settings
        code, out = self.run_cli("grok-auto", "set")
        self.assertEqual((code, out["changed"], out["was"]), (0, True, "ask"))
        self.assertEqual(oneclick.read_mode(self.read()), "auto")

    def test_backup_once_and_dry_run_writes_nothing(self):
        self.write('[ui]\npermission_mode = "ask"\n')
        code, out = self.run_cli("grok-auto", "set", "--dry-run")
        self.assertTrue(out["would_change"])
        self.assertIn('"ask"', self.read())
        self.assertFalse(os.path.exists(self.cfg + oneclick.BACKUP_SUFFIX))
        self.run_cli("grok-auto", "set")
        with open(self.cfg + oneclick.BACKUP_SUFFIX, encoding="utf-8") as fh:
            self.assertIn('"ask"', fh.read())

    def test_always_approve_left_alone_unless_forced(self):
        self.write('[ui]\npermission_mode = "always-approve"\n')
        code, out = self.run_cli("grok-auto", "set")
        self.assertEqual((code, out["changed"]), (0, False))
        self.assertIn("always-approve", self.read())
        self.assertTrue(self.run_cli("grok-auto", "set", "--force")[1]["changed"])

    def test_inline_table_reports_manual_fix(self):
        self.write("ui = { vim_mode = true }\n")
        code, out = self.run_cli("grok-auto", "set")
        self.assertEqual(code, 3)
        self.assertIn("permission_mode", out["fix"])

    def test_bom_file_does_not_get_a_second_ui_table(self):
        with open(self.cfg, "wb") as fh:                     # Notepad "UTF-8 with BOM"
            fh.write(b'\xef\xbb\xbf[ui]\nvim_mode = true\n')
        code, out = self.run_cli("grok-auto", "set")
        self.assertEqual((code, out["changed"]), (0, True))
        text = self.read().lstrip("\ufeff")
        self.assertEqual(text.count("[ui]"), 1)
        self.assertEqual(oneclick.read_mode(text), "auto")

    def test_linked_config_stays_a_link(self):
        real = os.path.join(self.home, "dotfiles-config.toml")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write('[ui]\npermission_mode = "ask"\n')
        try:
            os.symlink(real, self.cfg)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        self.assertEqual(self.run_cli("grok-auto", "set")[0], 0)
        self.assertTrue(os.path.islink(self.cfg))
        with open(real, encoding="utf-8") as fh:
            self.assertEqual(oneclick.read_mode(fh.read()), "auto")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs a non-root POSIX user")
    def test_unwritable_folder_gives_a_hand_fix_not_a_traceback(self):
        self.write('[ui]\npermission_mode = "ask"\n')
        os.chmod(self.home, 0o500)
        self.addCleanup(os.chmod, self.home, 0o700)
        code, out = self.run_cli("grok-auto", "set")
        self.assertEqual(code, 3)
        self.assertFalse(out["ok"])
        self.assertIn("permission_mode", out["fix"])

    def test_sign_in_file_never_opened(self):
        before = os.stat(self.auth)
        real_open = open
        opened = []

        def spy(path, *a, **k):
            opened.append(os.fspath(path))
            return real_open(path, *a, **k)

        with mock.patch.dict(os.environ, {"GROK_HOME": self.home}), mock.patch("builtins.open", spy), \
                mock.patch("sys.stdout"):
            oneclick.grok_auto("set")
            oneclick.grok_auto("check")
        self.assertFalse([p for p in opened if p.endswith("auth.json")])
        self.assertEqual(os.stat(self.auth).st_mtime_ns, before.st_mtime_ns)
        with open(os.path.join(ROOT, "oneclick.py"), encoding="utf-8") as fh:
            code = fh.read()
        self.assertNotIn('"auth.json"', code)


class PctSourceTests(unittest.TestCase):
    def test_create_from_example_then_switch(self):
        d = scratch()
        self.addCleanup(shutil.rmtree, d, True)
        cfg = os.path.join(d, "usage_config.json")
        with mock.patch("sys.stdout"):
            self.assertEqual(oneclick.pct_source("auto", cfg), 0)
        with open(cfg, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["pct_source"], "auto")
        data["pct_source"] = "manual"
        data["palette"] = "mine"
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with mock.patch("sys.stdout"):
            oneclick.pct_source("auto", cfg)
        with open(cfg, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual((data["pct_source"], data["palette"]), ("auto", "mine"))   # other keys kept


class PctSourceBomTests(unittest.TestCase):
    def test_config_with_bom_is_read(self):
        d = scratch()
        self.addCleanup(shutil.rmtree, d, True)
        cfg = os.path.join(d, "usage_config.json")
        with open(cfg, "wb") as fh:
            fh.write(b'\xef\xbb\xbf{"pct_source": "manual", "palette": "mine"}')
        with mock.patch("sys.stdout"):
            self.assertEqual(oneclick.pct_source("auto", cfg), 0)
        with open(cfg, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"pct_source": "auto", "palette": "mine"})


def _stub(folder, name, body):
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)


@unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "bash script")
class InstallShTests(unittest.TestCase):
    """install.sh in a copy of the needed files, with a fake HOME/GROK_HOME and stub grok/tmux/curl."""

    def setUp(self):
        self.d = scratch()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.app = os.path.join(self.d, "app")
        os.makedirs(self.app)
        for f in ("install.sh", "oneclick.py", "service.py", "usage_config.example.json", "requirements.txt",
                  "start-dashboard.sh"):
            shutil.copy2(os.path.join(ROOT, f), self.app)
        self.home = os.path.join(self.d, "home")
        self.grok_home = os.path.join(self.home, ".grok")
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        self.marker = os.path.join(self.d, "curl-called")
        _stub(self.bin, "curl", f'touch "{self.marker}"; exit 1')        # never reaches the network
        _stub(self.bin, "tmux", "exit 0")
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        # PATH without any real grok: our stub folder plus system folders that have no grok in them
        keep = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                if p and not os.path.exists(os.path.join(p, "grok"))]
        self.path = os.pathsep.join([self.bin] + keep)

    def run_sh(self, *args, grok=True, detach=False, extra_env=None):
        if grok:
            _stub(self.bin, "grok", 'echo "grok 1.0.0 (stub)"')
        elif os.path.exists(os.path.join(self.bin, "grok")):
            os.remove(os.path.join(self.bin, "grok"))
        import site
        env = {"PATH": self.path, "HOME": self.home, "GROK_HOME": self.grok_home, "LANG": "C.UTF-8",
               "PYTHONUSERBASE": site.getuserbase()}  # keep packages installed with --user visible
        if os.environ.get("VIRTUAL_ENV"):
            env["VIRTUAL_ENV"] = os.environ["VIRTUAL_ENV"]
        env.update(extra_env or {})
        return subprocess.run(["bash", os.path.join(self.app, "install.sh"), *args], capture_output=True,
                              text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
                              start_new_session=detach)

    def test_dry_run_without_grok_shows_official_installer_and_changes_nothing(self):
        p = self.run_sh("--dry-run", grok=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("curl -fsSL https://x.ai/cli/install.sh | bash", p.stdout)
        self.assertIn("Nothing was changed", p.stdout)
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(self.grok_home))
        self.assertFalse(os.path.exists(os.path.join(self.app, "usage_config.json")))

    def test_nothing_is_asked_by_default(self):
        p = self.run_sh(grok=False, detach=True)   # no terminal at all: it must go on by itself, not stop and wait
        self.assertNotIn("Continue?", p.stdout)
        self.assertNotIn("nobody is there", p.stdout)
        self.assertTrue(os.path.exists(self.marker))   # it went straight on to the (stubbed, failing) download

    def test_ask_is_opt_in_and_never_installs_without_an_answer(self):
        p = self.run_sh("--ask", grok=False, detach=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("nobody is there", p.stdout)
        self.assertFalse(os.path.exists(self.marker))

    def test_old_yes_flag_is_still_accepted(self):
        p = self.run_sh("--yes", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("Unknown option", p.stdout)

    def test_failed_download_says_what_to_do(self):
        p = self.run_sh("--no-start", grok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(os.path.exists(self.marker))
        self.assertIn("What to do:", p.stdout)
        # a failed download must say so (not "installed but cannot be found")
        self.assertIn("could not be downloaded", p.stdout)
        self.assertNotIn("cannot be found", p.stdout)

    def _fake_official_installer(self):
        """curl stub that 'downloads' a script which puts a stub grok into $HOME/.grok/bin."""
        _stub(self.bin, "curl", f'''touch "{self.marker}"
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
cat > "$out" <<'EOS'
mkdir -p "$HOME/.grok/bin"
printf '#!/bin/sh\\necho "grok 1.0.0 (stub)"\\n' > "$HOME/.grok/bin/grok"
chmod +x "$HOME/.grok/bin/grok"
EOS''')

    def test_missing_grok_is_installed_from_the_official_script_and_signin_hint_shown(self):
        try:
            import matplotlib  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("needs the requirements installed (as in CI)")
        self._fake_official_installer()
        p = self.run_sh("--no-start", grok=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.home, ".grok", "bin", "grok")))
        self.assertIn("One last step: sign in to Grok Build", p.stdout)
        p = self.run_sh("--no-start", grok=False)      # second run: found in ~/.grok/bin, no download
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Grok Build is installed", p.stdout)
        self.assertIn("Already installed, skipped", p.stdout)
        self.assertNotIn("One last step", p.stdout)
        self.assertIn("No Grok Build sign-in found", p.stdout)       # no sign-in file in the fake GROK_HOME
        self.assertIn("ignore this note", p.stdout)

    def test_official_installer_failing_is_reported(self):
        _stub(self.bin, "curl", 'out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                                'echo "exit 7" > "$out"')
        p = self.run_sh("--no-start", grok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("reported an error", p.stdout)

    def test_folder_with_spaces_and_chinese_letters(self):
        try:
            import matplotlib  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("needs the requirements installed (as in CI)")
        odd = os.path.join(self.d, "我的 資料夾 with spaces")
        shutil.move(self.app, odd)
        self.app = odd
        p = self.run_sh("--no-start")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(odd, "usage_config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["pct_source"], "auto")

    def test_incomplete_download_is_explained(self):
        os.remove(os.path.join(self.app, "oneclick.py"))
        p = self.run_sh("--no-start")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("does not hold the whole project", p.stdout)

    @unittest.skipIf(shutil.which("dash") is None, "dash not installed")
    def test_started_with_sh_is_switched_to_bash(self):
        _stub(self.bin, "grok", 'echo "grok 1.0.0 (stub)"')
        env = {"PATH": self.path, "HOME": self.home, "GROK_HOME": self.grok_home}
        p = subprocess.run(["dash", os.path.join(self.app, "install.sh"), "--dry-run"], capture_output=True,
                           text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Dry run finished", p.stdout)

    def test_full_run_sets_auto_and_rerun_restores_it(self):
        try:
            import matplotlib  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("needs the requirements installed (as in CI)")
        p = self.run_sh("--no-start")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        cfg = os.path.join(self.grok_home, "config.toml")
        with open(cfg, encoding="utf-8") as fh:
            self.assertEqual(oneclick.read_mode(fh.read()), "auto")
        with open(os.path.join(self.app, "usage_config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["pct_source"], "auto")
        p = self.run_sh("--no-start")
        self.assertIn("already 'auto'", p.stdout)
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write('[ui]\npermission_mode = "ask"\n')          # AUTO turned off afterwards
        p = self.run_sh("--no-start")                           # AUTO turned off: put back on re-run
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("is now 'auto'", p.stdout)
        self.assertFalse(os.path.exists(self.marker))

    def test_manual_leaves_grok_alone(self):
        p = self.run_sh("--no-start", "--manual", grok=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(os.path.exists(self.grok_home))
        with open(os.path.join(self.app, "usage_config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["pct_source"], "manual")


@unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "bash script")
class ShortcutTests(InstallShTests):
    """The Desktop launcher made by install.sh. HOME is a temp folder (or ONECLICK_DESKTOP is set), so the real
    Desktop is never touched."""

    def setUp(self):
        super().setUp()
        try:
            import matplotlib  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("needs the requirements installed (as in CI)")
        self.desk = os.path.join(self.home, "Desktop")

    def as_mac(self):  # a `uname` that says Darwin, so the macOS branch runs on Linux
        real = shutil.which("uname")
        _stub(self.bin, "uname", f'if [ "$1" = "-s" ]; then echo Darwin; else exec "{real}" "$@"; fi')

    def fast(self, *args, **kw):
        return self.run_sh("--no-start", "--manual", *args, grok=False, **kw)

    # the inherited install.sh tests need not run again here
    for _n in [n for n in dir(InstallShTests) if n.startswith("test_")]:
        locals()[_n] = None
    del _n

    def test_plan_lists_the_shortcut_and_dry_run_writes_nothing(self):
        os.makedirs(self.desk)
        p = self.run_sh("--dry-run", grok=False)
        self.assertIn("Grok Usage Dashboard", p.stdout)
        self.assertEqual(os.listdir(self.desk), [])
        p = self.run_sh("--dry-run", "--no-shortcut", grok=False)
        self.assertNotIn("launcher on your Desktop", p.stdout)

    def test_linux_makes_a_desktop_launcher_when_the_desktop_folder_exists(self):
        os.makedirs(self.desk)
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = os.path.join(self.desk, "Grok Usage Dashboard.desktop")
        self.assertTrue(os.path.isfile(f))
        self.assertTrue(os.access(f, os.X_OK))
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Name=Grok Usage Dashboard", text)
        self.assertIn('Exec=bash "%s"' % os.path.join(self.app, "start-dashboard.sh"), text)
        self.assertIn("Path=" + self.app, text)
        self.assertIn("Desktop shortcut: " + f, p.stdout)            # the one line at the end
        self.assertTrue(os.access(os.path.join(self.app, "start-dashboard.sh"), os.X_OK))

    def test_linux_without_a_desktop_folder_skips_silently(self):
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(os.path.exists(self.desk))                   # not created
        self.assertNotIn("Desktop shortcut:", p.stdout)
        self.assertNotIn("Could not make", p.stdout)

    def test_no_shortcut_flag(self):
        os.makedirs(self.desk)
        p = self.fast("--no-shortcut")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(os.listdir(self.desk), [])
        self.assertNotIn("Desktop shortcut:", p.stdout)

    def test_rerun_refreshes_the_same_file(self):
        os.makedirs(self.desk)
        self.fast()
        f = os.path.join(self.desk, "Grok Usage Dashboard.desktop")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("stale")
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(os.listdir(self.desk), ["Grok Usage Dashboard.desktop"])
        with open(f, encoding="utf-8") as fh:
            self.assertIn("[Desktop Entry]", fh.read())

    def test_env_override_replaces_the_desktop_folder(self):
        other = os.path.join(self.d, "elsewhere")
        os.makedirs(other)
        os.makedirs(self.desk)
        p = self.fast(extra_env={"ONECLICK_DESKTOP": other})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(os.listdir(other), ["Grok Usage Dashboard.desktop"])
        self.assertEqual(os.listdir(self.desk), [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write anywhere")
    def test_unwritable_desktop_is_only_a_warning(self):
        os.makedirs(self.desk)
        os.chmod(self.desk, 0o500)
        self.addCleanup(os.chmod, self.desk, 0o700)
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)       # the install still succeeds
        self.assertIn("Could not make the desktop launcher", p.stdout)
        self.assertIn("Done.", p.stdout)

    def test_missing_start_script_is_only_a_warning(self):
        os.makedirs(self.desk)
        os.remove(os.path.join(self.app, "start-dashboard.sh"))
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("start-dashboard.sh is missing", p.stdout)
        self.assertEqual(os.listdir(self.desk), [])

    def test_mac_makes_an_executable_command_file_that_cds_into_the_folder(self):
        self.as_mac()
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = os.path.join(self.desk, "Grok Usage Dashboard.command")   # ~/Desktop is made if missing on a Mac
        self.assertTrue(os.access(f, os.X_OK))
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("#!/bin/bash\n"))
        self.assertIn("start-dashboard.sh", text)
        self.assertIn("Desktop shortcut: " + f, p.stdout)
        # the file really goes to the install folder (its name has a space) and runs the start script there
        probe = os.path.join(self.d, "probe.sh")
        with open(f, encoding="utf-8") as fh:
            body = fh.read().replace("exec bash ./start-dashboard.sh", "pwd; ls start-dashboard.sh")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(body)
        q = subprocess.run(["bash", probe], capture_output=True, text=True, cwd="/")
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertEqual(os.path.realpath(q.stdout.splitlines()[0]), os.path.realpath(self.app))
        self.assertEqual(self.fast().returncode, 0)                  # idempotent
        self.assertEqual(os.listdir(self.desk), ["Grok Usage Dashboard.command"])

    def _signin_file(self, mode=0o600):
        os.makedirs(self.grok_home, exist_ok=True)
        path = os.path.join(self.grok_home, "auth.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("synthetic placeholder, not a real sign-in")
        os.chmod(path, mode)
        self.addCleanup(os.chmod, path, 0o600)
        return path

    def test_signed_in_computer_gets_no_sign_in_hint(self):
        self._signin_file()
        for extra in ([], ["--no-shortcut"]):
            p = self.run_sh("--no-start", *extra, grok=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("a sign-in was found", p.stdout)
            self.assertNotIn("No Grok Build sign-in found", p.stdout)
            self.assertNotIn("One last step", p.stdout)
            self.assertNotIn("Never signed in", p.stdout)

    def test_just_installed_but_already_signed_in_gets_no_hint(self):
        self._signin_file()
        self._fake_official_installer()
        p = self.run_sh("--no-start", grok=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("One last step", p.stdout)
        self.assertIn("a sign-in was found", p.stdout)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read anything")
    def test_only_existence_is_checked_the_file_is_never_read(self):
        self._signin_file(mode=0o000)          # unreadable: still found, so it was not opened
        p = self.run_sh("--no-start", grok=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("a sign-in was found", p.stdout)

    def test_manual_mode_prints_no_sign_in_note_either_way(self):
        p = self.run_sh("--no-start", "--manual", grok=False)
        self.assertNotIn("sign-in", p.stdout.lower().split("done.")[-1])

    def test_launcher_in_a_folder_with_spaces_and_chinese_letters(self):
        os.makedirs(self.desk)
        new = os.path.join(self.d, "我的 儀表板")
        shutil.move(self.app, new)
        self.app = new
        p = self.fast()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(self.desk, "Grok Usage Dashboard.desktop"), encoding="utf-8") as fh:
            self.assertIn('Exec=bash "%s"' % os.path.join(new, "start-dashboard.sh"), fh.read())


class LauncherTests(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8", newline="") as fh:
            return fh.read()

    def test_windows_launcher_bypasses_policy_for_this_process_only(self):
        cmd = self.read("Install-Windows.cmd")
        self.assertIn('-ExecutionPolicy Bypass -File ".\\one-click-install.ps1"', cmd)
        self.assertNotIn("Set-ExecutionPolicy", cmd)
        self.assertIn("pause", cmd)
        self.assertIn("pushd", cmd)                       # also works from a network folder
        self.assertIn("if not exist", cmd)                # opened from inside the ZIP viewer: explains what to do

    def test_windows_script_uses_official_installer_only(self):
        ps = self.read("one-click-install.ps1")
        self.assertIn("if ($Ask -and", ps)               # the question exists only with -Ask
        self.assertNotIn("-not $Yes", ps)
        self.assertIn("'https://x.ai/cli/install.ps1'", ps)
        self.assertEqual(ps.count("irm "), 3)  # the variable is used: plan line, install, hand-install hint
        self.assertIn("oneclick.py grok-auto set", ps)

    def test_windows_script_turns_quickedit_off_for_this_console_only(self):
        ps = self.read("one-click-install.ps1")
        self.assertIn("function Disable-QuickEdit", ps)
        self.assertIn("SetConsoleMode", ps)
        self.assertIn("-10", ps)                    # the console input handle
        self.assertIn("0x40", ps)                   # ENABLE_QUICK_EDIT_MODE cleared
        self.assertIn("0x80", ps)                   # ENABLE_EXTENDED_FLAGS kept / set
        body = ps.split("function Disable-QuickEdit", 1)[1].split("\nDisable-QuickEdit", 1)[0]
        self.assertIn("catch", body)                # failure is harmless
        for forbidden in ("HKCU", "HKLM", "Set-ItemProperty", "New-ItemProperty", "reg add", "reg.exe"):
            self.assertNotIn(forbidden, body)       # never touches the registry / global console defaults

    @unittest.skipIf(shutil.which("pwsh") is None and shutil.which("powershell") is None, "PowerShell not installed")
    @unittest.skipIf(not any(shutil.which(c) for c in ("py", "python", "python3")), "needs a Python on PATH")
    def test_windows_script_dry_run_starts_and_changes_nothing(self):
        d = scratch()
        self.addCleanup(shutil.rmtree, d, True)
        app = os.path.join(d, "app")
        os.makedirs(app)
        for f in ("one-click-install.ps1", "install.ps1", "oneclick.py", "service.py", "requirements.txt",
                  "usage_config.example.json"):
            shutil.copy2(os.path.join(ROOT, f), app)
        exe = shutil.which("pwsh") or shutil.which("powershell")
        env = dict(os.environ, USERPROFILE=d, HOME=d, GROK_HOME=os.path.join(d, ".grok"))
        p = subprocess.run([exe, "-NoProfile", "-File", os.path.join(app, "one-click-install.ps1"), "-DryRun",
                            "-Manual"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Dry run finished", p.stdout)
        self.assertEqual(sorted(os.listdir(app)), sorted(["one-click-install.ps1", "install.ps1", "oneclick.py",
                                                          "service.py", "requirements.txt",
                                                          "usage_config.example.json"]))

    def test_sign_in_note_checks_only_that_the_file_exists(self):
        for name in ("one-click-install.ps1", "install.sh"):
            src = self.read(name)
            lines = [l for l in src.splitlines() if "auth.json" in l and not l.lstrip().startswith("#")]
            self.assertEqual(len(lines), 1, (name, lines))             # exactly one place mentions the file
            self.assertTrue("Test-Path" in lines[0] or "[ -f" in lines[0], lines[0])
            for reader in ("Get-Content", "ReadAllText", "cat ", "grep", "open(", "type "):
                self.assertNotIn(reader, lines[0])
        ps = self.read("one-click-install.ps1")
        self.assertIn("$signedIn = Test-Path -LiteralPath", ps)
        self.assertIn("-PathType Leaf", ps)

    def test_windows_script_makes_the_desktop_shortcut_safely(self):
        ps = self.read("one-click-install.ps1")
        self.assertIn("[switch]$NoShortcut", ps)
        body = ps.split("function New-DesktopShortcut", 1)[1].split("\nfunction Disable-QuickEdit", 1)[0]
        self.assertIn("GetFolderPath('Desktop')", body)             # also an OneDrive-moved Desktop
        self.assertIn("ONECLICK_DESKTOP", body)                     # tests never touch the real Desktop
        self.assertIn("Grok Usage Dashboard.lnk", body)
        self.assertIn("-ExecutionPolicy Bypass -File", body)
        self.assertIn("start.ps1", body)
        self.assertIn("WorkingDirectory = $PSScriptRoot", body)
        self.assertIn("*.ico", body)                                # the repo's icon if there is one
        self.assertIn("powershell.exe", body)                       # else PowerShell's own
        self.assertIn("catch", body)                                # failure is a warning only
        self.assertNotIn("Fail ", body)
        for forbidden in ("HKCU", "HKLM", "Set-ItemProperty", "New-ItemProperty", "reg add", "reg.exe"):
            self.assertNotIn(forbidden, body)
        self.assertIn("if (-not $NoShortcut)", ps)
        self.assertIn("Desktop shortcut: $shortcut", ps)           # the one line at the end

    @unittest.skipIf(shutil.which("pwsh") is None, "PowerShell 7 not installed")
    @unittest.skipIf(os.name == "nt", "checks the failure path, which only exists off Windows")
    def test_windows_shortcut_function_failure_is_only_a_warning(self):
        d = scratch()
        self.addCleanup(shutil.rmtree, d, True)
        ps = self.read("one-click-install.ps1").replace("\r\n", "\n")
        func = "function New-DesktopShortcut" + ps.split("function New-DesktopShortcut", 1)[1].split(
            "\nfunction Disable-QuickEdit", 1)[0]
        script = ("function Info([string]$t) { Write-Host \"   $t\" }\n$PSScriptRoot_ = '%s'\n" % ROOT.replace("'", "''")
                  + func.replace("$PSScriptRoot", "$PSScriptRoot_")
                  + "\n$r = New-DesktopShortcut\nif ($r) { 'RESULT=' + $r } else { 'RESULT=none' }\n")
        env = dict(os.environ, ONECLICK_DESKTOP=d)
        for folder in (d, os.path.join(d, "missing")):               # real folder (no COM here) / missing folder
            env["ONECLICK_DESKTOP"] = folder
            p = subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("RESULT=none", p.stdout)
            self.assertRegex(p.stdout, "No desktop shortcut|Could not make the desktop shortcut")
        self.assertEqual(os.listdir(d), [])

    @unittest.skipIf(shutil.which("pwsh") is None, "PowerShell 7 not installed")
    def test_windows_script_dry_run_lists_the_shortcut_unless_skipped(self):
        d = scratch()
        self.addCleanup(shutil.rmtree, d, True)
        desk = os.path.join(d, "Desktop")
        os.makedirs(desk)
        env = dict(os.environ, USERPROFILE=d, HOME=d, GROK_HOME=os.path.join(d, ".grok"), ONECLICK_DESKTOP=desk)
        py = os.path.join(ROOT, "one-click-install.ps1")
        for flag, expect in (([], True), (["-NoShortcut"], False)):
            p = subprocess.run(["pwsh", "-NoProfile", "-File", py, "-DryRun", "-Manual", *flag], capture_output=True,
                               text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual("shortcut on your Desktop" in p.stdout, expect, p.stdout)
        self.assertEqual(os.listdir(desk), [])

    def test_mac_launcher_runs_install_sh(self):
        self.assertIn("exec bash ./install.sh", self.read("Install-Mac.command"))

    @unittest.skipIf(os.name == "nt", "exec bit")
    @unittest.skipIf(os.environ.get("GITHUB_ACTIONS") == "true",
                     "GitHub's web upload cannot set the executable bit, so the public repo's launchers are mode 644 there "
                     "(the README documents the `bash install.sh` / `bash Install-Mac.command` fallback); still checked on every other machine")
    def test_launchers_are_executable(self):
        for f in ("install.sh", "Install-Mac.command", "start-dashboard.sh"):
            self.assertTrue(os.access(os.path.join(ROOT, f), os.X_OK), f)

    @unittest.skipIf(shutil.which("shellcheck") is None, "shellcheck not installed")
    def test_shellcheck_clean(self):
        p = subprocess.run(["shellcheck", "install.sh", "Install-Mac.command", "start-dashboard.sh"], cwd=ROOT, capture_output=True,
                           text=True)
        self.assertEqual(p.returncode, 0, p.stdout)

    @unittest.skipIf(shutil.which("pwsh") is None, "PowerShell 7 not installed")
    def test_powershell_parses(self):
        script = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                  "(Resolve-Path 'one-click-install.ps1'),[ref]$null,[ref]$e); exit $e.Count")
        p = subprocess.run(["pwsh", "-NoProfile", "-Command", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
