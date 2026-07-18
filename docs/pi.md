# Pi

Pi support is project-local: one user-facing `goal-plus` skill under
`.pi/skills/goal-plus/`, one extension, and Python worker/facade commands. It
does not patch Pi core and does not expose a separate user-facing `search` skill.

## Setup

```bash
python -m pip install -e ".[dev]"
pi -p "/goal-plus inspect this repository"
```

The extension provides pre-model `/goal-plus` creation in interactive,
RPC, print, and JSON modes. It persists the active id when the Pi session is
persistent, injects hidden context, gates selected writes/Search calls, and
runs a native turn-level stop gate. This is no host process Stop hook.

`/goal-plus edit`, `/goal-plus resume`, and `/goal-plus-with-final-check` share
the same goal revision semantics as Codex. Required checks run through a
separate read-only Pi RPC reviewer.

Use `/goal-plus mode=autonomous <goal>` for substantial renewable candidate
exploration (the default), or `/goal-plus mode=probe <goal>` for short
feasibility/potential/blocker probes. The choice is normalized into the final
line of `raw_goal`; it is not a Pi pool or Search runtime state.

At the end of a main turn, every still-active record is continued with its full
raw goal and elapsed-time context. Pi stops only after the agent records a
terminal status. Worker watchdog expiry remains a dispatch event, not goal
completion.

At terminal state, Pi writes a visible `Goal Plus stats` custom entry with
elapsed time, messages, tool calls, token use, and estimated cost. It is not an
LLM message and does not trigger another assistant turn.

## How Pi Differs From Other Hosts

The main agent uses extension events rather than project hook files. Candidate
workers are stateless Pi RPC processes supervised by a durable host-local pool.
Each detached wrapper owns one foreground `pi --mode rpc` child launched by
`goal-plus-pi-worker` with `--no-session`.

Pool state lives under `.gp/host-pools/pi/`; Search records remain host-neutral.
Pi has no same-worker continuation. `pi_search_pool_continue` performs
state-level redispatch into the same candidate workspace with a fresh
`agent_session_id`.

## Worker Spec

Use `worker_host="pi-rpc"` and a wall-clock budget:

```json
{
  "strategy": {
    "name": "random",
    "orchestration_mode": "parallel_loops",
    "worker_host": "pi-rpc",
    "worker_budget": {
      "max_runtime_seconds": 600,
      "max_turns": 8,
      "on_exceed": "interrupt"
    }
  }
}
```

`max_runtime_seconds` is required and enforced by the Pi process watchdog.
Before the hard limit, the runner sends one closeout steer. `max_turns` is only
a prompt hint. A separate informational advisory may fire after a worker tool
completion when observed verifier time no longer fits the remaining window.

## Parallel Loops

Normal Pi Search follows the shared [Flow](flow-view.md):

1. set `orchestration_mode="parallel_loops"`, then plan and materialize the
   initial candidates exactly once;
2. `pi_search_pool_open(..., max_parallel=<frozen limit>)`;
3. `pi_search_pool_wait_any` for the first candidate-ready event;
4. observe any verifier-backed best update and call
   `pi_search_pool_continue` for that exact candidate unless a global stop
   condition is true;
5. recover interrupted main turns with `pi_search_pool_snapshot(run_id=...)`;
6. `pi_search_pool_close`, then select, report, and promote.

The supervisor enforces `max_parallel` and never auto-refills. Main never calls
submit after initial pool creation and never replaces a candidate because of
low score or lack of improvement. A terminal event
is published only after the driver has completed the worker, bound its handle,
and run final verification.

`pi_search_run_candidate` and `pi_search_run_batch` remain synchronous
compatibility helpers. They automatically start the agent session, run the Pi
RPC worker, bind the handle, and can run the final verifier. Low-level
`pi_rpc_run_worker` is hidden unless
`GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER=1` is set.

## Worker Boundary

Worker-role extension tools are limited to `search_get_agent_context`,
`search_run_verifier`, and `search_list_iterations`. The worker operates only
inside the returned workspace, creates an early real artifact, runs verifier
iterations, and writes a bounded `.tmp/handoff.json`.

The handoff plus candidate Git state and `.gp` verifier history are the recovery
surface. Pi workers do not need a persisted chat session.

Every redispatched worker owns the next hypothesis, pivot, and rebase within
the same candidate workspace. Main sends a neutral continuation directive and
does not act as a technical conductor.

## Tool Facade

`goal-plus-pi-tool` exposes the same GoalPlusTools/SearchTools facade plus the
Pi-local pool tools:

```bash
goal-plus-pi-tool goal_plus_monitor_snapshot \
  --root .gp \
  --args-json '{"run_id":"run_..."}' \
  --pretty
```

`goal_plus_monitor_snapshot` is read-only and also exists on MCP. It never
starts, waits for, or stops a worker. The complete concise tool index is in
[API](api.md).

Use `search_get_agent_observability(agent_session_id)` for the same normalized
per-worker schema used by Codex. Pi maps the existing `pi_metrics` model,
thinking level, duration, usage/cost, context, and log/session paths into that
schema; the legacy bound fields remain readable.

## State And Logs

Search state, candidate commits, and workspaces under `.gp/` are authoritative.
Worker logs default to a metadata-only event log:

```text
.gp/host-logs/pi-rpc-<agent_session_id>.jsonl
```

It stores event/tool status, bounded errors, timing, and usage without prompts
or reasoning. Set `GOAL_PLUS_PI_RAW_LOG=1` only for focused debugging; raw
streams can become very large.

Bound handles include `metadata.pi_metrics` (including resolved model and
thinking level), timeout/failure evidence, and a
bounded `metadata.progress_handoff`. A timeout is successful deadline
enforcement; runner failure is recorded separately with synthetic failure
metadata so monitoring never mistakes it for a live session.

## Supported Strategies

Pi currently supports the portable builtin strategies only:

- `agent_guided`, `agent`, `default`
- `random`, `random_mode`

## Verification

```bash
pytest -m pi -q
ST_PI_CYCLE_WORKER_SECONDS=120 \
  pytest -m "st and st_pi_rpc" -k managed_pool_wait_any -v -s -rs
```

The real-host test launches two detached Pi RPC workers, rediscovers the pool
by `run_id`, observes wait-any completion, and drains cleanly. See
[Debugging](debugging-runtime.md) for cross-host diagnosis.
