#!/usr/bin/env python3
"""Process helpers that are safe on Windows (stdlib only).

``os.kill(pid, 0)`` is the usual POSIX "is it alive?" probe, but on Windows signal 0 is
``CTRL_C_EVENT``: Python would try to send Ctrl+C to that process group instead of probing it.
These helpers use the Win32 API through ``ctypes`` on Windows and ``os.kill`` elsewhere.
"""
import os
import signal
import subprocess
import sys
import time

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5


def _k32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return ctypes, wintypes, k


def pid_alive(pid):
    """True when a process with this pid is running."""
    try:
        pid = int(str(pid).strip())
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            ctypes, wintypes, k = _k32()
        except (OSError, AttributeError, ImportError):
            return False
        h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # exists, but not ours to query
        try:
            code = wintypes.DWORD()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == _STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_image(pid):
    """Executable name/command line of ``pid`` (lower case) or "" when unknown."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ""
    if os.name == "nt":
        try:
            ctypes, wintypes, k = _k32()
            h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                n = wintypes.DWORD(1024)
                if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    return buf.value.lower()
            finally:
                k.CloseHandle(h)
        except Exception:
            return ""
        return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").lower()
    except OSError:
        pass
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=5).stdout
        return (out or "").strip().lower()
    except Exception:
        return ""


def looks_like_python(pid):
    """Guard against pid reuse: only stop processes that are (probably) our Python."""
    img = process_image(pid)
    return ("python" in img) if img else True  # unknown image: trust our own pidfile


def terminate(pid, timeout=10.0):
    """Stop ``pid`` (and, on Windows, its child processes). Returns True when it is gone."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if not pid_alive(pid):
        return True
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout, creationflags=flags)
        except Exception:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)
    return not pid_alive(pid)


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(a, pid_alive(a), process_image(a))
