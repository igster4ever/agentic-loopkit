# Dashboard Architecture

_Decided: 2026-05-02_

A realtime event inspector dashboard for agentic-loopkit.
Provides event chain visualisation, live tail, and runtime introspection
via a FastAPI management API + React frontend.

---

## Goals

- Inspect event chains by `correlation_id` (DAG graph view)
- Live-tail event streams in realtime via WebSocket
- Browse and filter the JSONL event store
- Introspect registered agents, adapters, and executor state
- Cold-startable by a future Claude session with no prior context

---

## Design constraints

- Core loopkit remains **zero runtime dependencies** — dashboard is an optional extra
- FastAPI serves both the REST/WS API and the bundled frontend static files
- Frontend is **read-only** — no write operations via the UI (no `POST /publish`, no agent control)
- Backend is instantiated from a running `EventBus` — it does not own the bus

---

## Directory structure

```
agentic-loopkit/
│
├── agentic_loopkit/
│   └── dashboard/                    # Python package — pip install agentic-loopkit[dashboard]
│       ├── __init__.py               # exports create_app(bus) → FastAPI
│       ├── app.py                    # FastAPI app factory + static file mount
│       ├── dependencies.py           # FastAPI dependency: get_bus() → EventBus
│       ├── ws.py                     # WebSocket live-tail handler
│       └── routes/
│           ├── __init__.py
│           ├── events.py             # GET /api/events, GET /api/events/{event_id}
│           ├── chains.py             # GET /api/chains/{correlation_id}
│           ├── streams.py            # GET /api/streams
│           ├── agents.py             # GET /api/agents
│           └── adapters.py           # GET /api/adapters
│
├── dashboard/
│   └── ui/                           # Bun + Vite + React + TypeScript
│       ├── package.json              # bun as runtime; vite as dev server
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── index.html
│       └── src/
│           ├── main.tsx
│           ├── App.tsx               # router: /chains/:id | /streams | /agents | /live
│           ├── types/
│           │   └── events.ts         # TypeScript mirror of Python Event dataclass
│           ├── stores/
│           │   ├── graph.ts          # Zustand: event chain graph state
│           │   ├── filters.ts        # Zustand: sidebar filter state
│           │   └── livetail.ts       # Zustand: live tail buffer + pause state
│           ├── hooks/
│           │   ├── useEventStream.ts # native WebSocket + exponential backoff reconnect
│           │   └── useApi.ts         # typed fetch wrappers
│           └── components/
│               ├── layout/
│               │   ├── Sidebar.tsx            # nav + filter controls
│               │   └── EventDetailPanel.tsx   # right panel: payload, chain, context
│               ├── graph/
│               │   ├── EventChainGraph.tsx    # @xyflow/react DAG
│               │   ├── EventNode.tsx          # custom node: type badge, timestamp, source
│               │   └── layout.ts              # dagre auto-layout for causation graph
│               ├── timeline/
│               │   └── EventTimeline.tsx      # Recharts scatter — time × stream
│               ├── table/
│               │   └── EventTable.tsx         # filterable event list
│               └── livetail/
│                   └── LiveTail.tsx           # scrolling live event stream
```

---

## Python backend

### Installation

```toml
# pyproject.toml addition
[project.optional-dependencies]
dashboard = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "websockets>=12",
]
```

### App factory

```python
# agentic_loopkit/dashboard/app.py

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routes import events, chains, streams, agents, adapters
from .ws import router as ws_router
from ..bus import EventBus

_UI_DIST = Path(__file__).parent.parent.parent / "dashboard" / "ui" / "dist"


def create_app(bus: EventBus) -> FastAPI:
    """
    Instantiate the dashboard FastAPI app bound to a running EventBus.

    Usage (FastAPI lifespan):
        app.state.dashboard = create_app(bus)
        app.mount("/dashboard", app.state.dashboard)

    Or run standalone:
        import uvicorn
        uvicorn.run(create_app(bus), host="0.0.0.0", port=8765)
    """
    dashboard = FastAPI(title="agentic-loopkit dashboard", docs_url="/api/docs")
    dashboard.state.bus = bus

    dashboard.include_router(events.router,   prefix="/api")
    dashboard.include_router(chains.router,   prefix="/api")
    dashboard.include_router(streams.router,  prefix="/api")
    dashboard.include_router(agents.router,   prefix="/api")
    dashboard.include_router(adapters.router, prefix="/api")
    dashboard.include_router(ws_router)

    if _UI_DIST.exists():
        dashboard.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")

    return dashboard
```

