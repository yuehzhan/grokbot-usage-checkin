"""
usage_record.py - Bot-first, idempotent weekly usage recorder and HUD renderer.

USAGE
-----
    python3 usage_record.py [--pct 73] \
        [--pct-source auto|manual] \
        [--reset-source auto|manual] \
        [--source grok] \
        [--privacy] \
        [--refresh-mode auto|manual] \
        [--refresh-interval 30] \
        [--time HH:MM] \
        [--date YYYY-MM-DD] \
        [--config usage_config.json] \
        [--data-dir DIR] \
        [--out PNG] \
        [--summary PATH] \
        [--format json|text] \
        [--reset-anchor "Sat 09:00" | "YYYY-MM-DDTHH:MM"] \
        [--tz local|<IANA zone, e.g. Europe/Berlin>] \
        [--period-days 7] \
        [--pace 14] \
        [--theme PATH] \
        [--lang auto|en|zh-TW|zh-CN|ja|ko|numeric] \
        [--no-cache] \
        [--no-render] \
        [--no-open]

    python3 usage_record.py --print-schedule [--at 13:15]
    python3 usage_record.py serve [--port 0] [--privacy] ...

SWITCHABLE SOURCES
------------------
- `pct_source` ("auto" | "manual", default "auto"):
  - "auto"  : Reads weekly usage % from Grok Build `/usage` (`pct_source="grok"` in JSON),
              unless `--pct` is passed explicitly on the CLI (which wins as `pct_source="cli"`
              with a note on stderr).
  - "manual": Requires `--pct` on the CLI (`pct_source="cli"` in JSON); missing -> exit 2.
              Grok Build is then only launched if `reset_source == "auto"`.
  - `--source grok` is kept as a backward-compatible alias for `--pct-source auto`.

- `reset_source` ("auto" | "manual", default "auto"):
  - "auto"  : Reads reset timestamp from Grok Build `/usage` (`reset_source="grok"` in JSON,
              also cached to `<data_dir>/state.json`). If the reset cannot be read from Grok
              Build, prints a WARNING on stderr and falls back to:
              1. CLI `--reset-anchor` (`reset_source="cli"`)
              2. Config `"reset_anchor"` (`reset_source="config"`)
              3. Cached `<data_dir>/state.json` (`reset_source="cache"`)
              Missing all -> exit 3.
  - "manual": ALWAYS uses CLI `--reset-anchor` (`reset_source="cli"`) or config
              `"reset_anchor"` (`reset_source="config"`). Never parses the reset from Grok
              Build and never uses the `state.json` cache; missing -> exit 3.

PRIVACY MODE (`--privacy` or `"privacy": true` or `--lang numeric`)
-------------------------------------------------------------------
- Renders word-free numeric chart (`<data_dir>/chart-YYYYMMDD.png` by default).
- Emits generic keys in `latest.json` and `stdout` with no service or timezone names.

EXIT CODES
----------
0 : Success
2 : Bad input (missing/invalid `--pct`, bad `--date`, `--time`, or `--at`)
3 : Configuration error (no reset anchor found, invalid config JSON, bad timezone/anchor)
4 : Render error (dashboard rendering failed)
5 : Source read error (failed to read weekly usage % from source)
"""

import argparse, csv, json, math, os, re, subprocess, sys, tempfile
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

WEEKDAY_SPEC_RE = re.compile(
    r"^\s*([A-Za-z]+)\.?\s+(\d{1,2}):(\d{2})(?:\s*([AaPp][Mm]))?\s*$"
)

WEEKDAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


class SilentArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        fail(2, f"Invalid arguments: {message}")


def emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fail(code, msg):
    emit({"ok": False, "error": str(msg)})
    sys.exit(code)


def format_duration(seconds: float, reset_text: str = "0m") -> str:
    """
    Format a duration in seconds unambiguously:
    - >= 86400s (>= 1 day): '2d 10h'
    - 3600s .. 86399s (1h .. <1d): '9h 05m'
    - 1s .. 3599s (< 1h): '45m'
    - <= 0s: reset_text
    """
    sec = int(seconds)
    if sec <= 0:
        return reset_text
    if sec >= 86400:
        return f"{sec // 86400}d {(sec % 86400) // 3600}h"
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"
    return f"{max(1, sec // 60)}m"


