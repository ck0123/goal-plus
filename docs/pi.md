# Pi

Pi support is implemented inside this repository without changing Pi core. The
project ships Pi prompt templates, one user-facing `goal-plus` skill, and an
extension under `.pi/`, plus two Python console scripts used by that extension.

## Setup

Install this package in the Python environment that launches Pi:

```bash
python -m pip install -e ".[dev]"
```

Pi loads project-local `.pi/` resources after project trust. For RPC workers,
the runner loads the extension explicitly with `-e`, so candidate workspaces do
not need to contain `.pi/extensions`.

When Pi is launched from this checkout, the extension runs
`python -c ... agentic_any_search_mcp.pi_tool` with `src/` inserted into
`sys.path`. That keeps local development on the same `python` Pi inherited from
the shell, even before the console scripts are on `PATH`. Installed checkouts
can still use the console scripts directly.

## Main Agent

Use `/goal-plus ...` from Pi. When the project `.pi/` extension is loaded, this
is a native Pi command. The command calls:

```text
goal_plus_create(raw_goal=...)
```

before the model turn starts, activates that `goal_plus_id`, and sends the
model a follow-up prompt to continue the Goal Plus flow. Interactive sessions
also persist the active id in a Pi custom session entry. Print/JSON modes keep
the active id in extension memory for the process lifetime, so their turn-level
gate has the same runtime ownership without requiring a persistent Pi session.

Interactive TUI/RPC uses the native command handler. Print/JSON uses the
extension `input` event to pre-create the record and transform the slash input
into the first Goal Plus model prompt, including for
`pi -p "/goal-plus ..."`. This distinction is required because Pi
command-handler `sendUserMessage` is fire-and-forget while `runPrintMode` only
awaits its original `session.prompt`. Both paths create the record before the
model runs. The checked-in `.pi/prompts/goal-plus.md` remains a compatibility
fallback for environments that do not load the extension; only that fallback
depends on the model calling `goal_plus_create` first.

The Pi extension runs as `AGENTIC_ANY_SEARCH_PI_ROLE=main` by default and
exposes `goal_plus_*`, `search_*`, `pi_search_run_batch`, and
`pi_search_run_candidate`. The low-level `pi_rpc_run_worker` tool is hidden in
normal main-agent flow and is registered only when
`AGENTIC_ANY_SEARCH_PI_EXPOSE_LOW_LEVEL_WORKER=1` is set for manual debugging.
The extension restores the active Goal Plus state on session start, injects
hidden Goal Plus context before agent starts, and calls
`goal_plus_gate(event="pre_tool_use")` before main-role `search_*` tool calls,
explicitly exposed `pi_rpc_run_worker` debugging calls, and mutating built-ins
(`bash`, `edit`, `write`).

At turn end, the extension calls `goal_plus_gate(event="stop")`. If the gate
blocks, it queues the runtime continuation prompt and triggers another Pi turn.
This gives Pi a native turn-level stop gate. It is not a host process Stop hook
that can block closing Pi, but it uses the same runtime gate semantics as the
Codex and Claude Code Stop hooks.

If the last assistant message ends with host/model `error` or `aborted`, the
extension does not queue another stop-gate continuation. Print mode can then
surface the host error and exit nonzero instead of repeating the same failed
model request indefinitely.

When a Goal Plus record reaches a terminal status, the extension prints a
visible `Goal Plus stats` custom entry with elapsed time, assistant messages,
tool calls, token totals, and estimated cost calculated from Pi session usage.
The stats entry is persisted in Pi JSONL but is not injected as an LLM message,
so it does not trigger another assistant turn after completion.

## How Pi Differs From Other Hosts

Pi support has two surfaces in this project: a main-agent extension surface and
the `pi-rpc` worker host.

