"""Thread extraction and state tracking.

A "thread" is any open item or decision that persists across sessions and
matters beyond the moment it was logged. Threads have a state machine:

    active ──── (7d no touch) ───▶ stale
       │                             │
       └── explicit resolution ──▶ resolved
                                     │
                            (30d stale) ──▶ abandoned

This module is deterministic: given the same set of daily reports it always
produces the same threads.json. No hidden state, no mutations. Rebuilding
from scratch == rebuilding incrementally + the latest day.

Matching strategy (MVP): Jaccard similarity on normalized token sets. Good
enough for exact and near-paraphrase matches. Known-weak for semantically
equivalent rewrites with no shared words — upgrade to embeddings later.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date as dt_date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# ── states ──────────────────────────────────────────────────────────────────
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_RESOLVED = "resolved"
STATE_ABANDONED = "abandoned"
STATE_DEPRIORITIZED = "deprioritized"

# Days since last_touched_on before transitions kick in
STALE_AFTER_DAYS = 7
ABANDON_AFTER_STALE_DAYS = 30

# Matching threshold (Jaccard)
SIMILARITY_MATCH = 0.55


# ── data model ─────────────────────────────────────────────────────────────
@dataclass
class Touch:
    date: str                      # YYYY-MM-DD
    session_id: str
    cwd: str | None = None
    source: str | None = None      # adapter name
    kind: str = "mention"          # mention | open | decision


@dataclass
class Thread:
    id: str
    title: str                     # canonical title (first-seen or most recent rewrite)
    state: str
    origin_kind: str               # open | decision
    topic: str | None
    created_on: str                # YYYY-MM-DD
    last_touched_on: str           # YYYY-MM-DD
    touches: list[Touch] = field(default_factory=list)
    cwd_signals: list[str] = field(default_factory=list)   # distinct cwds observed
    resolution: str | None = None  # the decision text that closed it (if resolved)
    resolution_date: str | None = None
    aliases: list[str] = field(default_factory=list)       # alternate phrasings seen


# ── text normalization + matching ──────────────────────────────────────────
_STOPWORDS = {
    "need", "needs", "should", "could", "would", "will", "decide", "decided",
    "add", "check", "ensure", "try", "use", "make", "the", "and", "with", "for",
    "into", "from", "when", "what", "how", "that", "this", "those", "these",
    "pick", "choose", "keep", "start", "run", "get", "set", "fix", "new",
    "still", "some", "any", "all", "not", "next",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"\w{3,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def similarity(a: str, b: str) -> float:
    """Jaccard on normalized token sets. 0.0 to 1.0."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def thread_id_for(title: str) -> str:
    """Stable ID derived from a canonical form of the title."""
    canonical = " ".join(sorted(_tokens(title)))
    return "th_" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]


# ── linking + resolution ───────────────────────────────────────────────────
def find_match(text: str, threads: list[Thread], already_resolved_ok: bool = False) -> Thread | None:
    """Find the best-matching thread for a given new item, above SIMILARITY_MATCH.
    Skips resolved threads by default (a resolved thread won't swallow a new idea)."""
    best: tuple[float, Thread] | None = None
    for t in threads:
        if not already_resolved_ok and t.state == STATE_RESOLVED:
            continue
        # Match against canonical title + any known aliases
        candidates = [t.title, *t.aliases]
        score = max(similarity(text, c) for c in candidates)
        if score >= SIMILARITY_MATCH and (best is None or score > best[0]):
            best = (score, t)
    return best[1] if best else None


# ── state transitions ──────────────────────────────────────────────────────
def age_days(date_str: str, today: dt_date) -> int:
    try:
        return (today - datetime.strptime(date_str, "%Y-%m-%d").date()).days
    except Exception:
        return 0


def age_transitions(threads: list[Thread], today: dt_date) -> None:
    """Apply age-based state transitions in place. Resolved/deprioritized stay put."""
    for t in threads:
        if t.state in (STATE_RESOLVED, STATE_DEPRIORITIZED):
            continue
        days = age_days(t.last_touched_on, today)
        if days >= STALE_AFTER_DAYS + ABANDON_AFTER_STALE_DAYS:
            t.state = STATE_ABANDONED
        elif days >= STALE_AFTER_DAYS:
            t.state = STATE_STALE
        else:
            t.state = STATE_ACTIVE


