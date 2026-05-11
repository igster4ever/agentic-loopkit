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

All governance events are published by `AuditAgent` to the `governance` stream.
They are themselves persisted and inspectable — the auditor is auditable.

| Event type | Published by | Condition | Typical subscribers |
|---|---|---|---|
| `governance.audit_flagged` | `AuditAgent` | generic policy rule breach | human-review agents, alerting |
| `governance.depth_exceeded` | `AuditAgent` | `delegation_depth > max_delegation_depth` | kill-switch agents, alerting |
| `governance.trust_escalation` | `AuditAgent` | `trust_level == UNTRUSTED` | policy router, alerting |

---

## Module communication contract

```
agentic_loopkit  ──publishes──▶  EventBus  ──routes──▶  agentic_govkit (AuditAgent)
                                                              │
                                                 publishes governance.* back to bus
                                                              │
                                              any subscriber (dashboards, alerts, agents)
```

**Rules:**
- `agentic_govkit` imports only from `agentic_loopkit`'s public API (`__init__.py`)
- `agentic_loopkit` has zero imports from `agentic_govkit`
- All inter-module communication is via published `Event` objects on the shared bus
- `AuditAgent` never subscribes to `governance.*` — prevents audit loops

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
