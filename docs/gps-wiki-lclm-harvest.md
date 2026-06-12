# LCLM-Inspired GPS Run Corpus Access for Wiki Agents

**Source:** "End-to-End Context Compression at Scale" (Li et al., arXiv:2606.09659, 2026) — §7 Agent Scaffolding With Latent Context  
**Related:** [`lclm-compressed-event-bus.md`](lclm-compressed-event-bus.md) — covers the general EventBus/EventStore headline pattern  
**Status:** Design spike — not yet committed to build order  
**Target version:** v6 candidate  
**Estimated scope:** 1 spike session + 1–2 implementation sessions

---

## Problem

`gps-history.jsonl` is the append-only source of truth for all GPS Radar runs. As it grows, agents that reason over historical runs face the same corpus-access dilemma identified in the LCLM paper:

- **Full dump** — prohibitively expensive as run count grows; O(N×tokens_per_run) per agent invocation
- **Semantic search (Voyage AI)** — misses connections where the relevant keyword isn't obvious before reading

The LCLM paper's exact statement of the failure mode:

> "Agents usually rely on lexical or semantic search to locate the information needed. These information retrieval methods can fail when the search keyword is not obvious before reading through the information."

This maps directly onto three wiki agent scenarios:

| Agent | Problem |
|-------|---------|
| `WikiHarvestAgent` | Needs to detect overlap with existing articles without loading all prior runs |
| `WikiLintAgent` | Contradiction detection between articles fails when the contradiction is implicit (different phrasing, same claim) |
| `WikiIndexAgent` | Incremental re-indexing needs to know which runs are already covered without full re-scan |

---

## The LCLM Pattern (What We're Borrowing)

From §7 and Figure 6 of the paper:

1. **Compress** the input corpus into fixed-size chunks (512 tokens each); assign sequential integer `chunk_id`s per document
2. **Surface the full compressed index** in the agent's prompt — one row per chunk, showing document ID + chunk ID + compressed headline
3. **Agent decides** which chunks to expand based on the compressed view
4. **`EXPAND(doc_id, chunk_id)`** tool returns the original uncompressed text for that chunk on demand

Key empirical result (Table 33): adding the agentic expand loop improves NIAH (needle-in-haystack) accuracy by +17–20% over compressed-only context at 16× compression.

Section 8 explicitly identifies the next frontier as applying this to "an agent's accumulated working history, which can grow to dominate the context budget in long-horizon tasks" — exactly what `gps-history.jsonl` is.

We borrow the **architecture** (indexed corpus + selective expand tool), not the ML compression mechanism. Our "compression" is structured prose summarisation; the decoder is Claude. This is implementable in pure Python, no model infrastructure required.

---

## Proposed Design

### 1. `GpsRunChunk` — sub-run granularity

Each GPS run is split into logical chunks at write time (or on first read). Chunk boundaries follow natural structure in the run payload:

| Chunk content | Typical size |
|---------------|-------------|
| Run header (squad, date, facilitator, attendee count) | ~50 tokens |
| Each reported blocker / issue | ~100–300 tokens |
| Each action item cluster | ~80–150 tokens |
| Retro / sentiment summary | ~100–200 tokens |

This matches the paper's key insight: sub-document chunking enables the agent to expand a single issue block rather than the full run.

```python
@dataclass
class GpsRunChunk:
    run_id: str       # e.g. "gps-2026-06-12-settlements"
    chunk_id: int     # sequential, scoped to run_id (1-based)
    squad: str
    date: str         # ISO date
    chunk_type: str   # "header" | "blocker" | "action" | "retro"
    headline: str     # ≤ 120-char summary
```

`headline` generation:
- `header`: `f"{squad} — {date} — {attendee_count} attendees"`
- `blocker`: `payload["title"][:80]` or first sentence of `payload["description"]`
- `action`: `f"Action: {payload['owner']} — {payload['description'][:80]}"`
- `retro`: first sentence of `payload["summary"]`

### 2. Storage

Parallel `gps-run-chunks.jsonl` in the same directory as `gps-history.jsonl`. Same append-only JSONL format. Indexed by `(run_id, chunk_id)` for O(1) expand.

Alternatively a `gps_run_chunks` table if the project moves to SQLite — covering index on `(run_id, chunk_id)`.

### 3. New access methods on `GpsHistory` (or a new `GpsRunStore`)

```python
# Existing — unchanged
def load_runs(self, limit: int | None = None) -> list[GpsRun]: ...
def load_run(self, run_id: str) -> GpsRun | None: ...

# New
def load_run_index(self, limit: int = 30) -> list[GpsRunChunk]:
    """Return headline index for most recent `limit` runs — all chunk types, sorted newest first."""

def expand_run_chunk(self, run_id: str, chunk_id: int) -> str | None:
    """Return full text of the specified chunk. Returns None if not found."""
```

