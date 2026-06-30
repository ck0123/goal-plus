# Search Examples

The example specs are small local scenarios for exercising the Search MCP
runtime with OpenCode-native Task workers.

| Spec | Fixture | Worker | Layout |
|---|---|---|---|
| `k_module_search_spec.json` | `tests/fixtures/k_module_problem` | `AnySearchAgentFlash` (15 steps) | 2 candidates, pool=2, smoke test |
| `circle_packing_search_spec.json` | `tests/fixtures/circle_packing` | `AnySearchAgentFlash` (15 steps) | 4 candidates, pool=2, two batches |
| `signal_processing_search_spec.json` | `tests/fixtures/signal_processing` | `AnySearchAgent` (50 steps) | 8 candidates, pool=4, two batches |
| `swe_bench_20212_search_spec.json` | `tests/fixtures/swe_bench_20212` | `AnySearchAgent` (50 steps) | 4 candidates, pool=2, single batch |

## OpenCode Runtime Flow

Candidate execution goes through `strategy.worker_mode: agent-session-pool`.
The MCP runtime owns specs, plans, candidate workspaces, verifier scoring,
history, reports, and promotion patches. OpenCode owns Task lifecycle and
completion notification.

For a new candidate session:

```text
search_freeze_spec(...)
search_create(...)
search_plan_next(run_id, requested_k=k)
search_start_batch(run_id, plan_id, proposals?)

session = search_start_agent_session(run_id, candidate_id, directive)
result = Task(
  subagent_type=session.launch.subagent_type,
  description=session.launch.description,
  prompt=session.launch.prompt,
  background=session.launch.background_required,
)
search_bind_opencode_session(
  agent_session_id=session.agent_session_id,
  opencode_session_id=result.metadata.sessionId,
)
search_run_verifier(run_id, candidate_id, "process")
```

For a same-session continuation:

```text
continued = search_continue_agent_session(session.agent_session_id, directive)
Task(
  task_id=continued.launch.task_id,
  subagent_type=continued.launch.subagent_type,
  description=continued.launch.description,
  prompt=continued.launch.prompt,
  background=continued.launch.background_required,
)
search_run_verifier(run_id, candidate_id, "process")
```

Continuation reuses the same runtime `agent_session_id`, OpenCode session,
candidate id, and workspace. It is not a fork and it does not create a new
candidate workspace. Do not call `search_start_agent_session` for the
continuation path.

For `max_parallel > 1`, start OpenCode with background subagents enabled:

```bash
OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true opencode
```

There is no MCP wait, abort, finish, submit, observation, or host-sync API.
Wait for OpenCode Task completion or background result injection. Stopping a
running subagent is an OpenCode/user interruption concern.

## Same-Session Continuation Smoke Test

Use this prompt to test the "subagent finished, then main starts it again from
the same node" scenario:

```text
Load examples/k_module_search_spec.json. Freeze tests/fixtures/k_module_problem/evaluator.py.

Run a same-session continuation smoke test:
1. create the run
2. plan_next(k=1)
3. start_batch
4. start one agent session for c001
5. launch the Task from session.launch
6. when Task returns, bind session.agent_session_id to Task metadata.sessionId with search_bind_opencode_session
7. run search_run_verifier(run_id, "c001", "process") from the main agent
8. call search_continue_agent_session(session.agent_session_id, directive="continue the same candidate once; do not branch")
9. launch Task again with task_id=continued.launch.task_id and the rest of continued.launch
10. when Task returns, run search_run_verifier(run_id, "c001", "process") again
11. call search_list_history and search_report

Do not create a second agent session for c001. Do not fork. Report the run_id,
agent_session_id, opencode_session_id, both final verifier scores, and report path.
```

Expected checks:

- The same `agent_session_id` appears before and after continuation.
- `.search/runs/<run_id>/agent_sessions/<agent_session_id>.json` has
  `opencode_session_id` populated.
- `search_continue_agent_session` returns `launch.task_id` equal to that
  `opencode_session_id`.
- `search_list_history` includes `agent_sessions[0].opencode_session_id`.
- The report contains an "OpenCode Session" column in the Agent Sessions table.

## Step Tiers

