"""
agentic_loopkit/events/headlines.py — LCLM-inspired compressed event headlines.

Provides corpus-level visibility into the event store at minimal token cost.
Based on the LCLM "latent context" pattern (Li et al., arXiv:2606.09659 §7):
agents skim the full history via EventHeadline records, then call expand_event()
to retrieve the full payload only for the events they identify as relevant.

Storage:
    headlines-<stream>.jsonl   — parallel to events-<stream>.jsonl
    chunk_id                   — 0-indexed per-stream integer assigned at write time

Headline generation is deterministic (no LLM):
    1. payload["summary"][:120]  — if present
    2. f"{event_type} from {source} at {timestamp[:16]}"  — fallback
    3. Append " [conf: HIGH/MEDIUM/LOW]" if _meta.confidence is present

Typical usage::

    from agentic_loopkit.events.headlines import append_headline, load_headlines, expand_event

    # Write (called automatically by EventBus.publish)
    headline = append_headline(event, store_dir)

    # Skim the full history cheaply
    headlines = load_headlines("my-stream", store_dir, limit=100)

    # Expand only the relevant event
    full_event = expand_event(chunk_id=7, stream="my-stream", store_dir=store_dir)

EventBus delegates:
    bus.load_headlines(stream, limit)   → list[EventHeadline]
    bus.expand_event(chunk_id, stream)  → Event | None
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Event

log = logging.getLogger("agentic_loopkit.headlines")

_DEFAULT_CACHE   = Path("~/.cache/agentic-loopkit").expanduser()
_MAX_HEADLINE    = 120
_CONF_HIGH       = 0.85
_CONF_MEDIUM     = 0.65


# ── EventHeadline ─────────────────────────────────────────────────────────────

@dataclass
class EventHeadline:
    """
    Lightweight summary record written alongside each Event at persist time.

    Attributes:
        event_id:   Matches ``Event.event_id`` — used by ``expand_event()`` to
                    retrieve the full payload.
        stream:     The event's stream prefix.
        event_type: Full event type string.
        timestamp:  ISO-8601 UTC timestamp string.
        headline:   ≤ 120-char human-readable summary.  Deterministic — no LLM.
        chunk_id:   0-indexed sequential integer scoped per stream.  Assigned at
                    write time as the line count before appending.  Used by
                    ``expand_event(chunk_id, stream)`` for O(file) resolution.
    """
    event_id:   str
    stream:     str
    event_type: str
    timestamp:  str
    headline:   str
    chunk_id:   int

    def to_dict(self) -> dict:
        return {
            "event_id":   self.event_id,
            "stream":     self.stream,
            "event_type": self.event_type,
            "timestamp":  self.timestamp,
            "headline":   self.headline,
            "chunk_id":   self.chunk_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EventHeadline":
        return cls(
            event_id   = d["event_id"],
            stream     = d["stream"],
            event_type = d["event_type"],
            timestamp  = d["timestamp"],
            headline   = d["headline"],
            chunk_id   = int(d["chunk_id"]),
        )

    @classmethod
    def from_event(cls, event: "Event", chunk_id: int) -> "EventHeadline":
        """Generate a headline from an Event.  Deterministic — no LLM."""
        from ..utils.time import iso_format
        return cls(
            event_id   = event.event_id,
            stream     = event.stream,
            event_type = str(event.event_type),
            timestamp  = iso_format(event.timestamp),
            headline   = _make_headline_text(event),
            chunk_id   = chunk_id,
        )


# ── Public API ────────────────────────────────────────────────────────────────

def append_headline(event: "Event", store_dir: Path = _DEFAULT_CACHE) -> EventHeadline:
    """
    Generate and persist a headline for this event.

    chunk_id is the number of existing headlines in the stream file before
    this append — giving a 0-indexed stable sequential address per stream.

    Called automatically by ``EventBus.publish()`` immediately after
    ``append_event()`` so headlines stay in sync with the event store.

    Returns the written ``EventHeadline``.
    """
    path = _headline_path(event.stream, store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    chunk_id = _count_lines(path)
    headline = EventHeadline.from_event(event, chunk_id)
    with path.open("a") as f:
        f.write(json.dumps(headline.to_dict()) + "\n")
    return headline


def load_headlines(
    stream: str,
    store_dir: Path = _DEFAULT_CACHE,
    limit: int = 50,
) -> list[EventHeadline]:
    """
    Return the most recent ``limit`` headlines for the stream, newest first.

    "Newest" is defined by ``chunk_id`` descending — the last appended headline
    has the highest chunk_id and appears first in the result.

    Returns an empty list if the headline file does not exist.
    """
    path = _headline_path(stream, store_dir)
    if not path.exists():
        return []

    headlines: list[EventHeadline] = []
    with path.open() as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                headlines.append(EventHeadline.from_dict(json.loads(line)))
            except json.JSONDecodeError as exc:
                log.warning("[headlines] malformed JSONL at %s line %d: %s", path.name, line_no, exc)
            except (KeyError, ValueError) as exc:
                log.warning("[headlines] invalid headline at %s line %d: %s", path.name, line_no, exc)

    return sorted(headlines, key=lambda h: h.chunk_id, reverse=True)[:limit]


def expand_event(
    chunk_id: int,
    stream: str,
    store_dir: Path = _DEFAULT_CACHE,
) -> Optional["Event"]:
    """
    Resolve a chunk_id to the full ``Event``.

    Scans the stream's headline file for the matching ``chunk_id``, extracts
    the ``event_id``, then loads the full event from the events store.

    Returns ``None`` if the chunk_id is not found or the underlying event has
    been removed (e.g. by ``compact_stream()`` outside the retention window).
    """
    path = _headline_path(stream, store_dir)
    if not path.exists():
        return None

    with path.open() as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                h = EventHeadline.from_dict(json.loads(line))
                if h.chunk_id == chunk_id:
                    from .store import load_events
                    events = load_events(stream, store_dir=store_dir, hours=24 * 365, event_id=h.event_id)
                    return events[0] if events else None
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.warning("[headlines] expand error at %s line %d: %s", path.name, line_no, exc)

    return None


# ── Internals ─────────────────────────────────────────────────────────────────

def _headline_path(stream: str, store_dir: Path) -> Path:
    return store_dir / f"headlines-{stream}.jsonl"


def _count_lines(path: Path) -> int:
    """Count non-blank lines in a JSONL file without loading its content."""
    if not path.exists():
        return 0
    with path.open() as f:
        return sum(1 for line in f if line.strip())


def _make_headline_text(event: "Event") -> str:
    """
    Generate a ≤ 120-char headline for an event.

    Priority:
        1. event.payload["summary"] (truncated to fit)
        2. Fallback: "{event_type} from {source} at {timestamp[:16]}"

    Appends " [conf: HIGH/MEDIUM/LOW]" when _meta.confidence is present.
    """
    from ..utils.time import iso_format

    payload = event.payload if isinstance(event.payload, dict) else {}
    base = str(payload.get("summary") or "").strip()
    if not base:
        ts = iso_format(event.timestamp)[:16]
        base = f"{event.event_type} from {event.source} at {ts}"

    meta = event.meta()
    suffix = ""
    if meta and "confidence" in meta:
        conf = float(meta["confidence"])
        level = "HIGH" if conf >= _CONF_HIGH else ("MEDIUM" if conf >= _CONF_MEDIUM else "LOW")
        suffix = f" [conf: {level}]"

    max_base = _MAX_HEADLINE - len(suffix)
    return (base[:max_base] + suffix)
