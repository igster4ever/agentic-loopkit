# Session Artefact — v7-2: CommunityFeedAdapter & Trust Graduation Pathway

_Date: 2026-06-20 · Namespace: agentic-loopkit · Commit: 166bba6_

---

## What was built

### `agentic_loopkit/adapters/community.py`

A JSONL polling adapter for community feeds. Extends `PollingAdapter`; emits
`community.entry_received` events at `TrustLevel.UNTRUSTED` by default.

**Design decisions:**

| Decision | Rationale |
|---|---|
| Byte-offset cursor | Line-number cursors break on JSONL compaction or file rotation. `f.tell()` is the only stable position signal. |
| Truncation reset | If stored offset > current file size, assume rotation and reset to 0. Loses no entries; may re-read a small window after rotation — acceptable for community feeds. |
| `default_trust_level` param | Single parameter covers the JsonlFeedAdapter use case without a subclass. Verdict: JsonlFeedAdapter not justified. |
| `_entry_to_event()` hook | Overridable for consumers that need domain-specific event types without forking the adapter. |
| `correlation_id` from `id` → `correlation_id` → None | Standard JSONL entry fields; no domain assumption. |

```
community.entry_received
  source        = adapter.name
  trust_level   = TrustLevel.UNTRUSTED  (default)
  correlation_id = entry["id"] or entry["correlation_id"] or None
  payload       = raw JSONL entry dict
```

### `agentic_govkit/agents/community_trust.py`

A concrete `GovernanceLearningAgent` that implements UNTRUSTED→LOW trust graduation.

**Algorithm:**

```
for each source in governance.trust_escalation events:
  if count(trust_escalations) < min_observations:    skip
  if any(halt | quarantine) in recent halt_veto_window events: skip
  confidence = min(0.9, 0.65 + (count - min_observations) × 0.02)
  emit governance.policy_recommendation(
    policy_key        = "source_trust_graduation"
    current_value     = "UNTRUSTED"
    recommended_value = "LOW"
    confidence        = <computed>
    tags              = ["trust-graduation", "community", <source>]
  )
```

**Key thresholds (all overridable as class attributes):**

| Attribute | Default | Meaning |
|---|---|---|
| `analysis_window` | 20 | Governance events to accumulate before triggering `analyse()` |
| `min_observations` | 5 | Minimum trust_escalation count before considering a source |
| `halt_veto_window` | 50 | Scan this many recent events per source for halt/quarantine |
| `min_confidence` | 0.65 | Inherited from GovernanceLearningAgent; discards below threshold |

**Confidence cap at 0.90:** community sources are external; they should never reach 1.0.
The cap signals to downstream consumers that this recommendation has been machine-generated,
not manually verified.

### `docs/community-feed-trust-pathway.md`

Consumer reference guide covering:
- Minimal wiring (EventBus + AuditAgent + CommunityTrustLearner + adapter)
- Threshold configuration
- Confidence scoring formula
- Halt/quarantine veto interaction with KillSwitchAgent
- CommunityTrustLearner extension pattern

---

## Architecture: the full chain

```
CommunityFeedAdapter.tick()
  └── poll(cursor)
       └── reads JSONL feed → emits community.entry_received (UNTRUSTED)
             │
             ▼  bus.publish() → persist-before-fanout
             │
       AuditAgent.observe()
         └── event.trust_level == UNTRUSTED
               └── emits governance.trust_escalation
                     payload["flagged_source"] = adapter.name  ← KEY: not "source"
                     │
                     ▼
             CommunityTrustLearner._handle(governance.trust_escalation)
               ├── observe(): event_type matches, accumulate to rolling window
               ├── orient(): window full → calls analyse()
               │     └── counts escalations by flagged_source
               │           checks halt/quarantine veto
               │           computes confidence
               │           returns [PolicyRecommendation, ...]
               ├── decide(): filters by min_confidence
               └── act(): emits governance.policy_recommendation
                     payload["policy_key"] = "source_trust_graduation"
                     payload["recommended_value"] = "LOW"
                     trust_level = TrustLevel.HIGH  ← governance events are authoritative
```

---

## Key discovery: `flagged_source` vs `source`

`AuditAgent.act()` stores the flagged source under `payload["flagged_source"]`, not
`payload["source"]`. This is intentional — the `source` field on the `Event` dataclass
refers to the emitter of the event (the AuditAgent itself), not the entity being flagged.

Any agent that processes `governance.trust_escalation` events and needs to identify the
problematic source must read `flagged_source` first:

```python
source = (
    event.payload.get("flagged_source") or event.payload.get("source") or ""
) if event.payload else ""
```

The fallback to `"source"` covers custom emitters and tests that predate this convention.

---

## Test coverage

| Test file | Tests | What they cover |
|---|---|---|
| `tests/adapters/test_community.py` | 17 | Constructor, poll, cursor advance, TrustLevel, event type, correlation_id derivation, malformed JSON, truncation reset, public API export |
| `tests/govkit/test_community_trust.py` | 11 | `analyse()` unit (7 cases), OODA pipeline (2 cases), full chain integration (real EventBus), module boundary export |

Full suite: **605 tests, all passing.**

---

## What is not included (JsonlFeedAdapter verdict)

`JsonlFeedAdapter` — a generic rename of `CommunityFeedAdapter` with no domain coupling —
was assessed as **not justified**. The `default_trust_level` parameter on `CommunityFeedAdapter`
already makes it generic. A second distinct JSONL consumer with materially different event types
would justify the generic adapter; that consumer does not exist. Decision logged to `decisions.jsonl`.

The gate condition (P40): ≥ 2 consumers with different event type needs. Current count: 1.

---

## Files changed (commit 166bba6)

| File | Status | Notes |
|---|---|---|
| `agentic_loopkit/adapters/community.py` | **new** | CommunityFeedAdapter + CommunityEventType |
| `agentic_loopkit/adapters/__init__.py` | modified | Added community exports |
| `agentic_loopkit/__init__.py` | modified | Added CommunityFeedAdapter, CommunityEventType |
| `agentic_govkit/agents/community_trust.py` | **new** | CommunityTrustLearner |
| `agentic_govkit/__init__.py` | modified | Added CommunityTrustLearner export |
| `docs/community-feed-trust-pathway.md` | **new** | Consumer reference guide |
| `tests/adapters/test_community.py` | **new** | 17 adapter tests |
| `tests/govkit/test_community_trust.py` | **new** | 11 govkit tests |
| `CLAUDE.md` | modified | Test count (556→605), new files, govkit layout updated |

---

## Planned next (v8 carry-forwards)

1. **CompassSkillOptExecutor end-to-end demo** — wire compass session history as the SkillOptExecutor training corpus; prove the self-improvement loop closes with real data; the one open checkbox from v5/v6/v7.
2. **`docs/event-catalog.md`** — add `community.entry_received` event type entry.
3. **`/loopkit-docs-hygiene`** — full docs hygiene check post-v7 (architecture.md executor table, idioms-adoption-plan.md, dashboard-architecture.md enum values).
