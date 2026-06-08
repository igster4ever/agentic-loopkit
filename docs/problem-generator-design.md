# ProblemGeneratorAgent — Design Brief

_Date: 2026-06-08_
_Status: Draft — v0.1_
_Informs: loopkit v4 theme 5 — proactive exploration_

---

## North Star

Every loopkit agent is reactive — it waits for events and responds. No component asks
*"what should we be looking at that we aren't?"* The `ProblemGeneratorAgent` closes this gap:
a proactive exploration layer that observes system state, identifies unexplored angles, and
emits `agenda.*` events that downstream agents can subscribe to and act on.

This is the **Problem Generator** module from the AIMA Learning Agent taxonomy — the fourth
and only missing module. It is not a replacement for reactive agents; it is a complementary
layer that drives exploration between reactive cycles.

---

## The Gap It Fills

| Reactive (current) | Proactive (new) |
|---|---|
| Agent observes event → responds | Agent observes state → generates questions |
| Driven by what *happened* | Driven by what *hasn't happened yet* |
| No capacity to notice absence | Explicitly models what is unexplored |
| Silent when no events arrive | Produces signal on cadence |

---

## Package Location

`agentic_loopkit/agents/problem_generator.py`

Exported from `agentic_loopkit/__init__.py` alongside `AgentBase` and `ProjectionAgent`.

---

## New Event Types

```python
class AgendaEventType(StrEnum):
    ITEM_ADDED     = "agenda.item_added"      # new exploration target identified
    ITEM_ADDRESSED = "agenda.item_addressed"  # downstream agent acted on an item
    ITEM_EXPIRED   = "agenda.item_expired"    # item not acted on within ttl
```

`AgendaEventType` lives in `agentic_loopkit/agents/problem_generator.py` alongside the agent.
`ITEM_ADDRESSED` is emitted by downstream consumers, not by `ProblemGeneratorAgent` itself.
`ITEM_EXPIRED` is emitted by `ProblemGeneratorAgent` if an item has no `ITEM_ADDRESSED`
event within `item_ttl_hours` (default: 48h).

---

## New Dataclass

```python
@dataclass
class AgendaItem:
    description:     str           # one-sentence exploration target
    priority:        float         # 0.0–1.0 — caller sets; not confidence
    rationale:       str           # why this is worth exploring now
    context_streams: list[str]     # which streams informed this item
    tags:            list[str] = field(default_factory=list)
    ttl_hours:       int = 48
```

`AgendaItem` is serialised into `agenda.item_added` payload. It is not a loopkit `Event` —
it is domain data carried inside one.

---

## Abstract Interface

```python
class ProblemGeneratorAgent(AgentBase):
    """
    Proactive exploration agent. Subscribes to context streams; on trigger,
    loads system state and generates a set of AgendaItems for downstream agents.

    OODA wiring:
        observe()  — checks trigger condition (e.g. is it time to explore?)
        orient()   — LLM phase: given state, what is unexplored or underweighted?
        decide()   — apply min_priority threshold; filter empty lists
        act()      — emit agenda.item_added for each item

    LLM is in orient() only. All other phases deterministic.
    """

    #: Streams to load when generating agenda items. Defaults to subscription streams.
    context_streams: list[str] = []

    #: Minimum priority to emit. Items below this are discarded silently.
    min_priority: float = 0.3

    @abstractmethod
    async def explore(self, events: list[Event]) -> list[AgendaItem]:
        """
        PRIMARY LLM PHASE — called from orient().

        Receives events from context_streams. Returns a list of AgendaItems
        representing unexplored angles, under-addressed goals, or blind spots.

        Guidelines for implementations:
        - Compare what has been acted on vs. what the goal demands
        - Surface absence: what signals are missing, not what is present
        - Each item should be independently actionable by a subscriber
        - Prefer 2–5 focused items over a long undifferentiated list
        """

    def should_explore(self, event: Event) -> bool:
        """
        Trigger gate — return False to suppress exploration on this event.
        Default: always explore. Override to throttle (e.g. only on bus.started
        or system.bus_pressure, or once per N hours via external timer).
        """
        return True
```

---

## OODA Wiring (internal)

```
observe()   calls should_explore(); returns None if False                      (deterministic)
orient()    loads context_streams via load_all_events(); calls explore()       (LLM here)
            wraps result in EventMeta(phase="orient", loop_type="ooda")
decide()    filters items below min_priority; returns None if list empty       (deterministic)
act()       emits one agenda.item_added per AgendaItem                         (deterministic)
            sets causation_id = trigger event's event_id
            sets correlation_id = trigger event's correlation_id
```

---

## Emitted Event

