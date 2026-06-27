# Debugging Runtime State

How to inspect a running or finished search — what the agents are doing, what scores they've produced, and where to look when something goes wrong.

For the general OpenCode inspection technique (SQLite DB, log files), see the `inspecting-opencode-runs` skill. This doc covers the project-specific surface.

## Three Layers of State

```
OpenCode process
  ├─ SQLite DB (~/.local/share/opencode/opencode.db)   ← agent actions, tool calls, bash cmds
  ├─ Log file  (~/.local/share/opencode/log/opencode.log) ← permission decisions, errors
  └─ MCP server subprocess
       └─ .search/  ← runtime-owned durable state (this project)
```

## `.search/` Layout

```
.search/
├── specs/<frozen_spec_id>/
│   ├── frozen_spec.json                          # the frozen SearchSpec
│   └── verifier_artifacts/<path>                 # frozen verifier files (hash-pinned)
└── runs/<run_id>/
    ├── run.json                                  # RunRecord: state, candidates_total/evaluated, best
    ├── plans/<plan_id>.json                      # SearchPlan snapshots
    ├── candidates/<candidate_id>/
    │   ├── candidate.json                        # CandidateRecord: status, score_report, iterations[]
    │   ├── task.json                             # CandidateTask snapshot
    │   └── logs/<verifier_name>.log              # verifier stdout/stderr per call
    ├── workspace/<candidate_id>/                 # the agent's editable workspace
    │   ├── .git/                                 # agent's git history (autoresearch loop)
    │   ├── .tmp/results.tsv                      # agent's private iteration log
    │   └── <allowed_files>
    ├── agent_sessions/<agent_session_id>.json    # AgentSessionRecord: status, phase, counters, heartbeat
    ├── agent_events/<event_id>.json              # AgentSessionEvent: lifecycle + supervisor wakeups
    └── observations/<observation_id>.json        # cross-session shared findings
```

## Quick Diagnostic Queries

### Run summary

```bash
RUN=$(ls -td .search/runs/* | head -1)
python3 -c "
import json
d = json.load(open('$RUN/run.json'))
print(f\"state={d['state']} candidates={d['candidates_total']}/{d.get('candidates_evaluated',0)} evaluated best={d.get('best_candidate_id')}/{d.get('best_score')}\")
"
```

### All session states

```bash
for f in $RUN/agent_sessions/*.json; do
  python3 -c "
import json
d = json.load(open('$f'))
print(f\"{d['candidate_id']}: status={d['status']} phase={d['phase']} \"
      f\"vruns={d['counters']['verifier_runs']}/{d['budget']['max_verifier_runs']} \"
      f\"heartbeat={d['last_heartbeat_at']}\")
"
done
```

### Iteration history per candidate

```bash
for f in $RUN/candidates/*/candidate.json; do
  python3 -c "
import json
d = json.load(open('$f'))
iters = d.get('iterations', [])
print(f\"{d['candidate_id']}: {len(iters)} iterations\")
for it in iters:
    print(f\"  iter {it['iteration']}: score={it['score']} \"
          f\"failure={it.get('failure_class')} \"
          f\"touched_denied={it['touched_denied_files']} \"
          f\"changed_files={it['changed_files']}\")
"
done
```

### Workspace git history (the autoresearch loop)

```bash
find $RUN/workspace -name ".git" -execdir sh -c 'echo "=== $(pwd) ===" && git log --oneline' \;
```

### Agent's private results.tsv

```bash
find $RUN/workspace -name "results.tsv" -exec sh -c 'echo "=== $1 ===" && cat "$1"' _ {} \;
```

## Live Monitoring

```bash
# Watch iterations accumulate every 30s
watch -n 30 "
  for f in $RUN/candidates/*/candidate.json; do
    python3 -c \"
import json
d = json.load(open('\$f'))
iters = d.get('iterations', [])
print(f\\\"{d['candidate_id']}: {len(iters)} iters, scores={[i['score'] for i in iters]}\\\")
\"
  done
"
```

## Common Failure Modes

### Agent marked "stale - no progress in background task"

- **Look at**: `agent_sessions/<id>.json` `last_heartbeat_at` vs `updated_at`
- **Cause**: Agent didn't call `search_update_agent_status` or `search_record_agent_step`. Common when the agent is deep in bash work and skips heartbeats.
- **Verification**: Cross-reference with OpenCode SQLite — `tool` count for the session. If bash count is high but heartbeat is stale, agent was busy but didn't tell MCP.

### Agent ran evaluator via bash (MCP bypass)

- **Look at**: SQLite `part` table for the session
  ```sql
  SELECT json_extract(data, '$.tool'), count(*)
  FROM part WHERE session_id='<SID>' AND json_extract(data, '$.type')='tool'
  GROUP BY 1;
  ```
- **Symptom**: `bash` count high, `search-runtime_search_run_verifier` count 0 or low
- **Cause**: Agent didn't trust MCP path, or `worker_local_verifier_max_runs` was 0 (now forbidden)
- **Verification**: Look at bash command contents — if `python evaluator.py` appears, agent bypassed MCP

### Candidate has 0 iterations but session shows activity

- **Look at**: `agent_sessions/<id>.json` `counters.verifier_runs` vs `candidates/<id>/candidate.json` `iterations` length
- **Cause**: They should always match (every run_verifier call appends an iteration). If they don't, runtime state is inconsistent — check for exceptions in `agent_events/`.

### Session status is "aborted" but workspace files keep changing

- **Look at**: `agent_sessions/<id>.json` `updated_at` vs workspace file mtimes
- **Cause**: MCP `abort` only marks state; it doesn't kill the OS process. OpenCode's Task background process keeps running until it finishes or OpenCode itself kills it.
- **Implication**: The runtime's view (aborted) and reality (still running) can diverge. Trust the workspace mtimes + SQLite for "is it actually doing work?"

### Verifier fails with "EditSurfaceViolation"

- **Look at**: `candidates/<id>/candidate.json` latest iteration's `touched_denied_files` and `changed_outside_allowed`
- **Cause**: Agent edited files outside `edit_surface.allow` or modified `deny`-listed files
- **List offending files**: `changed_files` field in the iteration record

## MCP APIs for Inspection (no SQLite needed)

These tools are safe to call anytime — they're read-only:

| Tool | What it shows |
|---|---|
| `search_status(run_id)` | Run state, candidate counts, best score |
| `search_list_history(run_id, top_n, sort_by)` | Top candidates by score |
| `search_list_agent_status(run_id)` | All session states + counters |
| `search_list_iterations(run_id, candidate_id)` | Full iteration history for a candidate |
| `search_list_observations(run_id)` | Cross-session shared findings |
| `search_get_agent_context(agent_session_id)` | What a specific subagent sees (including its own iterations) |

## Cross-Referencing Layers

When something goes wrong, cross-reference all three layers:

1. **OpenCode SQLite** — what the agent *did* (tool calls, bash commands, messages)
2. **OpenCode log** — what the host *permitted* (permission decisions, errors)
3. **`.search/` runtime state** — what the runtime *recorded* (scores, iterations, events)

Example: "session aborted as stale"
- SQLite shows: agent called `bash: 8`, `search_run_verifier: 0`
- Log shows: no errors, all permissions allowed
- Runtime shows: `counters.verifier_runs=0`, `last_heartbeat_at` unchanged for 5 min

→ Diagnosis: Agent was doing bash work (probably running evaluator directly), never told MCP it was alive, supervisor marked stale. Fix: bump `worker_local_verifier_max_runs` so agent uses MCP verifier (which auto-updates counters as a side effect).
