#!/bin/bash
# Orchestrator: collect → summarize → synthesize → day.json
set -euo pipefail

cd "$(dirname "$0")"

DATE="${1:-$(date +%Y-%m-%d)}"
echo "==> Building journal for $DATE"

if [ -z "${GLM_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "WARN: neither GLM_API_KEY nor ANTHROPIC_API_KEY set — skipping LLM steps."
  NOLLM="--no-llm"
  SKIP_SUMMARIZE=1
else
  NOLLM=""
  SKIP_SUMMARIZE=0
fi

PY="${PYTHON:-python3}"

echo "--> collect"
"$PY" scripts/collect.py --date "$DATE"

if [ "$SKIP_SUMMARIZE" = "0" ]; then
  echo "--> summarize"
  "$PY" scripts/summarize.py --date "$DATE"
fi

echo "--> synthesize"
"$PY" scripts/synthesize.py --date "$DATE" $NOLLM

echo "==> Done. Open reader/index.html"