### Dependency

```python
# agentic_loopkit/dashboard/dependencies.py

from fastapi import Request
from ..bus import EventBus

def get_bus(request: Request) -> EventBus:
    return request.app.state.bus
```

---

## HTTP API contract

All responses are JSON. Errors follow `{"detail": "<message>"}` (FastAPI default).

### GET `/api/streams`

Returns stream names with event counts.

**Response:**
```json
[
  {"stream": "clickup",  "event_count": 142, "last_event_at": "2026-05-02T10:15:44Z"},
  {"stream": "analysis", "event_count": 87,  "last_event_at": "2026-05-02T10:17:03Z"},
  {"stream": "system",   "event_count": 12,  "last_event_at": "2026-05-02T07:13:09Z"}
]
```

---

### GET `/api/events`

Filtered event list. All params optional.

**Query params:**
| Param | Type | Description |
|---|---|---|
| `stream` | string | Filter by stream name |
| `event_type` | string | Exact match on event_type |
| `correlation_id` | string | Filter by correlation_id |
| `source` | string | Filter by source component |
| `since` | ISO 8601 string | Events after this timestamp |
| `limit` | int (default 100, max 1000) | Max events returned |

**Response:**
```json
{
  "events": [
    {
      "event_id":       "8c0b7a2d-...",
      "event_type":     "analysis.task.received",
      "stream":         "analysis",
      "source":         "AnalysisAgent",
      "timestamp":      "2026-05-02T10:15:44Z",
      "payload":        {"task_id": "86an3dh2", "title": "..."},
      "causation_id":   "a2f8e7b1-...",
      "correlation_id": "a2f8e7b1-..."
    }
  ],
  "total": 87,
  "limit": 100
}
```

---

### GET `/api/events/{event_id}`

Single event with related events in the same correlation chain.

**Response:**
```json
{
  "event": { ...full Event... },
  "related": [
    {"event_id": "...", "event_type": "clickup.task.created",    "timestamp": "..."},
    {"event_id": "...", "event_type": "analysis.findings.ready", "timestamp": "..."}
  ]
}
```

---

### GET `/api/chains/{correlation_id}`

Full causation graph for a workflow. Returns all events sharing the `correlation_id`,
plus the causation edges needed to render the DAG.

**Response:**
```json
{
  "correlation_id": "a2f8e7b1-...",
  "events": [ ...full Event objects in timestamp order... ],
  "edges": [
    {"from": "event_id_of_cause", "to": "event_id_of_effect"}
  ],
  "summary": {
    "stream_count":  6,
    "agent_count":   3,
    "ralf_loops":    2,
    "ooda_events":   11,
    "error_count":   0,
    "duration_ms":   52000,
    "status":        "completed"
  }
}
```

`summary.status` is derived:
- `"completed"` — no `system.loop_rejected` or `system.adapter_error` in chain
- `"error"` — any error events present
- `"in_progress"` — latest event timestamp < 60s ago and no terminal event

---

### GET `/api/agents`

Registered agents and their subscription streams.

**Response:**
```json
[
  {
    "name":    "AnalysisAgent",
    "type":    "OODA",
    "streams": ["clickup", "analysis"]
  }
]
```

---

### GET `/api/adapters`

Adapter names and cursor state.

**Response:**
```json
[
  {
    "name":       "clickup",
    "type":       "PollingAdapter",
    "cursor":     "1746180942000",
    "last_tick":  "2026-05-02T10:15:40Z",
    "error_count": 0
  }
]
```

