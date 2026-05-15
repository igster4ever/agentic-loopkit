"""
agentic_govkit/agents/killswitch.py — KillSwitchAgent.

Policy enforcement agent.  Subscribes to the governance stream and executes
configurable enforcement actions when governance thresholds are breached.

Phase B of the two-phase governance model:
  Phase A (AuditAgent)      — observe all streams; emit governance.* flags
  Phase B (KillSwitchAgent) — subscribe to governance.*; enforce policy

All enforcement decisions are themselves events at TrustLevel.HIGH, consistent
with Phase A and making the enforcer fully observable and auditable.

Usage::

    from agentic_govkit import KillSwitchAgent, GovernanceEventType
    from agentic_govkit.agents.killswitch import halt_correlation, emit_human_override

    kill = KillSwitchAgent("killswitch", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED:    halt_correlation,
        GovernanceEventType.TRUST_ESCALATION:  emit_human_override,
        GovernanceEventType.CONFIDENCE_BREACH: emit_human_override,
    })
    kill.subscribe("governance")
    bus.register(kill)

Design principles:
  - Subscribes to governance.* only, not * — reacts to decisions, not raw events
  - Never calls AuditAgent directly — receives its output as bus events
  - Disabled by default — must be explicitly instantiated; purely opt-in
  - Self-excludes events it emits (by source name) to prevent feedback loops
"""

from __future__ import annotations

from typing import Any, Callable

from agentic_loopkit import AgentBase, Event, EventMeta, TrustLevel
from agentic_govkit.events.models import GovernanceEventType


# ── Enforcement action type ───────────────────────────────────────────────────

# EnforcementAction: callable that takes (agent_name, trigger_event) and
# returns the enforcement Event to publish.
EnforcementAction = Callable[[str, "Event"], "Event"]


# ── Built-in enforcement actions ─────────────────────────────────────────────

def halt_correlation(agent_name: str, trigger_event: Event) -> Event:
    """
    Halt processing of a correlation chain.

    Emits governance.halt.  Downstream agents should check for halt events
    in their observe() phases and skip events with a matching correlation_id.
    """
    e = trigger_event.caused(
        GovernanceEventType.HALT,
        agent_name,
        {
            "reason":         "correlation chain halted by policy",
            "correlation_id": trigger_event.correlation_id,
            "triggered_by":   str(trigger_event.event_type),
            "_meta": EventMeta(
                phase="act",
                loop_type="ooda",
                context=f"Halt: {trigger_event.event_type} triggered halt_correlation policy",
            ).to_dict(),
        },
    )
    e.trust_level = TrustLevel.HIGH
    return e


def quarantine_source(agent_name: str, trigger_event: Event) -> Event:
    """
    Quarantine an event source.

    Emits governance.quarantine.  The quarantined source name is extracted
    from the governance flag payload (``flagged_source``) if present;
    falls back to the trigger event's own source field.
    """
    source = trigger_event.payload.get("flagged_source", trigger_event.source)
    e = trigger_event.caused(
        GovernanceEventType.QUARANTINE,
        agent_name,
        {
            "reason":              "source quarantined by policy",
            "quarantined_source":  source,
            "triggered_by":        str(trigger_event.event_type),
            "_meta": EventMeta(
                phase="act",
                loop_type="ooda",
                context=f"Quarantine: {trigger_event.event_type} triggered quarantine_source policy on '{source}'",
            ).to_dict(),
        },
    )
    e.trust_level = TrustLevel.HIGH
    return e


def emit_human_override(agent_name: str, trigger_event: Event) -> Event:
    """
    Escalate to human review.

    Emits governance.human_override.  The payload includes the triggering
    event type and flagged_event_id so a human reviewer can trace back to
    the original flagged event.
    """
    e = trigger_event.caused(
        GovernanceEventType.HUMAN_OVERRIDE,
        agent_name,
        {
            "reason":           "escalated to human review by policy",
            "triggered_by":     str(trigger_event.event_type),
            "flagged_event_id": trigger_event.payload.get("flagged_event_id"),
            "_meta": EventMeta(
                phase="act",
                loop_type="ooda",
                context=f"Human override: {trigger_event.event_type} triggered emit_human_override policy",
            ).to_dict(),
        },
    )
    e.trust_level = TrustLevel.HIGH
    return e


# ── KillSwitchAgent ───────────────────────────────────────────────────────────

class KillSwitchAgent(AgentBase):
    """
    Policy enforcement agent.  Subscribes to the governance stream and
    executes configured enforcement actions on matching event types.

    Args:
        name:   Agent name registered on the bus.
        bus:    The EventBus instance.
        policy: Mapping from GovernanceEventType to an EnforcementAction.
                Events whose type is not in the policy are silently ignored.
    """

    def __init__(
        self,
        name: str,
        bus: Any,
        policy: dict[str, EnforcementAction],
    ) -> None:
        super().__init__(name, bus)
        self.policy = policy

    # ── OODA pipeline ─────────────────────────────────────────────────────────

    async def observe(self, event: Event) -> dict[str, Any] | None:
        if event.source == self.name:
            return None  # never react to our own enforcement events — prevents loops
        return {"event": event}

    async def orient(self, event: Event, context: dict[str, Any]) -> dict[str, Any] | None:
        trigger: Event = context["event"]
        action_fn = self.policy.get(str(trigger.event_type))
        if action_fn is None:
            return None
        return {"action_fn": action_fn, "trigger": trigger}

    async def decide(self, event: Event, orientation: dict[str, Any]) -> dict[str, Any] | None:
        return orientation  # all policy matches are enforced unconditionally

    async def act(self, event: Event, action: dict[str, Any]) -> None:
        action_fn: EnforcementAction = action["action_fn"]
        trigger: Event               = action["trigger"]
        enforcement_event = action_fn(self.name, trigger)
        await self._bus.publish(enforcement_event)
