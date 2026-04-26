#!/bin/bash
# Layer 1 — Device agent. Runs on every device on a schedule.
# Responsibilities: collect today's sessions, apply hard-exclude, push to git.
# No LLM calls. No API key required.
#
# Reads/writes session data via $CLAUDE_JOURNAL_DATA_DIR (the user's data repo).
# If unset, defaults to the app repo's working dir for legacy single-repo use.

set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${CLAUDE_JOURNAL_DATA_DIR:-$APP_ROOT}"

DATE="${1:-$(date +%Y-%m-%d)}"
PY="${PYTHON:-python3}"
DEVICE="${DEVICE_SLUG:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"

echo "==> [device-agent] $DEVICE · $DATE"
echo "    app:  $APP_ROOT"
echo "    data: $DATA_DIR"

# Collect: walks Claude Code, Cowork, Cursor sessions → $DATA_DIR/raw/sessions-DATE.json
cd "$APP_ROOT"
CLAUDE_JOURNAL_DATA_DIR="$DATA_DIR" "$PY" scripts/collect.py --date "$DATE"

# Sync Claude memory (per-device, per-project) → $DATA_DIR/memory/<device>/<project>/
MEM_ROOT="$HOME/.claude/projects"
MEM_OUT="$DATA_DIR/memory/$DEVICE"
if [ -d "$MEM_ROOT" ]; then
  mkdir -p "$MEM_OUT"
  count=0
  for d in "$MEM_ROOT"/*/memory; do
    [ -d "$d" ] || continue
    proj="$(basename "$(dirname "$d")")"
    mkdir -p "$MEM_OUT/$proj"
    rsync -a --delete "$d/" "$MEM_OUT/$proj/"
    count=$((count + 1))
  done
  echo "[memory] synced $count project memory dirs → $MEM_OUT"
fi

# Git handoff to synthesis layer happens IN THE DATA REPO (not app repo).
if [ -d "$DATA_DIR/.git" ]; then
  cd "$DATA_DIR"
  git pull --rebase --autostash 2>/dev/null || true
  git add raw/ config/ memory/ 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "chore(${DEVICE}): sessions+memory ${DATE} $(date +%H:%M)" --no-verify
    git push 2>&1 | tail -3 || echo "[device-agent] push skipped (no remote configured yet)"
  else
    echo "[device-agent] no new session data to commit"
  fi
else
  echo "[device-agent] $DATA_DIR is not a git repo — skipping sync."
fi

echo "==> [device-agent] done"