---

## WebSocket — live tail

**Endpoint:** `WS /ws/tail`

**Query params:**
| Param | Type | Description |
|---|---|---|
| `stream` | string | Subscribe to one stream only (omit for all) |
| `event_type` | string | Filter to a specific event_type |

**Protocol:**

On connect, the server subscribes to the EventBus router on the requested stream
(or `WILDCARD_STREAM` if no stream param).

Each message from server → client is a single JSON-serialised Event:

```json
{
  "event_id":       "...",
  "event_type":     "clickup.task.created",
  "stream":         "clickup",
  "source":         "ClickUpAdapter",
  "timestamp":      "2026-05-02T10:15:42Z",
  "payload":        {...},
  "causation_id":   null,
  "correlation_id": "a2f8e7b1-..."
}
```

Client → server: heartbeat ping only. No inbound commands.

On disconnect, the server unsubscribes from the router immediately.

**Implementation sketch:**

```python
# agentic_loopkit/dashboard/ws.py

import asyncio, json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ..events.models import WILDCARD_STREAM

router = APIRouter()

@router.websocket("/ws/tail")
async def live_tail(websocket: WebSocket, stream: str = WILDCARD_STREAM, event_type: str = ""):
    bus = websocket.app.state.bus
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    async def enqueue(event):
        if event_type and str(event.event_type) != event_type:
            return
        try:
            queue.put_nowait(event.to_dict())
        except asyncio.QueueFull:
            pass  # drop on backpressure; client can reconnect

    bus.router.subscribe(stream, enqueue)
    try:
        while True:
            item = await queue.get()
            await websocket.send_text(json.dumps(item))
    except WebSocketDisconnect:
        pass
    finally:
        bus.router.unsubscribe(stream, enqueue)
```

---

## Frontend

### Stack

| Concern | Library | Reason |
|---|---|---|
| Runtime / package manager | Bun | User preference; faster installs |
| Dev server / bundler | Vite | Mature React plugin ecosystem; Bun-compatible |
| UI framework | React 18 + TypeScript | — |
| Components | shadcn/ui + Tailwind | Pre-styled Radix primitives; dark mode built-in; copy-paste, no runtime overhead |
| Graph / DAG | @xyflow/react (React Flow v12) | Best-in-class interactive DAG; dagre auto-layout |
| Timeline / scatter | Recharts | Lightweight, React-native, Bun/Vite-compatible |
| State management | Zustand | User preference; domain-split stores |
| WebSocket | Native browser API + custom hook | ~50-line hook with exponential backoff; no extra dep |

### TypeScript Event type

```typescript
// src/types/events.ts — mirrors Python Event dataclass exactly

export interface Event {
  event_id:       string;
  event_type:     string;
  stream:         string;
  source:         string;
  timestamp:      string;   // ISO 8601 UTC
  payload:        Record<string, unknown>;
  causation_id:   string | null;
  correlation_id: string | null;
}

export interface ChainEdge {
  from: string;   // event_id
  to:   string;   // event_id
}

export interface ChainSummary {
  stream_count:  number;
  agent_count:   number;
  ralf_loops:    number;
  ooda_events:   number;
  error_count:   number;
  duration_ms:   number;
  status:        "completed" | "in_progress" | "error";
}

export interface EventChain {
  correlation_id: string;
  events:         Event[];
  edges:          ChainEdge[];
  summary:        ChainSummary;
}
```

### Zustand stores

```typescript
// stores/graph.ts
interface GraphStore {
  chain:        EventChain | null;
  selectedId:   string | null;
  setChain:     (chain: EventChain) => void;
  selectEvent:  (id: string | null) => void;
}

// stores/filters.ts
interface FilterStore {
  stream:         string;
  eventType:      string;
  correlationId:  string;
  since:          string;
  setFilter:      (key: keyof FilterState, value: string) => void;
  reset:          () => void;
}

// stores/livetail.ts
interface LiveTailStore {
  events:     Event[];
  paused:     boolean;
  maxBuffer:  number;           // default 500 — drop oldest on overflow
  push:       (event: Event) => void;
  setPaused:  (paused: boolean) => void;
  clear:      () => void;
}
```

