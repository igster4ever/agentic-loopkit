# Cognitive Architecture Extensions — Design Brief

_Date: 2026-06-08_
_Status: Draft — v0.1_
_Informs: loopkit v4 CoALA alignment, agentic-memorykit world_model bucket_

---

## North Star

The current `AgentState` covers three CoALA memory buckets: `episodic`, `semantic`,
`procedural`. Two gaps remain when mapped against the full AIMA agent taxonomy:

1. **World model** — agents that operate in partially-observable environments need a
   predictive/causal state store: *"how does the world evolve?"* and *"what do my actions do?"*
   This is distinct from episodic (what happened) and semantic (what is true durably).

2. **Performance Measure** — evaluation of agent output is currently embedded inside
   executors (`confidence` score, `evaluate()` verdict). There is no first-class,
   session-spanning concept that tracks how well an agent is performing over time.

This document specifies both extensions and how they connect to `agentic_memorykit`.

---

## Extension 1 — `world_model` Bucket in `AgentState`

### Current `AgentState`

```python
@dataclass
class AgentState:
    episodic:   list[dict] = field(default_factory=list)  # event log excerpts
    semantic:   dict       = field(default_factory=dict)  # durable facts (k/v)
    procedural: dict       = field(default_factory=dict)  # behavioural adjustments (reserved)
```

### Proposed Addition

```python
@dataclass
class AgentState:
    episodic:    list[dict] = field(default_factory=list)  # event log excerpts
    semantic:    dict       = field(default_factory=dict)  # durable facts (k/v)
    procedural:  dict       = field(default_factory=dict)  # behavioural adjustments (reserved)
    world_model: dict       = field(default_factory=dict)  # NEW — predictive/causal state
```

### What `world_model` Stores

`world_model` is for **predictive and causal state** — agent beliefs about how the external
environment behaves, not what has happened or what is factually true:

| Content type | Bucket |
|---|---|
| *"Event X happened at time T"* | `episodic` |
| *"The GPS stream prefix convention is `gps.`"* | `semantic` |
| *"When I emit a high-confidence ADR event, the wiki agent materialises within 2 ticks"* | `world_model` |
| *"The ClickUp adapter stalls when the API returns 502 errors"* | `world_model` |
| *"Confidence drops below 0.65 when delegation_depth > 3"* | `world_model` |

### CoALA Alignment

The CoALA framework (Sumers et al., 2023) decomposes agent memory into:

| CoALA component | `AgentState` bucket | Notes |
|---|---|---|
| Episodic memory | `episodic` | Event log excerpts |
| Semantic memory | `semantic` | Durable named facts |
| Procedural memory | `procedural` | Behavioural adjustments (v4+) |
| World model / transition model | `world_model` | **New — this document** |

The AIMA Model-Based Reflex Agent and Learning Agent both require a transition model
to operate in partially observable environments. Without `world_model`, loopkit agents
cannot encode *causal* knowledge — they can know facts and history but not cause-effect
relationships.

### Change to `agentic_loopkit/agents/base.py`

```python
@dataclass
class AgentState:
    episodic:    list[dict] = field(default_factory=list)
    semantic:    dict       = field(default_factory=dict)
    procedural:  dict       = field(default_factory=dict)
    world_model: dict       = field(default_factory=dict)
```

Single-line addition. Backwards-compatible — default is empty dict.
Existing `save_state()`/`load_state()` serialisation handles it automatically (dataclass
field → JSONL round-trip via `asdict()` / `from_dict()`).

### Memorykit Mapping

When `agentic_memorykit` is wired:

| `AgentState` bucket | `MemoryStore` key convention | Example |
|---|---|---|
| `semantic` | `"semantic.<key>"` | `"semantic.clickup_base_url"` |
| `world_model` | `"world_model.<key>"` | `"world_model.adapter_stall_pattern"` |
| `episodic` | Not stored in MemoryStore — stays in EventStore JSONL | — |
| `procedural` | `"procedural.<key>"` | `"procedural.confidence_bias"` (future) |

`world_model` facts differ from `semantic` facts in that they are *falsifiable* via new
observations. Agents should update `world_model` entries when their predictions fail —
the memorykit's `UPDATE` operation with version increment is the right mechanism.

---

## Extension 2 — `PerformanceMeasure` Protocol

### The Gap

Confidence scores (`_meta.confidence`) measure per-event certainty.
`OutcomeExecutor.evaluate()` measures per-artifact rubric satisfaction.
Neither measures **agent-level performance over time** — the AIMA Performance Measure.

This matters for:
- Detecting slow degradation (an agent that was 0.85 average confidence is now 0.62)
- Identifying which agents are producing actionable outputs vs. noise
- Feeding `GovernanceLearningAgent` with structured performance data
- Informing compass session reviews with objective metrics

