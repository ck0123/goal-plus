---
name: goal-plus
description: Run, resume, or edit a Codex Goal Plus task, including /goal-plus-with-final-check tasks that require an independent final reviewer, with optional upgrade to Agentic Search through the goal-plus MCP server.
---

# Goal Plus for Codex

Use this skill for `/goal-plus`: the normal goal workflow. It can upgrade to
multi-candidate Agentic Search when the success standard is measurable and
frozen.

Use the logical `goal_plus_*` and `search_*` tools exposed by the
`goal-plus` MCP server. Codex may display MCP tools with a client-specific
prefix; match by the final logical tool name.

## Workflow

1. Read the hidden Codex hook context first. When it contains an active
   `goal_plus_id`, the `UserPromptSubmit` hook already created and bound the
   record before this model turn; use that id and do not call
   `goal_plus_create` again. If no hook context is present, call
   `goal_plus_create(raw_goal=...)` as a compatibility fallback.
   `/goal-plus-with-final-check` is pre-created with
   `policy.final_check.mode="required"`. `/goal-plus edit <full revised goal>`
   updates the same record before the model turn; use the new `goal_revision`
   and do not continue against an older revision. `/goal-plus resume` restores
   the same active revision after a host interruption.
   Before resuming an active record, treat the latest user message as
   authoritative for this turn:
   - If it continues or steers the existing objective without changing its
     scope, deliverables, or success criteria, keep the current revision.
   - If it changes the effective scope, deliverables, or success criteria,
     call `goal_plus_update_goal` with the complete revised objective and the
     current `expected_revision`, then re-triage before doing further work.
   - If it is unrelated, respond without changing the goal. If its relationship
     to the goal is unclear, clarify before revising or resuming. Do not resume
     work merely because the Goal Plus record is active.
2. Inspect enough context to classify the task.
3. Call `goal_plus_record_triage`.
4. If triage chooses Goal Mode, work normally in the current workspace.
   Do not create a SearchSpec in Goal Mode.
5. If triage chooses Spec Discovery Mode, identify baseline, metric,
   correctness gate, edit surface, verifier artifacts, budget, and promotion
   rule. A ranking verifier must emit a final JSON object with a finite numeric
   `spec.metric_name`; keep its files in a source-owned path such as
   `.goal-plus-verifiers/`, never `.gp/` or `.search/`. `expected_outputs`
   lists artifact paths/globs and is not a stdout parser. Require the verifier
   to keep the candidate workspace read-only and use the unique
   `GOAL_PLUS_VERIFIER_TMPDIR`/`TMPDIR` (or Python `tempfile`) for compiler and
   temporary outputs; fixed `/tmp` paths are unsafe under parallel Search.
   Freeze rejects workspace side effects before candidate budget is spent.
   Save the complete contract with `goal_plus_save_spec_draft`.
6. Enter Search Mode only when the saved draft has `confidence="high"` and no
   open questions.
7. Search is an autonomous upgrade. Once the draft is high-confidence with no
   open questions, proceed to the Search Mode gate without asking the user to
   approve the verifier, metric, edit surface, promotion rule, or mode change.
   User-provided hints are useful evidence but are not required.
8. Keep `origin="initial"` or `origin="in_progress"` as provenance only. It
   must not change whether a search-ready draft can proceed.
9. Before calling Search Mode tools such as `search_freeze_spec`, call
   `goal_plus_gate(event="pre_tool_use", context={"tool_name": "search_freeze_spec"})`.
10. In Search Mode, use the internal `search` skill:
   `search_freeze_spec`, `search_create`, `search_plan_next`,
   `search_start_batch`, `search_start_agent_session`, final
   `search_run_verifier`, `search_select`, `search_report`, and
   `search_promote`.
11. After `search_create`, call `goal_plus_link_search_run`.
12. After selection/report/promotion, call `goal_plus_record_search_result`.
13. Run the raw-goal audit. If another verifier-backed search is required,
    freeze/create a new run and repeat steps 9-12 with the same
    `goal_plus_id`. Each distinct `run_id` is appended as another search task;
    do not reuse a prior `run_id` for a new frozen spec.
