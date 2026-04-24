#!/usr/bin/env python3
"""Daily synthesis: roll up per-session summaries into day.json for the reader.

Calls the Anthropic API once to generate:
  - day-level summary (2-3 sentences)
  - lessons learned (3-5 bullets)
  - honest one-liner

Also aggregates decisions, open threads, devices, topic counts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw"
DATA_DIR = ROOT / "data"

TOPIC_LABELS = {
    "health": "Health",
    "career": "Career",
    "ai": "AI",
    "tech": "Tech",
    "travel": "Travel",
    "job-seeking": "Job seeking",
    "other": "Other",
}

SYNTH_PROMPT = """You are synthesizing one day of Claude-assisted work for Francis.
Day: {dow} {date}. {kept} of {total} sessions kept after filtering.

SESSIONS:
{blob}

Return ONLY valid JSON (no markdown fence, no preamble), shape:
{{
  "summary": "2-3 sentence terse first-person recap across topics. No fluff. No 'the user'. Mention the dominant thread and any notable detour.",
  "honest": "One honest line — under 14 words. Slightly dry. How the day actually felt.",
  "lessons": ["3-5 short first-person past-tense lessons, under 16 words each. Patterns, mistakes, insights — NOT a to-do list."]
}}
"""


def build_session_blob(kept: list[dict]) -> str:
    parts = []
    for s in kept:
        start = _short_time(s.get("started_at", ""))
        head = f"[{start} · {s.get('topic', 'other')} · {s.get('device', '?')}] {s.get('title', '')}"
        goal = (s.get("goal") or "").strip()
        body = goal if goal else (s.get("messages") or [{}])[0].get("text", "")[:300]
        decisions = s.get("decisions") or []
        opens = s.get("open") or []
        bits = [head, body]
        if decisions:
            bits.append("Decisions: " + "; ".join(decisions))
        if opens:
            bits.append("Open: " + "; ".join(opens))
        parts.append("\n".join(bits))
    return "\n\n".join(parts)


def _short_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M")
    except Exception:
        return "--:--"


def to_reader_item(s: dict) -> dict:
    return {
        "topic": s.get("topic") or "other",
        "device": s.get("device", "?"),
        "source": s.get("source", "?"),
        "start": _short_time(s.get("started_at", "")),
        "dur": s.get("duration_min", 0),
        "title": s.get("title", ""),
        "body": (s.get("goal") or "").strip() or "—",
        "decisions": s.get("decisions") or [],
        "open": s.get("open") or [],
        "artifacts": s.get("artifacts") or [],
        "sensitive": s.get("topic") == "job-seeking",
    }


def device_rollup(kept: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for s in kept:
        d = s.get("device", "?")
        row = seen.setdefault(d, {"slug": d, "sessions": 0, "lastSync": "—"})
        row["sessions"] += 1
        last = _short_time(s.get("ended_at", ""))
        if last != "--:--":
            row["lastSync"] = last
    return list(seen.values())


def dow(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--no-llm", action="store_true", help="Skip LLM synthesis; use placeholders")
    args = ap.parse_args()

    in_path = RAW_DIR / f"summaries-{args.date}.json"
    if not in_path.exists():
        # Fallback to sessions file if summaries wasn't produced
        in_path = RAW_DIR / f"sessions-{args.date}.json"
        if not in_path.exists():
            print(f"[synthesize] not found: {in_path}", file=sys.stderr)
            return 1

    data = json.loads(in_path.read_text())
    kept = data.get("kept", [])
    dropped = data.get("filtered_out", [])

    # LLM synthesis
    summary = ""
    honest = "—"
    lessons: list[str] = []
    if kept and not args.no_llm:
        print(llm.banner(), file=sys.stderr)
        blob = build_session_blob(kept)
        prompt = SYNTH_PROMPT.format(
            dow=dow(args.date), date=args.date,
            kept=len(kept), total=data.get("sessions_total", len(kept)),
            blob=blob,
        )
        try:
            text = llm.complete(prompt, role="synth", max_tokens=800)
            import re
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                parsed = json.loads(m.group(0))
                summary = parsed.get("summary", "")
                honest = parsed.get("honest", "") or "—"
                lessons = parsed.get("lessons", []) or []
        except Exception as e:
            print(f"[synthesize] LLM error, using placeholders: {e}", file=sys.stderr)

    if not summary:
        summary = f"{len(kept)} sessions across {len({s.get('topic') for s in kept})} topics." if kept else "Nothing captured today."

    decisions = []
    open_threads = []
    for s in kept:
        decisions.extend(s.get("decisions") or [])
        open_threads.extend(s.get("open") or [])

    day = {
        "date": args.date,
        "dow": dow(args.date),
        "devices": device_rollup(kept),
        "sessions_total": data.get("sessions_total", len(kept)),
        "sessions_kept": len(kept),
        "honest": honest,
        "summary": summary,
        "lessons": lessons,
        "items": [to_reader_item(s) for s in kept],
        "filtered_out": [{"topic": d.get("topic_guess") or d.get("reason"), "device": d.get("device", "?"), "dur": d.get("duration_min", 0)} for d in dropped],
        "decisions": decisions[:12],
        "open_threads": open_threads[:12],
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "day.json"
    out_path.write_text(json.dumps(day, indent=2))
    print(f"[synthesize] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
