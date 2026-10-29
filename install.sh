#!/usr/bin/env bash
# One-click install for Linux and macOS (macOS: double-click Install-Mac.command instead).
#
#   bash install.sh             check, show what will be installed, then install it, set up and start (no questions)
#   bash install.sh --ask       same, but ask "Continue? [y/N]" before installing anything
#   bash install.sh --dry-run   only show what would be done; changes nothing
#   bash install.sh --manual    you will type the weekly % yourself; Grok Build is not installed or changed
#   bash install.sh --no-grok-auto   do not change Grok Build's default mode
#   bash install.sh --no-start  set up, but do not start the dashboard
#   bash install.sh --no-shortcut   do not put a "Grok Usage Dashboard" launcher on the Desktop
#
# Safe to run again at any time: finished steps are skipped, and the Grok Build "auto" default is put back if
# it was switched off. Nothing here prints, reads or stores sign-in data; your usage data stays in this folder.
# This file needs bash; "sh install.sh" is switched over to it here.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -u
export PYTHONUTF8=1   # folder or user names with Chinese / accented letters must not break the helper scripts

GROK_INSTALL_URL="https://x.ai/cli/install.sh"   # official installer (https://docs.x.ai/build/overview)
ASK=0; DRY=0; MANUAL=0; GROK_AUTO=1; START=1; SHORTCUT=1; PAUSE="${ONECLICK_PAUSE:-auto}"   # auto: wait for Enter at the end when a double-click opened this window
for a in "$@"; do
  case "$a" in
    -y|--yes) ;;   # kept so older instructions still work: nothing is asked by default now
    --ask) ASK=1 ;;
    --dry-run) DRY=1 ;;
    --manual) MANUAL=1 ;;
    --no-grok-auto) GROK_AUTO=0 ;;
    --no-start) START=0 ;;
    --no-shortcut) SHORTCUT=0 ;;
    --pause) PAUSE=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $a  (see: bash install.sh --help)"; exit 2 ;;
  esac
done

cd "$(dirname "$0")" || exit 1

finish() {  # $1 = exit code
  if [ "$PAUSE" = "auto" ]; then
    # Opened from a file manager ("Run in terminal")? Then the window would close before the result can be read.
    # Started from a shell the user already has open: no waiting.
    PAUSE=0
    if [ -t 0 ] && [ -t 1 ]; then
      parent="$(ps -o comm= -p "$PPID" 2>/dev/null | tr -d ' ')"; parent="${parent##*/}"; parent="${parent#-}"
      case "$parent" in
        ""|bash|sh|dash|zsh|fish|ksh|csh|tcsh|su|sudo|login|sshd*|tmux*|screen*|make|ssh) ;;
        *) PAUSE=1 ;;
      esac
    fi
  fi
  if [ "$PAUSE" = "1" ] && [ -t 0 ]; then
    echo; read -r -p "Press Enter to close this window." _ || true
  fi
  exit "$1"
}
step() { echo; echo "== $* =="; }
ok()   { echo "   OK: $*"; }
info() { echo "   $*"; }
fail() {  # $1 = what went wrong, $2 = what to do next
  echo
  echo "!! Problem: $1"
  echo "   What to do: $2"
  echo "   Your files were not deleted. When it is fixed, run this installer again; finished steps are skipped."
  finish 1
}

if [ ! -f oneclick.py ] || [ ! -f service.py ] || [ ! -f requirements.txt ]; then
  fail "this folder does not hold the whole project (oneclick.py, service.py or requirements.txt is missing)." "Download the project again (GitHub: Code, then Download ZIP), unzip it completely and run install.sh from inside that folder."
fi

OS="$(uname -s)"
case "$OS" in
  Linux) OS_NAME="Linux" ;;
  Darwin) OS_NAME="macOS" ;;
  *) fail "this installer is for Linux and macOS (found: $OS)." "On Windows, double-click Install-Windows.cmd instead." ;;
esac

echo "Grok Bot Usage & Check-in: one-click install ($OS_NAME)"
[ "$DRY" = "1" ] && echo "(dry run: nothing will be changed)"

