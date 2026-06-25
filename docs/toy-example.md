# Toy Example: k_module Search

This guide shows how to run the bundled toy search from a repo checkout when OpenCode is already available.

The toy task is intentionally simple. It is a control-plane smoke test, not a benchmark for search quality. The candidate edits one file, `initial_program.py`, and the frozen evaluator scores whether four configuration slots match the target values.

## Files

```text
examples/k_module_search_spec.json
tests/fixtures/k_module_problem/
  initial_program.py      # only editable file
  evaluator.py            # frozen verifier artifact
  config.yaml             # denied config file
.opencode/opencode.json   # starts the MCP server
.opencode/skills/search/SKILL.md
```

The target configuration is:

```python
loader = "csv_reader"
preprocess = "normalize"
algorithm = "quicksort"
formatter = "json"
```

## 1. Install Python Dependencies

From the project root:

```bash
python -m pip install -e ".[dev]"
```

The OpenCode MCP config uses `PYTHONPATH=src`, so editable install is not required for local imports, but it is the simplest way to install `fastmcp`, `pydantic`, and `pytest`.

## 2. Run Local Tests

```bash
python -m pytest -q
python -m compileall src tests
```

Expected:

```text
all tests pass
```

Warnings from transitive packages are okay as long as tests pass.

## 3. Verify OpenCode Sees The MCP Server

```bash
opencode mcp list
```

Expected:

```text
search-runtime connected
python -m agentic_any_search_mcp.server --root .search
```

If `search-runtime` is missing, run the command from the project root and check `.opencode/opencode.json`.

If it is present but not connected, make sure the Python environment can import `fastmcp`:

```bash
python -c "import fastmcp, pydantic"
```

## 4. Optional Negative Probe

This verifies that `opencode run` can call an MCP tool without creating a search run:

```bash
opencode run "Use the MCP tool search-runtime_search_status with run_id='missing-opencode-smoke'. Do not edit files. Report whether the tool was available and summarize the error if the run does not exist."
```

Expected behavior:

- OpenCode calls `search-runtime_search_status`
- the tool is available
- the runtime reports that `.search/runs/missing-opencode-smoke/run.json` does not exist

That error is expected because the run was never created.

## 5. Run The Search In OpenCode TUI

Start OpenCode:

```bash
opencode
```

Then send:

```text
/search run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py.
```

The skill should guide the host agent through this sequence:

1. Read `examples/k_module_search_spec.json`.
2. Call `search-runtime_search_freeze_spec`.
3. Call `search-runtime_search_create`.
4. Call `search-runtime_search_plan_next` with `requested_k=4`.
5. Call `search-runtime_search_start_batch` with the returned `plan_id`.
6. Follow the returned `worker_policy`. The bundled k_module spec uses `main-agent-search-direct`, so the host edits directly. Specs with `sub-agent-search-dispatch` must call `search-runtime_search_prepare_worker` for each candidate and pass the returned `dispatch_id` to the subagent. If `worker_policy.subagent_type` is present, use it as the OpenCode `subagent_type`; bundled dispatch examples use `AnySearchAgent`. Default worker timeout is 600 seconds unless the spec sets `strategy.worker_timeout_seconds`; `search_prepare_worker(..., timeout_seconds=...)` can override one dispatch. Default worker-local verifier limit is 0, so actual verification is main-agent/runtime-owned.
7. Edit only `initial_program.py` inside each candidate workspace.
8. Call `search-runtime_search_submit_candidate` for each candidate.
9. Call `search-runtime_search_run_verifier` for each candidate.
10. Call `search-runtime_search_select`.
11. Call `search-runtime_search_report`.
12. Ask before promotion, or call `search-runtime_search_promote` if you requested full promotion.

`search-runtime_search_next_batch(run_id, 4)` is still available as a compatibility shortcut for this default independent strategy; it performs the plan/start sequence internally.

## 6. Run Headless

You can trigger the same skill from the command line:

```bash
opencode run --command search "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Keep all edits inside candidate workspaces. Report the selected candidate, score, report path, and promotion patch path if promoted."
```

If you want to inspect before promotion, use:

```bash
opencode run --command search "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Stop after report generation and do not promote."
```

## 7. Expected Runtime Artifacts

After freeze and run creation:

```text
.search/specs/<frozen_spec_id>/frozen_spec.json
.search/specs/<frozen_spec_id>/frozen_verifiers/evaluator.py
.search/runs/<run_id>/run.json
.search/runs/<run_id>/plans/<plan_id>.json
```

After candidate creation:

```text
.search/runs/<run_id>/workspace/c001/
.search/runs/<run_id>/workspace/c002/
.search/runs/<run_id>/workspace/c003/
.search/runs/<run_id>/workspace/c004/
.search/runs/<run_id>/workspace/c001/.tmp/
.search/runs/<run_id>/candidates/c001/candidate.json
.search/runs/<run_id>/candidates/c001/task.json
```

Use each workspace's `.tmp/` directory for temporary files. Runtime change detection ignores `.tmp/`, and promotion patches do not include it.

If worker dispatches are used:

```text
.search/runs/<run_id>/dispatches/<dispatch_id>.json
.search/runs/<run_id>/dispatches/<dispatch_id>.md
```

The JSON file stores the main agent directive, context hash, and authoritative worker context. The markdown file is the brief that can be pasted into a subagent.

After verification and reporting:

```text
.search/runs/<run_id>/report.md
.search/runs/<run_id>/candidates/<candidate_id>/logs/k_module_score.log
```

After promotion:

```text
.search/runs/<run_id>/promotion/<candidate_id>.patch
```

Promotion writes a patch only. It does not modify `tests/fixtures/k_module_problem/initial_program.py`.

## 8. Expected Outcome

The best candidate should set all four configuration fields to the target values and receive:

```json
{"combined_score": 1.0}
```

The report should show the selected candidate with score `1.0` and no denied-file changes.

## Troubleshooting

`MCP tools unavailable`: run `opencode mcp list` from the project root.

`ModuleNotFoundError: fastmcp`: run `python -m pip install -e ".[dev]"` in the Python environment used by OpenCode.

`candidate must be submitted before verification`: the host skipped `search-runtime_search_submit_candidate`; submit the candidate artifact first.

`EditSurfaceViolation`: a candidate changed a denied file or a file outside `edit_surface.allow`; keep the failure in the report and create a new candidate.

`FrozenVerifierModified`: a candidate changed `evaluator.py`; this is an anti-cheat failure by design.

Need a clean run: remove `.search/` if you do not need previous runtime state.
