#!/bin/bash
# Scaffold a new user's private data repo.
#
# Usage:
#   ./scripts/new-user.sh <target-dir> [source1,source2,...]
#
# Examples:
#   ./scripts/new-user.sh ~/francis-journal-data claude-code,cowork
#   ./scripts/new-user.sh ~/yuwen-journal-data cursor
#
# This creates the directory tree a data repo needs, seeds config files with
# sensible defaults, and initializes it as a git repo. You still need to:
#   1. Create a PRIVATE repo on GitHub
#   2. git remote add origin git@github.com:<you>/<data-repo>.git
#   3. git push -u origin main
#   4. Set CLAUDE_JOURNAL_DATA_DIR in your shell config

set -euo pipefail

TARGET="${1:-}"
SOURCES="${2:-claude-code,cowork}"

if [ -z "$TARGET" ]; then
  echo "Usage: $0 <target-dir> [sources]" >&2
  exit 1
fi

if [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
  echo "FATAL: $TARGET already exists and is not empty." >&2
  exit 1
fi

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$TARGET"/{config,raw,data,memory}
cd "$TARGET"

# Copy starter configs from the app repo
cp "$APP_ROOT/config/topics.yaml" config/topics.yaml
cp "$APP_ROOT/config/hard_exclude.yaml" config/hard_exclude.yaml

# Generate sources.yaml from the user's chosen list
{
  echo "# Enabled source adapters for this user."
  echo "# Available: claude-code, cowork, cursor (see app repo scripts/sources/)."
  echo "sources:"
  IFS=',' read -ra A <<< "$SOURCES"
  for s in "${A[@]}"; do
    echo "  - $(echo "$s" | xargs)"
  done
} > config/sources.yaml

# Minimal README so the remote repo shows something useful
cat > README.md <<EOF
# Claude Journal — data repo

Private memory store for one user. Read-write by the owner only.

Operated by scripts in the claude-journal-app repo. Point the app at this
directory via \`CLAUDE_JOURNAL_DATA_DIR=\$PWD\`.

## Enabled sources

See \`config/sources.yaml\`.

## Layout

- \`config/\`   — per-user topics, hard-exclude, source selection
- \`raw/\`      — daily collected session JSON (device agents write)
- \`data/\`     — synthesized daily reports (synthesis agent writes)
- \`memory/\`   — thread state, answers-pending, digests (synthesis agent writes)

EOF

# .gitignore for things that should not travel
cat > .gitignore <<EOF
.DS_Store
raw/summaries-*.json
*.swp
EOF

git init -q -b main
git add -A
git commit -q -m "chore: scaffold empty data repo" --no-verify

echo "==> Scaffolded data repo at $TARGET"
echo "    Sources: $SOURCES"
echo
echo "Next steps:"
echo "  1. Create a PRIVATE GitHub repo (e.g. <you>/claude-journal-data)"
echo "  2. cd $TARGET"
echo "  3. git remote add origin git@github.com:<you>/<data-repo>.git"
echo "  4. git push -u origin main"
echo "  5. Add to your shell config:"
echo "       export CLAUDE_JOURNAL_DATA_DIR=$TARGET"
echo "  6. Run the app's device-agent.sh to populate raw/"
