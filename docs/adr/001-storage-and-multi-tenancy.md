# ADR-001: Storage + multi-tenancy architecture

**Status:** Proposed — revised 2026-04-24 to add shared vault
**Date:** 2026-04-24
**Deciders:** Francis (sole maintainer today; Yuwen as prospective second user using Cursor)

## Context

The current MVP stores everything in one git repo `claude-journal/`:
- Code (scripts, reader, config)
- Data (raw sessions, day digests)
- Future memory artifacts (threads.json, digests, events)

Two constraints now force a revisit:

1. **Multi-user (v2 requirement):** Yuwen would set up her own instance for her own memory. Her data must be completely private — not visible to Francis, not mixed into his repo.
2. **Simpler write model:** Writes happen in a **daily batch** (one synthesis run per day writes all state files). This eliminates the need for an event-sourcing log with runtime compaction.

### Forces at play

- **Operational simplicity** is the highest-priority non-functional requirement. Solo maintainer, spare-time project. Every moving part must earn its keep.
- **Privacy via separation** is non-negotiable for multi-user. Shared code, separate data.
- **Offline-capable local-first reads** are required. Coach hook and reader must work without internet.
- **Code updates should propagate easily** across users. Bug fix in the summarizer shouldn't require Yuwen to hand-merge.
- **No encryption in v1** — relying on GitHub private-repo access control for confidentiality.

## Decision

**Adopt a three-repo model: one shared code repo, one private data repo per user, one shared vault repo for cross-user artifacts.**

Each user has up to three git checkouts on each of their devices:

- **`claude-journal-app/`** — shared code. Same for every user. Contains scripts, source adapters, reader, workflow templates. Updated via `git pull`. Public or semi-public.
- **`<user>-journal-data/`** — private memory. One per user. Raw sessions, per-day digests, thread state, AI reflections. Contents of what the person did with their AI tools. Never shared.
- **`<shared-vault>/`** *(optional)* — authored content shared between users (e.g., Francis + Yuwen). Skills, plans, shared knowledge. Distinct from any user's private memory. Can be read-write by a small, explicit set of GitHub collaborators.

The app reads three environment variables:
- `CLAUDE_JOURNAL_DATA_DIR` — path to the current user's private data repo
- `CLAUDE_JOURNAL_SHARED_DIR` *(optional)* — path to the shared vault if the user participates in one
- `CLAUDE_JOURNAL_APP_DIR` — path to the app repo (usually where the script runs from)

## Options considered

### Option A — One shared repo, per-user subdirectories

```
claude-journal/
├── scripts/
├── reader/
├── users/
│   ├── francis/
│   │   ├── raw/
│   │   └── memory/
│   └── yuwen/
│       ├── raw/
│       └── memory/
```

| Dimension | Assessment |
|---|---|
| Complexity | Low (one repo, one place) |
| Cost | $0 |
| Privacy | ❌ Fails — any collaborator on the repo sees all users' content. GitHub cannot enforce per-path read permissions. |
| Code updates | Trivial (both users pull from same repo) |
| Scalability | Fine to ~dozens of users |
| Onboarding | Easy (add a subdir) |

**Pros:** Dead-simple code-update story.
**Cons:** Privacy model fundamentally broken. Yuwen doesn't want Francis to be able to `git clone` her wedding thread list. This disqualifies the option.

### Option B — One repo per user, code vendored inside each

Today's structure, just replicated. Each user has `<user>-claude-journal/` containing both scripts and data.

