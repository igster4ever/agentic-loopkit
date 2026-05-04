"""
agentic_loopkit/events/models.py — Event dataclass and system event types.

EventType is intentionally open: consuming applications define their own
domain event types as StrEnum subclasses and pass them as the event_type
field.  The loopkit only defines system-level events.

    from enum import StrEnum
    class GpsEventType(StrEnum):
        CYCLE_COMPLETE = "gps.cycle_complete"
        RECORD_NEW     = "gps.record_new"

    event = Event(event_type=GpsEventType.CYCLE_COMPLETE, source="scheduler", payload={})

Traceability fields:
    causation_id   — event_id of the event that caused this one
    correlation_id — business workflow ID (e.g. ClickUp task ID, GPS run ID)
                     threads all events in a workflow back to a common root
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

class SystemEventType(StrEnum):
    """Built-in loopkit system events.  Stream: 'system'."""
    BUS_STARTED     = "system.bus_started"
    BUS_STOPPED     = "system.bus_stopped"
    AGENT_STARTED   = "system.agent_started"
    AGENT_STOPPED   = "system.agent_stopped"
    ADAPTER_TICK    = "system.adapter_tick"
    ADAPTER_ERROR   = "system.adapter_error"
    LOOP_STARTED    = "system.loop_started"
    LOOP_COMPLETE   = "system.loop_complete"
    LOOP_REJECTED   = "system.loop_rejected"


WILDCARD_STREAM = "*"  # subscribe to all streams


@dataclass
class EventMeta:
    """
    Optional loopkit framework metadata, stored at ``payload["_meta"]``.

    Consumers embed this to give the dashboard a reliable inspection point
    without constraining the top-level payload structure.  All fields are
    optional — include only what is relevant for a given event.

    Usage::

        from agentic_loopkit.events.models import EventMeta

        payload = {
            **domain_data,
            "_meta": EventMeta(
                phase="act",
                loop_type="react",
                confidence=0.82,
                context="Tool search: looking for recent ADR events",
            ).to_dict()
        }

    Read back via ``event.meta()`` on any ``Event`` instance.
    """

    phase:      Optional[str]   = None  # "observe"|"orient"|"decide"|"act"|"think"|"execute"|"retrieve"|"learn"
    loop_type:  Optional[str]   = None  # "ooda"|"ralf"|"react"|"plan"|"reflexion"
    iteration:  Optional[int]   = None  # step/iteration number within the loop
    confidence: Optional[float] = None  # 0.0–1.0
    context:    Optional[str]   = None  # human-readable reasoning for dashboard Context tab
    tags:       list[str]       = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dict, omitting None values and empty tag lists."""
        out: dict[str, Any] = {}
        if self.phase      is not None: out["phase"]      = self.phase
        if self.loop_type  is not None: out["loop_type"]  = self.loop_type
        if self.iteration  is not None: out["iteration"]  = self.iteration
        if self.confidence is not None: out["confidence"] = self.confidence
        if self.context    is not None: out["context"]    = self.context
        if self.tags:                   out["tags"]        = list(self.tags)
        return out


@dataclass
class Event:
    """
    One discrete occurrence in an agentic-loopkit system.

    event_type     — any StrEnum value or plain dot-namespaced string
                     (convention: "<stream>.<action>", e.g. "gps.cycle_complete")
    source         — component that emitted this event
    payload        — arbitrary domain data
    stream         — top-level routing key; auto-derived from event_type prefix
    causation_id   — event_id of the direct cause (enables event chain tracing)
    correlation_id — business workflow identifier shared by all events in a flow
    """

    event_type:     str
    source:         str
    payload:        dict[str, Any]
    stream:         str            = ""
    event_id:       str            = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:      datetime       = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    causation_id:   Optional[str]  = None
    correlation_id: Optional[str]  = None

    def __post_init__(self) -> None:
        if not self.stream:
            self.stream = str(self.event_type).split(".")[0]

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "event_id":       self.event_id,
            "event_type":     str(self.event_type),
            "stream":         self.stream,
            "source":         self.source,
            "timestamp":      _iso(self.timestamp),
            "payload":        self.payload,
            "causation_id":   self.causation_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            event_id       = d["event_id"],
            event_type     = d["event_type"],   # kept as plain string on load
            stream         = d["stream"],
            source         = d["source"],
            timestamp      = _parse(d["timestamp"]),
            payload        = d.get("payload", {}),
            causation_id   = d.get("causation_id"),
            correlation_id = d.get("correlation_id"),
        )

    def caused(self, event_type: str, source: str, payload: dict) -> "Event":
        """Convenience: create a child event that traces back to this one."""
        return Event(
            event_type     = event_type,
            source         = source,
            payload        = payload,
            causation_id   = self.event_id,
            correlation_id = self.correlation_id,
        )

    def meta(self) -> Optional[dict[str, Any]]:
        """Return the EventMeta dict from ``payload["_meta"]``, or None if absent."""
        return self.payload.get("_meta")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