For the main agent, Codex and Claude Code enforce Goal Plus with project hook
files that wrap host lifecycle events. OpenCode currently relies on
instruction-driven skill calls. Pi uses extension events instead. The extension
registers native `/goal-plus` for interactive/RPC sessions and intercepts the
same slash input before prompt expansion in print/JSON sessions, pre-creates
the Goal Plus record before the model turn, restores active state
from Pi custom entries, injects hidden Goal Plus context, gates selected tool
calls through `tool_call`, and runs the final stop gate through `agent_end`.
Print/JSON invocations use in-memory active state because those process modes
do not need a persistent user session.

For workers, Pi RPC is a foreground `pi --mode rpc` process started by
`agentic-any-search-pi-worker`, not a host-managed background subagent. The
main agent still receives a normal Search Mode launch payload and binds the
returned handle with `search_bind_agent_handle`, but the runner owns the
process watchdog, metadata-only event log, optional raw debug log, and
`metadata.pi_metrics`. Workers run with `--no-session`; MCP Search state,
verifier iterations, candidate Git commits, and workspace files are the durable
recovery surface. At exit, the runner also stores a bounded
`metadata.progress_handoff` containing an optional model-authored
`.tmp/handoff.json` note plus deterministic Git and verifier snapshots.

Pi RPC has no same-worker continuation. The Pi main agent uses
`pi_search_run_candidate(..., redispatch=true)` for state-level redispatch; the
driver invokes `search_redispatch_candidate` and creates a fresh
`agent_session_id` in the same candidate workspace. The new worker receives
prior handoffs and current workspace state through `context.resume`.

For ecosystem compatibility, this implementation is project-local and does not
patch Pi core. External Pi packages can still register overlapping commands or
tools, so do not install another Pi goal package alongside this project unless
its semantics are intentionally compatible. A package that exposes an unrelated
`goal_complete` flow can confuse the model into ending Goal Plus outside the
runtime-owned state machine.

## Supported Strategies

Pi currently supports the portable builtin strategies only:

- `agent_guided`, `agent`, or `default`
- `random` or `random_mode`

OpenCode-specific or high-touch strategies such as `independent_branches`,
`openevolve`, `evolve`, `mcts`, Python strategy plugins, and external strategy
drivers remain OpenCode-only until they are adapted and tested for Pi RPC.

## Worker Host

Set the SearchSpec strategy to `worker_host="pi-rpc"`:

```json
{
  "strategy": {
    "name": "random",
    "driver": "builtin",
    "worker_mode": "agent-session-pool",
    "worker_host": "pi-rpc",
    "worker_budget": {
      "max_runtime_seconds": 600,
      "max_turns": 8,
      "on_exceed": "interrupt"
    }
  }
}
```

`worker_budget.max_runtime_seconds` is required. The Pi runner uses it as a hard
process watchdog. Before that deadline, the adapter derives a short closeout
window and the runner sends one `steer` request telling an active worker to stop
new iterations, run a final verifier if needed, and return. The hard abort still
applies if the worker does not exit. `max_turns` is only included as a prompt
hint. Monitor snapshots expose `soft_closeout_seconds`, `soft_closeout_sent`,
and `timed_out` separately.

## Runtime Flow

Pi does not expose a separate user-facing `search` skill. After `goal-plus`
opens Search Mode, the Pi main agent uses the same durable Search runtime as
other hosts:

1. `search_plan_next`
2. `search_start_batch`
3. `pi_search_run_batch(run_id, candidate_ids, directive?, final_verify=true, max_parallel=<budget.max_parallel>)`
4. inspect the returned per-candidate step evidence, handle metadata, and final score reports
5. `search_select`, `search_report`, `search_promote`

