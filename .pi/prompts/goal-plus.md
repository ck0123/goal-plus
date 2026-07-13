Call `goal_plus_create(raw_goal="$ARGUMENTS")` first, before triage, planning, editing, or search.
Except for loading the goal-plus skill, do not read or audit target files before `goal_plus_record_triage`.

# Goal Plus

Use `/skill:goal-plus` with this raw user goal:

$ARGUMENTS

When this Pi prompt opens Search Mode, the SearchSpec strategy must set
`worker_host: "pi-rpc"` and `worker_mode: "agent-session-pool"` so workers run
through the Pi RPC driver, not the default OpenCode host.

Before freezing, require each `ranking_signal` to print a final JSON object
with a finite numeric `spec.metric_name`. Keep verifier artifacts in a
source-owned materialized path such as `.goal-plus-verifiers/`, never `.gp/`
or `.search/`; `expected_outputs` lists artifact paths/globs only.
