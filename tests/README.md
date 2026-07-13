# Tests

Pytest layout for the `/goal-plus` runtime and internal Search Mode engine. Two
tiers: unit/integration tests run by default; system tests (ST) drive a real
host code agent and are opt-in.

## Layout

```
tests/
├── fixtures/                          # Shared fixture projects (source workspaces)
│   ├── circle_packing/
│   ├── k_module_problem/
│   ├── signal_processing/
│   └── swe_bench_20212/
├── test_models.py                     # SearchSpec / dataclass validation
├── test_workspace_backends.py         # Copy/worktree materialization and Git lineage
├── test_runtime_unit.py               # FileSearchRuntime internals (history, batches, verifier)
├── test_tools.py                      # GoalPlusTools/SearchTools facades
├── test_server.py                     # MCP server tool registration
├── test_examples.py                   # Examples + runtime integration (no real opencode)
├── test_kernel_optimize_example.py    # Kernel template verifier behavior
├── test_k_module_runtime.py           # k_module end-to-end on runtime (no real opencode)
├── test_opencode_assets.py            # Bundled agents / skills are well-formed
├── test_pi_assets.py                  # Pi prompts / skills / extension are well-formed
├── test_pi_tool.py                    # Pi JSON CLI facade
├── test_pi_driver.py                  # Pi native candidate driver orchestration
├── test_pi_worker.py                  # Pi RPC worker runner metrics and retries
├── test_codex_goal_plus_hooks.py      # Codex create/edit/resume/with-check hook scenarios
├── test_goal_plus.py                  # Goal revisions and final-check state-machine scenarios
├── st/                                # System tests (real host code-agent run)
│   ├── conftest.py                    # Pre-flight checks + fixtures
│   ├── hosts.py                       # Host marker mapping and asset linking
│   ├── helpers/
│   │   ├── codex_runner.py            # subprocess wrapper for `codex exec`
│   │   ├── claude_runner.py           # subprocess wrapper for `claude -p`
│   │   ├── opencode_runner.py         # subprocess wrapper for `opencode run`
│   │   └── report_parser.py           # Parse st_report JSON block from stdout
│   ├── prompts/                       # Scenario prompt templates (with {{PROJECT_ROOT}})
│   │   ├── _schema.md                 # ST output contract docs
│   │   ├── circle_packing_continue.md
│   │   ├── circle_packing_two_batch.md
│   │   ├── circle_packing_random.md
│   │   ├── claude_k_module_smoke.md
│   │   ├── codex_circle_packing_cycle.md
│   │   ├── codex_redispatch.md
│   │   ├── k_module_smoke.md
│   │   ├── k_module_then_circle_packing.md
│   │   ├── signal_processing_multi.md
│   │   └── swe_bench_20212.md
│   └── test_st_scenarios.py           # Parametrized ST cases
└── st_pi/                             # Pi native /goal-plus print/TUI ST
    ├── conftest.py                    # Pi binary checks + per-test run root
    └── test_goal_plus_pi.py           # Real Pi command/TUI + autonomous Search admission cases
```

## Testing Contract

Use this contract when adding coverage, reviewing changes, or deciding what to
run before merging.

| Tier | Command | What it proves | What it does not prove |
|---|---|---|---|
| Default gate | `python -m pytest -q` | Models, runtime state machine, facades, bundled assets, example specs, and mock/fixture integration still behave | A real host agent can finish the user-visible workflow |
| Runtime E2E | Included in default tests, e.g. `test_k_module_runtime.py` and the `workspace-backends` example in `test_examples.py` | Candidate workspaces, workspace backend lineage, frozen verifier artifacts, selection, reports, and promotion logic work without a host agent | Host launch, prompt following, MCP wiring, native hooks, or real worker lifecycle |
| System tests | `python -m pytest -m "st or st_pi" -v -s` | Real host/Pi entrypoints can drive the workflow and produce final runtime evidence | Full exhaustive strategy quality; ST is a slow final-effect gate |

Rules:

- Default tests must stay fast and must not launch real OpenCode, Codex,
  Claude Code, or Pi workers.
- Only ST results can be cited as proof that a user-visible host workflow works
  end to end.
- Any change to host assets, worker launch/budget/continuation, MCP/tool
  gating, Pi native extension code, or `/goal-plus` command behavior must run a
  matching ST before claiming final behavior.
