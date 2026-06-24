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
25 passed
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
4. Call `search-runtime_search_next_batch` with `k=4`.
5. Edit only `initial_program.py` inside each candidate workspace.
6. Call `search-runtime_search_submit_candidate` for each candidate.
7. Call `search-runtime_search_run_verifier` for each candidate.
8. Call `search-runtime_search_select`.
9. Call `search-runtime_search_report`.
10. Ask before promotion, or call `search-runtime_search_promote` if you requested full promotion.

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
```

After candidate creation:

```text
.search/runs/<run_id>/workspace/c001/
.search/runs/<run_id>/workspace/c002/
.search/runs/<run_id>/workspace/c003/
.search/runs/<run_id>/workspace/c004/
.search/runs/<run_id>/candidates/c001/record.json
```

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
