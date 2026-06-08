# GovernanceLearningAgent — Design Brief

_Date: 2026-06-08_
_Status: Draft — v0.1_
_Informs: agentic_govkit v2 — adaptive governance_

---

## North Star

Current govkit observes, flags, and enforces — but it does not *learn*. The same thresholds
that were configured at startup remain unchanged regardless of how the system behaves over time.
`GovernanceLearningAgent` closes the adaptive loop: it analyses governance event patterns and
emits `governance.policy_recommendation` events, giving operators and automated systems a
signal-driven path to tuning policy without manual threshold management.

---

## The Gap

From the AIMA Learning Agent 4-module decomposition:

| Module | Current govkit equivalent | Gap |
|---|---|---|
| Performance Element (decisions) | `KillSwitchAgent` | ✅ covered |
| Critic (evaluates + corrects) | `AuditAgent` | ✅ covered |
| Learning Element (improves performance) | — | **missing** |
| Problem Generator (explores) | — | missing (loopkit-layer concern) |

`GovernanceLearningAgent` is the **Learning Element**: it processes audit history and
recommends policy adjustments. It does not enforce — that remains `KillSwitchAgent`'s role.
The separation preserves the existing one-way dependency chain:

```
GovernanceLearningAgent → emits governance.policy_recommendation
KillSwitchAgent         → may subscribe to governance.policy_recommendation and re-read policy
```

---

## Package Location

`agentic_govkit/agents/learning.py`

Exported from `agentic_govkit/__init__.py` alongside `AuditAgent` and `KillSwitchAgent`.

---

## New Event Types

Added to `GovernanceEventType` in `agentic_govkit/events/models.py`:

```python
class GovernanceEventType(StrEnum):
    # ... existing members ...
    POLICY_RECOMMENDATION = "governance.policy_recommendation"  # NEW
    POLICY_APPLIED        = "governance.policy_applied"         # NEW — emitted by consumer
```

`POLICY_APPLIED` is emitted by the agent or operator that *acted* on a recommendation —
`GovernanceLearningAgent` does not emit it. This keeps Learning Agent output read-only.

---

## Abstract Interface

```python
class GovernanceLearningAgent(AgentBase):
    """
    Learning Element for the govkit governance layer.

    Subscribes to the governance stream. Accumulates governance events over a
    rolling window; on trigger, calls analyse() to identify policy drift and
    emits governance.policy_recommendation events.

    OODA wiring:
        observe()  — accumulates governance events in a rolling window;
                     returns context when analysis window is full or trigger fires
        orient()   — LLM phase: identify patterns, recommend threshold adjustments
        decide()   — filter low-confidence recommendations (< min_confidence)
        act()      — emit governance.policy_recommendation for each recommendation

    Self-exclusion: GovernanceLearningAgent never analyses its own emitted events
    (source == self.name) — same pattern as AuditAgent's governance stream exclusion.
    """

    #: Number of governance events to accumulate before triggering analysis.
    analysis_window: int = 20

    #: Minimum confidence to emit a recommendation.
    min_confidence: float = 0.65

    #: How far back to load governance history for analysis (hours).
    history_hours: int = 72

    @abstractmethod
    async def analyse(
        self, events: list["Event"]
    ) -> list["PolicyRecommendation"]:
        """
        PRIMARY LLM PHASE — called from orient().

        Receives governance events from the rolling window.
        Returns a list of PolicyRecommendations.

        Patterns to look for:
        - Repeated confidence_breach on same stream → lower confidence_threshold for that stream
        - Repeated depth_exceeded from same source → lower max_delegation_depth
        - trust_escalation cluster → consider quarantine policy for that source
        - Absence of any flags for N sessions → thresholds may be too permissive
        - High false-positive rate (human_override after halt/quarantine) → thresholds too strict
        """
```

---

## New Dataclass