# ---------- 1. look around (changes nothing) ----------
step "1/6 Checking this computer"
PY=""
for c in python3 python python3.13 python3.12 python3.11 python3.10; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  if [ "$OS_NAME" = "macOS" ]; then
    fail "Python 3.10 or newer is not installed." "Install it from https://www.python.org/downloads/macos/ , then double-click Install-Mac.command again."
  else
    fail "Python 3.10 or newer is not installed." "Install it with your system's software centre (or: sudo apt install python3 python3-pip), then run this installer again."
  fi
fi
ok "Python: $("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
# A private .venv made by an earlier run of this installer (used when the system Python refuses pip installs).
if [ -x ".venv/bin/python" ] && ".venv/bin/python" -c 'import matplotlib, numpy' >/dev/null 2>&1; then
  PY="$PWD/.venv/bin/python"; ok "Using this folder's private Python packages (.venv)"
fi

find_grok() {
  if [ -n "${GROK_BIN:-}" ] && [ -x "${GROK_BIN}" ]; then echo "$GROK_BIN"; return 0; fi
  if command -v grok >/dev/null 2>&1; then command -v grok; return 0; fi
  if [ -x "$HOME/.grok/bin/grok" ]; then echo "$HOME/.grok/bin/grok"; return 0; fi
  return 1
}
GROK="$(find_grok || true)"
NEED_GROK=0
if [ "$MANUAL" = "1" ]; then
  info "Grok Build: not needed (manual mode)"
elif [ -n "$GROK" ]; then
  ok "Grok Build is installed: $GROK ($("$GROK" --version 2>/dev/null | head -n1))"
else
  NEED_GROK=1; info "Grok Build: not installed yet"
fi

if [ -n "$GROK" ] && [ "$MANUAL" = "0" ]; then
  case "$("$PY" oneclick.py grok-auto check 2>/dev/null)" in
    *'"auto": true'*) ok "Grok Build's default mode: auto" ;;
    *) info "Grok Build's default mode: not auto (will be set to auto)" ;;
  esac
fi

NEED_TMUX=0; TMUX_HOW=""
if [ "$MANUAL" = "0" ]; then
  if command -v tmux >/dev/null 2>&1; then ok "tmux is installed (used to read Grok Build's usage screen)"
  else
    NEED_TMUX=1
    if [ "$OS_NAME" = "macOS" ]; then
      command -v brew >/dev/null 2>&1 && TMUX_HOW="brew install tmux"
    elif command -v apt-get >/dev/null 2>&1; then TMUX_HOW="sudo apt-get install -y tmux"
    elif command -v dnf >/dev/null 2>&1; then TMUX_HOW="sudo dnf install -y tmux"
    elif command -v pacman >/dev/null 2>&1; then TMUX_HOW="sudo pacman -S --noconfirm tmux"
    fi
    info "tmux: not installed yet"
  fi
fi

NEED_PIP=0
if "$PY" -c 'import matplotlib, numpy' >/dev/null 2>&1; then ok "Python packages are installed (matplotlib, numpy)"
else NEED_PIP=1; info "Python packages: not installed yet (matplotlib, numpy)"; fi

# ---------- 2. say what will happen (asks only with --ask) ----------
step "2/6 What this installer will do now (no questions asked)"
n=0
if [ "$NEED_GROK" = "1" ]; then n=$((n+1)); info "$n. Install Grok Build, xAI's official app, with xAI's own installer:"
  info "     curl -fsSL $GROK_INSTALL_URL | bash"
  info "     (puts 'grok' in ~/.grok/bin and adds it to your PATH in your shell settings file)"; fi
if [ "$NEED_TMUX" = "1" ]; then n=$((n+1))
  if [ -n "$TMUX_HOW" ]; then info "$n. Install tmux:  $TMUX_HOW  (may ask for your computer password)"
  else info "$n. tmux is needed but no package tool was found; you will be told how to add it."; fi; fi