# ── ingestion from a daily report ──────────────────────────────────────────
def _touch_for(item: dict, kind: str, date: str) -> Touch:
    return Touch(
        date=date,
        session_id=item.get("session_id") or item.get("id") or "unknown",
        cwd=item.get("cwd"),
        source=item.get("source"),
        kind=kind,
    )


def ingest_day(threads: list[Thread], day: dict) -> None:
    """Fold one day's report (data/day.json shape) into the threads list.
    Mutates threads in place."""
    date = day.get("date")
    if not date:
        return

    items = day.get("items") or []

    # Pass 1 — resolutions: for each decision, see if it closes a matching open thread
    for item in items:
        for decision in item.get("decisions") or []:
            match = find_match(decision, threads)
            if match and match.state != STATE_RESOLVED and match.origin_kind == "open":
                match.state = STATE_RESOLVED
                match.resolution = decision
                match.resolution_date = date
                match.last_touched_on = date
                match.touches.append(_touch_for(item, "decision", date))
                if item.get("cwd") and item["cwd"] not in match.cwd_signals:
                    match.cwd_signals.append(item["cwd"])
                if decision not in match.aliases and decision != match.title:
                    match.aliases.append(decision)

    # Pass 2 — opens: match or create
    for item in items:
        for open_text in item.get("open") or []:
            match = find_match(open_text, threads)
            if match:
                match.last_touched_on = date
                match.touches.append(_touch_for(item, "open", date))
                if item.get("cwd") and item["cwd"] not in match.cwd_signals:
                    match.cwd_signals.append(item["cwd"])
                if open_text not in match.aliases and open_text != match.title:
                    match.aliases.append(open_text)
                # A re-raised open after a resolution counts as a reopening
                if match.state == STATE_RESOLVED:
                    match.state = STATE_ACTIVE
                    match.resolution = None
                    match.resolution_date = None
            else:
                t = Thread(
                    id=thread_id_for(open_text),
                    title=open_text,
                    state=STATE_ACTIVE,
                    origin_kind="open",
                    topic=item.get("topic"),
                    created_on=date,
                    last_touched_on=date,
                )
                t.touches.append(_touch_for(item, "open", date))
                if item.get("cwd"):
                    t.cwd_signals.append(item["cwd"])
                threads.append(t)

    # Pass 3 — decisions as potential first-raise threads (promoted only if worth it).
    # MVP heuristic: skip. Decisions that don't close an open item are point-in-time
    # and rarely need tracking. Revisit if coach.py wants to ask "you decided X — did
    # it stick?" questions.


# ── serialization ──────────────────────────────────────────────────────────
def to_dict(threads: list[Thread]) -> dict:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "threads": [asdict(t) for t in threads],
    }


def from_dict(d: dict) -> list[Thread]:
    out = []
    for raw in (d.get("threads") or []):
        touches = [Touch(**t) for t in raw.pop("touches", [])]
        out.append(Thread(touches=touches, **raw))
    return out


def load(path: Path) -> list[Thread]:
    if not path.exists():
        return []
    try:
        return from_dict(json.loads(path.read_text()))
    except Exception:
        return []


def save(path: Path, threads: list[Thread]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(threads), indent=2))


# ── helpers for coach.py + reader ──────────────────────────────────────────
def for_cwd(threads: list[Thread], cwd: str) -> list[Thread]:
    """Return threads whose cwd_signals match the given cwd (basename or substring).
    Coach.py uses this to filter threads to what's relevant in the current repo."""
    if not cwd:
        return []
    cwd_basename = Path(cwd).name
    out = []
    for t in threads:
        for sig in t.cwd_signals:
            if not sig:
                continue
            if sig == cwd or Path(sig).name == cwd_basename or cwd in sig or sig in cwd:
                out.append(t)
                break
    return out


def active_and_stale(threads: Iterable[Thread]) -> list[Thread]:
    return [t for t in threads if t.state in (STATE_ACTIVE, STATE_STALE)]