`strategy.worker_agent_type` picks one of four OpenCode subagent variants. The
variant fixes the per-Task step cap; runtime cannot change it inside a running
Task.

| Variant | Steps | Use when |
|---|---:|---|
| `AnySearchAgentFlash` | 15 | Smoke tests, toy tasks, cheap iterations |
| `AnySearchAgent` | 50 | Standard autoresearch loop |
| `AnySearchAgentDeep` | 100 | Sustained iteration on harder problems |
| `AnySearchAgentExtraDeep` | 150 | Extensive search, complex fixtures |

A promising candidate can be reinvested in by continuing the same bound
OpenCode session with `search_continue_agent_session`.

## Budget Semantics

Each `SearchSpec` must include an explicit `budget`.

```json
{
  "budget": {
    "max_candidates": 4,
    "max_parallel": 2
  }
}
```

- `max_candidates`: total candidate workspaces allowed for the run. Enforced by
  `search_plan_next` / `search_start_batch`.
- `max_parallel`: OpenCode-side concurrency budget. The main agent must not
  launch more concurrent Tasks than this value.
- `max_tokens`: optional spec field for callers that want to track token budget;
  the runtime does not use it as a Task timeout.

Freeze the matching evaluator as the verifier artifact:

```text
tests/fixtures/k_module_problem/evaluator.py
tests/fixtures/circle_packing/evaluator.py
tests/fixtures/signal_processing/evaluator.py
tests/fixtures/swe_bench_20212/evaluator.py
```

## Strategy Modes

The default strategy is `agent_guided`: the runtime exposes official candidate
history and the main agent authors the next batch. The bundled specs pin
`independent_branches` so demo flows are deterministic and easy to inspect.

| Strategy | Parent picker | `requires_agent_proposals` | First batch | Use when |
|---|---|---|---|---|
| `agent_guided` | Main agent | `true` | From source | Let the main agent choose how to build on history |
| `independent_branches` | None | `false` | From source | Baselines and smoke tests |
| `evolve` | Runtime best-score parent plus inspirations | `false` | From source | OpenEvolve-style fixed parent selection |
| `mcts` | Runtime best-score frontier | `false` | From source | MCTS-style expansion |
| `random` | Runtime random scored parent | `false` | From source | Random-walk baseline |

## Other Example Prompts

### k_module baseline smoke test

```text
Load examples/k_module_search_spec.json. The spec sets max_candidates=2,
max_parallel=2, worker_agent_type=AnySearchAgentFlash. Freeze
tests/fixtures/k_module_problem/evaluator.py and run end-to-end:
freeze_spec -> create -> plan_next(k=2) -> start_batch -> start 2 sessions ->
Task -> bind_opencode_session -> verify each candidate -> select -> report.
```

### circle_packing two batches

```text
Load examples/circle_packing_search_spec.json. Freeze
tests/fixtures/circle_packing/evaluator.py. Run two batches of 2 candidates
with background Tasks. Bind each Task metadata.sessionId after it returns.
After each candidate returns, run main-agent search_run_verifier without
agent_session_id. After all 4 candidates are evaluated, select and report.
```

### signal_processing multi-batch

```text
Load examples/signal_processing_search_spec.json. Freeze
tests/fixtures/signal_processing/evaluator.py. Plan and start 4 candidates,
then plan and start the next 4 after the first batch returns. Use
AnySearchAgent, bind every OpenCode Task session id, verify, select, and report.
```

### SWE-bench style fixture

`swe_bench_20212_search_spec.json` wraps a SWE-bench style bug fix
(`sympy__sympy-20212`) instead of a multi-batch optimization. The candidate's
job is to patch `evaluate_power` in
`tests/fixtures/swe_bench_20212/initial_program.py` so that
`evaluate_power(ZERO, NEG_INFINITY)` returns `COMPLEX_INFINITY`.

```text
Load examples/swe_bench_20212_search_spec.json. Freeze
tests/fixtures/swe_bench_20212/evaluator.py. Request 4 candidates. After
verifying them, inspect summaries and FAIL_TO_PASS / PASS_TO_PASS results.
Stop after report generation and do not promote.
```

Quick local sanity check without the runtime:

```bash
cd tests/fixtures/swe_bench_20212
python3 -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2))"
```
