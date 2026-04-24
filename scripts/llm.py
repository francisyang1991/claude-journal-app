"""Minimal LLM client abstraction.

Supports two providers:
- anthropic (Claude Haiku/Sonnet) — via anthropic SDK
- glm (Zhipu GLM, OpenAI-compatible) — via stdlib urllib, no extra deps

Provider is selected by env var LLM_PROVIDER (glm | anthropic), or auto-detected:
  GLM_API_KEY set       → glm
  ANTHROPIC_API_KEY set → anthropic

Model is selected by LLM_MODEL env var, or per-role defaults:
  role="summary" (per-session, cheap) → haiku / glm-4-flash
  role="synth"   (daily, better)      → sonnet / glm-4-plus
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Literal

Role = Literal["summary", "synth"]

# Z.AI is the international endpoint for Zhipu GLM.
# Override with GLM_BASE_URL if you need the China-facing endpoint
# (https://open.bigmodel.cn/api/paas/v4/chat/completions) or a self-hosted one.
GLM_DEFAULT_ENDPOINT = "https://api.z.ai/api/paas/v4/chat/completions"

DEFAULT_MODELS = {
    "anthropic": {
        "summary": "claude-haiku-4-5-20251001",
        "synth":   "claude-sonnet-4-6",
    },
    "glm": {
        # Free tier, plenty strong for per-session extraction
        "summary": "glm-4.5-flash",
        # Cheap but higher-quality variant for the daily synthesis pass
        "synth":   "glm-4.5-air",
    },
}


def provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if p in ("glm", "anthropic"):
        return p
    if os.environ.get("GLM_API_KEY"):
        return "glm"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError(
        "No LLM provider configured. Set GLM_API_KEY or ANTHROPIC_API_KEY, "
        "or LLM_PROVIDER=glm|anthropic with the matching key."
    )


def _model_for(role: Role) -> str:
    override = os.environ.get("LLM_MODEL")
    if override:
        return override
    return DEFAULT_MODELS[provider()][role]


def complete(prompt: str, *, role: Role = "summary", max_tokens: int = 800) -> str:
    """Single-shot user-turn completion. Returns the assistant's text."""
    p = provider()
    model = _model_for(role)
    if p == "glm":
        return _glm_complete(prompt, model, max_tokens)
    return _anthropic_complete(prompt, model, max_tokens)


def _glm_complete(prompt: str, model: str, max_tokens: int) -> str:
    key = os.environ["GLM_API_KEY"]
    endpoint = os.environ.get("GLM_BASE_URL", GLM_DEFAULT_ENDPOINT)
    # Disable GLM 4.5's "thinking" mode — otherwise reasoning tokens consume
    # the whole max_tokens budget and content comes back empty. We want direct
    # output for extraction tasks. Set GLM_THINKING=1 to override.
    body_obj = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if not os.environ.get("GLM_THINKING"):
        body_obj["thinking"] = {"type": "disabled"}
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GLM HTTP {e.code}: {detail}") from e
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"GLM empty response: {data}")
    return choices[0].get("message", {}).get("content", "")


def _anthropic_complete(prompt: str, model: str, max_tokens: int) -> str:
    import anthropic  # lazy import so GLM-only users don't need the SDK
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


def banner() -> str:
    try:
        return f"[llm] provider={provider()} summary-model={_model_for('summary')} synth-model={_model_for('synth')}"
    except RuntimeError as e:
        return f"[llm] {e}"


if __name__ == "__main__":
    print(banner(), file=sys.stderr)
