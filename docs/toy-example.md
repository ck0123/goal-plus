# Toy Example: k_module Goal Plus Search

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
.opencode/skills/goal-plus/SKILL.md
.opencode/command/goal-plus.md
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
goal-plus --help
```

The OpenCode MCP config calls the installed `goal-plus` console
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
goal-plus connected
goal-plus --root .gp
```

If `goal-plus` is missing, run the command from the project root and check `opencode.json`.

If it is present but not connected, make sure the Python environment can import `fastmcp`:

```bash
goal-plus --help
```

## 4. Optional Negative Probe

This verifies that `opencode run` can call an MCP tool without creating a search run:

```bash
opencode run "Use the MCP tool goal-plus_search_status with run_id='missing-opencode-smoke'. Do not edit files. Report whether the tool was available and summarize the error if the run does not exist."
```

Expected behavior:

- OpenCode calls `goal-plus_search_status`
- the tool is available
- the runtime reports that `.gp/runs/missing-opencode-smoke/run.json` does not exist

That error is expected because the run was never created.

## 5. Run The Search In OpenCode TUI

Start OpenCode:

```bash
opencode
```

Then send:

```text
Use /goal-plus. Load examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Decide autonomously whether the verifier-backed spec is search-ready, then run the k_module smoke test with 4 candidates end-to-end without asking for user confirmation.
```

The skill should guide the host agent through this sequence:

1. Call `goal-plus_goal_plus_create`.
2. Read `examples/k_module_search_spec.json`.
3. Record search-ready triage with `identified_at="initial"`.
4. Save a high-confidence spec draft with `origin="initial"`.
5. Call `goal-plus_goal_plus_gate` before `goal-plus_search_freeze_spec`;
   no separate user confirmation is required.
6. Call `goal-plus_search_freeze_spec`.
7. Call `goal-plus_search_create`, then `goal-plus_goal_plus_link_search_run`.
8. Call `goal-plus_search_plan_next` with `requested_k=4`.
9. Call `goal-plus_search_start_batch` with the returned `plan_id`.
10. For each candidate, call `goal-plus_search_start_agent_session` to obtain a context handle plus a `launch` payload, then launch the OpenCode Task using the launch payload verbatim as a foreground Task call.
11. When Task metadata is available, call `goal-plus_search_bind_opencode_session` with the runtime `agent_session_id` and Task `metadata.sessionId`.
12. Subagents edit only `initial_program.py` inside each candidate workspace and self-score with `goal-plus_search_run_verifier(..., agent_session_id=...)`. The only required MCP calls are `search_get_agent_context` and `search_run_verifier`.
13. After OpenCode Task return, call `goal-plus_search_run_verifier` for each candidate from the main agent (without `agent_session_id`) to confirm final scores.
14. Call `goal-plus_search_select`.
15. Call `goal-plus_search_report`.
16. Ask before promotion, or call `goal-plus_search_promote` if you requested full promotion.
17. Call `goal-plus_goal_plus_record_search_result`, then perform the final raw-goal audit.

There is no batch-shortcut tool. Call `search_plan_next` followed by `search_start_batch`.

## 6. Run Headless

You can trigger the same skill from the command line:

```bash
opencode run --command goal-plus "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Decide autonomously whether the verifier-backed spec is search-ready. Keep all edits inside candidate workspaces. Report the selected candidate, score, report path, and promotion patch path if promoted."
```

If you want to inspect before promotion, use:

```bash
opencode run --command goal-plus "Run the k_module smoke test with 4 candidates. Use examples/k_module_search_spec.json and freeze tests/fixtures/k_module_problem/evaluator.py. Decide autonomously whether the verifier-backed spec is search-ready. Stop after report generation and do not promote."
```

## 7. Expected Runtime Artifacts

After freeze and run creation:

```text
.gp/specs/<frozen_spec_id>/frozen_spec.json
.gp/specs/<frozen_spec_id>/frozen_verifiers/evaluator.py
.gp/runs/<run_id>/run.json
.gp/runs/<run_id>/plans/<plan_id>.json
```

After candidate creation:

```text
.gp/runs/<run_id>/workspace/c001/
.gp/runs/<run_id>/workspace/c002/
.gp/runs/<run_id>/workspace/c003/
.gp/runs/<run_id>/workspace/c004/
.gp/runs/<run_id>/workspace/c001/.tmp/
.gp/runs/<run_id>/candidates/c001/candidate.json
.gp/runs/<run_id>/candidates/c001/task.json
```

Use each workspace's `.tmp/` directory for temporary files. Runtime change detection ignores `.tmp/`, and promotion patches do not include it.

If agent sessions are used:

```text
.gp/runs/<run_id>/agent_sessions/<agent_session_id>.json
```

The session file stores candidate linkage, optional `opencode_session_id`, workspace, launch payload, directive, and a `verifier_runs` counter. There is no event queue or observation store; OpenCode owns lifecycle state.

After verification and reporting:

```text
.gp/runs/<run_id>/report.md
.gp/runs/<run_id>/candidates/<candidate_id>/logs/k_module_score.log
```

After promotion:

```text
.gp/runs/<run_id>/promotion/<candidate_id>.patch
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

Need a clean run: remove `.gp/` if you do not need previous runtime state.
