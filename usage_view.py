"""
usage_view.py - Lightweight local web viewer for the HUD chart and summary JSON.

Uses Python standard library only (`http.server` + `threading`).
Binds to `127.0.0.1` ONLY by default (refuses non-loopback `--host` unless `--allow-remote` is given).

USAGE
-----
    python3 usage_view.py [--config usage_config.json] [--host 127.0.0.1] [--port 0] \
        [--no-open] [--privacy] [--refresh-mode auto|manual] [--refresh-interval 30] \
        [--pct-source auto|manual] [--reset-source auto|manual]

    # Or via usage_record.py convenience subcommand:
    python3 usage_record.py serve [--port 0] [--privacy]

ROUTES
------
- `GET /`            : HTML viewer (inline CSS/JS, zero external dependencies/CDNs)
- `GET /image`       : Current rendered PNG (`Cache-Control: no-store`)
- `GET /api/latest`  : Current `latest.json` + refresh status (`next_refresh_at`, etc.)
- `GET /api/status`  : Refresh state (`refreshing`, `cooldown_remaining`, `next_refresh_at`)
- `POST /api/refresh`: Trigger one record/render cycle via `usage_record.py` subprocess.
  Needs the `X-Usage-Token` header (fresh random token per start, written to
  `<data_dir>/view_server.token`, mode 600, and embedded in the page). `GET /api/refresh` is refused (405).
  A `pct` override (query, JSON or form) is only accepted with `--debug-pct-override` or
  `"debug_pct_override": true` in the config.
- Every request must carry a loopback `Host` header (DNS-rebinding guard); writes with a
  cross-site `Origin`/`Referer` are refused.
"""

import argparse, datetime as dt_mod, html, ipaddress, json, math, os, subprocess, sys, threading, time, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from usage_record import resolve_refresh_settings

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_guard  # noqa: E402
import usage_palette  # noqa: E402
import usage_offset  # noqa: E402
import usage_dates  # noqa: E402

# Manual refresh cooldown; overridden by config "manual_refresh_cooldown_seconds" (default 60).
COOLDOWN_SECONDS = 60.0


def emit_json_line(payload: Dict[str, Any]):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fail(code: int, msg: str):
    emit_json_line({"ok": False, "error": str(msg)})
    sys.exit(code)


def is_loopback_host(host: str) -> bool:
    h = host.strip().lower()
    if h in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return True
    try:
        addr = ipaddress.ip_address(h.strip("[]"))
        return bool(addr.is_loopback)
    except ValueError:
        return False


