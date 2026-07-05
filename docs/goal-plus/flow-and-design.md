# Goal Plus: Flow And Design

## Objective

Design and implement a small `goal-plus` layer that makes the existing Search
MCP workflow usable from broader goal-shaped requests.

The key idea is progressive commitment:

1. Accept a user goal in natural language.
2. Decide whether the goal is optimization-shaped.
3. If the success standard is still fuzzy, run a short discovery pass.
4. If a verifier and ranking metric can be frozen, upgrade to Search MCP.
5. If not, continue as an ordinary goal-style task.

This keeps `/goal-plus` natural for users while preserving the Search MCP
runtime's requirement that search candidates be compared by frozen evidence.

## Relationship To Existing Entrypoints

| Entrypoint | Primary shape | Success standard | Execution model |
|---|---|---|---|
| `/goal` | Long-running task | May be fuzzy; model audits evidence | Single thread, host-native continuation |
| `/goal-any-optimize` | Explicit optimization run | Must be frozen before search | Multi-candidate Search MCP run |
| `/goal-plus` | Goal with optional search upgrade | Starts fuzzy, freezes only when justified | Triage + optional Search MCP |

`goal-plus` should keep the original user goal as the root objective even after
it creates a `SearchSpec`. The frozen spec proves one measurable subproblem;
the final completion audit still checks whether the original goal is satisfied.

## Design Principles

1. **Freeze standards, not implementation ideas.** A frozen spec should lock
   baseline, verifier, metrics, edit surface, and promotion rules. It should
   not lock the candidate to the first guessed technique.
2. **Search is an upgrade, not the default.** Only enter Search Mode when
   candidates can be compared with automated evidence.
3. **The MCP runtime remains a search runtime.** It owns specs, workspaces,
   verification, history, reports, and promotion. It does not become a generic
   lifecycle supervisor for all goals.
4. **Domain scenarios should be thin.** A scenario contributes detection hints,
   verifier bootstrap steps, spec templates, and edit-surface heuristics. It
   should not fork the runtime.
5. **Final success is broader than best score.** Selecting a best candidate is
   not automatically the same as completing the user's original goal.

## Top-Level Flow

```text
User
  |
  | /goal-plus <natural-language objective>
  v
Goal Plus Orchestrator
  |
  | 1. Preserve raw goal
  | 2. Inspect repo / files / task context
  | 3. Classify task shape
  v
+-----------------------------+
| Optimization Triage         |
+-----------------------------+
  |
  +--> Not optimization-shaped
  |      |
  |      v
  |   Goal Mode
  |      - work normally
  |      - verify with available evidence
  |      - finish/block by goal audit
  |
  +--> Optimization-shaped, spec unclear
  |      |
  |      v
  |   Spec Discovery Mode
  |      - identify baseline
  |      - find/generate verifier
  |      - define metric and constraints
  |      - draft frozen SearchSpec
  |      - continue to Search Mode only if evidence is strong
  |
  +--> Optimization-shaped, spec clear
         |
         v
      Search Mode
         - freeze spec and verifier artifacts
         - create candidate workspaces
         - dispatch host workers
         - run verifier
         - select best candidate
         - report and promote
         - audit original goal before claiming completion
```

## Triage

The triage decision should be conservative. A task is a good Search Mode
candidate when most of these are true:

| Signal | Why it matters |
|---|---|
| There is a measurable target | Candidates need a ranking metric. |
| There is an automated correctness gate | Faster but wrong candidates must lose. |
| The edit surface can be bounded | Workers need isolated workspaces and anti-cheat checks. |
| Multiple approaches are plausible | Search adds value only when the solution space branches. |
| Baseline behavior can be captured | Promotion should compare against current state. |
| Runtime cost is acceptable | Parallel exploration spends more time and tokens. |

If the task lacks a metric or verifier but looks like optimization, enter Spec
Discovery Mode instead of guessing. Discovery is still part of the goal; it is
not yet a search run.

## Mode Details

### Goal Mode

Goal Mode is the fallback for ordinary tasks:

- implement or investigate directly in the current workspace
- use tests, commands, reviews, and file inspection as evidence
- keep the user's raw goal intact
- complete only after an evidence-based audit

This mode should not create a `SearchSpec`.

### Spec Discovery Mode

Spec Discovery Mode turns a fuzzy optimization request into a candidate frozen
spec. It may read code, run baseline commands, inspect existing benchmarks, and
consult scenario docs.

Discovery should produce a short `GoalPlusSpecDraft`:

```text
raw_goal: original user objective
scenario: optional matched domain bundle
baseline: commit/workspace state and baseline command
metric: name, direction, aggregation
correctness_gate: command or verifier artifact
edit_surface: allowed and denied paths
candidate_budget: max candidates and worker budget
promotion_rule: what must be true before applying the best candidate
confidence: high | medium | low
open_questions: unresolved issues that block freezing
```

When confidence is high, the host agent can proceed to `search_freeze_spec`.
When confidence is medium or low, it should either ask the user for the missing
piece or continue in Goal Mode.

### Search Mode

Search Mode reuses the current Search MCP flow:

