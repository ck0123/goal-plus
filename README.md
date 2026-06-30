# Agentic Any Search MCP

`agentic-any-search-mcp` is a small MCP-first Search Runtime prototype for verifiable agentic search.

The goal of V0 is not to control one specific coding agent. The runtime exposes a generic MCP control plane, while the host agent uses a `/search` skill to follow a disciplined workflow: freeze the spec, ask the active strategy to plan the next batch, create isolated candidate workspaces, verify candidates through runtime-owned checks, select the best candidate, and export a promotion patch.

Strategies are run-level settings. The default is `agent_guided`: the runtime exposes the official candidate history and the main agent authors the next batch by picking parents and writing one proposal per slot. Built-in alternatives include `independent_branches` (no lineage), `evolve` (runtime picks best-score parent + inspirations), `mcts` (best-score frontier expansion), and `random` (random verified parent). Custom strategies can enter through a local Python `module:Class` planner or through the standard external proposal contract. See `examples/README.md` for the full strategy comparison table.

Candidate execution always runs through `strategy.worker_mode: agent-session-pool`. The host dispatches one OpenCode Task per candidate via `search_start_agent_session`, binds the returned Task `metadata.sessionId` with `search_bind_opencode_session`, and can later continue the same node with `search_continue_agent_session`. OpenCode owns Task lifecycle and completion notification; the runtime owns candidate workspaces, verifier scoring, history, reports, and promotion patches. `budget.max_parallel` is the OpenCode-side concurrency budget and must be respected by the main agent when launching Tasks. `strategy.worker_agent_type` tells OpenCode which `subagent_type` to launch; bundled examples use `AnySearchAgent`, an autoresearch-style looper that self-iterates inside its workspace and self-verifies through `search_run_verifier`. `strategy.worker_agent_type` can be set to `AnySearchAgent` (default, 50 steps), `AnySearchAgentFlash` (15), `AnySearchAgentDeep` (100), or `AnySearchAgentExtraDeep` (150). The step cap is enforced by OpenCode per Task invocation.

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

## OpenCode

Assumption: OpenCode is already installed and has model credentials configured.

This repository ships project-local OpenCode assets:

```text
opencode.json
.opencode/skills/search/SKILL.md
.opencode/agents/search-orchestrator.md
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
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode
```

Inside OpenCode:

```text
Load examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Then run the k_module smoke test end-to-end (freeze_spec -> create -> plan_next -> start_batch -> start sessions -> Task -> bind_opencode_session -> verify -> select -> report).
```

For a headless command-line run:

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode run --command search "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Keep all edits inside candidate workspaces."
```

The environment variable must be set on the OpenCode process. It exposes `Task(background=true)`, which is required for parallel `agent-session-pool` runs. OpenCode `Task` does not currently expose a `timeout` parameter; subagents run until their OpenCode step cap hits or the user interrupts them.

See [docs/toy-example.md](docs/toy-example.md) for the complete step-by-step flow and expected artifacts.

Additional bundled specs are listed in [examples/README.md](examples/README.md), including a same-session continuation smoke test and multi-batch `circle_packing` / `signal_processing` scenarios.

## Installation Notes

This project is structured like a normal Python command-line MCP package:

- `pyproject.toml` declares the package, dependencies, and the
  `agentic-any-search-mcp` console script.
- `src/agentic_any_search_mcp/server.py` owns FastMCP stdio startup.
- `src/agentic_any_search_mcp/tools.py` exposes JSON-friendly tool methods.
- `src/agentic_any_search_mcp/runtime.py` owns file-backed runtime state.
- `src/agentic_any_search_mcp/models.py` defines the Pydantic API models.

The current repository-local OpenCode setup is for development and examples.
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
- broader client-specific setup sections beyond OpenCode

## Repository Layout

```text
opencode.json                         # project-local OpenCode MCP config
.opencode/
  skills/search/SKILL.md              # search workflow guide (loaded by host agent via Skill tool, NOT a slash command)
  agents/search-orchestrator.md       # optional host-agent prompt
  agents/AnySearchAgent.md            # candidate worker subagent prompt
docs/
  design.md                           # architecture and control-plane design
  toy-example.md                      # step-by-step k_module walkthrough
  opencode.md                         # OpenCode-specific reference
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

OpenCode registers the MCP server as `search-runtime`, so tools appear with that prefix:

- `search-runtime_search_freeze_spec`
- `search-runtime_search_create`
- `search-runtime_search_status`
- `search-runtime_search_list_history`
- `search-runtime_search_plan_next`
- `search-runtime_search_start_batch`
- `search-runtime_search_start_agent_session`
- `search-runtime_search_bind_opencode_session`
- `search-runtime_search_continue_agent_session`
- `search-runtime_search_get_agent_context`
- `search-runtime_search_run_verifier`
- `search-runtime_search_list_iterations`
- `search-runtime_search_select`
- `search-runtime_search_report`
- `search-runtime_search_promote`

The same methods are available through the Python `SearchTools` facade for unit tests and non-OpenCode hosts.

## Development Checks

```bash
python -m pytest -q
python -m compileall src tests
```

Runtime state is written under `.search/`, which is ignored by git.
