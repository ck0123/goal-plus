---
name: goal-plus
description: Run Goal Plus in Pi, including Goal Mode, Spec Discovery Mode, and Search Mode through agentic-any-search-mcp.
---

# Goal Plus For Pi

## Entry Contract

The native Pi `/goal-plus` command creates the Goal Plus record before the model turn starts. If a compatibility prompt path is used and no active `goal_plus_id` is already present, the first tool call must be `goal_plus_create(raw_goal=...)`. Do not triage, search, or edit before the goal record exists. Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

## Goal Mode

Use Goal Mode when the request is not yet a verifiable optimization/search task. Record triage with `goal_plus_record_triage({ goal_plus_id, triage: { is_optimization, confidence, recommended_phase, identified_at, scenario, reasons, missing } })` and keep the user-facing goal separate from implementation guesses. Do not create a SearchSpec in Goal Mode.

## Spec Discovery Mode

Use Spec Discovery Mode when the target needs a frozen verifier or edit surface. Save candidate details with `goal_plus_save_spec_draft`; if the verifier is already frozen and trustworthy, call `goal_plus_confirm_frozen_verifier` with evidence.

## Search Mode

When the goal is search-ready:

1. `search_freeze_spec`
2. `search_create`
3. `goal_plus_link_search_run`
4. Use `/skill:search` for Pi Search Mode.
5. After selection/promotion, call `goal_plus_record_search_result`.
6. Run the final raw-goal audit and then `goal_plus_set_status`.

## Gates

Before Search Mode tool use and main-agent mutating tools (`bash`, `edit`, `write`, `pi_rpc_run_worker`), Pi's extension calls `goal_plus_gate(event="pre_tool_use")`. At turn end, the extension calls `goal_plus_gate(event="stop")`; if the gate blocks, it queues the continuation prompt and triggers another model turn. If the extension is unavailable, manually call the same gates and follow their allow/block decisions.
