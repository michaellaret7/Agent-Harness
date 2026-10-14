"""History archive: the on-disk record of messages removed from context by compaction.

Each compaction writes one append-only JSONL segment, one chat message per line,
under a per-session folder so processes sharing a workspace never overwrite each
other:

    <root>/<session>/segment_001.jsonl
    <root>/<session>/segment_002.jsonl

Paths are reported relative to `root.parent`. With a workspace that is the
sandbox, so the agent can open them directly from ExecuteCode.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class HistoryArchive:
    """Writes compacted spans of history as numbered JSONL segments."""

    def __init__(self, root: Path) -> None:
        self.root = root

        # Timestamp for humans, short random suffix so two sessions started in the same second never collide
        self.session_dir = root / f'{datetime.now():%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}'
        self._segments = 0

    def write(self, messages: list[dict[str, Any]]) -> str:
        """Write `messages` as the next segment and return its path relative to `root.parent`."""
        self._segments += 1
        path = self.session_dir / f'segment_{self._segments:03d}.jsonl'

        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open('w', encoding='utf-8') as f:
            for message in messages:
                f.write(json.dumps(message, ensure_ascii=False) + '\n')

        return path.relative_to(self.root.parent).as_posix()
