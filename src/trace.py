"""Replayable JSON trace of one review run: every agent call, tool call, and cost."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

CLIP = 20_000  # per-field cap so contexts/results don't balloon the trace file


def clip(text: str | None, limit: int = CLIP) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"… [clipped, {len(text)} chars total]"


class Tracer:
    """Collects timestamped events; write() dumps them as one JSON document.

    Always safe to use — callers create a throwaway Tracer when none is passed,
    so tracing code never needs None checks.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._t0 = time.monotonic()
        self._created = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def event(self, kind: str, **data) -> None:
        self.events.append({"t": round(time.monotonic() - self._t0, 3), "kind": kind, **data})

    def blocks(self, content) -> list[dict]:
        """Serialize API response content blocks for the trace."""
        out = []
        for b in content:
            entry = {"type": b.type}
            if getattr(b, "name", None):
                entry["name"] = b.name
            if getattr(b, "input", None) is not None:
                entry["input"] = b.input
            if getattr(b, "text", None):
                entry["text"] = clip(b.text, 2000)
            out.append(entry)
        return out

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"created": self._created, "events": self.events},
            indent=2,
            default=str,
        ))
        return path