```python
@dataclass
class PolicyRecommendation:
    policy_key:     str     # which policy parameter to adjust, e.g. "confidence_threshold"
    current_value:  Any     # current configured value
    recommended_value: Any  # suggested new value
    rationale:      str     # why — pattern observed in governance events
    confidence:     float   # 0.0–1.0 — how confident the agent is in this recommendation
    evidence_event_ids: list[str] = field(default_factory=list)  # governance events that support this
    tags:           list[str] = field(default_factory=list)
```

---

## Emitted Event

```python
Event(
    event_type     = GovernanceEventType.POLICY_RECOMMENDATION,
    source         = agent.name,
    trust_level    = TrustLevel.HIGH,   # governance components always HIGH
    payload = {
        "policy_key":          "confidence_threshold",
        "current_value":       0.4,
        "recommended_value":   0.55,
        "rationale":           "17 of 20 confidence_breach events in last 72h on gps stream...",
        "confidence":          0.82,
        "evidence_event_ids":  ["evt-abc123", "evt-def456", ...],
        "tags":                ["confidence", "gps"],
        "_meta": {
            "phase":      "orient",
            "loop_type":  "ooda",
            "confidence": 0.82,
            "context":    "Analysis window: 20 events. 3 recommendations generated.",
        }
    }
)
```

---

## OODA Wiring (internal)

```
observe()   accumulates governance events into rolling window buffer
            returns context dict when:
              - window reaches analysis_window size, OR
              - event.event_type == SystemEventType.BUS_STARTED (startup analysis)
            self-excludes events where event.source == self.name
            excludes governance.policy_recommendation + governance.policy_applied
            (prevents feedback loop from analysing own output)
            returns None otherwise (no trigger yet)

orient()    loads history_hours of governance events from store
            combines with buffered window
            calls analyse() → list[PolicyRecommendation]
            wraps each in EventMeta

decide()    filters recommendations below min_confidence
            returns None if list is empty

act()       emits one governance.policy_recommendation per recommendation
            clears the rolling window buffer
```

---

## Usage Example

```python
class GpsGovernanceLearner(GovernanceLearningAgent):
    analysis_window = 15
    history_hours   = 48

    async def analyse(self, events: list[Event]) -> list[PolicyRecommendation]:
        breach_events = [
            e for e in events
            if e.event_type == GovernanceEventType.CONFIDENCE_BREACH
        ]
        if len(breach_events) > 10:
            avg_confidence = sum(
                e.payload.get("confidence", 0) for e in breach_events
            ) / len(breach_events)
            return [
                PolicyRecommendation(
                    policy_key        = "confidence_threshold",
                    current_value     = 0.4,
                    recommended_value = round(avg_confidence + 0.05, 2),
                    rationale         = f"{len(breach_events)} breaches; avg confidence {avg_confidence:.2f}",
                    confidence        = 0.75,
                    evidence_event_ids= [e.event_id for e in breach_events[:5]],
                    tags              = ["confidence"],
                )
            ]
        return []


learner = GpsGovernanceLearner("governance-learner", bus)
learner.subscribe("governance")
bus.register(learner)
```

---

## Applying Recommendations — Two Patterns

`GovernanceLearningAgent` emits recommendations; it does not apply them.
Two patterns for consuming `governance.policy_recommendation`:

### Pattern A — Human-in-the-loop

Dashboard surfaces `governance.policy_recommendation` events. Operator reviews and
manually adjusts `AuditAgent` + `KillSwitchAgent` configuration, emitting
`governance.policy_applied` as confirmation.

### Pattern B — Automated (advanced)

`KillSwitchAgent` subscribes to `governance.policy_recommendation` as an additional input.
On receipt, it re-reads its policy dict from a `MemoryStore`-backed configuration:

