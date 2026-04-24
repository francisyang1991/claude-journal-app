#!/bin/bash
# Layer 2 — Synthesis agent. Runs centrally (one host, or GitHub Actions).
# Responsibilities: pull raw sessions from git, summarize per-session (Haiku),
# synthesize the day (Sonnet), write data/day.json, push back.
# Requires ANTHROPIC_API_KEY.

set -euo pipefail
cd "$(dirname "$0")"

DATE="${1:-$(date +%Y-%m-%d)}"
PY="${PYTHON:-python3}"

echo "==> [synthesis-agent] $DATE"

if [ -z "${GLM_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "FATAL: neither GLM_API_KEY nor ANTHROPIC_API_KEY is set."
  echo "Set one of them (GLM is cheaper), or use LLM_PROVIDER=glm|anthropic to force."
  exit 1
fi

# Pull latest device pushes
if [ -d .git ]; then
  git pull --rebase --autostash 2>/dev/null || echo "[synthesis-agent] pull skipped"
fi

RAW="raw/sessions-${DATE}.json"
if [ ! -f "$RAW" ]; then
  echo "[synthesis-agent] no $RAW — no device has synced data for $DATE yet"
  exit 0
fi

echo "--> summarize (per-session Haiku)"
"$PY" scripts/summarize.py --date "$DATE"

echo "--> synthesize (daily Sonnet)"
"$PY" scripts/synthesize.py --date "$DATE"

# Commit the report back
if [ -d .git ]; then
  git add data/day.json || true
  if ! git diff --cached --quiet; then
    git commit -m "synthesis: daily report ${DATE}" --no-verify
    git push 2>&1 | tail -3 || echo "[synthesis-agent] push skipped (no remote)"
  else
    echo "[synthesis-agent] report unchanged, nothing to commit"
  fi
fi

echo "==> [synthesis-agent] done. Open reader/index.html"
