"""Shared utilities for agents (retry, JSON parsing, validation).

Filled out as agents become real. For now this is a parking spot for
common helpers so individual agent modules stay thin.
"""

from __future__ import annotations

import json
from typing import Any


def safe_json_loads(raw: str) -> Any:
    """Parse JSON, stripping common LLM wrapping (``` fences, leading text)."""
    raw = raw.strip()
    if raw.startswith("```"):
        # Strip leading fence (```json or ```) and trailing fence.
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines)
    return json.loads(raw)
