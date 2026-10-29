#!/usr/bin/env bash
# Starts the dashboard (page + sampler, in the background) and opens it in your browser.
# This is what the "Grok Usage Dashboard" desktop shortcut made by install.sh runs (Mac: the .command file,
# Linux: the .desktop launcher). Stop it with:  .venv/bin/python service.py stop   (or: python3 service.py stop)
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
PY=""
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"
else
  for c in python3 python python3.13 python3.12 python3.11 python3.10; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import matplotlib, numpy' >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "Not installed yet. Run install.sh (Mac: Install-Mac.command) in this folder first."
  [ -t 0 ] && { read -r -p "Press Enter to close." _ || true; }
  exit 1
fi
if out="$("$PY" service.py start --open 2>&1)"; then
  echo "Grok Usage Dashboard is running; your browser opens it. You can close this window."
  exit 0
fi
echo "Could not start the dashboard:"; echo "$out"
echo "More in usage-data/service.log in this folder."
[ -t 0 ] && { read -r -p "Press Enter to close." _ || true; }
exit 1