if [ "$NEED_PIP" = "1" ]; then n=$((n+1)); info "$n. Install Python packages:  $PY -m pip install -r requirements.txt"; fi
n=$((n+1)); info "$n. Create usage_config.json if it is missing and set reading to $([ "$MANUAL" = "1" ] && echo manual || echo auto)"
if [ "$MANUAL" = "0" ] && [ "$GROK_AUTO" = "1" ]; then n=$((n+1))
  info "$n. Make 'auto' Grok Build's saved default mode (only that one setting in ~/.grok/config.toml; your sign-in data is never opened)"; fi
if [ "$SHORTCUT" = "1" ]; then n=$((n+1)); info "$n. Put a \"Grok Usage Dashboard\" launcher on your Desktop (macOS: always; Linux: only if ~/Desktop exists)"; fi
if [ "$START" = "1" ]; then n=$((n+1)); info "$n. Start the dashboard on this computer only (127.0.0.1) and open it in your browser"; fi

if [ "$DRY" = "1" ]; then
  echo; echo "Dry run finished. Nothing was changed."; finish 0
fi
if [ "$ASK" = "1" ] && [ $((NEED_GROK + NEED_TMUX + NEED_PIP)) -gt 0 ]; then   # opt-in only (--ask)
  echo
  if [ -t 0 ]; then
    read -r -p "Continue? [y/N] " ans || ans=""
  elif (: </dev/tty) 2>/dev/null; then
    read -r -p "Continue? [y/N] " ans </dev/tty || ans=""
  else
    fail "nobody is there to answer the question." "Run it again without --ask."
  fi
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "Stopped. Nothing was installed."; finish 1 ;; esac
fi

# ---------- 3. Grok Build ----------
step "3/6 Grok Build"
JUST_INSTALLED_GROK=0
if [ "$NEED_GROK" = "1" ]; then
  command -v curl >/dev/null 2>&1 || fail "the download tool 'curl' is missing." "Install curl (Linux: sudo apt install curl), then run this installer again."
  info "Downloading and installing Grok Build from x.ai ..."
  # Download to a file first: with "curl | bash" a failed download looks like a successful (empty) script.
  GROK_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/grok-install.XXXXXX")" || fail "no temporary file could be created." "Free some disk space or check the TMPDIR setting, then run this installer again."
  if ! curl -fsSL "$GROK_INSTALL_URL" -o "$GROK_SCRIPT" || [ ! -s "$GROK_SCRIPT" ]; then
    rm -f "$GROK_SCRIPT"
    fail "Grok Build could not be downloaded from x.ai (is the internet connection working?)." "Check your internet connection and run this installer again. You can also install it by hand: curl -fsSL $GROK_INSTALL_URL | bash"
  fi
  if ! bash "$GROK_SCRIPT"; then
    rm -f "$GROK_SCRIPT"
    fail "Grok Build's own installer reported an error (see the messages above)." "Run this installer again. If it keeps failing, install it by hand: curl -fsSL $GROK_INSTALL_URL | bash   (or run this installer with --manual to type the weekly % yourself)"
  fi
  rm -f "$GROK_SCRIPT"
  export PATH="$HOME/.grok/bin:$PATH"
  GROK="$(find_grok || true)"
  [ -n "$GROK" ] || fail "Grok Build's installer finished, but the 'grok' program cannot be found." "Close this window, open a new one and run this installer again."
  JUST_INSTALLED_GROK=1
  ok "Grok Build installed: $("$GROK" --version 2>/dev/null | head -n1)"
elif [ "$MANUAL" = "1" ]; then info "Skipped (manual mode)"
else ok "Already installed, skipped"; fi

