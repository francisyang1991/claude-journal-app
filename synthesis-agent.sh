#!/bin/bash
# Layer 2 — Synthesis agent. Runs centrally (one host, or GitHub Actions).
# Responsibilities: pull raw sessions from git, summarize per-session,
# synthesize the day, write data/day.json + memory/threads.json, push back.
# Requires GLM_API_KEY or ANTHROPIC_API_KEY.
#
# Reads/writes via $CLAUDE_JOURNAL_DATA_DIR (the user's data repo).

set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${CLAUDE_JOURNAL_DATA_DIR:-$APP_ROOT}"

DATE="${1:-$(date +%Y-%m-%d)}"
PY="${PYTHON:-python3}"

echo "==> [synthesis-agent] $DATE"
echo "    app:  $APP_ROOT"
echo "    data: $DATA_DIR"

if [ -z "${GLM_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "FATAL: neither GLM_API_KEY nor ANTHROPIC_API_KEY is set."
  echo "Set one (GLM is cheaper), or LLM_PROVIDER=glm|anthropic to force."
  exit 1
fi

# Pull latest from data repo before computing
if [ -d "$DATA_DIR/.git" ]; then
  git -C "$DATA_DIR" pull --rebase --autostash 2>/dev/null || echo "[synthesis-agent] pull skipped"
fi

RAW="$DATA_DIR/raw/sessions-${DATE}.json"
if [ ! -f "$RAW" ]; then
  echo "[synthesis-agent] no $RAW — no device has synced data for $DATE yet"
  exit 0
fi

cd "$APP_ROOT"

echo "--> summarize (per-session)"
CLAUDE_JOURNAL_DATA_DIR="$DATA_DIR" "$PY" scripts/summarize.py --date "$DATE"

echo "--> synthesize (daily)"
CLAUDE_JOURNAL_DATA_DIR="$DATA_DIR" "$PY" scripts/synthesize.py --date "$DATE"

# Commit the report back to the data repo
if [ -d "$DATA_DIR/.git" ]; then
  cd "$DATA_DIR"
  git add data/ memory/ 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "synthesis: daily report ${DATE}" --no-verify
    git push 2>&1 | tail -3 || echo "[synthesis-agent] push skipped (no remote)"
  else
    echo "[synthesis-agent] report unchanged, nothing to commit"
  fi
fi

echo "==> [synthesis-agent] done. Run ./reader/serve.sh to view."
