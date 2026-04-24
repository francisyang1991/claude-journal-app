#!/usr/bin/env python3
"""Walk Claude Code + Cowork sessions for a given date, apply hard-exclude, emit raw/sessions.json.

Output shape: list of session dicts with raw content for downstream summarize/synthesize.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

HOME = Path.home()
CC_PROJECTS = HOME / ".claude" / "projects"
COWORK_ROOT = HOME / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
RAW_DIR = ROOT / "raw"


def load_hard_exclude() -> dict:
    with open(CONFIG_DIR / "hard_exclude.yaml") as f:
        return yaml.safe_load(f)


def load_topics() -> dict:
    with open(CONFIG_DIR / "topics.yaml") as f:
        return yaml.safe_load(f)["topics"]


def device_slug() -> str:
    return os.environ.get("DEVICE_SLUG") or socket.gethostname().split(".")[0].lower()


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def within_day(ts_str: str, day_start: datetime, day_end: datetime) -> bool:
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return False
    return day_start <= ts < day_end


def local_day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """Return [start, end) in UTC for the local day."""
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=local_tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def extract_claude_code_sessions(date_str: str) -> list[dict]:
    """Walk ~/.claude/projects/*/*.jsonl, return sessions with activity on date_str (local)."""
    if not CC_PROJECTS.exists():
        return []
    day_start, day_end = local_day_bounds(date_str)
    out = []
    for jsonl in CC_PROJECTS.glob("*/*.jsonl"):
        session_id = jsonl.stem
        cwd = None
        title = None
        ts_first = None
        ts_last = None
        msgs = []
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
                    if t in ("user", "assistant") and ts:
                        if within_day(ts, day_start, day_end):
                            if ts_first is None:
                                ts_first = ts
                            ts_last = ts
                            cwd = cwd or d.get("cwd")
                            msg = d.get("message") or {}
                            content = msg.get("content")
                            if isinstance(content, list):
                                txt = "\n".join(
                                    c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                                )
                            elif isinstance(content, str):
                                txt = content
                            else:
                                txt = ""
                            if txt.strip():
                                msgs.append({"role": t, "text": txt[:4000]})
                    elif t == "last-prompt" and not title:
                        title = (d.get("lastPrompt") or "")[:80]
        except Exception as e:
            print(f"[collect] skip {jsonl.name}: {e}", file=sys.stderr)
            continue
        if not ts_first or not msgs:
            continue
        out.append({
            "session_id": session_id,
            "source": "claude-code",
            "device": device_slug(),
            "cwd": cwd,
            "title": title or f"Session {session_id[:8]}",
            "started_at": ts_first,
            "ended_at": ts_last,
            "messages": msgs[:50],
        })
    return out


def extract_cowork_sessions(date_str: str) -> list[dict]:
    """Walk Cowork sidecar JSONs + audit.jsonl for activity on date_str."""
    if not COWORK_ROOT.exists():
        return []
    day_start, day_end = local_day_bounds(date_str)
    out = []
    for meta in COWORK_ROOT.glob("*/*/local_*.json"):
        try:
            md = json.loads(meta.read_text())
        except Exception:
            continue
        last_ms = md.get("lastActivityAt")
        created_ms = md.get("createdAt")
        if not last_ms:
            continue
        last_ts = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
        if not (day_start <= last_ts < day_end):
            continue
        ts_first = datetime.fromtimestamp((created_ms or last_ms) / 1000, tz=timezone.utc).isoformat()
        ts_last = last_ts.isoformat()
        audit = meta.parent / meta.stem / "audit.jsonl"
        msgs = []
        if audit.exists():
            try:
                with open(audit) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        t = d.get("type") or d.get("role")
                        msg = d.get("message") or {}
                        content = msg.get("content") if isinstance(msg, dict) else None
                        if isinstance(content, list):
                            txt = "\n".join(
                                c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                            )
                        elif isinstance(content, str):
                            txt = content
                        else:
                            txt = d.get("content") if isinstance(d.get("content"), str) else ""
                        if t in ("user", "assistant") and txt and txt.strip():
                            msgs.append({"role": t, "text": txt[:4000]})
                        if len(msgs) >= 50:
                            break
            except Exception:
                pass
        out.append({
            "session_id": md.get("sessionId") or meta.stem,
            "source": "cowork",
            "device": device_slug(),
            "cwd": md.get("cwd") or (md.get("userSelectedFolders") or [None])[0],
            "title": md.get("title") or f"Cowork {meta.stem[:12]}",
            "started_at": ts_first,
            "ended_at": ts_last,
            "messages": msgs,
        })
    return out


