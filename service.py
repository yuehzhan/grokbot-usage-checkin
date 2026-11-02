#!/usr/bin/env python3
"""Run the local viewer (``usage_history.py serve``) and the sampler in the background (stdlib only).

    python service.py start   [--config usage_config.json] [--data-dir DIR] [--no-sampler] [--open]
    python service.py stop    [--config ...]
    python service.py status  [--config ...]
    python service.py run     [--config ...]      # foreground supervisor (used by start / the scheduled task)

``start`` launches one hidden supervisor process (``run``) and returns once the viewer URL is known.
The supervisor starts the two workers, restarts a worker that dies (at most 5 times per hour) and
keeps ``<data_dir>/service.pid`` / ``service.json`` up to date. ``stop`` ends the supervisor and the
workers and removes the files they leave behind when they are killed (pid file, server info,
write token).

Windows: every process is created with CREATE_NO_WINDOW, so no console window appears or flashes,
including when ``start`` is run with ``pythonw.exe`` from the scheduled task. Linux/macOS: the
supervisor runs in its own session (like ``nohup``).
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import proc_util  # noqa: E402

MAX_RESTARTS_PER_HOUR = 5
LOG_MAX_BYTES = 1_000_000


def _flags():
    if os.name != "nt":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}


def console_python():
    """python.exe next to pythonw.exe: workers need a (hidden) console for Grok Build's pseudo console."""
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def resolve(args):
    """(config path or None, data dir) with the same rules as usage_history.py."""
    import usage_history
    ns = argparse.Namespace(config=args.config, data_dir=args.data_dir)
    cfg = Path(args.config).resolve() if args.config else None
    if cfg is None and (HERE / "usage_config.json").exists():
        cfg = HERE / "usage_config.json"
        ns.config = str(cfg)
    if args.data_dir is None and cfg is not None:
        try:
            d = json.loads(cfg.read_text(encoding="utf-8")).get("data_dir")
        except Exception:
            d = None
        if d and not os.path.isabs(d):  # relative data_dir = relative to the config file, not the cwd
            ns.data_dir = str((cfg.parent / d).resolve())
    prev = os.getcwd()
    try:
        os.chdir(HERE)
        ctx = usage_history.Ctx(ns)
    finally:
        os.chdir(prev)
    return (str(cfg) if cfg else None), ctx.data_dir


def common_args(cfg, data_dir):
    out = []
    if cfg:
        out += ["--config", cfg]
    out += ["--data-dir", str(data_dir)]
    return out


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(p, obj):
    p = Path(p)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _log(path):
    path = Path(path)
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    return open(path, "ab")


def _alive(pid):
    return bool(pid) and proc_util.pid_alive(pid)


def status(cfg, data_dir):
    svc = _read_json(data_dir / "service.json") or {}
    info = _read_json(data_dir / "history_server.json") or {}
    sup = svc.get("supervisor")
    out = {"ok": True, "data_dir": str(data_dir), "config": cfg,
           "running": _alive(sup), "supervisor_pid": sup if _alive(sup) else None,
           "server_pid": svc.get("server") if _alive(svc.get("server")) else None,
           "sampler_pid": svc.get("sampler") if _alive(svc.get("sampler")) else None,
           "url": info.get("url") if _alive(info.get("pid")) else None,
           "logs": [str(data_dir / n) for n in ("service.log", "server.log", "sampler.log")]}
    return out


def cleanup(data_dir):
    """Remove files that workers delete on a clean exit but leave behind when they are killed."""
    info = _read_json(data_dir / "history_server.json")
    if info is None or not _alive(info.get("pid")):
        for n in ("history_server.json", "history_server.token"):
            try:
                (data_dir / n).unlink()
            except OSError:
                pass
    pf = data_dir / "sampler.pid"
    try:
        if pf.exists() and not _alive(pf.read_text(encoding="utf-8").strip()):
            pf.unlink()
    except OSError:
        pass
    for n in ("service.json", "service.pid"):
        try:
            (data_dir / n).unlink()
        except OSError:
            pass