- If a matching ST cannot be run, the final report must say exactly which ST
  was skipped and why.
- New host-native paths need an ST smoke. New batch/round behavior needs an ST
  cycle test that asserts the expected candidate count unless the test is
  explicitly documented as link-level.
- Every `tests/st/` ST must have `st` plus exactly one host marker:
  `st_opencode`, `st_codex`, `st_claude`, or `st_pi_rpc`.
- Pi native `/goal-plus` print/TUI tests live under `tests/st_pi/` and use
  `st_pi`; Pi RPC worker tests live under `tests/st/` and use `st_pi_rpc`.
- When adding, renaming, or deleting an ST, update this README's layout, run
  commands, and marker list in the same change.

## Unit / Integration Tests (default)

Run on every change. No external dependencies beyond the venv.

```bash
pytest                                 # all
pytest tests/test_runtime_unit.py      # one file
pytest -k history                      # by name pattern
pytest -m codex                        # fast Codex unit/asset/parity slice
pytest -m pi                           # fast Pi unit/asset/parity slice
```

Host-specific fast tests use `pytest.mark.codex` or `pytest.mark.pi`.
Real-host tests keep their opt-in `st_codex` or `st_pi_rpc` marker and are not
selected by those fast slices.

## System Tests (ST, opt-in)

ST tests drive a real host code agent in a temporary project root and parse a
machine-readable JSON report from the main agent's final message. Host-specific
markers select the runner:

| Marker | Runner | Default model |
|---|---|---|
| `st_opencode` | `opencode run --command goal-plus` | OpenCode default unless `$ST_OPENCODE_MODEL` is set |
| `st_codex` | `codex exec` | `gpt-5.6-terra` unless `$ST_CODEX_MODEL` is set |
| `st_claude` | `claude -p` | Claude default unless `$ST_CLAUDE_MODEL` is set |
| `st_pi_rpc` | `goal-plus-pi-worker` launching `pi --mode rpc` | Pi default unless runner args override it |

The runner prepends a non-interactive autonomy preamble: the host agent must
decide whether Search adds value, discover missing verifier/spec details, and
enter Search when ready without asking for user confirmation. Each scenario
prompt is in `tests/st/prompts/<scenario>.md`; the prompt embeds an
`{{PROJECT_ROOT}}` placeholder that `conftest.load_prompt` renders with the
absolute repo path so the host agent can find specs and fixtures without
copying them.

Host runner subprocesses set `GOAL_PLUS_ST_ACTIVE=<scenario>`. If a
host agent accidentally tries to run `pytest -m st` from inside an active ST,
`tests/st/conftest.py` exits with code 4. This prevents recursive ST launches;
the correct path is for the host agent to call `goal-plus` MCP tools
directly, then launch foreground workers from runtime launch payloads.

ST tests are skipped by default. Pass `-m st` to enable `tests/st`, pass
`-m st_pi` to enable Pi native `/goal-plus` tests, or pass
`-m "st or st_pi"` for the complete real-host gate.

### Run

