#!/usr/bin/env python3
"""Cross-day lesson pattern matcher.

Runs AFTER synthesize.py. Takes today's LLM-extracted lessons and matches
them against the running lessons store. Bumps occurrences on matches,
appends new ones. Emits an "active" subset for the morning email — only
lessons that have actually compounded (occurrences >= 2) or are flagged
notable, so the email avoids one-off platitudes.

Files:
  $DATA/data/day.json              ← read today's lessons[]
  $DATA/lessons/store.json         ← read/write running store
  $DATA/data/lessons-active.json   ← write subset for the morning email

Idempotent: stores last_processed_date and skips if already processed.
Safe to re-run with --force.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("CLAUDE_JOURNAL_DATA_DIR") or APP_ROOT).resolve()
DATA_DIR = ROOT / "data"
LESSONS_DIR = ROOT / "lessons"

# Lessons must compound to surface in the email. Single-day observations
# stay in the store but don't get pushed.
SURFACE_THRESHOLD = 2

MATCH_PROMPT = """You are a pattern-matcher for a personal journal's lessons store.

ALIVE LESSONS (the existing running store):
{alive}

CANDIDATE LESSONS FROM TODAY ({date}):
{candidates}

For each candidate (by index 0..N-1), decide ONE of:
  - "NEW"          — genuinely new pattern, not represented in the store
  - "MATCH:<id>"   — same underlying pattern as an existing alive lesson
  - "SKIP"         — too vague/generic to be useful (e.g. "be more focused")

Be strict. SKIP anything that reads like a platitude. MATCH liberally — if
two lessons describe the same recurring behavior in different words, that's
a match. NEW only when it's a behavior the store has never named.

Return ONLY a JSON object, no prose:
{{
  "decisions": [
    {{"index": 0, "verdict": "NEW",     "reason": "..."}},
    {{"index": 1, "verdict": "MATCH:L-20260422-001", "reason": "..."}},
    {{"index": 2, "verdict": "SKIP",    "reason": "..."}}
  ]
}}
"""


def load_store() -> dict:
    p = LESSONS_DIR / "store.json"
    if not p.exists():
        return {"lessons": [], "last_processed_date": None}
    return json.loads(p.read_text())


def save_store(store: dict) -> None:
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    (LESSONS_DIR / "store.json").write_text(json.dumps(store, indent=2))


def alive_lessons(store: dict) -> list[dict]:
    return [l for l in store.get("lessons", []) if l.get("status", "alive") == "alive"]


def next_id(store: dict, date: str) -> str:
    """L-YYYYMMDD-NNN, monotonic per day."""
    prefix = f"L-{date.replace('-', '')}-"
    existing = [l["id"] for l in store.get("lessons", []) if l.get("id", "").startswith(prefix)]
    n = len(existing) + 1
    return f"{prefix}{n:03d}"


def call_matcher(alive: list[dict], candidates: list[str], date: str) -> list[dict]:
    if not candidates:
        return []
    if not alive:
        # Nothing to match against — every candidate is NEW (subject to SKIP for vagueness).
        # Still call LLM so it can SKIP platitudes.
        alive_blob = "(none — store is empty)"
    else:
        alive_blob = "\n".join(f"  {l['id']}: {l['text']}" for l in alive)
    cand_blob = "\n".join(f"  [{i}] {c}" for i, c in enumerate(candidates))

    prompt = MATCH_PROMPT.format(alive=alive_blob, candidates=cand_blob, date=date)
    text = llm.complete(prompt, role="synth", max_tokens=600)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        print(f"[match_lessons] LLM returned no JSON; treating all as SKIP", file=sys.stderr)
        return [{"index": i, "verdict": "SKIP", "reason": "no-json"} for i in range(len(candidates))]
    parsed = json.loads(m.group(0))
    return parsed.get("decisions", [])


def process_day(date: str, force: bool = False) -> dict:
    day_path = DATA_DIR / "day.json"
    if not day_path.exists():
        raise SystemExit(f"[match_lessons] {day_path} not found — run synthesize.py first")
    day = json.loads(day_path.read_text())
    if day.get("date") != date:
        print(f"[match_lessons] warn: day.json is for {day.get('date')}, not {date}",
              file=sys.stderr)
    candidates = list(day.get("lessons") or [])

    store = load_store()
    if store.get("last_processed_date") == date and not force:
        print(f"[match_lessons] {date} already processed; --force to re-run",
              file=sys.stderr)
        return store

    alive = alive_lessons(store)
    print(f"[match_lessons] {date}: {len(candidates)} candidates vs {len(alive)} alive lessons",
          file=sys.stderr)

    if not candidates:
        store["last_processed_date"] = date
        save_store(store)
        return store

    decisions = call_matcher(alive, candidates, date)
    by_id = {l["id"]: l for l in store["lessons"]}
    new_count = match_count = skip_count = 0

    for d in decisions:
        i = d.get("index")
        v = d.get("verdict", "")
        if i is None or i >= len(candidates):
            continue
        text = candidates[i]
        if v == "NEW":
            lid = next_id(store, date)
            store["lessons"].append({
                "id": lid,
                "text": text,
                "first_seen": date,
                "last_seen": date,
                "occurrences": 1,
                "evidence_dates": [date],
                "status": "alive",
                "reason": d.get("reason", ""),
            })
            new_count += 1
        elif v.startswith("MATCH:"):
            lid = v.split(":", 1)[1].strip()
            existing = by_id.get(lid)
            if not existing:
                # LLM hallucinated an ID — fall back to NEW
                lid = next_id(store, date)
                store["lessons"].append({
                    "id": lid, "text": text,
                    "first_seen": date, "last_seen": date,
                    "occurrences": 1, "evidence_dates": [date],
                    "status": "alive",
                    "reason": d.get("reason", "fallback-from-bad-match"),
                })
                new_count += 1
            else:
                existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
                existing["last_seen"] = date
                ed = existing.setdefault("evidence_dates", [])
                if date not in ed:
                    ed.append(date)
                match_count += 1
        else:  # SKIP
            skip_count += 1

    store["last_processed_date"] = date
    save_store(store)
    print(f"[match_lessons] new={new_count} match={match_count} skip={skip_count}",
          file=sys.stderr)
    return store


def write_active(store: dict, date: str) -> None:
    """Subset emitted for the morning email — what's worth re-surfacing."""
    active = []
    for l in store.get("lessons", []):
        if l.get("status", "alive") != "alive":
            continue
        # Compounded patterns are the main signal.
        if int(l.get("occurrences", 0)) >= SURFACE_THRESHOLD:
            active.append({**l, "_reason": "repeated"})
            continue
        # New-this-week lessons get one-week grace to be seen even at occurrences=1,
        # but only if the LLM didn't tag the matcher reason as "vague".
        if l.get("first_seen") == date and "vague" not in (l.get("reason", "").lower()):
            active.append({**l, "_reason": "new-this-week"})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "lessons-active.json").write_text(json.dumps({
        "as_of": date,
        "count": len(active),
        "lessons": active,
    }, indent=2))
    print(f"[match_lessons] wrote lessons-active.json — {len(active)} active", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    store = process_day(args.date, force=args.force)
    write_active(store, args.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