def can_open_browser() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class ViewerState:
    def __init__(
        self,
        args: argparse.Namespace,
        cfg_data: Dict[str, Any],
        data_dir: Path,
        summary_path: Path,
        explicit_out: Optional[Path],
        is_privacy: bool,
        refresh_mode: str,
        refresh_interval_minutes: int,
        pass_through_flags: List[str],
    ):
        self.args = args
        self.cfg_data = cfg_data
        self.data_dir = data_dir
        self.summary_path = summary_path
        self.explicit_out = explicit_out
        self.is_privacy = is_privacy
        self.refresh_mode = refresh_mode
        self.refresh_interval_minutes = refresh_interval_minutes
        self.pass_through_flags = pass_through_flags
        try:
            self.cooldown_seconds = float(cfg_data.get("manual_refresh_cooldown_seconds", COOLDOWN_SECONDS))
        except (TypeError, ValueError):
            self.cooldown_seconds = COOLDOWN_SECONDS
        self.debug_pct = bool(getattr(args, "debug_pct_override", False)) or cfg_data.get("debug_pct_override") is True
        self.palette = usage_palette.resolve(getattr(args, "palette", None) or cfg_data.get("palette"), cfg_data.get("custom_colors"))
        self.token = local_guard.new_token()
        self.extra_hosts: List[str] = [str(h) for h in (cfg_data.get("view_allowed_hosts") or [])]

        self.refresh_lock = threading.Lock()
        self.meta_lock = threading.Lock()
        self.is_refreshing = False
        self.last_manual_refresh_end: float = 0.0
        self.next_refresh_epoch: Optional[float] = (
            time.time() + self.refresh_interval_minutes * 60.0
            if self.refresh_mode == "auto"
            else None
        )
        self.stop_event = threading.Event()

    def get_cooldown_remaining(self) -> int:
        with self.meta_lock:
            if self.last_manual_refresh_end <= 0.0:
                return 0
            elapsed = time.monotonic() - self.last_manual_refresh_end
            if elapsed >= self.cooldown_seconds:
                return 0
            return max(1, int(math.ceil(self.cooldown_seconds - elapsed)))

    def get_next_refresh_iso(self) -> Optional[str]:
        with self.meta_lock:
            if self.refresh_mode != "auto" or self.next_refresh_epoch is None:
                return None
            dt_obj = dt_mod.datetime.fromtimestamp(self.next_refresh_epoch, tz=dt_mod.timezone.utc).astimezone()
            return dt_obj.isoformat(timespec="seconds")

    def get_next_refresh_in_sec(self) -> Optional[int]:
        with self.meta_lock:
            if self.refresh_mode != "auto" or self.next_refresh_epoch is None:
                return None
            return max(0, int(round(self.next_refresh_epoch - time.time())))

    def resolve_current_image_path(self) -> Optional[Path]:
        # 1. Check latest.json
        latest = self.read_latest_raw()
        if isinstance(latest, dict):
            img_str = latest.get("file") if self.is_privacy else (latest.get("image") or latest.get("file"))
            if img_str:
                cand = Path(str(img_str))
                if cand.is_file():
                    return cand

        # 2. Check explicit --out / config out
        if self.explicit_out and self.explicit_out.is_file():
            return self.explicit_out

        # 3. Check default file(s) in data_dir
        if self.is_privacy:
            chart_files = sorted(
                self.data_dir.glob("chart-*.png"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                reverse=True,
            )
            if chart_files:
                return chart_files[0]
        else:
            default_wk = self.data_dir / "usage-week.png"
            if default_wk.is_file():
                return default_wk

        any_pngs = sorted(
            self.data_dir.glob("*.png"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        return any_pngs[0] if any_pngs else None

    def read_latest_raw(self) -> Dict[str, Any]:
        if self.summary_path.is_file():
            try:
                with self.summary_path.open(encoding="utf-8") as fp:
                    data = json.load(fp)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def build_latest_payload(self) -> Dict[str, Any]:
        raw = dict(self.read_latest_raw())
        if self.is_privacy:
            # Ensure privacy payload uses generic keys and never exposes service names
            sanitized: Dict[str, Any] = {"ok": raw.get("ok", True)}
            start_v = raw.get("start") or raw.get("period_start")
            end_v = raw.get("end") or raw.get("period_end")
            if start_v is not None:
                sanitized["start"] = start_v
            if end_v is not None:
                sanitized["end"] = end_v
            for priv_k, std_k in (
                ("day", "day"),
                ("days", "days_total"),
                ("value", "pct"),
                ("step", "pace"),
                ("target", "baseline"),
                ("delta", "delta"),
                ("projected", "projected_end_pct"),
                ("left", "remaining_pct"),
                ("avg", "avg_per_day"),
                ("next", "reset_at"),
                ("next_in_h", "reset_in_hours"),
                ("updated_at", "recorded_at"),
                ("file", "image"),
                ("csv", "csv"),
                ("text", "summary_text"),
            ):
                val = raw.get(priv_k, raw.get(std_k))
                if val is not None:
                    sanitized[priv_k] = val
            mw = raw.get("mw") or raw.get("midweek_reset")
            if isinstance(mw, dict):  # mid-week reset: only the two numbers the cards need, no times
                sanitized["mw"] = {"days": mw.get("days", mw.get("days_since")), "left": mw.get("left", mw.get("days_left"))}
            if "trend" in raw:
                sanitized["trend"] = raw["trend"]
            elif "status" in raw:
                sanitized["trend"] = "up" if raw["status"] == "ahead" else "ok"
            raw = sanitized

        img_path = self.resolve_current_image_path()
        img_mtime = int(img_path.stat().st_mtime * 1000) if (img_path and img_path.is_file()) else None

        raw.setdefault("ok", True)
        if not raw.get("updated_at") and raw.get("recorded_at"):
            raw["updated_at"] = raw["recorded_at"]
        raw["refresh_mode"] = self.refresh_mode
        raw["refresh_interval_minutes"] = self.refresh_interval_minutes
        raw["next_refresh_at"] = self.get_next_refresh_iso()
        raw["next_refresh_in_sec"] = self.get_next_refresh_in_sec()
        raw["refreshing"] = self.is_refreshing
        raw["cooldown_remaining"] = self.get_cooldown_remaining()
        raw["cooldown_seconds"] = int(self.cooldown_seconds)
        raw["image_mtime"] = img_mtime
        return raw

    def run_record_cycle(
        self,
        manual: bool = False,
        pct_override: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Execute one record cycle via `usage_record.py` subprocess.
        Returns (http_status_code, json_payload).
        """
        if not self.refresh_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "busy"}

        try:
            if manual:
                cd_rem = self.get_cooldown_remaining()
                if cd_rem > 0:
                    return 429, {"ok": False, "error": "cooldown", "retry_in": cd_rem}

            with self.meta_lock:
                self.is_refreshing = True

            record_script = Path(__file__).resolve().parent / "usage_record.py"
            cmd = [sys.executable, str(record_script), "--no-open", "--format", "json"]
            cmd.extend(self.pass_through_flags)
            if pct_override is not None and str(pct_override).strip() != "":
                cmd.extend(["--pct", str(pct_override).strip()])

            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            stdout_str = (proc.stdout or "").strip()
            stderr_str = (proc.stderr or "").strip()
            if stderr_str and not self.is_privacy:
                sys.stderr.write(stderr_str + "\n")

            parsed: Optional[Dict[str, Any]] = None
            if stdout_str:
                last_line = stdout_str.splitlines()[-1].strip()
                try:
                    obj = json.loads(last_line)
                    if isinstance(obj, dict):
                        parsed = obj
                except Exception:
                    pass

            if parsed is None:
                err_msg = "source read failed" if self.is_privacy else (stderr_str or f"usage_record exited with code {proc.returncode}")
                parsed = {"ok": False, "error": err_msg}

            if manual and parsed.get("ok"):
                with self.meta_lock:
                    self.last_manual_refresh_end = time.monotonic()

            status_code = 200 if parsed.get("ok") else (400 if proc.returncode == 2 else 500)
            if parsed.get("ok"):
                parsed["refresh_mode"] = self.refresh_mode
                parsed["refresh_interval_minutes"] = self.refresh_interval_minutes
                parsed["next_refresh_at"] = self.get_next_refresh_iso()
                parsed["next_refresh_in_sec"] = self.get_next_refresh_in_sec()
                parsed["cooldown_remaining"] = self.get_cooldown_remaining()
            return status_code, parsed
        finally:
            with self.meta_lock:
                self.is_refreshing = False
            self.refresh_lock.release()


_PAL_LOCK = threading.Lock()


def render_palette_image(data_dir: Path, name: str, img_path: Path) -> Optional[Path]:
    """<data_dir>/pal/<name>/<image>: the current chart in another palette, drawn on demand from the settings
    usage_record.py saved in pal/render.json and kept until the main chart changes."""
    alt = data_dir / "pal" / name / img_path.name
    with _PAL_LOCK:
        try:
            if alt.is_file() and alt.stat().st_mtime >= img_path.stat().st_mtime:
                return alt
            spec = json.loads((data_dir / "pal" / "render.json").read_text(encoding="utf-8"))
            if spec.get("out_name") != img_path.name:
                return None
            import csv
            import chart_hud
            with open(spec["rows_csv"], encoding="utf-8", newline="") as fp:
                rows = [r for r in csv.DictReader(fp) if r.get("day")]
            alt.parent.mkdir(parents=True, exist_ok=True)
            tmp = alt.with_name(alt.stem + ".tmp.png")
            chart_hud.render(rows, str(tmp), spec["period_start"], dict(spec.get("cfg") or {}, palette=name))
            os.replace(tmp, alt)
            return alt
        except Exception as exc:
            print(f"Warning: palette '{name}' image not drawn: {exc}", file=sys.stderr)
            return None


def render_html_page(state: ViewerState) -> str:
    priv = state.is_privacy
    latest = state.build_latest_payload()
    if priv:
        s_date = str(latest.get("start") or "")[-5:]
        e_date = str(latest.get("end") or "")[-5:]
        page_title = f"{s_date} → {e_date}" if (s_date and e_date) else "Chart"
        header_title = page_title
        btn_label = "↻"
        lbl_updated = "⏱"
    else:
        page_title = html.escape(str(state.cfg_data.get("title") or "Grok Bot Usage & Check-in"))
        header_title = page_title
        btn_label = "↻ Refresh"
        lbl_updated = "Last updated:"

    initial_json = json.dumps(latest, ensure_ascii=False).replace("<", "\\u003c")
    is_priv_js = "true" if priv else "false"
    pal_now = usage_palette.get(state.palette)
    table = usage_palette.js_table()
    if state.palette not in table:  # configured custom / legacy palette: not in the menu list, still the page default
        table[state.palette] = {v: usage_palette.get(state.palette, v == "light") for v in ("dark", "light")}
    pal_json = json.dumps({"default": state.palette, "table": table, "alias": {"colorblind": "orange-deepblue"},
                           "patterns": bool(state.cfg_data.get("patterns"))})
    offset = usage_offset.from_settings(state.cfg_data, state.data_dir)
    rd_js = usage_dates.JS
    off_json = json.dumps({"off": offset, "tag": usage_offset.tag(offset)})
    pal_opts = "".join(
        f'<option value="{n}"{" selected" if n == state.palette else ""}>{(str(i + 1) + (" " + usage_palette.STAR if usage_palette.cvd_safe(n) else "") if priv else usage_palette.label(n, "en"))}</option>'
        for i, n in enumerate(usage_palette.names())) + (
        "" if state.palette in usage_palette.names() else
        f'<option value="{state.palette}" selected>{"✎" if priv else usage_palette.label(state.palette, "en")}</option>')
    pal_lbl = "◐" if priv else "Colours"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{local_guard.token_meta(state.token)}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<style>
  :root {{
    --bg: #05070a;
    --card: #0c1017;
    --border: {usage_palette.blend("#05070a", pal_now["under"], 0.25)};
    --accent: {pal_now["under"]};
    --text: #e8f4ff;
    --dim: #8ba0b5;
    --over: {pal_now['over']};
    --under: {pal_now['under']};
    --baseline: {pal_now['baseline']};
    --notice: {pal_now['notice']};
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans CJK TC", "Noto Sans CJK SC", sans-serif;
    min-height: 100vh;
    padding: 18px 22px 32px;
  }}
  .container {{
    max-width: 1360px;
    margin: 0 auto;
  }}
  .topbar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 18px;
    background: var(--card);
    border: 1px solid var(--border);
    border-top: 2px solid var(--accent);
    border-radius: 12px;
    margin-bottom: 16px;
  }}
  .title-wrap {{
    display: flex;
    align-items: baseline;
    gap: 14px;
  }}
  .title {{
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }}
  .meta {{
    font-size: 0.86rem;
    color: var(--dim);
  }}
  .controls {{
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .countdown {{
    font-size: 0.85rem;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
  }}
  button#refresh-btn {{
    background: rgba(127, 231, 255, 0.12);
    color: var(--text);
    border: 1px solid var(--accent);
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 0.92rem;
    font-weight: 600;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    transition: background 0.15s, opacity 0.15s;
  }}
  button#refresh-btn:hover:not(:disabled) {{
    background: rgba(127, 231, 255, 0.24);
  }}
  button#refresh-btn:disabled {{
    opacity: 0.55;
    cursor: not-allowed;
    border-color: var(--border);
  }}
  .spinner {{
    width: 14px;
    height: 14px;
    border: 2px solid var(--dim);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.75s linear infinite;
    display: none;
  }}
  @keyframes spin {{
    to {{ transform: rotate(360deg); }}
  }}
  .stats-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
  }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
  }}
  .stat-label {{
    font-size: 0.78rem;
    color: var(--dim);
    font-weight: 600;
    margin-bottom: 4px;
  }}
  .otag {{ font-size: 0.55em; font-weight: 400; opacity: 0.7; }}
  .stat-value {{
    font-size: 1.32rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }}
  .val-under {{ color: var(--under); }}
  .val-over {{ color: var(--over); }}
  html[data-patterns="1"] .val-over:not(.arr)::before {{ content: "\25B2  "; }}
  html[data-patterns="1"] .val-under:not(.arr)::before {{ content: "\25BC  "; }}
  html[data-patterns="1"] .stat-card:has(.val-over) {{ background-image: repeating-linear-gradient(135deg, transparent 0 6px, color-mix(in srgb, var(--over) 14%, transparent) 6px 9px); }}
  .val-base {{ color: var(--baseline); }}
  .pal {{ color: var(--dim); font-size: 0.8rem; }}
  .pal select {{ background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 3px 6px; }}
  .val-neutral {{ color: var(--text); }}
  .img-frame {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 10px;
    text-align: center;
    overflow: hidden;
  }}
  .img-frame img {{
    width: 100%;
    max-width: 1320px;
    height: auto;
    display: block;
    margin: 0 auto;
    border-radius: 8px;
  }}
  .status-banner {{
    font-size: 0.85rem;
    color: var(--notice);
    margin-bottom: 10px;
    min-height: 1.2em;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="title-wrap">
      <div class="title" id="hdr-title">{header_title}</div>
      <div class="meta"><span id="lbl-updated">{lbl_updated}</span> <span id="val-updated">--</span></div>
    </div>
    <div class="controls">
      <div class="countdown" id="auto-countdown"></div>
      <label class="pal">{pal_lbl} <select id="pal-sel"{"" if priv else ' title="' + usage_palette.STAR_NOTE["en"].replace("&", "&amp;").replace('"', "&quot;") + '"'}>{pal_opts}</select></label>
      <button id="refresh-btn" type="button">
        <span class="spinner" id="btn-spinner"></span>
        <span id="btn-text">{btn_label}</span>
      </button>
    </div>
  </div>
  <div class="status-banner" id="status-msg"></div>
  <div class="stats-row" id="stats-grid"></div>
  <div class="img-frame">
    <img id="hud-img" src="/image?ts={int(time.time() * 1000)}" alt="Chart">
  </div>
</div>
<script>
(function() {{
  const IS_PRIV = {is_priv_js};
  const TOKEN = (document.querySelector('meta[name="usage-token"]') || {{}}).content || "";
  const PAL = {pal_json};
  {rd_js}
  // next reset as an explicit local date (exactly the time in latest.json, e.g. /usage 'Resets:')
  function resetDate(iso) {{
    if (!iso) return "";
    const d = new Date(iso); if (isNaN(d)) return "";
    const m = /([+-])(\\d\\d):(\\d\\d)$/.exec(iso);
    const w = m ? new Date(d.getTime() + (m[1] == "-" ? -1 : 1) * (+m[2] * 60 + +m[3]) * 60000)
                : new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    return RD(w, IS_PRIV ? "numeric" : "en");
  }}
  const OFFS = {off_json};  // display offset: added to the shown weekly % (latest.json stays raw)
  let palette = PAL.default;
  try {{ palette = localStorage.getItem("usage.palette") || PAL.default; }} catch (e) {{}}
  palette = PAL.alias[palette] || palette;
  if (!PAL.table[palette]) palette = PAL.default;
  function mix(a, b, t) {{
    const x = [1, 3, 5].map(function(i) {{ return parseInt(a.substr(i, 2), 16); }}), y = [1, 3, 5].map(function(i) {{ return parseInt(b.substr(i, 2), 16); }});
    return "#" + x.map(function(v, i) {{ return ("0" + Math.round(v + (y[i] - v) * t).toString(16)).slice(-2); }}).join("");
  }}
  function applyPalette() {{
    const c = PAL.table[palette].dark, r = document.documentElement.style;
    ["over", "under", "baseline", "notice"].forEach(function(k) {{ r.setProperty("--" + k, c[k]); }});
    r.setProperty("--accent", c.under); r.setProperty("--border", mix("#05070a", c.under, 0.25));
    document.documentElement.dataset.patterns = PAL.patterns ? "1" : "";
    const sel = document.getElementById("pal-sel");
    if (sel) sel.value = palette;
  }}
  const BASE_BTN = {json.dumps(btn_label)};
  let state = {initial_json};
  let cooldownLeft = state.cooldown_remaining || 0;
  let nextRefreshSec = state.next_refresh_in_sec;
  let lastUpdatedKey = state.updated_at || state.recorded_at || "";
  let lastImgMtime = state.image_mtime || 0;
  let isBusy = !!state.refreshing;

  const btn = document.getElementById("refresh-btn");
  const btnText = document.getElementById("btn-text");
  const spinner = document.getElementById("btn-spinner");
  const updatedEl = document.getElementById("val-updated");
  const autoCdEl = document.getElementById("auto-countdown");
  const statsGrid = document.getElementById("stats-grid");
  const hudImg = document.getElementById("hud-img");
  const statusMsg = document.getElementById("status-msg");
  const hdrTitle = document.getElementById("hdr-title");

  function fmtTimer(sec) {{
    const s = Math.max(0, Math.floor(sec));
    if (s >= 3600) {{
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      return h + "h " + String(m).padStart(2, "0") + "m";
    }}
    const m = Math.floor(s / 60);
    const r = s % 60;
    return String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
  }}

  function fmtDurationSec(sec) {{
    const s = Math.max(0, Math.floor(sec));
    if (s <= 0) return "0m";
    if (s >= 86400) {{
      return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
    }}
    if (s >= 3600) {{
      return Math.floor(s / 3600) + "h " + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
    }}
    return Math.max(1, Math.floor(s / 60)) + "m";
  }}

  function reloadImage() {{
    hudImg.src = "/image?palette=" + encodeURIComponent(palette) + "&ts=" + Date.now();
  }}

  function renderStats(d) {{
    const upd = d.updated_at || d.recorded_at || "--";
    updatedEl.textContent = upd;

    if (IS_PRIV && d.start && d.end) {{
      const t = String(d.start).slice(-5) + " \\u2192 " + String(d.end).slice(-5);
      hdrTitle.textContent = t;
      document.title = t;
    }}

    const day = d.day != null ? d.day : "--";
    const days = (d.days != null ? d.days : d.days_total) || 7;
    const r1 = function(v) {{ return Math.round(v * 10) / 10; }};
    const rawPct = d.value != null ? d.value : d.pct;
    const base = d.target != null ? d.target : d.baseline;
    const pct = rawPct != null ? r1(Math.max(0, +rawPct + OFFS.off)) : rawPct;
    const sh = (rawPct != null && pct != null) ? pct - rawPct : 0;  // what the offset actually added (0 floor)
    const dayN = +(d.day != null ? d.day : 0) || 1;
    const delta = d.delta != null ? r1(+d.delta + sh) : d.delta;
    const rem0 = d.left != null ? d.left : d.remaining_pct;
    const rem = (rem0 != null && pct != null) ? r1(Math.max(0, 100 - pct)) : rem0;
    const avg0 = d.avg != null ? d.avg : d.avg_per_day;
    // after a mid-week reset the average counts from the reset (days since it) and the projection is
    // pct + avg x days left, so the display offset scales by those instead of the day index
    const mw = d.mw || d.midweek_reset || null;
    const mwDays = mw ? Math.max(1 / 24, +(mw.days != null ? mw.days : mw.days_since) || 0) : null;
    const mwLeft = mw ? Math.max(0, +(mw.left != null ? mw.left : mw.days_left) || 0) : null;
    const mwTag = mw ? (IS_PRIV ? " \u21ba" : " (since mid-week reset)") : "";
    const avg = avg0 != null ? r1(+avg0 + sh / (mw ? mwDays : dayN)) : avg0;
    const proj0 = d.projected != null ? d.projected : d.projected_end_pct;
    const pace = (d.step != null ? d.step : d.pace) || 14;
    const proj = proj0 != null ? r1(+proj0 + sh * (mw ? (1 + mwLeft / mwDays) : ((d.days != null ? d.days : d.days_total) || 7) / dayN)) : proj0;
    const otag = OFFS.tag ? ' <span class="otag">' + OFFS.tag + '</span>' : '';
    const over = delta != null ? delta > 0 : false;
    const projOver = proj != null ? proj > (pace * days) : false;
    const nextH = d.next_in_h != null ? d.next_in_h : d.reset_in_hours;
    const nextTxt = d.reset_in_text || (nextH != null ? fmtDurationSec(nextH * 3600) : "--");
    const privNextTxt0 = (nextTxt && nextTxt !== "--" && !String(nextTxt).startsWith("\\u21bb")) ? ("\\u21bb " + nextTxt) : nextTxt;
    const nextDate = resetDate(d.next != null ? d.next : d.reset_at);
    const privNextTxt = privNextTxt0 + (nextDate ? ' <span class="otag">@ ' + nextDate + '</span>' : '');
    const nextFull = nextTxt + (nextDate ? ' <span class="otag">' + nextDate + '</span>' : '');

    const cards = IS_PRIV ? [
      {{ lbl: "D", val: day + "/" + days, cls: "val-neutral" }},
      {{ lbl: "\\u2211 %", val: pct != null ? pct + "%" + otag : "--", cls: over ? "val-over" : "val-under" }},
      {{ lbl: "\\u0394", val: delta != null ? arrow(delta) + " " + (delta >= 0 ? "+" + delta : delta) + "%" : "--", cls: (over ? "val-over" : "val-under") + " arr" }},
      {{ lbl: "100% \\u2212", val: rem != null ? rem + "%" : "--", cls: (pct > 100) ? "val-over" : "val-under" }},
      {{ lbl: "\\u00d8" + mwTag, val: avg != null ? avg + "%" : "--", cls: (avg > pace) ? "val-over" : "val-under" }},
      {{ lbl: "\\u2192 7/7" + mwTag, val: proj != null ? proj + "%" : "--", cls: projOver ? "val-over" : "val-under" }},
      {{ lbl: "\\u21bb", val: privNextTxt, cls: "val-neutral" }}
    ] : [
      {{ lbl: "Day", val: day + " / " + days, cls: "val-neutral" }},
      {{ lbl: "Cumulative", val: pct != null ? pct + "%" + otag : "--", cls: over ? "val-over" : "val-under" }},
      {{ lbl: "vs Baseline (" + (base != null ? base + "%" : "--") + ")", val: delta != null ? arrow(delta) + " " + (delta >= 0 ? "+" + delta : delta) + "%" : "--", cls: (over ? "val-over" : "val-under") + " arr" }},
      {{ lbl: "Remaining", val: rem != null ? rem + "%" : "--", cls: (pct > 100) ? "val-over" : "val-under" }},
      {{ lbl: "Avg / Day" + mwTag, val: avg != null ? avg + "%" : "--", cls: (avg > pace) ? "val-over" : "val-under" }},
      {{ lbl: "Projected (7d)" + mwTag, val: proj != null ? proj + "%" : "--", cls: projOver ? "val-over" : "val-under" }},
      {{ lbl: "Reset In", val: nextFull, cls: "val-neutral" }}
    ];

    function arrow(v) {{ return v > 0 ? "\\u25b2" : "\\u25bc"; }}
    statsGrid.innerHTML = cards.map(function(c) {{
      return '<div class="stat-card"><div class="stat-label">' + c.lbl + '</div><div class="stat-value ' + c.cls + '">' + c.val + '</div></div>';
    }}).join("");
  }}

  function updateControls() {{
    if (isBusy) {{
      btn.disabled = true;
      spinner.style.display = "inline-block";
      btnText.textContent = IS_PRIV ? "..." : "Refreshing...";
    }} else if (cooldownLeft > 0) {{
      btn.disabled = true;
      spinner.style.display = "none";
      btnText.textContent = BASE_BTN + " (" + cooldownLeft + "s)";
    }} else {{
      btn.disabled = false;
      spinner.style.display = "none";
      btnText.textContent = BASE_BTN;
    }}

    if (state.refresh_mode === "auto" && nextRefreshSec != null) {{
      autoCdEl.textContent = IS_PRIV
        ? ("\\u21bb " + fmtTimer(nextRefreshSec))
        : ("next auto refresh in " + fmtTimer(nextRefreshSec));
    }} else {{
      autoCdEl.textContent = "";
    }}
  }}

  async function pollLatest() {{
    try {{
      const res = await fetch("/api/latest", {{ cache: "no-store" }});
      if (!res.ok) return;
      const d = await res.json();
      state = d;
      isBusy = !!d.refreshing;
      if (d.cooldown_remaining != null) cooldownLeft = d.cooldown_remaining;
      if (d.next_refresh_in_sec != null) nextRefreshSec = d.next_refresh_in_sec;
      renderStats(d);
      updateControls();

      const newKey = d.updated_at || d.recorded_at || "";
      const newMtime = d.image_mtime || 0;
      if ((newKey && newKey !== lastUpdatedKey) || (newMtime && newMtime !== lastImgMtime)) {{
        lastUpdatedKey = newKey;
        lastImgMtime = newMtime;
        reloadImage();
      }}
    }} catch (e) {{}}
  }}

  btn.addEventListener("click", async function() {{
    if (isBusy || cooldownLeft > 0) return;
    isBusy = true;
    statusMsg.textContent = "";
    updateControls();
    try {{
      const res = await fetch("/api/refresh", {{ method: "POST", headers: {{ "X-Usage-Token": TOKEN }} }});
      const d = await res.json();
      if (res.status === 429 && d.retry_in) {{
        cooldownLeft = d.retry_in;
      }} else if (res.status === 409) {{
        statusMsg.textContent = IS_PRIV ? "409" : "Refresh already in progress...";
      }} else if (!res.ok || !d.ok) {{
        statusMsg.textContent = d.error || "Error";
      }} else {{
        cooldownLeft = d.cooldown_remaining || state.cooldown_seconds || 60;
        await pollLatest();
        reloadImage();
      }}
    }} catch (e) {{
      statusMsg.textContent = "Request failed";
    }} finally {{
      isBusy = false;
      updateControls();
    }}
  }});

  setInterval(function() {{
    if (cooldownLeft > 0) {{
      cooldownLeft = Math.max(0, cooldownLeft - 1);
    }}
    if (state.refresh_mode === "auto" && nextRefreshSec != null) {{
      if (nextRefreshSec > 0) {{
        nextRefreshSec -= 1;
      }} else {{
        pollLatest();
      }}
    }}
    updateControls();
  }}, 1000);

  setInterval(pollLatest, 60000);

  const palSel = document.getElementById("pal-sel");
  // fetch + decode the new chart first, then switch colours and image together
  let palSeq = 0;
  function switchPalette(n, save) {{
    const my = ++palSeq;
    fetch("/image?palette=" + encodeURIComponent(n) + "&ts=" + Date.now(), {{ cache: "no-store" }})
      .then(function(r) {{ if (!r.ok) throw 0; return r.blob(); }})
      .then(function(b) {{
        const u = URL.createObjectURL(b), im = new Image();
        im.onload = function() {{
          if (my !== palSeq) return;
          palette = n;
          if (save) {{ try {{ localStorage.setItem("usage.palette", n); }} catch (e) {{}} }}
          applyPalette(); hudImg.src = u;
        }};
        im.src = u;
      }}).catch(function() {{ if (my === palSeq && palSel) palSel.value = palette; }});
  }}
  if (palSel) palSel.addEventListener("change", function() {{ switchPalette(palSel.value, true); }});
  const wanted = palette; palette = PAL.default;
  applyPalette();
  if (wanted !== PAL.default) switchPalette(wanted, false);
  renderStats(state);
  updateControls();
}})();
</script>
</body>
</html>
"""


MAX_BODY = 1024 * 1024  # request bodies here are a few bytes (pct); anything above this is refused with 413


def make_handler(state: ViewerState):
    class ViewerRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any):
            return

        def _security_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

        def _send_json(self, code: int, payload: Dict[str, Any]):
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, code: int, html: str):
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            if local_guard.host_allowed(self.headers.get("Host"), self.server.server_address[1], state.extra_hosts):
                return True
            self._send_json(421, {"ok": False, "error": "unexpected Host header"})
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            parsed_url = urllib.parse.urlparse(self.path)
            route = parsed_url.path
            qs = urllib.parse.parse_qs(parsed_url.query)

            if route == "/":
                self._send_html(200, render_html_page(state))
                return

            if route == "/image":
                img_path = state.resolve_current_image_path()
                want = (qs.get("palette") or [""])[0]
                if img_path is not None and img_path.is_file() and want in usage_palette.names() and want != state.palette:
                    alt = render_palette_image(state.data_dir, want, img_path)  # drawn on first request, then cached
                    if alt is not None:
                        img_path = alt
                if img_path is None or not img_path.is_file():
                    self._send_json(404, {"ok": False, "error": "image not found"})
                    return
                try:
                    data = img_path.read_bytes()
                except Exception:
                    self._send_json(500, {"ok": False, "error": "failed to read image"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            if route in ("/api/latest", "/api/status"):
                self._send_json(200, state.build_latest_payload())
                return

            if route == "/api/refresh":  # writes are POST-only
                self.send_response(405)
                body = b'{"ok": false, "error": "use POST with the X-Usage-Token header"}\n'
                self.send_header("Allow", "POST")
                self._security_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return
            parsed_url = urllib.parse.urlparse(self.path)
            route = parsed_url.path
            qs = urllib.parse.parse_qs(parsed_url.query)

            if route == "/api/refresh":
                ok, code, why = local_guard.check_write(self.headers, self.server.server_address[1], state.token, state.extra_hosts)
                if not ok:
                    self._send_json(code, {"ok": False, "error": why})
                    return
                pct_val = qs.get("pct", [None])[0]
                try:
                    content_len = local_guard.content_length(self.headers)
                except ValueError:
                    self.close_connection = True
                    self._send_json(400, {"ok": False, "error": "bad Content-Length"})
                    return
                if content_len > MAX_BODY:
                    self.close_connection = True
                    self._send_json(413, {"ok": False, "error": "request body too large"})
                    return
                if content_len > 0:
                    raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
                    ctype = (self.headers.get("Content-Type") or "").lower()
                    if "application/json" in ctype:
                        try:
                            b_obj = json.loads(raw_body)
                            if isinstance(b_obj, dict) and b_obj.get("pct") is not None:
                                pct_val = str(b_obj["pct"])
                        except Exception:
                            pass
                    else:
                        form_qs = urllib.parse.parse_qs(raw_body)
                        if "pct" in form_qs and form_qs["pct"]:
                            pct_val = form_qs["pct"][0]

                if pct_val not in (None, "") and not state.debug_pct:
                    self._send_json(400, {"ok": False, "error": "pct override is disabled (start with --debug-pct-override)"})
                    return
                code, payload = state.run_record_cycle(manual=True, pct_override=pct_val)
                self._send_json(code, payload)
                return

            self._send_json(404, {"ok": False, "error": "not found"})

    return ViewerRequestHandler


def _auto_refresh_worker(state: ViewerState):
    interval_sec = max(300.0, state.refresh_interval_minutes * 60.0)
    while not state.stop_event.is_set():
        with state.meta_lock:
            state.next_refresh_epoch = time.time() + interval_sec
        if state.stop_event.wait(timeout=interval_sec):
            break
        # Auto timer is NOT subject to the manual button cooldown
        state.run_record_cycle(manual=False, pct_override=None)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local HTTP viewer for the weekly usage HUD dashboard.")
    p.add_argument("--config", default=None, help="Path to JSON config file (default: usage_config.json if present)")
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1; non-loopback requires --allow-remote)")
    p.add_argument("--port", type=int, default=0, help="Bind port (default: 0 for random free port)")
    p.add_argument("--allow-remote", action="store_true", help="Allow binding to a non-loopback --host")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open browser on startup")
    p.add_argument("--privacy", action="store_true", default=None, help="Discreet privacy mode")
    p.add_argument("--refresh-mode", choices=["auto", "manual"], default=None, help="Refresh mode: auto (default) or manual")
    p.add_argument("--refresh-interval", default=None, help="Auto-refresh interval in minutes (default: 30, min: 5)")
    # Pass-through arguments to usage_record.py
    p.add_argument("--pct-source", choices=["auto", "manual"], default=None)
    p.add_argument("--reset-source", choices=["auto", "manual"], default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--reset-anchor", default=None)
    p.add_argument("--data-dir", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--tz", default=None)
    p.add_argument("--period-days", default=None)
    p.add_argument("--pace", default=None)
    p.add_argument("--theme", default=None)
    p.add_argument("--lang", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--bg-color", default=None)
    p.add_argument("--accent-color", default=None)
    p.add_argument("--baseline-color", default=None)
    p.add_argument("--under-color", default=None)
    p.add_argument("--over-color", default=None)
    p.add_argument("--text-color", default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--palette", default=None, help="Colour preset (22, see README): orange-teal (default), orange-blue, ..., teal-mono, ...; or custom (config custom_colors)")
    p.add_argument("--debug-pct-override", action="store_true",
                   help="Testing only: let POST /api/refresh accept a pct value")
    return p


def main(argv: Optional[List[str]] = None):
    p = build_arg_parser()
    args = p.parse_args(argv)

    # Security check: refuse non-loopback host unless --allow-remote is given
    host = str(args.host or "127.0.0.1").strip()
    if not is_loopback_host(host):
        if not args.allow_remote:
            print(
                f"WARNING: Refusing to bind usage_view.py to non-loopback host '{host}' without --allow-remote.",
                file=sys.stderr,
            )
            fail(2, f"Non-loopback host '{host}' refused without --allow-remote")
        else:
            print(
                f"WARNING: Binding usage_view.py to non-loopback host '{host}' (--allow-remote is set).",
                file=sys.stderr,
            )

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

    refresh_mode, refresh_interval_minutes = resolve_refresh_settings(
        args.refresh_mode, args.refresh_interval, cfg_file_data
    )

    resolved_lang = resolve(args.lang, "lang")
    is_privacy = bool(resolve(args.privacy, "privacy", default=False)) or (
        str(resolved_lang or "").strip().lower() == "numeric"
    )

    env_dir = os.environ.get("USAGE_DASH_DIR")
    data_dir = Path(resolve(args.data_dir, "data_dir", default=(env_dir if env_dir else "./usage-data")))
    summary_path = Path(resolve(args.summary, "summary", default=str(data_dir / "latest.json")))

    explicit_out_str = (
        resolve(args.out, "privacy_out", "out")
        if is_privacy
        else resolve(args.out, "out")
    )
    explicit_out = Path(explicit_out_str) if explicit_out_str else None

    pass_through: List[str] = []
    for flag_name, attr_name in (
        ("--config", "config"),
        ("--pct-source", "pct_source"),
        ("--reset-source", "reset_source"),
        ("--source", "source"),
        ("--reset-anchor", "reset_anchor"),
        ("--data-dir", "data_dir"),
        ("--out", "out"),
        ("--summary", "summary"),
        ("--tz", "tz"),
        ("--period-days", "period_days"),
        ("--pace", "pace"),
        ("--theme", "theme"),
        ("--lang", "lang"),
        ("--title", "title"),
        ("--bg-color", "bg_color"),
        ("--accent-color", "accent_color"),
        ("--baseline-color", "baseline_color"),
        ("--under-color", "under_color"),
        ("--over-color", "over_color"),
        ("--text-color", "text_color"),
        ("--palette", "palette"),
    ):
        val = getattr(args, attr_name, None)
        if val is not None:
            pass_through.extend([flag_name, str(val)])

    if is_privacy:
        pass_through.append("--privacy")
    if args.no_cache:
        pass_through.append("--no-cache")

    state = ViewerState(
        args=args,
        cfg_data=cfg_file_data,
        data_dir=data_dir,
        summary_path=summary_path,
        explicit_out=explicit_out,
        is_privacy=is_privacy,
        refresh_mode=refresh_mode,
        refresh_interval_minutes=refresh_interval_minutes,
        pass_through_flags=pass_through,
    )

    handler_cls = make_handler(state)
    try:
        server = ThreadingHTTPServer((host, int(args.port)), handler_cls)
    except Exception as exc:
        fail(3, f"Failed to bind HTTP server on {host}:{args.port}: {exc}")
        return

    actual_port = server.server_address[1]
    url_host = f"[{host}]" if (":" in host and not host.startswith("[")) else host
    url = f"http://{url_host}:{actual_port}/"

    if refresh_mode == "auto":
        t = threading.Thread(target=_auto_refresh_worker, args=(state,), daemon=True)
        t.start()

    token_file = data_dir / "view_server.token"
    try:
        local_guard.write_token_file(token_file, state.token)
    except OSError as exc:
        print(f"Warning: cannot write token file '{token_file}': {exc}", file=sys.stderr)
    emit_json_line({"ok": True, "url": url, "token_file": str(token_file.resolve())})

    should_open = (not args.no_open) and bool(resolve(None, "open", default=True))
    if should_open and can_open_browser():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_event.set()
        server.server_close()
        local_guard.remove_token_file(token_file, state.token)


if __name__ == "__main__":
    main()
