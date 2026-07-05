# Goal Plus: MCP API Impact

## Objective

Define how `goal-plus` fits around the current MCP API surface.

The current runtime exposes a `search_*` API for verifiable candidate search.
That surface should remain focused. `goal-plus` needs a small state machine for
goal intake, phase tracking, hook gating, and linking to an optional search run.
It should not turn the existing search runtime into a generic worker
supervisor.

## Current MCP Surface

The current MCP server registers these tools:

```text
search_freeze_spec
search_create
search_status
search_list_history
search_plan_next
search_start_batch
search_start_agent_session
search_bind_agent_handle
search_bind_opencode_session
search_continue_agent_session
search_get_agent_context
search_run_verifier
search_list_iterations
search_select
search_report
search_promote
```

The surface is intentionally search-shaped:

| Area | Existing tool(s) | Keep for goal-plus? | Notes |
|---|---|---|---|
| Freeze measurable standard | `search_freeze_spec` | Yes | Goal Plus enters Search Mode only after it can produce a valid `SearchSpec`. |
| Create search run | `search_create` | Yes | A goal can link to the resulting `run_id`. |
| Inspect search state | `search_status`, `search_list_history`, `search_list_iterations` | Yes | Hook and orchestrator code may read these indirectly through goal-plus state. |
| Plan/materialize candidates | `search_plan_next`, `search_start_batch` | Yes | No goal-specific change needed. |
| Worker context and launch | `search_start_agent_session`, bind/continue tools, `search_get_agent_context` | Yes | Continue to treat sessions as context handles, not lifecycle records. |
| Verification and selection | `search_run_verifier`, `search_select`, `search_report`, `search_promote` | Yes | This is the value that goal-plus upgrades into. |

No current `search_*` tool stores the raw user goal, triage decision, discovery
notes, hook gate decision, or final raw-goal audit. Those are the missing
pieces.

## Design Choice

Add a separate `goal_plus_*` API namespace.

Do not overload `SearchSpec.constraints` as the main state store. It is useful
for carrying a backlink to a `goal_plus_id`, but it should not become the
authoritative goal state. Search state and goal state have different lifecycles:

- search state starts only after a spec can be frozen
- goal-plus state starts at raw user intake
- hook gating needs a lightweight record even before Search Mode exists
- final completion must audit the raw goal, not just the selected candidate

## Implemented Minimal API

### `goal_plus_create`

Create the goal-plus record before any triage.

Input:

```text
raw_goal: str
source_path?: str
mode_hint?: "auto" | "goal" | "search"
policy?: dict
```

Output:

```text
goal_plus_id
status: "active"
phase: "intake"
next_action
```

Use `mode_hint="search"` for a strict `/goal-any-optimize` compatibility path.
Use `mode_hint="auto"` for normal `/goal-plus`.

### `goal_plus_status`

Read the complete goal-plus state. This is the main read API for the host agent
and for hook helper commands.

Input:

```text
goal_plus_id: str
```

Output includes:

```text
raw_goal
status
phase
triage
spec_draft
linked_search
next_action
evidence_log
hook_counters
```

### `goal_plus_record_triage`

Record the orchestrator's classification decision.

Input:

```text
goal_plus_id: str
triage:
  is_optimization: bool
  confidence: "high" | "medium" | "low"
  recommended_phase: "goal" | "spec_discovery" | "search"
  scenario?: str
  reasons: list[str]
  missing: list[str]
```

Output: updated goal-plus state.

This tool should not run inference itself. The host agent performs the analysis;
the MCP records the decision so hooks can enforce the resulting phase.

### `goal_plus_save_spec_draft`

Store the frozen-spec candidate discovered by the host agent before calling
`search_freeze_spec`.

Input:

```text
goal_plus_id: str
spec_draft:
  baseline: dict
  metric: dict
  correctness_gate: dict
  edit_surface: dict
  verifier_artifacts: list[str]
  search_spec: dict
  promotion_rule: str
  confidence: "high" | "medium" | "low"
  open_questions: list[str]
```

Output: updated goal-plus state.

The draft should freeze standards, not implementation plans.

### `goal_plus_link_search_run`

Link a frozen search run to the goal-plus record.

Input:

```text
goal_plus_id: str
frozen_spec_id: str
run_id: str
```

Output: updated goal-plus state with `phase="search"`.

This avoids wrapping `search_freeze_spec` and `search_create`. The existing
search tools remain the source of truth for search state.

### `goal_plus_record_search_result`

Record the selected or promoted search outcome for the final raw-goal audit.

Input:

```text
goal_plus_id: str
run_id: str
selected_candidate_id?: str
report_path?: str
promotion_artifact_path?: str
summary?: str
```

Output: updated goal-plus state with `phase="final_audit"` unless more search
work remains.

### `goal_plus_set_status`

Set the terminal or user-waiting status.

Input:

```text
goal_plus_id: str
status: "active" | "needs_user" | "blocked" | "complete" | "abandoned"
reason?: str
evidence?: list[dict]
next_action?: dict
```

Output: updated goal-plus state.

Only `complete`, `blocked`, and `abandoned` are terminal.

### `goal_plus_gate`

Return a hook-friendly decision for a lifecycle event.

Input:

```text
goal_plus_id: str
event: "stop" | "subagent_stop" | "pre_tool_use" | "user_prompt_submit"
context: dict
```

Output:

```text
decision: "allow" | "block"
reason?: str
continuation_prompt?: str
phase
status
```

