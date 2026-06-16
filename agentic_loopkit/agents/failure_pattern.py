"""
agentic_loopkit/agents/failure_pattern.py — FailurePatternAgent.

Observes the system.* and governance.* event streams; deterministically
clusters error events by (terminal_cause, causal_status, agent_mechanism);
generates a natural-language summary per cluster (LLM phase); and emits
one system.failure_pattern_detected event per distinct failure signature.

Usage::

    class MyFailurePatternAgent(FailurePatternAgent):
        async def materialise(self, events):
            # events = all error events in one cluster
            return await llm.summarise(
                f"Summarise this failure pattern: {format_events(events)}"
            )

    agent = MyFailurePatternAgent("failure-detector", bus)
    agent.subscribe("system", "governance")
    bus.register(agent)

The agent triggers on events whose type contains an error/governance keyword.
Each trigger loads the full system + governance event history, re-clusters, and
emits fresh signatures.  Downstream agents subscribe to the ``system`` stream
and act on ``system.failure_pattern_detected`` events.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..events.models import Event, EventMeta, SystemEventType
from ..events.store import load_all_events
from .projection import ProjectionAgent


# ── Error keyword sets ─────────────────────────────────────────────────────────

_TRIGGER_KEYWORDS: frozenset[str] = frozenset({
    "error", "halt", "quarantine", "override",
    "depth_exceeded", "trust_escalation", "confidence_breach",
})

_ERROR_STATUSES: frozenset[str] = frozenset({
    "error", "rejected", "failed", "halted", "quarantined",
})

# Deterministic (terminal_cause, causal_status) derivation from known event types.
_KNOWN_TYPE_MAP: dict[str, tuple[str, str]] = {
    "governance.halt":              ("halt_enforced",              "halted"),
    "governance.quarantine":        ("source_quarantined",         "quarantined"),
    "governance.human_override":    ("human_override_required",    "overridden"),
    "governance.depth_exceeded":    ("delegation_depth_exceeded",  "flagged"),
    "governance.trust_escalation":  ("untrusted_source",           "flagged"),
    "governance.confidence_breach": ("confidence_below_threshold", "flagged"),
    "governance.dispute_opened":    ("conflict_unresolved",        "disputed"),
    "system.adapter_error":         ("adapter_error",              "error"),
}


# ── FailureSignature ───────────────────────────────────────────────────────────

@dataclass
class FailureSignature:
    """A cluster of related error events sharing a common failure shape."""
    terminal_cause:  str                # "halt_enforced", "max_iterations_exceeded", etc.
    causal_status:   str                # "error", "rejected", "halted", "quarantined", "flagged"
    agent_mechanism: str                # source component (e.g. "killswitch", "ralf-loop")
    count:           int = 0            # events in this cluster
    event_ids:       list[str] = field(default_factory=list)
    pattern_summary: str = ""           # filled by materialise() — the LLM phase


# ── FailurePatternAgent ────────────────────────────────────────────────────────

class FailurePatternAgent(ProjectionAgent):
    """
    Reactive agent that clusters error events and emits structured failure
    signatures.

    Extends ProjectionAgent to inherit stream-loading and OODA wiring, but
    overrides orient() to cluster events before the LLM phase and act() to
    emit system.failure_pattern_detected instead of projection.updated.

    OODA wiring:
      observe()  — gates on error/governance trigger keywords    (deterministic)
      orient()   — loads streams; clusters; calls materialise()  (LLM phase)
      decide()   — pass-through                                  (deterministic)
      act()      — emits system.failure_pattern_detected          (deterministic)

    Subclasses must implement materialise(events) — the LLM phase that
    summarises a single failure cluster from its constituent events.

    Class attribute:
        projection_streams: defaults to ["system", "governance"] when not set
                            via the constructor.
    """

    def __init__(self, name: str, bus: Any, **kwargs: Any) -> None:
        # Default projection_streams to system + governance unless caller overrides.
        if "projection_streams" not in kwargs:
            kwargs["projection_streams"] = ["system", "governance"]
        super().__init__(name, bus, **kwargs)

    # ── Trigger gate ──────────────────────────────────────────────────────────

    def should_materialise(self, event: Event) -> bool:
        """Trigger only on events that carry an error or governance keyword."""
        t = str(event.event_type)
        if any(k in t for k in _TRIGGER_KEYWORDS):
            return True
        return event.payload.get("status", "") in _ERROR_STATUSES

    # ── OODA overrides ────────────────────────────────────────────────────────

    async def orient(
        self, event: Event, context: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """
        Load projection_streams; cluster error events; call materialise() per
        cluster — the primary LLM phase — to generate a human-readable summary.
        """
        streams = self.projection_streams
        source_events: list[Event] = []
        for stream in streams:
            source_events.extend(
                load_all_events(stream, store_dir=self._bus.store_dir)
            )

        signatures = self._cluster_errors(source_events)
        if not signatures:
            return None

        for sig in signatures:
            cluster_events = [e for e in source_events if e.event_id in sig.event_ids]
            sig.pattern_summary = await self.materialise(cluster_events)

        return {
            "signatures":  signatures,
            "event_count": len(source_events),
            "streams":     streams,
        }

    async def act(self, event: Event, action: dict[str, Any]) -> None:
        """Emit system.failure_pattern_detected for each FailureSignature."""
        signatures: list[FailureSignature] = action["signatures"]
        event_count = action["event_count"]
        streams     = action["streams"]

        for sig in signatures:
            await self._bus.publish(
                event.caused(
                    SystemEventType.FAILURE_PATTERN_DETECTED,
                    self.name,
                    {
                        "terminal_cause":  sig.terminal_cause,
                        "causal_status":   sig.causal_status,
                        "agent_mechanism": sig.agent_mechanism,
                        "count":           sig.count,
                        "event_ids":       sig.event_ids,
                        "pattern_summary": sig.pattern_summary,
                        "_meta": EventMeta(
                            phase="act",
                            loop_type="ooda",
                            context=(
                                f"FailurePattern '{sig.terminal_cause}' via "
                                f"'{sig.agent_mechanism}': {sig.count} event(s) "
                                f"across {streams} ({event_count} total loaded)"
                            ),
                        ).to_dict(),
                    },
                )
            )

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def materialise(self, events: list[Event]) -> str:
        """
        PRIMARY LLM PHASE — summarise a failure cluster from its events.

        Receives the subset of events belonging to one FailureSignature cluster.
        Return a concise natural-language summary of what the pattern represents
        and what the likely remediation is.

        Called once per distinct (terminal_cause, causal_status, agent_mechanism)
        cluster, not once per event.
        """
        ...

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _cluster_errors(events: list[Event]) -> list[FailureSignature]:
        """
        Deterministic clustering: group error events by
        (terminal_cause, causal_status, agent_mechanism).

        An event is included when its type contains a recognised error keyword
        OR its payload ``status`` field indicates a failure state.
        """
        clusters: dict[tuple[str, str, str], FailureSignature] = {}

        for event in events:
            t = str(event.event_type)
            payload_status = event.payload.get("status", "")

            is_error_type   = any(k in t for k in _TRIGGER_KEYWORDS)
            is_error_status = payload_status in _ERROR_STATUSES

            if not (is_error_type or is_error_status):
                continue

            if t in _KNOWN_TYPE_MAP:
                terminal_cause, causal_status = _KNOWN_TYPE_MAP[t]
            else:
                terminal_cause = payload_status or t.rsplit(".", 1)[-1]
                causal_status  = payload_status or "error"

            key = (terminal_cause, causal_status, event.source)
            if key not in clusters:
                clusters[key] = FailureSignature(
                    terminal_cause=terminal_cause,
                    causal_status=causal_status,
                    agent_mechanism=event.source,
                )
            clusters[key].count += 1
            clusters[key].event_ids.append(event.event_id)

        return list(clusters.values())
