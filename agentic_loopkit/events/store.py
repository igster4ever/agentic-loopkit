"""
agentic_loopkit/events/store.py — JSONL per-stream event persistence.

One file per stream: <store_dir>/events-<stream>.jsonl
Append-only writes; compaction rewrites within a retention window.

The store_dir defaults to ~/.cache/agentic-loopkit but is fully configurable
so multiple bus instances (GPS Radar, MPSM, tests) never collide.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import Event, WILDCARD_STREAM

log = logging.getLogger("agentic_loopkit.store")

_DEFAULT_CACHE    = Path("~/.cache/agentic-loopkit").expanduser()
_RETENTION_HOURS  = 72


# ── Public API ─────────────────────────────────────────────────────────────────

def append_event(event: Event, store_dir: Path = _DEFAULT_CACHE) -> None:
    """Append one Event to its stream file.  Creates the file if absent."""
    path = _stream_path(event.stream, store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(event.to_dict()) + "\n")


def load_events(
    stream: str,
    store_dir: Path = _DEFAULT_CACHE,
    hours: int = _RETENTION_HOURS,
    event_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> list[Event]:
    """
    Return events for stream from the last `hours` hours, newest first.

    Filters:
      event_type     — exact match on event_type string
      correlation_id — return only events belonging to a workflow
      stream="*"     — load from all stream files
    """
    if stream == WILDCARD_STREAM:
        events: list[Event] = []
        for path in store_dir.glob("events-*.jsonl"):
            events.extend(_read_path(path, hours, event_type, correlation_id))
        return sorted(events, key=lambda e: e.timestamp, reverse=True)

    return _read_path(_stream_path(stream, store_dir), hours, event_type, correlation_id)


def load_all_events(stream: str, store_dir: Path = _DEFAULT_CACHE) -> list[Event]:
    """Return every event in a stream with no time filter, newest first."""
    return load_events(stream, store_dir=store_dir, hours=24 * 365)


def compact_stream(
    stream: str,
    store_dir: Path = _DEFAULT_CACHE,
    hours: int = _RETENTION_HOURS,
) -> int:
    """Rewrite stream file keeping only events within retention window.
    Returns number of events removed."""
    path = _stream_path(stream, store_dir)
    if not path.exists():
        return 0

    all_events = load_all_events(stream, store_dir)
    cutoff = _now() - timedelta(hours=hours)
    kept = [e for e in all_events if e.timestamp >= cutoff]
    removed = len(all_events) - len(kept)
    if removed == 0:
        return 0

    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w") as f:
            for e in reversed(kept):
                f.write(json.dumps(e.to_dict()) + "\n")
        tmp.replace(path)
        log.info("[store] compacted %s — removed %d event(s)", path.name, removed)
    except Exception as exc:
        log.error("[store] compaction failed for %s: %s", path.name, exc)
        if tmp.exists():
            tmp.unlink()
        return 0
    return removed


# ── Internals ──────────────────────────────────────────────────────────────────

def _stream_path(stream: str, store_dir: Path) -> Path:
    return store_dir / f"events-{stream}.jsonl"


def _read_path(
    path: Path,
    hours: int,
    event_type: Optional[str],
    correlation_id: Optional[str] = None,
) -> list[Event]:
    if not path.exists():
        return []

    cutoff = _now() - timedelta(hours=hours)
    events: list[Event] = []

    with path.open() as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                e = Event.from_dict(d)
                if e.timestamp < cutoff:
                    continue
                if event_type and e.event_type != event_type:
                    continue
                if correlation_id and e.correlation_id != correlation_id:
                    continue
                events.append(e)
            except json.JSONDecodeError as exc:
                log.warning("[store] malformed JSONL at %s line %d: %s", path.name, line_no, exc)
            except (KeyError, ValueError) as exc:
                log.warning("[store] invalid event at %s line %d: %s", path.name, line_no, exc)

    return list(reversed(events))


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)
