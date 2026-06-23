# A2A Protocol Alignment — agentic-loopkit

**Status:** Design reference — 2026-06-23  
**Author:** Ivan Smith + Claude Sonnet 4.6  
**Scope:** agentic-loopkit executor and adapter design — concept borrowing from Agent2Agent protocol  
**See also:** `~/.claude/skills/compass/docs/a2a-protocol-alignment.md` — full cross-project alignment doc  

---

## Stance

Borrow A2A concepts as design patterns; do not adopt the HTTP transport. loopkit is local-first and JSONL-backed. The transport question reopens when loopkit nodes need to be addressable across machines.

---

## 1. Executor Skill declarations

**A2A concept:** Each agent publishes an Agent Card that declares its `skills` — named, typed, tagged capability units. Clients discover what an agent can do without calling it first.

**loopkit gap:** Executor capabilities are implicit. There is no way to ask `bus.available_skills()` and get a structured answer. Wiring is done by the application author who knows (or guesses) what each executor handles.

**Proposed pattern — `ExecutorSkill` dataclass:**

```python
from dataclasses import dataclass, field

@dataclass
class ExecutorSkill:
    name: str
    description: str
    input_event_types: list[str]   # event type strings; "*" means any
    output_event_types: list[str]  # events this executor may emit
    tags: list[str] = field(default_factory=list)
```

Each executor declares itself at the class level:

```python
class RALFExecutor(BaseExecutor):
    skill = ExecutorSkill(
        name="ralf-reflect",
        description="Bounded Reflect-Analyse-Learn-Forward loop triggered by a single event",
        input_event_types=["*"],
        output_event_types=["ralf.completed", "ralf.learned"],
        tags=["reflection", "learning", "bounded"],
    )

class ReActExecutor(BaseExecutor):
    skill = ExecutorSkill(
        name="react-tool-use",
        description="Bounded think-execute loop with deterministic tool dispatch",
        input_event_types=["*"],
        output_event_types=["react.step_completed", "react.done"],
        tags=["tool-use", "bounded", "deterministic"],
    )
```

**`EventBus.available_skills()` — discovery:**

```python
def available_skills(self) -> list[ExecutorSkill]:
    skills = []
    for handler in self._router.all_handlers():
        if hasattr(handler, "skill") and isinstance(handler.skill, ExecutorSkill):
            skills.append(handler.skill)
    return skills
```

**Why this matters:**

- `AgentBase.orient()` can reason about what executors are available rather than relying on hardcoded dispatch.
- A future A2A-addressable loopkit node maps `available_skills()` directly to the `skills` array in the Agent Card — no field invention required.
- Community FeedAdapter in compass (`CommunityFeedAdapter`) can declare itself as a skill, making its ingest capability discoverable to govkit agents that need to wire trust escalation.

**Backward compatibility:** `skill` is an optional class attribute. Executors without it remain valid — `available_skills()` silently skips them.

**Implementation location:** `agentic_loopkit/executors/base.py` — add `ExecutorSkill` dataclass and `BaseExecutor.skill: ExecutorSkill | None = None`. `EventBus.available_skills()` in `agentic_loopkit/bus.py`.

---

## 2. PollingAdapter / PushAdapter split

**A2A concept:** A2A supports push-notification delivery — the producer calls a registered webhook when a Task completes. No polling required.

**loopkit today:** `PollingAdapter` is the only adapter base. All adapters are tick-driven (pull). There is no structural home for push-capable sources.

**Proposed design seam:**

```
BaseAdapter          (common: emit(), cursor management, liveness)
  ├── PollingAdapter  ← current; tick()-driven; call poll() on a schedule
  └── PushAdapter     ← future; callback-registered; receives inbound HTTP/webhook; no tick loop
```

Both bases emit typed `Event`s to the `EventBus`. The consumer side is identical — no changes required downstream.

`PushAdapter` sketch:

```python
class PushAdapter(BaseAdapter):
    """Base for adapters that receive events via callback rather than polling."""

    def register_callback(self, bus: EventBus) -> None:
        """
        Register this adapter's inbound handler with an HTTP server or
        queue listener. Called once at startup.
        """
        raise NotImplementedError

    def handle_inbound(self, payload: dict) -> None:
        """Parse an inbound push payload and emit to the bus."""
        raise NotImplementedError
```

**Why name this now:**

The `CommunityFeedAdapter` for P40 is currently designed as a `PollingAdapter` (git pull on a tick). If a future community feed source supports A2A push notifications, the correct home for that adapter is `PushAdapter` — not a polling subclass with an empty `tick()`. Naming the seam now prevents an awkward retrofit.

**Current priority:** Low. The community feed transport is git JSONL (pull). This is a design marker, not an implementation task.

---

## 3. A2A Task lifecycle → executor event naming

**A2A concept:** Tasks carry a well-known lifecycle (`submitted → working → completed / failed / cancelled`) expressed as status strings in the Task object.

**loopkit today:** Each executor invents its own completion and error event type names (e.g. `ralf.completed`, `react.done`, `plan.step_failed`). There is no shared vocabulary.

**Proposed alignment:** Executors adopt A2A-aligned status suffixes in their output event types:

| A2A status | Executor event suffix convention |
|---|---|
| `submitted` | `.submitted` (optional — on task intake) |
| `working` | `.working` (optional — on each step/iteration) |
| `completed` | `.completed` |
| `failed` | `.failed` |
| `cancelled` | `.cancelled` |

Example migration:

```python
# Before
output_event_types=["ralf.done", "ralf.error"]

# After (A2A-aligned)
output_event_types=["ralf.completed", "ralf.failed"]
```

This is a naming convention, not a schema change. `GovernanceLearningAgent` and `AuditAgent` subscribe to `governance.*` — they are unaffected. The alignment means a future A2A Task wrapper around a loopkit executor can map status fields without translation.

**Scope:** New executors should follow the convention. Existing executors can be migrated opportunistically — no flag day.

---

## 4. Agent Card generation (future)

When a loopkit node becomes A2A-addressable:

```python
def generate_agent_card(bus: EventBus, identity: dict) -> dict:
    """
    Generate an A2A-compatible Agent Card from registered executor skills.
    identity: {name, description, provider, url, version}
    """
    return {
        **identity,
        "skills": [
            {
                "id": s.name,
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
            for s in bus.available_skills()
        ],
        "capabilities": {
            "streaming": False,       # SSE — not yet
            "pushNotifications": False,  # PushAdapter — not yet
        },
    }
```

This function is the only new code required at A2A transport adoption time, because the vocabulary was aligned at the executor level from the start.

---

## Cross-references

| Document | Relation |
|---|---|
| [`architecture.md`](architecture.md) | Core loopkit architecture — executor and adapter roles |
| [`community-feed-trust-pathway.md`](community-feed-trust-pathway.md) | CommunityFeedAdapter trust wiring — P40 govkit integration |
| [`polling-adapter-extension-pattern.md`](polling-adapter-extension-pattern.md) | PollingAdapter extension patterns — PushAdapter is the push-capable complement |
| [`~/.claude/skills/compass/docs/a2a-protocol-alignment.md`](../../../../.claude/skills/compass/docs/a2a-protocol-alignment.md) | Full cross-project alignment doc — P40/P54 concept mappings |