This is the bridge for Codex/Claude Code hooks. For a `Stop` hook, `block`
means "do not let the model stop; continue with this prompt." For a
`PreToolUse` hook, `block` means "this tool call is not valid in the current
goal-plus phase."

The gate should be conservative and deterministic:

- allow terminal states
- block when `status="active"` and `next_action` is required
- block premature promotion before search selection
- block Search Mode steps before a spec draft has high confidence
- never launch, wait for, or abort workers

## State Model

Implemented file layout:

```text
.search/
  goal-plus/
    gp_0001/
      goal.json
      events.jsonl
```

`goal.json`:

```text
goal_plus_id: str
raw_goal: str
source_path?: str
status: active | needs_user | blocked | complete | abandoned
phase: intake | goal | spec_discovery | search | final_audit
mode_hint: auto | goal | search
triage?: GoalPlusTriage
spec_draft?: GoalPlusSpecDraft
linked_search?:
  frozen_spec_id?: str
  run_id?: str
  selected_candidate_id?: str
  report_path?: str
  promotion_artifact_path?: str
next_action?: GoalPlusNextAction
hook_counters: dict
created_at: str
updated_at: str
```

`events.jsonl` records append-only evidence:

```text
created
triage_recorded
spec_draft_saved
search_linked
search_result_recorded
gate_blocked
gate_allowed
status_changed
```

## Changes To Existing APIs

### Keep Unchanged

Most existing `search_*` tools should not change.

```text
search_freeze_spec
search_create
search_status
search_list_history
search_plan_next
search_start_batch
search_start_agent_session
search_bind_agent_handle
search_bind_opencode_session
search_continue_agent_session
search_get_agent_context
search_run_verifier
search_list_iterations
search_select
search_report
search_promote
```

### Optional Backlink

When `goal-plus` creates a `SearchSpec`, it may set:

```json
{
  "constraints": {
    "goal_plus_id": "gp_0001",
    "raw_goal_summary": "..."
  }
}
```

This is a backlink only. The authoritative state stays under
`.search/goal-plus/<goal_plus_id>/`.

### No Deletions

No current API should be deleted for `goal-plus`.

The important deletion decision is negative: do not reintroduce worker
lifecycle APIs into the search namespace. Hook gating can block or steer the
agent at lifecycle boundaries, but process lifecycle remains host-owned.

## Implementation Impact

Files changed by the baseline implementation:

| File | Change |
|---|---|
| `src/agentic_any_search_mcp/models.py` | Add `GoalPlusRecord`, `GoalPlusTriage`, `GoalPlusSpecDraft`, `GoalPlusNextAction`, and status/phase literals. |
| `src/agentic_any_search_mcp/goal_plus.py` | New file-backed goal-plus runtime. Keeps goal state separate from `FileSearchRuntime`. |
| `src/agentic_any_search_mcp/tools.py` | Add `GoalPlusTools` as the JSON-friendly facade beside `SearchTools`. |
| `src/agentic_any_search_mcp/server.py` | Register `goal_plus_*` tools in addition to existing `search_*` tools. |
| `tests/test_goal_plus.py` | Unit tests for state transitions, gate decisions, and search linking. |
| `tests/test_server.py` | Update exact MCP tool registration expectations. |
| `tests/test_tools.py` | Add JSON facade tests; keep lifecycle-exclusion tests for `search_*`. |
| `.opencode/command/goal-plus.md` | New command that loads goal-plus instructions and then search skill only in Search Mode. |
| `.agents/skills/goal-plus/SKILL.md` | Codex workflow instructions. |
| `.claude/skills/goal-plus/SKILL.md` | Claude Code workflow instructions. |
| hook scripts/docs | Optional host-specific `Stop` / `SubagentStop` / `PreToolUse` adapters that call `goal_plus_gate` or read its state. |

## Hook Integration Pattern

Hooks should not need to know search details. They should ask one question:

```text
Given this goal_plus_id and hook event, may the agent stop or perform this tool?
```

Example Stop hook behavior:

```text
goal_plus_gate(goal_plus_id, event="stop", context=<hook input>)
  -> allow
       exit 0
  -> block
       return hook block decision with continuation_prompt
```

Example continuation prompt:

```text
Goal Plus is still active in phase spec_discovery.
Do not stop yet. The next required action is:
  produce a high-confidence GoalPlusSpecDraft with metric, correctness gate,
  verifier artifacts, edit surface, and promotion rule.
```

This gives deterministic phase control without making MCP responsible for
worker supervision.

## Compatibility With `/goal-any-optimize`

`/goal-any-optimize` can remain as an explicit search-first shortcut.

Recommended behavior after `goal-plus` exists:

```text
/goal-any-optimize <objective>
  -> goal_plus_create(mode_hint="search")
  -> require high-confidence spec draft
  -> use existing search flow
```

The command should not bypass frozen-spec creation. It should simply skip the
ordinary Goal Mode fallback unless the spec cannot be made safe.

## Open Questions

- Should `goal_plus_gate` be an MCP tool only, or should there also be a small
  CLI helper for hook scripts that cannot easily reuse the active MCP
  connection?
- Should `goal_plus_save_spec_draft` validate that `search_spec` already passes
  `SearchSpec` validation, or should validation remain deferred to
  `search_freeze_spec`?
- Should final raw-goal audit be structured evidence in
  `goal_plus_set_status`, or a separate `goal_plus_record_audit` tool?
