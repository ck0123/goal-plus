# Agentic Any Search MCP

`agentic-any-search-mcp` is a small MCP-first Search Runtime prototype for verifiable agentic search.

The goal of V0 is not to control one specific coding agent. The runtime exposes a generic MCP control plane, while the host agent uses a `/search` skill to follow a disciplined workflow: freeze the spec, create isolated candidate workspaces, verify candidates through runtime-owned checks, select the best candidate, and export a promotion patch.

## Quick Start With OpenCode

Assumption: OpenCode is already installed and has model credentials configured.

```bash
cd agentic-any-search-mcp
python -m pip install -e ".[dev]"
python -m pytest -q
opencode mcp list
```

`opencode mcp list` should show:

```text
search-runtime connected
python -m agentic_any_search_mcp.server --root .search
```

Then run the toy search from the OpenCode TUI:

```bash
opencode
```

Inside OpenCode:

```text
/search run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py.
```

For a headless command-line run:

```bash
opencode run --command search "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Keep all edits inside candidate workspaces."
```

See [docs/toy-example.md](docs/toy-example.md) for the complete step-by-step flow and expected artifacts.

Additional bundled specs are listed in [examples/README.md](examples/README.md), including multi-batch `circle_packing` and `signal_processing` scenarios that use `max_candidates=8` and `max_parallel=4`.

## Repository Layout

```text
.opencode/
  opencode.json                       # local MCP server config
  skills/search/SKILL.md              # /search workflow guide for the host agent
  agents/search-orchestrator.md       # optional host-agent prompt
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
- `search-runtime_search_next_batch`
- `search-runtime_search_submit_candidate`
- `search-runtime_search_run_verifier`
- `search-runtime_search_select`
- `search-runtime_search_report`
- `search-runtime_search_promote`
- `search-runtime_search_abort`

The same methods are available through the Python `SearchTools` facade for unit tests and non-OpenCode hosts.

## Development Checks

```bash
python -m pytest -q
python -m compileall src tests
```

Runtime state is written under `.search/`, which is ignored by git.
