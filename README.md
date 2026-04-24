# Claude Journal — MVP

Two-agent daily journal for Claude Code + Cowork. Git is the handoff.

```
┌────────────────────────┐      git       ┌────────────────────────┐
│  Layer 1               │  ───────────▶  │  Layer 2               │
│  Device agent          │    raw/        │  Synthesis agent       │
│  (every device, cron)  │                │  (one host or GHA)     │
│                        │  ◀───────────  │                        │
│  collect → push        │    data/       │  pull → LLM → push     │
│                        │                │                        │
│  no API key            │                │  needs ANTHROPIC_API_KEY│
└────────────────────────┘                └────────────────────────┘
```

## Setup

```bash
cd claude-journal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Once you have a private GitHub repo:
git remote add origin git@github.com:<you>/claude-journal.git
git push -u origin main
```

## Layer 1 — Device agent

Runs on every device. No LLM, no API key. Just collect sessions → git push.

```bash
./device-agent.sh                # today
./device-agent.sh 2026-04-22     # specific date
```

What it does:
- Walks `~/.claude/projects/*/*.jsonl` (Claude Code)
- Walks `~/Library/Application Support/Claude/local-agent-mode-sessions/*/*/local_*.json` (Cowork)
- Applies `config/hard_exclude.yaml` blocklist (ESD, wedding, family, trading, PII)
- Writes `raw/sessions-YYYY-MM-DD.json`
- Commits + pushes

Device slug is auto-detected from `hostname -s`, or set `DEVICE_SLUG=...` to override.

### Schedule it

**macOS (launchd):**
```bash
# ~/Library/LaunchAgents/com.francis.claude-journal.plist
# See §7 of PRD for the full plist — runs 13:00 + 23:00 local
launchctl load ~/Library/LaunchAgents/com.francis.claude-journal.plist
```

**Linux (cron):**
```
0 13,23 * * * cd /path/to/claude-journal && ./device-agent.sh >> ~/.claude-journal.log 2>&1
```

## Layer 2 — Synthesis agent

Runs centrally. Two options:

### Option A (recommended): GitHub Actions

Zero device dependency. Triggered automatically on every device push.

1. Push the repo to a private GitHub repo
2. Repo → Settings → Secrets → Actions → add `ANTHROPIC_API_KEY`
3. Workflow `.github/workflows/synthesize.yml` does the rest

The workflow runs on:
- **push** to `raw/sessions-*.json` — every device push triggers a rebuild
- **schedule** — 21:00 UTC + 07:00 UTC safety net (~14:00 PT + 00:00 PT)
- **workflow_dispatch** — manual backfill via GitHub UI, optional date input

Concurrency is deduplicated (newer pushes cancel in-flight runs).

### Option B: local

Runs on one designated host. Needs `ANTHROPIC_API_KEY` in env.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
./synthesis-agent.sh                # today
./synthesis-agent.sh 2026-04-22     # specific date
```

What it does:
- Pulls latest raw pushes from all devices
- Per-session summarize (Haiku): Goal / Decisions / Open / Files
- Daily synthesis (Sonnet): summary + honest one-liner + lessons learned
- Writes `data/day.json`
- Commits + pushes

## Reader

After either agent has produced `data/day.json`, open `reader/index.html`:

```bash
python3 -m http.server 8000
# open http://localhost:8000/reader/
```

## Local dev — run both layers together

```bash
./build.sh    # collect + summarize + synthesize, no git
```

Useful when iterating on prompts or topic keywords.

## File layout

```
claude-journal/
├── device-agent.sh           # Layer 1
├── synthesis-agent.sh        # Layer 2
├── build.sh                  # local dev: both layers, no git
├── reader/index.html         # the Obsidian UI
├── scripts/
│   ├── collect.py            # Layer 1 core
│   ├── summarize.py          # Layer 2 — per session
│   └── synthesize.py         # Layer 2 — per day
├── config/
│   ├── topics.yaml
│   └── hard_exclude.yaml
├── raw/sessions-DATE.json    # device agent output (git-tracked)
└── data/day.json             # synthesis agent output (git-tracked)
```

## Known MVP gaps

- **Claude.ai** not integrated — `recent_chats` headless is still an open M0 question
- **Encryption** — git-crypt not installed yet; raw/ is currently plaintext
- **Retention** — no pruning of old raw/ files
- **Meta-sessions** — if a session's content itself mentions blocklist terms (e.g. this Claude Journal PRD mentions "ESD" in the exclude rules), it gets filtered out. Workaround: rename the session's cwd, or add an allow-list override.