```python
class AdaptiveKillSwitch(KillSwitchAgent):
    async def observe(self, event):
        if event.event_type == GovernanceEventType.POLICY_RECOMMENDATION:
            return {"recommendation": event.payload}
        return await super().observe(event)

    async def act(self, event, action):
        if "recommendation" in action:
            # Apply if confidence >= threshold AND human has not overridden
            await self._memory.write(
                key=action["recommendation"]["policy_key"],
                value=str(action["recommendation"]["recommended_value"]),
                agent_id=self.name,
                confidence=action["recommendation"]["confidence"],
            )
            await self._bus.publish(event.caused(
                GovernanceEventType.POLICY_APPLIED, self.name,
                {"policy_key": action["recommendation"]["policy_key"]}
            ))
        else:
            await super().act(event, action)
```

Pattern B requires `agentic_memorykit` for config persistence. It is an advanced use case;
Pattern A is the recommended default.

---

## Design Decisions

1. **Learning Element, not Enforcement** — `GovernanceLearningAgent` emits *recommendations*;
   it never modifies `AuditAgent` or `KillSwitchAgent` directly. This preserves the governance
   chain: observe → flag → enforce → recommend → (optionally) adapt.

2. **Self-exclusion from analysis** — same rule as `AuditAgent`'s governance stream exclusion.
   Analysing your own recommendation events would create a feedback loop where the learner
   learns to generate more of its own recommendations regardless of signal quality.

3. **Rolling window + history hybrid** — the `analysis_window` buffer catches recent patterns;
   `history_hours` provides the broader context needed to distinguish noise from signal. Both
   are needed: recency alone misses slow drift; history alone misses sudden threshold violations.

4. **`governance.policy_recommendation` carries full evidence trail** — `evidence_event_ids`
   allows an operator (or automated consumer) to load the original events that drove the
   recommendation. This is governance archaeology at the governance layer.

5. **`trust_level = TrustLevel.HIGH` on output** — all govkit agent outputs use HIGH trust.
   Recommendations are not less trustworthy than enforcement decisions. Consistency matters
   for downstream consumers who filter by trust level.

6. **`POLICY_APPLIED` is emitted by the *consumer*, not the learner** — the learner cannot
   know whether its recommendation was accepted. `POLICY_APPLIED` exists so the learner can
   detect that the feedback loop is working (or not) in a future analysis pass.

---

## Relationship to Existing Govkit Components

```
AuditAgent         — observes all streams, emits governance.*  (Critic)
KillSwitchAgent    — enforces policy on governance.*           (Performance Element)
ConflictResolutionExecutor — mediates disputes                  (Mediator)
GovernanceLearningAgent    — analyses patterns, recommends      (Learning Element)  ← NEW
```

All four are participants on the same bus. None call each other directly.
All communicate only via published events.

---

## Public API Addition

```python
# agentic_govkit/__init__.py
from .agents.learning import GovernanceLearningAgent, PolicyRecommendation
```

---

## Event Catalog Addition

`docs/event-catalog.md` — add to `governance.*` table:

| Event type | Published by | Condition | Typical subscribers |
|---|---|---|---|
| `governance.policy_recommendation` | `GovernanceLearningAgent` | Analysis window full or bus started | operators, `KillSwitchAgent` subclasses, dashboard |
| `governance.policy_applied` | Consumer of recommendation | Recommendation accepted and applied | audit log, `GovernanceLearningAgent` (feedback) |

---

## Test Targets

`tests/govkit/test_learning_agent.py`

- `analyse()` called with accumulated governance events
- Self-exclusion: agent's own events not included in analysis window
- `governance.policy_recommendation` + `governance.policy_applied` excluded from window
- Rolling window clears after `act()` emits recommendations
- `min_confidence` filters low-confidence recommendations
- `BUS_STARTED` triggers analysis regardless of window size
- `TrustLevel.HIGH` on all emitted events
- `evidence_event_ids` in payload references actual governance event IDs
- `_meta` present with `phase="orient"`, `loop_type="ooda"`
- Module boundary: `GovernanceLearningAgent` imports only from `agentic_loopkit` public API

---

## Estimated Scope

~140 lines implementation, ~100 lines tests.
