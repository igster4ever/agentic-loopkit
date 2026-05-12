"""
agentic_loopkit/events/confidence.py — Confidence aggregation utility.

Aggregates per-event confidence scores (from payload["_meta"]["confidence"])
into a single weighted score for a collection of events.

Weights are derived from two provenance signals already on every Event:
  - trust_level:       HIGH=3, MEDIUM=2, LOW=1, UNTRUSTED=0
  - delegation_depth:  decays as 1 / (1 + depth)

Events without _meta.confidence are skipped — absence of a confidence signal
is not treated as zero confidence.

Returns None when no events carry usable confidence data (e.g. all UNTRUSTED,
or no _meta.confidence fields present at all).
"""

from __future__ import annotations

from typing import Optional

from .models import Event, TrustLevel


_TRUST_WEIGHT: dict[TrustLevel, float] = {
    TrustLevel.HIGH:      3.0,
    TrustLevel.MEDIUM:    2.0,
    TrustLevel.LOW:       1.0,
    TrustLevel.UNTRUSTED: 0.0,
}


def aggregate_confidence(events: list[Event]) -> Optional[float]:
    """
    Weighted mean of _meta.confidence across a collection of events.

    Weight per event = trust_weight × depth_decay, where:
      trust_weight = TrustLevel ordinal (HIGH=3, MEDIUM=2, LOW=1, UNTRUSTED=0)
      depth_decay  = 1 / (1 + delegation_depth)

    UNTRUSTED events contribute no signal even if they carry a high
    self-declared confidence — intentional; the trustworthiness of the
    declarer determines whether the confidence claim is credited.

    Returns None if total weight is zero (all events are UNTRUSTED,
    have no _meta, or carry no confidence field).
    """
    weighted_sum = 0.0
    total_weight = 0.0

    for event in events:
        meta = event.meta()
        if meta is None:
            continue
        confidence = meta.get("confidence")
        if confidence is None:
            continue

        trust_weight = _TRUST_WEIGHT.get(event.trust_level, 0.0)
        depth_decay  = 1.0 / (1.0 + event.delegation_depth)
        weight       = trust_weight * depth_decay

        weighted_sum += confidence * weight
        total_weight += weight

    if total_weight == 0.0:
        return None

    return weighted_sum / total_weight
