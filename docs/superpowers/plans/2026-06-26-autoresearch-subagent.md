# Autoresearch-Style Subagent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the AnySearchAgent from a single-shot candidate producer into an autoresearch-style autonomous looper that self-iterates inside its workspace, while keeping MCP as the sole scoring authority.

**Architecture:** Three changes layered together. (1) Runtime: `run_verifier` accepts an optional `agent_session_id` so per-session verifier-run budget is enforced at the scoring boundary rather than via opt-in step recording. (2) Subagent prompt: remove git deny-list (so it can `git init`/commit/reset inside its workspace), explicitly allow `search_run_verifier`, and document the autoresearch iteration loop. (3) Orchestrator/Skill: main agent stops micromanaging candidate execution and only dispatches sessions + reacts to terminal events + reallocates the next batch.

**Tech Stack:** Python 3, Pydantic, FastMCP, pytest, OpenCode agent/skill markdown.

---

## File Structure

- Modify: `src/agentic_any_search_mcp/runtime.py` — `run_verifier` signature + body, `_create_candidate_task` instruction list
- Modify: `src/agentic_any_search_mcp/tools.py` — `search_run_verifier` passthrough param
- Modify: `.opencode/agents/AnySearchAgent.md` — full prompt rewrite (autoresearcher)
- Modify: `.opencode/agents/search-orchestrator.md` — main = dispatcher
- Modify: `.opencode/skills/search/SKILL.md` — workflow section rewrite
- Modify: `tests/test_runtime_unit.py` — new test for `run_verifier` budget enforcement; update instruction-string assertions
- Modify: `tests/test_opencode_assets.py` — update destructive-command deny assertions

No new files, no model changes. `AgentSessionBudget.max_verifier_runs` already exists.

---

## Task 1: `run_verifier` enforces per-session verifier-run budget

**Files:**
- Modify: `src/agentic_any_search_mcp/runtime.py:855-899`
- Test: `tests/test_runtime_unit.py` (append new test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_unit.py`:

```python
def test_run_verifier_increments_session_verifier_runs_and_enforces_budget(
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
            "worker_local_verifier_max_runs": 2,
        },
        max_candidates=1,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=1)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    candidate_id = tasks[0].candidate_id
    session = runtime.start_agent_session(run_id, candidate_id, {"goal": "iterate"})

    runtime.submit_candidate(
        run_id,
        candidate_id,
        ArtifactBundle(
            candidate_id=candidate_id,
            status="patch_ready",
            agent_session_id=session.agent_session_id,
        ),
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"combined_score": 0.5, "valid": true}\n',
            stderr="",
        )

    monkeypatch.setattr("agentic_any_search_mcp.runtime.subprocess.run", fake_run)

    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)
    session = runtime._load_agent_session_by_id(session.agent_session_id)
    assert session.counters["verifier_runs"] == 1
    assert session.status == "running"

    runtime.run_verifier(run_id, candidate_id, agent_session_id=session.agent_session_id)
    session = runtime._load_agent_session_by_id(session.agent_session_id)
    assert session.counters["verifier_runs"] == 2
    assert session.status == "finalizing"


