# Governance Architecture & ProjectionAgent Workflow

Companion to `docs/architecture.md` and `docs/idioms-adoption-plan.md`.
ASCII diagrams showing how the governance layer (Phase A + B) and the
`ProjectionAgent` materialisation cycle fit together.

---

## 1 — Governance Architecture (Phase A + Phase B)

```
  Domain events (any stream)
  ┌───────────┐  ┌──────────────────┐  ┌──────────────────────┐
  │ Adapters  │  │  Domain Agents   │  │  ProjectionAgent     │  ...
  │ (clickup, │  │  (OODA / RALF /  │  │  emits               │
  │  slack,   │  │   ReAct / Plan / │  │  projection.updated  │
  │  git)     │  │   Reflexion /    │  │                      │
  └─────┬─────┘  │   Outcome)       │  └──────────┬───────────┘
        │        └────────┬─────────┘             │
        └─────────────────┴─────────────────────── ┘
                                  │
                       publish(Event)
                                  │
                     ┌────────────▼───────────┐
                     │        EventBus         │
                     │   persist → fanout      │
                     └───────────┬─────────────┘
                                 │
               ┌─────────────────┴──────────────────┐
               │ all streams (*)                     │ governance.* only
               ▼                                     ▼

  ┌── PHASE A — Observability ──────┐   ┌── PHASE B — Enforcement (v3) ───────┐
  │                                  │   │                                      │
  │         AuditAgent               │   │         KillSwitchAgent              │
  │   Subscribes: all streams (*)    │   │   Subscribes: governance.* only      │
  │                                  │   │                                      │
  │  observe()                       │   │  Configurable policy map:            │
  │  └─ skip governance.*            │   │                                      │
  │     (loop guard — prevents       │   │  depth_exceeded                      │
  │      the auditor auditing        │   │    → halt_correlation                │
  │      its own decisions)          │   │                                      │
  │                                  │   │  trust_escalation                    │
  │  orient()  ─ checks every event: │   │  confidence_breach                   │
  │  ┌────────────────────────────┐  │   │    → emit_human_override             │
  │  │ delegation_depth > max?    │  │   │                                      │
  │  │ → governance.depth_        │  │   │  dispute_opened                      │
  │  │   exceeded                 │  │   │    → ConflictResolutionExecutor      │
  │  ├────────────────────────────┤  │   │      (planned v3)                    │
  │  │ trust_level == UNTRUSTED?  │  │   │                                      │
  │  │ → governance.trust_        │  │   │  act():                              │
  │  │   escalation               │  │   │  → governance.halt                   │
  │  ├────────────────────────────┤  │   │  → governance.quarantine             │
  │  │ _meta.confidence           │  │   │  → governance.human_override         │
  │  │ < confidence_threshold?    │  │   │                                      │
  │  │ → governance.confidence_   │  │   │  All enforcement events are          │
  │  │   breach                   │  │   │  persisted to the bus —              │
  │  └────────────────────────────┘  │   │  the enforcer is observable.         │
  │                                  │   │                                      │
  │  act():                          │   └───────────────┬──────────────────────┘
  │  → publish governance.* event    │                   │
  │    (persisted to bus)            │                   │ enforcement events
  └─────────────┬────────────────────┘                   │
                │ governance.* events                     │
                └─────────────────────────────────────────┘
                                  │
                                  ▼
                            EventBus
                   (events-governance.jsonl)
                                  │
                     routed to governance.* subscribers
                     (KillSwitchAgent, dashboards, alerts)
```

**Phase separation contract:**
- Phase A (AuditAgent) subscribes to `*` and never subscribes to `governance.*`
- Phase B (KillSwitchAgent) subscribes to `governance.*` only
- Neither component calls the other directly — communication is via the bus

---

## 2 — ProjectionAgent OODA Pipeline

