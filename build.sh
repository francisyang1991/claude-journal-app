#!/bin/bash
# Orchestrator: collect → summarize → synthesize → day.json
set -euo pipefail

cd "$(dirname "$0")"

DATE="${1:-$(date +%Y-%m-%d)}"
echo "==> Building journal for $DATE"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "WARN: ANTHROPIC_API_KEY not set — summarize/synthesize will skip LLM calls."
  NOLLM="--no-llm"
else
  NOLLM=""
fi

PY="${PYTHON:-python3}"

echo "--> collect"
"$PY" scripts/collect.py --date "$DATE"

if [ -z "$NOLLM" ]; then
  echo "--> summarize"
  "$PY" scripts/summarize.py --date "$DATE"
fi

echo "--> synthesize"
"$PY" scripts/synthesize.py --date "$DATE" $NOLLM

echo "==> Done. Open reader/index.html"