```bash
# Single scenario (smoke, ~2 min)
pytest -m "st and st_opencode" -k k_module_smoke -v -s

# Codex redispatch scenario, default model gpt-5.6-terra
pytest -m "st and st_codex" -k codex_redispatch -v -s

# Codex circle-packing cycle, batch=2 and round=2
pytest -m "st and st_codex" -k codex_circle_packing_cycle -v -s

# Codex Search candidate PostTool time-advisory E2E
pytest -m "st and st_codex" -k codex_time_advisory -v -s

# Pi RPC worker smoke
pytest -m "st and st_pi_rpc" -k pi_rpc_k_module -v -s

# Pi RPC EdgeBench-lite PostTool time-advisory E2E
pytest -m "st and st_pi_rpc" -k edgebench_time_advisory -v -s

# Pi RPC circle-packing cycle, batch=2 and round=2
pytest -m "st and st_pi_rpc" -k circle_packing_two_batch -v -s

# Pi native /goal-plus print/TUI entrypoints
pytest -m st_pi -v -s

# Real Codex and Pi required-final-check reviewer smokes
pytest -m "st and st_codex" -k goal_plus_required_final_checker -v -s
pytest -m st_pi -k with_final_check_runs_pi_reviewer -v -s

# Pi native plain-language optimization must autonomously enter Search
pytest -m st_pi -k autonomously_enters_search -v -s

# Pi native one-Goal-Plus/two-search-task smoke
pytest -m st_pi -k two_search_tasks -v -s

# Pi RPC worker smoke with explicit model and thinking level
ST_PI_MODEL=openai-codex/gpt-5.4-mini ST_PI_THINKING=high pytest -m "st and st_pi_rpc" -k pi_rpc_k_module -v -s

# Two-run isolation scenario (k_module then circle_packing, ~5-8 min)
pytest -m "st and st_opencode" -k k_module_then_circle_packing -v -s

# All tests/st scenarios for every installed/configured host
pytest -m st -v -s

# Complete real-host gate, including Pi native /goal-plus print/TUI ST
pytest -m "st or st_pi" -v -s

# Use a different host model
ST_OPENCODE_MODEL=anthropic/claude-sonnet-4-6 pytest -m st -v -s
ST_CODEX_MODEL=gpt-5.6-terra pytest -m "st and st_codex" -v -s
ST_CLAUDE_MODEL=sonnet pytest -m "st and st_claude" -v -s
ST_PI_MODEL=openai-codex/gpt-5.4-mini ST_PI_THINKING=high pytest -m "st and st_pi_rpc" -v -s

# Raise per-run timeout (default 1800s)
ST_OPENCODE_TIMEOUT=3600 pytest -m st -v -s
ST_CODEX_TIMEOUT=3600 pytest -m "st and st_codex" -v -s
ST_CLAUDE_TIMEOUT=3600 pytest -m "st and st_claude" -v -s
ST_PI_CYCLE_WORKER_SECONDS=120 pytest -m "st and st_pi_rpc" -k circle_packing_two_batch -v -s
```

`-s` is required: ST tests print the log path on failure, and pytest swallows
that without it.

### Pre-flight Checks

When `-m st` is selected, `conftest.py:pytest_collection_modifyitems` runs these
checks before any test executes. Failures are aggregated and reported as a
single skip reason so you see all problems at once:

| Check | How |
|---|---|
| Host binary on PATH | `shutil.which` for `opencode`, `codex`, `claude`, or `pi` based on marker |
| `goal-plus` server binary on PATH | `shutil.which` |
| `goal-plus` MCP connected/configured | host-native MCP listing for the selected marker |
| Configured model available | OpenCode only, via `opencode models`; Codex/Claude validate model during the real run |
| ST specs + fixture evaluators present | `tests/st/fixtures/*/{spec.json,evaluator.py,initial_program.py,config.yaml}` exist |
| Nested ST guard | `GOAL_PLUS_ST_ACTIVE` is not set in the pytest process |

When a check fails, ST tests are skipped with a concrete reason that includes
the fix command. Use `pytest -rs` to see skip reasons in the summary.

### ST Output Contract

Every ST prompt ends with a hard constraint: the host main agent must emit
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
  "report_path": "/abs/path/to/.gp/runs/run_.../report.md",
  "extra": {}
}
```

Scenario-specific `extra` fields (e.g. `parent_candidate_id` for random
strategy, `fail_to_pass` for swe_bench) are documented in
`tests/st/prompts/_schema.md`.

### Debugging

Each ST case writes the full host stdout/stderr to
`<repo>/.tmp/st-logs/<test-node>/<scenario>.log`. On failure, the test prints
the path. To find the latest log:

```bash
find .tmp/st-logs -name "*.log" -print
```

The runtime also writes `report.md` and run state under `<repo>/.gp/runs/`
(that path comes from `opencode.json`'s `--root .gp`). Multiple ST runs
accumulate there; clear with `rm -rf .gp/` if needed.

## First-time Setup on a New Machine

```bash
# 1. opencode installed
opencode --version

# 2. MCP server reachable
opencode mcp list | grep "goal-plus.*connected"
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
    codex: Codex-specific unit, integration, asset, or parity test
    pi: Pi-specific unit, integration, asset, or parity test
    st: system test that drives a real host code agent (slow, opt-in via `-m st`)
    st_opencode: ST case that runs through OpenCode
    st_codex: ST case that runs through Codex
    st_claude: ST case that runs through Claude Code
    st_pi_rpc: ST case that runs through Pi RPC
    st_pi: ST case that runs through Pi native/print Goal Plus
```

The `st` and `st_pi` markers gate opt-in behavior. Without `-m st` or
`-m st_pi`, every real-host test is skipped before expensive pre-flight checks,
so normal `pytest` stays fast.
