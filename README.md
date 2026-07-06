# Agentic Any Search MCP

`agentic-any-search-mcp` is a small MCP-first runtime for `/goal-plus`: a
goal-like agent entrypoint that can upgrade measurable optimization work into
verifiable multi-candidate search.

The goal of V0 is not to control one specific coding agent. The runtime exposes
a generic MCP control plane, while the host agent uses `/goal-plus` as the
user-facing workflow. Ordinary tasks stay goal-like. Search-shaped tasks freeze
the verifier and metric, ask the active strategy to plan the next batch, create
isolated candidate workspaces, verify candidates through runtime-owned checks,
select the best candidate, and export a promotion patch.

Strategies are run-level settings. The default is `agent_guided`: the runtime exposes the official candidate history and the main agent authors the next batch by picking parents and writing one proposal per slot. Built-in alternatives include `independent_branches` (no lineage), `evolve` (runtime picks best-score parent + inspirations), `openevolve` (OpenEvolve-style parent/archive/inspiration sampling), `mcts` (best-score frontier expansion), and `random` (random verified parent). Custom strategies can enter through a local Python `module:Class` planner or through the standard external proposal contract; the bundled `adaptevolve` Python planner adds evolve-style parent selection plus dynamic worker-tier routing. See `examples/README.md` for the full strategy comparison table, `docs/strategy-adaptevolve.md` for the AdaptEvolve code path, and `docs/strategy-openevolve.md` for the OpenEvolve path.

Candidate execution always runs through `strategy.worker_mode: agent-session-pool`. The runtime creates an `AgentSessionRecord` and returns a host-native launch payload; the main agent dispatches one foreground worker in OpenCode, Codex, or Claude Code. The host owns worker lifecycle and return values. The runtime owns candidate workspaces, verifier scoring, history, reports, and promotion patches. `budget.max_parallel` is a batch planning hint; the runtime does not provide a wait loop or lifecycle supervisor. `strategy.worker_host` selects the adapter, and `strategy.worker_agent_type` gives that host its default worker type. The launch payload from `search_start_agent_session` is authoritative. See [docs/agent-host-adapters.md](docs/agent-host-adapters.md) for the adapter design and current host differences.

## Getting Started

`agentic-any-search-mcp` is a stdio MCP server. Install the Python package so the
`agentic-any-search-mcp` command is on your `PATH`, then point your MCP client at
that command.

Standard MCP client config:

```json
{
  "mcpServers": {
    "search-runtime": {
      "command": "agentic-any-search-mcp",
      "args": ["--root", ".search"]
    }
  }
}
```

Install from Git when you do not already have the repository checked out:

```bash
python -m pip install --user "git+https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git"
agentic-any-search-mcp --help
```

If `agentic-any-search-mcp` is not found after installation, add the Python user
scripts directory to `PATH`. Common locations:

```bash
# macOS/Linux
export PATH="$HOME/.local/bin:$PATH"

# macOS framework Python sometimes uses:
export PATH="$HOME/Library/Python/3.11/bin:$PATH"
```

Install from an existing checkout for local development:

```bash
cd agentic-any-search-mcp
python -m pip install -e .
agentic-any-search-mcp --help
```

For an installed package, use the same command in your MCP client:

```text
agentic-any-search-mcp --root .search
```

## Agent Hosts

The runtime currently supports three host clients through adapters:

| Host | `strategy.worker_host` | Worker launch | Continuation | Goal Plus gate enforcement | Strategy scope |
|---|---|---|---|---|---|
| OpenCode | `opencode` | foreground `Task` | `Task(task_id=...)` | manual / instruction-driven | compatibility baseline |
| Codex | `codex` | foreground `spawn_agent` | not supported by this adapter | manual unless external hooks are wired | portable builtin modes |
| Claude Code | `claude-code` | foreground `Agent`, `background: false` | `SendMessage` when a handle is bound | manual unless external hooks are wired | portable builtin modes |

Portable builtin modes for Codex and Claude Code are `agent_guided`, `agent`,
`default`, `random`, and `random_mode`. OpenCode remains the baseline for existing
OpenCode-tested strategies and trace export.

Goal Plus has two support levels:

- **Search Mode orchestration**: host assets can launch candidate workers,
  verify scores, bind handles, select, report, and promote. This is implemented
  for the hosts above.
