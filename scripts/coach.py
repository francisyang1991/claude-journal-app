#!/usr/bin/env python3
"""Coach — session-start context injector.

Designed to be called from a Claude Code SessionStart hook. Reads
memory/threads.json, filters to threads relevant to the current cwd,
and prints a short markdown block to stdout. That output gets injected
into the starting session as context.

Design principles:
  - Fast: reads local files only, no LLM calls. Sub-100ms typical.
  - Quiet: prints nothing if there are no relevant threads.
  - Robust: prints nothing on any error rather than blocking the session.
  - Deterministic: same state → same questions (no randomness).

Usage:
  python3 coach.py --cwd /path/to/project
  python3 coach.py --cwd "$CLAUDE_PROJECT_DIR"

Env:
  CLAUDE_JOURNAL_DATA_DIR — where to find memory/threads.json
  COACH_LIMIT             — max threads to show (default 3)
  COACH_SILENT_IF_EMPTY   — if "1", print nothing when no threads match
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as dt_date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import threads as T
except Exception:
    T = None

APP_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return Path(os.environ.get("CLAUDE_JOURNAL_DATA_DIR") or APP_ROOT).resolve()


def _safe_load(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _cwd_matches(thread: dict, cwd: str) -> bool:
    if not cwd:
        return False
    cwd_basename = Path(cwd).name
    for sig in thread.get("cwd_signals") or []:
        if not sig:
            continue
        if sig == cwd:
            return True
        if Path(sig).name == cwd_basename:
            return True
        if cwd in sig or sig in cwd:
            return True
    return False


def _days_ago(iso_date: str, today: dt_date) -> int:
    try:
        return max(0, (today - datetime.strptime(iso_date, "%Y-%m-%d").date()).days)
    except Exception:
        return 0


def _diagnostic_for(thread: dict, days: int) -> str:
    """Generate a Socratic question tied to the thread's state and age.
    Keep it short and forcing — answer should transition the thread state."""
    state = thread.get("state", "active")
    title = (thread.get("title") or "").rstrip(".?!")
    if state == "stale":
        return (
            f"Haven't touched in {days}d: *{title}*. "
            "Pick one: (a) blocked, (b) deprioritized, (c) finished without logging, (d) still relevant / forgot."
        )
    if days >= 3:
        return f"Still on *{title}*? If so, what's the next concrete step?"
    return f"Heads up: *{title}* — still want to close this today?"


def _pick_threads(threads: list[dict], cwd: str, limit: int) -> list[dict]:
    """Pick up to `limit` live threads, scored by cwd match + recency."""
    live = [t for t in threads if t.get("state") in ("active", "stale")]
    if not live:
        return []

    # Score: +100 for cwd match, -1 per day of age (more recent = higher)
    def score(t: dict) -> float:
        s = 100 if _cwd_matches(t, cwd) else 0
        last = t.get("last_touched_on") or t.get("created_on") or ""
        try:
            age = (dt_date.today() - datetime.strptime(last, "%Y-%m-%d").date()).days
        except Exception:
            age = 999
        # Stale items get a small bump — they're exactly what coach should surface
        if t.get("state") == "stale":
            s += 5
        return s - age

    ranked = sorted(live, key=score, reverse=True)
    # Prioritize cwd-matching threads: if any exist, only show those up to limit
    cwd_matched = [t for t in ranked if _cwd_matches(t, cwd)]
    if cwd_matched:
        return cwd_matched[:limit]
    return ranked[:limit]


def render(cwd: str, threads_data: dict | None, day_data: dict | None, limit: int) -> str:
    today = dt_date.today()

    if not threads_data or not threads_data.get("threads"):
        return ""

    picked = _pick_threads(threads_data["threads"], cwd, limit)
    if not picked:
        return ""

    repo_name = Path(cwd).name if cwd else "this workspace"

    # Last-seen-here info if available
    header_extra = ""
    if day_data and day_data.get("date"):
        try:
            d = datetime.strptime(day_data["date"], "%Y-%m-%d").date()
            n = (today - d).days
            if n == 0:
                header_extra = f" · last journal entry: today"
            elif n == 1:
                header_extra = f" · last journal entry: yesterday"
            else:
                header_extra = f" · last journal entry: {n}d ago"
        except Exception:
            pass

    lines = []
    lines.append(f"── Coach (claude-journal) · {repo_name}{header_extra} ─────────────────")
    lines.append("")
    has_stale = any(t.get("state") == "stale" for t in picked)
    has_cwd_match = any(_cwd_matches(t, cwd) for t in picked)
    if has_cwd_match:
        lines.append(f"Open threads in this repo:")
    else:
        lines.append(f"Live threads (none specifically tagged to this repo):")

    for t in picked:
        title = t.get("title", "")
        last = t.get("last_touched_on") or t.get("created_on") or ""
        days = _days_ago(last, today) if last else 0
        state = t.get("state", "active")
        marker = "○" if state == "stale" else "●"
        age_str = f"{days}d ago" if days > 0 else "today"
        lines.append(f"  {marker} {title}")
        lines.append(f"       last touched {age_str} · {state}")

    lines.append("")
    lines.append("Coach asks:")
    for t in picked:
        days = _days_ago(t.get("last_touched_on") or "", today)
        lines.append(f"  → {_diagnostic_for(t, days)}")

    lines.append("")
    lines.append("(Answer inline to update thread state. Ignore if not relevant.)")
    lines.append("─────────────────────────────────────────────────────────────────────")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--limit", type=int, default=int(os.environ.get("COACH_LIMIT", "3")))
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        data = data_dir()
        threads_data = _safe_load(data / "memory" / "threads.json")
        day_data = _safe_load(data / "data" / "day.json")
        out = render(args.cwd, threads_data, day_data, args.limit)
        if out.strip():
            print(out)
        elif args.debug:
            print("[coach] no threads to show", file=sys.stderr)
        return 0
    except Exception as e:
        # Never block a session over coach failure
        if args.debug:
            print(f"[coach] error: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
