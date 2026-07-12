---
name: goal-plus
description: Run a Codex goal with optional upgrade to Agentic Search through the search-runtime MCP server.
---

# Goal Plus for Codex

Use this skill for `/goal-plus`: the normal goal workflow. It can upgrade to
multi-candidate Agentic Search when the success standard is measurable and
frozen.

Use the logical `goal_plus_*` and `search_*` tools exposed by the
`search-runtime` MCP server. Codex may display MCP tools with a client-specific
prefix; match by the final logical tool name.

## Workflow

1. Read the hidden Codex hook context first. When it contains an active
   `goal_plus_id`, the `UserPromptSubmit` hook already created and bound the
   record before this model turn; use that id and do not call
   `goal_plus_create` again. If no hook context is present, call
   `goal_plus_create(raw_goal=...)` as a compatibility fallback.
2. Inspect enough context to classify the task.
3. Call `goal_plus_record_triage`.
4. If triage chooses Goal Mode, work normally in the current workspace.
   Do not create a SearchSpec in Goal Mode.
5. If triage chooses Spec Discovery Mode, identify baseline, metric,
   correctness gate, edit surface, verifier artifacts, budget, and promotion
   rule. Save them with `goal_plus_save_spec_draft`.
6. Enter Search Mode only when the saved draft has `confidence="high"` and no
   open questions.
7. For Initial Search-Ready goals, ask the user to confirm the frozen verifier,
   metric, edit surface, and promotion rule, then call
   `goal_plus_confirm_frozen_verifier`.
8. For In-Progress Search Discovery, when the verifier is constructed during
   goal execution, save the draft with `origin="in_progress"` and do not ask for
   a separate verifier-freeze confirmation.
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
14. Finish with a final raw-goal audit, then call
    `goal_plus_set_status(status="complete", evidence=[...])` only when the
    original objective is satisfied.
15. Before stopping, call `goal_plus_gate(event="stop", context={})`; continue
    if it returns a continuation prompt.

One Goal Plus record is the complete task. `search_tasks` is its append-only
history of Search Mode tasks, one `run_id` over one frozen spec each;
`linked_search` is only the current-task compatibility view. Within a search
task, planning and started search rounds are reported separately by
`goal_plus_monitor_snapshot`.

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

## Hook Compatibility

This repository ships Codex 0.144.1 Goal Plus host hooks at
`.codex/hooks.json`. They run
`agentic-any-search-mcp --goal-plus-host-hook` for `UserPromptSubmit`,
`SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, and `SubagentStop`.
`UserPromptSubmit` pre-creates and binds `/goal-plus` or `$goal-plus` before the
model turn. `SessionStart` restores a session-bound active id. `PreToolUse`
enforces the search and mutation gates. `PostToolUse(goal_plus_create)` remains
a compatibility binding fallback. `Stop` and `SubagentStop` return runtime
continuation prompts when a required action remains.

Keep the explicit workflow calls above as auditable state transitions even
though the hooks are enforcement backstops. Subagent tool events do not bind
Goal Plus ownership. `goal_plus_gate` does not supervise worker lifecycle;
Codex worker budget and foreground subagent behavior remain the responsibility
of the internal `search` skill.