- **Lifecycle gate enforcement**: host hooks automatically call
  `goal_plus_gate` before Search Mode tools and before the agent stops. This
  repository does not currently ship OpenCode, Codex, or Claude Code hook
  wiring. Until a host hook adapter is added, `goal_plus_gate` is called by the
  skill/orchestrator instructions and is best-effort rather than enforced.

Host references:

- [OpenCode](docs/opencode.md)
- [Codex](docs/codex.md)
- [Claude Code](docs/claude-code.md)
- [Adapter design and host differences](docs/agent-host-adapters.md)
- [Runtime and host log debugging](docs/debugging-runtime.md)

## OpenCode

Assumption: OpenCode is already installed and has model credentials configured.

This repository ships project-local OpenCode assets:

```text
opencode.json
.opencode/command/goal-plus.md
.opencode/command/goal-any-optimize.md        # legacy alias to goal-plus
.opencode/skills/goal-plus/SKILL.md
.opencode/skills/search/SKILL.md              # internal Search Mode engine
.opencode/agents/goal-plus-orchestrator.md
.opencode/agents/search-orchestrator.md       # internal Search Mode dispatcher
.opencode/agents/AnySearchAgent*.md
```

The MCP server entry in `opencode.json` uses the installed console script:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "search-runtime": {
      "type": "local",
      "command": ["agentic-any-search-mcp", "--root", ".search"],
      "cwd": ".",
      "timeout": 300000,
      "enabled": true
    }
  }
}
```

Verify the connection from the project root:

```bash
opencode mcp list
```

Expected entry:

```text
search-runtime connected
agentic-any-search-mcp --root .search
```

Then run the toy search from the OpenCode TUI:

```bash
opencode
```

Inside OpenCode:

```text
Use /goal-plus. Load examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Show and confirm that frozen verifier, metric, edit surface, and promotion rule before Search Mode. Then run the k_module smoke test end-to-end.
```

For a headless command-line run:

```bash
opencode run --command goal-plus "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. This prompt explicitly confirms the frozen verifier, metric, edit surface, and promotion rule. Keep all edits inside candidate workspaces."
```

OpenCode `Task` does not currently expose a `timeout` parameter; subagents run until their OpenCode step cap hits or the user interrupts them. The MCP runtime does not provide wait or abort tools.

OpenCode currently has no project hook that automatically calls
`goal_plus_gate` on `Stop` or `PreToolUse`. `/goal-plus` works as an
instruction-driven command, but final raw-goal audit and phase gates are not
strongly enforced by OpenCode itself.

See [docs/toy-example.md](docs/toy-example.md) for the complete step-by-step flow and expected artifacts.

Additional bundled specs are listed in [examples/README.md](examples/README.md), including a `circle_packing` fork-style continuation smoke test and multi-batch `circle_packing` / `signal_processing` scenarios.

## Installation Notes

This project is structured like a normal Python command-line MCP package:

- `pyproject.toml` declares the package, dependencies, and the
  `agentic-any-search-mcp` console script.
- `src/agentic_any_search_mcp/server.py` owns FastMCP stdio startup.
- `src/agentic_any_search_mcp/tools.py` exposes JSON-friendly tool methods.
- `src/agentic_any_search_mcp/runtime.py` owns file-backed runtime state.
- `src/agentic_any_search_mcp/models.py` defines the Pydantic API models.

The current repository-local host setup is for development and examples.
This package is not published to PyPI yet; use the Git or editable install
commands below.

### Python Package Install Scope

User-level install from Git, recommended when you want the command available
across projects:

```bash
python -m pip install --user "git+https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git"
```

Project or directory-local development install from a clone:

```bash
git clone https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git
cd agentic-any-search-mcp
python -m pip install -e ".[dev]"
```

Machine-wide installs should be managed by the administrator's Python tooling.
The only requirement is that `agentic-any-search-mcp` is available on every
target user's `PATH`. Avoid relying on `PYTHONPATH` for normal users.

PyPI install commands should not be documented until the package is actually
published.

### OpenCode Config Scope

Project-level config, recommended for this repository:

```text
opencode.json
```

User/global OpenCode config:

```text
~/.config/opencode/opencode.json
```

Custom config file for one-off runs:

```bash
OPENCODE_CONFIG=/path/to/opencode.json opencode
```

Admin-managed config locations are supported by OpenCode for organization-wide
defaults, but this project does not ship managed config.

All scopes should call the same installed command:

```json
{
  "mcp": {
    "search-runtime": {
      "type": "local",
      "command": ["agentic-any-search-mcp", "--root", ".search"],
      "enabled": true
    }
  }
}
```

### Updating

Git install through `pip --user`:

```bash
python -m pip install --user -U "git+https://gitcode.com/yiyanzhi_akane1/agentic-any-search-mcp.git"
```

Editable development install:

```bash
cd agentic-any-search-mcp
git pull
python -m pip install -e ".[dev]"
python -m pytest -q
opencode mcp list
```

Not yet covered by this prototype:

- PyPI release metadata and install badges
- one-click installer links for individual MCP clients
- generated CLI option tables
- Docker image and remote transport examples

## Repository Layout

```text
opencode.json                         # project-local OpenCode MCP config
.mcp.json                             # project-local Claude Code MCP config
.codex/config.toml                    # project-local Codex MCP config
.opencode/
  command/goal-plus.md                # canonical OpenCode goal entrypoint
  command/goal-any-optimize.md        # legacy alias to goal-plus
  skills/goal-plus/SKILL.md           # goal-plus workflow guide
  skills/search/SKILL.md              # internal Search Mode workflow guide
  agents/goal-plus-orchestrator.md    # canonical host-agent prompt
  agents/search-orchestrator.md       # internal Search Mode dispatcher
  agents/AnySearchAgent.md            # candidate worker subagent prompt