```
  trigger event arrives on subscribed stream (e.g. "gps.cycle_complete")
        │
        ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │                      ProjectionAgent (OODA)                          │
  │                                                                       │
  │  [1] observe(event)                                                   │
  │      └─ should_materialise(event)?                                    │
  │             ├─ False → return None  ─────────────────────▶ (skip)    │
  │             └─ True  → return {"trigger": event}                     │
  │                                 │                                     │
  │  [2] orient(event, context)     ▼                                     │
  │      │                                                                │
  │      ├─ load_all_events(projection_streams)                           │
  │      │  ├─ events-gps.jsonl   ┐                                       │
  │      │  ├─ events-adr.jsonl   ┼─ full history, no time filter        │
  │      │  └─ events-<N>.jsonl   ┘                                       │
  │      │                                                                │
  │      ├─ await materialise(events)   ◄──── PRIMARY LLM PHASE          │
  │      │  └─ "synthesise these N events into a document"               │
  │      │     returns: str  (markdown / JSON / plain text)              │
  │      │                                                                │
  │      └─ aggregate_confidence(events)                                  │
  │         ├─ for each event with _meta.confidence:                      │
  │         │    weight = TrustLevel_ordinal × 1/(1 + delegation_depth)  │
  │         │    HIGH=3 · MEDIUM=2 · LOW=1 · UNTRUSTED=0 (no signal)    │
  │         └─ returns weighted mean, or None if no data                 │
  │                                 │                                     │
  │  [3] decide(event, orientation) ▼                                     │
  │      └─ pass-through (always act when orient succeeds)               │
  │                                 │                                     │
  │  [4] act(event, action)         ▼                                     │
  │      └─ bus.publish(event.caused("projection.updated", self.name,    │
  │           {                                                           │
  │             "projection_id": self.name,                              │
  │             "content":       "<materialised document>",              │
  │             "confidence":    0.74,   ← aggregate_confidence()        │
  │             "event_count":   42,                                     │
  │             "streams":       ["gps", "adr"],                         │
  │             "_meta": { "phase": "orient", "loop_type": "ooda",      │
  │                        "confidence": 0.74, "context": "..." }        │
  │           }))                                                         │
  └───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
              Event: projection.updated  (stream: "projection")
                                  │
                     ┌────────────▼───────────┐
                     │        EventBus         │
                     │  persisted →            │
                     │  events-projection.jsonl│
                     └───────────┬─────────────┘
                                 │
           ┌─────────────────────┼──────────────────────┐
           ▼                     ▼                       ▼
    Dashboard             AuditAgent               Downstream
    (ChainPage,           (checks confidence        agents
     EventDetail,          vs threshold →           (subscribe
     EventTimeline)        may emit                  to "projection"
                           confidence_breach)         stream)
```

---

## 3 — Integrated Flow: End-to-end Example

A `WikiPageAgent(ProjectionAgent)` materialises a GPS status page.
Two scenarios: confidence passes, and confidence breaches threshold.

```
  gps.cycle_complete
  (trust: HIGH, depth: 1, _meta.confidence: 0.91)
          │
          ▼
     EventBus ────────────────────────────────────────────────────────┐
          │                                                            │
          │ routes to WikiPageAgent          routes to AuditAgent     │
          ▼                                  (subscribed to *)        │
  WikiPageAgent                                       │               │
  should_materialise() → True                         ▼               │
  load_all_events("gps","adr") → 42 events    orient():              │
  materialise(LLM) → "# GPS Status..."         confidence 0.91 ≥     │
  aggregate_confidence() → 0.74                threshold 0.50 ✓       │
  publish projection.updated(confidence=0.74)  no flag raised         │
          │                                                            │
          ▼                                                            │
     EventBus                                                         │
          │                                                            │
          ├──▶ Dashboard (projection stream) ── renders wiki page      │
          │                                                            │
          └──▶ AuditAgent                                             │
               confidence 0.74 ≥ threshold 0.50 ✓  no flag raised    │
                                                                       │
  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│
  scenario B: materialise() returns low-confidence result             │
  aggregate_confidence() → 0.28                                        │
          │                                                            │
          └──▶ AuditAgent                                             │
               confidence 0.28 < threshold 0.50                       │
               → governance.confidence_breach ────────────────────────┘
                        │
                        ▼
                   EventBus
                        │
                        └──▶ KillSwitchAgent (Phase B)
                              policy: confidence_breach
                                → emit_human_override
                                  │
                                  ▼
                             governance.human_override
                             (human reviews, amends synthesis)
                                  │
                                  ▼
                             WikiPageAgent re-materialises
                             incorporating the human decision
                             (it is now an event in the log)
```

