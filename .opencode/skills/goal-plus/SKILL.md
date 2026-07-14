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

The MCP server is configured as `goal-plus`, so tools appear with this
prefix:

| Runtime tool | OpenCode tool name |
|---|---|
| `goal_plus_create` | `goal-plus_goal_plus_create` |
| `goal_plus_status` | `goal-plus_goal_plus_status` |
| `goal_plus_record_triage` | `goal-plus_goal_plus_record_triage` |
| `goal_plus_save_spec_draft` | `goal-plus_goal_plus_save_spec_draft` |
| `goal_plus_confirm_frozen_verifier` | `goal-plus_goal_plus_confirm_frozen_verifier` (legacy optional audit evidence) |
| `goal_plus_link_search_run` | `goal-plus_goal_plus_link_search_run` |
| `goal_plus_record_search_result` | `goal-plus_goal_plus_record_search_result` |
| `goal_plus_set_status` | `goal-plus_goal_plus_set_status` |
| `goal_plus_gate` | `goal-plus_goal_plus_gate` |

Use the internal `search` skill for Search Mode tools such as
`search_freeze_spec`, `search_create`, `search_plan_next`,
`search_start_batch`, `search_start_agent_session`, `search_select`,
`search_report`, and `search_promote`.

If any required MCP tool is unavailable, stop and report that the goal-plus
MCP server is not connected. Do not simulate `.gp` state in chat.

## Workflow

### Step 1: Create Goal

Call:

```text
goal-plus_goal_plus_create(raw_goal="<user objective>", source_path="<optional>")
```

The raw goal may begin with `mode=autonomous` (default) for substantial,
renewable candidate exploration or `mode=probe` for short feasibility,
potential, and blocker probes. The runtime replaces the prefix with one
canonical final line in `raw_goal`; this is exploration guidance, not the
Goal/Spec Discovery/Search phase or a Search strategy mode. The model still
decides from evidence whether the task stays goal-like or upgrades to Search
Mode.

### Step 2: Triage

Read enough context to decide whether search adds value. Then call
`goal-plus_goal_plus_record_triage`.

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
`goal-plus_goal_plus_set_status(status="complete", evidence=[...])`.

Before final response, call:

```text
goal-plus_goal_plus_gate(goal_plus_id="<id>", event="stop", context={})
```

If the gate blocks, continue with its `continuation_prompt`.

For a top-level agent, every still-active record blocks Stop and re-presents
the full raw goal plus timing context. Continue or record a truthful terminal
status; a candidate worker lease ending never completes the parent goal.

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

The ranking verifier must emit a final JSON object with a finite numeric
`spec.metric_name`, for example `{"combined_score": 123.0}`. Its command may
be inline or call an existing repository tool. Create a custom verifier file
only when needed; materialize it during Spec Discovery before freezing, in a
source-owned path such as `.goal-plus-verifiers/`, never `.gp/` or `.search/`.
It must keep the candidate workspace read-only and put compiler products and
temporary outputs in the unique `GOAL_PLUS_VERIFIER_TMPDIR`/`TMPDIR` (or a
Python `tempfile.TemporaryDirectory()`); fixed `/tmp` paths are unsafe when
Search candidates verify concurrently. Freeze rejects workspace side effects
before candidate budget is spent.
Spec Discovery may use host inspection and write tools for this work. The
freeze tool exposes the complete nested `SearchSpec` schema.
`expected_outputs` lists artifact paths/globs and is not a stdout parser.
`search_freeze_spec` repeats this preflight before candidate workers can start.

For an AscendC Direct Invoke operator goal described by semantics, approximate
shapes/dtypes, and reference hints, record
`scenario="ascendc_direct_invoke"` and read
`examples/ascendc-direct-search/SPEC_DISCOVERY.md` completely. Follow its
request schema and source template. Run its `materialize_knowledge.py` with
`knowledge.sources.json` against exact pinned Git commits to generate the
task-local `_skills/`; never copy a live Skill directory. Treat the curated AKG
AscendC tree as primary knowledge and use only the declared CANNBot supplements
for uncovered operator families. The main agent generates the Golden, cases,
verifier, baseline, and SearchSpec. Before `search_freeze_spec`, use a JSON
Schema validator to validate the generated `_task/operator_request.json`
against `examples/ascendc-direct-search/request.schema.json`; JSON parsing or a
manual field checklist is insufficient, and validation failure blocks
freezing. Never require the user to run a task preparer, supply a task
directory, or write a verifier. Support Direct Invoke only; the generated
knowledge is read-only and cannot launch source Agent or Plugin workflows.

This scenario is self-contained. Do not invoke an external AscendC Agent,
plugin, or orchestration workflow.

Call `goal-plus_goal_plus_save_spec_draft`. Continue to Search Mode only
when `confidence="high"` and `open_questions=[]`. Otherwise ask for the missing
piece or continue in Goal Mode.

#### Autonomous Search Upgrade

When the draft is high-confidence with no open questions, continue to the
Search Mode gate automatically. Do not ask the user to approve the verifier,
metric, edit surface, promotion rule, or mode change. User hints are useful but
optional; the agent must discover missing details and decide from evidence.

Keep `identified_at` and `origin` as provenance only. The legacy
`goal-plus_goal_plus_confirm_frozen_verifier` tool and
`user_confirmed_frozen_verifier` field remain compatible with older runs, but
they are not Search admission requirements and must never pause `/goal-plus`.

### Step 5: Search Mode

Before calling any `search_*` tool that creates or runs search state, call:

```text
goal-plus_goal_plus_gate(
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

After `search_create`, call `goal-plus_goal_plus_link_search_run`.
After selection/report/promotion, call
`goal-plus_goal_plus_record_search_result`.

One Goal Plus record is the complete user task. If the raw-goal audit needs
another verifier-backed search, create and link another `run_id` under the same
`goal_plus_id`. `search_tasks` is the append-only task history, one run over
one frozen spec per item; `linked_search` is only the current-task
compatibility view. Each search task may contain multiple search rounds.

### Step 6: Final Raw-Goal Audit

Search completion proves only the frozen spec. The final raw-goal audit checks
whether the original user objective is actually satisfied after promotion and
any integration work.

If yes, call `goal-plus_goal_plus_set_status(status="complete",
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
