# Iteration-History Plan: merge submit + verify + counter

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the two-step `submit_candidate → run_verifier` flow with a single `run_verifier` call that scores, records an iteration, and increments the counter in one shot. All iterations are visible via list API and in `get_agent_context`.

**Architecture:** Add `IterationRecord` model and `iterations: list` field on `CandidateRecord`. Relax `run_verifier` to accept `created` status (no pre-submit required). Each call appends a record. `submit_candidate` becomes optional (still works for explicit "I'm done" marking, but no longer required for verify). Budget control stays via `max_verifier_runs` + agent step count.

**Tech Stack:** Python 3, Pydantic, FastMCP, pytest.

---

## File Structure

- Modify: `src/agentic_any_search_mcp/models.py` — add IterationRecord, add iterations field on CandidateRecord
- Modify: `src/agentic_any_search_mcp/runtime.py` — relax run_verifier, append iterations, add list_iterations
- Modify: `src/agentic_any_search_mcp/tools.py` — add search_list_iterations
- Modify: `src/agentic_any_search_mcp/server.py` — register new tool
- Modify: `tests/test_runtime_unit.py` — new tests for iteration recording
- Modify: `tests/test_tools.py` — passthrough test for list_iterations
- Modify: `.opencode/agents/AnySearchAgent.md` — simplify Iteration Loop (drop submit step)
- Modify: `.opencode/skills/search/SKILL.md` — drop submit from contract

`submit_candidate` stays in code for backward compat but the AnySearchAgent prompt no longer uses it.

---

## Task 1: Add IterationRecord model and iterations field

**Files:**
- Modify: `src/agentic_any_search_mcp/models.py`

- [ ] **Step 1: Add IterationRecord class and iterations field**

Insert after `ScoreReport` class (around line 305):

```python
class IterationRecord(SearchModel):
    iteration: int
    agent_session_id: str | None = None
    score: float | None = None
    failure_class: str | None = None
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str
```

Add to `CandidateRecord` (around line 338):

```python
class CandidateRecord(SearchModel):
    candidate_id: str
    status: Literal["created", "submitted", "evaluated", "failed"]
    task: CandidateTask
    artifact: ArtifactBundle | None = None
    detected_changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    score_report: ScoreReport | None = None
    iterations: list[IterationRecord] = Field(default_factory=list)
```

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `python -m pytest -q`
Expected: 53 passed (model change is additive).

- [ ] **Step 3: Commit**

```bash
git add src/agentic_any_search_mcp/models.py
git commit -m "feat(models): add IterationRecord and CandidateRecord.iterations"
```

---

## Task 2: Relax run_verifier and record iterations

**Files:**
- Modify: `src/agentic_any_search_mcp/runtime.py:855-940`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_runtime_unit.py`:

```python
def test_run_verifier_works_without_submit_and_records_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_agent_type": "AnySearchAgent",
            "worker_timeout_seconds": 120,
            "worker_local_verifier_max_runs": 3,
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    scores = [0.4, 0.7, 0.9]

    def fake_run(*args, **kwargs):
        score = scores.pop(0)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f'{{"combined_score": {score}, "valid": true}}\n',
            stderr="",
        )

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)

    # No submit_candidate call — run_verifier should work directly on created status.
    for expected_score in [0.4, 0.7, 0.9]:
        report = runtime.run_verifier(
            run_id, candidate_id, agent_session_id=session.agent_session_id
        )
        assert report.aggregate_score == expected_score

    record = runtime._load_candidate_record(run_id, candidate_id)
    assert len(record.iterations) == 3
    assert [it.score for it in record.iterations] == [0.4, 0.7, 0.9]
    assert [it.iteration for it in record.iterations] == [1, 2, 3]
    # score_report holds the latest
    assert record.score_report.aggregate_score == 0.9

    session = runtime._load_agent_session_by_id(session.agent_session_id)
    assert session.counters["verifier_runs"] == 3
    assert session.status == "finalizing"  # max_verifier_runs=3 reached