def resolve_refresh_settings(
    cli_mode: Optional[str],
    cli_interval: Optional[Any],
    cfg_data: Dict[str, Any],
) -> Tuple[str, int]:
    """Resolve and validate (refresh_mode, refresh_interval_minutes), clamping interval >= 5 with a stderr warning."""
    raw_mode = cli_mode if cli_mode is not None else cfg_data.get("refresh_mode", "auto")
    if raw_mode is None:
        raw_mode = "auto"
    mode_s = str(raw_mode).strip().lower()
    if mode_s not in ("auto", "manual"):
        fail(3, f"Invalid refresh_mode '{raw_mode}' (expected 'auto' or 'manual')")

    raw_int = cli_interval if cli_interval is not None else cfg_data.get("refresh_interval_minutes", 30)
    if raw_int is None:
        raw_int = 30
    try:
        val_f = float(raw_int)
        if math.isnan(val_f) or math.isinf(val_f):
            raise ValueError("not finite")
    except (ValueError, TypeError) as exc:
        fail(3, f"Invalid refresh_interval_minutes '{raw_int}': {exc}")
        val_f = 30.0

    if val_f < 5.0:
        print(
            f"WARNING: refresh_interval_minutes ({raw_int}) is below minimum 5; clamping to 5 minutes.",
            file=sys.stderr,
        )
        interval_min = 5
    else:
        interval_min = int(round(val_f))
    return mode_s, interval_min


def resolve_period_from_anchor_spec(
    anchor_str: str,
    reading_dt: datetime,
    period_days: int,
    tz: tzinfo,
) -> Tuple[datetime, datetime]:
    """
    Resolve (period_start_dt, period_end_dt) from either:
    - A weekly weekday+time spec like 'Sat 09:00' or 'Saturday 09:00'
    - An ISO datetime string like '2026-10-17T09:00' or '2026-10-17T09:00:00+02:00'
    """
    raw = str(anchor_str).strip()

    # 1. Check weekly weekday + HH:MM spec (e.g. "Sat 09:00", "Saturday 09:00")
    m_wk = WEEKDAY_SPEC_RE.match(raw)
    if m_wk:
        w_key = m_wk.group(1).lower()
        if w_key not in WEEKDAY_MAP:
            raise ValueError(f"Unknown weekday '{m_wk.group(1)}' in reset_anchor '{anchor_str}'")
        target_wday = WEEKDAY_MAP[w_key]
        hour = int(m_wk.group(2))
        minute = int(m_wk.group(3))
        ampm = m_wk.group(4)
        if ampm:
            if hour < 1 or hour > 12:
                raise ValueError(f"Invalid hour in reset_anchor '{anchor_str}'")
            if ampm.upper() == "AM":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Invalid time in reset_anchor '{anchor_str}'")

        cand = reading_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_back = (cand.weekday() - target_wday) % 7
        cand = cand - timedelta(days=days_back)
        if cand > reading_dt:
            cand = cand - timedelta(days=7)
        return cand, cand + timedelta(days=period_days)

    # 2. Check ISO datetime spec
    s = raw.replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    anchor_dt: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(s)
        anchor_dt = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                anchor_dt = datetime.strptime(s, fmt).replace(tzinfo=tz)
                break
            except ValueError:
                continue

    if anchor_dt is None:
        raise ValueError(
            f"Invalid reset_anchor '{anchor_str}' (expected 'Sat HH:MM' or 'YYYY-MM-DDTHH:MM')"
        )

    period_sec = period_days * 86400.0
    diff_sec = (reading_dt - anchor_dt).total_seconds()
    k = math.floor(diff_sec / period_sec)
    p_start = anchor_dt + timedelta(days=k * period_days)
    p_end = p_start + timedelta(days=period_days)
    return p_start, p_end


def resolve_period_from_reset_at(
    reset_at_dt: datetime,
    reading_dt: datetime,
    period_days: int,
    tz: tzinfo,
) -> Tuple[datetime, datetime]:
    """
    Given a target reset_at timestamp (end of period), roll forward by period_days
    steps if reading_dt >= reset_at_dt (or backward if reading_dt < reset_at - period_days)
    so that period_start <= reading_dt < period_end.
    """
    p_end = reset_at_dt.replace(tzinfo=tz) if reset_at_dt.tzinfo is None else reset_at_dt.astimezone(tz)
    step = timedelta(days=period_days)
    while p_end <= reading_dt:
        p_end = p_end + step
    while (p_end - step) > reading_dt:
        p_end = p_end - step
    p_start = p_end - step
    return p_start, p_end


_WRITE_LOCK_DIR: Optional[Path] = None  # set in main(): every write holds <data_dir>/.write.lock


def _write_lock():
    import contextlib
    if _WRITE_LOCK_DIR is None:
        return contextlib.nullcontext()
    import usage_lock
    return usage_lock.write_lock(_WRITE_LOCK_DIR)