def stop(cfg, data_dir):
    svc = _read_json(data_dir / "service.json") or {}
    stopped = []
    for key in ("supervisor", "server", "sampler"):
        pid = svc.get(key)
        if _alive(pid) and proc_util.looks_like_python(pid):
            proc_util.terminate(pid)
            stopped.append(pid)
    # a server or sampler started by hand (not by this script) is left alone
    cleanup(data_dir)
    return {"ok": True, "stopped": stopped, "data_dir": str(data_dir)}


def run(cfg, data_dir, with_sampler=True):
    """Foreground supervisor."""
    data_dir.mkdir(parents=True, exist_ok=True)
    other = (_read_json(data_dir / "service.json") or {}).get("supervisor")
    if other and other != os.getpid() and _alive(other) and proc_util.looks_like_python(other):
        return  # already running (e.g. started by start.ps1 before the logon task fired)
    py = console_python()
    hist = str(HERE / "usage_history.py")
    base = common_args(cfg, data_dir)
    workers = {"server": [py, "-u", hist, "serve"] + base}
    if with_sampler:
        workers["sampler"] = [py, "-u", hist, "sampler"] + base
    procs, starts = {}, {k: [] for k in workers}
    slog = _log(data_dir / "service.log")

    def say(msg):
        slog.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] {msg}\n".encode("utf-8")); slog.flush()

    def launch(name):
        out = _log(data_dir / ("server.log" if name == "server" else "sampler-console.log"))
        try:
            procs[name] = subprocess.Popen(workers[name], cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=out,
                                           stderr=subprocess.STDOUT, env=dict(os.environ, MPLBACKEND="Agg", PYTHONUTF8="1", PYTHONIOENCODING="utf-8"), **_flags_child())
        finally:
            out.close()
        starts[name].append(time.time())
        say(f"started {name} pid={procs[name].pid}")

    def write_state():
        _write_json(data_dir / "service.json", {"supervisor": os.getpid(), "python": py, "config": cfg,
                                                 **{k: p.pid for k, p in procs.items()}})
        (data_dir / "service.pid").write_text(str(os.getpid()), encoding="utf-8")

    stopping = {"flag": False}

    def on_term(*_):
        stopping["flag"] = True
    try:
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
    except (ValueError, OSError):
        pass
    say(f"supervisor pid={os.getpid()} python={py} config={cfg} data_dir={data_dir}")
    for name in workers:
        launch(name)
    write_state()
    try:
        while not stopping["flag"]:
            time.sleep(2)
            for name, p in list(procs.items()):
                if p.poll() is None:
                    continue
                say(f"{name} exited with code {p.returncode}")
                recent = [t for t in starts[name] if time.time() - t < 3600]
                starts[name] = recent
                if name == "sampler" and p.returncode == 1 and (data_dir / "sampler.pid").exists():
                    say("another sampler is already running; not restarting")
                    procs.pop(name)
                elif len(recent) >= MAX_RESTARTS_PER_HOUR:
                    say(f"{name} restarted {len(recent)} times in the last hour; giving up (see its log)")
                    procs.pop(name)
                else:
                    time.sleep(5)
                    launch(name)
                write_state()
            if not procs:
                say("no workers left; supervisor exits")
                break
    finally:
        for name, p in procs.items():
            if p.poll() is not None:
                continue
            if os.name == "nt":
                proc_util.terminate(p.pid)  # taskkill /T: also ends Grok Build if a read is running
            else:
                p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
        say("supervisor stopped")
        slog.close()
        cleanup(data_dir)


def _flags_child():
    return _flags() if os.name == "nt" else {}


_SAFE_URL = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\]):\d{1,5}(/[A-Za-z0-9._~/?=%-]*)?$")


def wait_http(url, timeout=15):
    """True once the viewer answers HTTP (any status counts: it is up). Never raises."""
    import urllib.error
    import urllib.request
    t0 = time.time()
    while True:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            pass
        if time.time() - t0 >= timeout:
            return False
        time.sleep(0.4)


