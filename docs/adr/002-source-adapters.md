# ADR-002: Source adapter architecture

**Status:** Proposed
**Date:** 2026-04-24
**Deciders:** Francis
**Related:** ADR-001 (three-repo model)

## Context

The current MVP has source collection hardcoded in `scripts/collect.py`:
- `extract_claude_code_sessions()` walks `~/.claude/projects/*/*.jsonl`
- `extract_cowork_sessions()` walks `~/Library/Application Support/Claude/local-agent-mode-sessions/*/*/local_*.json`

This worked for a single user using Claude Code + Cowork. It does not work for:

- **Yuwen using Cursor.** Cursor stores chat history in SQLite DBs under `~/Library/Application Support/Cursor/User/workspaceStorage/*/state.vscdb`. Totally different format.
- **Future users adding Codex CLI, Cline, Aider, Windsurf.** Each has its own log format.
- **Per-user source selection.** Francis's sources ≠ Yuwen's sources. The collector must know which sources to attempt for a given data repo.
- **Contributors.** Open-sourcing the app repo invites contributions. Adding Cursor support shouldn't require editing `collect.py`; it should be a new file.

## Decision

**Factor source collection into pluggable adapters. One file per source. Each user's data repo declares which adapters it uses.**

### Adapter contract

```python
# scripts/sources/_base.py
from dataclasses import dataclass
from typing import Protocol

@dataclass
class RawSession:
    session_id: str
    source: str                  # e.g. "claude-code", "cursor"
    device: str
    started_at: str              # ISO 8601 UTC
    ended_at: str
    cwd: str | None
    title: str
    messages: list[dict]         # [{"role": "user"|"assistant", "text": str}, ...]

class SourceAdapter(Protocol):
    name: str                    # stable identifier, e.g. "cursor"
    label: str                   # human name, e.g. "Cursor"

    def is_available(self) -> bool:
        """Return True if this source is installed on this machine."""

    def collect(self, date: str) -> list[RawSession]:
        """Return sessions with activity on the given local date (YYYY-MM-DD)."""
```

### Registry and selection

```python
# scripts/sources/__init__.py
from .claude_code import ClaudeCode
from .cowork import Cowork
from .cursor import Cursor
from .codex import Codex

REGISTRY = {a.name: a for a in [ClaudeCode(), Cowork(), Cursor(), Codex()]}

def get_enabled(data_dir: Path) -> list[SourceAdapter]:
    config = yaml.safe_load((data_dir / "config/sources.yaml").read_text())
    enabled = config.get("sources", [])
    return [REGISTRY[name] for name in enabled if name in REGISTRY]
```

Data repo declares its sources:

```yaml
# francis-journal-data/config/sources.yaml
sources:
  - claude-code
  - cowork

# yuwen-journal-data/config/sources.yaml
sources:
  - cursor
```

### Collector simplifies to

```python
# scripts/collect.py
for adapter in sources.get_enabled(data_dir):
    if not adapter.is_available():
        print(f"[collect] {adapter.name}: not installed on this device", file=sys.stderr)
        continue
    sessions.extend(adapter.collect(date))
```

One loop. Each adapter is independently testable, independently installable, independently versioned in its own file.

## Options considered

### Option A — Keep everything in `collect.py` (today)

A single file with branches per source.

**Pros:** Simple until it isn't. Zero abstraction tax.
**Cons:** File grows linearly with supported sources. Every new source is a merge risk. Can't disable sources per user without runtime flags. Contributor friction is high — "where do I add Cursor?" has no obvious answer.

**Breaks at:** 3rd source or 2nd user.

### Option B — Source adapters as separate files, auto-registered (recommended)

What's described above. Each adapter is a file in `scripts/sources/`. A registry maps `name → adapter`. The data repo's `sources.yaml` selects which to run.

**Pros:**
- New source = new file. No editing existing code.
- Per-user selection is declarative (one YAML line).
- Each adapter has its own tests without touching others.
- Natural contribution model (PR a new file).
- `is_available()` lets an adapter self-skip if the tool isn't installed on a device.

**Cons:**
- Slightly more code (registry, base class).
- Requires discipline: adapters must produce `RawSession` objects with the same shape. A test suite covering the contract is worth writing.

