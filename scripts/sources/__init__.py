"""Source adapter registry.

New adapter = new file in this directory + one line in REGISTRY below.
See ADR-002 for the protocol contract.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ._base import RawSession, SourceAdapter, device_slug
from .claude_code import ClaudeCode
from .cowork import Cowork
from .cursor import Cursor

REGISTRY: dict[str, SourceAdapter] = {
    a.name: a for a in [ClaudeCode(), Cowork(), Cursor()]
}


def get_enabled(data_dir: Path) -> list[SourceAdapter]:
    """Read data_dir/config/sources.yaml and return enabled adapters.

    Fall back to all registered adapters if sources.yaml is missing.
    """
    cfg_path = Path(data_dir) / "config" / "sources.yaml"
    if not cfg_path.exists():
        return list(REGISTRY.values())
    try:
        cfg: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return list(REGISTRY.values())
    names = cfg.get("sources") or list(REGISTRY.keys())
    return [REGISTRY[n] for n in names if n in REGISTRY]


__all__ = ["RawSession", "SourceAdapter", "REGISTRY", "get_enabled", "device_slug"]
