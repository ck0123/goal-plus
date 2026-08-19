# Ultra Orchestration

`/goal-plus` uses one host-neutral execution contract:

```text
main reasoning=max + proactive delegation + automatic Search routing + until terminal
```

The runtime owns the work DAG, evidence-backed transitions, Search links, and
completion gate. The host owns process/thread launch, wait, messaging,
interruption, and native logs. Core code never calls Codex, Pi, or a model
provider directly.

## Work Items

After triage, call `goal_plus_upsert_work_items`. Routes mean:

| Route | Owner |
|---|---|
| `main` | shared state, architecture, integration, final verification |
| `subagent` | bounded independent work dispatched by the current host |
| `search` | verifier-guided exploration through existing Search Mode |

The subagent transition sequence is
`planned -> launching(attempt, generation, TTL) -> active(bound handle) -> result_ready -> accepted`.
`rework` resumes a bound `result_ready` attempt; unbound blocked or failed work
returns to `planned` for a fresh attempt.
Dependencies must be accepted before dispatch. Required work that is not
accepted blocks final review and `complete`.

`dispatch` creates the attempt identity; it never binds a worker handle. The
host records `bind` only after native launch succeeds and includes the current
attempt and generation on worker messages, failures, and results. A host may
re-dispatch an expired `launching` item to reconcile a launch crash. Late
terminal output from an older generation is retained as `stale_result`
evidence but cannot mutate the current item. Repeating an identical result for
the current attempt is idempotent; a conflicting duplicate is rejected.

Use Search only when a work item has a numeric metric, deterministic verifier,
isolated edit surface, multiple worthwhile hypotheses, and sufficient budget.
After creating and linking the run, record `search_routed`; a selected Search
result still requires main-agent acceptance.

## Host Contract

An Ultra host binding uses protocol `goal-plus-ultra-v1`. `spawn`, `wait`, and
`observe` are required; `send` and `interrupt` are optional native
capabilities. `main_reasoning_effort="max"` is logical. Each host records the
native value and who enforces it.

Codex binds `native_reasoning_effort="max"`. Project hooks cannot change an
already-started first turn, so the host must select max before starting it.
Codex uses native collaboration tools for ordinary subagents.

Pi maps logical `max` to its native `xhigh` request for Main and ordinary
children, records the model-clamped level, and rejects a resulting `off` level.
Its `pi_goal_plus_run_work_item` tool runs an isolated Pi RPC child and returns
its result to Main; concurrent tool calls provide independent parallel lanes.

A DeepSeek or other harness supplies the same profile at creation time:

```json
{
  "execution": {
    "host": {
      "protocol": "goal-plus-ultra-v1",
      "host_id": "deepseek-harness",
      "native_reasoning_effort": "highest",
      "enforcement": "harness",
      "operations": ["spawn", "wait", "observe"]
    }
  }
}
```

The harness must set its highest supported reasoning budget before the first
main turn, dispatch work items through its worker API, and report lifecycle
transitions through `goal_plus_record_work_event`. Provider names and command
schemas remain outside the core state machine.

Main/worker interaction monitoring is an optional read-only projection, not an
Ultra lifecycle stage. Hosts may annotate work events with namespaced native
operation metadata; the `orchestration` feature plugin then exposes a common
task/result/decision view without moving process ownership into the runtime.
See [Monitor Feature Plugins](feature-plugins.md).