```python
Event(
    event_type     = AgendaEventType.ITEM_ADDED,   # stream: "agenda"
    source         = agent.name,
    causation_id   = trigger_event.event_id,
    correlation_id = trigger_event.correlation_id,
    payload = {
        "description":     "Investigate why GPS cycles are clustering before 09:00",
        "priority":        0.78,
        "rationale":       "12 of last 15 cycles occurred in the 08:45–09:00 window...",
        "context_streams": ["gps", "adr"],
        "tags":            ["timing", "gps"],
        "ttl_hours":       48,
        "_meta": {
            "phase":      "orient",
            "loop_type":  "ooda",
            "confidence": 0.78,
            "context":    "Exploration pass — identified 3 agenda items from 42 events",
        }
    }
)
```

---

## Usage Example

```python
class GpsExplorationAgent(ProblemGeneratorAgent):
    context_streams = ["gps", "adr", "projection"]

    def should_explore(self, event: Event) -> bool:
        # Only trigger on projection updates (new wiki page available)
        return event.event_type == "projection.updated"

    async def explore(self, events: list[Event]) -> list[AgendaItem]:
        summary = "\n".join(
            e.payload.get("content", "")[:200]
            for e in events
            if e.event_type == "projection.updated"
        )
        result = await llm.call(
            system="You are a technical analyst. Identify 2–4 unexplored angles...",
            user=f"Recent system state:\n{summary}"
        )
        return parse_agenda_items(result)  # caller implements parse_agenda_items


agent = GpsExplorationAgent("gps-explorer", bus)
agent.subscribe("projection")          # trigger on new projections
bus.register(agent)
```

---

## Downstream Subscriber Pattern

A downstream agent subscribes to `"agenda"` and acts on items:

```python
class GpsInvestigatorAgent(AgentBase):
    async def observe(self, event: Event):
        if event.event_type != AgendaEventType.ITEM_ADDED: return None
        if "gps" not in event.payload.get("tags", []): return None
        return {"item": event.payload}

    async def orient(self, event: Event, context: dict):
        # LLM: investigate the agenda item
        ...

    async def act(self, event: Event, action: dict):
        # Emit agenda.item_addressed when done
        await self._bus.publish(event.caused(
            AgendaEventType.ITEM_ADDRESSED, self.name,
            {"item_description": context["item"]["description"], "finding": action["finding"]}
        ))
```

---

## Design Decisions

1. **`AgentBase` subclass, not an adapter** — adapters are stateless bridges to external
   systems; they cannot call LLMs. The LLM-in-`orient()` constraint means this is an agent.
   External scheduling (APScheduler, asyncio, cron) triggers it like any other event source.

2. **`context_streams` decoupled from subscription streams** — subscribe to trigger events
   (e.g. `projection.updated`); load from a broader set when exploring (e.g. `gps`, `adr`,
   `projection`). Same pattern as `ProjectionAgent.projection_streams`.

3. **`agenda.*` as a first-class stream, not reusing `system.*`** — agenda items are domain
   signals, not bus infrastructure. They should be subscribable, filterable, and inspectable
   in the dashboard separately from system events.

4. **TTL + `agenda.item_expired`** — unanswered agenda items are observability signals in
   their own right. An item that expires without an `ITEM_ADDRESSED` event is evidence of
   a gap in agent coverage.

5. **`priority` is not `confidence`** — priority reflects *importance* (caller-set, may be
   heuristic). Confidence in `_meta.confidence` reflects *certainty about the assessment*.
   Both are present and independent.

6. **No built-in scheduler** — `ProblemGeneratorAgent` does not own a timer. The consuming
   app drives it via events (e.g. on `system.bus_started`, on `projection.updated`, on an
   external cron publishing a `scheduler.tick` event). This keeps loopkit scheduler-free.

---

## Relationship to Other Agents

| Agent | Role | Relation to ProblemGeneratorAgent |
|---|---|---|
| `ProjectionAgent` | Materialises live document from log | ProblemGenerator can subscribe to `projection.updated` as its trigger |
| `AuditAgent` | Flags governance breaches | AuditAgent is reactive; ProblemGenerator is proactive. They operate on different axes |
| `RALFExecutor` users | Execute tasks | Natural consumers of `agenda.item_added` — `retrieve()` loads the agenda item as context |
| Dashboard | Visual inspector | `agenda` stream surfaced in Streams page; items visible in EventTable |

---

## Public API Addition

```python
# agentic_loopkit/__init__.py
from .agents.problem_generator import ProblemGeneratorAgent, AgendaEventType, AgendaItem
```

---

## Test Targets

`tests/agents/test_problem_generator.py`

- `explore()` called from `orient()` with loaded context events
- `should_explore()` returning False short-circuits without LLM call
- `min_priority` filter discards low-priority items
- `agenda.item_added` payload correctness (description, priority, rationale, ttl_hours)
- `_meta` present in payload with `phase="orient"`, `loop_type="ooda"`
- `causation_id` and `correlation_id` inherited from trigger event
- Empty `explore()` result → no events emitted
- `ITEM_EXPIRED` emitted after ttl_hours if no `ITEM_ADDRESSED` seen (optional — advanced)

---

## Estimated Scope

~120 lines implementation, ~80 lines tests.
