# Pi

Pi support is implemented inside this repository without changing Pi core. The
project ships Pi prompt templates, skills, and an extension under `.pi/`, plus
two Python console scripts used by that extension.

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

before the model turn starts, stores the active `goal_plus_id` in a Pi custom
session entry, and sends the model a follow-up prompt to continue the Goal Plus
flow. The prompt template remains as a compatibility path; in that path the
first model tool call still must be `goal_plus_create(raw_goal=...)`.

In `pi -p` print mode, Pi's prompt-template expansion is the reliable command
path. The extension intentionally does not register the native `/goal-plus`
command for `-p/--print` invocations, so the checked-in
`.pi/prompts/goal-plus.md` template handles the slash command and asks the model
to call `goal_plus_create` first. Interactive TUI/RPC sessions keep the native
pre-create path.

The Pi extension runs as `AGENTIC_ANY_SEARCH_PI_ROLE=main` by default and
exposes `goal_plus_*`, `search_*`, and `pi_rpc_run_worker`. It restores the
active Goal Plus state on session start, injects hidden Goal Plus context before
agent starts, and calls `goal_plus_gate(event="pre_tool_use")` before main-role
`search_*` tool calls, `pi_rpc_run_worker`, and mutating built-ins (`bash`,
`edit`, `write`).

At turn end, the extension calls `goal_plus_gate(event="stop")`. If the gate
blocks, it queues the runtime continuation prompt and triggers another Pi turn.
This gives Pi a native turn-level stop gate. It is not a host process Stop hook
that can block closing Pi, but it uses the same runtime gate semantics as the
Codex and Claude Code Stop hooks.

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
registers the native `/goal-plus` command for interactive/RPC sessions,
pre-creates the Goal Plus record before the model turn, restores active state
from Pi custom entries, injects hidden Goal Plus context, gates selected tool
calls through `tool_call`, and runs the final stop gate through `agent_end`.
`pi -p` is the exception: print mode uses the `.pi/prompts/goal-plus.md`
compatibility prompt and asks the model to call `goal_plus_create` first.

For workers, Pi RPC is a foreground `pi --mode rpc` process started by
`agentic-any-search-pi-worker`, not a host-managed background subagent. The
main agent still receives a normal Search Mode launch payload and binds the
returned handle with `search_bind_agent_handle`, but the runner owns the
process watchdog, Pi session directory, event log, text log, and
`metadata.pi_metrics`.

For continuation, Pi can restart the same Pi JSONL session with the same
`--session-id`. That is `session_jsonl_restart`, not a live stdin
continuation. The portable recovery path is still `search_redispatch_candidate`
with a fresh `agent_session_id`.

For ecosystem compatibility, this implementation is project-local and does not
patch Pi core. External Pi packages can still register overlapping commands or
tools, so do not install another Pi goal package alongside this project unless
its semantics are intentionally compatible. A package that exposes an unrelated
`goal_complete` flow can confuse the model into ending Goal Plus outside the
runtime-owned state machine.

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
process watchdog. `max_turns` is only included as a prompt hint.

## Runtime Flow

Pi Search Mode uses the same durable Search runtime as other hosts:

1. `search_plan_next`
2. `search_start_batch`
3. `search_start_agent_session`
4. `pi_rpc_run_worker(launch=session.launch)`
5. `search_bind_agent_handle(agent_session_id, handle)`
6. final `search_run_verifier` from the main agent
7. `search_select`, `search_report`, `search_promote`

The launch payload uses `tool="pi_rpc_worker"` and contains `root`, `cwd`,
`agent_session_id`, `candidate_id`, `prompt`, `session_id`, and
`budget_control`.

## RPC Runner

`agentic-any-search-pi-worker run` starts Pi in the candidate workspace:

```bash
pi --mode rpc --approve \
  --session-dir .search/host-logs/pi-rpc-sessions \
  --session-id <agent_session_id> \
  -e <repo>/.pi/extensions/search-runtime.ts
```

The runner sets:

- `AGENTIC_ANY_SEARCH_ROOT=<abs .search>`
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
  --root .search \
  --args-json '{"agent_session_id":"agent_..."}'
```

It dispatches to the same `SearchTools` and `GoalPlusTools` Python facade used
by the MCP server.

## State And Logs

Search MCP `.search/runs/...` is the authoritative search state. Pi JSONL
session state is a transcript/resume surface only.

Pi worker logs are written under:

- `.search/host-logs/pi-rpc-<agent_session_id>.jsonl`
- `.search/host-logs/pi-rpc-<agent_session_id>.txt`
- `.search/host-logs/pi-rpc-sessions/`

Each completed `pi_rpc_run_worker` call also returns `metadata.pi_metrics`.
When the handle is passed to `search_bind_agent_handle`, those metrics are
persisted in `AgentSessionRecord.host_handle.metadata` with the rest of the Pi
handle. This is the place to read per-worker cost and timing data for later
reports, benchmark tables, or strategy analysis.

`metadata.pi_metrics` includes:

| Field | Meaning |
|---|---|
| `usage_delta` | Tokens and estimated cost for this runner invocation only. Computed from Pi session entries added after the pre-prompt baseline. |
| `usage_total` | Tokens and estimated cost for the whole Pi JSONL session. Useful for continued sessions and rough historical accounting. |
| `duration_seconds` | Wall-clock runtime measured by `agentic-any-search-pi-worker`, including waiting for the Pi RPC worker to finish. |
| `session_file` | Pi JSONL session file used for transcript/resume and offline inspection. |
| `baseline_entry_count`, `final_entry_count` | Entry boundaries used to compute `usage_delta`. |
| `session_stats` | Pi RPC `get_session_stats` output, kept as host-native context. |

The token and cost fields use the same persisted Pi assistant-message usage that
drives Pi's footer display. Cost is Pi's local estimate from model pricing, not
an external billing statement. If a worker is interrupted before Pi records
assistant usage for the request, the delta can be empty while `duration_seconds`
and timeout metadata still describe the run.

Same-worker continuation means `session_jsonl_restart`: the runner starts a new
Pi RPC process with the same `--session-id`. It is not a live stdin
continuation. `search_redispatch_candidate` still creates a new
`agent_session_id` for state-level resume.
