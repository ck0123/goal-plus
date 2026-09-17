# Ultra Orchestration

`/goal-plus` uses one host-neutral execution contract:

```text
main reasoning=max + proactive delegation + automatic Search routing + until terminal
```

The Main agent owns task decomposition, delegation, waiting, follow-up, retry,
Search decisions, result review, and final integration. The host owns native
worker launch, wait, messaging, interruption, and logs. Goal Plus only records
the subagent operations that actually happen and protects their attempt
identity; it does not require an execution plan or work DAG.

## Subagent Records

The Main agent records `dispatch` immediately before using the host's native
spawn operation. The first dispatch for a `work_item_id` creates a lightweight
record from its summary:

```text
dispatch -> launching -> bind -> active -> result -> result_ready
```

`accepted` records that Main checked and used a result. `rework` records a
follow-up on the same bound worker and creates a new attempt/generation.
Neither decision is a global execution plan or a prerequisite for unrelated
work. A new dispatch may reuse an id after a terminal result, failure, or
cancellation.

`dispatch` creates the attempt identity and launch TTL; it never claims that a
worker started. Record `bind` only after native launch succeeds. `bind`,
`message`, `result`, and `failed` include the current `attempt_id` and
`generation`. An expired `launching` record may be dispatched again. Late
terminal output from an older generation is retained as `stale_result` evidence
and cannot mutate the current attempt.

Goal Plus allows Main-only tasks with no subagent records. Final check and
`complete` are blocked only while a current dispatch is `launching` or `active`;
Main remains responsible for reviewing returned work and running whole-task
verification.

## Search Routing

Search keeps its existing SearchSpec, run, candidate, verifier, and promotion
state. It is not represented as a subagent record. Main enters Search only when
the task has a numeric metric, deterministic verifier, isolated edit surface,
multiple worthwhile hypotheses, and sufficient budget.

## Host Contract

An Ultra host binding uses protocol `goal-plus-ultra-v1`. `spawn`, `wait`, and
`observe` are required; `send` and `interrupt` are optional native capabilities.
`main_reasoning_effort="max"` is logical. Each host records the native value and
who enforces it.

Codex uses native collaboration tools such as `spawn_agent`, `wait_agent`, and
`followup_task`. Pi maps logical `max` to its native `xhigh` request and exposes
`pi_goal_plus_run_work_item`, which accepts the task directly and runs an
isolated Pi RPC child. A DeepSeek or other harness binds the same host profile,
uses its own worker API, and reports the same lightweight lifecycle events.

The optional `orchestration` monitor is a read-only projection of these records
and events. It never launches, waits for, messages, retries, or supervises a
worker. See [Monitor Feature Plugins](feature-plugins.md).