def atomic_write_text(target_path: Path, content: str):
    with _write_lock():
        _atomic_write_text(target_path, content)


def _atomic_write_text(target_path: Path, content: str):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target_path.name}.", suffix=".tmp", dir=str(target_path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tf:
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(str(tmp_path), str(target_path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def open_image_file(img_path: Path):
    try:
        if sys.platform == "win32":
            os.startfile(str(img_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(img_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                subprocess.Popen(["xdg-open", str(img_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"Warning: failed to auto-open image '{img_path}': {exc}", file=sys.stderr)


def print_schedule_command(at_str: str, cfg_arg: Optional[str], data_dir: Path):
    try:
        tm = datetime.strptime(at_str.strip(), "%H:%M").time()
    except ValueError:
        fail(2, f"Invalid --at '{at_str}' (expected HH:MM)")

    script_abs = Path(__file__).resolve()
    script_dir = script_abs.parent
    if cfg_arg:
        cfg_abs: Optional[Path] = Path(cfg_arg).resolve()
    elif Path("usage_config.json").exists():
        cfg_abs = Path("usage_config.json").resolve()
    elif (script_dir / "usage_config.json").exists():
        cfg_abs = (script_dir / "usage_config.json").resolve()
    else:
        cfg_abs = None
    data_abs = data_dir.resolve()

    if sys.platform == "win32":
        py_exe = Path(sys.executable or "python.exe")
        if py_exe.name.lower() == "pythonw.exe":
            py_exe = py_exe.with_name("python.exe")
        hh_mm = f"{tm.hour:02d}:{tm.minute:02d}"
        cfg_part_win = f' --config \\"{cfg_abs}\\"' if cfg_abs is not None else ""
        tr_cmd = f'\\"{py_exe}\\" \\"{script_abs}\\" --pct-source auto{cfg_part_win}'
        sys.stdout.write(
            f"# Windows Task Scheduler (uses python.exe, not pythonw.exe, for ConPTY support; /RL LIMITED runs as standard user):\n"
            f'schtasks /Create /SC DAILY /ST {hh_mm} /RL LIMITED /TN "UsageDashboard" /TR "{tr_cmd}"\n'
        )
    else:
        py_bin = sys.executable or "python3"
        log_path = data_abs / "cron.log"
        cfg_part_posix = f" --config {cfg_abs}" if cfg_abs is not None else ""
        sys.stdout.write(
            f"{tm.minute} {tm.hour} * * * cd {script_dir} && {py_bin} {script_abs.name} --pct-source auto{cfg_part_posix} --no-open >> {log_path} 2>&1\n"
        )
    sys.exit(0)


def _normalize_source_mode(val: Optional[str], key_name: str, allow_grok_alias: bool = False) -> str:
    if val is None:
        return "auto"
    s = str(val).strip().lower()
    if allow_grok_alias and s == "grok":
        return "auto"
    if s in ("auto", "manual"):
        return s
    fail(3, f"Invalid {key_name} '{val}' (expected 'auto' or 'manual')")
    return "auto"


def _ledger_hook(data_dir, cfg_data, tz, lang, pct, reset_at, source, error=None):
    """Shared ledger (usage_ledger.py). Never affects the exit code of usage_record.py."""
    try:
        import usage_ledger
        usage_ledger.record_hud(data_dir, cfg_data, pct, reset_at, source=source, error=error, tz=tz, lang=lang)
    except Exception as exc:
        print(f"Warning: ledger write failed: {exc}", file=sys.stderr)


def main(argv: Optional[List[str]] = None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "serve":
        import usage_view
        usage_view.main(raw_argv[1:])
        return

    p = SilentArgumentParser(description="Record weekly usage reading and render HUD dashboard.", add_help=True)
    p.add_argument("--pct", default=None, help="Cumulative weekly usage percentage (0..200)")
    p.add_argument(
        "--pct-source",
        choices=["auto", "manual"],
        default=None,
        help="Where to read weekly usage %%: 'auto' (Grok Build /usage unless --pct is given) or 'manual' (--pct required)",
    )
    p.add_argument(
        "--reset-source",
        choices=["auto", "manual"],
        default=None,
        help="Where to read reset time: 'auto' (Grok Build /usage with fallback to cli/config/cache) or 'manual' (cli/config only)",
    )
    p.add_argument(
        "--source",
        default=None,
        help="Backward-compatible alias for --pct-source ('grok'/'auto' -> auto, 'manual' -> manual)",
    )
    p.add_argument(
        "--privacy",
        action="store_true",
        default=None,
        help="Discreet privacy mode (word-free numeric render, neutral filename and JSON keys)",
    )
    p.add_argument(
        "--refresh-mode",
        choices=["auto", "manual"],
        default=None,
        help="Refresh mode for local viewer ('auto' or 'manual', default: 'auto')",
    )
    p.add_argument(
        "--refresh-interval",
        default=None,
        help="Auto-refresh interval in minutes (default: 30, minimum: 5)",
    )
    p.add_argument("--time", default=None, help="Reading time HH:MM in configured timezone")
    p.add_argument("--date", default=None, help="Reading date YYYY-MM-DD in configured timezone")
    p.add_argument("--config", default=None, help="Path to JSON config file (default: usage_config.json if present)")
    p.add_argument("--data-dir", default=None, help="Directory for weekly CSV, state.json, and latest.json")
    p.add_argument("--out", default=None, help="Output PNG path")
    p.add_argument("--summary", default=None, help="Output summary JSON path (default: <data_dir>/latest.json)")
    p.add_argument("--format", choices=["json", "text"], default="json", help="Stdout format: json (default) or text")
    p.add_argument("--reset-anchor", default=None, help="Reset anchor ('Sat 09:00' or 'YYYY-MM-DDTHH:MM')")
    p.add_argument("--tz", default=None, help="IANA timezone name (default: machine local timezone)")
    p.add_argument("--period-days", default=None, help="Days per cycle (default: 7)")
    p.add_argument("--pace", default=None, help="Daily baseline pace %% (default: 14)")
    p.add_argument("--theme", default=None, help="Theme JSON path for renderer")
    p.add_argument("--lang", default=None, help="Language code (auto, en, zh-TW, zh-CN, ja, ko, numeric)")
    p.add_argument("--title", default=None, help="Custom dashboard title")
    p.add_argument("--bg-color", default=None)
    p.add_argument("--accent-color", default=None)
    p.add_argument("--baseline-color", default=None)
    p.add_argument("--under-color", default=None)
    p.add_argument("--over-color", default=None)
    p.add_argument("--text-color", default=None)
    p.add_argument("--palette", default=None, help="Colour preset (14, see README): orange-teal (default), orange-blue, colorblind, amber-purple, ...")
    p.add_argument("--no-cache", action="store_true", help="Do not write auto-detected reset_at to <data_dir>/state.json")
    p.add_argument("--no-render", action="store_true", help="Update CSV and summary JSON only without rendering PNG")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open rendered PNG")
    p.add_argument("--print-schedule", action="store_true", help="Print OS-appropriate daily schedule command and exit")
    p.add_argument("--at", default="13:15", help="Time HH:MM for --print-schedule (default: 13:15)")
    args = p.parse_args(raw_argv)

    # Load config file (precedence: CLI flags > config file > env > defaults)
    cfg_file_data: Dict[str, Any] = {}
    cfg_path = Path(args.config) if args.config is not None else None
    default_cfg_path = Path("usage_config.json")
    if cfg_path is not None:
        if not cfg_path.exists():
            fail(3, f"Config file not found: {cfg_path}")
        try:
            with cfg_path.open(encoding="utf-8") as fp:
                cfg_file_data = json.load(fp)
        except Exception as exc:
            fail(3, f"Failed to parse config file '{cfg_path}': {exc}")
    elif default_cfg_path.exists():
        try:
            with default_cfg_path.open(encoding="utf-8") as fp:
                cfg_file_data = json.load(fp)
        except Exception as exc:
            fail(3, f"Failed to parse default usage_config.json: {exc}")

    def resolve(cli_val, *cfg_keys, default=None):
        if cli_val is not None:
            return cli_val
        for k in cfg_keys:
            if k in cfg_file_data and cfg_file_data[k] is not None:
                return cfg_file_data[k]
        return default

    # Validate refresh settings if provided in CLI or config
    resolve_refresh_settings(args.refresh_mode, args.refresh_interval, cfg_file_data)

    resolved_lang = resolve(args.lang, "lang")
    is_privacy = bool(resolve(args.privacy, "privacy", default=False)) or (
        str(resolved_lang or "").strip().lower() == "numeric"
    )

    env_dir = os.environ.get("USAGE_DASH_DIR")
    data_dir = Path(resolve(args.data_dir, "data_dir", default=(env_dir if env_dir else "./usage-data")))

    if args.print_schedule:
        print_schedule_command(args.at, args.config, data_dir)

    globals()["_WRITE_LOCK_DIR"] = data_dir.resolve()  # shared write lock for this data dir

    # Resolve timezone (default = machine local timezone)
    tz_spec = resolve(args.tz, "tz", default=None)
    if tz_spec is None or str(tz_spec).strip().lower() in ("", "local"):
        tz: tzinfo = datetime.now().astimezone().tzinfo  # type: ignore[assignment]
        tz_for_render = "local"
    else:
        try:
            tz = ZoneInfo(str(tz_spec))
            tz_for_render = str(tz_spec)
        except Exception as exc:
            fail(3, "invalid timezone" if is_privacy else f"Invalid timezone '{tz_spec}': {exc}")

    try:
        period_days = int(resolve(args.period_days, "period_days", default=7))
        if period_days <= 0:
            raise ValueError("period_days must be positive")
    except Exception as exc:
        fail(3, f"Invalid period_days: {exc}")

    try:
        pace_val = float(resolve(args.pace, "pace", default=14.0))
        if pace_val <= 0:
            raise ValueError("pace must be positive")
    except Exception as exc:
        fail(3, f"Invalid pace: {exc}")

    # Resolve reading timestamp in tz (exit 2 on bad --date / --time)
    now_dt = datetime.now(tz)
    if args.date is not None:
        try:
            d_part = datetime.strptime(args.date.strip(), "%Y-%m-%d").date()
        except ValueError:
            fail(2, f"Invalid --date '{args.date}' (expected YYYY-MM-DD)")
    else:
        d_part = now_dt.date()

    if args.time is not None:
        try:
            t_part = datetime.strptime(args.time.strip(), "%H:%M").time()
        except ValueError:
            fail(2, f"Invalid --time '{args.time}' (expected HH:MM)")
    else:
        t_part = now_dt.time().replace(second=0, microsecond=0)

    reading_dt = datetime(d_part.year, d_part.month, d_part.day, t_part.hour, t_part.minute, tzinfo=tz)

    # Resolve pct_source mode ("auto" | "manual", default "auto"; --source grok aliases pct_source=auto)
    raw_pct_source = (
        args.pct_source
        if args.pct_source is not None
        else (
            args.source
            if args.source is not None
            else resolve(None, "pct_source", "source", default="auto")
        )
    )
    pct_source_mode = _normalize_source_mode(raw_pct_source, "pct_source", allow_grok_alias=True)

    # Resolve reset_source mode ("auto" | "manual", default "auto")
    raw_reset_source = resolve(args.reset_source, "reset_source", default="auto")
    reset_source_mode = _normalize_source_mode(raw_reset_source, "reset_source", allow_grok_alias=False)

    # Validate CLI --pct early if provided or required by pct_source_mode == "manual"
    pct_val: Optional[float] = None
    actual_pct_source: Optional[str] = None

    if pct_source_mode == "manual":
        if args.pct is None:
            fail(2, "Missing required argument: --pct (pct_source is 'manual')")
        try:
            pct_val = float(args.pct)
        except (ValueError, TypeError):
            fail(2, f"Invalid --pct value '{args.pct}': must be a number")
        if math.isnan(pct_val) or math.isinf(pct_val) or pct_val < 0.0 or pct_val > 200.0:
            fail(2, f"--pct out of range ({pct_val}): must be between 0 and 200")
        actual_pct_source = "cli"
    else:
        # pct_source_mode == "auto"
        if args.pct is not None:
            try:
                pct_val = float(args.pct)
            except (ValueError, TypeError):
                fail(2, f"Invalid --pct value '{args.pct}': must be a number")
            if math.isnan(pct_val) or math.isinf(pct_val) or pct_val < 0.0 or pct_val > 200.0:
                fail(2, f"--pct out of range ({pct_val}): must be between 0 and 200")
            actual_pct_source = "cli"
            print(
                "Note: --pct provided explicitly on CLI; overriding pct_source='auto' with CLI value.",
                file=sys.stderr,
            )

    # Validate manual reset anchor early if reset_source_mode == "manual"
    cfg_anchor_str = cfg_file_data.get("reset_anchor") or cfg_file_data.get("anchor")
    period_start_dt: Optional[datetime] = None
    period_end_dt: Optional[datetime] = None
    actual_reset_source: Optional[str] = None

    if reset_source_mode == "manual":
        if args.reset_anchor is not None:
            actual_reset_source = "cli"
            try:
                period_start_dt, period_end_dt = resolve_period_from_anchor_spec(
                    args.reset_anchor, reading_dt, period_days, tz
                )
            except ValueError as exc:
                fail(3, str(exc))
        elif cfg_anchor_str:
            actual_reset_source = "config"
            try:
                period_start_dt, period_end_dt = resolve_period_from_anchor_spec(
                    str(cfg_anchor_str), reading_dt, period_days, tz
                )
            except ValueError as exc:
                fail(3, str(exc))
        else:
            fail(
                3,
                "no reset anchor configured"
                if is_privacy
                else "no reset anchor: reset_source is 'manual', so --reset-anchor or config 'reset_anchor' is required",
            )

    # Launch reader at most once if needed by pct_source=="auto" (without --pct) or reset_source=="auto"
    need_grok_for_pct = pct_source_mode == "auto" and pct_val is None
    need_grok_for_reset = reset_source_mode == "auto"

    grok_reset_dt: Optional[datetime] = None
    grok_reset_err_reason = "auto source unavailable" if is_privacy else "not available"

    if need_grok_for_pct or need_grok_for_reset:
        try:
            import grok_usage_reader
            grok_res = grok_usage_reader.read_usage(tz=tz)
            if need_grok_for_pct:
                pct_val = float(grok_res["pct"])
                if math.isnan(pct_val) or math.isinf(pct_val) or pct_val < 0.0 or pct_val > 200.0:
                    fail(2, f"--pct out of range ({pct_val}): must be between 0 and 200")
                actual_pct_source = "grok"
            if need_grok_for_reset:
                grok_reset_dt = grok_res.get("reset_at")
                if grok_reset_dt is None:
                    grok_reset_err_reason = (
                        "reset timestamp not found in source output"
                        if is_privacy
                        else "no 'Resets:' line found in Grok Build /usage output"
                    )
        except Exception as exc:
            if need_grok_for_pct:
                _ledger_hook(data_dir, cfg_file_data, tz, "numeric" if is_privacy else str(resolved_lang or "en"),
                             None, None, "record", error=str(exc) or type(exc).__name__)
                if is_privacy:
                    fail(5, "source read failed")
                else:
                    fail(5, f"Failed to read weekly usage from Grok Build: {exc}")
            grok_reset_err_reason = "source read failed" if is_privacy else str(exc)

    assert pct_val is not None and actual_pct_source is not None

    # Resolve reset when reset_source_mode == "auto"
    state_path = data_dir / "state.json"
    should_cache = (not args.no_cache) and bool(resolve(None, "cache_reset", default=True))

    if reset_source_mode == "auto":
        if grok_reset_dt is not None:
            actual_reset_source = "grok"
            period_start_dt, period_end_dt = resolve_period_from_reset_at(
                grok_reset_dt, reading_dt, period_days, tz
            )
            if should_cache:
                state_payload = {
                    "reset_at": period_end_dt.isoformat(timespec="minutes"),
                    "period_days": period_days,
                    "updated_at": now_dt.isoformat(timespec="minutes"),
                    "source": "a" if is_privacy else "grok",
                }
                try:
                    atomic_write_text(state_path, json.dumps(state_payload, ensure_ascii=False, indent=2) + "\n")
                except Exception as exc:
                    print(f"Warning: failed to write state cache '{state_path}': {exc}", file=sys.stderr)
        else:
            src_label = "auto source" if is_privacy else "Grok Build"
            if args.reset_anchor is not None:
                print(
                    f"WARNING: Could not read reset time from {src_label} ({grok_reset_err_reason}); "
                    f"falling back to CLI --reset-anchor.",
                    file=sys.stderr,
                )
                actual_reset_source = "cli"
                try:
                    period_start_dt, period_end_dt = resolve_period_from_anchor_spec(
                        args.reset_anchor, reading_dt, period_days, tz
                    )
                except ValueError as exc:
                    fail(3, str(exc))
            elif cfg_anchor_str:
                print(
                    f"WARNING: Could not read reset time from {src_label} ({grok_reset_err_reason}); "
                    f"falling back to config reset_anchor.",
                    file=sys.stderr,
                )
                actual_reset_source = "config"
                try:
                    period_start_dt, period_end_dt = resolve_period_from_anchor_spec(
                        str(cfg_anchor_str), reading_dt, period_days, tz
                    )
                except ValueError as exc:
                    fail(3, str(exc))
            elif state_path.exists():
                try:
                    with state_path.open(encoding="utf-8") as sfp:
                        s_data = json.load(sfp)
                    cached_reset_str = s_data.get("reset_at")
                    if cached_reset_str:
                        s_norm = str(cached_reset_str).strip()
                        if s_norm.endswith("Z"):
                            s_norm = s_norm[:-1] + "+00:00"
                        cached_dt = datetime.fromisoformat(s_norm)
                        period_start_dt, period_end_dt = resolve_period_from_reset_at(
                            cached_dt, reading_dt, period_days, tz
                        )
                        actual_reset_source = "cache"
                        print(
                            f"WARNING: Could not read reset time from {src_label} ({grok_reset_err_reason}); "
                            f"falling back to cached state.json.",
                            file=sys.stderr,
                        )
                except Exception as exc:
                    print(f"Warning: failed to read state cache '{state_path}': {exc}", file=sys.stderr)

            if period_start_dt is None or period_end_dt is None or actual_reset_source is None:
                print(
                    f"WARNING: Could not read reset time from {src_label} ({grok_reset_err_reason}) "
                    f"and no fallback anchor was found.",
                    file=sys.stderr,
                )
                fail(
                    3,
                    "no reset anchor configured"
                    if is_privacy
                    else "no reset anchor: run with Grok Build once, or set reset_anchor in usage_config.json",
                )

    assert period_start_dt is not None and period_end_dt is not None and actual_reset_source is not None

    day_idx = int(math.floor((reading_dt - period_start_dt).total_seconds() / 86400.0)) + 1
    day_idx = max(1, min(period_days, day_idx))

    period_start_str = period_start_dt.strftime("%Y-%m-%d")
    period_end_str = period_end_dt.strftime("%Y-%m-%d")
    reset_hm_str = period_end_dt.strftime("%H:%M")
    reading_date_str = reading_dt.strftime("%Y-%m-%d")
    reading_ymd_compact = reading_dt.strftime("%Y%m%d")
    note_str = reading_dt.strftime("%H:%M")
    pct_str = str(int(pct_val)) if float(pct_val).is_integer() else f"{pct_val:g}"

    if is_privacy:
        default_png = str(data_dir / f"chart-{reading_ymd_compact}.png")
        out_png = Path(resolve(args.out, "privacy_out", "out", default=default_png))
    else:
        out_png = Path(resolve(args.out, "out", default=str(data_dir / "usage-week.png")))

    summary_path = Path(resolve(args.summary, "summary", default=str(data_dir / "latest.json")))
    should_open = (not args.no_open) and bool(resolve(None, "open", default=True))

    # Upsert row into DATA_DIR/week-<start YYYY-MM-DD>.csv atomically (read-modify-write under the write lock)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        csv_path = data_dir / f"week-{period_start_str}.csv"
        with _write_lock():
            rows = []
            if csv_path.exists():
                with csv_path.open(encoding="utf-8", newline="") as fp:
                    rows = list(csv.DictReader(fp))

            new_row = {"day": str(day_idx), "date": reading_date_str, "pct": pct_str, "note": note_str}
            rows = [r for r in rows if int(r["day"]) != day_idx] + [new_row]
            rows.sort(key=lambda r: int(r["day"]))

            lines = ["day,date,pct,note"] + [f"{r['day']},{r['date']},{r['pct']},{r['note']}" for r in rows]
            atomic_write_text(csv_path, "\n".join(lines) + "\n")
    except Exception as exc:
        fail(3, f"Failed to write CSV in '{data_dir}': {exc}")

    # Render dashboard unless --no-render
    if not args.no_render:
        try:
            import chart_hud
            render_cfg = {
                "theme": resolve(args.theme, "theme"),
                "lang": "numeric" if is_privacy else resolved_lang,
                "privacy": is_privacy,
                "pace": pace_val,
                "title": None if is_privacy else resolve(args.title, "title"),
                "tz": tz_for_render,
                "reset_hm": reset_hm_str,
                "bg_color": resolve(args.bg_color, "bg_color"),
                "accent_color": resolve(args.accent_color, "accent_color"),
                "baseline_color": resolve(args.baseline_color, "baseline_color"),
                "under_color": resolve(args.under_color, "under_color"),
                "over_color": resolve(args.over_color, "over_color"),
                "text_color": resolve(args.text_color, "text_color"),
                "palette": resolve(getattr(args, "palette", None), "palette"),
            }
            chart_hud.render(rows, str(out_png), period_start_str, render_cfg)
            # Other palettes for the viewer's palette menu (<data_dir>/pal/<name>/<file>): usage_view.py draws one
            # on demand from pal/render.json; render_all_palettes: true draws all 14 right away instead.
            if not any(render_cfg.get(k) for k in ("baseline_color", "under_color", "over_color")):
                try:
                    (data_dir / "pal").mkdir(parents=True, exist_ok=True)
                    atomic_write_text(data_dir / "pal" / "render.json", json.dumps(
                        {"rows_csv": str(csv_path), "period_start": period_start_str, "out_name": out_png.name,
                         "cfg": {k: v for k, v in render_cfg.items() if k != "palette"}}, ensure_ascii=False))
                except Exception as exc:
                    print(f"Warning: cannot save palette render settings: {exc}", file=sys.stderr)
            if not any(render_cfg.get(k) for k in ("baseline_color", "under_color", "over_color")) \
                    and bool(resolve(None, "render_all_palettes", default=False)):
                import usage_palette
                for pal_name in usage_palette.names():
                    try:
                        pal_png = data_dir / "pal" / pal_name / out_png.name
                        pal_png.parent.mkdir(parents=True, exist_ok=True)
                        chart_hud.render(rows, str(pal_png), period_start_str, dict(render_cfg, palette=pal_name))
                    except Exception as exc:
                        print(f"Warning: palette '{pal_name}' render failed: {exc}", file=sys.stderr)
        except Exception as exc:
            fail(4, f"Render failed: {exc}")

        if should_open:
            open_image_file(out_png)

    baseline_val = round(pace_val * day_idx, 4)
    delta_val = round(pct_val - baseline_val, 4)
    status_str = "ahead" if pct_val > baseline_val else "on_pace"
    avg_per_day = round(pct_val / max(1, day_idx), 2)
    projected_end_pct = round((pct_val / max(1, day_idx)) * period_days, 1)
    remaining_pct = round(max(0.0, 100.0 - pct_val), 4)

    rem_sec = max(0, int((period_end_dt - reading_dt).total_seconds()))
    reset_in_hours = round(rem_sec / 3600.0, 2)
    reset_in_text = format_duration(rem_sec)

    if is_privacy:
        priv_text = (
            f"D{day_idx}/{period_days} {pct_val:g} ({baseline_val:g}, {'▲' if delta_val > 0 else '▼'} {delta_val:+g}) "
            f"proj {projected_end_pct:.0f} next {reset_in_text}"
        )
        summary_payload = {
            "ok": True,
            "start": period_start_str,
            "end": period_end_str,
            "day": day_idx,
            "days": period_days,
            "value": round(pct_val, 4),
            "step": round(pace_val, 4),
            "target": baseline_val,
            "delta": delta_val,
            "trend": "up" if pct_val > baseline_val else "ok",
            "projected": projected_end_pct,
            "left": remaining_pct,
            "avg": avg_per_day,
            "next": period_end_dt.isoformat(timespec="minutes"),
            "next_in_h": reset_in_hours,
            "pct_source": "a" if actual_pct_source == "grok" else "m",
            "reset_source": "a" if actual_reset_source in ("grok", "cache") else "m",
            "updated_at": reading_dt.isoformat(timespec="minutes"),
            "file": None if args.no_render else str(out_png),
            "csv": str(csv_path),
            "text": priv_text,
        }
        out_text = priv_text
    else:
        status_phrase = "ahead of pace" if status_str == "ahead" else "on pace"
        summary_text = (
            f"Day {day_idx}/{period_days}: {pct_val:g}% used "
            f"(baseline {baseline_val:g}%, {'▲' if delta_val > 0 else '▼'} {delta_val:+g}) - {status_phrase}; "
            f"projected {projected_end_pct:.0f}%; resets in {reset_in_text}"
        )
        summary_payload = {
            "ok": True,
            "period_start": period_start_str,
            "period_end": period_end_str,
            "day": day_idx,
            "days_total": period_days,
            "pct": round(pct_val, 4),
            "pct_source": actual_pct_source,
            "pace": round(pace_val, 4),
            "baseline": baseline_val,
            "delta": delta_val,
            "status": status_str,
            "projected_end_pct": projected_end_pct,
            "remaining_pct": remaining_pct,
            "avg_per_day": avg_per_day,
            "reset_at": period_end_dt.isoformat(timespec="minutes"),
            "reset_in_hours": reset_in_hours,
            "reset_in_text": reset_in_text,
            "reset_source": actual_reset_source,
            "recorded_at": reading_dt.isoformat(timespec="minutes"),
            "csv": str(csv_path),
            "image": None if args.no_render else str(out_png),
            "summary_text": summary_text,
        }
        out_text = summary_text

    if getattr(args, "date", None) is None and getattr(args, "time", None) is None:  # live reading, not a backfill
        _ledger_hook(data_dir, cfg_file_data, tz, "numeric" if is_privacy else str(resolved_lang or "en"), pct_val,
                     grok_reset_dt if actual_reset_source == "grok" else None,
                     "record" if actual_pct_source == "grok" else "manual")

    try:
        atomic_write_text(summary_path, json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:
        print(f"Warning: failed to write summary file '{summary_path}': {exc}", file=sys.stderr)

    if args.format == "text":
        sys.stdout.write(out_text + "\n")
        sys.stdout.flush()
    else:
        emit(summary_payload)
    sys.exit(0)


if __name__ == "__main__":
    main()
