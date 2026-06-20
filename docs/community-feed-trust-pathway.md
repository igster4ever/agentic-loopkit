# Community Feed Trust Pathway

*How to wire AuditAgent + GovernanceLearningAgent for external community feeds*

---

## Overview

Community feeds are external JSONL sources ingested by `CommunityFeedAdapter`. All events
from these feeds start at `TrustLevel.UNTRUSTED`. This is not a permanent state — it is a
conservative starting point. Trust can be graduated upward as a source demonstrates compliant
behaviour. Graduation is handled by the governance layer, not by the adapter itself.

**Why one level at a time?** `aggregate_confidence()` applies a weight decay based on
`TrustLevel` ordinal (HIGH=3, MEDIUM=2, LOW=1, UNTRUSTED=0). Skipping from UNTRUSTED
directly to MEDIUM would produce an artificially inflated confidence score on the first
page/projection that consumes the events. Stepping through LOW first gives the confidence
aggregation a stable intermediate signal.

---

## Trust graduation levels

```
UNTRUSTED ──► LOW ──► MEDIUM ──► HIGH
   (start)   (observed OK)  (validated)  (fully trusted)
```

Each promotion requires a `governance.policy_recommendation` emitted by
`CommunityTrustLearner` and acted on by the consumer.

---

## Components

| Component              | Role                                                        | Where              |
|------------------------|-------------------------------------------------------------|--------------------|
| `CommunityFeedAdapter` | Reads JSONL feed; emits `community.entry_received`          | `agentic_loopkit`  |
| `AuditAgent`           | Catches all UNTRUSTED events; emits `governance.trust_escalation` | `agentic_govkit`   |
| `CommunityTrustLearner`| Accumulates trust_escalation signals; recommends promotion  | `agentic_govkit`   |
| `KillSwitchAgent`      | Optional: halt/quarantine if violations occur               | `agentic_govkit`   |
| Consumer handler       | Applies `policy_recommendation` — promotes trust level      | Your codebase      |

---

## Minimal wiring

```python
from pathlib import Path
from agentic_loopkit import EventBus, CommunityFeedAdapter
from agentic_govkit import AuditAgent, GovernanceEventType
from agentic_govkit.agents.community_trust import CommunityTrustLearner

bus = EventBus(store_dir=Path("~/.cache/my-app").expanduser())

# 1. Audit: catches every UNTRUSTED event → emits governance.trust_escalation
audit = AuditAgent("audit", bus)
bus.register(audit)

# 2. Trust learner: accumulates trust_escalation signals → recommends promotion
learner = CommunityTrustLearner("community-trust", bus)
learner.subscribe("governance")
bus.register(learner)

# 3. Community feed adapter: reads JSONL, emits UNTRUSTED events
adapter = CommunityFeedAdapter(
    bus       = bus,
    feed_path = Path("/var/feeds/external-insights.jsonl"),
    name      = "external-insights",   # used as cursor key
)
bus.add_adapter(adapter)

# 4. Handle promotion recommendations in your application loop
async def on_governance(event):
    if event.event_type == GovernanceEventType.POLICY_RECOMMENDATION:
        if event.payload.get("policy_key") == "source_trust_graduation":
            source   = event.payload["tags"][2]   # third tag is the source name
            from_lvl = event.payload["current_value"]
            to_lvl   = event.payload["recommended_value"]
            print(f"Promote {source}: {from_lvl} → {to_lvl}")
            # Update your application's trust registry here

bus.router.subscribe("governance", on_governance)

await bus.start()
# Tick adapter on a schedule: await adapter.tick()
```

---

## Default thresholds

`CommunityTrustLearner` has two configurable thresholds:

| Attribute          | Default | Meaning                                                           |
|--------------------|---------|-------------------------------------------------------------------|
| `analysis_window`  | 20      | Governance events to accumulate before calling `analyse()`        |
| `min_observations` | 5       | Minimum trust_escalation count before a source is considered      |
| `halt_veto_window` | 50      | If any halt/quarantine appears in this many recent events, skip   |
| `min_confidence`   | 0.65    | Recommendations below this confidence are discarded (inherited)   |

For a new feed you probably want to lower `analysis_window` and `min_observations`
during evaluation, then raise them for production:

```python
learner = CommunityTrustLearner("community-trust", bus)
learner.analysis_window  = 5    # trigger sooner in evaluation
learner.min_observations = 5    # match analysis_window
```

---

## Confidence scoring

Confidence scales with the number of trust_escalation events above `min_observations`:

```
confidence = min(0.9, 0.65 + (count - min_observations) × 0.02)
```

A source with exactly `min_observations` events gets confidence 0.65 (just above
`min_confidence`). At 25 events above threshold, confidence caps at 0.90. The cap
prevents any community source from ever reaching 1.0 — that level is reserved for
fully trusted internal components.

---

## Halt / quarantine veto

If `KillSwitchAgent` has halted or quarantined a source, `CommunityTrustLearner`
will not recommend promotion for that source. The veto is scanned over the last
`halt_veto_window` governance events per source (default: 50).

Add `KillSwitchAgent` to enforce halt/quarantine on depth or trust violations:

```python
from agentic_govkit import KillSwitchAgent, GovernanceEventType
from agentic_govkit.agents.killswitch import quarantine_source

ks = KillSwitchAgent("killswitch", bus, policy={
    GovernanceEventType.TRUST_ESCALATION: quarantine_source,
})
ks.subscribe("governance")
bus.register(ks)
```

**Note:** If KillSwitchAgent quarantines on every `trust_escalation`, no community
source will ever graduate to LOW. Either remove `TRUST_ESCALATION` from the policy,
or use a more targeted policy that only quarantines when combined with other flags.

---

## Extending CommunityTrustLearner

Override `analyse()` to implement custom promotion logic, e.g. requiring payload
schema validation before recommending promotion:

```python
class StrictTrustLearner(CommunityTrustLearner):
    min_observations = 10

    async def analyse(self, events):
        recs = await super().analyse(events)
        # Only recommend if all payloads contain a required field
        validated = [r for r in recs if self._all_payloads_valid(events, r)]
        return validated

    def _all_payloads_valid(self, events, rec):
        source = rec.tags[2]
        source_events = [
            e for e in events
            if e.payload and e.payload.get("flagged_source") == source
        ]
        # ... your validation logic
        return True
```

---

## Event catalog

| Event type                              | Emitter                 | Payload fields                                                  |
|-----------------------------------------|-------------------------|-----------------------------------------------------------------|
| `community.entry_received`              | `CommunityFeedAdapter`  | full JSONL entry; `trust_level=UNTRUSTED`                       |
| `governance.trust_escalation`           | `AuditAgent`            | `flagged_source`, `flagged_event_type`, `flagged_event_id`      |
| `governance.policy_recommendation`      | `CommunityTrustLearner` | `policy_key`, `current_value`, `recommended_value`, `rationale`, `confidence`, `tags` |
| `governance.halt` / `governance.quarantine` | `KillSwitchAgent`   | `source`, `correlation_id`                                      |

---

## See also

- [`docs/polling-adapter-extension-pattern.md`](polling-adapter-extension-pattern.md) — how to subclass `PollingAdapter` for custom feeds
- [`docs/architecture.md`](architecture.md) — full component roles and data flow
- [`docs/event-catalog.md`](event-catalog.md) — all event types including `governance.*`
- [`agentic_govkit/agents/community_trust.py`](../agentic_govkit/agents/community_trust.py) — `CommunityTrustLearner` source
- [`agentic_loopkit/adapters/community.py`](../agentic_loopkit/adapters/community.py) — `CommunityFeedAdapter` source
