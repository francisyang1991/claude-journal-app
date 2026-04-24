"""Cursor source adapter.

Reads Cursor's Composer chat history from the globalStorage SQLite DB.
Schema (as of 2025-2026, confirmed by inspection):

  Location: ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
  Table:    cursorDiskKV
  Keys:
    composerData:{composerId}       → conversation metadata + bubble order
    bubbleId:{composerId}:{bubbleId} → individual messages

  Composer JSON:
    { composerId, name, createdAt (ms), lastUpdatedAt (ms),
      fullConversationHeadersOnly: [{ bubbleId, type }]   (type: 1=user, 2=assistant)
    }

  Bubble JSON:
    { type (1|2), bubbleId, text }

Workspace → cwd mapping (best-effort): each workspace DB at
workspaceStorage/{hash}/state.vscdb has ItemTable[composer.composerData]
listing composerIds, and workspace.json in the same dir has the folder URI.

Limitation: Cursor Pro's "privacy mode" disables local persistence; this
adapter sees an empty DB in that case.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from ._base import RawSession, device_slug, local_day_bounds, scrub_secrets, truncate_messages

HOME = Path.home()

# Candidate paths across platforms
_GLOBAL_CANDIDATES = [
    HOME / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb",
    HOME / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb",
    HOME / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage" / "state.vscdb",
]
_WORKSPACE_CANDIDATES = [
    HOME / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage",
    HOME / ".config" / "Cursor" / "User" / "workspaceStorage",
    HOME / "AppData" / "Roaming" / "Cursor" / "User" / "workspaceStorage",
]


def _global_db() -> Path | None:
    for p in _GLOBAL_CANDIDATES:
        if p.exists():
            return p
    return None


def _workspace_root() -> Path | None:
    for p in _WORKSPACE_CANDIDATES:
        if p.exists():
            return p
    return None


def _open_ro(path: Path) -> sqlite3.Connection:
    # Open read-only to avoid locking a DB Cursor has open
    uri = f"file:{path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _build_composer_cwd_map() -> dict[str, str]:
    """Map composerId → cwd (filesystem path). Best-effort; missing entries return empty string."""
    root = _workspace_root()
    if not root:
        return {}
    out: dict[str, str] = {}
    for wsdir in root.glob("*/"):
        ws_json = wsdir / "workspace.json"
        ws_db = wsdir / "state.vscdb"
        if not ws_json.exists() or not ws_db.exists():
            continue
        try:
            folder_uri = json.loads(ws_json.read_text()).get("folder") or ""
            cwd = unquote(urlparse(folder_uri).path) if folder_uri.startswith("file://") else folder_uri
        except Exception:
            cwd = ""
        if not cwd:
            continue
        try:
            conn = _open_ro(ws_db)
            cur = conn.execute(
                "SELECT value FROM ItemTable WHERE key = 'composer.composerData'"
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                continue
            data = json.loads(row[0])
            for c in data.get("allComposers", []):
                cid = c.get("composerId")
                if cid and cid not in out:
                    out[cid] = cwd
        except Exception:
            continue
    return out


class Cursor:
    name = "cursor"
    label = "Cursor"

    def is_available(self) -> bool:
        return _global_db() is not None

    def collect(self, date: str) -> list[RawSession]:
        db_path = _global_db()
        if not db_path:
            return []
        day_start, day_end = local_day_bounds(date)
        day_start_ms = int(day_start.timestamp() * 1000)
        day_end_ms = int(day_end.timestamp() * 1000)

        cwd_map = _build_composer_cwd_map()

        try:
            conn = _open_ro(db_path)
        except sqlite3.OperationalError as e:
            print(f"[cursor] cannot open DB: {e}", file=sys.stderr)
            return []

        out: list[RawSession] = []
        try:
            # Pull every composer; filter by lastUpdatedAt (or createdAt) in Python.
            cur = conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            )
            composers = []
            for key, raw in cur:
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                last_ms = d.get("lastUpdatedAt") or d.get("createdAt")
                if not last_ms:
                    continue
                if not (day_start_ms <= last_ms < day_end_ms):
                    continue
                composers.append(d)

            for c in composers:
                composer_id = c.get("composerId")
                if not composer_id:
                    continue
                headers = c.get("fullConversationHeadersOnly") or []
                if not headers:
                    continue

                # Fetch all bubbles for this composer in one query
                bubble_ids = [h.get("bubbleId") for h in headers if h.get("bubbleId")]
                if not bubble_ids:
                    continue
                placeholders = ",".join("?" * len(bubble_ids))
                keys = [f"bubbleId:{composer_id}:{bid}" for bid in bubble_ids]
                bcur = conn.execute(
                    f"SELECT key, value FROM cursorDiskKV WHERE key IN ({placeholders})",
                    keys,
                )
                by_id: dict[str, dict] = {}
                for k, v in bcur:
                    if not v:
                        continue
                    try:
                        bd = json.loads(v)
                    except Exception:
                        continue
                    bid = bd.get("bubbleId") or k.rsplit(":", 1)[-1]
                    by_id[bid] = bd

                msgs: list[dict] = []
                for h in headers:
                    bid = h.get("bubbleId")
                    bd = by_id.get(bid)
                    if not bd:
                        continue
                    t = bd.get("type")
                    role = "user" if t == 1 else "assistant" if t == 2 else None
                    text = (bd.get("text") or "").strip()
                    if role and text:
                        msgs.append({"role": role, "text": text})

                if not msgs:
                    continue

                created_ms = c.get("createdAt") or c.get("lastUpdatedAt")
                ts_first = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
                ts_last = datetime.fromtimestamp(
                    (c.get("lastUpdatedAt") or created_ms) / 1000, tz=timezone.utc
                ).isoformat()

                title = c.get("name")
                if not title:
                    # Fallback: first 80 chars of first user message
                    first_user = next((m["text"] for m in msgs if m["role"] == "user"), "")
                    title = first_user[:80] if first_user else f"Cursor {composer_id[:8]}"

                out.append(RawSession(
                    session_id=composer_id,
                    source=self.name,
                    device=device_slug(),
                    cwd=cwd_map.get(composer_id),
                    title=scrub_secrets(title),
                    started_at=ts_first,
                    ended_at=ts_last,
                    messages=truncate_messages(msgs),
                ))
        finally:
            conn.close()

        return out