```text
GoalPlusSpecDraft
  |
  v
SearchSpec + verifier_artifact_paths
  |
  v
search_freeze_spec
  |
  v
search_create
  |
  v
search_plan_next
  |
  v
search_start_batch
  |
  v
search_start_agent_session
  |
  v
Host foreground worker launch
  |
  v
worker search_get_agent_context
  |
  v
worker autoresearch loop + search_run_verifier
  |
  v
main final search_run_verifier
  |
  v
search_select -> search_report -> search_promote
  |
  v
Goal Plus final audit against raw_goal
```

The last step is deliberate. A selected candidate proves that the frozen spec's
ranking rule found a best candidate. It does not necessarily prove that the
original user goal was fully satisfied.

## MCP Boundary

For this repository, `goal-plus` is implemented as a small file-backed state
machine plus host/skill instructions over the existing Search MCP tools.

```text
Host command or skill
  owns:
    - raw goal intake
    - triage
    - spec discovery
    - user-facing decisions
    - final audit against raw goal

Search MCP runtime
  owns:
    - frozen SearchSpec and verifier hashes
    - candidate workspaces
    - planning and strategy state
    - verifier execution and score reports
    - selection, report, promotion patch

Host adapter
  owns:
    - foreground worker launch
    - host-native worker budget mapping
    - worker handle binding
    - continuation only where supported
```

This avoids changing the runtime into a general goal supervisor. The goal-plus
state machine records phase, next action, spec draft, linked search run, and
gate decisions; the search runtime stays strict where it is already strong:
frozen inputs, isolated candidates, verifier results, and promotion artifacts.

## Natural Implementation Shape

The baseline implementation has these pieces:

```text
.search/goal-plus/<goal_plus_id>/
  - goal.json
  - events.jsonl

src/agentic_any_search_mcp/goal_plus.py
  - file-backed Goal Plus state machine
  - deterministic gate decisions for stop and pre-tool-use checkpoints

src/agentic_any_search_mcp/tools.py / server.py
  - goal_plus_* MCP facade and registration

.opencode/command/goal-plus.md
  - load a goal-plus skill or instructions
  - create goal-plus state and run triage
  - call the existing search skill only when Search Mode is selected

.agents/skills/goal-plus/SKILL.md
.claude/skills/goal-plus/SKILL.md
  - host-specific workflow text
  - same triage model
  - host-specific worker launch notes

docs/goal-plus/
  - shared design and scenario guidance
```

Likely future additions, if needed, are:

- scenario metadata files that declare detection hints and required verifier
  artifacts
- a pure helper that validates a `GoalPlusSpecDraft` before converting it to a
  `SearchSpec`
- host asset tests that ensure the `goal-plus` commands do not bypass the
  required search workflow

## Scenario Packs

`goal-plus` becomes useful when domain packs make spec discovery cheap. A pack
should answer:

| Field | Example |
|---|---|
| Detection hints | "kernel", "latency", "throughput", "CANN", "vLLM" |
| Baseline command | Benchmark or test command to run before search |
| Correctness verifier | Unit tests, numerical checker, golden output diff |
| Metric | `avg_latency_ms minimize`, `tokens_per_second maximize` |
| Edit surface | Kernel file, model config, scheduler path, selected Python modules |
| Deny surface | Verifier, reference model, benchmark harness |
| Worker profile | fast/default/deep candidate budget hints |
| Promotion rule | correctness pass and metric improvement over baseline |

This pattern lets new domains plug into `goal-plus` without new runtime
semantics.

## Cost Control

`goal-plus` should spend search budget only when there is a real search
opportunity.

Recommended admission policy:

```text
Use Goal Mode when:
  - success is qualitative
  - only one obvious implementation path exists
  - verifier cannot be automated cheaply
  - the edit surface is too broad to isolate

Use Spec Discovery Mode when:
  - the request sounds like optimization
  - the metric or verifier is missing
  - baseline is unknown
  - a scenario pack may provide the missing pieces

Use Search Mode when:
  - metric, correctness gate, baseline, and edit surface are known
  - at least two credible approaches exist
  - candidate budget is justified by expected gain
```

Search Mode can then rely on existing runtime budgets such as
`budget.max_candidates`, `budget.max_parallel`, and host-specific
`strategy.worker_budget`.

## Completion Semantics

There are two completion checks:

1. **Search completion.** The runtime selected, reported, and optionally
   promoted the best candidate under the frozen spec.
2. **Goal completion.** The host agent audits the original user goal and
   verifies that the promoted result actually satisfies it.

If the search result improves the metric but leaves surrounding integration
work unfinished, `goal-plus` should keep working in Goal Mode instead of
claiming completion.

## Risks

- **Premature freeze.** The orchestrator may freeze a weak metric and optimize
  the wrong thing. Mitigation: require a confidence field and keep discovery
  cheap but explicit.
- **Spec drift.** The original goal and frozen spec may diverge. Mitigation:
  preserve `raw_goal` and require a final raw-goal audit.
- **Scenario overgrowth.** Domain packs may become mini-frameworks. Mitigation:
  keep packs declarative and verifier-focused.
- **Lifecycle overreach.** It is tempting to add wait/abort/status APIs to make
  `goal-plus` feel like a supervisor. Mitigation: keep lifecycle controls in
  host adapters and host-native surfaces unless the runtime contract is
  intentionally redesigned.

## Open Questions

- Should medium-confidence spec drafts require explicit user confirmation, or
  can the host agent proceed when the verifier is strong?
- Should scenario packs be plain docs first, or should they grow a tiny metadata
  schema once two or three domains repeat the same fields?
