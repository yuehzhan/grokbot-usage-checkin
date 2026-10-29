#!/usr/bin/env python3
"""Small helpers for the one-click installers (stdlib only, Python 3.10+).

    python3 oneclick.py grok-auto check            # is Grok Build's default permission mode "auto"?
    python3 oneclick.py grok-auto set [--dry-run]  # make "auto" the saved default (restores it if it was turned off)
    python3 oneclick.py pct-source auto|manual [--config usage_config.json] [--dry-run]

``grok-auto`` touches exactly one file, Grok Build's user settings file ``config.toml`` in ``$GROK_HOME``
(default ``~/.grok``), and in it only the ``permission_mode`` key of the ``[ui]`` table, which is where the
Grok Build docs say the default mode lives (https://docs.x.ai/build/features/permissions). Every other line is
kept as it is. It never opens sign-in data (``auth.json``) or anything else in that folder.

Every command prints one line of JSON and exits 0 (done / already so), 1 (check: not auto) or 3 (cannot change
it safely; the message says what to do by hand).
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTO = "auto"
# Modes that already approve more than auto does. The installer does not lower them without --force.
WIDER = {"always-approve", "bypasspermissions"}
BACKUP_SUFFIX = ".bak-before-auto"

_TABLE = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\"' -]+?)\s*\]\s*(#.*)?$")
_ARRAY_TABLE = re.compile(r"^\s*\[\[")
_KEY = r"(?P<lead>\s*{key}\s*=\s*)(?P<val>\"[^\"]*\"|'[^']*'|[A-Za-z0-9_-]+)(?P<rest>.*)$"


def grok_home():
    """$GROK_HOME (documented override) or ~/.grok."""
    env = os.environ.get("GROK_HOME")
    return Path(env).expanduser() if env else Path.home() / ".grok"


def config_path():
    return grok_home() / "config.toml"


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _scan(lines):
    """Yield (index, table, line) with the table name each line belongs to ("" = top level, None = [[array]])."""
    table = ""
    for i, line in enumerate(lines):
        if _ARRAY_TABLE.match(line):
            table = None
        else:
            m = _TABLE.match(line)
            if m:
                table = _unquote(m.group(1).strip())
        yield i, table, line


def _find(lines, key):
    """(index, value) of ``key`` inside [ui] (or ``ui.key`` at top level), else (None, None)."""
    rx_ui = re.compile(_KEY.format(key=re.escape(key)))
    rx_top = re.compile(_KEY.format(key=r"ui\s*\.\s*" + re.escape(key)))
    for i, table, line in _scan(lines):
        rx = rx_ui if table == "ui" else rx_top if table == "" else None
        m = rx.match(line) if rx else None
        if m:
            return i, _unquote(m.group("val"))
    return None, None


def read_mode(text):
    """Effective default mode written in the file: permission_mode, else the legacy keys, else None (= ask)."""
    lines = text.splitlines()
    _, mode = _find(lines, "permission_mode")
    if mode:
        return mode
    _, yolo = _find(lines, "yolo")
    if yolo and yolo.lower() == "true":
        return "always-approve"
    _, legacy = _find(lines, "approval_mode")
    return legacy or None


def set_auto(text):
    """Return ``text`` with ``[ui] permission_mode = "auto"``; raises ValueError when it can't be done safely."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    if any(re.match(r"^\s*ui\s*=\s*\{", ln) for ln in lines):
        raise ValueError("config.toml uses an inline ui = { ... } table")
    i, _ = _find(lines, "permission_mode")
    if i is None and any(t == "" and re.match(r"^\s*ui\s*\.", ln) for _, t, ln in _scan(lines)):
        raise ValueError("config.toml writes ui.* keys at the top level, so a [ui] table cannot be added next to them")
    if i is not None:
        rx = re.compile(_KEY.format(key=r"(?:ui\s*\.\s*)?permission_mode"))
        m = rx.match(lines[i])
        lines[i] = m.group("lead") + '"auto"' + m.group("rest")
    else:
        header = next((j for j, table, line in _scan(lines) if table == "ui" and _TABLE.match(line)), None)
        if header is not None:
            lines.insert(header + 1, 'permission_mode = "auto"')
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines += ["[ui]", 'permission_mode = "auto"']
    return nl.join(lines) + nl


