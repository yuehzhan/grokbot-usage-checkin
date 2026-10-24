"""
grok_usage_reader.py - Cross-platform Grok Build `/usage` reader and reset parser.

Launches Grok Build (`grok`) in an isolated terminal session, opens `/usage`
(which makes no model call and consumes zero usage), parses both the weekly
usage percentage (`Weekly limit ... N%`) and the reset timestamp
(`Resets: October 24, 09:00`), and exits cleanly.

Only the visible screen text is used (tmux `capture-pane` on Linux/macOS; on Windows the ConPTY
output is replayed into a small virtual screen, `vt_screen.py`). Nothing else is read: no Grok
Build settings, sign-in data, cookies, tokens or logs.

Robustness:
- fixed window size (200x50, `window-size manual`), one hard deadline for the whole read,
  every tmux call has its own timeout, and the session is always killed;
- stale `grok_usage_<pid>_*` sessions left by a crashed reader are cleaned up first;
- a screen that is not a usage screen (update prompt, sign-in request, wrapped/cut line,
  anything unknown) raises ScreenReadError with a short reason - callers record
  "unavailable", never 0%.

Backends:
- Linux / macOS: `tmux` (`new-session -d -x 200 -y 50`, `send-keys -l`, `capture-pane -pJ`, `kill-session`)
- Windows 10 1809+: `pywinpty` (`from winpty import PtyProcess`, ConPTY) + `vt_screen.Screen`

CLI Usage:
    python3 grok_usage_reader.py [--timeout 60] [--work-dir DIR] [--tz TZ]
    python3 grok_usage_reader.py --parse-test "Weekly limit 84% Resets: October 24, 09:00" [--now ISO]
"""

import argparse, json, os, re, shutil, subprocess, sys, tempfile, threading, time
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
from zoneinfo import ZoneInfo

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-9;?]*[a-zA-Z]|\].*?(?:\x07|\x1b\\)|[()][AB012]|[>=<])")
# The percentage must be on the same line as "Weekly limit" or the line right after it.
# "Weekly limit" and its percentage on the same line or on the next non-blank line (a wide, padded
# panel puts the bar + % two rows down with an empty bordered row in between).
WEEKLY_LIMIT_RE = re.compile(r"Weekly limit[^\n%]{0,320}?(?:\n[^\n%]{0,320}?)?(\d{1,3}(?:\.\d+)?)\s?%", re.IGNORECASE)
_BLANKISH = " \t\u2502\u2503\u2551|"  # spaces and vertical panel borders
SESSION_PREFIX = "grok_usage_"
SESSION_RE = re.compile(r"^grok_usage_(\d+)_\d+$")
WINDOW_W, WINDOW_H = 200, 50

LOGIN_RE = re.compile(r"\b(log ?in|sign ?in|re-?authenticat\w*|authenticat\w* required|session (has )?expired|not (logged|signed) in)\b", re.IGNORECASE)
UPDATE_RE = re.compile(r"(update available|new version|a newer version|upgrade (now|available|required)|"
                       r"press \S+ to (update|upgrade)|would you like to (update|upgrade)|restart to update)", re.IGNORECASE)