---

## 4 — ConflictResolutionExecutor Flow (planned v3)

Triggered when two agents produce incompatible orientations about the same entity.

```
  AgentA: "gps.analysis" (correlation_id: "W-42", confidence: 0.81)
  └─ payload: { "status": "on-track", ... }

  AgentB: "gps.analysis" (correlation_id: "W-42", confidence: 0.79)
  └─ payload: { "status": "at-risk",  ... }
                    │
                    │ something detects the contradiction
                    ▼
         governance.dispute_opened
         (correlation_id: "W-42",
          payload: { position_a: ..., position_b: ... })
                    │
                    ▼
     ┌──────────────────────────────────────────────────────┐
     │          ConflictResolutionExecutor                   │
     │          (extends OutcomeExecutor — v3, planned)      │
     │                                                        │
     │  retrieve()                                           │
     │  └─ load both positions from event log               │
     │     by correlation_id                                 │
     │                                                        │
     │  ┌─ iteration (max 3) ────────────────────────────┐  │
     │  │                                                  │  │
     │  │  act(context, prior_result)                      │  │
     │  │  └─ LLM: "synthesise a reconciled view of       │  │
     │  │           both positions"                        │  │
     │  │                                                  │  │
     │  │  evaluate(synthesis, rubric)  ◄─ ISOLATED       │  │
     │  │  └─ LLM sees ONLY:                               │  │
     │  │     · the synthesis                              │  │
     │  │     · the rubric criteria                        │  │
     │  │     · NO prior reasoning history                 │  │
     │  │       (prevents anchoring on either position)   │  │
     │  │                                                  │  │
     │  │  satisfied? ──yes──────────────────────────────▶│  │
     │  │      │                                           │  │
     │  │      no → gaps fed to next act() iteration      │  │
     │  └──────┘                                           │  │
     │                                                        │
     └───────────┬──────────────────────────────────────────┘
                 │
        ┌────────┴────────────┐
        │                     │
        ▼ satisfied           ▼ max_iterations reached
  governance.              governance.
  dispute_resolved         human_override
  (reconciled view)        (both positions +
                            gap list → human reviewer)
```

---

## 5 — Component Responsibility Summary

```
  ┌────────────────────────┬─────────────────────────────────────────┐
  │ Component              │ Responsibility                           │
  ├────────────────────────┼─────────────────────────────────────────┤
  │ AuditAgent             │ Observe all streams; flag threshold      │
  │ (Phase A)              │ violations; emit governance.* events.    │
  │                        │ The auditor is itself auditable.         │
  ├────────────────────────┼─────────────────────────────────────────┤
  │ KillSwitchAgent        │ Enforce policy on governance.* events.   │
  │ (Phase B — planned)    │ Halt chains, quarantine sources,         │
  │                        │ escalate to humans. Opt-in only.         │
  ├────────────────────────┼─────────────────────────────────────────┤
  │ ConflictResolution-    │ Mediate between competing agent views.   │
  │ Executor (v3 planned)  │ Isolated evaluation (no anchoring).      │
  │                        │ Escalates to human on max iterations.    │
  ├────────────────────────┼─────────────────────────────────────────┤
  │ ProjectionAgent        │ Materialise a live document from the     │
  │                        │ event log. LLM in materialise() only.   │
  │                        │ Emits projection.updated — itself an     │
  │                        │ observable, replayable event.            │
  ├────────────────────────┼─────────────────────────────────────────┤
  │ aggregate_confidence() │ Utility: page-level confidence from      │
  │                        │ _meta.confidence across source events,   │
  │                        │ weighted by TrustLevel + depth decay.   │
  ├────────────────────────┼─────────────────────────────────────────┤
  │ EventBus / JSONL store │ Source of truth. All governance and      │
  │                        │ projection decisions are events — fully  │
  │                        │ persistent, replayable, queryable.       │
  └────────────────────────┴─────────────────────────────────────────┘
```

**The fundamental rule:** the event log is the source of truth.
Governance flags, enforcement actions, projection documents, and human overrides
are all events. None of them live outside the log. Replay is always authoritative.
