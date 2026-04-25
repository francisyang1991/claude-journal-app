#!/bin/bash
# Pull-only refresh of the data repo. Use on devices that don't run the
# full device-agent.sh (e.g. a laptop you only read from) — or as an extra
# tick before your morning routine so the reader and coach show fresh state.
#
# device-agent.sh and synthesis-agent.sh both already pull as part of their
# normal flow; this script is for "I just want to refresh, nothing else."
#
# Usage:
#   ./scripts/sync.sh
#   CLAUDE_JOURNAL_DATA_DIR=~/francis-journal-data ./scripts/sync.sh

set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${CLAUDE_JOURNAL_DATA_DIR:-$APP_ROOT}"

if [ ! -d "$DATA_DIR/.git" ]; then
  echo "[sync] $DATA_DIR is not a git repo — nothing to sync" >&2
  exit 0
fi

echo "[sync] $(date +%H:%M) pulling $DATA_DIR"
git -C "$DATA_DIR" pull --rebase --autostash 2>&1 | tail -3
