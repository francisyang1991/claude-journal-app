# Multi-device deployment — checklist

This is the path from "running on my Mac" to "all my devices push, one synthesis runs anywhere, I read from any device." Estimated: half a day.

Status meaning:
- ✅ done by the agent
- 🟡 ready to run (Francis-step)
- ⏳ depends on prior

## 1 — Code/data split (one-time, on your Mac)

| Step | Status | Command / action |
|---|---|---|
| Create `claude-journal-app` repo (public) on GitHub | 🟡 | `gh repo create <you>/claude-journal-app --public` |
| Create `francis-journal-data` repo (private) on GitHub | 🟡 | `gh repo create <you>/francis-journal-data --private` |
| Run the migration (moves `raw/`, `data/`, `memory/`, user configs out of code repo) | 🟡 | Follow `docs/migration.md` Step 2 |
| Set `CLAUDE_JOURNAL_DATA_DIR` in `~/.zshrc` | 🟡 | `export CLAUDE_JOURNAL_DATA_DIR=~/francis-journal-data` |
| Verify: `./build.sh` writes into the data repo, not the code repo | 🟡 | `ls $CLAUDE_JOURNAL_DATA_DIR/raw/` should show today's session file |

## 2 — Onboard your second device (work laptop / Linux)

| Step | Status | Command |
|---|---|---|
| `git clone <app-repo>` to `~/claude-journal-app` | 🟡 | (public, no auth) |
| `git clone <data-repo>` to `~/francis-journal-data` | 🟡 | needs SSH key on GitHub for this device |
| Add `CLAUDE_JOURNAL_DATA_DIR` to that device's shell rc | 🟡 | same as Mac |
| Run `./device-agent.sh` once to test | 🟡 | should pull, collect, push without conflicts |

Repeat for any third device.

## 3 — Schedule the device agent on each device

Choose one based on OS:

**macOS (launchd):**
```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.francis.claude-journal.plist <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.francis.claude-journal</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd ~/claude-journal-app && ./device-agent.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/tmp/claude-journal.log</string>
  <key>StandardErrorPath</key><string>/tmp/claude-journal.err</string>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/com.francis.claude-journal.plist
```

**Linux (cron):**
```cron
0 13,23 * * * cd ~/claude-journal-app && ./device-agent.sh >> ~/.claude-journal.log 2>&1
```

## 4 — Run the synthesis agent (one host, anywhere)

You have two paths:

### Option A — designated laptop (simplest)

Pick one device (your Mac is fine). Add to its crontab:
```cron
30 13,23 * * * cd ~/claude-journal-app && GLM_API_KEY=... ./synthesis-agent.sh >> ~/.claude-journal-synth.log 2>&1
```
Runs 30 min after each device-agent window so all devices have pushed.

### Option B — GitHub Actions (no device dependency)

The workflow file is already in the repo at `.github/workflows/synthesize.yml`. To activate:

1. In GitHub: Settings → Secrets and variables → Actions, add `GLM_API_KEY` (or `ANTHROPIC_API_KEY`)
2. Optionally set `LLM_PROVIDER` as a repo variable to force which is used
3. The workflow runs on every push to `raw/**` (debounced)

GitHub Actions is preferable when you have 2+ devices because synthesis runs even if all your laptops are asleep.

## 5 — Read from any device

Once data is syncing, on any device:
```bash
cd ~/claude-journal-app
git -C ~/francis-journal-data pull   # get the latest
python3 -m http.server 8765
open http://localhost:8765/reader/
```

The reader's date pager (`← prev | next →`) walks the full history. The Threads view shows the long-term memory across every device that contributed.

## 6 — Optional: install the SessionStart coach hook on every device

```bash
cd ~/claude-journal-app
./scripts/install-hook.sh
```

Each device's coach reads from the locally-pulled data repo, so the hook works offline. As long as `git pull` runs sometimes (manually or via cron), threads stay fresh.

## 7 — Optional: onboard Yuwen

```bash
cd ~/claude-journal-app
./scripts/new-user.sh ~/yuwen-journal-data cursor
# Yuwen creates her own private GitHub repo, pushes, sets her CLAUDE_JOURNAL_DATA_DIR
```

She gets a fully isolated journal using only the Cursor adapter. See `docs/migration.md` Step 6 for the full flow.

## 8 — Optional: shared household-vault for skills + plans

Create one more private repo (`household-vault`), invite Yuwen as collaborator. See `docs/migration.md` Step 7.

---

**You don't need to do all of this at once.** The minimum viable next step is Step 1 + a single working second device. Everything else can land later.