`pi_search_run_batch` runs the candidate workers concurrently up to the planned
`max_parallel` window and returns ordered per-candidate results.
`pi_search_run_candidate` is the single-candidate fallback for manual recovery
or debugging. Both drivers automatically start the agent session, run the Pi
RPC worker, bind the handle, and can run the final verifier. The returned
`steps` arrays record the exact tool chain:
`search_start_agent_session`, `pi_rpc_run_worker`,
`search_bind_agent_handle`, and `search_run_verifier` when final verification
is enabled. The main agent should not call the low-level worker tool in normal
Search Mode. To expose it for manual debugging, start Pi with
`AGENTIC_ANY_SEARCH_PI_EXPOSE_LOW_LEVEL_WORKER=1`.

The Pi main agent also does not directly call `search_start_agent_session`,
`search_bind_agent_handle`, or `search_continue_agent_session`; those mechanical
tools are omitted from its visible tool set. To retry an existing candidate,
call `pi_search_run_candidate(..., redispatch=true)`. The driver invokes
`search_redispatch_candidate` internally, launches the fresh stateless worker,
and returns that step in its evidence. Provider/model override fields are not
exposed in the main-agent driver schemas; select the worker model through the
trusted process environment or direct Python/CLI caller.

Before each verifier call, the runtime automatically commits changed candidate
artifact files in the candidate workspace. It records each iteration's real
`git_head`. Worker verifier results rank committed iterations, but they are not
the final authority. `search_select` checks out ranked commits and runs the
main-agent final verifier on each exact commit; the first commit that passes
that final verifier becomes the recorded selection and its final verifier score
becomes the selected score. A worker-side historical best may therefore be
skipped when final verification fails or times out.
`search_promote` then generates the patch from the selected commit.

The launch payload uses `tool="pi_rpc_worker"` and contains `root`, `cwd`,
`agent_session_id`, `candidate_id`, `prompt`, `session_id`, and
`budget_control`.

## RPC Runner

`agentic-any-search-pi-worker run` starts Pi in the candidate workspace:

```bash
pi --mode rpc --approve \
  --no-session \
  --session-id <agent_session_id> \
  -e <repo>/.pi/extensions/search-runtime.ts
```

The runner sets:

- `AGENTIC_ANY_SEARCH_ROOT=<abs .gp>`
- `AGENTIC_ANY_SEARCH_PI_ROLE=worker`

If `--model` is passed to `agentic-any-search-pi-worker run`, or
`AGENTIC_ANY_SEARCH_PI_MODEL` is set, the runner starts Pi with that model
pattern.

Worker-role extension tools are restricted to `search_get_agent_context`,
`search_run_verifier`, and `search_list_iterations`. After
`search_get_agent_context`, the extension applies a workspace guard to
`edit`, `write`, and `bash`.

Workers should produce a complete candidate artifact and run an early
`search_run_verifier` before spending time on local optimization loops. For
fix/target tasks, the edit comes before the first verifier call so workers do
not spend the whole budget verifying the unmodified starting point. This keeps
Search runtime iterations authoritative and prevents long worker-local searches
from timing out without a candidate artifact. Candidate workspaces are created
with an isolated git baseline, so worker-local `git status` and `git diff`
operate on the candidate workspace instead of an enclosing repository.

## Tool Facade

`agentic-any-search-pi-tool` is a JSON CLI facade for the Pi extension:

```bash
agentic-any-search-pi-tool search_get_agent_context \
  --root .gp \
  --args-json '{"agent_session_id":"agent_..."}'
```

It dispatches to the same `SearchTools` and `GoalPlusTools` Python facade used
by the MCP server. It also exposes `pi_search_run_batch` and
`pi_search_run_candidate`, Pi-native candidate drivers that wrap the mechanical
Search Mode worker chain while leaving planning, selection, reporting, and
promotion as explicit runtime steps.

The same facade exposes the generic read-only monitor tool:

```bash
agentic-any-search-pi-tool goal_plus_monitor_snapshot \
  --root .gp \
  --args-json '{"run_id":"run_...","stale_after_seconds":600}' \
  --pretty
```

