# Pi

Pi support is implemented inside this repository without changing Pi core. The
project ships Pi prompt templates, skills, and an extension under `.pi/`, plus
two Python console scripts used by that extension.

## Setup

Install this package in the environment that launches Pi:

```bash
pip install -e .
```

Pi loads project-local `.pi/` resources after project trust. For RPC workers,
the runner loads the extension explicitly with `-e`, so candidate workspaces do
not need to contain `.pi/extensions`.

## Main Agent

Use `/goal-plus ...` from Pi. The prompt template requires the first tool call
to be:

```text
goal_plus_create(raw_goal=...)
```

The Pi extension runs as `AGENTIC_ANY_SEARCH_PI_ROLE=main` by default and
exposes `goal_plus_*`, `search_*`, and `pi_rpc_run_worker`. Before main-role
`search_*` tool calls, it attempts `goal_plus_gate(event="pre_tool_use")` when
`AGENTIC_ANY_SEARCH_GOAL_PLUS_ID` is set.

Pi has no Codex Stop hook parity. The v1 boundary is extension pre-tool guard,
skill-level stop gate instructions, and runtime state audit.

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

Same-worker continuation means `session_jsonl_restart`: the runner starts a new
Pi RPC process with the same `--session-id`. It is not a live stdin
continuation. `search_redispatch_candidate` still creates a new
`agent_session_id` for state-level resume.
