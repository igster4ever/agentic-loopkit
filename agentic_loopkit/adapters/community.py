"""
agentic_loopkit/adapters/community.py — Local JSONL community feed adapter.

Reads a local JSONL file as a community feed and emits one event per line.
Designed for trust-graduated ingestion: all events start at TrustLevel.UNTRUSTED;
promotion to higher levels is handled by GovernanceLearningAgent via
POLICY_RECOMMENDATION (one level at a time: UNTRUSTED → LOW → MEDIUM → HIGH).

Cursor strategy:
    Byte offset of the last read position in the feed file.
    Byte offset (not line number) is correct — a line-number cursor breaks
    on JSONL compaction or reorder.  On first run cursor=None → reads from
    the beginning.  If the file is truncated (rotation), the adapter resets
    to offset 0 automatically.

Event types:
    community.entry_received — a new entry arrived in the JSONL feed

Usage:

    from agentic_loopkit.adapters.community import CommunityFeedAdapter, CommunityEventType
    from pathlib import Path

    adapter = CommunityFeedAdapter(
        bus                  = bus,
        feed_path            = Path("/var/feeds/gps-insights.jsonl"),
        name                 = "gps-insights",        # optional — used as cursor key + log prefix
        default_trust_level  = TrustLevel.UNTRUSTED,  # default; override for pre-vetted feeds
    )
    bus.add_adapter(adapter)
    await adapter.tick()

CommunityEventType lives in loopkit as a minimal infrastructure enum.
Consumers that want richer event types should subclass CommunityFeedAdapter
and override _entry_to_event() — do not add domain-specific event types here.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from ..events.models import Event, TrustLevel
from .base import PollingAdapter

log = logging.getLogger("agentic_loopkit.adapter.community")


# ── Event types ────────────────────────────────────────────────────────────────


class CommunityEventType(StrEnum):
    """Infrastructure events emitted by CommunityFeedAdapter.  Stream: 'community'."""
    ENTRY_RECEIVED = "community.entry_received"


# ── Adapter ────────────────────────────────────────────────────────────────────


class CommunityFeedAdapter(PollingAdapter):
    """
    Reads a local JSONL community feed file and emits community.entry_received events.

    All emitted events carry TrustLevel.UNTRUSTED.  Trust graduation is the
    responsibility of the consumer's governance layer — not this adapter.

    Attributes:
        name        adapter identifier; also the cursor key (override to avoid
                    collisions when running multiple feeds simultaneously)
        feed_path   path to the JSONL feed file
    """

    name = "community"

    def __init__(
        self,
        bus,
        feed_path:           Path,
        name:                Optional[str]  = None,
        default_trust_level: TrustLevel     = TrustLevel.UNTRUSTED,
    ) -> None:
        if name is not None:
            self.name = name
        self._feed_path          = Path(feed_path)
        self._default_trust_level = default_trust_level
        super().__init__(bus)

    # ── PollingAdapter contract ────────────────────────────────────────────────

    async def poll(
        self, cursor: Optional[Any]
    ) -> tuple[list[Event], Optional[Any]]:
        """
        Read new lines from the feed since the last byte offset.

        Returns (events, new_offset).  new_offset is None when:
          - the feed file does not exist yet
          - no bytes were read past the cursor (empty file or no new lines)
          - an OS error occurred (cursor unchanged; error logged)
        """
        byte_offset: int = cursor if cursor is not None else 0

        if not self._feed_path.exists():
            log.debug("[%s] feed file not found: %s", self.name, self._feed_path)
            return [], None

        events: list[Event] = []
        new_offset: int = byte_offset

        try:
            with open(self._feed_path, "r", encoding="utf-8") as f:
                # Detect truncation (file rotation): if the stored offset is
                # beyond the current file size, reset to the beginning.
                file_size = f.seek(0, 2)
                if byte_offset > file_size:
                    log.info(
                        "[%s] feed truncated (offset %d > size %d) — resetting to 0",
                        self.name, byte_offset, file_size,
                    )
                    byte_offset = 0

                f.seek(byte_offset)

                while True:
                    line = f.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        log.warning(
                            "[%s] malformed JSON line (skipping): %r",
                            self.name, stripped[:120],
                        )
                        continue
                    events.append(self._entry_to_event(entry))

                new_offset = f.tell()

        except OSError as exc:
            log.error("[%s] error reading feed %s: %s", self.name, self._feed_path, exc)
            return [], None

        if new_offset == byte_offset:
            return [], None

        log.debug("[%s] read %d entry(ies) from offset %d → %d",
                  self.name, len(events), byte_offset, new_offset)
        return events, new_offset

    # ── Event mapping ──────────────────────────────────────────────────────────

    def _entry_to_event(self, entry: dict) -> Event:
        """Map one parsed JSONL entry to a loopkit Event.

        Override in subclasses to emit consumer-specific event types.
        """
        correlation_id = (
            entry.get("id")
            or entry.get("correlation_id")
            or None
        )
        return Event(
            event_type     = CommunityEventType.ENTRY_RECEIVED,
            source         = self.name,
            correlation_id = str(correlation_id) if correlation_id is not None else None,
            trust_level    = self._default_trust_level,
            payload        = entry,
        )