class ScreenReadError(RuntimeError):
    """The usage screen could not be read. `reason` is a short code:
    update_prompt, login_required, wrapped, unrecognized, empty, timeout, no_tmux, no_winpty, not_found."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def classify_screen(raw_text: str) -> Tuple[str, Optional[float], str]:
    """Look at one captured screen. Returns (state, pct, detail); state is "ok" only when a
    weekly percentage (0-100) is shown next to "Weekly limit"."""
    text = strip_ansi(raw_text or "")
    if not text.strip():
        return "empty", None, "blank screen"
    pct = parse_weekly_pct(text)
    if pct is not None:
        if 0.0 <= pct <= 100.0:
            return "ok", pct, ""
        return "unrecognized", None, f"percentage out of range ({pct:g})"
    if LOGIN_RE.search(text):
        return "login_required", None, "sign-in requested"
    if UPDATE_RE.search(text):
        return "update_prompt", None, "update prompt shown"
    joined = re.sub(r"[ \t]*\n[ \t]*", "", text)
    if parse_weekly_pct(joined) is not None or re.search(r"weekly\s*l\s*i\s*m\s*i\s*t", joined, re.IGNORECASE):
        return "wrapped", None, "usage line wrapped or cut"
    return "unrecognized", None, "no weekly limit on screen"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def cleanup_stale_sessions(tmux_bin: str, timeout: float = 5.0) -> int:
    """Kill grok_usage_<pid>_* tmux sessions whose reader process is gone. Returns the count."""
    try:
        out = subprocess.run([tmux_bin, "list-sessions", "-F", "#{session_name}"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=timeout, check=False).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 0
    n = 0
    for name in out.split():
        m = SESSION_RE.match(name.strip())
        if m and int(m.group(1)) != os.getpid() and not _alive(int(m.group(1))):
            subprocess.run([tmux_bin, "kill-session", "-t", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=False)
            n += 1
    return n
RESET_LINE_RE = re.compile(
    r"(Resets:\s*([A-Za-z]+)\.?\s+(\d{1,2})(?:,\s*(\d{4})|\s+(\d{4}))?,?\s*(\d{1,2}):(\d{2})(?:\s*([AaPp][Mm]))?)",
    re.IGNORECASE,
)

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def parse_weekly_pct(raw_text: str) -> Optional[float]:
    cleaned = strip_ansi(raw_text)
    # rows that hold only spaces / panel borders carry no text; drop them so the bar line counts as "next"
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip(_BLANKISH))
    m = WEEKLY_LIMIT_RE.search(cleaned)
    if m:
        return float(m.group(1))
    return None


def _resolve_tz_and_now(
    now: Optional[Union[datetime, str]] = None,
    tz: Optional[Union[tzinfo, str]] = None,
) -> Tuple[tzinfo, datetime]:
    tz_obj: Optional[tzinfo] = None
    if tz is not None:
        tz_obj = ZoneInfo(tz) if isinstance(tz, str) else tz

    if now is None:
        if tz_obj is None:
            tz_obj = datetime.now().astimezone().tzinfo
        now_dt = datetime.now(tz_obj)
        return tz_obj, now_dt  # type: ignore[return-value]

    if isinstance(now, str):
        s = now.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        parsed_now = datetime.fromisoformat(s)
    else:
        parsed_now = now

    if parsed_now.tzinfo is None:
        if tz_obj is None:
            tz_obj = datetime.now().astimezone().tzinfo
        now_dt = parsed_now.replace(tzinfo=tz_obj)
    else:
        if tz_obj is None:
            tz_obj = parsed_now.tzinfo
            now_dt = parsed_now
        else:
            now_dt = parsed_now.astimezone(tz_obj)

    return tz_obj, now_dt  # type: ignore[return-value]


def _parse_reset_with_raw(
    text: str,
    now: Optional[Union[datetime, str]] = None,
    tz: Optional[Union[tzinfo, str]] = None,
) -> Tuple[Optional[datetime], Optional[str]]:
    cleaned = strip_ansi(text)
    m = RESET_LINE_RE.search(cleaned)
    if not m:
        return None, None

    raw_matched = m.group(1).strip()
    mon_str = m.group(2).lower()
    day_str = m.group(3)
    yr_str = m.group(4) or m.group(5)
    hh_str = m.group(6)
    mm_str = m.group(7)
    ampm_str = m.group(8)

    month = MONTH_MAP.get(mon_str)
    if month is None:
        return None, raw_matched

    day = int(day_str)
    hour = int(hh_str)
    minute = int(mm_str)

    if ampm_str:
        if hour < 1 or hour > 12:
            return None, raw_matched
        if ampm_str.upper() == "AM":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12

    if not (1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        return None, raw_matched

    tz_obj, now_dt = _resolve_tz_and_now(now=now, tz=tz)

    if yr_str:
        try:
            return datetime(int(yr_str), month, day, hour, minute, tzinfo=tz_obj), raw_matched
        except ValueError:
            return None, raw_matched

    base_year = now_dt.year

    # Handle Feb 29 in non-leap years by finding valid candidate years
    def _build_candidate(y: int) -> Optional[datetime]:
        try:
            return datetime(y, month, day, hour, minute, tzinfo=tz_obj)
        except ValueError:
            return None

    cand = _build_candidate(base_year)
    if cand is not None:
        diff_days = (cand - now_dt).total_seconds() / 86400.0
        if diff_days < -2.0:
            nxt = _build_candidate(base_year + 1)
            if nxt is not None:
                cand = nxt
            else:
                for step in range(2, 9):
                    nxt = _build_candidate(base_year + step)
                    if nxt is not None:
                        cand = nxt
                        break
        elif diff_days > 300.0:
            prv = _build_candidate(base_year - 1)
            if prv is not None:
                cand = prv
        return cand, raw_matched

    # Feb 29 when base_year is not a leap year -> try next valid leap year
    for offset in range(1, 9):
        nxt = _build_candidate(base_year + offset)
        if nxt is not None:
            return nxt, raw_matched
    return None, raw_matched


def parse_reset(
    text: str,
    now: Optional[Union[datetime, str]] = None,
    tz: Optional[Union[tzinfo, str]] = None,
) -> Optional[datetime]:
    """Parse 'Resets: <Month> <day>, HH:MM' into a timezone-aware datetime."""
    dt, _ = _parse_reset_with_raw(text, now=now, tz=tz)
    return dt


def find_grok_executable() -> str:
    """Locate the Grok Build CLI: the GROK_BIN environment variable, else `grok` on PATH.

    Only the executable is located and started; no Grok Build file (settings, sign-in data,
    logs) is ever opened by this tool."""
    env_bin = (os.environ.get("GROK_BIN") or "").strip().strip('"')
    if env_bin:
        if Path(env_bin).is_file():
            return str(Path(env_bin).resolve())
        raise ScreenReadError("not_found", f"GROK_BIN is set but is not a file: {env_bin}")
    found = shutil.which("grok")
    if found:
        return str(Path(found).resolve())
    raise ScreenReadError(
        "not_found",
        "Grok Build CLI ('grok') not found on PATH. Install Grok Build, open a new terminal so PATH "
        "is updated (or set GROK_BIN to the full path of grok/grok.exe), or enter the value manually "
        "(usage_history.py sample --pct N)."
    )


def _spawn_argv(grok_bin: str) -> list:
    """Windows: .cmd/.bat shims must run through cmd.exe; everything else is started directly."""
    if sys.platform == "win32" and Path(grok_bin).suffix.lower() in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/c", grok_bin]
    return [grok_bin]


def _read_via_tmux(
    grok_bin: str,
    timeout: float,
    scratch_dir: Path,
    tz: Optional[Union[tzinfo, str]] = None,
) -> Dict[str, Any]:
    tmux_bin = shutil.which("tmux")
    if not tmux_bin:
        raise ScreenReadError(
            "no_tmux",
            "tmux is required on Linux/macOS to read /usage from Grok Build. "
            "Install tmux (e.g. 'sudo apt install tmux' or 'brew install tmux') or pass --pct <N> manually.",
        )
    deadline = time.monotonic() + max(5.0, float(timeout))

    def left(cap: float = 5.0) -> float:
        return max(0.5, min(cap, deadline - time.monotonic()))

    def tmux(*args: str, capture: bool = False) -> str:
        try:
            r = subprocess.run([tmux_bin, *args], stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, text=True, timeout=left(), check=False)
            return (r.stdout or "") if capture else ""
        except subprocess.TimeoutExpired:
            return ""

    def keys(*k: str, literal: bool = False):
        tmux("send-keys", "-t", session_name, *(["-l"] if literal else []), *k)

    cleanup_stale_sessions(tmux_bin)
    session_name = f"{SESSION_PREFIX}{os.getpid()}_{int(time.time() * 1000)}"
    try:
        subprocess.run(
            [tmux_bin, "new-session", "-d", "-s", session_name, "-x", str(WINDOW_W), "-y", str(WINDOW_H),
             "-c", str(scratch_dir), grok_bin],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=left(10.0),
        )
        # keep the size fixed even if someone attaches with a smaller terminal
        tmux("set-option", "-t", session_name, "window-size", "manual")
        tmux("resize-window", "-t", session_name, "-x", str(WINDOW_W), "-y", str(WINDOW_H))

        start_t = time.monotonic()
        sent_usage = False
        resent_usage = False
        tab_switches = 0
        last_tab_t = 0.0
        pct_found_t: Optional[float] = None
        found_pct: Optional[float] = None
        found_reset: Optional[datetime] = None
        found_raw_reset: Optional[str] = None
        pane_text = ""
        bad_state, bad_count = "", 0

        while time.monotonic() < deadline:
            pane_text = tmux("capture-pane", "-pJ", "-t", session_name, capture=True)
            now_mono = time.monotonic()

            state, pct, _detail = classify_screen(pane_text)
            reset_dt, raw_reset = _parse_reset_with_raw(pane_text, tz=tz)
            if state == "ok":
                found_pct = pct
                if pct_found_t is None:
                    pct_found_t = now_mono
            if reset_dt is not None:
                found_reset = reset_dt
                found_raw_reset = raw_reset

            # Return once we have both pct and reset_at, or after polling ~3s past pct discovery
            if found_pct is not None and (found_reset is not None or (now_mono - (pct_found_t or now_mono)) >= 3.0):
                keys("Escape")
                time.sleep(0.15)
                keys("/exit", literal=True)
                keys("Enter")
                time.sleep(0.15)
                keys("C-c")
                return {"pct": found_pct, "reset_at": found_reset, "raw_reset": found_raw_reset}

            # An update prompt or sign-in request that stays on screen after /usage: give up early
            # (only after /usage was sent twice and the prompt stayed for ~4 s, so a banner shown
            #  while the usage panel is still loading does not count)
            if resent_usage and found_pct is None and state in ("update_prompt", "login_required"):
                bad_count = bad_count + 1 if state == bad_state else 1
                bad_state = state
                if bad_count >= 10:
                    raise ScreenReadError(state, _detail)
            else:
                bad_state, bad_count = "", 0

            elapsed = now_mono - start_t
            cleaned = strip_ansi(pane_text).strip()
            if not sent_usage and (len(cleaned) > 10 or elapsed >= 1.8):
                time.sleep(0.35)
                keys("/usage", literal=True)
                keys("Enter")
                sent_usage = True
                last_tab_t = now_mono
            elif sent_usage and found_pct is None:
                # If /usage opened on "Context usage" tab instead of "Usage limit", switch tab boundedly
                if ("Context usage" in cleaned or "Session info" in cleaned) and "Weekly limit" not in cleaned:
                    if tab_switches < 3 and (now_mono - last_tab_t) >= 1.0:
                        keys("Right")
                        tab_switches += 1
                        last_tab_t = now_mono
                elif not resent_usage and elapsed >= 6.5:
                    keys("Escape")
                    time.sleep(0.2)
                    keys("/usage", literal=True)
                    keys("Enter")
                    resent_usage = True

            time.sleep(0.35)

        if found_pct is not None:
            return {"pct": found_pct, "reset_at": found_reset, "raw_reset": found_raw_reset}
        state, _, detail = classify_screen(pane_text)
        if state in ("ok", "empty", "unrecognized"):
            raise ScreenReadError("timeout", f"no weekly limit after {timeout:g}s ({state})")
        raise ScreenReadError(state, detail)
    except subprocess.TimeoutExpired as exc:
        raise ScreenReadError("timeout", f"tmux did not answer ({exc.cmd[1] if exc.cmd else 'tmux'})") from exc
    except subprocess.CalledProcessError as exc:
        raise ScreenReadError("unrecognized", f"could not start tmux session: {(exc.stderr or '').strip()[:120]}") from exc
    finally:
        try:
            subprocess.run([tmux_bin, "kill-session", "-t", session_name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def _read_via_winpty(
    grok_bin: str,
    timeout: float,
    scratch_dir: Path,
    tz: Optional[Union[tzinfo, str]] = None,
) -> Dict[str, Any]:
    try:
        from winpty import PtyProcess  # type: ignore
    except ImportError as exc:
        raise ScreenReadError(
            "no_winpty",
            "pywinpty is required on Windows to read /usage from Grok Build. "
            "Run 'pip install pywinpty' (install.ps1 does this) or enter the value manually (--pct N).",
        ) from exc
    import vt_screen

    try:
        proc = PtyProcess.spawn(_spawn_argv(grok_bin), cwd=str(scratch_dir), dimensions=(WINDOW_H, WINDOW_W))
    except Exception as exc:
        raise ScreenReadError("unrecognized", f"could not start Grok Build in a pseudo console: {exc}") from exc
    chunks: list[str] = []
    screen = vt_screen.Screen(WINDOW_W, WINDOW_H)
    fed = 0
    lock = threading.Lock()
    stop_evt = threading.Event()

    def _reader_worker():
        while not stop_evt.is_set():
            try:
                if not proc.isalive():
                    break
                data = proc.read(4096)
                if isinstance(data, (bytes, bytearray)):
                    data = bytes(data).decode("utf-8", "replace")
                if data:
                    with lock:
                        chunks.append(data)
                else:
                    time.sleep(0.05)
            except EOFError:
                break
            except Exception:
                time.sleep(0.05)

    reader_thread = threading.Thread(target=_reader_worker, daemon=True)
    reader_thread.start()

    try:
        start_t = time.monotonic()
        sent_usage = False
        resent_usage = False
        tab_switches = 0
        last_tab_t = 0.0
        pct_found_t: Optional[float] = None
        found_pct: Optional[float] = None
        found_reset: Optional[datetime] = None
        found_raw_reset: Optional[str] = None

        while (time.monotonic() - start_t) < timeout:
            with lock:
                new_chunks = chunks[fed:]
                fed = len(chunks)
            for c in new_chunks:
                screen.feed(c)
            for answer in screen.take_responses():  # cursor-position / device-attribute replies
                try:
                    proc.write(answer)
                except Exception:
                    pass
            acc_text = screen.text()  # what ConPTY would show (cursor moves applied), like tmux capture-pane
            now_mono = time.monotonic()

            state, pct, _detail = classify_screen(acc_text)
            reset_dt, raw_reset = _parse_reset_with_raw(acc_text, tz=tz)
            if state == "ok":
                found_pct = pct
                if pct_found_t is None:
                    pct_found_t = now_mono
            if reset_dt is not None:
                found_reset = reset_dt
                found_raw_reset = raw_reset

            if found_pct is not None and (found_reset is not None or (now_mono - (pct_found_t or now_mono)) >= 3.0):
                try:
                    proc.write("\x1b")
                    time.sleep(0.15)
                    proc.write("/exit\r\n")
                    time.sleep(0.15)
                    proc.write("\x03")
                except Exception:
                    pass
                return {"pct": found_pct, "reset_at": found_reset, "raw_reset": found_raw_reset}

            elapsed = now_mono - start_t
            cleaned = strip_ansi(acc_text).strip()
            if not sent_usage and (len(cleaned) > 10 or elapsed >= 1.8):
                time.sleep(0.35)
                proc.write("/usage")
                time.sleep(0.1)
                proc.write("\r")
                sent_usage = True
                last_tab_t = now_mono
            elif sent_usage and found_pct is None:
                if ("Context usage" in cleaned or "Session info" in cleaned) and "Weekly limit" not in cleaned:
                    if tab_switches < 3 and (now_mono - last_tab_t) >= 1.0:
                        proc.write("\x1b[C")
                        tab_switches += 1
                        last_tab_t = now_mono
                elif not resent_usage and elapsed >= 6.5:
                    proc.write("\x1b")
                    time.sleep(0.2)
                    proc.write("/usage\r")
                    resent_usage = True

            time.sleep(0.35)

        if found_pct is not None:
            return {"pct": found_pct, "reset_at": found_reset, "raw_reset": found_raw_reset}
        with lock:
            for c in chunks[fed:]:
                screen.feed(c)
        state, _, detail = classify_screen(screen.text())
        if state in ("ok", "empty", "unrecognized"):
            raise ScreenReadError("timeout", f"no weekly limit after {timeout:g}s ({state})")
        raise ScreenReadError(state, detail)
    finally:
        stop_evt.set()
        try:
            if proc.isalive():
                proc.terminate(force=True)
        except Exception:
            pass
        try:
            proc.close()
        except Exception:
            pass


def read_usage(
    timeout: float = 60.0,
    work_dir: Optional[os.PathLike] = None,
    tz: Optional[Union[tzinfo, str]] = None,
) -> Dict[str, Any]:
    """Launch Grok Build, open /usage, and return {'pct': float, 'reset_at': datetime|None, 'raw_reset': str|None}."""
    grok_bin = find_grok_executable()
    if work_dir is not None:
        scratch_path = Path(work_dir).resolve()
        scratch_path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            return _read_via_winpty(grok_bin, timeout, scratch_path, tz=tz)
        return _read_via_tmux(grok_bin, timeout, scratch_path, tz=tz)

    with tempfile.TemporaryDirectory(prefix="grok_usage_") as tmp_dir:
        scratch_path = Path(tmp_dir).resolve()
        if sys.platform == "win32":
            return _read_via_winpty(grok_bin, timeout, scratch_path, tz=tz)
        return _read_via_tmux(grok_bin, timeout, scratch_path, tz=tz)


def read_weekly_pct(timeout: float = 60.0, work_dir: Optional[os.PathLike] = None) -> float:
    """Backward-compatible wrapper returning only the weekly usage percentage as a float."""
    res = read_usage(timeout=timeout, work_dir=work_dir)
    return float(res["pct"])


def main():
    p = argparse.ArgumentParser(description="Read weekly usage % and reset time from Grok Build /usage.")
    p.add_argument("--timeout", type=float, default=60.0, help="Timeout in seconds (default: 60)")
    p.add_argument("--work-dir", default=None, help="Optional scratch directory for launching grok")
    p.add_argument("--tz", default=None, help="Optional IANA timezone name (default: machine local timezone)")
    p.add_argument("--parse-test", default=None, help="Test parsers on given text without launching grok")
    p.add_argument("--now", default=None, help="Reference ISO timestamp for --parse-test year inference")
    p.add_argument("--classify", metavar="FILE", default=None, help="Classify a saved screen text file (testing)")
    args = p.parse_args()

    if args.classify is not None:
        state, pct, detail = classify_screen(Path(args.classify).read_text(encoding="utf-8", errors="replace"))
        sys.stdout.write(json.dumps({"state": state, "pct": pct, "detail": detail}) + "\n")
        sys.exit(0 if state == "ok" else 1)

    if args.parse_test is not None:
        pct = parse_weekly_pct(args.parse_test)
        reset_dt = parse_reset(args.parse_test, now=args.now, tz=args.tz)
        out = {
            "pct": pct,
            "reset_at": reset_dt.isoformat() if reset_dt is not None else None,
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.exit(0)

    try:
        res = read_usage(timeout=args.timeout, work_dir=args.work_dir, tz=args.tz)
        reset_dt = res.get("reset_at")
        out = {
            "pct": res["pct"],
            "reset_at": reset_dt.isoformat() if reset_dt is not None else None,
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.exit(0)
    except ScreenReadError as exc:
        sys.stdout.write(json.dumps({"pct": None, "status": "unavailable", "reason": exc.reason, "detail": exc.detail}) + "\n")
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
