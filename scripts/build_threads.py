#!/usr/bin/env python3
"""Build memory/threads.json from the history of daily reports.

Deterministic: given the same set of data/history/*.json files it always
produces the same output. Safe to run repeatedly.

Usage:
  python scripts/build_threads.py                 # rebuild from everything available
  python scripts/build_threads.py --through DATE  # as if today were DATE (for backfill)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as dt_date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import threads as T  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CLAUDE_JOURNAL_DATA_DIR") or APP_ROOT).resolve()


def load_day(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def find_day_files() -> list[Path]:
    """Return daily reports in chronological order.

    History layout convention:
      data/day.json                 — today's report (single file)
      data/history/YYYY-MM-DD.json  — archived daily reports (future; not required today)
    """
    out: list[tuple[str, Path]] = []

    hist = DATA_DIR / "data" / "history"
    if hist.exists():
        for p in hist.glob("*.json"):
            out.append((p.stem, p))

    current = DATA_DIR / "data" / "day.json"
    if current.exists():
        d = load_day(current)
        if d and d.get("date"):
            out.append((d["date"], current))

    # Dedup by date, keep last seen (archived takes precedence if both exist)
    by_date: dict[str, Path] = {}
    for date, path in out:
        by_date[date] = path

    return [by_date[k] for k in sorted(by_date.keys())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--through", default=None,
                    help="Compute state transitions as if today were YYYY-MM-DD (default: real today)")
    args = ap.parse_args()

    today = (
        datetime.strptime(args.through, "%Y-%m-%d").date()
        if args.through
        else dt_date.today()
    )

    day_files = find_day_files()
    if not day_files:
        print("[threads] no day reports found; nothing to do", file=sys.stderr)
        return 0

    threads: list[T.Thread] = []
    for p in day_files:
        day = load_day(p)
        if not day:
            continue
        T.ingest_day(threads, day)

    T.age_transitions(threads, today)

    out_path = DATA_DIR / "memory" / "threads.json"
    T.save(out_path, threads)

    # Summary to stderr
    by_state: dict[str, int] = {}
    for t in threads:
        by_state[t.state] = by_state.get(t.state, 0) + 1
    print(f"[threads] {len(threads)} total, by state: {by_state}", file=sys.stderr)
    print(f"[threads] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