def _out(obj, code):
    print(json.dumps(obj, ensure_ascii=False))
    return code


def grok_auto(action, dry_run=False, force=False):
    p = config_path()
    try:
        text = p.read_text(encoding="utf-8-sig") if p.exists() else ""   # Notepad may add a BOM
    except (OSError, UnicodeDecodeError) as e:
        return _out({"ok": False, "file": str(p), "error": f"cannot read: {e.__class__.__name__}"}, 3)
    mode = read_mode(text)
    current = mode or "ask"
    if action == "check":
        return _out({"ok": True, "file": str(p), "mode": current, "auto": current == AUTO}, 0 if current == AUTO else 1)
    if current == AUTO:
        return _out({"ok": True, "file": str(p), "mode": current, "changed": False}, 0)
    if current.lower() in WIDER and not force:
        return _out({"ok": True, "file": str(p), "mode": current, "changed": False,
                     "note": "already approves more than auto; left as it is (use --force to switch to auto)"}, 0)
    try:
        new = set_auto(text)
    except ValueError as e:
        return _out({"ok": False, "file": str(p), "mode": current, "error": str(e),
                     "fix": 'add  permission_mode = "auto"  to the [ui] section of that file by hand'}, 3)
    if read_mode(new) != AUTO:  # e.g. a layout this simple editor does not understand
        return _out({"ok": False, "file": str(p), "mode": current, "error": "could not place the setting safely",
                     "fix": 'add  permission_mode = "auto"  to the [ui] section of that file by hand'}, 3)
    if dry_run:
        return _out({"ok": True, "file": str(p), "mode": current, "changed": False, "would_change": True}, 0)
    try:
        target = p.resolve() if p.is_symlink() else p   # a linked config file (dotfiles) stays a link
        p.parent.mkdir(parents=True, exist_ok=True)
        backup = Path(str(p) + BACKUP_SUFFIX)
        if p.exists() and not backup.exists():
            shutil.copy2(target, backup)
        tmp = target.with_name(target.name + ".tmp-oneclick")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        os.replace(tmp, target)
    except OSError as e:
        return _out({"ok": False, "file": str(p), "mode": current, "error": f"cannot write: {e.__class__.__name__}",
                     "fix": 'add  permission_mode = "auto"  to the [ui] section of that file by hand'}, 3)
    return _out({"ok": True, "file": str(p), "mode": AUTO, "was": current, "changed": True,
                 "backup": str(backup) if backup.exists() else None}, 0)


def pct_source(value, config=None, dry_run=False):
    """Create usage_config.json from the example if missing, then set "pct_source" (keeps every other key)."""
    cfg = Path(config) if config else HERE / "usage_config.json"
    created = False
    if not cfg.exists():
        if dry_run:
            return _out({"ok": True, "config": str(cfg), "would_create": True, "pct_source": value}, 0)
        shutil.copyfile(HERE / "usage_config.example.json", cfg)
        created = True
    try:
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return _out({"ok": False, "config": str(cfg), "error": f"cannot read: {e.__class__.__name__}",
                     "fix": "fix or delete usage_config.json, then run the installer again"}, 3)
    was = data.get("pct_source")
    if was == value:
        return _out({"ok": True, "config": str(cfg), "pct_source": value, "changed": False, "created": created}, 0)
    if dry_run:
        return _out({"ok": True, "config": str(cfg), "pct_source": was, "would_change": True}, 0)
    data["pct_source"] = value
    try:
        tmp = cfg.with_name(cfg.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, cfg)
    except OSError as e:
        return _out({"ok": False, "config": str(cfg), "error": f"cannot write: {e.__class__.__name__}",
                     "fix": 'set "pct_source" in usage_config.json by hand'}, 3)
    return _out({"ok": True, "config": str(cfg), "pct_source": value, "was": was, "changed": True,
                 "created": created}, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grok-auto", help="check or set Grok Build's default permission mode")
    g.add_argument("action", choices=("check", "set"))
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--force", action="store_true", help="also switch always-approve down to auto")
    s = sub.add_parser("pct-source", help='set "pct_source" in usage_config.json')
    s.add_argument("value", choices=("auto", "manual"))
    s.add_argument("--config")
    s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "grok-auto":
        return grok_auto(a.action, a.dry_run, a.force)
    return pct_source(a.value, a.config, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