### Option C — Subprocess-based adapters (any language)

Adapters are shell scripts or binaries that emit JSON lines on stdout. Python just shells out.

**Pros:** Polyglot. Someone could write a Rust adapter for a tool whose logs are most easily parsed in Rust.
**Cons:** Process overhead (~50ms × N adapters). Harder debugging. Overkill when Python covers everything we'd plausibly need.

**Verdict:** Not worth it today. Keep the door open by having `is_available()` and `collect()` be defined at the interface level — a subprocess adapter could be wrapped in a Python class later.

## Source adapters needed

| Adapter | Status | Path / format | Notes |
|---|---|---|---|
| `claude-code` | ✅ exists (inline in collect.py, needs extraction) | `~/.claude/projects/*/*.jsonl` | JSONL, one event per line |
| `cowork` | ✅ exists (inline in collect.py, needs extraction) | `~/Library/Application Support/Claude/local-agent-mode-sessions/*/*/local_*.json` + `audit.jsonl` | Sidecar JSON metadata + JSONL transcript |
| `cursor` | 🔲 to build | `~/Library/Application Support/Cursor/User/workspaceStorage/*/state.vscdb` (SQLite) | Tables include `ItemTable` keyed by workspace. Chat history in specific keys. Needs sqlite3 (stdlib) + JSON parsing of blob values. |
| `codex` | 🔲 future | `~/.codex/sessions/*.json` (to be confirmed) | |
| `cline` / `windsurf` / `aider` | 🔲 future | various local formats | |
| `claude-ai` (chat) | 🔲 blocked on M0 | `recent_chats` MCP tool from primary device | |

## Implementation plan

1. Extract existing Claude Code and Cowork logic into `scripts/sources/claude_code.py` and `scripts/sources/cowork.py`. Mechanical refactor.
2. Define `scripts/sources/_base.py` with `RawSession` dataclass and `SourceAdapter` protocol.
3. Build `scripts/sources/__init__.py` with the registry + `get_enabled(data_dir)` helper.
4. Rewrite `collect.py` as a thin orchestrator over adapters.
5. Add contract tests: a fixture directory with sample inputs per source, golden output files for `RawSession[]`.
6. Build `cursor.py` adapter (research the SQLite schema first — see "Open questions" below).

## Trade-off analysis

The abstraction tax is real but small (maybe 50 lines of registry/base code). It buys:
- Per-user source selection (a hard requirement after Yuwen onboarded)
- Independent contribution paths (important if the app repo goes semi-public)
- Clean isolation for `is_available()` probing (no more runtime `os.path.exists` branches scattered across a 200-line function)

The alternative — keeping `collect.py` monolithic — breaks the moment a third source or a second user appears. Both are near-term.

## Consequences

