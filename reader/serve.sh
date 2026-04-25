#!/bin/bash
# Serve the reader against your data repo. Creates symlinks at the app root
# pointing to $CLAUDE_JOURNAL_DATA_DIR/data and /memory so the reader's relative
# fetch paths resolve.
#
# Usage:
#   CLAUDE_JOURNAL_DATA_DIR=~/francis-journal-data ./reader/serve.sh
#   ./reader/serve.sh                          # uses current dir if env not set
#
# Optional: PORT (default 8765)

set -euo pipefail
cd "$(dirname "$0")/.."   # app repo root
APP_ROOT="$(pwd)"
DATA="${CLAUDE_JOURNAL_DATA_DIR:-$APP_ROOT}"
PORT="${PORT:-8765}"

if [ ! -d "$DATA/data" ]; then
  echo "FATAL: $DATA/data not found." >&2
  echo "Set CLAUDE_JOURNAL_DATA_DIR to your data repo (e.g. ~/francis-journal-data)." >&2
  exit 1
fi

# Refresh symlinks at app root → data repo. Idempotent.
ln -snf "$DATA/data" data
ln -snf "$DATA/memory" memory

echo "==> serving reader at http://localhost:$PORT/reader/"
echo "    data: $DATA"
echo "    Ctrl-C to stop."
exec python3 -m http.server "$PORT"
