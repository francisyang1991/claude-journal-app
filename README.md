# Claude Journal — MVP

Obsidian-paper daily reader for Claude Code + Cowork sessions.

## One-time setup

```bash
cd claude-journal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."   # add to ~/.zshrc to persist
```

## Daily run

```bash
./build.sh                  # today
./build.sh 2026-04-22       # specific date
```

Then open `reader/index.html` in a browser. (Double-click it, or serve the folder with `python3 -m http.server 8000` and visit http://localhost:8000/reader/.)

## Pipeline

1. **`scripts/collect.py`** — walks `~/.claude/projects/*/*.jsonl` (Claude Code) and `~/Library/Application Support/Claude/local-agent-mode-sessions/*/*/local_*.json` (Cowork). Applies `config/hard_exclude.yaml` blocklist, guesses topic from keywords, writes `raw/sessions-DATE.json`.
2. **`scripts/summarize.py`** — calls Anthropic API (Haiku) per session, extracts Goal / Decisions / Open / Files → `raw/summaries-DATE.json`.
3. **`scripts/synthesize.py`** — one Anthropic API call (Sonnet) to produce day-level summary, honest line, lessons learned → `data/day.json`.
4. **`reader/index.html`** — fetches `data/day.json`, renders Obsidian variant.

## Scope — what's in, what's out

**In:** Claude Code, Cowork, hard-exclude filter, Anthropic-backed summaries, Obsidian reader with decisions/open/lessons rails.

**Out (deferred):**
- Claude.ai (needs `recent_chats` headless verification — PRD M0)
- Git sync + multi-device
- Twice-daily cron + launchd
- Encryption (git-crypt)
- Retention pruning

## Config

- `config/topics.yaml` — keyword hints for topic labeling
- `config/hard_exclude.yaml` — blocklist (ESD, wedding, family, trading, PII regex)

## Cost note

Per day: one Haiku call per kept session (~$0.001 each) + one Sonnet call (~$0.02). Heavy day (~20 sessions) ≈ $0.04.
