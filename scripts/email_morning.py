#!/usr/bin/env python3
"""Morning journal email — assembled from data/day.json + lessons-active.json
and sent via Resend.

Three panels, flat HTML, no clicks required to read:
  1. PICKUP        — yesterday's open threads + last device per project
  2. YESTERDAY     — One Honest Line + summary
  3. STILL ALIVE   — repeated-pattern lessons (+ a few notable new ones)

Env:
  RESEND_API_KEY        — required
  TO_EMAIL              — recipient
  FROM_EMAIL            — sender (default: onboarding@resend.dev for dev sandbox)
  CLAUDE_JOURNAL_DATA_DIR — data repo (defaults to repo root)
  EMAIL_DATE            — override "yesterday" (YYYY-MM-DD)
  EMAIL_DRY_RUN=1       — print HTML to stdout instead of sending
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("CLAUDE_JOURNAL_DATA_DIR") or APP_ROOT).resolve()
DATA_DIR = ROOT / "data"


def yesterday_pacific() -> str:
    # Sent at 7am Pacific → "yesterday" is the day that just ended at midnight PT.
    # Pacific is UTC-7 (DST) or UTC-8 (PST). Use offset-aware approximation:
    # if we're running in CI/UTC, yesterday-PT = (now_utc - 7h).date() - 1day,
    # which is 99% correct year-round and never off by more than the DST gap.
    now_pt_approx = datetime.now(timezone.utc) - timedelta(hours=7)
    return (now_pt_approx - timedelta(days=1)).strftime("%Y-%m-%d")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"[email] warn: {path} unreadable ({e})", file=sys.stderr)
        return {}


def fmt_date(d: str) -> str:
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%A, %B %-d")
    except Exception:
        return d


def render(day: dict, active: dict, date: str) -> tuple[str, str]:
    pickup_threads = day.get("open_threads") or []
    devices = day.get("devices") or []
    honest = (day.get("honest") or "—").strip()
    summary = (day.get("summary") or "").strip()
    lessons = active.get("lessons") or []

    # Subject line
    pickup_n = len(pickup_threads)
    alive_n = len(lessons)
    subj_date = datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %-d")
    subject = f"{subj_date} — {pickup_n} pickup · {alive_n} still alive"

    # ── HTML body ────────────────────────────────────────────────────────────
    css = """
      body { font-family: Georgia, 'Times New Roman', serif; max-width: 620px;
             margin: 24px auto; padding: 0 20px; color: #2a2622; line-height: 1.5; }
      h1 { font-size: 14px; letter-spacing: 0.18em; text-transform: uppercase;
           color: #b87842; font-weight: normal; margin: 32px 0 8px; }
      .honest { font-size: 22px; font-style: italic; line-height: 1.35; margin: 8px 0 16px; }
      .summary { font-size: 16px; }
      ul { padding-left: 18px; }
      li { margin: 6px 0; }
      .device { font-family: monospace; font-size: 12px; color: #888; }
      .lesson-tag { display: inline-block; font-family: monospace; font-size: 11px;
                    color: #b87842; margin-right: 6px; }
      .lesson-meta { font-size: 12px; color: #888; }
      hr { border: 0; border-top: 1px solid #e8e0d4; margin: 28px 0; }
      .footer { font-size: 12px; color: #888; margin-top: 32px; }
      a { color: #b87842; }
    """

    parts = [f"<style>{css}</style>"]
    parts.append(f"<div class='honest'>“{escape(honest)}”</div>")

    # PICKUP
    parts.append("<h1>— Pickup —</h1>")
    if pickup_threads:
        parts.append("<ul>")
        for t in pickup_threads[:6]:
            parts.append(f"<li>{escape(str(t))}</li>")
        parts.append("</ul>")
        if day.get("open_extra"):
            parts.append(f"<div class='lesson-meta'>+ {day['open_extra']} more in the reader</div>")
    else:
        parts.append("<div class='lesson-meta'>Nothing open. Clean slate.</div>")

    if devices:
        bits = " · ".join(f"<span class='device'>{escape(d.get('slug','?'))} ({d.get('sessions',0)}s)</span>" for d in devices)
        parts.append(f"<div class='lesson-meta' style='margin-top:8px'>{bits}</div>")

    # YESTERDAY
    parts.append(f"<hr><h1>— {escape(fmt_date(date))} —</h1>")
    if summary:
        parts.append(f"<div class='summary'>{escape(summary)}</div>")
    else:
        parts.append("<div class='lesson-meta'>No sessions captured.</div>")

    # STILL ALIVE
    parts.append("<hr><h1>— Still alive —</h1>")
    if lessons:
        parts.append("<ul>")
        for l in lessons[:5]:
            tag = "⚠" if l.get("_reason") == "repeated" else "✱"
            occ = int(l.get("occurrences", 1))
            occ_str = f" ({occ}×)" if occ > 1 else ""
            parts.append(
                f"<li><span class='lesson-tag'>{tag}</span>{escape(l.get('text',''))}"
                f"<div class='lesson-meta'>since {l.get('first_seen','?')}{occ_str}</div></li>"
            )
        parts.append("</ul>")
    else:
        parts.append("<div class='lesson-meta'>No patterns surfaced yet.</div>")

    parts.append("<div class='footer'>journal · sent at 7am PT · "
                 "<a href='http://localhost:8765/reader/'>open reader</a></div>")

    body_html = "\n".join(parts)
    return subject, body_html


def send_resend(subject: str, html: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise SystemExit("[email] RESEND_API_KEY not set")
    to = os.environ.get("TO_EMAIL")
    if not to:
        raise SystemExit("[email] TO_EMAIL not set")
    sender = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")

    body = json.dumps({
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            print(f"[email] sent · id={payload.get('id','?')} · to={to}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[email] Resend HTTP {e.code}: {detail}")


def main() -> int:
    date = os.environ.get("EMAIL_DATE") or yesterday_pacific()
    day = load_json(DATA_DIR / "day.json")
    if day.get("date") and day["date"] != date:
        # Reader's day.json may not be the date we want — fall back to that day.
        # In v1 we only synthesize "today"; the morning email reads whatever's there.
        print(f"[email] day.json date is {day['date']}, requested {date} — using day.json date",
              file=sys.stderr)
        date = day.get("date") or date
    active = load_json(DATA_DIR / "lessons-active.json")

    subject, html = render(day, active, date)

    if os.environ.get("EMAIL_DRY_RUN"):
        print(f"Subject: {subject}\n\n{html}")
        return 0
    send_resend(subject, html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
