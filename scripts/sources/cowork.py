"""Cowork (Claude local-agent-mode) source adapter.

Reads ~/Library/Application Support/Claude/local-agent-mode-sessions/*/*/local_*.json
(sidecar metadata) and the sibling directory's audit.jsonl (transcript).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ._base import RawSession, device_slug, local_day_bounds, scrub_secrets, truncate_messages

COWORK_ROOT = Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"


class Cowork:
    name = "cowork"
    label = "Claude Cowork"

    def is_available(self) -> bool:
        return COWORK_ROOT.exists()

    def collect(self, date: str) -> list[RawSession]:
        if not self.is_available():
            return []
        day_start, day_end = local_day_bounds(date)
        out: list[RawSession] = []

        for meta in COWORK_ROOT.glob("*/*/local_*.json"):
            try:
                md = json.loads(meta.read_text())
            except Exception:
                continue
            last_ms = md.get("lastActivityAt")
            created_ms = md.get("createdAt")
            if not last_ms:
                continue
            last_ts = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
            if not (day_start <= last_ts < day_end):
                continue

            ts_first = datetime.fromtimestamp((created_ms or last_ms) / 1000, tz=timezone.utc).isoformat()
            ts_last = last_ts.isoformat()

            audit = meta.parent / meta.stem / "audit.jsonl"
            msgs: list[dict] = []
            if audit.exists():
                try:
                    with open(audit) as f:
                        for line in f:
                            try:
                                d = json.loads(line)
                            except Exception:
                                continue
                            t = d.get("type") or d.get("role")
                            msg = d.get("message") or {}
                            content = msg.get("content") if isinstance(msg, dict) else None
                            if isinstance(content, list):
                                txt = "\n".join(
                                    c.get("text", "")
                                    for c in content
                                    if isinstance(c, dict) and c.get("type") == "text"
                                )
                            elif isinstance(content, str):
                                txt = content
                            else:
                                txt = d.get("content") if isinstance(d.get("content"), str) else ""
                            if t in ("user", "assistant") and txt and txt.strip():
                                msgs.append({"role": t, "text": txt})
                            if len(msgs) >= 50:
                                break
                except Exception:
                    pass

            out.append(RawSession(
                session_id=md.get("sessionId") or meta.stem,
                source=self.name,
                device=device_slug(),
                cwd=md.get("cwd") or (md.get("userSelectedFolders") or [None])[0],
                title=scrub_secrets(md.get("title") or f"Cowork {meta.stem[:12]}"),
                started_at=ts_first,
                ended_at=ts_last,
                messages=truncate_messages(msgs),
            ))
        return out
