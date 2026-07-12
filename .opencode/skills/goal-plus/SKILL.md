---
name: goal-plus
description: >
  Run a natural-language goal with an optional upgrade to Agentic Search when
  the task has a measurable verifier, bounded edit surface, and useful
  multi-candidate search space.
argument-hint: Objective, source path, optional optimization/scenario hints.
---

# Goal Plus Skill

Goal Plus is a thin goal-shaped layer over the Search MCP runtime. It records
the raw goal, triages whether search is justified, discovers a frozen spec when
needed, and then delegates Search Mode to the internal `search` skill.

## Tool Names In OpenCode

The MCP server is configured as `search-runtime`, so tools appear with this
prefix:

| Runtime tool | OpenCode tool name |
|---|---|
| `goal_plus_create` | `search-runtime_goal_plus_create` |
| `goal_plus_status` | `search-runtime_goal_plus_status` |
| `goal_plus_record_triage` | `search-runtime_goal_plus_record_triage` |
| `goal_plus_save_spec_draft` | `search-runtime_goal_plus_save_spec_draft` |
| `goal_plus_confirm_frozen_verifier` | `search-runtime_goal_plus_confirm_frozen_verifier` |
| `goal_plus_link_search_run` | `search-runtime_goal_plus_link_search_run` |
| `goal_plus_record_search_result` | `search-runtime_goal_plus_record_search_result` |
| `goal_plus_set_status` | `search-runtime_goal_plus_set_status` |
| `goal_plus_gate` | `search-runtime_goal_plus_gate` |

Use the internal `search` skill for Search Mode tools such as
`search_freeze_spec`, `search_create`, `search_plan_next`,
`search_start_batch`, `search_start_agent_session`, `search_select`,
`search_report`, and `search_promote`.

If any required MCP tool is unavailable, stop and report that the search-runtime
MCP server is not connected. Do not simulate `.gp` state in chat.

## Workflow

### Step 1: Create Goal

Call:

```text
search-runtime_goal_plus_create(raw_goal="<user objective>", source_path="<optional>")
```

There is no user-provided mode hint. The model decides from context whether the
goal should stay goal-like or upgrade into Search Mode.

### Step 2: Triage

Read enough context to decide whether search adds value. Then call
`search-runtime_goal_plus_record_triage`.

Use Search Mode only when most of these are true:

- a numeric or comparable metric exists
- an automated correctness gate exists
- the edit surface can be bounded
- at least two credible implementation approaches exist
- baseline behavior can be measured
- candidate budget is worth spending

Recommended triage mapping:

- Goal Mode: `is_optimization=false`, `recommended_phase="goal"`,
  `confidence="high"`.
- Spec Discovery Mode: `is_optimization=true`,
  `recommended_phase="spec_discovery"`, and list missing baseline/metric/gate
  fields.
- Search Mode: `is_optimization=true`, `recommended_phase="search"`,
  `confidence="high"`.

### Step 3: Goal Mode

Goal Mode is for ordinary implementation, investigation, docs, review, and
qualitative tasks.

Do not create a SearchSpec in Goal Mode. Work in the current workspace, verify
with appropriate commands or review evidence, then call
`search-runtime_goal_plus_set_status(status="complete", evidence=[...])`.

Before final response, call:

```text
search-runtime_goal_plus_gate(goal_plus_id="<id>", event="stop", context={})
```

If the gate blocks, continue with its `continuation_prompt`.

### Step 4: Spec Discovery Mode

Discovery turns a fuzzy optimization request into a SearchSpec draft. Produce:

- baseline command and baseline result
- metric name, direction, and aggregation
- correctness gate command or verifier artifact
- allowed and denied edit surface
- verifier artifact paths to freeze
- candidate budget and worker profile
- promotion rule
- unresolved questions, if any

Call `search-runtime_goal_plus_save_spec_draft`. Continue to Search Mode only
when `confidence="high"` and `open_questions=[]`. Otherwise ask for the missing
piece or continue in Goal Mode.

#### Initial Search-Ready

When the first triage already proves that search is ready, set
`identified_at="initial"` in `goal_plus_record_triage` and `origin="initial"`
in `goal_plus_save_spec_draft`. Show the user the frozen verifier artifacts,
metric, edit surface, and promotion rule. After explicit approval, call
`search-runtime_goal_plus_confirm_frozen_verifier`.

#### In-Progress Search Discovery

When ordinary Goal Mode work discovers or constructs a verifier later, set
`identified_at="in_progress"` and `origin="in_progress"`. Do not ask for a
separate verifier-freeze confirmation; the discovery work is already part of
the active `/goal-plus` execution.

### Step 5: Search Mode

Before calling any `search_*` tool that creates or runs search state, call:

```text
search-runtime_goal_plus_gate(
  goal_plus_id="<id>",
  event="pre_tool_use",
  context={"tool_name": "search_freeze_spec"}
)
```

If allowed, call the internal `search` skill and follow its workflow exactly:

```text
search_freeze_spec -> search_create -> search_plan_next -> search_start_batch
-> search_start_agent_session -> host foreground workers -> search_run_verifier
-> search_select -> search_report -> search_promote
```

After `search_create`, call `search-runtime_goal_plus_link_search_run`.
After selection/report/promotion, call
`search-runtime_goal_plus_record_search_result`.

One Goal Plus record is the complete user task. If the raw-goal audit needs
another verifier-backed search, create and link another `run_id` under the same
`goal_plus_id`. `search_tasks` is the append-only task history, one run over
one frozen spec per item; `linked_search` is only the current-task
compatibility view. Each search task may contain multiple search rounds.

### Step 6: Final Raw-Goal Audit

Search completion proves only the frozen spec. The final raw-goal audit checks
whether the original user objective is actually satisfied after promotion and
any integration work.

If yes, call `search-runtime_goal_plus_set_status(status="complete",
evidence=[...])`. If not, continue in Goal Mode with the remaining integration
work or mark the goal blocked with clear evidence.

## Hook Compatibility

When a host has hooks, wire `goal_plus_gate` to:

- `pre_tool_use` before `search_*` tools
- `stop` or `subagent_stop` before the agent ends

The checked-in OpenCode assets do not include such hooks. In OpenCode, call the
same gate manually at those checkpoints. This is instruction-driven and can be
skipped by a non-compliant agent; it is not enforced by OpenCode itself. The
gate controls phase order; the host still owns foreground worker launch,
interruptions, and return values.
