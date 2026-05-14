"""
agentic_govkit/agents/audit.py — AuditAgent.

Observability-first governance agent.  Subscribes to all event streams
and emits structured governance events when configurable thresholds are
breached.  All audit decisions are themselves events on the bus — the
auditor is fully observable and auditable.

Usage::

    from agentic_govkit import AuditAgent
    from agentic_loopkit import EventBus, WILDCARD_STREAM

    bus = EventBus(store_dir=Path("~/.cache/my-app").expanduser())
    audit = AuditAgent("audit", bus, max_delegation_depth=5)
    audit.subscribe(WILDCARD_STREAM)
    bus.register(audit)
"""

from __future__ import annotations

from typing import Any

from agentic_loopkit import AgentBase, Event, EventMeta, TrustLevel, WILDCARD_STREAM
from agentic_govkit.events.models import GovernanceEventType


class AuditAgent(AgentBase):
    """
    Governance observer.  Subscribes to all streams; emits governance events
    for delegation depth violations and untrusted sources.

    Governance events are themselves persisted to the bus (stream:
    'governance'), making the audit trail queryable via the dashboard
    or any downstream subscriber.

    Args:
        name:                 Agent name registered on the bus.
        bus:                  The EventBus instance to publish to.
        max_delegation_depth: Flag events where delegation_depth exceeds
                              this value.  Default: 5.
    """

    def __init__(
        self,
        name: str,
        bus: Any,
        max_delegation_depth: int = 5,
        confidence_threshold: float | None = None,
    ) -> None:
        super().__init__(name, bus)
        self.max_delegation_depth  = max_delegation_depth
        self.confidence_threshold  = confidence_threshold
        self.subscribe(WILDCARD_STREAM)

    # ── OODA pipeline ─────────────────────────────────────────────────────────

    async def observe(self, event: Event) -> dict[str, Any] | None:
        if event.stream == "governance":
            return None  # never audit governance events — prevents loops
        return {"event": event}

    async def orient(self, event: Event, context: dict[str, Any]) -> dict[str, Any] | None:
        e: Event = context["event"]
        flags: list[dict[str, Any]] = []

        if e.delegation_depth > self.max_delegation_depth:
            flags.append({
                "governance_type": GovernanceEventType.DEPTH_EXCEEDED,
                "detail": (
                    f"delegation_depth={e.delegation_depth} "
                    f"exceeds limit={self.max_delegation_depth}"
                ),
            })

        if e.trust_level == TrustLevel.UNTRUSTED:
            flags.append({
                "governance_type": GovernanceEventType.TRUST_ESCALATION,
                "detail": f"source='{e.source}' declared trust_level=untrusted",
            })

        if self.confidence_threshold is not None:
            meta = e.meta()
            if meta is not None:
                confidence = meta.get("confidence")
                if confidence is not None and confidence < self.confidence_threshold:
                    flags.append({
                        "governance_type": GovernanceEventType.CONFIDENCE_BREACH,
                        "detail": (
                            f"confidence={confidence:.3f} "
                            f"below threshold={self.confidence_threshold:.3f}"
                        ),
                        "extra": {
                            "confidence":  confidence,
                            "threshold":   self.confidence_threshold,
                        },
                    })

        return {"flags": flags} if flags else None

    async def decide(self, event: Event, orientation: dict[str, Any]) -> dict[str, Any] | None:
        return orientation  # all flags are acted on unconditionally

    async def act(self, event: Event, action: dict[str, Any]) -> None:
        for flag in action["flags"]:
            governance_type = str(flag["governance_type"])
            detail          = flag["detail"]
            extra           = flag.get("extra", {})
            await self._bus.publish(
                event.caused(
                    governance_type,
                    self.name,
                    {
                        "flagged_event_id":   event.event_id,
                        "flagged_event_type": str(event.event_type),
                        "flagged_source":     event.source,
                        "detail":             detail,
                        **extra,
                        "_meta": EventMeta(
                            phase="act",
                            loop_type="ooda",
                            context=f"Governance audit: {detail}",
                        ).to_dict(),
                    },
                )
            )
