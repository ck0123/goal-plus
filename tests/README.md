# Tests

Pytest layout for the Search MCP runtime. Two tiers: unit/integration tests run by
default; system tests (ST) drive a real `opencode run` and are opt-in.

## Layout

```
tests/
├── fixtures/                          # Shared fixture projects (source workspaces)
│   ├── circle_packing/
│   ├── k_module_problem/
│   ├── signal_processing/
│   └── swe_bench_20212/
├── test_models.py                     # SearchSpec / dataclass validation
├── test_runtime_unit.py               # FileSearchRuntime internals (history, batches, verifier)
├── test_tools.py                      # SearchTools facade delegates to runtime
├── test_server.py                     # MCP server tool registration
├── test_example_scenarios.py          # Examples + runtime integration (no real opencode)
├── test_k_module_runtime.py           # k_module end-to-end on runtime (no real opencode)
├── test_opencode_assets.py            # Bundled agents / skills are well-formed
└── st/                                # System tests (real opencode run)
    ├── conftest.py                    # Pre-flight checks + fixtures
    ├── helpers/
    │   ├── opencode_runner.py         # subprocess wrapper for `opencode run`
    │   └── report_parser.py           # Parse st_report JSON block from stdout
    ├── prompts/                       # Scenario prompt templates (with {{PROJECT_ROOT}})
    │   ├── _schema.md                 # ST output contract docs
    │   ├── circle_packing_continue.md
    │   ├── circle_packing_two_batch.md
    │   ├── circle_packing_random.md
    │   ├── k_module_smoke.md
    │   ├── k_module_then_circle_packing.md
    │   ├── signal_processing_multi.md
    │   └── swe_bench_20212.md
    └── test_st_scenarios.py           # Parametrized ST cases
```

## Unit / Integration Tests (default)

Run on every change. No external dependencies beyond the venv.

```bash
pytest                                 # all
pytest tests/test_runtime_unit.py      # one file
pytest -k history                      # by name pattern
```

## System Tests (ST, opt-in)

ST tests drive `opencode run --command search "<prompt>"` in a temporary project
root and parse a machine-readable JSON report from the main agent's final
message. Each scenario prompt is in `tests/st/prompts/<scenario>.md`; the
prompt embeds an `{{PROJECT_ROOT}}` placeholder that `conftest.load_prompt`
renders with the absolute repo path so opencode (running in a tmpdir) can find
specs and fixtures without anything being copied.

ST tests are skipped by default. Pass `-m st` to enable them.

### Run

```bash
# Single scenario (smoke, ~2 min)
pytest -m st -k k_module_smoke -v -s

# Two-run isolation scenario (k_module then circle_packing, ~5-8 min)
pytest -m st -k k_module_then_circle_packing -v -s

# All seven scenarios (10-25 min)
pytest -m st -v -s

# Use a different model
ST_OPENCODE_MODEL=anthropic/claude-sonnet-4-6 pytest -m st -v -s

# Raise per-run timeout (default 1800s)
ST_OPENCODE_TIMEOUT=3600 pytest -m st -v -s
```

`-s` is required: ST tests print the log path on failure, and pytest swallows
that without it.

### Pre-flight Checks

When `-m st` is selected, `conftest.py:pytest_collection_modifyitems` runs these
checks before any test executes. Failures are aggregated and reported as a
single skip reason so you see all problems at once:

| Check | How |
|---|---|
| `opencode` binary on PATH | `shutil.which` |
| `agentic-any-search-mcp` server binary on PATH | `shutil.which` |
| `search-runtime` MCP connected | `opencode mcp list` matches `search-runtime.*connected` |
| Configured model available | `opencode models` contains `$ST_OPENCODE_MODEL` (default `deepseek/deepseek-v4-flash`) |
| Example specs + fixture evaluators present | `examples/*.json` and `tests/fixtures/*/evaluator.py` exist |

When a check fails, ST tests are skipped with a concrete reason that includes
the fix command. Use `pytest -rs` to see skip reasons in the summary.

### ST Output Contract

Every ST prompt ends with a hard constraint: the OpenCode main agent must emit
a fenced JSON block tagged `st_report` as the LAST thing in its final message.
`helpers/report_parser.py` extracts and parses it. Schema:

```json
{
  "scenario": "k_module_smoke",
  "run_id": "run_20260701_031433_51a1d2f9",
  "candidates": [
    {"candidate_id": "c001", "score": 1.0, "iterations": 1, "status": "evaluated"}
  ],
  "selected_candidate_id": "c001",
  "best_score": 1.0,
  "report_path": "/abs/path/to/.search/runs/run_.../report.md",
  "extra": {}
}
```

Scenario-specific `extra` fields (e.g. `parent_candidate_id` for random
strategy, `fail_to_pass` for swe_bench) are documented in
`tests/st/prompts/_schema.md`.

### Debugging

Each ST case writes the full opencode stdout/stderr to a log file under
`tmp_path/st_logs/<scenario>.log`. On failure, the test prints the path. To
find the latest log:

```bash
find /private/var/folders -name "*.log" -path "*st_logs*" -newer /tmp 2>/dev/null
```

The runtime also writes `report.md` and run state under `<repo>/.search/runs/`
(that path comes from `opencode.json`'s `--root .search`). Multiple ST runs
accumulate there; clear with `rm -rf .search/` if needed.

## First-time Setup on a New Machine

```bash
# 1. opencode installed
opencode --version

# 2. MCP server reachable
opencode mcp list | grep "search-runtime.*connected"
# If missing, install the server and let opencode pick up opencode.json:
pip install -e .
opencode mcp list   # run from project root so opencode.json is loaded

# 3. Model available
opencode models | grep deepseek   # or whatever ST_OPENCODE_MODEL you'll use

# 4. (Optional) configure credentials if using a provider that needs them
opencode auth login

# 5. Sanity check: collect ST tests and confirm none are skipped for pre-flight
pytest tests/st -m st --collect-only
```

## pytest.ini

```ini
[pytest]
testpaths = tests
pythonpath = src
markers =
    st: system test that drives a real `opencode run` (slow, opt-in via `-m st`)
```

The `st` marker is what gates the opt-in behavior. Without `-m st`, every ST
test is skipped with `ST tests not selected (use -m st to run)` — no preflight
checks run, so normal `pytest` stays fast.