def open_page(url, wait=15):
    """Open ``url`` in the default browser; never raises.

    Waits until the viewer answers, then tries ``webbrowser`` and, when that fails (it returns False
    quietly when no browser is registered, e.g. under a hidden / service-like session), the platform
    opener: ``os.startfile`` then ``cmd /c start`` (Windows), ``open`` (macOS), ``xdg-open`` (Linux).
    Only a plain loopback http URL is ever handed to an opener.
    Returns {"opened": bool, "method": str|None, "ready": bool}.
    """
    if not url or not _SAFE_URL.match(str(url)):
        return {"opened": False, "method": None, "ready": False}
    ready = wait_http(url, wait)  # open anyway when it is slow: the tab keeps retrying / can be reloaded
    attempts = []
    try:
        import webbrowser
        attempts.append(("webbrowser", lambda: bool(webbrowser.open(url))))
    except Exception:
        pass
    if os.name == "nt":
        attempts.append(("startfile", lambda: (os.startfile(url), True)[1]))
        attempts.append(("cmd-start", lambda: subprocess.Popen(
            ["cmd", "/c", "start", "", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) is not None))
    else:
        exe = "open" if sys.platform == "darwin" else "xdg-open"
        attempts.append((exe, lambda: subprocess.Popen(
            [exe, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) is not None))
    for name, fn in attempts:
        try:
            if fn():
                return {"opened": True, "method": name, "ready": ready}
        except Exception:
            continue
    return {"opened": False, "method": None, "ready": ready}


def _wait_url(cfg, data_dir, st, wait):
    t0 = time.time()
    while not st["url"] and time.time() - t0 < wait:
        time.sleep(0.5)
        st = status(cfg, data_dir)
    return st


def start(cfg, data_dir, with_sampler=True, open_browser=False, wait=60):
    st = status(cfg, data_dir)
    if st["running"]:
        # Still open the page: the shortcut / start.ps1 should always show it,
        # even when the logon task (or an earlier start) already launched it.
        if open_browser:
            st = _wait_url(cfg, data_dir, st, 15)  # the logon task may have started it a moment ago
            if st["url"]:
                st.update(open_page(st["url"]))
        return dict(st, ok=True, note="already running")
    cleanup(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    # pythonw.exe (no console) is fine for the supervisor itself; workers use python.exe, hidden
    sup_py = str(Path(sys.executable))
    cmd = [sup_py, str(Path(__file__).resolve()), "run"] + common_args(cfg, data_dir)
    if not with_sampler:
        cmd.append("--no-sampler")
    subprocess.Popen(cmd, cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, env=dict(os.environ, PYTHONUTF8="1"), **_flags())
    st = _wait_url(cfg, data_dir, st, wait)
    if st["url"] and open_browser:
        st.update(open_page(st["url"]))
    if not st["url"]:
        st["ok"] = False
        st["error"] = f"viewer did not start within {wait}s; see {data_dir / 'server.log'}"
    return st


def main(argv=None):
    p = argparse.ArgumentParser(description="Start/stop the viewer and sampler in the background.")
    p.add_argument("cmd", choices=["start", "stop", "status", "run", "restart"])
    p.add_argument("--config")
    p.add_argument("--data-dir", dest="data_dir")
    p.add_argument("--no-sampler", action="store_true", help="viewer only (e.g. pct_source manual)")
    p.add_argument("--open", action="store_true", help="start: open the page in the browser (also when it is already running)")
    a = p.parse_args(argv)
    cfg, data_dir = resolve(a)
    if a.cmd == "run":
        run(cfg, data_dir, not a.no_sampler)
        return
    if a.cmd == "stop":
        r = stop(cfg, data_dir)
    elif a.cmd == "status":
        r = status(cfg, data_dir)
    else:
        if a.cmd == "restart":
            stop(cfg, data_dir)
        r = start(cfg, data_dir, not a.no_sampler, a.open)
    if sys.stdout is not None:  # None under pythonw.exe
        print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
