"""
agentic-govkit — governance layer for agentic-loopkit.

Provides observability enrichment and audit primitives that sit above the
loopkit EventBus without coupling to its internals.  Communication between
govkit and loopkit is exclusively via published Events — never direct calls.

Module boundary contract:
    agentic_govkit  →  agentic_loopkit (public API only)
    agentic_loopkit →  agentic_govkit  (NEVER — one-way dependency)

Published events (stream: 'governance'):
    governance.audit_flagged    — generic governance flag
    governance.depth_exceeded   — delegation_depth exceeded threshold
    governance.trust_escalation — source declared TrustLevel.UNTRUSTED
    governance.confidence_breach — _meta.confidence below configured threshold
    governance.dispute_opened   — competing agent interpretations of same entity
    governance.dispute_resolved — dispute closed (consensus or human override)
    governance.human_override   — HIGH-trust human decision supersedes agent synthesis

Subscribed streams:
    * (all streams) — AuditAgent is a wildcard observer

Public API:
    AuditAgent          — registers on bus, monitors all streams, emits governance events
    GovernanceEventType — StrEnum of governance event types
"""

from agentic_govkit.agents.audit import AuditAgent
from agentic_govkit.events.models import GovernanceEventType

__all__ = [
    "AuditAgent",
    "GovernanceEventType",
]
