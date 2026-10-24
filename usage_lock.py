#!/usr/bin/env python3
"""One shared write lock for every file in a data directory (stdlib only).

Every writer of CSV/JSON state (recorder, sampler, refresh endpoints, check-ins, ledger)
wraps its write in ``write_lock(data_dir)``. The lock is an advisory lock on
``<data_dir>/.write.lock``: ``fcntl.flock`` on Linux/macOS, ``msvcrt.locking`` on Windows.

* Re-entrant inside one thread: a writer that already holds the lock can call another
  helper that takes it again (for example the sampler calling the ledger) without deadlock.
* Different threads and different processes wait for each other.
* ``timeout`` seconds (default 30) then ``LockTimeout`` -- writers never hang forever.
* Hold it only for the write itself, never while reading Grok Build (that can take a minute).
"""
import os
import threading
import time
from contextlib import contextmanager

LOCK_NAME = ".write.lock"
_local = threading.local()


class LockTimeout(RuntimeError):
    pass


def lock_path_for(data_dir):
    return os.path.join(os.path.abspath(str(data_dir)), LOCK_NAME)


def _held():
    if not hasattr(_local, "held"):
        _local.held = {}
    return _local.held


def _try_lock(f):
    try:
        import fcntl
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, PermissionError):
            return False
        except OSError as exc:
            if getattr(exc, "errno", None) in (11, 35):  # EAGAIN / EWOULDBLOCK
                return False
            raise
    except ImportError:  # Windows
        import msvcrt
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False


def _unlock(f):
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except ImportError:
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def file_lock(path, timeout=30.0, poll=0.05):
    """Exclusive, re-entrant (per thread) lock on ``path``."""
    key = os.path.realpath(str(path))
    held = _held()
    if key in held:
        held[key][1] += 1
        try:
            yield
        finally:
            held[key][1] -= 1
        return
    os.makedirs(os.path.dirname(key) or ".", exist_ok=True)
    f = open(key, "a+")
    deadline = time.monotonic() + float(timeout)
    try:
        while not _try_lock(f):
            if time.monotonic() >= deadline:
                raise LockTimeout(f"could not lock {key} within {timeout:g}s")
            time.sleep(poll)
        held[key] = [f, 1]
        try:
            yield
        finally:
            del held[key]
            _unlock(f)
    finally:
        f.close()


def write_lock(data_dir, timeout=30.0):
    """The shared write lock for ``data_dir`` (context manager)."""
    return file_lock(lock_path_for(data_dir), timeout=timeout)