**What becomes easier:**
- Adding a new AI tool: one file, one yaml entry.
- Disabling a source on a specific device (e.g., Francis's work laptop has no Cursor).
- Testing in isolation.
- Open-source contributions — "here's how to add your tool" is a 30-minute onboarding.

**What becomes harder:**
- Schema drift between adapters: all must produce `RawSession` with the same fields. Contract tests are required.
- Debugging when an adapter breaks: need clear error messages per adapter, not swallowed into a monolithic try/except.

**What we'll need to revisit:**
- **Adapter versioning.** If Claude Code changes its JSONL format, the adapter needs to handle both old and new. Consider a `schema_version` field on `RawSession`.
- **Cross-source deduplication.** If a session appears in both Cowork and Claude Code (unlikely but possible), we'd need to dedupe. Deferred until observed.
- **Incremental collection.** Today adapters scan the entire date window every run. For high-volume users, add a state file tracking last-collected timestamps per adapter.

## Cursor schema — confirmed by inspection

Research done 2026-04-24 against a live Cursor install. The `cursor-chat-export` tool docs the *old* `ItemTable` key path (`workbench.panel.aichat.view.aichat.chatdata`), which is **deprecated**. Current Cursor (2025–2026) uses the Composer architecture with a different storage table.

### Location

```
~/Library/Application Support/Cursor/User/globalStorage/state.vscdb   # macOS
~/.config/Cursor/User/globalStorage/state.vscdb                        # Linux
%APPDATA%/Cursor/User/globalStorage/state.vscdb                        # Windows
```

Single global SQLite DB per Cursor install. Not per-workspace.

### Tables

- `ItemTable` — small KV pairs (deprecated-style chat data, some config)
- `cursorDiskKV` — the main store. All composer/message data lives here.

### Key prefixes in `cursorDiskKV`

| Prefix | Purpose | Count (sample) |
|---|---|---|
| `composerData:{composerId}` | one row per conversation; metadata + bubble order | 178 |
| `bubbleId:{composerId}:{bubbleId}` | one row per message | 42,798 |
| `messageRequestContext:{...}` | per-message request context (rarely needed) | 975 |
| `checkpointId:{...}` | file checkpoint snapshots | 7,776 |
| `codeBlockDiff:{...}` | code edit diffs | 3,783 |
| `agentKv:blob:{...}` | agent tool-call outputs | 50,883 |

### Composer shape

```json
{
  "_v": 3,
  "composerId": "cf30e0cd-...",
  "name": "<optional title>",
  "createdAt": 1759867821881,        // unix ms
  "lastUpdatedAt": 1759900000000,    // unix ms (present on active composers)
  "fullConversationHeadersOnly": [
    { "bubbleId": "feafd5ef-...", "type": 1 },   // type 1 = user
    { "bubbleId": "9f6041f8-...", "type": 2 },   // type 2 = assistant
    ...
  ],
  "richText": "<lexical JSON>",
  "status": "none",
  "unifiedMode": "chat"
}
```

### Bubble (message) shape

Both types (user and assistant) expose plaintext via `text`:

```json
{
  "_v": 3,
  "type": 1,                          // 1 = user, 2 = assistant
  "bubbleId": "feafd5ef-...",
  "text": "plain-text message content",
  "richText": "<lexical JSON>",       // user bubbles only
  "tokenCount": 1234,
  ...many attachment/context fields (ignored)
}
```

No per-bubble timestamp. Ordering is authoritative via `fullConversationHeadersOnly`.

### Workspace ↔ composer linkage (best effort)

Each workspace has a local DB at `workspaceStorage/{hash}/state.vscdb` containing an `ItemTable` entry at key `composer.composerData` with the list of composerIds used in that workspace. Combined with `workspaceStorage/{hash}/workspace.json` (folder URI), we can map composer → cwd.

For MVP, we record composer `cwd` as best-effort — if the lookup fails, leave blank.

### Cursor "privacy mode" caveat

Cursor Pro's privacy mode disables local chat persistence. In that mode, `cursorDiskKV` is not populated. Adapter must handle empty-DB gracefully and document this limitation in the setup guide for users considering this system.

## Remaining open questions

1. **Cursor composer → cwd mapping.** Iterating all `workspaceStorage/*/state.vscdb` files to build a composerId-to-cwd map is ~100–500 workspaces × one SQLite open per workspace. Acceptable performance at this scale, but cache the map per-run.
2. **Where do we cut off "tool X is in scope"?** IntelliJ AI Assistant, Copilot chat, Zed — each is a candidate. Principle: add adapters only when a real user of this system needs them. Not speculative.
3. **Schema versioning.** Cursor has already changed chat storage once (ItemTable → cursorDiskKV). Expect another migration. The adapter should probe both key shapes and pick whichever has data.

## Action items

1. [ ] Extract Claude Code and Cowork collection out of `collect.py` into `scripts/sources/*.py`
2. [ ] Define `SourceAdapter` protocol + `RawSession` dataclass in `scripts/sources/_base.py`
3. [ ] Registry + `get_enabled()` in `scripts/sources/__init__.py`
4. [ ] Rewrite `collect.py` as orchestrator
5. [ ] Move `config/topics.yaml` + `config/hard_exclude.yaml` from app repo to data repo template
6. [ ] Add `config/sources.yaml` schema — ship with a sensible default for new users
7. [ ] Research Cursor SQLite schema; document findings
8. [ ] Build and test `scripts/sources/cursor.py`
9. [ ] Write golden-test fixtures for each adapter