### WebSocket hook

```typescript
// hooks/useEventStream.ts
export function useEventStream(
  params: { stream?: string; eventType?: string },
  onEvent: (event: Event) => void
): { connected: boolean; reconnecting: boolean }
```

Behaviour:
- Connects to `WS /ws/tail` with query params
- Exponential backoff on disconnect: 1s → 2s → 4s → 8s → max 30s
- Exposes `connected` and `reconnecting` for UI status indicator
- Cleans up subscription on unmount

### Component responsibilities

| Component | Responsibility |
|---|---|
| `EventChainGraph` | Renders `EventChain.events` + `edges` as a React Flow DAG. Nodes are `EventNode`; layout via dagre. Clicking a node fires `selectEvent()`. |
| `EventNode` | Custom React Flow node. Shows: event number, event_type, source, timestamp, type badge (OODA / RALF / Adapter / System). Colour-coded by source type (matches screenshot). |
| `layout.ts` | Takes `Event[]` + `ChainEdge[]`, runs dagre layout, returns React Flow `Node[]` + `Edge[]`. |
| `EventTimeline` | Recharts `ScatterChart` — x-axis = timestamp, y-axis = stream name. One dot per event; click navigates to chain view. |
| `EventTable` | Paginated table of events from `/api/events`. Columns: timestamp, event_type, source, stream, correlation_id (link to chain). |
| `LiveTail` | Virtualised list fed by `useEventStream` → `livetailStore.push()`. Pause button, clear button, stream/type filter inputs. |
| `Sidebar` | Left nav (Dashboard / Event Streams / Agents / Executions / Event Chains / Live Tail / JSONL Files / Adapters) + filter controls for the active view. |
| `EventDetailPanel` | Right panel. Shows full Event fields + payload (JSON pretty-print) + related events list + Context tab (renders `payload["_meta"]["context"]` if present). |

### Node colour scheme (from screenshot)

| Source type | Colour |
|---|---|
| External (adapters) | Purple |
| OODA Agent | Blue |
| RALF Executor | Green |
| Notification / follow-up | Orange |
| System | Grey |

Derive from `event.source` and `event.event_type` prefix at render time.

---

## Test strategy

### Unit tests — `tests/dashboard/`

**Route handlers** (via `httpx.AsyncClient` + `TestClient`, no real bus):

```python
# tests/dashboard/test_routes_events.py
# Mock EventStore: inject fixture JSONL data
# Assert: filter params produce correct SQL-like filtering on in-memory events
# Assert: pagination (limit / offset)
# Assert: 404 on unknown event_id

# tests/dashboard/test_routes_chains.py
# Assert: chain endpoint returns correct edges (causation_id → parent event_id lookup)
# Assert: summary.status derivation (completed / in_progress / error)

# tests/dashboard/test_ws.py
# Use FastAPI WebSocket test client
# Publish event to mock bus router → assert client receives serialised JSON
# Assert: stream filter excludes events from other streams
# Assert: QueueFull is handled gracefully (no crash)
```

**Chain builder logic** (pure function, no FastAPI):

```python
# tests/dashboard/test_chain_builder.py
# Given a list of Events with causation_id links
# Assert: edges are correctly derived
# Assert: root event (causation_id=None) is identified
# Assert: disconnected events (orphaned causation_id) are included as roots
```

### Integration tests — `tests/dashboard/integration/`

**Real JSONL store:**

```python
# tests/dashboard/integration/test_full_stack.py
# Write fixture events to a temp JSONL store
# Start full FastAPI app via create_app(bus)
# GET /api/events?stream=clickup → assert fixture events returned
# GET /api/chains/{correlation_id} → assert correct DAG + summary
# WS /ws/tail → publish event → assert message received within 1s timeout
```

