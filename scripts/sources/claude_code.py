"""Claude Code source adapter.

Reads ~/.claude/projects/*/*.jsonl — one file per session, JSONL events.
Event types we care about: user, assistant (content), ai-title (title hint),
last-prompt (fallback title).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ._base import RawSession, device_slug, local_day_bounds, scrub_secrets, truncate_messages, within_day

CC_PROJECTS = Path.home() / ".claude" / "projects"


class ClaudeCode:
    name = "claude-code"
    label = "Claude Code"

    def is_available(self) -> bool:
        return CC_PROJECTS.exists()

    def collect(self, date: str) -> list[RawSession]:
        if not self.is_available():
            return []
        day_start, day_end = local_day_bounds(date)
        out: list[RawSession] = []

        for jsonl in CC_PROJECTS.glob("*/*.jsonl"):
            session_id = jsonl.stem
            cwd = None
            title = None
            ts_first = None
            ts_last = None
            msgs: list[dict] = []

            try:
                with open(jsonl) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        t = d.get("type")
                        ts = d.get("timestamp")

                        if t == "ai-title":
                            title = d.get("aiTitle")
                        elif t == "last-prompt" and not title:
                            title = (d.get("lastPrompt") or "")[:80]
                        elif t in ("user", "assistant") and ts and within_day(ts, day_start, day_end):
                            if ts_first is None:
                                ts_first = ts
                            ts_last = ts
                            cwd = cwd or d.get("cwd")
                            msg = d.get("message") or {}
                            content = msg.get("content")
                            if isinstance(content, list):
                                txt = "\n".join(
                                    c.get("text", "")
                                    for c in content
                                    if isinstance(c, dict) and c.get("type") == "text"
                                )
                            elif isinstance(content, str):
                                txt = content
                            else:
                                txt = ""
                            if txt.strip():
                                msgs.append({"role": t, "text": txt})
            except Exception as e:
                print(f"[claude-code] skip {jsonl.name}: {e}", file=sys.stderr)
                continue

            if not ts_first or not msgs:
                continue

            out.append(RawSession(
                session_id=session_id,
                source=self.name,
                device=device_slug(),
                cwd=cwd,
                title=scrub_secrets(title or f"Session {session_id[:8]}"),
                started_at=ts_first,
                ended_at=ts_last,
                messages=truncate_messages(msgs),
            ))
        return out
