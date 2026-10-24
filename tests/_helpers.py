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
    env = dict(os.environ, MPLBACKEND="Agg")
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
