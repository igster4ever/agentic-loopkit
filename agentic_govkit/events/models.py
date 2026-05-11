"""
agentic_govkit/events/models.py — Governance event types.

All governance events land on the 'governance' stream so they can be
filtered, subscribed to, and inspected independently of domain events.
AuditAgent self-excludes from auditing this stream to prevent loops.
"""

from enum import StrEnum


class GovernanceEventType(StrEnum):
    """Published events emitted by govkit components.  Stream: 'governance'."""
    AUDIT_FLAGGED     = "governance.audit_flagged"      # generic flag, detail in payload
    DEPTH_EXCEEDED    = "governance.depth_exceeded"     # delegation_depth > threshold
    TRUST_ESCALATION  = "governance.trust_escalation"   # source declared UNTRUSTED