# ---------- 4. tmux + Python packages ----------
step "4/6 Helper programs"
if [ "$NEED_TMUX" = "1" ]; then
  if [ -n "$TMUX_HOW" ]; then
    info "Installing tmux ($TMUX_HOW) ..."
    # shellcheck disable=SC2086  # TMUX_HOW is one of the fixed commands above
    $TMUX_HOW || fail "tmux could not be installed." "Run  $TMUX_HOW  yourself, then run this installer again. (Or run it with --manual to type the weekly % yourself.)"
    ok "tmux installed"
  else
    info "Note: tmux could not be installed automatically (no package tool found)."
    info "      Until you add it ($([ "$OS_NAME" = "macOS" ] && echo "for example from https://brew.sh then: brew install tmux" || echo "with your system's software centre")),"
    info "      the dashboard cannot read Grok Build by itself; use 'Enter manually' on the page."
  fi
elif [ "$MANUAL" = "0" ]; then ok "tmux already installed, skipped"; fi
if [ "$NEED_PIP" = "1" ]; then
  info "Installing Python packages (a few minutes the first time) ..."
  if ! "$PY" -m pip install --disable-pip-version-check -r requirements.txt; then
    # Newer Debian / Ubuntu / Homebrew Pythons refuse system-wide pip installs: use a private .venv in this folder.
    info "That did not work. Trying again in a private folder (.venv) inside this project ..."
    MADE_VENV=0; [ -e .venv ] || MADE_VENV=1
    if "$PY" -m venv .venv && .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt; then
      PY="$PWD/.venv/bin/python"
    else
      [ "$MADE_VENV" = "1" ] && rm -rf .venv
      if [ "$OS_NAME" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
        fail "Python packages could not be installed (no internet, or this system blocks pip)." "Check your internet connection. If it is connected, run  sudo apt install python3-venv python3-matplotlib python3-numpy  and then run this installer again."
      else
        fail "Python packages could not be installed (no internet, or this system blocks pip)." "Check your internet connection and run this installer again."
      fi
    fi
  fi
  ok "Python packages installed"
else ok "Python packages already installed, skipped"; fi

# ---------- 5. settings ----------
step "5/6 Settings"
PCT="auto"; [ "$MANUAL" = "1" ] && PCT="manual"
out="$("$PY" oneclick.py pct-source "$PCT")" || fail "usage_config.json could not be updated ($out)." "Fix or delete usage_config.json in this folder, then run this installer again."
case "$out" in
  *'"created": true'*) ok "usage_config.json created: reading is $PCT" ;;
  *'"changed": true'*) ok "usage_config.json: reading set to $PCT" ;;
  *) ok "usage_config.json: reading already $PCT" ;;
esac
if [ "$MANUAL" = "0" ] && [ "$GROK_AUTO" = "1" ]; then
  out="$("$PY" oneclick.py grok-auto set)"; rc=$?
  if [ $rc -ne 0 ]; then
    info "Could not set Grok Build's default mode by itself: $out"
    info "To do it by hand: open ~/.grok/config.toml and put  permission_mode = \"auto\"  under the [ui] line."
  else
    case "$out" in
      *'"changed": true'*) ok "Grok Build's default mode is now 'auto' (saved, so it stays after you close Grok Build)" ;;
      *'"note"'*) ok "Grok Build already uses always-approve, which approves more than auto; left as it is" ;;
      *) ok "Grok Build's default mode is already 'auto'" ;;
    esac
  fi
fi

