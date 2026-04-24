# Migration: single-repo → app/data split

This guide walks through splitting today's single `claude-journal/` repo into:

- **`claude-journal-app`** — public repo with code
- **`francis-journal-data`** — private repo with your memory
- **`household-vault`** *(later)* — shared with Yuwen

Do this when you're ready to onboard a second device (or Yuwen). Not required for single-user single-device operation — the current repo works as both code and data via `CLAUDE_JOURNAL_DATA_DIR` defaulting to the repo root.

## Step 1 — Create the two GitHub repos

Public repo: `claude-journal-app` (or whatever name you prefer).
Private repo: `francis-journal-data`.

```bash
gh repo create <you>/claude-journal-app --public
gh repo create <you>/francis-journal-data --private
```

## Step 2 — Split the local checkout

From `~/Multiverse/claude-journal/`:

```bash
# Make a new clean data dir
mkdir -p ~/francis-journal-data

# Move user-specific things into the data repo
git mv raw data memory config/topics.yaml config/hard_exclude.yaml config/sources.yaml ~/francis-journal-data/ 2>/dev/null || true

# Commit the removal in the current repo (this becomes the app repo)
git add -A
git commit -m "split: move user data to separate data repo"

# Initialize the data repo
cd ~/francis-journal-data
git init -b main
git add -A
git commit -m "chore: initial import from claude-journal"
git remote add origin git@github.com:<you>/francis-journal-data.git
git push -u origin main

# Point the app at the data repo
cd -   # back to claude-journal
git remote set-url origin git@github.com:<you>/claude-journal-app.git
git push -u origin main
```

## Step 3 — Set the env var

Add to `~/.zshrc`:

```bash
export CLAUDE_JOURNAL_DATA_DIR=~/francis-journal-data
export GLM_API_KEY="..."   # your LLM key
```

Reload: `source ~/.zshrc`.

## Step 4 — Verify

```bash
cd ~/Multiverse/claude-journal    # the app repo
./build.sh                         # should operate on ~/francis-journal-data
ls ~/francis-journal-data/raw/     # fresh data should land here
```

## Step 5 — Onboard a second device (your work laptop)

```bash
# Clone both repos
git clone git@github.com:<you>/claude-journal-app.git ~/claude-journal-app
git clone git@github.com:<you>/francis-journal-data.git ~/francis-journal-data

# Set env var on this device too
echo 'export CLAUDE_JOURNAL_DATA_DIR=~/francis-journal-data' >> ~/.zshrc
source ~/.zshrc

# Run device agent — picks up from this device and pushes
cd ~/claude-journal-app
./device-agent.sh
```

Both devices now write into the same `francis-journal-data` repo. Unique session-id filenames in `raw/` prevent conflicts.

## Step 6 — Onboard Yuwen on her laptop

```bash
# She clones the app (public — no auth)
git clone https://github.com/<you>/claude-journal-app.git ~/claude-journal-app

# Scaffold her data repo locally
cd ~/claude-journal-app
./scripts/new-user.sh ~/yuwen-journal-data cursor

# She creates her own PRIVATE GitHub repo, then:
cd ~/yuwen-journal-data
git remote add origin git@github.com:yuwen/journal-data.git
git push -u origin main

# She sets her env var
echo 'export CLAUDE_JOURNAL_DATA_DIR=~/yuwen-journal-data' >> ~/.zshrc
source ~/.zshrc

# She runs the device agent
cd ~/claude-journal-app
./device-agent.sh
```

She now has a fully isolated journal using only the Cursor adapter. Francis can't read her data repo (GitHub access control), and her source adapter enablement is independent.

## Step 7 — (Optional) Set up the shared household vault

```bash
# You create the shared PRIVATE repo, invite Yuwen as a collaborator
gh repo create <you>/household-vault --private
gh repo edit <you>/household-vault --add-collaborator yuwen

# Both clone it
git clone git@github.com:<you>/household-vault.git ~/household-vault

# Both add to shell:
echo 'export CLAUDE_JOURNAL_SHARED_DIR=~/household-vault' >> ~/.zshrc

# Seed the vault
cd ~/household-vault
mkdir -p skills plans knowledge
cat > README.md <<'EOF'
# Household vault
Shared authored content — skills, plans, references — read-write by both users.
NEVER contains per-user private memory.
EOF
git add -A
git commit -m "chore: seed household vault"
git push
```

## Rollback

If anything goes wrong:

```bash
# Restore from git
cd ~/Multiverse/claude-journal
git reset --hard <commit-before-migration>
rm -rf ~/francis-journal-data
```

The split is fully recoverable as long as you haven't pushed the destructive `git mv` commit upstream.
