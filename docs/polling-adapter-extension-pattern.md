# PollingAdapter Extension Pattern — JSONL Community Feeds

_P40 Phase 1 — design reference, 2026-06-19_

This doc covers how to extend `PollingAdapter` for external JSONL community feeds and how to
wire the trust graduation pathway for untrusted events. The `CommunityFeedAdapter` (v7-2)
follows this pattern exactly.

---

## The two-method contract

`PollingAdapter` requires one abstract method: `poll(cursor)`. Everything else — cursor
persistence, error counting, stall detection, bus drain — is handled by `tick()`.

```python
class PollingAdapter(ABC):
    name: str = "adapter"          # adapter identifier in logs + cursor key
    stall_threshold: int = 3       # consecutive failures before system.adapter_stalled

    @abstractmethod
    async def poll(
        self, cursor: Optional[Any]
    ) -> tuple[list[Event], Optional[Any]]:
        """
        Fetch new events from the source since `cursor`.

        Returns:
            events  — new Event objects to publish (may be empty)
            cursor  — updated cursor, or None to leave unchanged
        """
```

The cursor is opaque — use whatever tracks position in the source:

| Source type | Cursor form |
|---|---|
| REST API with pagination | page token or `since` timestamp (Unix ms) |
| Git log | commit SHA |
| JSONL file | byte offset (int) |
| Slack | `{channel_id: ts}` dict |

---

## JSONL feed pattern

For a local or fetched JSONL file, use a **byte offset** as the cursor. Each `poll()` seeks
to the cursor, reads new lines, returns the new offset.

```python
class JsonlFeedAdapter(PollingAdapter):
    """
    Reads a JSONL file from `feed_path` and emits one Event per new line.
    Cursor = byte offset into the file.
    """
    name = "jsonl-feed"

    def __init__(self, bus: EventBus, feed_path: Path, event_type: str) -> None:
        super().__init__(bus)
        self._feed_path = feed_path
        self._event_type = event_type

    async def poll(self, cursor: Optional[int]) -> tuple[list[Event], Optional[int]]:
        offset = cursor or 0
        events: list[Event] = []

        if not self._feed_path.exists():
            return [], None

        with self._feed_path.open("rb") as fh:
            fh.seek(offset)
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(Event(
                    event_type   = self._event_type,
                    source       = self.name,
                    payload      = payload,
                    trust_level  = TrustLevel.UNTRUSTED,
                ))
            new_offset = fh.tell()

        return events, (new_offset if new_offset != offset else None)
```

**Why byte offset?** Append-only JSONL files grow monotonically. A byte offset is
exact, cheap to persist, and survives restarts. A line-number cursor breaks if lines
are re-ordered or the file is compacted.

---

## Trust tagging

Community-sourced events must be tagged `trust_level = TrustLevel.UNTRUSTED` at emit time.
This is set directly on the `Event` object — not inferred downstream.

```python
from agentic_loopkit.events.models import TrustLevel

Event(
    event_type  = "community.event_received",
    source      = self.name,
    payload     = payload,
    trust_level = TrustLevel.UNTRUSTED,
)
```

`TrustLevel` is a StrEnum: `UNTRUSTED → LOW → MEDIUM → HIGH`. `aggregate_confidence()`
weights events by trust level — untrusted events contribute less to aggregate confidence
scores, so untrusted signals cannot silently override high-trust decisions.

---

## Trust graduation pathway

Untrusted community events become trusted through `GovernanceLearningAgent`. The pathway:

```
community feed event (UNTRUSTED)
    │
    ▼
GovernanceLearningAgent
    │  observes community.* stream
    │  accumulates signal (volume, consistency, agreement with HIGH-trust events)
    │
    ├─ below threshold  →  keeps UNTRUSTED  (no action)
    │
    └─ threshold met    →  emits governance.policy_recommendation
                                payload: {
                                    subject:     "community.event_received",
                                    action:      "promote_trust",
                                    target_level: "low",   # or "medium"
                                    rationale:   "...",
                                    confidence:  0.82,
                                }
                                    │
                                    ▼
                            KillSwitchAgent / custom PolicyAgent
                                reads governance.policy_recommendation
                                promotes source to LOW (or MEDIUM)
                                persists trust policy update
```

Promotion is always one level at a time (UNTRUSTED → LOW → MEDIUM → HIGH). A `MEDIUM`
event has sufficient weight to influence projection pages; a `HIGH` event is treated as
first-party. The governance audit trail is preserved at every step.

### Wiring GovernanceLearningAgent for a community stream

```python
from agentic_govkit import GovernanceLearningAgent

# Instantiate and subscribe to the community stream
learner = GovernanceLearningAgent(
    name="community-learner",
    bus=bus,
    target_streams=["community"],       # streams to learn from
    policy_streams=["governance"],      # where to emit policy_recommendation
)
learner.subscribe("community", "governance")
bus.register(learner)
```

`GovernanceLearningAgent` is a `ProjectionAgent` subclass. Its `materialise()` call is
where trust signals are accumulated — implement it (or use the default policy logic) to
produce `POLICY_RECOMMENDATION` events when the evidence threshold is met.

---

## CommunityFeedAdapter sketch (v7-2 target)

`CommunityFeedAdapter` in `agentic_loopkit/adapters/community.py` will extend this pattern
for named community JSONL feeds with configurable poll intervals.

```python
class CommunityFeedAdapter(PollingAdapter):
    """
    Polls a JSONL community feed at `feed_url` (local path or remote URL).
    Emits events with trust_level=UNTRUSTED. Trust promotion is handled
    externally via GovernanceLearningAgent + KillSwitchAgent.
    """
    name = "community-feed"

    def __init__(
        self,
        bus: EventBus,
        feed_url: str,           # local path or HTTPS URL
        community_name: str,     # becomes the event stream name
        event_type: str,         # e.g. "community.signal_received"
        poll_interval: float = 300.0,  # seconds; default 5 minutes
    ) -> None:
        super().__init__(bus)
        self._feed_url = feed_url
        self._community_name = community_name
        self._event_type = event_type
        self._poll_interval = poll_interval

    async def poll(self, cursor: Optional[int]) -> tuple[list[Event], Optional[int]]:
        # Fetch/read the feed, seek to cursor offset, parse new JSONL lines.
        # Tag each event UNTRUSTED with source=self._community_name.
        ...
```

**Cursor for remote feeds:** for HTTP feeds, use an `ETag` or `Last-Modified` header as the
cursor rather than a byte offset — the server controls content identity.

**Event stream naming:** use `community.<name>` (e.g. `community.loopkit-users`) so
governance agents can subscribe to all community streams via `community.*`.

---

## Extension checklist

When building a new JSONL-backed adapter:

- [ ] Subclass `PollingAdapter`; set a unique `name` class attribute
- [ ] Implement `poll(cursor)` — return `(events, new_cursor)` or `([], None)` if nothing new
- [ ] Set `trust_level` on every emitted `Event` (UNTRUSTED for external sources)
- [ ] Use byte offset (local files) or ETag/timestamp (remote) as cursor — not line numbers
- [ ] Register `GovernanceLearningAgent` on the adapter's stream for trust graduation
- [ ] Wire `stall_threshold` appropriately for the feed's expected update frequency
- [ ] Test with `tick()` directly — `poll()` is unit-testable in isolation with a tmp file