`load_run_index` is the primary corpus-access method for agents. `expand_run_chunk` is the `EXPAND(run_id, chunk_id)` equivalent.

### 4. `WikiHarvestAgent` — headline-first context

Current pattern (inferred from architecture):
```python
# Load recent runs → embed full payloads in agent context
runs = gps_history.load_runs(limit=10)
context = "\n\n".join(run.to_text() for run in runs)
```

Proposed pattern:
```python
# 1. Skim headline index (all recent runs, ~200–400 tokens for 30 runs)
index = gps_history.load_run_index(limit=30)
index_text = format_run_index(index)   # tabular: run_id | chunk_id | type | headline

# 2. Always expand run headers (cheap — header chunks are tiny)
headers = [gps_history.expand_run_chunk(c.run_id, c.chunk_id)
           for c in index if c.chunk_type == "header"]

# 3. Agent identifies which blocker/action chunks to expand
# 4. Expand on demand via expand_run_chunk
# 5. Recency window: always expand all chunks for the most recent 2 runs in full
```

Step 5 mirrors the LCLM interleaved pattern: newest content uncompressed (highest novelty), older content headline-only.

Agent prompt preamble:
```
GPS run index (most recent 30 runs, compressed to one line per chunk):
run_id                        | cid | type    | headline
gps-2026-06-12-settlements    |   1 | header  | settlements — 2026-06-12 — 8 attendees
gps-2026-06-12-settlements    |   2 | blocker | Kafka consumer lag spike — 3 topics, P1
gps-2026-06-12-settlements    |   3 | blocker | Redis TTL misconfiguration — cache invalidation
gps-2026-06-12-settlements    |   4 | action  | Action: ismith — audit TTL config across services
...

To read the full text of any chunk, call: expand_run_chunk(run_id, chunk_id)
```

### 5. `WikiLintAgent` — compressed article index for contradiction detection

Current contradiction detection path:
1. Voyage AI semantic search for potentially contradictory articles
2. Claude Haiku judges each candidate pair

Failure mode: contradictions between articles that use different terminology for the same concept aren't surfaced by the Voyage query.

Proposed addition (pre-step before existing Voyage pass):
1. Build a compressed article index: one-line headline per article (title + last-updated + squad + confidence level)
2. Give `WikiLintAgent` the full compressed index as initial context
3. Agent identifies candidate pairs from the compressed view before calling Voyage for semantic expansion
4. Expand full article text only for flagged pairs

This is a **pre-filter** that improves recall; it doesn't replace the existing Voyage+Haiku path.

---

## Relationship to Existing Design

| Component | Interaction |
|-----------|-------------|
| `lclm-compressed-event-bus.md` | Same architectural pattern; EventBus headlines cover real-time event streams; GPS run chunks cover the historical run corpus. Both implement `load_headlines` + `expand(id)`. |
| `WikiHarvestAgent` | Headline-first context replaces full-payload load for runs >2 sessions old |
| `WikiLintAgent` | Compressed article index as pre-filter; Voyage+Haiku path unchanged |
| `WikiIndexAgent` | Can use `load_run_index` to check coverage without loading full payloads |
| `gps-history.jsonl` | Unchanged; `gps-run-chunks.jsonl` is additive |
| `bridge_server/wiki_routes.py` | `GET /api/gps/runs/index` returning `list[GpsRunChunk]` is the natural API complement; no frontend changes required to ship the backend |

---

## Spike Goals

| Question | Pass criterion |
|----------|----------------|
| **Chunking quality** | Chunk boundaries correctly isolate blockers and actions from 5 real GPS run payloads | Manual review |
| **Headline signal** | Compressed index of 30 runs (all chunk types) fits in <500 tokens and gives sufficient signal to identify relevant runs without expanding | Token count + manual relevance review |
| **Expand latency** | `expand_run_chunk(run_id, chunk_id)` resolves in <5ms for a 1,000-run history | `timeit` measurement |
| **Harvest agent fidelity** | Agent with headline-first context produces equivalent article suggestions to agent with full-payload context, on 3 representative runs | Side-by-side output comparison |

---

## Estimated Scope

| Work | Sessions |
|------|----------|
| Spike: `GpsRunChunk`, `load_run_index`, `expand_run_chunk`, no agent integration | 0.5 |
| `WikiHarvestAgent` headline-first context | 1 |
| `WikiLintAgent` compressed article pre-filter | 1 |
| Tests + API endpoint | 0.5–1 |
| **Total** | **3–3.5** |