.agents/
  skills/goal-plus/SKILL.md           # Codex goal-plus skill
  skills/search/SKILL.md              # Codex internal search skill
.codex/
  agents/any_search_agent.toml        # Codex worker agent config
.claude/
  skills/goal-plus/SKILL.md           # Claude Code goal-plus skill
  skills/search/SKILL.md              # Claude Code internal search skill
  agents/any-search-agent.md          # Claude Code worker agent config
docs/
  agent-host-adapters.md              # host adapter design and OpenCode/Codex/Claude differences
  design.md                           # architecture and control-plane design
  toy-example.md                      # step-by-step k_module walkthrough
  opencode.md                         # OpenCode-specific reference
  codex.md                            # Codex-specific reference
  claude-code.md                      # Claude Code-specific reference
examples/
  README.md                           # bundled example index
  k_module_search_spec.json           # single-round toy SearchSpec
  circle_packing_search_spec.json     # multi-batch geometric optimization SearchSpec
  signal_processing_search_spec.json  # multi-batch filtering algorithm SearchSpec
src/agentic_any_search_mcp/
  models.py                           # Pydantic API models
  runtime.py                          # file-backed runtime state machine
  tools.py                            # JSON-friendly tool facade
  server.py                           # FastMCP stdio server
tests/
  fixtures/k_module_problem/          # toy project
  fixtures/circle_packing/            # circle packing example project
  fixtures/signal_processing/         # signal filtering example project
```

## Runtime Surface

OpenCode registers the MCP server as `search-runtime`, so tools appear with
that prefix. Codex and Claude Code expose the same logical tool names through
their own MCP tool naming conventions.

- `search-runtime_goal_plus_create`
- `search-runtime_goal_plus_status`
- `search-runtime_goal_plus_record_triage`
- `search-runtime_goal_plus_save_spec_draft`
- `search-runtime_goal_plus_confirm_frozen_verifier`
- `search-runtime_goal_plus_link_search_run`
- `search-runtime_goal_plus_record_search_result`
- `search-runtime_goal_plus_set_status`
- `search-runtime_goal_plus_gate`
- `search-runtime_search_freeze_spec`
- `search-runtime_search_create`
- `search-runtime_search_status`
- `search-runtime_search_list_history`
- `search-runtime_search_plan_next`
- `search-runtime_search_start_batch`
- `search-runtime_search_start_agent_session`
- `search-runtime_search_bind_agent_handle`
- `search-runtime_search_bind_opencode_session`
- `search-runtime_search_continue_agent_session`
- `search-runtime_search_get_agent_context`
- `search-runtime_search_run_verifier`
- `search-runtime_search_list_iterations`
- `search-runtime_search_select`
- `search-runtime_search_report`
- `search-runtime_search_promote`

The `goal_plus_*` tools are the user-facing orchestration surface. The
`search_*` tools remain the internal Search Mode engine after `/goal-plus`
freezes a verifier-backed spec. The same methods are available through the
Python `GoalPlusTools` and `SearchTools` facades for unit tests and non-OpenCode
hosts.

## Development Checks

```bash
python -m pytest -q
python -m compileall src tests
```

Runtime state is written under `.search/`, which is ignored by git.
