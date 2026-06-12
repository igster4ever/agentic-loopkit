# LCLM-Inspired Compressed Event Bus Headlines

**Source:** "End-to-End Context Compression at Scale" (Li et al., arXiv:2606.09659, 2026) — §7 Agent Scaffolding With Latent Context  
**Status:** Design spike — not yet committed to build order  
**Target version:** v6 candidate (pending spike outcome)  
**Estimated scope:** 1 session (spike) + 2–3 sessions (full implementation)

---

## Problem

In long-running agentic loops, `EventStore` accumulates a large history of events. When an orchestrator agent reasons over this history — deciding which sub-agent to invoke next, diagnosing a failure, or correlating a chain — it currently receives either:

- **Full event payload dump** — expensive; context-burning; O(N) tokens per tick
- **Semantic search result** — cheap; misses events where the relevant keyword isn't obvious before reading

The LCLM paper identifies exactly this failure mode:

> "Agents usually rely on lexical or semantic search to locate the information needed. These information retrieval methods can fail when the search keyword is not obvious before reading through the information. For example, in a large codebase, a bug reported in the 'dashboard login flow' may actually originate in an indirectly called entitlement module that never mentions 'dashboard' or 'login'."

The paper's fix — compressed corpus-level visibility + `EXPAND(i)` on demand — maps directly onto the EventBus history access pattern. The agent **skims** the full event stream via headlines, then **expands** only the events it determines are relevant.

---

## Proposed Architecture

### `EventHeadline` dataclass

A new lightweight record written alongside the full event payload at persist time:

```python
@dataclass
class EventHeadline:
    event_id: str       # matches Event.event_id
    stream: str
    event_type: str
    timestamp: str      # ISO-8601 UTC
    headline: str       # ≤ 120-char summary
    chunk_id: int       # sequential integer for EXPAND addressing
```

`headline` generation rules:
- If event payload has a `summary` field: use `payload["summary"][:120]`
- If event has `_meta.confidence`: append ` [conf: {level}]`
- Fallback: `f"{event_type} on {stream} at {timestamp[:16]}"`

`chunk_id` is a namespace-scoped sequential integer assigned at persist time (not a UUID). This makes `expand_event(chunk_id)` O(1) with a simple index.

### Storage

Headlines stored in a parallel `headlines-<stream>.jsonl` alongside the existing `events-<stream>.jsonl`. Same append-only, JSONL format — compatible with `compact_stream()`.

Alternatively: if the project moves toward SQLite-backed storage, add a `headlines` table with a covering index on `(chunk_id, stream)`.

### `EventStore` changes

```python
# Existing — unchanged
def load_events(self, stream: str, limit: int | None = None) -> list[Event]: ...
def load_all_events(self) -> list[Event]: ...

# New
def load_headlines(self, stream: str, limit: int = 50) -> list[EventHeadline]: ...
def expand_event(self, chunk_id: int) -> Event | None: ...
```

`load_headlines` is the primary history access method for orchestrator agents. `expand_event` is the `EXPAND(i)` equivalent.

### `EventBus` delegation

`EventBus` exposes `load_headlines` and `expand_event` as pass-through delegates to `EventStore`. No behaviour change to existing `emit` / subscribe flow.

### `AgentBase.observe()` pattern

Current pattern (in `agents/base.py`):
```python
# Load all events from store → embed in context
events = self._bus.load_events(stream)
context = "\n".join(str(e) for e in events)
```

Proposed pattern:
```python
# 1. Skim headlines (full corpus, cheap)
headlines = self._bus.load_headlines(stream, limit=100)

# 2. Identify relevant chunk_ids (model decision, in prompt)
# ... model selects: [chunk_id_3, chunk_id_7, chunk_id_12]

# 3. Expand only relevant events
relevant = [self._bus.expand_event(cid) for cid in selected_ids]

# 4. Keep last N events uncompressed (recency window — always full fidelity)
recent = self._bus.load_events(stream, limit=5)
```

Step 4 mirrors the LCLM interleaved pattern: recent context uncompressed (immediate reasoning needs full fidelity), older context headline-only (global visibility, low cost).

### `ProjectionAgent` integration

`ProjectionAgent.materialise(events)` currently receives `list[Event]`. A headline-first variant would:
1. Receive `list[EventHeadline]` for the full stream
2. Call `expand_event` for salient events (e.g. those with `confidence >= HIGH` or matching the projection goal)
3. Materialise from expanded events + headline text for the rest

This is an optional optimisation, not a breaking change. `materialise(events)` continues to work unchanged.

### Dashboard `/api/events` alignment

The dashboard's `GET /api/events` endpoint already returns a list of events with metadata. A `GET /api/events/headlines` endpoint returning `EventHeadline` list would be the natural lightweight companion — consistent with `GET /api/events/{id}` as the expand endpoint. No frontend changes required to ship the backend.

---

## Spike Goals

Before committing to the full build, a spike session should answer:

| Question | Pass criterion |
|----------|----------------|
| **Headline quality** | Template-generated headlines (no LLM) provide sufficient signal for an orchestrator to identify relevant events in a representative 50-event history | Manual review of 3 real runs |
| **Write overhead** | Headline write at emit time < 2ms per event | `timeit` measurement against `EventStore._persist` |
| **Compatibility** | `load_headlines` returns valid data without breaking any existing test | `pytest` green after store changes |
| **Index correctness** | `expand_event(chunk_id)` resolves to the correct event 100% across a round-trip test | Unit test |

---

## Relationship to Existing Work

| Component | Interaction |
|-----------|-------------|
| `events/store.py` — `load_events` / `load_all_events` | Unchanged; headlines are additive |
| `events/store.py` — `compact_stream()` | Must preserve headlines during compaction; compact full payloads, keep headlines |
| `agents/projection.py` — `ProjectionAgent` | Opportunistic refactor to headline-first in same session |
| `agentic-memorykit expand(id)` | Same architectural pattern; see `agentic-memorykit/docs/lclm-expand-api.md` |
| Dashboard `/api/events` | Backend-only addition; no frontend changes needed to ship |

---

## Estimated Scope

| Work | Sessions |
|------|----------|
| Spike: `EventHeadline`, `load_headlines`, `expand_event`, no AgentBase integration | 1 |
| Full: `AgentBase.observe()` headline-first pattern, `ProjectionAgent` opt-in refactor | 1–2 |
| Tests + docs hygiene | 0.5–1 |
| **Total** | **2.5–4** |
