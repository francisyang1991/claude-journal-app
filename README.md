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

# Pick a provider (GLM 4.5-flash is currently free):
export GLM_API_KEY="..."               # Z.AI GLM — glm-4.5-flash + glm-4.5-air
# OR
export ANTHROPIC_API_KEY="sk-ant-..."  # Claude — Haiku + Sonnet

# Once you have a private GitHub repo:
git remote add origin git@github.com:<you>/claude-journal.git
git push -u origin main
```

### LLM provider choice

The synthesis agent is provider-agnostic via `scripts/llm.py`. Pick one:

| Env var | Provider | Endpoint | Per-session (cheap) | Daily (quality) |
|---|---|---|---|---|
| `GLM_API_KEY` | Z.AI GLM | `https://api.z.ai/api/paas/v4/` | `glm-4.5-flash` (free tier) | `glm-4.5-air` |
| `ANTHROPIC_API_KEY` | Anthropic | — | `claude-haiku-4-5-20251001` | `claude-sonnet-4-6` |

Overrides:
- `LLM_PROVIDER=glm|anthropic` — force provider when both keys set
- `LLM_MODEL=<name>` — override the model for the current run
- `GLM_BASE_URL=...` — use the China-facing endpoint (`open.bigmodel.cn`) or a self-hosted one
- `GLM_THINKING=1` — enable GLM 4.5's thinking mode (off by default; it burns output tokens as reasoning)

GLM 4.5-flash is currently free-tier and produces good enough summaries for per-session extraction. Start with GLM; upgrade to Anthropic if you need better prose on the daily synthesis.

### Secret scrubbing

The collector regex-redacts common API-key patterns (`sk-ant-...`, `sk-proj-...`, `AKIA...`, GLM `{32hex}.{16alphanum}`, GitHub tokens, SHA-1-shaped hex) from every message before writing to `raw/`. Replaced with `[REDACTED:<kind>]` inline. This is belt-and-suspenders on top of the hard-exclude blocklist — if you paste a key into a Claude session, it won't leak into git.

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

### Daily background pull (every device)

So the reader and coach.py always see fresh data without you remembering to pull. Devices that already run device-agent on a schedule pull as a side effect; this is for read-only devices or as an extra morning refresh.

**macOS:** `~/Library/LaunchAgents/com.francis.journal-pull.plist` running at 07:00 — see [docs/multi-device-checklist.md §5](docs/multi-device-checklist.md) for the exact plist.

**Linux:**
```cron
0 7 * * * cd /path/to/claude-journal && ./scripts/sync.sh >> ~/.journal-sync.log 2>&1
```

`./scripts/sync.sh` is a one-liner: `git -C $CLAUDE_JOURNAL_DATA_DIR pull --rebase --autostash`.

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

## Thread tracking + coach (optional)

Each synthesis run builds `memory/threads.json` — a cross-day view of persistent decisions and open items with a state machine (`active` → `stale` at 7d → `abandoned` at 30d, or `resolved` when a later decision closes an earlier open).

### Coach at session start

Install the Claude Code hook to get a "carry over" preamble in every new session:

```bash
./scripts/install-hook.sh             # installs to ~/.claude/settings.json
./scripts/install-hook.sh --project   # installs to ./.claude/settings.json
./scripts/install-hook.sh --print     # print the JSON snippet, don't install
```

When you open Claude Code in a repo with live threads, you'll see:

```
── Coach (claude-journal) · cadence · last journal entry: today ──
Open threads in this repo:
  ● How to handle plan upload and calendar integration
       last touched today · active
Coach asks:
  → Heads up: *How to handle plan upload and calendar integration* — still want to close this today?
```

Coach is fast (local files only, no LLM, ~50ms), silent when there's nothing to show, and never blocks your session if something goes wrong.

### Optional: better matching with embeddings

Thread linking uses a hybrid word + character n-gram Jaccard matcher by default (stdlib only, catches paraphrases like "competitive analysis" ↔ "competitor analysis"). For true semantic matching across unrelated vocabulary — install `fastembed`:

```bash
pip install fastembed
```

On the next `./build.sh` or synthesis run, thread matching will automatically use `BAAI/bge-small-en-v1.5` (~130MB one-time model download, runs locally, no API key needed). Override model with `FASTEMBED_MODEL=...`.

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
