#!/bin/bash
# Install the SessionStart hook that injects coach output into new Claude Code sessions.
#
# Usage:
#   ./scripts/install-hook.sh                 # install to ~/.claude/settings.json
#   ./scripts/install-hook.sh --project       # install to ./.claude/settings.json
#   ./scripts/install-hook.sh --print         # print the JSON snippet; don't install
#
# Idempotent — running twice is safe.

set -euo pipefail
cd "$(dirname "$0")/.."
APP_ROOT="$(pwd)"

MODE="user"
for arg in "$@"; do
  case "$arg" in
    --project) MODE="project" ;;
    --print)   MODE="print" ;;
    -h|--help) grep "^# " "$0"; exit 0 ;;
  esac
done

COACH_CMD="python3 \"$APP_ROOT/scripts/coach.py\" --cwd \"\$CLAUDE_PROJECT_DIR\""

read -r -d '' SNIPPET <<JSON || true
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": $(printf %s "$COACH_CMD" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read()))'),
            "timeout": 10
          }
        ]
      }
    ]
  }
}
JSON

if [ "$MODE" = "print" ]; then
  echo "Add this to ~/.claude/settings.json (or ./.claude/settings.json for project-scoped):"
  echo
  echo "$SNIPPET"
  exit 0
fi

if [ "$MODE" = "user" ]; then
  TARGET="$HOME/.claude/settings.json"
else
  TARGET="./.claude/settings.json"
fi
mkdir -p "$(dirname "$TARGET")"

# Merge with existing settings if present. Uses Python so we don't add jq as a dep.
python3 - "$TARGET" "$COACH_CMD" <<'PY'
import json, os, sys
target, coach_cmd = sys.argv[1], sys.argv[2]

if os.path.exists(target):
    try:
        with open(target) as f:
            cfg = json.load(f)
    except Exception:
        print(f"ERROR: {target} exists but isn't valid JSON. Back it up and re-run.", file=sys.stderr)
        sys.exit(1)
else:
    cfg = {}

hooks = cfg.setdefault("hooks", {})
ss = hooks.setdefault("SessionStart", [])

# Check if a coach hook is already installed
def already_installed(hook_list):
    for group in hook_list:
        for h in group.get("hooks", []):
            if h.get("type") == "command" and "coach.py" in (h.get("command") or ""):
                return True
    return False

if already_installed(ss):
    print(f"[install-hook] coach hook already present in {target} — no changes")
    sys.exit(0)

ss.append({
    "matcher": "",
    "hooks": [
        {"type": "command", "command": coach_cmd, "timeout": 10}
    ],
})

with open(target, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"[install-hook] installed coach SessionStart hook → {target}")
PY

echo
echo "Done. Open a new Claude Code session in any folder — the coach block will show up at the top."
echo "To uninstall: edit $TARGET and remove the coach.py entry from hooks.SessionStart."
