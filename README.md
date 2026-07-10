# Agentic Any Search MCP

`agentic-any-search-mcp` is the MCP runtime behind `/goal-plus`.

`/goal-plus` is the default user-facing workflow. It behaves like an ordinary
goal for normal coding, docs, review, and investigation tasks. When the task is
measurable optimization, it can upgrade into Search Mode: freeze the verifier
and metric, create isolated candidate workspaces, launch host-native workers,
score candidates with runtime-owned checks, select the best result, write a
report, and export a promotion patch.

The project is not OpenCode-only. Current checked-in host assets target:

- Codex
- Claude Code
- OpenCode
- Pi

The MCP runtime stays host-neutral. Host-specific behavior is in the checked-in
host configs, skills, hooks, and worker-agent prompts.

## Install

Install the Python package so the `agentic-any-search-mcp` command is on
`PATH`.

From Git:

```bash
python -m pip install --user "git+https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git"
agentic-any-search-mcp --help
```

From an existing checkout:

```bash
cd agentic-any-search-mcp
python -m pip install -e ".[dev]"
agentic-any-search-mcp --help
```

If the command is not found after a user-level install, add the Python user
scripts directory to `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

macOS framework Python may use:

```bash
export PATH="$HOME/Library/Python/3.10/bin:$PATH"
```

This package is not published to PyPI yet, so use the Git or editable install
path.

## Configure A Host

The runtime is a stdio MCP server. All hosts should launch the same command:

```text
agentic-any-search-mcp --root .gp
```

This repository already includes project-local config for all supported hosts.

| Host | Project config | User entrypoint | Notes |
|---|---|---|---|
| Codex | `.codex/config.example.toml`, `.codex/hooks.json`, `.codex/skills/` | Copy the example to the ignored local `.codex/config.toml`, then use the `goal-plus` skill / `/goal-plus` prompt | Ships `PostToolUse(goal_plus_create)` session binding and a session-scoped `Stop` hook. Review/trust project hooks when Codex asks. |
| Claude Code | `.mcp.json`, `.claude/settings.json`, `.claude/skills/`, `.claude/agents/` | Use the `goal-plus` skill / `/goal-plus` prompt from Claude Code | Ships `PostToolUse(goal_plus_create)` session binding and a session-scoped `Stop` hook. |
| OpenCode | `opencode.json`, `.opencode/command/goal-plus.md`, `.opencode/skills/`, `.opencode/agents/` | `/goal-plus` in the TUI, or `opencode run --command goal-plus "<prompt>"` | OpenCode is the compatibility baseline for older Search Mode strategies, but Goal Plus gates are instruction-driven because no OpenCode hook is shipped. |
| Pi | `.pi/prompts/`, `.pi/skills/goal-plus/`, `.pi/extensions/search-runtime.ts` | `/goal-plus` in interactive Pi or `pi -p "/goal-plus ..."` | The extension pre-creates Goal Plus before the model: a native command in TUI/RPC and an input transform in print/JSON. Pi RPC workers run statelessly through `agentic-any-search-pi-worker`; stats are Pi custom entries, not LLM messages. |

Host-specific setup and debugging details live in:

- [Codex reference](docs/codex.md)
- [Claude Code reference](docs/claude-code.md)
- [OpenCode reference](docs/opencode.md)
- [Pi reference](docs/pi.md)
- [Host adapter capability matrix](docs/agent-host-adapters.md)
- [Runtime and host log debugging](docs/debugging-runtime.md)

## Run `/goal-plus`

Use `/goal-plus` for both ordinary goals and optimization-shaped goals. The
workflow starts with `goal_plus_create`, records triage, and only enters Search
Mode after the goal has a verifier-backed spec.

Examples:

```text
Use /goal-plus. Fix this bug and verify the test suite.
```

```text
Use /goal-plus. Optimize this model-serving path for lower p95 latency. First
identify the benchmark, correctness gate, editable files, and promotion rule.
If the verifier is frozen and search-ready, run Search Mode with Codex workers.
```

OpenCode also keeps `/goal-any-optimize` as a legacy alias, but `/goal-plus` is
the canonical entrypoint.

## Search Mode Flow

After `/goal-plus` upgrades a task to Search Mode, the main agent drives this
common MCP flow:

1. `search_freeze_spec`
2. `search_create`
3. `search_plan_next`
4. `search_start_batch`
5. `search_start_agent_session`
6. launch the returned foreground worker in Codex, Claude Code, OpenCode, or Pi RPC
7. bind the host handle with `search_bind_agent_handle` or
   `search_bind_opencode_session`
8. worker calls `search_get_agent_context`
9. worker self-scores with `search_run_verifier(..., agent_session_id=...)`
10. main agent confirms the final score, selects, reports, and optionally
    promotes

The runtime owns `.gp/` state, candidate workspaces, verifier scoring,
history, reports, and promotion artifacts. The host owns worker launch,
interrupts, step/turn/time limits, foreground returns, and native transcripts.
There are no MCP wait, abort, submit, observe, or host-sync tools.

## Task Continuation And Resume

There are two different continuation concepts:

| Concept | Portable? | What it does |
|---|---|---|
| State-level resume with `search_redispatch_candidate` | yes, all hosts | Creates a fresh `agent_session_id` for the same candidate workspace. The new worker reads `search_get_agent_context`, including explicit prior-session handoffs, current Git state, runtime history, and previous iterations. It can override `worker_agent_type` or `worker_budget` for that dispatch. |
| Same-worker continuation with `search_continue_agent_session` | host-specific | Reuses a prior host worker/session when the host exposes a reliable handle. OpenCode supports this with `Task(task_id=...)`; Claude Code is conditional through `SendMessage`; Codex and Pi RPC are explicitly unsupported in these adapters. |

Default to state-level resume when a worker hits a step/turn/time cap, returns
without useful verifier evidence, or needs a larger worker tier. Same-worker
continuation is an optimization, not the portable recovery model.

Search history is runtime-owned under `.gp/runs/...`; it is not stored in a
`plan.md` file. See [agent-host-adapters.md](docs/agent-host-adapters.md) for
the detailed resume and continuation matrix.

## Strategies

The portable strategy subset for Codex, Claude Code, and Pi RPC is:

- `agent_guided`
- `agent`
- `default`
- `random`
- `random_mode`

OpenCode remains the compatibility baseline for existing OpenCode-tested
strategies such as `independent_branches`, `evolve`, `openevolve`, `mcts`,
Python strategy plugins, and trace export. See
[examples/README.md](examples/README.md),
[docs/strategy-openevolve.md](docs/strategy-openevolve.md), and
[docs/strategy-adaptevolve.md](docs/strategy-adaptevolve.md).

## Repository Layout

```text
opencode.json                         # project-local OpenCode MCP config
.mcp.json                             # project-local Claude Code MCP config
.codex/config.example.toml            # tracked Codex MCP config template
.codex/config.toml                    # ignored local Codex MCP config
.codex/hooks.json                     # Codex Goal Plus host hooks
.pi/                                  # Pi prompts, skills, and extension
scripts/hooks/goal_plus_stop.py       # legacy wrapper for local hook testing
.opencode/                            # OpenCode commands, skills, worker agents
.codex/skills/                        # Codex skills
.codex/agents/                        # Codex worker agent config
.claude/                              # Claude Code settings, skills, worker agents
docs/                                 # design, host, debug, and strategy docs
examples/                             # bundled SearchSpec examples
src/agentic_any_search_mcp/           # runtime, models, tools, server
tests/                                # unit, integration, asset, and opt-in ST tests
```

## Development Checks

```bash
python -m pytest -q
git diff --check
```

Runtime state is written under `.gp/`, which is ignored by git.
