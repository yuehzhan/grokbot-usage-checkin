"""Shared helpers for the tests: scratch dirs and a throw-away local server."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def fixed_datetime(at):
    """A ``datetime`` stand-in whose ``now()`` is pinned to the aware datetime ``at`` (patch it over a module's
    ``datetime`` name so date-dependent output does not depend on the day the tests run). ``isinstance`` still
    accepts ordinary datetimes."""
    from datetime import datetime

    class _Meta(type(datetime)):
        def __instancecheck__(cls, obj):
            return isinstance(obj, datetime)

    class FixedDatetime(datetime, metaclass=_Meta):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.astimezone().replace(tzinfo=None)

    return FixedDatetime


def scratch():
    """A temp dir under the system temp folder (never the real data folder)."""
    return tempfile.mkdtemp(prefix="usagehud-test-")


def request(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def start_server(argv, info_file, wait=60):
    """Start a server process; wait until `info_file` (JSON with url/token_file) appears."""
    env = dict(os.environ, MPLBACKEND="Agg", USAGE_NO_AUTO_UPDATE_CHECK="1")  # never fetch the real checkout
    proc = subprocess.Popen([sys.executable] + argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, text=True)
    t0 = time.time()
    while time.time() - t0 < wait:
        if info_file and os.path.exists(info_file):
            try:
                with open(info_file, encoding="utf-8") as fh:
                    info = json.load(fh)
                if os.path.exists(info.get("token_file", "")):
                    return proc, info
            except ValueError:
                pass
        if info_file is None:
            line = proc.stdout.readline()
            if line.strip().startswith("{"):
                info = json.loads(line)
                for _ in range(100):
                    if os.path.exists(info.get("token_file", "")):
                        break
                    time.sleep(0.05)
                return proc, info
        if proc.poll() is not None:
            raise RuntimeError("server exited early")
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not start")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    if proc.stdout:
        proc.stdout.close()


_CJK = None


def has_cjk_font():
    """True when chart_hud finds a CJK font. Without one (e.g. a bare Linux CI runner) the charts fall back to English
    on purpose, so tests that check Chinese chart text are skipped there instead of failing."""
    global _CJK
    if _CJK is None:
        os.environ.setdefault("MPLBACKEND", "Agg")
        import chart_hud
        chart_hud._add_cjk_fonts_once()
        installed = {f.name for f in chart_hud.font_manager.fontManager.ttflist}
        _CJK = any(c in installed for k in ("zh-TW", "zh-CN", "ja", "ko") for c in chart_hud.FONT_CANDIDATES[k])
    return _CJK


NEEDS_CJK = "no CJK font installed: chart text falls back to English"
