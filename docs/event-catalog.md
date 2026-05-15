# Event Catalog — agentic-loopkit + agentic-govkit

Documents all event types by module, their stream, published-by, and typical subscribers.
Update this file when adding new event types to either module.

---

## agentic-loopkit

### `system.*` — Bus and component lifecycle

| Event type | Published by | Typical subscribers |
|---|---|---|
| `system.bus_started` | `EventBus` | monitoring agents |
| `system.bus_stopped` | `EventBus` | monitoring agents |
| `system.agent_started` | `EventBus` | monitoring agents |
| `system.agent_stopped` | `EventBus` | monitoring agents |
| `system.adapter_tick` | `PollingAdapter` | monitoring agents |
| `system.adapter_error` | `PollingAdapter` | alerting agents |
| `system.loop_started` | executors (RALF/ReAct/Plan/…) | monitoring agents |
| `system.loop_complete` | executors | downstream agents |
| `system.loop_rejected` | executors (confidence < 0.40) | retry / escalation agents |

### `projection.*` — Live document materialisation

| Event type | Published by | Condition | Typical subscribers |
|---|---|---|---|
| `projection.updated` | `ProjectionAgent` subclass | Trigger event received on subscribed stream | wiki renderers, downstream agents, dashboard |

Payload fields: `projection_id` (agent name), `content` (materialised document string),
`confidence` (aggregate_confidence() result, may be `null`), `event_count`, `streams`.

### Consumer-defined streams

Loopkit places no constraints on consumer event types. Convention: `<stream>.<action>`.
Examples from current consumers:

| Module | Example event | Stream |
|---|---|---|
| GPS·ADR | `gps.cycle_complete` | `gps` |
| GPS·ADR | `adr.record_new` | `adr` |
| ClickUpAdapter | `clickup.task_updated` | `clickup` |
| SlackAdapter | `slack.message_received` | `slack` |
| LocalGitAdapter | `git.commit_added` | `git` |

---

## agentic-govkit

### `governance.*` — Audit and policy decisions

All `governance.*` events are persisted and inspectable — the auditor is auditable.
`AuditAgent` never subscribes to `governance.*` (prevents loops).

| Event type | Published by | Condition | Typical subscribers |
|---|---|---|---|
| `governance.audit_flagged` | `AuditAgent` | generic policy rule breach | human-review agents, alerting |
| `governance.depth_exceeded` | `AuditAgent` | `delegation_depth > max_delegation_depth` | kill-switch agents, alerting |
| `governance.trust_escalation` | `AuditAgent` | `trust_level == UNTRUSTED` | policy router, alerting |
| `governance.confidence_breach` | `AuditAgent` | `_meta.confidence < confidence_threshold` | quality gates, alerting |
| `governance.dispute_opened` | `ConflictResolutionExecutor` trigger | competing agent interpretations of same entity | `ConflictResolutionExecutor`, human review |
| `governance.dispute_resolved` | `ConflictResolutionExecutor` | dispute closed by consensus | downstream agents, audit log |
| `governance.human_override` | `ConflictResolutionExecutor` / `KillSwitchAgent` | consensus not reached or escalation policy | audit log, downstream agents |
| `governance.halt` | `KillSwitchAgent` | correlation chain halted by policy | downstream agents, audit log |
| `governance.quarantine` | `KillSwitchAgent` | source quarantined by policy | policy router, alerting |

`governance.confidence_breach` payload includes `confidence` (float) and `threshold` (float)
in addition to the standard `flagged_event_id`, `flagged_event_type`, `flagged_source`, `detail` fields.

`governance.dispute_opened` and `governance.dispute_resolved` use `correlation_id` as the
dispute identifier — a dispute is a workflow; all events in it share a `correlation_id`.

---

## Module communication contract

```
agentic_loopkit  ──publishes──▶  EventBus  ──routes──▶  agentic_govkit {
                                                             AuditAgent       ── observes all (*) ──▶ publishes governance.*
                                                             KillSwitchAgent  ── listens governance.* ──▶ publishes governance.halt/quarantine/human_override
                                                             ConflictResolutionExecutor ── triggered by governance.dispute_opened ──▶ publishes governance.dispute_resolved/human_override
                                                         }
                                                             │
                                              governance.* events routed to all bus subscribers
                                                             │
                                              any subscriber (dashboards, alerts, agents)
```

**Rules:**
- `agentic_govkit` imports only from `agentic_loopkit`'s public API (`__init__.py`)
- `agentic_loopkit` has zero imports from `agentic_govkit`
- All inter-module communication is via published `Event` objects on the shared bus
- `AuditAgent` never subscribes to `governance.*` — prevents audit loops
- `KillSwitchAgent` never subscribes to `*` — reacts to decisions (governance.*), not raw events

---

## Trust levels (`agentic_loopkit.TrustLevel`)

| Level | Meaning | AuditAgent behaviour |
|---|---|---|
| `HIGH` | Fully trusted internal component | No flag |
| `MEDIUM` | Default — standard internal agent | No flag |
| `LOW` | External or partially-trusted source | No flag (informational) |
| `UNTRUSTED` | Known-hostile or unverified source | Triggers `governance.trust_escalation` |

## Delegation depth

`Event.delegation_depth` increments by 1 on every `event.caused(...)` call.
`AuditAgent` flags events where `delegation_depth > max_delegation_depth` (default: 5).
Use this to detect and cap runaway delegation chains before they exhaust context or budget.