def test_list_iterations_returns_all_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_local_verifier_max_runs": 5,
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.5, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)
    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)

    iterations = runtime.list_iterations(run_id, candidate_id)
    assert len(iterations) == 2
    assert iterations[0]["iteration"] == 1
    assert iterations[1]["iteration"] == 2
    assert all(it["agent_session_id"] == session.agent_session_id for it in iterations)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime_unit.py::test_run_verifier_works_without_submit_and_records_iterations tests/test_runtime_unit.py::test_list_iterations_returns_all_records -v`
Expected: FAIL.

- [ ] **Step 3: Modify `run_verifier` in `runtime.py:855-940`**

Replace the method. Key changes:
- Relax status check to allow `created`
- Always detect changed files (was submit's job)
- Append to `record.iterations`
- Keep all existing behavior (counter, finalize, score_report update)

```python
def run_verifier(
    self,
    run_id: str,
    candidate_id: str,
    scope: Literal["process", "promotion"] = "process",
    agent_session_id: str | None = None,
) -> ScoreReport:
    run = self._load_run(run_id)
    frozen = self._load_frozen_spec(run.frozen_spec_id)
    record = self._load_candidate_record(run_id, candidate_id)
    if record.status not in {"created", "submitted", "evaluated"}:
        raise RuntimeError(
            f"cannot verify candidate in status {record.status}"
        )

    session = None
    if agent_session_id:
        session = self._load_agent_session_by_id(agent_session_id, run_id=run_id)
        if session.candidate_id != candidate_id:
            raise ValueError(
                "artifact agent_session_id does not belong to this candidate"
            )
        if session.status in TERMINAL_AGENT_SESSION_STATUSES:
            raise RuntimeError(
                f"cannot verify from terminal agent session {agent_session_id}"
            )

    # Detect workspace changes here (was submit_candidate's job).
    detected_changed = self._detect_changed_files(Path(run.source_path), record.task.workspace)
    touched_denied = any(
        path_matches(path, frozen.spec.edit_surface.deny) for path in detected_changed
    )
    outside_allowed = any(
        not path_matches(path, frozen.spec.edit_surface.allow) for path in detected_changed
    )
    if (
        frozen.spec.edit_surface.max_file_changes is not None
        and len(detected_changed) > frozen.spec.edit_surface.max_file_changes
    ):
        outside_allowed = True

    record.detected_changed_files = detected_changed
    record.touched_denied_files = touched_denied
    record.changed_outside_allowed = outside_allowed

    old_state = run.state
    run.state = RunState.EVALUATING
    self._write_run(run)

    try:
        precheck = self._precheck_candidate(frozen, record)
        if precheck is not None:
            report = precheck
        else:
            commands = (
                frozen.spec.process_verifiers
                if scope == "process"
                else frozen.spec.promotion_verifiers
            )
            if not commands:
                commands = frozen.spec.process_verifiers
            report = self._run_commands(run, frozen, record, commands, scope)

        record.status = "evaluated"
        record.score_report = report
        record.iterations.append(
            IterationRecord(
                iteration=len(record.iterations) + 1,
                agent_session_id=agent_session_id,
                score=report.aggregate_score,
                failure_class=(
                    next(
                        (r.failure_class for r in report.verifier_results if r.failure_class),
                        None,
                    )
                ),
                summary="",
                changed_files=detected_changed,
                touched_denied_files=touched_denied,
                changed_outside_allowed=outside_allowed,
                metrics={
                    r.name: r.metrics for r in report.verifier_results
                },
                created_at=utc_timestamp(),
            )
        )
        self._write_candidate_record(run_id, record)
        self._update_best_seen(run, frozen.spec, report)
        run.candidates_evaluated = len(
            [r for r in self._load_candidate_records(run_id) if r.status == "evaluated"]
        )
        if run.state == RunState.EVALUATING:
            run.state = RunState.RUNNING if old_state != RunState.READY_TO_PROMOTE else old_state
        self._write_run(run)

        if session is not None and agent_session_id is not None:
            counters = dict(session.counters)
            counters["verifier_runs"] = counters.get("verifier_runs", 0) + 1
            updated = session.model_copy(
                update={"counters": counters, "updated_at": utc_timestamp()}
            )
            self._write_agent_session(updated)
            if (
                updated.budget.max_verifier_runs is not None
                and counters["verifier_runs"] >= updated.budget.max_verifier_runs
            ):
                self.request_agent_finalize(
                    agent_session_id, "max_verifier_runs reached"
                )

        return report
    except Exception:
        run.state = RunState.FAILED
        self._write_run(run)
        raise
```

Make sure to import `IterationRecord` at the top of runtime.py.

- [ ] **Step 4: Add `list_iterations` method to FileSearchRuntime**

Add after `list_history` (around line 290):

```python
def list_iterations(
    self,
    run_id: str,
    candidate_id: str,
) -> list[dict[str, Any]]:
    record = self._load_candidate_record(run_id, candidate_id)
    return [it.model_dump(mode="json") for it in record.iterations]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime_unit.py -v -k "iteration or run_verifier"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_any_search_mcp/runtime.py tests/test_runtime_unit.py
git commit -m "feat(runtime): run_verifier records iterations and works without submit"
```

---

## Task 3: Expose list_iterations via tools and server

**Files:**
- Modify: `src/agentic_any_search_mcp/tools.py`
- Modify: `src/agentic_any_search_mcp/server.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Add tools passthrough**

In `tools.py`, add after `search_run_verifier`:

```python
def search_list_iterations(
    self,
    run_id: str,
    candidate_id: str,
) -> list[dict[str, Any]]:
    return self.runtime.list_iterations(run_id, candidate_id)
```

- [ ] **Step 2: Add to test_tools.py**

In `test_search_tools_delegate_runtime_calls_with_models`, add assertion:

```python
assert tools.search_list_iterations("run_1", "c001") == runtime.list_iterations.return_value
runtime.list_iterations.assert_called_once_with("run_1", "c001")
```

And in the Mock setup at top of test:

