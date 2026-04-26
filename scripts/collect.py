#!/usr/bin/env python3
"""Collector — walks enabled source adapters, applies hard-exclude, writes raw/sessions-DATE.json.

Environment:
  CLAUDE_JOURNAL_DATA_DIR  — path to the user's private data repo (default: repo root)
  DEVICE_SLUG              — override hostname-based device name

Usage:
  python scripts/collect.py [--date YYYY-MM-DD] [--sources a,b,c]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Make `scripts/` importable whether called as module or script
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources  # noqa: E402
from sources._base import device_slug  # noqa: E402


# ── paths ────────────────────────────────────────────────────────────────────
APP_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Return the user's data repo path. Defaults to the app repo root for single-
    repo dev mode; override with CLAUDE_JOURNAL_DATA_DIR once the split happens."""
    env = os.environ.get("CLAUDE_JOURNAL_DATA_DIR")
    return Path(env).resolve() if env else APP_ROOT


def config_path(data: Path, name: str) -> Path:
    """Look up a config file. Prefer data-repo copy; fall back to app-repo template."""
    data_copy = data / "config" / name
    if data_copy.exists():
        return data_copy
    template = APP_ROOT / "config" / "templates" / name
    if template.exists():
        return template
    return APP_ROOT / "config" / name  # legacy fallback


# ── hard-exclude ─────────────────────────────────────────────────────────────
def load_hard_exclude(data: Path) -> dict:
    p = config_path(data, "hard_exclude.yaml")
    if not p.exists():
        return {"patterns": []}
    return yaml.safe_load(p.read_text()) or {"patterns": []}


def load_topics(data: Path) -> dict:
    p = config_path(data, "topics.yaml")
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text()) or {}).get("topics", {})


def _blob(s) -> str:
    parts = [s.title or "", s.cwd or ""]
    for m in s.messages:
        parts.append(m.get("text", ""))
    return "\n".join(parts)


def apply_hard_exclude(sessions, config: dict):
    kept, dropped = [], []
    patterns = config.get("patterns") or []
    for s in sessions:
        blob = _blob(s).lower()
        matched = None
        for pat in patterns:
            for term in (pat.get("any_of") or []):
                if term.lower() in blob:
                    matched = pat.get("name") or "match"
                    break
            if matched:
                break
            for rx in (pat.get("regex") or []):
                if re.search(rx, blob):
                    matched = pat.get("name") or "regex"
                    break
            if matched:
                break
        if matched:
            dropped.append({
                "session_id": s.session_id,
                "source": s.source,
                "title": s.title,
                "reason": matched,
                "duration_min": _duration_min(s),
                "topic_guess": matched,
            })
        else:
            kept.append(s)
    return kept, dropped


def _duration_min(s) -> int:
    try:
        a = datetime.fromisoformat(s.started_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(s.ended_at.replace("Z", "+00:00"))
        return max(1, round((b - a).total_seconds() / 60))
    except Exception:
        return 0


def guess_topic(s, topics: dict) -> tuple[str, float]:
    blob = _blob(s).lower()
    scores = {}
    for name, spec in topics.items():
        hits = sum(1 for kw in (spec.get("keywords") or []) if kw.lower() in blob)
        if hits:
            scores[name] = hits
    if not scores:
        return ("other", 0.0)
    top = max(scores.items(), key=lambda x: x[1])
    total = sum(scores.values()) or 1
    return (top[0], round(top[1] / total, 2))


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--sources", default=None, help="Override config/sources.yaml (comma-separated)")
    args = ap.parse_args()

    data = data_dir()
    hard = load_hard_exclude(data)
    topics = load_topics(data)

    if args.sources:
        names = [n.strip() for n in args.sources.split(",") if n.strip()]
        adapters = [sources.REGISTRY[n] for n in names if n in sources.REGISTRY]
    else:
        adapters = sources.get_enabled(data)

    all_sessions = []
    for a in adapters:
        if not a.is_available():
            print(f"[collect] {a.name}: not installed on this device, skipping", file=sys.stderr)
            continue
        found = a.collect(args.date)
        print(f"[collect] {a.name}: {len(found)} sessions", file=sys.stderr)
        all_sessions.extend(found)

    kept, dropped = apply_hard_exclude(all_sessions, hard)
    kept_dicts = []
    for s in kept:
        d = asdict(s)
        t, conf = guess_topic(s, topics)
        d["topic"] = t
        d["topic_confidence"] = conf
        d["duration_min"] = _duration_min(s)
        kept_dicts.append(d)

    kept_dicts.sort(key=lambda s: s["started_at"])

    # Stamp this device on every session/dropped entry so the merge below can
    # safely strip-and-replace just our contributions, leaving other devices'
    # entries intact.
    me = device_slug()
    now_iso = datetime.now(timezone.utc).isoformat()
    for d in kept_dicts:
        d.setdefault("device", me)
    for d in dropped:
        d.setdefault("device", me)

    raw_dir = data / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"sessions-{args.date}.json"

    # ── multi-device merge ─────────────────────────────────────────────────
    # Read existing shard (if another device wrote earlier today), strip our
    # own prior entries, then append fresh. Last-writer-wins for OUR entries
    # only — never for other devices'.
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except Exception as e:
            print(f"[collect] warn: existing shard unreadable ({e}); starting fresh",
                  file=sys.stderr)
            existing = {}

    other_kept = [s for s in (existing.get("kept") or []) if s.get("device") != me]
    other_dropped = [s for s in (existing.get("filtered_out") or []) if s.get("device") != me]

    merged_kept = sorted(other_kept + kept_dicts, key=lambda s: s.get("started_at") or "")
    merged_dropped = other_dropped + dropped

    # Per-device summary, including this run's contribution.
    devices = dict(existing.get("devices") or {})
    devices[me] = {
        "sessions": len(kept_dicts),
        "filtered_out": len(dropped),
        "last_sync": now_iso,
    }

    payload = {
        "date": args.date,
        "schema": 2,
        "generated_at": now_iso,
        "devices": devices,
        "sessions_total": len(merged_kept) + len(merged_dropped),
        "sessions_kept": len(merged_kept),
        "kept": merged_kept,
        "filtered_out": merged_dropped,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(
        f"[collect] wrote {out_path} — {len(merged_kept)} kept "
        f"({len(kept_dicts)} from {me}, {len(other_kept)} from other devices), "
        f"{len(merged_dropped)} dropped",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
