#!/bin/bash
# Layer 1 — Device agent. Runs on every device on a schedule.
# Responsibilities: collect today's sessions, apply hard-exclude, push to git.
# No LLM calls. No API key required.

set -euo pipefail
cd "$(dirname "$0")"

DATE="${1:-$(date +%Y-%m-%d)}"
PY="${PYTHON:-python3}"
DEVICE="${DEVICE_SLUG:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"

echo "==> [device-agent] $DEVICE · $DATE"

# Collect: walks ~/.claude/projects/ and Cowork sessions → raw/sessions-DATE.json
"$PY" scripts/collect.py --date "$DATE"

# Git handoff to synthesis layer. Skip gracefully if not a repo yet.
if [ -d .git ]; then
  # Pull first so we don't push a stale branch
  git pull --rebase --autostash 2>/dev/null || true
  git add raw/ config/ || true
  if ! git diff --cached --quiet; then
    git commit -m "chore(${DEVICE}): sessions ${DATE} $(date +%H:%M)" --no-verify
    git push 2>&1 | tail -3 || echo "[device-agent] push skipped (no remote configured yet)"
  else
    echo "[device-agent] no new session data to commit"
  fi
else
  echo "[device-agent] not a git repo — skipping sync. Run 'git init' to enable."
fi

echo "==> [device-agent] done"
