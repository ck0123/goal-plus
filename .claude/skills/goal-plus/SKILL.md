---
name: goal-plus
description: Run a Claude Code goal with optional upgrade to Agentic Search through the goal-plus MCP server.
---

# Goal Plus for Claude Code

Use this skill for `/goal-plus`: the normal goal workflow. It can upgrade to
multi-candidate Agentic Search when the success standard is measurable and
frozen.

Use the logical `goal_plus_*` and `search_*` tools exposed by the
`goal-plus` MCP server. Claude Code may display MCP tools with a server
prefix; match by the final logical tool name.

## Workflow

1. Call `goal_plus_create(raw_goal=...)`.
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
13. If the raw-goal audit requires another verifier-backed search, create and
    link a new `run_id` under the same `goal_plus_id`, then repeat the Search
    Mode flow. `search_tasks` is append-only; `linked_search` is the current
    task compatibility view.
14. Finish with a final raw-goal audit, then call
    `goal_plus_set_status(status="complete", evidence=[...])` only when the
    original objective is satisfied.
15. Before stopping, call `goal_plus_gate(event="stop", context={})`; continue
    if it returns a continuation prompt.

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
uses normal Claude Code verification evidence and no SearchSpec.

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

This repository ships Claude Code Goal Plus host hooks in `.claude/settings.json`
that run `goal-plus --goal-plus-host-hook`.
`PostToolUse(goal_plus_create)` binds the created Goal Plus record to the
current top-level Claude Code `session_id`. The `Stop` hook is a final backstop
for `goal_plus_gate(event="stop")`: if the session-bound Goal Plus record still
has a required next action, Claude receives a continuation prompt instead of
ending.

The hook does not replace the explicit workflow calls above. It does not wire
`PreToolUse` or `SubagentStop`, so call `goal_plus_gate(event="pre_tool_use",
...)` before Search Mode tools and call the stop gate manually before the final
response. Subagent tool events do not bind Goal Plus ownership. `goal_plus_gate`
does not supervise worker lifecycle. Claude foreground agent behavior and turn
budgets remain the responsibility of the internal `search` skill.