```python
runtime.list_iterations.return_value = [{"iteration": 1, "score": 0.5}]
```

- [ ] **Step 3: Run tools tests**

Run: `python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/agentic_any_search_mcp/tools.py tests/test_tools.py
git commit -m "feat(tools): expose search_list_iterations"
```

---

## Task 4: Surface iterations in get_agent_context

**Files:**
- Modify: `src/agentic_any_search_mcp/runtime.py:487-519` (`get_agent_context`)

- [ ] **Step 1: Add iterations to context**

In `get_agent_context`, add after the `observations` key:

```python
"iterations": (
    self.list_iterations(session.run_id, session.candidate_id)
    if session.candidate_id else []
),
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q`
Expected: 53+ passed.

- [ ] **Step 3: Commit**

```bash
git add src/agentic_any_search_mcp/runtime.py
git commit -m "feat(runtime): surface iterations in get_agent_context"
```

---

## Task 5: Update AnySearchAgent.md and SKILL.md

**Files:**
- Modify: `.opencode/agents/AnySearchAgent.md`
- Modify: `.opencode/skills/search/SKILL.md`

- [ ] **Step 1: Simplify AnySearchAgent.md Iteration Loop**

In `.opencode/agents/AnySearchAgent.md`, replace the Verifier Discipline + Iteration Loop sections:

```markdown
## Verifier Discipline

All scoring goes through MCP. Each call scores the current workspace state, appends to the candidate's iteration history, and increments your verifier_runs counter.

1. Call `search-runtime_search_run_verifier(run_id=context.run_id, candidate_id=context.candidate_id, scope="process", agent_session_id=context.agent_session_id)`.
2. The runtime detects changed files, runs the verifier command, and appends an `IterationRecord` to the candidate. You do not need to call `search_submit_candidate` first.
3. The returned `ScoreReport.aggregate_score` is your score for this iteration. The `iteration` field in `get_agent_context` shows all your previous attempts.
4. Reaching `max_verifier_runs` triggers finalize. Plan iterations accordingly.
5. Never run the verifier command directly via bash. Never write your own scorer.
6. Static non-scoring checks (`python -m py_compile`) are always allowed.

## Iteration Loop

```text
read context -> objective, allowed_files, history, observations, iterations
git init baseline in workspace
write .tmp/results.tsv with header: iter \t score \t status \t hypothesis

while steps_remaining and verifier_runs_remaining and time_remaining:
    decide next hypothesis based on:
      - your previous iterations in context.iterations
      - context.history (top scored candidates across the run)
      - context.observations (cross-session findings)
    edit allowed_files to implement the hypothesis
    report = search_run_verifier(..., agent_session_id=self)
    score = report.aggregate_score
    if score improved over last iteration:
        git commit -m "iter N: score=X"
        append results.tsv row with status=keep
    else:
        git reset --hard HEAD~1
        append results.tsv row with status=discard
    (optional) search_publish_observation(summary, evidence, next_ideas)

before deadline:
    ensure best-so-far workspace state is in place (last iteration's code if keep, or revert)
    search_finish_agent_session(status="completed",
                                summary="best score X over N iterations",
                                result={best_score, best_iter, total_iterations})
```
```

- [ ] **Step 2: Update SKILL.md Step 6 (subagent contract)**

In `.opencode/skills/search/SKILL.md`, replace the "Subagent Autoresearch Contract" section:

```markdown
### Step 6: Subagent Autoresearch Contract

The subagent receives only `agent_session_id` and a candidate idea. It then:

1. Calls `search-runtime_search_get_agent_context(agent_session_id)` to read authoritative `run_id`, `candidate_id`, `workspace`, `allowed_files`, `denied_files`, `budget`, `history`, `observations`, and `iterations` (its own previous attempts).
2. Runs an autoresearch loop inside `workspace`: edit allowed files → `search-runtime_search_run_verifier(..., agent_session_id=...)` → read ScoreReport → `git commit` (improvement) or `git reset --hard HEAD~1` (regression). Each verifier call appends to the candidate's iteration history; no separate `submit_candidate` step is needed.
3. Maintains `workspace/.tmp/results.tsv` as its private iteration log.
4. Calls `search-runtime_search_finish_agent_session(agent_session_id, status, summary, result)` when done, with the best score and iteration count.

You do not pass numeric score targets, baseline scores, or local-verification requests in the worker prompt. The worker reads its own verifier output and decides next steps.
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest -q`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add .opencode/agents/AnySearchAgent.md .opencode/skills/search/SKILL.md
git commit -m "docs: simplify iteration loop — single run_verifier call per iteration"
```

---

## Task 6: Validation

- [ ] **Step 1: Run full pytest**

Run: `python -m pytest -q`
Expected: All pass.

- [ ] **Step 2: Run compileall**

Run: `python -m compileall -q src tests`
Expected: No errors.

- [ ] **Step 3: Commit if fixups needed**

If any fixups:

```bash
git add -A
git commit -m "fix: address validation issues"
```