### Proposed Protocol

```python
class PerformanceMeasure(ABC):
    """
    Session-spanning evaluator for an agent or executor.

    Not a runtime component — computed post-hoc from the event store.
    Callable from compass, dashboard, or GovernanceLearningAgent.
    """

    @abstractmethod
    def score(
        self,
        agent_name: str,
        events: list["Event"],
        window_hours: int = 72,
    ) -> "PerformanceScore":
        """
        Compute a performance score for agent_name from its emitted events.
        Deterministic — no LLM. Use confidence values, trust levels,
        delegation depths, and follow_up event counts as raw signals.
        """


@dataclass
class PerformanceScore:
    agent_name:       str
    window_hours:     int
    mean_confidence:  float | None   # mean _meta.confidence across emitted events; None if no _meta
    event_count:      int            # total events emitted in window
    follow_up_rate:   float | None   # fraction of RALF/ReAct runs that produced a follow_up event
    governance_flags: int            # AuditAgent flags against this agent in window
    trend:            str            # "improving" | "stable" | "degrading" | "insufficient_data"
    computed_at:      str            # ISO 8601 UTC
```

### Where It Lives

`agentic_loopkit/agents/performance.py` — alongside `base.py` and `projection.py`.

This is **not** wired into the runtime agent loop. It is a post-hoc analytical tool:

```python
from agentic_loopkit.agents.performance import PerformanceMeasure, PerformanceScore

class SimpleConfidencePerformance(PerformanceMeasure):
    def score(self, agent_name, events, window_hours=72):
        agent_events = [e for e in events if e.source == agent_name]
        confidences = [
            e.meta()["confidence"]
            for e in agent_events
            if e.meta() and "confidence" in e.meta()
        ]
        mean = sum(confidences) / len(confidences) if confidences else None
        return PerformanceScore(
            agent_name      = agent_name,
            window_hours    = window_hours,
            mean_confidence = mean,
            event_count     = len(agent_events),
            ...
        )
```

### Integration Points

| Consumer | How it uses PerformanceMeasure |
|---|---|
| `GovernanceLearningAgent` | Calls `score()` per registered agent; includes `PerformanceScore` in analysis context |
| Dashboard `/api/agents` route | Augments agent list with current performance score |
| Compass session close | Records mean_confidence + governance_flags in reality snapshot |

---

## Implementation Order

These are prerequisite changes for v4 themes 5 and 6, not standalone deliverables:

| Change | Effort | Blocks |
|---|---|---|
| `world_model` field in `AgentState` | XS (1 line + test) | agentic_memorykit world_model storage |
| `PerformanceMeasure` protocol + `SimpleConfidencePerformance` | S (~60 lines + tests) | `GovernanceLearningAgent` analyse() quality |
| Memorykit `world_model` key convention documented | XS (doc update only) | memorykit-design.md |

Neither change touches the event loop, executor logic, or dashboard routes.
Both are backwards-compatible (new fields + new classes; nothing removed or renamed).

---

## Compatibility Notes

### `world_model` and existing `save_state()` / `load_state()`

`AgentBase.save_state()` calls `asdict(state)` and stores the result.
`load_state()` deserialises it. Adding `world_model: dict` to `AgentState` means:

- **Old state files** (without `world_model` key): `load_state()` returns `AgentState`
  with `world_model={}` (default factory applies). No migration needed.
- **New state files** (with `world_model` key): fully round-trip serialised.

### `PerformanceMeasure` and existing confidence scoring

`PerformanceMeasure` reads `_meta.confidence` from stored events — the same field
already emitted by all executors via `EventMeta`. No schema changes to existing events.

---

## Public API Additions

```python
# agentic_loopkit/__init__.py
from .agents.base        import AgentState   # updated (world_model field added)
from .agents.performance import PerformanceMeasure, PerformanceScore
```

---

## Test Targets

`tests/agents/test_base.py` — extend existing AgentState tests:

- `world_model` defaults to empty dict
- `world_model` round-trips through `save_state()`/`load_state()`
- Old state file (no `world_model` key) loads without error

`tests/agents/test_performance.py` — new file:

- `SimpleConfidencePerformance.score()` returns correct `mean_confidence`
- Events without `_meta` contribute to `event_count` but not `mean_confidence`
- `governance_flags` count matches flagged events in window
- `trend` logic: improving / stable / degrading based on windowed comparison
- Empty event list → `PerformanceScore` with `mean_confidence=None`, `event_count=0`

---

## Estimated Scope

- `world_model` field: ~5 lines implementation, ~15 lines tests
- `PerformanceMeasure`: ~60 lines implementation, ~50 lines tests
- Total: ~65 lines implementation, ~65 lines tests
