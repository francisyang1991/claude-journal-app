"""Source adapter contract + shared utilities.

Each AI tool (Claude Code, Cowork, Cursor, Codex, ...) ships as one file in
this directory implementing a SourceAdapter. Adapters are registered in
sources/__init__.py and selected per-user via config/sources.yaml.

See ADR-002 for the design rationale.
"""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol


@dataclass
class RawSession:
    """Normalized session record produced by every adapter.

    Downstream code (classifier, summarizer, synthesizer) only sees this
    shape — no source-specific leakage.
    """
    session_id: str
    source: str            # stable slug: "claude-code", "cowork", "cursor", ...
    device: str
    started_at: str        # ISO 8601 with timezone
    ended_at: str
    cwd: str | None
    title: str
    messages: list[dict] = field(default_factory=list)  # [{"role": "user"|"assistant", "text": str}, ...]


class SourceAdapter(Protocol):
    name: str              # slug used in config/sources.yaml
    label: str             # human name

    def is_available(self) -> bool:
        """True if this source's data is present on the current machine."""
        ...

    def collect(self, date: str) -> list[RawSession]:
        """Return sessions that had activity on the given local date (YYYY-MM-DD)."""
        ...


# ── Shared utilities (adapters import from here, not from each other) ────────

def device_slug() -> str:
    return os.environ.get("DEVICE_SLUG") or socket.gethostname().split(".")[0].lower()


def local_day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """Return [start, end) in UTC for the local day matching date_str."""
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=local_tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def within_day(ts_iso: str, start_utc: datetime, end_utc: datetime) -> bool:
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except Exception:
        return False
    return start_utc <= ts < end_utc


# Regex patterns for common secret formats. Adapters should pass message text
# through scrub_secrets before writing into RawSession.messages.
_SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "anthropic"),
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), "openai"),
    (re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16}\b"), "glm"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-access-key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "github"),
    (re.compile(r"\bghs_[A-Za-z0-9]{30,}\b"), "github-server"),
    (re.compile(r"\b[a-f0-9]{40}\b"), "hex40"),
]


def scrub_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, kind in _SECRET_PATTERNS:
        out = pat.sub(f"[REDACTED:{kind}]", out)
    return out


def truncate_messages(messages: Iterable[dict], max_per_msg: int = 4000, max_count: int = 50) -> list[dict]:
    """Cap message length and session size. Uniformly applied across adapters."""
    out = []
    for m in messages:
        if len(out) >= max_count:
            break
        text = m.get("text", "")
        if not text or not text.strip():
            continue
        out.append({"role": m.get("role", "user"), "text": scrub_secrets(text[:max_per_msg])})
    return out
