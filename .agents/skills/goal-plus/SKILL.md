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

1. Call `goal_plus_create(raw_goal=...)`.
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
13. Finish with a final raw-goal audit, then call
    `goal_plus_set_status(status="complete", evidence=[...])` only when the
    original objective is satisfied.
14. Before stopping, call `goal_plus_gate(event="stop", context={})`; continue
    if it returns a continuation prompt.

## Modes

Goal Mode is for ordinary coding, docs, review, and investigation tasks. It
uses normal Codex verification evidence and no SearchSpec.

Spec Discovery Mode is for optimization-shaped goals where the metric,
baseline, correctness gate, or edit surface is still unclear.

Search Mode is for frozen, measurable optimization. It delegates candidate
workspace creation, verifier execution, selection, report, and promotion to the
existing Search MCP flow.

## Hook Compatibility

This repository ships a Codex `Stop` hook at `.codex/hooks.json` that runs
`scripts/hooks/goal_plus_stop.py`. It is a final backstop for
`goal_plus_gate(event="stop")`: if the active Goal Plus record still has a
required next action, Codex receives a continuation prompt instead of ending.

The hook does not replace the explicit workflow calls above. It does not wire
`PreToolUse` or `SubagentStop`, so call `goal_plus_gate(event="pre_tool_use",
...)` before Search Mode tools and call the stop gate manually before the final
response. `goal_plus_gate` does not supervise worker lifecycle. Codex worker
budget and foreground subagent behavior remain the responsibility of the
internal `search` skill.
