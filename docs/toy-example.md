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
opencode.json             # starts the MCP server
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
agentic-any-search-mcp --help
```

The OpenCode MCP config calls the installed `agentic-any-search-mcp` console
script. Editable install is the development setup; normal users should install
from Git and keep the same MCP command.

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
agentic-any-search-mcp --root .search
```

If `search-runtime` is missing, run the command from the project root and check `opencode.json`.

If it is present but not connected, make sure the Python environment can import `fastmcp`:

```bash
agentic-any-search-mcp --help
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
Load examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Then run the k_module smoke test with 4 candidates end-to-end (freeze_spec → create → plan_next → start_batch → start_agent_session → Task → bind_opencode_session → verify → select → report).
```

The skill should guide the host agent through this sequence:

1. Read `examples/k_module_search_spec.json`.
2. Call `search-runtime_search_freeze_spec`.
3. Call `search-runtime_search_create`.
4. Call `search-runtime_search_plan_next` with `requested_k=4`.
5. Call `search-runtime_search_start_batch` with the returned `plan_id`.
6. For each candidate, call `search-runtime_search_start_agent_session` to obtain a context handle plus a `launch` payload, then launch the OpenCode Task using the launch payload verbatim as a foreground Task call.
7. When Task metadata is available, call `search-runtime_search_bind_opencode_session` with the runtime `agent_session_id` and Task `metadata.sessionId`.
8. Subagents edit only `initial_program.py` inside each candidate workspace and self-score with `search-runtime_search_run_verifier(..., agent_session_id=...)`. The only required MCP calls are `search_get_agent_context` and `search_run_verifier`.
9. After OpenCode Task return, call `search-runtime_search_run_verifier` for each candidate from the main agent (without `agent_session_id`) to confirm final scores.
10. Call `search-runtime_search_select`.
11. Call `search-runtime_search_report`.
12. Ask before promotion, or call `search-runtime_search_promote` if you requested full promotion.

There is no batch-shortcut tool. Call `search_plan_next` followed by `search_start_batch`.

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

If agent sessions are used:

```text
.search/runs/<run_id>/agent_sessions/<agent_session_id>.json
```

The session file stores candidate linkage, optional `opencode_session_id`, workspace, launch payload, directive, and a `verifier_runs` counter. There is no event queue or observation store; OpenCode owns lifecycle state.

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

`EditSurfaceViolation`: a candidate changed a denied file or a file outside `edit_surface.allow`; keep the failure in the report and create a new candidate.

`FrozenVerifierModified`: a candidate changed `evaluator.py`; this is an anti-cheat failure by design.

`subagent still running after I asked it to stop`: stopping a running subagent is an OpenCode/user interruption concern. There is no MCP abort tool. Stop the Task from OpenCode and let the user interrupt.

Need a clean run: remove `.search/` if you do not need previous runtime state.