# ---------- 6. Desktop launcher + start ----------
step "6/6 Desktop launcher and starting the dashboard"
SHORTCUT_PATH=""
# Double-clickable launcher on the Desktop. Only a warning if it cannot be made. ONECLICK_DESKTOP replaces the
# Desktop folder (tests use it; otherwise the folder is $HOME/Desktop, so a fake HOME never touches the real one).
make_shortcut() {
  local desk="${ONECLICK_DESKTOP:-$HOME/Desktop}" app="$PWD" name="Grok Usage Dashboard" f q
  if [ ! -f "$app/start-dashboard.sh" ]; then info "No desktop launcher: start-dashboard.sh is missing from this folder."; return 0; fi
  chmod +x "$app/start-dashboard.sh" 2>/dev/null
  if [ "$OS_NAME" = "macOS" ]; then
    f="$desk/$name.command"
    mkdir -p "$desk" 2>/dev/null
    q="$(printf '%q' "$app")"
    if printf '#!/bin/bash\ncd %s || exit 1\nexec bash ./start-dashboard.sh\n' "$q" > "$f" 2>/dev/null && chmod +x "$f" 2>/dev/null; then
      SHORTCUT_PATH="$f"; ok "Desktop launcher: $f"
    else info "Could not make the desktop launcher (not a problem; start the dashboard with start-dashboard.sh in this folder)."; fi
  else
    [ -d "$desk" ] || return 0   # Linux: no Desktop folder, nothing to do
    f="$desk/$name.desktop"
    q="$app/start-dashboard.sh"   # .desktop Exec quoting: backslash-escape quote, backtick, dollar and backslash; double the %
    q="$(printf '%s' "$q" | sed -e 's/[\\"`$]/\\&/g' -e 's/%/%%/g')"
    if printf '[Desktop Entry]\nType=Application\nName=%s\nComment=Open the Grok usage dashboard\nExec=bash "%s"\nPath=%s\nTerminal=false\nIcon=utilities-system-monitor\n' "$name" "$q" "$app" > "$f" 2>/dev/null && chmod +x "$f" 2>/dev/null; then
      gio set "$f" metadata::trusted true >/dev/null 2>&1 || true   # lets GNOME run it with a double-click
      SHORTCUT_PATH="$f"; ok "Desktop launcher: $f"
    else info "Could not make the desktop launcher (not a problem; start the dashboard with start-dashboard.sh in this folder)."; fi
  fi
  return 0
}
[ "$SHORTCUT" = "1" ] && make_shortcut

URL=""
if [ "$START" = "1" ]; then
  out="$("$PY" service.py start --open 2>&1)" || fail "the dashboard did not start." "Look at usage-data/service.log in this folder, or ask your Grok Bot to read it. Then run this installer again."
  URL="$(printf '%s\n' "$out" | "$PY" -c 'import json,sys
for l in sys.stdin:
    l = l.strip()
    if l.startswith("{"):
        try: print(json.loads(l).get("url") or ""); break
        except ValueError: pass')"
  case "$out" in
    *'"note": "already running"'*) ok "Dashboard was already running (it keeps the version it started with; to load a newer one run:  \"$PY\" service.py restart)" ;;
    *) ok "Dashboard is running" ;;
  esac
else info "Skipped (--no-start). Start it later with:  \"$PY\" service.py start --open"; fi

echo
echo "============================================================"
echo " Done."
[ -n "$URL" ] && echo " Your dashboard: $URL"
[ -n "$SHORTCUT_PATH" ] && echo " Desktop shortcut: $SHORTCUT_PATH   (delete it when you uninstall)"
echo " (In this folder)  Stop:  \"$PY\" service.py stop     Start again:  \"$PY\" service.py start --open"
# Sign-in hint only when sign-in looks missing. This only asks whether Grok Build's sign-in file EXISTS ([ -f ]);
# the file is never opened or read.
SIGNED_IN=0
[ -f "${GROK_HOME:-$HOME/.grok}/auth.json" ] && SIGNED_IN=1
if [ "$MANUAL" = "0" ] && [ "$SIGNED_IN" = "1" ]; then
  echo
  echo " Grok Build: a sign-in was found on this computer, so the automatic reading should work."
elif [ "$MANUAL" = "0" ] && [ "$JUST_INSTALLED_GROK" = "1" ]; then
  echo
  echo " One last step: sign in to Grok Build once. Open a NEW terminal window and run  grok  (a browser"
  echo " page opens; check that the account is yours, press Allow, then quit Grok Build with Ctrl+Q)."
elif [ "$MANUAL" = "0" ]; then
  echo
  echo " No Grok Build sign-in found on this computer. Run  grok  once in a terminal (a browser page opens; check that"
  echo " the account is yours, press Allow, then quit with Ctrl+Q). Until then the automatic reading shows as unavailable."
  echo " (Already signed in? Then you can ignore this note.)"
fi
echo "============================================================"
finish 0