14. Finish with a final raw-goal audit. For a normal Goal Plus record, call
    `goal_plus_set_status(status="complete", evidence=[...])` only when the
    current objective is satisfied. When `policy.final_check.mode="required"`:
    - call `goal_plus_prepare_final_check(checker_host="codex")`
    - project `launch.task_name`, `launch.message`, and `launch.fork_turns`
      onto the current `spawn_agent` schema and launch it foreground
    - use `fork_turns="none"`; the reviewer must reconstruct the result from
      the workspace and runtime evidence, not inherit the parent transcript
    - wait for the reviewer to return; it must call
      `goal_plus_submit_final_check` itself
    - on failure, address its findings and prepare a fresh check; never submit
      a reviewer verdict on the reviewer's behalf
    A passing required check atomically marks the Goal Plus record complete.
15. Before stopping, call `goal_plus_gate(event="stop", context={})`; continue
    if it returns a continuation prompt.

One Goal Plus record is the complete task. `search_tasks` is its append-only
history of Search Mode tasks, one `run_id` over one frozen spec each;
`linked_search` is only the current-task compatibility view. Within a search
task, planning and started search rounds are reported separately by
`goal_plus_monitor_snapshot`.

Goal edits are also append-only: `goal_revisions` preserves every effective
objective. Updating a goal resets intake/triage for the new revision and makes
older Search tasks and final checks historical without deleting them. If a
turn is interrupted, call `goal_plus_status` and resume the durable revision.
If a reviewer is interrupted, its attempt is recorded as `interrupted`; call
`goal_plus_prepare_final_check` to create and launch a fresh attempt.

## Triage Schema

`goal_plus_record_triage` expects this runtime schema:

```json
{
  "is_optimization": false,
  "confidence": "high",
  "recommended_phase": "goal",
  "identified_at": "initial",
  "scenario": null,
  "reasons": ["why this classification is correct"],
  "missing": []
}
```

Use only these `recommended_phase` values: `"goal"`, `"spec_discovery"`, or
`"search"`. Do not send fields named `mode` or `reason`, and do not use values
like `"goal_mode"`.

Recommended mapping:

- Goal Mode: `is_optimization=false`, `recommended_phase="goal"`,
  `confidence="high"`.
- Spec Discovery Mode: `is_optimization=true`,
  `recommended_phase="spec_discovery"`, and list missing baseline, metric,
  correctness gate, edit surface, verifier, budget, or promotion details.
- Search Mode: `is_optimization=true`, `recommended_phase="search"`,
  `confidence="high"`.

## Modes

Goal Mode is for ordinary coding, docs, review, and investigation tasks. It
uses normal Codex verification evidence and no SearchSpec.

Spec Discovery Mode is for optimization-shaped goals where the metric,
baseline, correctness gate, or edit surface is still unclear.

Search Mode is for frozen, measurable optimization. It delegates candidate
workspace creation, verifier execution, selection, report, and promotion to the
existing Search MCP flow.

`goal_plus_confirm_frozen_verifier` and
`user_confirmed_frozen_verifier` remain readable for compatibility with older
runs. They are optional audit evidence, not Search Mode admission requirements.
Never pause or ask the user for them during `/goal-plus` execution.

## Hook Compatibility

This repository ships Codex 0.144.1 Goal Plus host hooks at
`.codex/hooks.json`. They run
`goal-plus --goal-plus-host-hook` for `UserPromptSubmit`,
`SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, and `SubagentStop`.
`UserPromptSubmit` pre-creates and binds `/goal-plus` or `$goal-plus` before the
model turn. It also recognizes `/goal-plus-with-final-check` and explicit
`/goal-plus edit` updates. `SessionStart` restores a session-bound active id.
`PreToolUse`
enforces the search and mutation gates. `PostToolUse(goal_plus_create)` remains
a compatibility binding fallback. Search candidate subagent PostTool events
also perform a read-only, one-shot verifier-time advisory check; they never
bind Goal Plus ownership, and main/final-checker/ordinary-subagent events are
ignored. Top-level `Stop` enforces the parent-owned Goal Plus next action.
`SubagentStop` is ownership-aware: a Search candidate is blocked only until its
own `search_run_verifier(..., agent_session_id=...)` call is durably recorded,
then it may return while the parent continues selection, reporting, promotion,
and final audit. Ordinary subagents do not inherit parent actions; final-check
reviewers retain their independent-review gate.

Keep the explicit workflow calls above as auditable state transitions even
though the hooks are enforcement backstops. Subagent tool events do not bind
Goal Plus ownership. `goal_plus_gate` does not supervise worker lifecycle;
Codex worker budget and foreground subagent behavior remain the responsibility
of the internal `search` skill.