def test_run_verifier_rejects_mismatched_agent_session(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runtime = FileSearchRuntime(tmp_path / ".search")
    spec = spec_with_strategy(
        project,
        {
            "name": "independent_branches",
            "worker_mode": "agent-session-pool",
            "worker_local_verifier_max_runs": 2,
        },
        max_candidates=2,
    )
    frozen = runtime.freeze_spec(spec, [project / "evaluator.py"])
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    other_session = runtime.start_agent_session(run_id, tasks[1].candidate_id, {"goal": "other"})
    runtime.submit_candidate(
        run_id,
        tasks[0].candidate_id,
        ArtifactBundle(candidate_id=tasks[0].candidate_id, status="patch_ready"),
    )

    with pytest.raises(ValueError, match="agent_session_id does not belong"):
        runtime.run_verifier(
            run_id,
            tasks[0].candidate_id,
            agent_session_id=other_session.agent_session_id,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime_unit.py::test_run_verifier_increments_session_verifier_runs_and_enforces_budget tests/test_runtime_unit.py::test_run_verifier_rejects_mismatched_agent_session -v`
Expected: FAIL with `TypeError: run_verifier() got an unexpected keyword argument 'agent_session_id'`

- [ ] **Step 3: Modify `run_verifier` in `runtime.py:855-899`**

Replace the method signature and add session-accounting block. The full new method:

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
    if record.status not in {"submitted", "evaluated"}:
        raise RuntimeError("candidate must be submitted before verification")

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
        self._write_candidate_record(run_id, record)
        self._update_best_seen(run, frozen.spec, report)
        run.candidates_evaluated = len(
            [r for r in self._load_candidate_records(run_id) if r.status == "evaluated"]
        )
        if run.state == RunState.EVALUATING:
            run.state = RunState.RUNNING if old_state != RunState.READY_TO_PROMOTE else old_state
        self._write_run(run)

        if session is not None:
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

- [ ] **Step 4: Update `tools.py:238-245` passthrough**

```python
def search_run_verifier(
    self,
    run_id: str,
    candidate_id: str,
    scope: str = "process",
    agent_session_id: str | None = None,
) -> dict[str, Any]:
    report = self.runtime.run_verifier(
        run_id,
        candidate_id,
        scope=scope,  # type: ignore[arg-type]
        agent_session_id=agent_session_id,
    )
    return report.model_dump(mode="json")
```

- [ ] **Step 5: Run new tests + existing run_verifier tests to verify they pass**

Run: `python -m pytest tests/test_runtime_unit.py -v -k "run_verifier or agent_session"`
Expected: PASS for all matched tests, including the two new ones.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_any_search_mcp/runtime.py src/agentic_any_search_mcp/tools.py tests/test_runtime_unit.py
git commit -m "feat(runtime): run_verifier enforces per-session verifier-run budget

When called with agent_session_id, increments the session's verifier_runs
counter and triggers finalize when max_verifier_runs is reached. Validates
session provenance against the candidate."
```

---

## Task 2: Update candidate-task instructions for autoresearch semantics

**Files:**
- Modify: `src/agentic_any_search_mcp/runtime.py:1500-1541` (instruction list in `_create_candidate_task`)
- Modify: `tests/test_runtime_unit.py:178-237` (instruction-string assertions)

- [ ] **Step 1: Update instruction assertions in the test first**

In `tests/test_runtime_unit.py`, modify `test_agent_session_pool_mode_is_planned_and_required_for_submission`:

Replace the block of `assert any(...)` lines (around line 203-212) with:

```python
    assert plan.worker_policy["mode"] == "agent-session-pool"
    assert plan.worker_policy["subagent_type"] == "AnySearchAgent"
    assert plan.worker_policy["timeout_seconds"] == 120
    assert plan.worker_policy["local_verifier_max_runs"] == 3
    assert plan.worker_policy["requires_agent_session"] is True
    assert tasks[0].strategy_metadata["worker_mode"] == "agent-session-pool"
    assert any(
        "worker_mode=agent-session-pool" in instruction
        for instruction in tasks[0].instructions
    )
    assert any("agent_session_id" in instruction for instruction in tasks[0].instructions)
    assert any("subagent_type='AnySearchAgent'" in instruction for instruction in tasks[0].instructions)
    assert any("120 seconds" in instruction for instruction in tasks[0].instructions)
    assert any(
        "search_run_verifier" in instruction for instruction in tasks[0].instructions
    )
    assert any(
        "git init" in instruction for instruction in tasks[0].instructions
    )
    assert any(
        "iteration log" in instruction for instruction in tasks[0].instructions
    )
```

(Removed: `"bounded and fast"`, `"score targets"`, `"at most 3 times"` assertions.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_unit.py::test_agent_session_pool_mode_is_planned_and_required_for_submission -v`
Expected: FAIL on the new assertions.

- [ ] **Step 3: Rewrite the instruction block in `_create_candidate_task`**

Replace lines `runtime.py:1500-1541` (the `instructions = [...]` block plus the conditional appends) with:

```python
        instructions = [
            "Work only inside this candidate workspace.",
            "Use this workspace's .tmp/ directory for notes, scratch drafts, and your local iteration log (e.g. results.tsv).",
            "Do not use /tmp, home directories, or paths outside the candidate workspace for candidate work.",
            "Modify only files listed in allowed_files; never touch denied_files or frozen verifier artifacts.",
            "Do not delete, move, or clean files; destructive commands such as rm, mv, rmdir, unlink, trash, and find -delete are forbidden.",
            "You may git init, git add, git commit, git reset, git restore, and git checkout INSIDE this workspace to advance and revert iterations.",
            "All scoring must go through search-runtime_search_run_verifier; do not run the process_verifiers command directly via bash, and do not write your own scorer.",
        ]
        if plan.worker_policy.get("requires_agent_session"):
            instructions.append(
                "This run is configured with worker_mode=agent-session-pool; candidate execution must be tracked by search_start_agent_session and supervised with search_wait_agent_events."
            )
            instructions.append(
                f"Agent session wall-clock budget defaults to {plan.worker_policy['timeout_seconds']} seconds and is capped by the remaining run budget."
            )
            instructions.append(
                "Candidate artifacts must include the producing agent_session_id."
            )
            instructions.append(
                "Do not launch long-running foreground Task calls when supervision or abort is required; run workers as background/managed sessions so the supervisor can wait, inspect status, and abort."
            )
            local_runs = plan.worker_policy["local_verifier_max_runs"]
            if local_runs == 0:
                instructions.append(
                    "Local verifier budget is 0; you may not call search_run_verifier yourself. Edit, submit once, and finish."
                )
            else:
                instructions.append(
                    f"You may call search_run_verifier (with your agent_session_id) at most {local_runs} times. Each call increments your verifier_runs counter; reaching the cap triggers finalize."
                )
                instructions.append(
                    "Inside the workspace, git init and use git commit to mark iterations that improved, and git reset --hard HEAD~1 to discard iterations that regressed."
                )
                instructions.append(
                    "Maintain an iteration log (workspace/.tmp/results.tsv or similar) recording each attempt's hypothesis, score, and outcome."
                )
            if plan.worker_policy.get("subagent_type"):
                instructions.append(
                    f"Use subagent_type={plan.worker_policy['subagent_type']!r} for the managed/background agent session."
                )
        instructions.extend(proposal.instructions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime_unit.py::test_agent_session_pool_mode_is_planned_and_required_for_submission -v`
Expected: PASS.

- [ ] **Step 5: Run full test suite to catch downstream breakage**

Run: `python -m pytest -q`
Expected: All tests pass except possibly `test_any_search_agent_denies_destructive_shell_commands` (that one is fixed in Task 3).

- [ ] **Step 6: Commit**

```bash
git add src/agentic_any_search_mcp/runtime.py tests/test_runtime_unit.py
git commit -m "feat(runtime): candidate instructions describe autoresearch iteration loop

Instructions now tell workers to git init/commit/reset inside the workspace,
route all scoring through search_run_verifier, and maintain a local
iteration log. Local verifier budget semantics: 0 means no self-verify,
N>0 means up to N MCP verifier calls before finalize."
```

---

## Task 3: Rewrite AnySearchAgent.md as autoresearcher

**Files:**
- Modify: `.opencode/agents/AnySearchAgent.md`
- Modify: `tests/test_opencode_assets.py:78-94` (destructive-command assertions)

- [ ] **Step 1: Update destructive-command deny test**

In `tests/test_opencode_assets.py`, replace `test_any_search_agent_denies_destructive_shell_commands`:

```python
def test_any_search_agent_denies_destructive_shell_commands() -> None:
    agent = (ROOT / ".opencode" / "agents" / "AnySearchAgent.md").read_text(
        encoding="utf-8"
    )

    assert "bash:" in agent
    for pattern in [
        '"rm*": deny',
        '"mv*": deny',
        '"rmdir*": deny',
        '"unlink*": deny',
        '"trash*": deny',
        '"find*delete*": deny',
    ]:
        assert pattern in agent
    # git reset/restore/checkout/clean must NOT be denied — worker uses them
    # to advance/revert iterations inside its workspace.
    for pattern in [
        '"git reset*": deny',
        '"git restore*": deny',
        '"git checkout*": deny',
        '"git clean*": deny',
    ]:
        assert pattern not in agent
```

Also append a new test that checks for autoresearcher content:

```python
def test_any_search_agent_documents_autoresearch_loop() -> None:
    agent = (ROOT / ".opencode" / "agents" / "AnySearchAgent.md").read_text(
        encoding="utf-8"
    )

    assert "## Iteration Loop" in agent
    assert "git init" in agent
    assert "search_run_verifier" in agent
    assert "results.tsv" in agent
    assert "agent_session_id" in agent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_opencode_assets.py::test_any_search_agent_denies_destructive_shell_commands tests/test_opencode_assets.py::test_any_search_agent_documents_autoresearch_loop -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite `.opencode/agents/AnySearchAgent.md`**

Full new content:

```markdown
---
name: AnySearchAgent
description: Executes one Agentic Search candidate as an autonomous autoresearch loop inside a managed MCP agent session.
mode: subagent
temperature: 0.2
steps: 50

permission:
  task: deny
  todowrite: deny
  bash:
    "rm*": deny
    "*/rm*": deny
    "mv*": deny
    "rmdir*": deny
    "unlink*": deny
    "trash*": deny
    "find*delete*": deny
---

# AnySearchAgent

You execute exactly one candidate as an autonomous autoresearch-style loop, bounded by step count, verifier-call budget, and a wall-clock deadline. You self-direct hypotheses, self-verify through MCP, and self-record an iteration log.

## Required Input

The main agent must provide only an `agent_session_id`. Your first action is:

```text
search-runtime_search_get_agent_context(agent_session_id="<agent_session_id>")
```

Treat the returned MCP context as authoritative. If the user prompt, main-agent directive, and MCP context disagree, follow the MCP context and report the conflict in your final session summary.

Use `context.run_id`, `context.candidate_id`, `context.workspace`, and `context.candidate_task` for all file work and submission. Do not trust or reuse any `run_id`, `candidate_id`, or workspace path from the launch prompt.

Read `context.budget.deadline_at`, `context.budget.max_steps`, `context.budget.max_tool_calls`, and `context.budget.max_verifier_runs`. Treat the deadline as a hard delivery deadline for the candidate artifact.

## Workspace Rules

1. Work only in `context.workspace`.
2. Use `context.workspace/.tmp/` for notes, scratch drafts, and your local iteration log (e.g., `results.tsv`).
3. Do not use `/tmp`, home directories, or paths outside the candidate workspace for candidate work.
4. Modify only files listed in `context.candidate_task.allowed_files`.
5. Do not modify files listed in `context.candidate_task.denied_files` or any frozen verifier artifact.
6. Do not edit the main source workspace.

## Workspace Git Workflow

You are encouraged to use git inside your workspace to track iterations:

1. On first iteration: `cd context.workspace && git init && git add -A && git commit -m "baseline"`.
2. After each successful iteration: `git add -A && git commit -m "iter N: <hypothesis>, score=<x>"`.
3. After a regression or crash: `git reset --hard HEAD~1` (or `git reset --hard <last-good-commit>`).
4. `git restore`, `git checkout`, and `git clean` are allowed **inside the workspace only**. They are forbidden outside the workspace.

Git operations must never leave the workspace directory.

## Verifier Discipline

All scoring must go through MCP:

1. Call `search-runtime_search_submit_candidate(run_id=context.run_id, candidate_id=context.candidate_id, artifact={...})` to unlock the verifier. You may call this multiple times; each call refreshes the workspace snapshot the runtime scores against.
2. Call `search-runtime_search_run_verifier(run_id=context.run_id, candidate_id=context.candidate_id, scope="process", agent_session_id=context.agent_session_id)` to score the current workspace state.
3. The runtime increments your `verifier_runs` counter and triggers finalize when `max_verifier_runs` is reached. Plan your iterations accordingly.
4. Never run the verifier command (`context.candidate_task` process_verifiers) directly via bash.
5. Never write your own scorer, evaluator, or benchmark harness. The MCP verifier is the single source of truth for scores.
6. Static non-scoring checks (`python -m py_compile`, syntax checks) are always allowed.

If `context.budget.max_verifier_runs` is 0, you may not call `search_run_verifier`. Submit once with your best implementation and finish.

## Iteration Loop

Run an autoresearch-style loop inside your session:

```text
read context → understand objective, allowed_files, history, observations
git init baseline in workspace
write .tmp/results.tsv with header: iter \t score \t status \t hypothesis

while steps_remaining and verifier_runs_remaining and time_remaining:
    decide next hypothesis based on:
      - your own previous iteration log
      - context.history (top scored candidates across the run)
      - context.observations (cross-session findings)
    edit allowed_files to implement the hypothesis
    search_submit_candidate(artifact={candidate_id, agent_session_id,
                            status:"patch_ready",
                            summary:"iter N: <hypothesis>", next_ideas:[]})
    search_run_verifier(..., agent_session_id=self)
    read returned ScoreReport.aggregate_score and failure_class
    if improved:
        git commit -m "iter N: score=X"
        append row to results.tsv with status=keep
    else:
        git reset --hard HEAD~1
        append row to results.tsv with status=discard
    (optional) search_publish_observation(summary, evidence, next_ideas)
      when you find something surprising worth sharing with peer sessions

before deadline:
    ensure best-so-far workspace state is in place
    search_submit_candidate(artifact={..., summary:"best score X over N iterations"})
    search_finish_agent_session(status="completed",
                                summary="best score X, tried N iterations",
                                result={best_score, best_iter, total_iterations})
```

## Session Rules

1. Status updates (`search-runtime_search_update_agent_status`) are optional heartbeats. Use them sparingly after meaningful progress. Never retry a failed status update.
2. Call `search-runtime_search_record_agent_step(steps_delta=1)` after each MCP tool call if you want fine-grained step tracking; otherwise rely on the OpenCode `steps` budget.
3. If you discover reusable evidence or a next idea worth surfacing to peers, publish it with `search-runtime_search_publish_observation`.
4. If the deadline is near, deliver the best-so-far artifact with an honest summary. Do not continue exploration past the deadline.
5. Finish by calling `search-runtime_search_finish_agent_session(agent_session_id, status, summary, result)`.

## Destructive Commands

Forbidden: `rm`, `mv`, `rmdir`, `unlink`, `trash`, `find -delete`. Do not bypass these via Python, Node, or shell scripts.

Allowed inside workspace: `git init`, `git add`, `git commit`, `git reset --hard`, `git restore`, `git checkout`, `git clean`.

## Final Submission

The artifact you submit at the end must reflect the best workspace state you achieved:

```json
{
  "candidate_id": "context.candidate_id",
  "agent_session_id": "context.agent_session_id",
  "status": "patch_ready",
  "summary": "best score X over N iterations; key winning change was ...",
  "next_ideas": ["concrete follow-up hypothesis for another session"]
}
```

Call `search-runtime_search_submit_candidate` with this artifact, then `search-runtime_search_finish_agent_session`. Do not promote, copy files into the source workspace, or modify verifier files.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_opencode_assets.py -v`
Expected: PASS for all tests including the new one and updated destructive-command test.

- [ ] **Step 5: Commit**

```bash
git add .opencode/agents/AnySearchAgent.md tests/test_opencode_assets.py
git commit -m "feat(agent): AnySearchAgent becomes autoresearch-style looper

- Remove git reset/restore/checkout/clean deny (allowed in workspace)
- Bump steps 12 -> 50
- Add Iteration Loop, Workspace Git Workflow, Verifier Discipline sections
- All scoring routed through search_run_verifier with agent_session_id"
```

---

## Task 4: Trim search-orchestrator.md to dispatcher-only

**Files:**
- Modify: `.opencode/agents/search-orchestrator.md`

- [ ] **Step 1: Read current orchestrator file**

Run: `head -50 .opencode/agents/search-orchestrator.md`
Confirm current rules so we know which to drop.

- [ ] **Step 2: Rewrite `.opencode/agents/search-orchestrator.md`**

Full new content:

```markdown
---
name: search-orchestrator
description: Search Runtime dispatcher for verifiable multi-candidate tasks. Spawns autoresearcher subagents, supervises terminal events, and reallocates the next batch.
mode: primary
temperature: 0.1

tools:
  read: true
  edit: true
  bash: true
  skill: true

skills:
  - search
---

# Search Orchestrator

You are a dispatcher for Agentic Search. The runtime owns state, workspaces, verifier execution, and budget enforcement. Each candidate is executed by an autonomous AnySearchAgent subagent running an autoresearch-style loop inside its own workspace.

Your job is to allocate resources and react to terminal events, not to micromanage candidate execution.

Rules:

1. Freeze a SearchSpec before candidate execution.
2. Keep all edits inside runtime-provided workspaces; do not touch the main source workspace.
3. Spawn one AnySearchAgent per candidate via `search_start_agent_session` + `Task(subagent_type="AnySearchAgent", background=true)`.
4. The Task prompt must contain only `agent_session_id` and a human-readable candidate idea. Do not hard-code `run_id`, `candidate_id`, or workspace paths into the worker prompt.
5. Wait for terminal events via `search_wait_agent_events`; do not poll worker state synchronously or block on foreground Task calls.
6. When a session terminates, run `search_run_verifier` yourself (without `agent_session_id`) to confirm the final score against the best-so-far workspace state.
7. Reallocate the next batch when slots free and budget remains. Read recent observations via `search_list_observations` to inform the next plan.
8. Select, report, and promote only through runtime APIs.
9. OpenCode managed subagents require the parent process to be started with `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` or `OPENCODE_EXPERIMENTAL=true`. Each Task must include `background: true`.
10. Do not pass a Task-level `timeout`. Treat `worker_timeout_seconds` as an MCP supervisor deadline enforced via `search_wait_agent_events`, finalize, and abort.
11. Keep updates concise. Always report `run_id`, selected candidate, score, and report path.
```

- [ ] **Step 3: Run opencode-asset tests to verify nothing broke**

Run: `python -m pytest tests/test_opencode_assets.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add .opencode/agents/search-orchestrator.md
git commit -m "feat(orchestrator): dispatcher-only — spawn, supervise, reallocate

Drop micromanagement rules (local verifier budget, score-target framing,
subagent-reported-score distrust). Main agent trusts the runtime verifier
and focuses on resource allocation across autoresearcher sessions."
```

---

## Task 5: Update SKILL.md workflow section

**Files:**
- Modify: `.opencode/skills/search/SKILL.md`

- [ ] **Step 1: Read current SKILL.md sections that need editing**

Run: `grep -n "^### Step" .opencode/skills/search/SKILL.md`
Identify sections: Step 5 (Supervise Agent Sessions) and Step 6 (Subagent Contract) need rewriting.

- [ ] **Step 2: Replace Step 5 and Step 6 in `.opencode/skills/search/SKILL.md`**

Find the `### Step 5: Supervise Agent Sessions` heading through the end of `### Step 6: Subagent Contract` section. Replace with:

```markdown
### Step 5: Dispatch Autoresearcher Sessions

For `worker_policy.mode == "agent-session-pool"`:

1. Start at most `budget.max_parallel` sessions.
2. For each candidate, call `search-runtime_search_start_agent_session(run_id, candidate_id, directive, budget)` to get `agent_session_id`.
3. Launch the subagent with `Task(subagent_type="AnySearchAgent", background=true, prompt="<agent_session_id>; candidate idea: <one paragraph>")`.
4. In OpenCode, the Task call must include `background: true`. This requires `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true`. No `timeout` field exists on Task.
5. Each AnySearchAgent runs an autoresearch-style loop inside its workspace: it self-iterates, calls `search_run_verifier` with its own `agent_session_id`, tracks git commits, and maintains a local `results.tsv`. You do not supervise iteration-level progress.
6. Enter a supervisor loop with `search-runtime_search_wait_agent_events(run_id, timeout_seconds=<poll window>, since_event_id=<last seen>)` to wake on terminal events.
7. When a session terminates (completed / failed / aborted / timed_out), run `search-runtime_search_run_verifier(run_id, candidate_id, "process")` yourself to confirm the final score.
8. If slots free and candidate budget remains, plan and start the next batch. Read `search-runtime_search_list_observations(run_id, top_n=20)` to inform the next plan when useful.
9. On run deadline, call `search-runtime_search_abort_all_agent_sessions(run_id)` before reporting.

Supervisor loop sketch:

```text
last_event_id = null
pending_candidates = [...]
while pending_candidates or active_sessions:
  while pending_candidates and active_count < max_parallel:
    session = search_start_agent_session(...)
    Task(subagent_type="AnySearchAgent", background=true,
         prompt=f"agent_session_id={session.agent_session_id}; {idea}")
    active_count += 1

  wait = search_wait_agent_events(run_id, timeout_seconds=300, since_event_id=last_event_id)
  last_event_id = wait.last_event_id

  if wait.run_deadline_reached:
    search_abort_all_agent_sessions(run_id, "run budget exhausted")
    break

  for terminal event in wait.events:
    search_run_verifier(run_id, event.candidate_id, "process")  # main-side final confirm
    active_count = wait.active_count

  if not pending_candidates and budget_remaining and active_count == 0:
    observations = search_list_observations(run_id, top_n=20)
    plan = search_plan_next(run_id, requested_k=k)
    tasks = search_start_batch(run_id, plan.plan_id)
    pending_candidates = [t.candidate_id for t in tasks]
```

### Step 6: Subagent Autoresearch Contract

The subagent receives only `agent_session_id` and a candidate idea. It then:

1. Calls `search-runtime_search_get_agent_context(agent_session_id)` to read authoritative `run_id`, `candidate_id`, `workspace`, `allowed_files`, `denied_files`, `budget`, `history`, and `observations`.
2. Runs an autoresearch loop inside `workspace`: edit allowed files → `search-runtime_search_submit_candidate` → `search-runtime_search_run_verifier(..., agent_session_id=...)` → read ScoreReport → `git commit` (improvement) or `git reset --hard HEAD~1` (regression).
3. Maintains `workspace/.tmp/results.tsv` as its private iteration log.
4. Calls `search-runtime_search_finish_agent_session(agent_session_id, status, summary, result)` when done, with the best score and iteration count.

You do not pass numeric score targets, baseline scores, or local-verification requests in the worker prompt. The worker reads its own verifier output and decides next steps.
```

Also: find the line in `### Required Discipline` section that says "Do not accept subagent-reported scores. Always call `search_run_verifier`." and update to:

```markdown
7. Subagents self-verify via `search_run_verifier` with their own `agent_session_id`. After session termination, call `search_run_verifier` yourself (without `agent_session_id`) to confirm the final score against the best-so-far workspace state.
```

And in `### Step 4: Plan And Start Candidate Workspaces`, the line `Each returned `CandidateTask` owns an isolated workspace. Candidate work must stay inside that workspace and only modify allowed files.` stays unchanged.

- [ ] **Step 3: Run all opencode-asset tests**

Run: `python -m pytest tests/test_opencode_assets.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add .opencode/skills/search/SKILL.md
git commit -m "feat(skill): document autoresearcher dispatch workflow

Step 5/6 rewritten: main agent dispatches AnySearchAgent sessions and
reacts to terminal events; subagent owns iteration loop, self-verifies
through MCP, and maintains local results.tsv."
```

---

## Task 6: Full-suite validation

**Files:** none (validation only)

- [ ] **Step 1: Run full pytest suite**

Run: `python -m pytest -q`
Expected: All tests pass.

- [ ] **Step 2: Run compile check**

Run: `python -m compileall src tests`
Expected: No errors.

- [ ] **Step 3: Manual smoke check of agent markdown**

Run: `head -25 .opencode/agents/AnySearchAgent.md`
Verify: deny list contains `rm`, `mv`, `rmdir`, `unlink`, `trash`, `find*delete*`; does NOT contain `git reset*`, `git checkout*`, `git restore*`, `git clean*`. `steps: 50`.

- [ ] **Step 4: Commit if any fixups were needed (otherwise skip)**

If anything needed adjustment during validation:

```bash
git add -A
git commit -m "fix: address issues found during full-suite validation"
```

---

## Notes

- **No verifier-result event is added.** The user explicitly confirmed subagent reads `run_verifier` return value directly; no MCP-level wake-on-verifier event needed.
- **Planner does not yet read observations.** Task 5 mentions it as a hint, but `_plan_evolve` / `_plan_mcts` still only consume scored history. That's a separate follow-up — out of scope here.
- **CandidateRecord stays single-shot.** Each `submit_candidate` overwrites the previous artifact. The runtime always reflects the "current workspace state"; iteration history lives in the subagent's local `results.tsv`.