| Dimension | Assessment |
|---|---|
| Complexity | Medium (duplicated code across users) |
| Cost | $0 |
| Privacy | ✅ Fully isolated (separate private repos) |
| Code updates | ❌ Painful — every bug fix requires hand-merging into each user's repo or running a syncing script |
| Scalability | Fine, but maintenance cost scales linearly with users |
| Onboarding | Moderate (fork + remove other user's data) |

**Pros:** Privacy solved.
**Cons:** Code divergence is inevitable when two users both commit to their own forks. Upstream fix becomes a merge conflict. This is the mess dotfile users avoid with the app/data split.

### Option C — Remote multi-tenant DB (Turso / Supabase / Postgres + RLS)

Structured state in a hosted SQL database with row-level security by user.

| Dimension | Assessment |
|---|---|
| Complexity | High (schema, migrations, auth) |
| Cost | $5–20/mo per user (Turso free tier limits, Supabase free tier) |
| Privacy | ✅ RLS-enforced |
| Code updates | Medium (migrations + app pulls) |
| Offline | ❌ Breaks — coach hook needs internet at session start |
| Onboarding | Credentials per user, schema migration per user |
| Vendor lock | High |

**Pros:** Proper multi-tenancy, fast queries.
**Cons:** Violates offline constraint, introduces auth system, monthly cost, vendor lock-in. Each of these individually is tolerable; together they blow past "operational simplicity."

### Option D — App/data split + optional shared vault (recommended)

Three-repo pattern, extending the classic dotfile/vault split with an explicit shared-content repo between collaborators.

```
claude-journal-app/              ← shared code, public or semi-public
├── scripts/
│   ├── collect.py
│   ├── sources/                 ← pluggable source adapters (see ADR-002)
│   │   ├── claude_code.py
│   │   ├── cowork.py
│   │   ├── cursor.py
│   │   └── codex.py
│   ├── summarize.py
│   ├── synthesize.py
│   └── new-user.sh              ← scaffolds an empty data repo
├── reader/
├── .github/workflows/
└── docs/

francis-journal-data/            ← Francis's private data (Claude Code + Cowork)
├── config/
│   ├── sources.yaml             ← enabled: [claude-code, cowork]
│   ├── topics.yaml              ← user-specific
│   └── hard_exclude.yaml        ← user-specific
├── raw/
│   └── sessions-*.json
├── data/
│   └── day.json
└── memory/
    ├── threads.json
    ├── answers-pending-*.json   ← cleaned up by synthesizer each day
    └── digests/

yuwen-journal-data/              ← Yuwen's private data (Cursor on her laptop)
├── config/
│   ├── sources.yaml             ← enabled: [cursor]
│   └── topics.yaml
├── raw/
├── data/
└── memory/

shared-vault/                    ← shared authored content (Francis + Yuwen)
├── skills/                      ← reusable knowledge
│   ├── award-booking.md
│   └── interview-prep-playbook.md
├── plans/                       ← future intentions
│   ├── 2026-taipei-trip.md
│   └── 2026-wedding-logistics.md
├── knowledge/                   ← shared references
│   └── household-accounts.md
└── README.md
```

Every script takes `CLAUDE_JOURNAL_DATA_DIR` from env (or a `--data-dir` flag) for the user's private repo, and optionally reads the shared vault via `CLAUDE_JOURNAL_SHARED_DIR`. Never mixes paths.

### The shared vault — scope and non-scope

**In scope:**
- Authored content both users want to reference — skills, plans, knowledge bases
- Read-write by both users (they're collaborators on the repo)
- Manually curated (no auto-routing from private memory in v1)
- Can be referenced from either user's private synthesis pipeline (e.g., "add context from `shared-vault/plans/2026-taipei-trip.md` when synthesizing a travel topic session")

**Out of scope for v1:**
- Auto-promoting sessions from private to shared (privacy risk, user-agency risk)
- Cross-user thread tracking ("Francis + Yuwen both have an open 'Taipei routing' thread")
- Shared coach questions ("ask both users about the wedding")
- Skill extraction from sessions (future: LLM detects "this session discovered a reusable pattern" → drafts a skill → user approves manually)

If a private session produces something worth sharing, v1 answer is: user manually copies it over. Friction is the feature — it forces explicit decisions about what's shared.

| Dimension | Assessment |
|---|---|
| Complexity | Low-medium (two or three clones per device, 2–3 env vars) |
| Cost | $0 |
| Privacy | ✅ Separate private repos; GitHub access control is the isolation boundary |
| Code updates | ✅ `git pull` in the app repo; instant propagation to all users |
| Offline | ✅ Fully local reads; writes queue and push when online |
| Onboarding | Clone app + create empty data repo + set env var → done. Shared vault is opt-in. |
| Vendor lock | Low (git is portable) |
| Scalability | N users = N data repos; zero code duplication |
| Collaboration | Shared vault enables explicit, low-friction sharing between trusted users |

**Pros:** Clean code/data separation; privacy by separate repos; bug fixes propagate instantly; Yuwen sets up in 5 minutes; shared vault supports household-level knowledge without compromising per-user privacy.
**Cons:** Two–three checkouts instead of one. Requires discipline: no user-specific values in the app repo; no private content in the shared vault.

## Trade-off analysis

The core trade-off is **operational complexity vs privacy model**:

- Option A: simplest, no privacy
- Option B: privacy by duplication, divergence debt
- Option C: proper multi-tenancy, breaks offline + adds cost
- Option D: privacy by separation, one extra clone

For this system's scale and shape (one maintainer, ≤5 users ever, personal data, offline-first reads), the app/data split is dominant:
- It matches the "code is code, data is data" mental model.
- It scales trivially to N users with zero code changes.
- It's the exact pattern Obsidian, Logseq, and every dotfile manager has converged on.
- Migration from today's structure is mechanical (split the repo).

## Daily-batch write model

Given writes are daily, the design simplifies versus the event-sourced variant:

```
Day T, during the day — on any device:
  - coach.py reads local data repo (cached from last pull)
  - user answers a coach question → appended to
    memory/answers-pending-{device}.json  (local-only, not committed yet)

Day T, end of day — on the designated synthesis host (laptop or GHA):
  - git pull (picks up each device's session commits + pending answers)
  - run synthesize.py:
      consume all answers-pending-*.json files
      update memory/threads.json (single writer)
      write data/day.json, memory/digests/*.md
      delete consumed answers-pending-*.json
  - git commit + push (one commit per day)

Day T+1, on any device:
  - git pull (if >15min stale)
  - fresh threads.json visible to coach.py
```

No event log. No runtime compaction. No race conditions because:
- Devices only append uniquely-named files (`answers-pending-{device}.json`)
- The synthesis agent has single-writer access to the compacted state files
- One push per day per user → conflict surface is minimal

## Consequences

**What becomes easier:**
- Onboarding a new user: fork app repo + create new private data repo + `git clone` both.
- Pushing a bug fix: merge to `claude-journal-app/main`; all users get it on next `git pull`.
- Privacy guarantees: provable by repo inspection (no cross-user data in any repo).
- Device onboarding: same steps as user onboarding minus the repo creation.
- Local SQLite index (from system-design doc) stays per-device in `~/.cache/claude-journal/` — data repo has the canonical state, index is cheap to rebuild.

**What becomes harder:**
- Users must keep two checkouts in sync on each device.
- Shared features between users (e.g., "Yuwen and Francis share a travel thread") need an explicit cross-repo mechanism. **Out of scope for v1.**
- Coordinating breaking changes in data shape: the app must support reading old schemas, or prompt users to run a migration script in their data repo.
- First-time setup is slightly more involved than a single clone.

**What we'll need to revisit:**
- **Encryption:** with separate repos, GitHub access control is the isolation boundary. If a user ever wants to self-host on an untrusted server, or the threat model expands (device theft with disk unencrypted), we add `git-crypt` per data repo. Do not add it until required.
- **Shared threads:** if users want to collaborate on shared memory (rare), add an optional `shared/` data repo both can access.
- **Schema migrations:** today schema is implicit in JSON shape. Once 2+ users exist, we need versioned schemas and a `migrate.py` script in the app repo.
- **Config portability:** `topics.yaml` and `hard_exclude.yaml` will diverge per user. They must live in the data repo, not the app repo.

## Action items

1. [ ] Rename current `claude-journal/` to `claude-journal-app/` (or create new app repo)
2. [ ] Move `raw/`, `data/`, `memory/` (future), `config/topics.yaml`, `config/hard_exclude.yaml` into a new `francis-journal-data/` private repo
3. [ ] Update all scripts to honor `CLAUDE_JOURNAL_DATA_DIR` env var (default: `$PWD` for dev)
4. [ ] Update `device-agent.sh` and `synthesis-agent.sh` to `cd "$CLAUDE_JOURNAL_DATA_DIR"` for git operations
5. [ ] Create `docs/setup.md` describing the two-clone installation flow
6. [ ] Create `scripts/new-user.sh` that scaffolds a fresh data repo (empty raw/, empty memory/, starter config/)
7. [ ] Document per-device setup: clone both repos, set env var, run `device-agent.sh` once
8. [ ] Revisit encryption (git-crypt) before Yuwen onboards if her data includes anything she considers sensitive (likely yes — wedding, family, health)

## Migration path (1 afternoon)

```bash
# From current claude-journal/
mkdir ../francis-journal-data
git mv raw data memory config/topics.yaml config/hard_exclude.yaml ../francis-journal-data/
cd ../francis-journal-data && git init && git remote add origin git@github.com:francis/journal-data.git

cd ../claude-journal-app
# Add CLAUDE_JOURNAL_DATA_DIR handling to all scripts
# Update device-agent.sh and synthesis-agent.sh to cd into the data dir for git ops
```
