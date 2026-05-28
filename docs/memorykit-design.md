# agentic-memorykit — Design Brief

_Date: 2026-05-28_
_Status: Draft — v0.1_
_Informs: loopkit v4 semantic memory store_

---

## North Star

A standalone, zero-dependency Python package that gives any agent — loopkit-based or otherwise — a queryable semantic fact store backed by JSONL, with rule-based reconciliation and BM25 keyword retrieval out of the box, and optional vector search behind a lazy-import gate.

**Not** a replacement for the event log. **Not** a session summary tool. A durable fact store that agents read and write structured knowledge to — separate from, but traceable back to, the event stream that produced it.

---

## Package Identity

| Property | Value |
|---|---|
| Package name | `agentic-memorykit` |
| Import name | `agentic_memorykit` |
| Repo | `igster4ever/agentic-memorykit` |
| Runtime deps | **zero** (stdlib only) |
| Optional extras | `[vector]` — sqlite-vec or hnswlib, lazy-imported |
| Loopkit integration | Optional extra `[loopkit]` — adds `EventBus.memory` property and `MemoryAdapter` |
| Python | 3.11+ |
| Licence | MIT |

---

## Core Data Model

### MemoryRecord

```python
@dataclass
class MemoryRecord:
    memory_id: str          # uuid4
    key: str                # scoped fact key, e.g. "user.language_preference"
    value: str              # natural-language fact string
    agent_id: str           # agent that wrote this; use "shared" for bus-wide facts
    confidence: float       # 0.0–1.0; inherited from _meta.confidence if loopkit source
    tags: list[str]         # free-form; used for tag-based retrieval
    source_event_id: str | None   # loopkit Event.event_id — back-reference to episodic log
    correlation_id: str | None    # loopkit correlation_id — workflow scope
    created_at: str         # ISO 8601 UTC
    updated_at: str         # ISO 8601 UTC
    expires_at: str | None  # ISO 8601 UTC; None = no expiry; expired records excluded from query/list
    valid: bool             # False = soft-deleted / superseded
    version: int            # increments on each UPDATE
```

### MemoryOperation (history log)

```python
@dataclass
class MemoryOperation:
    op: Literal["ADD", "UPDATE", "DELETE", "NOOP"]
    memory_id: str
    agent_id: str
    old_value: str | None
    new_value: str | None
    reason: str             # "new fact" | "key conflict — supersedes" | "explicit delete" | "duplicate"
    timestamp: str          # ISO 8601 UTC
```

---

## Storage Layout

```
~/.cache/<app>/memory/
├── facts-<agent_id>.jsonl       # one MemoryRecord per line (all versions, valid + invalid)
└── ops-<agent_id>.jsonl         # one MemoryOperation per line (full audit trail)
```

**Design choices:**
- One file per `agent_id` — scans stay bounded; cross-agent queries load multiple files
- All records appended (ADD + UPDATE both append); soft-deletes flip `valid=False` in a new record line — no in-place mutation
- History file (`ops-*.jsonl`) is append-only; never modified
- `load_facts(agent_id, valid_only=True)` returns the latest version of each `memory_id` by scanning and deduplicating on `memory_id` + `version`

---

## Interface

```python
class MemoryStore:
    def __init__(self, store_dir: Path): ...

    # Write a fact — runs reconciliation, returns the operation type
    async def write(
        self,
        key: str,
        value: str,
        agent_id: str,
        *,
        confidence: float = 1.0,
        tags: list[str] = (),
        source_event_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MemoryOperation: ...

    # Keyword + tag retrieval (BM25 + exact tag filter)
    async def query(
        self,
        text: str,
        agent_id: str | None = None,       # None = search across all agents
        *,
        tags: list[str] = (),
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[tuple[MemoryRecord, float]]: ...   # (record, score)

    # Exact key lookup within a scope
    async def get(self, key: str, agent_id: str) -> MemoryRecord | None: ...

    # List all valid facts for a scope
    async def list(
        self,
        agent_id: str | None = None,
        tags: list[str] = (),
        min_confidence: float = 0.0,
    ) -> list[MemoryRecord]: ...

    # Soft-delete by key + agent
    async def forget(self, key: str, agent_id: str) -> bool: ...

    # Full audit trail for a memory_id
    async def history(self, memory_id: str) -> list[MemoryOperation]: ...

    # Remove all facts for an agent (hard purge — use carefully)
    async def purge(self, agent_id: str) -> int: ...
```

---

## Reconciliation Algorithm

Runs inside `write()`, pure Python, no LLM:

```
1. EXACT KEY MATCH
   Load valid facts for agent_id where fact.key == key
   If none found → ADD (new fact)

2. DUPLICATE CHECK
   Normalise both strings (lowercase, strip punctuation, collapse whitespace)
   If normalised(existing.value) == normalised(new_value) → NOOP (identical fact, skip write)

3. CONFLICT RESOLUTION
   If existing fact found with same key and different value:
     - Mark existing record valid=False (soft-delete, append new record line)
     - Write new record with version = existing.version + 1 → UPDATE
     - Log MemoryOperation(op="UPDATE", reason="key conflict — supersedes")

4. FUZZY NEAR-DUPLICATE (optional, ~5 lines)
   If no exact key match but normalised edit-distance(existing.value, new_value) < threshold:
     - Surface as candidate NOOP or UPDATE (caller decides)
     - Default: treat as ADD (safe; duplicates surface at query time via BM25 rank)
```

**Design principle:** reconciliation is deterministic and auditable. No LLM required. The four-operation vocabulary (ADD/UPDATE/DELETE/NOOP) mirrors Mem0's proven model but with rule-based conflict detection.

