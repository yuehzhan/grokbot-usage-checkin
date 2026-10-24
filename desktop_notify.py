#!/usr/bin/env python3
"""Cross-platform desktop toast notifications (stdlib only, never raises).

  Windows 10/11 : PowerShell -> BurntToast if installed, else Windows.UI.Notifications (WinRT)
  macOS         : osascript 'display notification'
  Linux / BSD   : notify-send (libnotify), if available
  otherwise     : silent no-op

Usage as a module:   notify(title, message) -> "sent:<backend>" | "noop:<reason>" | "error:<backend>:<reason>"
Usage from a shell:  python3 desktop_notify.py "Title" "Message" [--dry-run]

Text is passed as data, never as code: PowerShell gets single-quoted literals inside a
base64 -EncodedCommand (no shell quoting), the WinRT path uses XML text nodes, and
osascript / notify-send receive the strings as separate argv items.
"""
import base64
import os
import re
import shutil
import subprocess
import sys

MAX_TITLE, MAX_MSG = 64, 240
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(s, n):
    s = _CTRL.sub(" ", str(s if s is not None else "")).replace("\r", " ").replace("\n", " ").strip()
    return s[:n]


def ps_quote(s):
    """PowerShell single-quoted literal: only ' (and its typographic variants) need doubling."""
    return "'" + re.sub(r"(['\u2018\u2019\u201a\u201b])", r"\1\1", s) + "'"


def windows_script(title, msg, app_id=None):
    t, m = ps_quote(clean(title, MAX_TITLE)), ps_quote(clean(msg, MAX_MSG))
    aid = ps_quote(app_id or r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe")
    return (
        "$ErrorActionPreference='Stop';"
        f"$t={t};$m={m};"
        "if(Get-Module -ListAvailable -Name BurntToast){Import-Module BurntToast;New-BurntToastNotification -Text $t,$m;exit 0};"
        "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n=$x.GetElementsByTagName('text');"
        "[void]$n.Item(0).AppendChild($x.CreateTextNode($t));[void]$n.Item(1).AppendChild($x.CreateTextNode($m));"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({aid}).Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )


def build_windows_command(title, msg, exe="powershell"):
    enc = base64.b64encode(windows_script(title, msg).encode("utf-16-le")).decode("ascii")
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-EncodedCommand", enc]


def build_macos_command(title, msg):
    return ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run", "--", clean(title, MAX_TITLE), clean(msg, MAX_MSG)]


APP_NAME = "Grok Bot Usage & Check-in"  # display name only (notify-send --app-name)


def build_linux_command(title, msg, app_name=APP_NAME):
    return ["notify-send", "--app-name", app_name, "--", clean(title, MAX_TITLE), clean(msg, MAX_MSG)]


def pick_backend(platform=None, which=shutil.which):
    platform = platform or sys.platform
    if platform.startswith("win"):
        exe = which("powershell") or which("pwsh")
        return ("windows", exe) if exe else (None, "no powershell")
    if platform == "darwin":
        return ("macos", "osascript") if which("osascript") else (None, "no osascript")
    if which("notify-send"):
        return ("linux", "notify-send")
    return (None, "no notifier")


def build_command(title, msg, platform=None, which=shutil.which):
    backend, exe = pick_backend(platform, which)
    if backend == "windows":
        return backend, build_windows_command(title, msg, exe=exe)
    if backend == "macos":
        return backend, build_macos_command(title, msg)
    if backend == "linux":
        return backend, build_linux_command(title, msg)
    return None, exe  # exe holds the reason


def notify(title, msg, enabled=True, dry_run=False, timeout=15):
    """Show a toast. Never raises. Returns a short status string."""
    try:
        if not enabled:
            return "noop:disabled"
        if os.environ.get("USAGE_NOTIFY_DISABLE"):
            return "noop:env-disabled"
        backend, cmd = build_command(title, msg)
        if backend is None:
            return f"noop:{cmd}"
        if dry_run:
            return f"dry-run:{backend}"
        kw = {}
        if backend == "windows" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout, **kw)
        if r.returncode:
            return f"error:{backend}:rc={r.returncode}"
        return f"sent:{backend}"
    except Exception as exc:  # missing display/DBus, timeout, permissions ...
        return f"error:{type(exc).__name__}"


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--dry-run"]
    print(notify(a[0] if a else "Test", a[1] if len(a) > 1 else "desktop_notify test", dry_run="--dry-run" in sys.argv[1:]))
