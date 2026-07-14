Call `goal_plus_create(raw_goal="$ARGUMENTS")` first, before triage, planning, editing, or search.
Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

# Goal Plus

Use `/skill:goal-plus` with this raw user goal:

$ARGUMENTS

When this Pi prompt opens Search Mode, the SearchSpec strategy must set
`worker_host: "pi-rpc"` and `worker_mode: "agent-session-pool"` so workers run
through the Pi RPC driver, not the default OpenCode host.

Before freezing, require each `ranking_signal` to print a final JSON object
with a finite numeric `spec.metric_name`. The command may be inline or call an
existing tool. Create a custom verifier file only when needed, and materialize
it during Spec Discovery before freezing in a source-owned path such as
`.goal-plus-verifiers/`, never `.gp/` or `.search/`. Spec Discovery may use
`bash`, `write`, and `edit` for this work. The freeze tool exposes the full
nested `SearchSpec` schema; do not guess fields from validation errors.
`expected_outputs` lists artifact paths/globs only.
The verifier must keep the candidate workspace read-only and use the unique
`GOAL_PLUS_VERIFIER_TMPDIR`/`TMPDIR` or Python `tempfile` for compiler and
temporary outputs. Never use one fixed `/tmp` path because
`pi_search_run_batch` may verify candidates concurrently. Freeze rejects any
workspace side effect before candidate budget is spent.

After the first meaningful optimization result is available, apply the skill's
existing raw-goal audit before assuming that the frozen spec should continue
unchanged. A large relative improvement over baseline does not show that the
result is close to meaningful success, especially when an absolute target or
acceptance criterion is unavailable. Explicitly consider `upgrade_spec`,
`keep_spec_with_justification`, or `revise_goal`; this is reasoning within the
existing flow, not a new runtime phase or approval step.