---

## Retrieval — BM25 + Tag Filter

Zero dependencies — pure stdlib. ~60 lines.

```python
# Simplified BM25 implementation
# k1=1.5, b=0.75 (standard defaults)

def bm25_score(query_terms, doc_terms, corpus_stats) -> float: ...

async def query(self, text, agent_id, *, tags, min_confidence, limit):
    # 1. Load valid records (filtered by agent_id + min_confidence)
    candidates = await self._load(agent_id, min_confidence)

    # 2. Tag filter (AND — all specified tags must be present)
    if tags:
        candidates = [r for r in candidates if all(t in r.tags for t in tags)]

    # 3. BM25 score each candidate value against query terms
    tokenise = lambda s: s.lower().split()
    corpus = [tokenise(r.value) for r in candidates]
    query_terms = tokenise(text)
    scores = [bm25_score(query_terms, doc, corpus_stats(corpus)) for doc in corpus]

    # 4. Rank and return top-N
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    return ranked[:limit]
```

**Optional vector search** (lazy import, only if `agentic-memorykit[vector]` installed):
```python
try:
    from agentic_memorykit._vector import embed, cosine_sim
    # ... fuse BM25 score with cosine score (0.5 weight each)
except ImportError:
    pass  # falls back to BM25 only — no error, no warning
```

---

## Loopkit Integration (optional extra)

When `agentic-memorykit[loopkit]` is installed alongside `agentic-loopkit`:

### `EventBus.memory` property

```python
# In agentic_loopkit/bus.py (or via monkey-patch in the extra)
@property
def memory(self) -> "MemoryStore":
    if self._memory is None:
        from agentic_memorykit import MemoryStore
        self._memory = MemoryStore(self._store_dir / "memory")
    return self._memory
```

### `MemoryAdapter` — event → fact bridge

An optional `PollingAdapter` subclass that watches the event log and auto-populates
the memory store from events carrying `_meta.confidence >= threshold`:

```python
class MemoryAdapter(AgentBase):
    """
    Subscribes to specified streams. On each event:
    - Calls extract(event) → list[MemoryFact] (abstract — implement in subclass)
    - Writes each fact to bus.memory with confidence from _meta.confidence
    """
    async def extract(self, event: Event) -> list[MemoryFact]: ...
```

This keeps memory population explicit (agents control what gets extracted) rather than
auto-extracting via LLM — consistent with loopkit's "LLM is not the orchestrator" principle.

---

## Module Layout

```
agentic_memorykit/
├── __init__.py              # Public API: MemoryStore, MemoryRecord, MemoryOperation
├── store.py                 # MemoryStore — write, query, get, list, forget, history, purge
├── models.py                # MemoryRecord + MemoryOperation dataclasses
├── reconcile.py             # ADD/UPDATE/DELETE/NOOP algorithm (pure stdlib)
├── retrieval.py             # BM25 implementation + tag filter
├── _vector.py               # Optional vector search — imported lazily; not in __init__
└── loopkit/
    ├── __init__.py          # exports MemoryAdapter, bus_memory_property
    └── adapter.py           # MemoryAdapter(AgentBase) + EventBus.memory patch

tests/
├── test_store.py            # write/get/list/forget/history/purge
├── test_reconcile.py        # ADD/UPDATE/DELETE/NOOP cases + soft-delete
├── test_retrieval.py        # BM25 ranking, tag filter, min_confidence gate
└── test_loopkit.py          # MemoryAdapter wiring (optional extra tests)
```

**Estimated scope:** ~350–450 lines of implementation, ~150 lines of tests.

---

## Public API

```python
from agentic_memorykit import (
    MemoryStore,
    MemoryRecord,
    MemoryOperation,
)

# Optional loopkit integration
from agentic_memorykit.loopkit import MemoryAdapter
```

---

## What This Is Not

- Not an LLM extraction pipeline — facts are written explicitly by agents, not inferred from message blobs
- Not a session summariser — it is a durable fact store, not a rolling context compressor
- Not a replacement for the EventStore — episodic memory (what happened, when) stays in JSONL event streams; semantic memory (what is true, durably) lives here
- Not cloud-dependent — no vector DB required; no embeddings required; no network calls in the zero-dep baseline

---

## Resolved Design Decisions

1. **sqlite3 upgrade path** — auto-migrate silently when `facts-<agent>.jsonl` exceeds 5000 lines (configurable). `MemoryStore.compact()` also available as an explicit trigger. Both paths produce the same sqlite3 output in the same store directory; JSONL is preserved as a backup until the user deletes it.

2. **Cross-agent fact sharing** — `agent_id="shared"` is the convention for bus-wide facts. No schema change; just a well-known value. `query(agent_id=None)` searches across all agent scopes including `"shared"`. Agents write to `"shared"` deliberately — no auto-promotion.

3. **Memory expiry** — `MemoryRecord.expires_at: str | None` (ISO 8601 UTC). Expired records are excluded from `query()` and `list()` results by default but preserved in JSONL for audit. Agents set `expires_at` explicitly when writing; no system-level TTL enforcement daemon. A `purge_expired()` maintenance method handles physical cleanup on demand.

4. **Dashboard integration** — extend the existing loopkit dashboard via the `[loopkit]` extra. Adds `GET /api/memory`, `GET /api/memory/{agent_id}`, `GET /api/memory/{agent_id}/{key}/history` to the FastAPI backend, plus a Memory page in the React UI. Surfaced in the same sidebar as Streams, Events, Agents, Adapters.
