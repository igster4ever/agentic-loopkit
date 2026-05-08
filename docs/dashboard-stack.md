# Dashboard Management Stack — Logical Layers

Illustrates how the React frontend, FastAPI dashboard backend, and loopkit
runtime bind together, and which component handles each data flow.

---

```
╔══════════════════════════════════════════════════════════════════════════╗
║                    BROWSER  —  React 19 + Vite + Bun                    ║
║                                                                          ║
║   StreamsPage   EventsPage   AgentsPage   AdaptersPage   LiveTail        ║
║        │             │            │             │              │          ║
║     useApi()      useApi()     useApi()      useApi()   useEventStream() ║
║   /api/streams  /api/events  /api/agents  /api/adapters   /ws/tail       ║
║   /api/chains                                             (WebSocket)    ║
╚════════╤═════════════╤════════════╤══════════════╤═══════════════╤═══════╝
         │             │            │              │               │
         │   HTTP GET (JSON responses)             │      WS (upgrade)
         │             │            │              │               │
╔════════╪═════════════╪════════════╪══════════════╪═══════════════╪═══════╗
║        │   FastAPI Dashboard App — create_app(bus)               │       ║
║        │             │            │              │               │       ║
║   ┌────▼───────┐  ┌──▼───────┐  ┌▼──────────┐  ┌▼───────────┐  │       ║
║   │ /api/      │  │ /api/    │  │ /api/     │  │ /api/      │  │       ║
║   │ streams    │  │ events   │  │ agents    │  │ adapters   │  │       ║
║   │ chains     │  │ events   │  │           │  │            │  │       ║
║   │            │  │ /{id}    │  │           │  │            │  │       ║
║   └──────┬─────┘  └────┬─────┘  └─────┬─────┘  └──────┬─────┘  │       ║
║          │ reads       │ reads         │ reads          │ reads  │       ║
║          │ EventStore  │ EventStore    │ bus.agents     │ bus.   │       ║
║          │ + edge calc │ + filter      │ .subscriptions │ adapters       ║
║          │             │               │              │ .cursor_state()  ║
║          │             │               │              │         │       ║
║          │   Depends(get_bus) ─────────┘──────────────┘         │       ║
║          │   conn.app.state.bus                                  │       ║
║          │                                          ┌────────────▼─────┐ ║
║          │                                          │ WS /ws/tail      │ ║
║          │                                          │                  │ ║
║          │                                          │ asyncio.Queue    │ ║
║          │                                          │ (maxsize=500)    │ ║
║          │                                          │                  │ ║
║          │                          ┌───────────────│ subscribe(stream,│ ║
║          │                          │  at connect   │   enqueue)       │ ║
║          │                          │               │                  │ ║
║          │                          │ at disconnect:│ unsubscribe()    │ ║
║          │                          └───────────────│ immediately      │ ║
║          │                                          └──────────────────┘ ║
╚══════════╪══════════════════════════════════╤════════════════════════════╝
           │ bound once at startup            │ subscribes/unsubscribes
           ▼                                  ▼   per WS connection lifetime
╔══════════════════════════════════════════════════════════════════════════╗
║                       EventBus  (loopkit runtime)                        ║
║                                                                          ║
║  persist-before-fanout:  JSONL append  →  EventRouter dispatch           ║
║                                                                          ║
║  ┌───────────────────┐   ┌──────────────────┐   ┌─────────────────────┐ ║
║  │   EventRouter     │   │   EventStore     │   │   Agent Registry    │ ║
║  │                   │   │                  │   │   (.agents)         │ ║
║  │  stream → [cbs]   │   │  append_event()  │   │                     │ ║
║  │  WILDCARD_STREAM  │◄──│  ← every publish │   │   AgentBase × N     │ ║
║  │  "*" catches all  │   │                  │   │   (OODA loops)      │ ║
║  │                   │   │  load_events()   │   │   .subscriptions    │ ║
║  │  ← WS handler     │   │  ← REST reads    │   │   → /api/agents     │ ║
║  │    plugs in here  │   │                  │   └─────────────────────┘ ║
║  │    at connect     │   │  compact_stream()│                           ║
║  │                   │   │                  │   ┌─────────────────────┐ ║
║  └───────────────────┘   └────────┬─────────┘   │ Adapter Registry    │ ║
║                                   │             │   (.adapters)       │ ║
║                         writes /  │ reads       │                     │ ║
║                         appends   │             │   PollingAdapter × N│ ║
║                                   │             │   .cursor_state()   │ ║
║                                   │             │   → /api/adapters   │ ║
║                                   │             └─────────────────────┘ ║
╚═══════════════════════════════════╪════════════════════════════════════╝
                                    │ one JSONL file per stream
                                    ▼
╔══════════════════════════════════════════════════════════════════════════╗
║                         JSONL  file store  (disk)                        ║
║                                                                          ║
║   ~/.cache/<app>/events-<stream>.jsonl      one file per stream name     ║
║   ~/.cache/<app>/events-system.jsonl        bus lifecycle events         ║
║   ~/.cache/<app>/cursor-<adapter>.json      adapter resume cursors       ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## Data flow summary

| Client call | Route | Reads from | Notes |
|---|---|---|---|
| `GET /api/streams` | `routes/streams.py` | EventStore — scans JSONL headers | counts + last timestamp per stream file |
| `GET /api/events` | `routes/events.py` | EventStore — `load_events()` | filter by stream / type / correlation / since / limit |
| `GET /api/events/{id}` | `routes/events.py` | EventStore — `load_events()` short-circuit | returns event + related chain events |
| `GET /api/chains/{id}` | `routes/chains.py` | EventStore + chain builder | computes causation edges; derives summary.status |
| `GET /api/agents` | `routes/agents.py` | `bus.agents` + `.subscriptions` | runtime registry only — no JSONL reads |
| `GET /api/adapters` | `routes/adapters.py` | `bus.adapters` + `.cursor_state()` | runtime registry only — no JSONL reads |
| `WS /ws/tail` | `ws.py` | EventRouter subscription | live push via asyncio.Queue; drops on QueueFull |

## Binding contract

The dashboard has **one entry point** into the loopkit runtime:

```python
# agentic_loopkit/dashboard/dependencies.py
def get_bus(conn: HTTPConnection) -> EventBus:
    return conn.app.state.bus          # set once in create_app(bus)
```

`conn` is the base class of both `Request` (HTTP routes) and `WebSocket` — the same
dependency resolves for all route types without duplication.

The EventBus reference is **read-only from the dashboard's perspective**:
- REST routes call `bus.store.load_events()`, `bus.agents`, `bus.adapters`
- The WS handler calls `bus.router.subscribe()` / `bus.router.unsubscribe()`
- No route calls `bus.publish()` — the dashboard is a read-only observer

## WS subscription lifecycle

```
  client connects
       │
       ▼
  bus.router.subscribe(stream, enqueue)   ← enqueue is a closure over the asyncio.Queue
       │
       │   server loop: dequeue → websocket.send_text(json)
       │
  client disconnects (WebSocketDisconnect)
       │
       ▼
  bus.router.unsubscribe(stream, enqueue)  ← immediate; no leaked subscriber
```

If `stream` param is omitted, the handler subscribes to `WILDCARD_STREAM = "*"`,
receiving all events regardless of stream. The `event_type` filter is applied inside
the `enqueue` closure before queuing — never on the router itself.
