#!/usr/bin/env bash
# Smoke test for a freshly-onboarded device.
# Walks through pre-flight, setup, and a real device-agent run, reporting
# pass/fail for each check. Safe to re-run.
#
# Usage:
#   scripts/smoke-test.sh              # uses today's date
#   scripts/smoke-test.sh 2026-04-25   # back-fill a specific date
#
# Exit code: 0 if all required checks pass, 1 otherwise.

set -uo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATE="${1:-$(date +%Y-%m-%d)}"

# ── colors ───────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[1m'; N='\033[0m'
else
  G=''; R=''; Y=''; B=''; N=''
fi

PASS=0; FAIL=0; WARN=0
say() { printf '%b\n' "$*"; }
ok()   { say "  ${G}✓${N} $*"; PASS=$((PASS+1)); }
bad()  { say "  ${R}✗${N} $*"; FAIL=$((FAIL+1)); }
warn() { say "  ${Y}!${N} $*"; WARN=$((WARN+1)); }
hdr()  { say "\n${B}── $* ──${N}"; }

# ── 1. pre-flight ────────────────────────────────────────────────────────────
hdr "pre-flight"

if command -v gh >/dev/null 2>&1; then
  ok "gh CLI installed ($(gh --version | head -1))"
else
  bad "gh CLI not found — install with 'brew install gh' or apt"
fi

if command -v git >/dev/null 2>&1; then
  ok "git installed"
else
  bad "git not found"
fi

if command -v python3 >/dev/null 2>&1; then
  ok "python3 installed ($(python3 --version))"
else
  bad "python3 not found"
fi

if gh auth status >/dev/null 2>&1; then
  user="$(gh api user --jq .login 2>/dev/null || echo '?')"
  ok "gh authenticated as $user"
else
  bad "gh not authenticated — run 'gh auth login'"
fi

uname_e="$(git config --global user.email || echo '')"
uname_n="$(git config --global user.name || echo '')"
if [ -n "$uname_e" ] && [ -n "$uname_n" ]; then
  ok "git identity: $uname_n <$uname_e>"
else
  warn "git identity not set globally — commits will use auto-generated identity"
fi

# ── 2. session sources ───────────────────────────────────────────────────────
hdr "session sources on this device"

found_any=0
if [ -d "$HOME/.claude/projects" ]; then
  n=$(find "$HOME/.claude/projects" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
  ok "Claude Code sessions: $n project dir(s) under ~/.claude/projects"
  found_any=1
else
  warn "no ~/.claude/projects (Claude Code never used here)"
fi

cowork_dir="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"
if [ -d "$cowork_dir" ]; then
  n=$(find "$cowork_dir" -maxdepth 1 -mindepth 1 | wc -l | tr -d ' ')
  ok "Cowork sessions: $n entries"
  found_any=1
else
  warn "no Cowork session dir"
fi

cursor_db="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
if [ -f "$cursor_db" ]; then
  ok "Cursor state.vscdb present"
  found_any=1
else
  warn "no Cursor state.vscdb"
fi

if [ "$found_any" -eq 0 ]; then
  bad "no session sources found — collect will produce 0 sessions"
fi

# ── 3. env vars ──────────────────────────────────────────────────────────────
hdr "environment"

DATA_DIR="${CLAUDE_JOURNAL_DATA_DIR:-}"
if [ -n "$DATA_DIR" ]; then
  # Expand ~
  DATA_DIR="${DATA_DIR/#\~/$HOME}"
  ok "CLAUDE_JOURNAL_DATA_DIR=$DATA_DIR"
else
  bad "CLAUDE_JOURNAL_DATA_DIR not set"
fi

DEVICE="${DEVICE_SLUG:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"
if [ -n "${DEVICE_SLUG:-}" ]; then
  ok "DEVICE_SLUG=$DEVICE"
else
  warn "DEVICE_SLUG unset, falling back to hostname '$DEVICE' — set explicitly to avoid clashes"
fi

# ── 4. data repo ─────────────────────────────────────────────────────────────
hdr "data repo"

if [ -n "$DATA_DIR" ] && [ -d "$DATA_DIR/.git" ]; then
  ok "data repo is a git checkout"
  remote="$(git -C "$DATA_DIR" remote get-url origin 2>/dev/null || echo '')"
  if [ -n "$remote" ]; then
    ok "remote: $remote"
  else
    bad "no 'origin' remote configured"
  fi
  branch="$(git -C "$DATA_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  if [ "$branch" = "main" ]; then
    ok "on branch main"
  else
    warn "on branch '$branch' (expected main) — GHA likely won't trigger"
  fi
else
  bad "data dir missing or not a git repo"
fi

# ── 5. run device-agent ──────────────────────────────────────────────────────
hdr "device-agent dry run (date=$DATE)"

if [ -x "$APP_ROOT/device-agent.sh" ]; then
  ok "device-agent.sh executable"
  before_sha="$(git -C "$DATA_DIR" rev-parse HEAD 2>/dev/null || echo '')"
  out_file="$DATA_DIR/raw/sessions-$DATE.json"

  if "$APP_ROOT/device-agent.sh" "$DATE"; then
    ok "device-agent exited 0"
  else
    bad "device-agent exited non-zero"
  fi

  if [ -f "$out_file" ]; then
    kept=$(python3 -c "import json,sys; d=json.load(open('$out_file')); print(d.get('sessions_kept',0))" 2>/dev/null || echo '?')
    ok "raw/sessions-$DATE.json written (kept=$kept)"
  else
    bad "raw/sessions-$DATE.json not produced"
  fi

  after_sha="$(git -C "$DATA_DIR" rev-parse HEAD 2>/dev/null || echo '')"
  if [ -n "$before_sha" ] && [ "$before_sha" != "$after_sha" ]; then
    ok "new commit: $before_sha → $after_sha"
    if git -C "$DATA_DIR" status -uno | grep -q "Your branch is up to date"; then
      ok "pushed to origin"
    else
      warn "local ahead of origin — push may have failed"
    fi
  else
    warn "no new commit (likely no new data since last run)"
  fi
else
  bad "device-agent.sh not executable at $APP_ROOT/device-agent.sh"
fi

# ── 6. GHA trigger check ─────────────────────────────────────────────────────
hdr "GitHub Actions"

if [ -n "$DATA_DIR" ] && [ -d "$DATA_DIR/.git" ] && command -v gh >/dev/null 2>&1; then
  repo_url="$(git -C "$DATA_DIR" remote get-url origin 2>/dev/null || echo '')"
  # Convert git@github.com:foo/bar.git or https://github.com/foo/bar.git → foo/bar
  repo_slug="$(echo "$repo_url" | sed -E 's#(git@github.com:|https?://github.com/)##; s#\.git$##')"
  if [ -n "$repo_slug" ]; then
    say "  checking recent runs for $repo_slug …"
    gh run list --repo "$repo_slug" --limit 3 2>/dev/null | sed 's/^/    /' || warn "couldn't query gh runs"
    ok "see above for synthesis run status"
  fi
fi

# ── summary ──────────────────────────────────────────────────────────────────
hdr "summary"
say "  ${G}pass:${N} $PASS   ${R}fail:${N} $FAIL   ${Y}warn:${N} $WARN"
if [ "$FAIL" -gt 0 ]; then
  say "\n${R}smoke test FAILED${N} — see ✗ items above"
  exit 1
fi
say "\n${G}smoke test passed${N}"
exit 0