def session_blob(s: dict) -> str:
    lines = [s.get("title", ""), s.get("cwd", "") or ""]
    for m in s.get("messages", []):
        lines.append(m.get("text", ""))
    return "\n".join(lines)


def _any_of_match(blob_lower: str, term: str) -> bool:
    """Match with word boundaries; multi-word phrases use substring."""
    t = term.lower().strip()
    if " " in t or "." in t:
        return t in blob_lower
    # single token: require word boundary — reject substring hits like "ESD" inside "tested"
    return re.search(rf"\b{re.escape(t)}\b", blob_lower) is not None


def apply_hard_exclude(sessions: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for s in sessions:
        blob = session_blob(s).lower()
        # Title is weighted — a blocklist word in the title is a strong signal.
        # For single-token matches in body only, require >=2 occurrences to reduce
        # false positives from passing mentions in meta/discussion sessions.
        title_blob = (s.get("title", "") + "\n" + (s.get("cwd") or "")).lower()
        matched = None
        for pat in config.get("patterns", []):
            for term in pat.get("any_of", []):
                t = term.lower().strip()
                if _any_of_match(title_blob, term):
                    matched = pat["name"]
                    break
                # Body match: multi-word phrases are strong enough on their own.
                # Single-token must appear at least twice to count as on-topic.
                if " " in t or "." in t:
                    if _any_of_match(blob, term):
                        matched = pat["name"]
                        break
                else:
                    hits = len(re.findall(rf"\b{re.escape(t)}\b", blob))
                    if hits >= 2:
                        matched = pat["name"]
                        break
            if matched:
                break
            for rx in pat.get("regex", []):
                if re.search(rx, blob):
                    matched = pat["name"]
                    break
            if matched:
                break
        if matched:
            dropped.append({
                "session_id": s["session_id"],
                "source": s["source"],
                "title": s["title"],
                "reason": matched,
                "duration_min": _duration_min(s),
                "topic_guess": matched,
            })
        else:
            kept.append(s)
    return kept, dropped


def _duration_min(s: dict) -> int:
    try:
        a = datetime.fromisoformat(s["started_at"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(s["ended_at"].replace("Z", "+00:00"))
        return max(1, round((b - a).total_seconds() / 60))
    except Exception:
        return 0


def guess_topic(session: dict, topics: dict) -> tuple[str, float]:
    """Cheap keyword-based topic guess. Summarizer refines later."""
    blob = session_blob(session).lower()
    scores = {}
    for name, spec in topics.items():
        s = 0
        for kw in spec.get("keywords", []):
            if str(kw).lower() in blob:
                s += 1
        if s:
            scores[name] = s
    if not scores:
        return ("other", 0.0)
    top = max(scores.items(), key=lambda x: x[1])
    total = sum(scores.values())
    return (top[0], round(top[1] / max(total, 1), 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--sources", default="claude-code,cowork")
    args = ap.parse_args()

    hard = load_hard_exclude()
    topics = load_topics()
    sources = args.sources.split(",")

    all_sessions: list[dict] = []
    if "claude-code" in sources:
        cc = extract_claude_code_sessions(args.date)
        print(f"[collect] claude-code: {len(cc)} sessions", file=sys.stderr)
        all_sessions += cc
    if "cowork" in sources:
        cw = extract_cowork_sessions(args.date)
        print(f"[collect] cowork: {len(cw)} sessions", file=sys.stderr)
        all_sessions += cw

    kept, dropped = apply_hard_exclude(all_sessions, hard)
    for s in kept:
        t, conf = guess_topic(s, topics)
        s["topic"] = t
        s["topic_confidence"] = conf
        s["duration_min"] = _duration_min(s)

    kept.sort(key=lambda s: s["started_at"])

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"sessions-{args.date}.json"
    payload = {
        "date": args.date,
        "device": device_slug(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sessions_total": len(all_sessions),
        "sessions_kept": len(kept),
        "kept": kept,
        "filtered_out": dropped,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"[collect] wrote {out_path} — {len(kept)} kept, {len(dropped)} dropped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
