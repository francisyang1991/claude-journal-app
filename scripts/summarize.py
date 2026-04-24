#!/usr/bin/env python3
"""Per-session summarization via Anthropic API.

Reads raw/sessions-YYYY-MM-DD.json, produces raw/summaries-YYYY-MM-DD.json with
structured fields: goal, decisions, open, artifacts, topic (label), body.
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

PROMPT = """You are extracting structured notes from a {source} session from {date}.
Session title: {title}
Working directory: {cwd}

Produce ONLY four sections, in this exact format. Do not add a header or preamble.

## Goal
[one sentence, first-person, what I was trying to do]

## Decisions
- [terse bullet, one line each]
- [only decisions I actually made or concluded]
- [if none, write exactly: None]

## Open
- [unresolved questions or next actions]
- [if none, write exactly: None]

## Files
- Modified: `path/to/file`
- Added: `path/to/file`
- Deleted: `path/to/file`
- [if no file changes, write exactly: None]

Rules:
- No narrative prose, no "Summary" section
- Write as me, first person, terse
- NEVER include PII, credentials, API keys, passwords
- Max 150 words total

Session transcript (user and assistant messages):
{transcript}
"""


def build_transcript(messages: list[dict], max_chars: int = 12000) -> str:
    out = []
    total = 0
    for m in messages:
        line = f"[{m.get('role', '?')}] {m.get('text', '')}"
        if total + len(line) > max_chars:
            break
        out.append(line)
        total += len(line)
    return "\n\n".join(out)


def parse_sections(text: str) -> dict:
    """Pull out Goal / Decisions / Open / Files from markdown-ish output."""
    import re

    sections = {"goal": "", "decisions": [], "open": [], "artifacts": []}
    current = None
    buf: list[str] = []

    def flush():
        if current is None:
            return
        content = "\n".join(buf).strip()
        if current == "goal":
            sections["goal"] = content
        elif current in ("decisions", "open"):
            items = [re.sub(r"^[-*]\s*", "", l).strip() for l in content.splitlines() if l.strip()]
            items = [i for i in items if i and i.lower() != "none"]
            sections[current] = items
        elif current == "files":
            items = []
            for l in content.splitlines():
                l = l.strip()
                if not l or l.lower() == "none":
                    continue
                m = re.match(r"[-*]\s*(Modified|Added|Deleted):\s*`?([^`]+)`?", l)
                if m:
                    items.append(f"{m.group(1).lower()}: {m.group(2)}")
                elif l.startswith(("- ", "* ")):
                    items.append(l[2:])
            sections["artifacts"] = items

    for line in text.splitlines():
        h = re.match(r"##\s+(\w+)", line.strip())
        if h:
            flush()
            name = h.group(1).lower()
            if name == "files":
                current = "files"
            elif name in ("goal", "decisions", "open"):
                current = name
            else:
                current = None
            buf = []
        else:
            buf.append(line)
    flush()
    return sections


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0, help="Max sessions to process (0 = all)")
    args = ap.parse_args()

    raw_path = RAW_DIR / f"sessions-{args.date}.json"
    if not raw_path.exists():
        print(f"[summarize] not found: {raw_path}", file=sys.stderr)
        return 1

    data = json.loads(raw_path.read_text())
    sessions = data["kept"]
    if args.limit:
        sessions = sessions[: args.limit]

    print(llm.banner(), file=sys.stderr)

    enriched = []
    for i, s in enumerate(sessions, 1):
        transcript = build_transcript(s.get("messages", []))
        if not transcript.strip():
            print(f"[summarize] [{i}/{len(sessions)}] skip empty: {s.get('title')}", file=sys.stderr)
            enriched.append({**s, "goal": s.get("title", ""), "decisions": [], "open": [], "artifacts": []})
            continue
        prompt = PROMPT.format(
            source=s.get("source", "?"),
            date=args.date,
            title=s.get("title", ""),
            cwd=s.get("cwd") or "",
            transcript=transcript,
        )
        print(f"[summarize] [{i}/{len(sessions)}] {s.get('title', '')[:60]}", file=sys.stderr)
        try:
            text = llm.complete(prompt, role="summary", max_tokens=600)
            parsed = parse_sections(text)
            enriched.append({**s, **parsed, "summary_raw": text})
        except Exception as e:
            print(f"[summarize] error on {s.get('session_id')}: {e}", file=sys.stderr)
            enriched.append({**s, "goal": s.get("title", ""), "decisions": [], "open": [], "artifacts": []})

    out_path = RAW_DIR / f"summaries-{args.date}.json"
    payload = {**data, "kept": enriched}
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"[summarize] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
