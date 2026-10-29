#!/usr/bin/env bash
# Double-click this file on a Mac to install and start the dashboard.
# (First time: if macOS says it cannot be opened, right-click it, choose Open, then Open again.)
cd "$(dirname "$0")" || exit 1
ONECLICK_PAUSE=1 exec bash ./install.sh "$@"