**Fixtures:**

Provide `tests/dashboard/fixtures/events-clickup.jsonl` and
`tests/dashboard/fixtures/events-analysis.jsonl` with a canonical
5-event chain (matching the screenshot scenario) for deterministic test assertions.

---

## Build and run

### Development

```bash
# Backend (from repo root)
pip install -e ".[dashboard]"
python -m agentic_loopkit.dashboard  # starts uvicorn on :8765

# Frontend (from dashboard/ui/)
bun install
bun run dev          # Vite dev server on :5173 with proxy to :8765
```

Vite config proxies `/api` and `/ws` to the Python backend during development.

### Production build

```bash
cd dashboard/ui
bun run build        # outputs to dashboard/ui/dist/
```

FastAPI then serves `dist/` as static files at `/`. Single origin, no CORS config needed.

### `pyproject.toml` additions (summary)

```toml
[project.optional-dependencies]
dashboard = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "websockets>=12",
]

[project.scripts]
agentic-loopkit-dashboard = "agentic_loopkit.dashboard.__main__:main"
```

`__main__.py` accepts `--host`, `--port`, and `--store-dir` CLI args so consumers
can start the dashboard without writing any code.

---

## Resolved decisions

1. **Agent context tab** — **Resolved 2026-05-02.**
   Loopkit defines an optional `EventMeta` dataclass serialised into `payload["_meta"]`.
   Agents populate `_meta.context` with their observation/reasoning text.
   The dashboard renders the Context tab when `payload["_meta"]["context"]` is present.
   See `EventMeta` spec below.

2. **RALF loop grouping** — **Resolved 2026-05-02.**
   Use `SystemEventType.LOOP_STARTED` payload: `{"executor_type": "ralf"|"react"|"plan"|"reflexion"}`.
   Chain builder classifies events by inspecting `_meta.loop_type` where present,
   falling back to `system.loop_started` events in the same correlation chain.

3. **Schema field** — **Deferred.** Not in the current Event model. Revisit post-v1.

---

## EventMeta — optional payload metadata convention

`EventMeta` is a lightweight dataclass for loopkit framework metadata.
It is **never required** — consumer domain payloads are unaffected.
Loopkit components (agents, executors) populate it when emitting events.

```python
# events/models.py addition — to implement

@dataclass
class EventMeta:
    """
    Optional structured metadata for loopkit components.
    Written to payload["_meta"]. Consumer payload keys are never touched.
    """
    phase:      str   | None = None   # "observe"|"orient"|"decide"|"act"|"think"|"execute"
    loop_type:  str   | None = None   # "ooda"|"ralf"|"react"|"plan"|"reflexion"
    iteration:  int   | None = None   # loop iteration number
    confidence: float | None = None   # confidence score (0.0–1.0)
    context:    str   | None = None   # agent reasoning text → dashboard Context tab
    tags:       list[str]    = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and v != []}

    @classmethod
    def from_dict(cls, d: dict) -> "EventMeta":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
```

Helper on `Event`:

```python
def meta(self) -> Optional["EventMeta"]:
    """Extract loopkit metadata from payload["_meta"], if present."""
    raw = self.payload.get("_meta")
    return EventMeta.from_dict(raw) if raw else None
```

Usage by an OODA agent:

```python
async def act(self, event, action):
    await self._bus.publish(event.caused(
        "analysis.findings.ready",
        self.name,
        {
            **domain_payload,
            "_meta": EventMeta(
                phase="act",
                loop_type="ooda",
                confidence=0.82,
                context="New ClickUp task detected. Analysis required.",
            ).to_dict()
        }
    ))
```

The dashboard inspects `event.payload.get("_meta", {}).get("context")` for the Context tab.
All other payload keys are rendered verbatim in the Raw / Payload tab.

**Why `_meta` in payload rather than a top-level Event field:**
Adding a top-level field would change the JSONL store format and all serialisation paths.
Opt-in via payload is backward compatible — existing events without `_meta` are unaffected.