`goal_plus_monitor_snapshot` is also registered on the MCP server, so it is not
Pi-specific. Pi runs get richer fields because `search_bind_agent_handle`
persists `metadata.pi_metrics`, including per-worker token, cost, duration, and
context usage data. The tool is read-only: it never starts, waits for, or stops
workers.

## State And Logs

Search MCP `.gp/runs/...`, candidate Git commits, and candidate workspace files
are the authoritative search and recovery state. Pi workers do not persist a
session transcript by default.

Pi worker logs are written under:

- `.gp/host-logs/pi-rpc-<agent_session_id>.jsonl`: metadata-only event log by
  default. It records event types, tool names/status, usage/counts, and bounded
  error summaries without prompts, reasoning, tool payloads, or transcripts.

Set `AGENTIC_ANY_SEARCH_PI_RAW_LOG=1` only for short, targeted debugging. It
retains streaming updates in the JSONL and also writes the duplicate raw
`.gp/host-logs/pi-rpc-<agent_session_id>.txt` stream. Raw mode can grow by
hundreds of MiB per worker because Pi updates may contain cumulative message
state.

Each completed `pi_rpc_run_worker` call also returns `metadata.pi_metrics`.
When the handle is passed to `search_bind_agent_handle`, those metrics are
persisted in `AgentSessionRecord.host_handle.metadata` with the rest of the Pi
handle. This is the place to read per-worker cost and timing data for later
reports, benchmark tables, or strategy analysis.

`metadata.pi_metrics` includes:

| Field | Meaning |
|---|---|
| `usage_delta` | Tokens and estimated cost for this runner invocation. Computed from in-memory Pi entries added after the pre-prompt baseline. |
| `usage_total` | Tokens and estimated cost visible in the worker process at completion. With stateless workers this is normally the current invocation total. |
| `duration_seconds` | Wall-clock runtime measured by `agentic-any-search-pi-worker`, including waiting for the Pi RPC worker to finish. |
| `session_file` | `null` for stateless workers; retained in the schema so older run records remain readable. |
| `baseline_entry_count`, `final_entry_count` | Entry boundaries used to compute `usage_delta`. |
| `session_stats` | Pi RPC `get_session_stats` output, kept as host-native context. |

The token and cost fields use the same persisted Pi assistant-message usage that
drives Pi's footer display. Cost is Pi's local estimate from model pricing, not
an external billing statement. If a worker is interrupted before Pi records
assistant usage for the request, the delta can be empty while `duration_seconds`
and timeout metadata still describe the run.

When a worker needs more time or another approach, the Pi main agent calls
`pi_search_run_candidate(..., redispatch=true)`. The driver calls
`search_redispatch_candidate`, which creates a new `agent_session_id` for
state-level redispatch in the same candidate workspace. The new worker starts
from `search_get_agent_context`, verifier history, and Git state rather than a
prior Pi transcript.

For a promising attempt that has no usable verifier evidence, the high-level
driver accepts `runtime_multiplier` only together with `redispatch=true`. The
value must be greater than 1 and at most 2, and scales only that launch's
`max_runtime_seconds`; the frozen SearchSpec remains unchanged.

A watchdog timeout and a runner failure are distinct. Timeout means the host
successfully enforced the configured deadline and returned a bindable handle;
runner failure means the runner could not return normally. If a timed-out
candidate already has a passing Git-backed iteration, the runtime keeps that
best iteration in planning and selection history. `search_list_history` exposes
the recoverable evidence as `score`, `best_iteration`, and `best_git_head`, and
keeps the final attempt separately as `latest_score` and
`latest_process_passed`. Redispatch is needed only when no useful passing
iteration exists or the main agent deliberately wants another exploration
attempt.

If the runner fails before returning a normal handle, the Pi driver binds a
synthetic failure handle to the agent session. Its metadata includes
`runner_failed`, `failure_stage`, `error_type`, and a bounded `error` summary,
so `goal_plus_monitor_snapshot` and the main agent see an explicit failed
session instead of a permanently running one.
