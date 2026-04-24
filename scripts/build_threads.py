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


def try_embed_with_fastembed(texts: list[str]) -> dict[str, list[float]] | None:
    """Opt-in: if fastembed is installed, embed texts with a small local model.
    Returns None if fastembed isn't available (no extra deps required)."""
    try:
        from fastembed import TextEmbedding  # type: ignore
    except ImportError:
        return None
    if not texts:
        return {}
    model_name = os.environ.get("FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
    model = TextEmbedding(model_name=model_name)
    out: dict[str, list[float]] = {}
    # fastembed returns an iterator of np arrays
    for text, vec in zip(texts, model.embed(texts)):
        out[text] = list(map(float, vec))
    return out


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

    # Pre-populate embedding cache if available (opt-in via fastembed).
    emb_path = DATA_DIR / "memory" / "embeddings.json"
    T.load_embeddings(emb_path)

    # Collect every text we'll match on this run so we can embed them all at once.
    texts_to_embed: set[str] = set()
    for p in day_files:
        day = load_day(p)
        if not day:
            continue
        for it in day.get("items") or []:
            for s in (it.get("open") or []) + (it.get("decisions") or []):
                if s and s not in T.EMBEDDINGS:
                    texts_to_embed.add(s)

    if texts_to_embed:
        new = try_embed_with_fastembed(sorted(texts_to_embed))
        if new:
            T.EMBEDDINGS.update(new)
            emb_path.parent.mkdir(parents=True, exist_ok=True)
            emb_path.write_text(json.dumps(T.EMBEDDINGS, indent=2))
            print(f"[threads] embedded {len(new)} new texts (fastembed)", file=sys.stderr)

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
