# Debugging Runtime State

How to inspect a running or finished search — what the agents are doing, what scores they've produced, and where to look when something goes wrong.

For the general OpenCode inspection technique (SQLite DB, log files), see the `inspecting-opencode-runs` skill. This doc covers the project-specific surface.

## Two Layers of State

```
OpenCode process
  ├─ SQLite DB (~/.local/share/opencode/opencode.db)   ← agent actions, tool calls, bash cmds, child-session lifecycle
  ├─ Log file  (~/.local/share/opencode/log/opencode.log) ← permission decisions, errors
  └─ MCP server subprocess
       └─ .search/  ← runtime-owned durable state (this project)
```

The runtime owns specs, plans, candidate workspaces, iteration history, verifier scoring, reports, and promotion patches. OpenCode owns subagent lifecycle — start, run, step cap, stop/interrupt, completion notification. The MCP runtime does not maintain lifecycle status, host-sync state, or process cancellation. Debugging lifecycle state belongs in OpenCode; debugging candidate state belongs in `.search/`.

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
    │   ├── .tmp/results.tsv                      # iteration log: commit \t <metric_name> \t status \t hypothesis
    │   └── <allowed_files>
    ├── agent_sessions/<agent_session_id>.json    # AgentSessionRecord: candidate/OpenCode binding, launch payload, counters
    └── report.md / promotion/                    # final outputs
```

There is no `agent_events/` or `observations/` directory. The session record carries optional `opencode_session_id`, `launch` (the OpenCode Task fields), `directive`, and `counters.verifier_runs`.

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

Columns (tab-separated, autoresearch-aligned):

| col | name | meaning |
|---|---|---|
| 1 | `commit` | 7-char git short hash of the iteration's commit (commit-first: committed before verify) |
| 2 | `<metric_name>` | the frozen `spec.metric_name` literal (e.g. `combined_score`, `val_bpb`) — set by the main agent at freeze time |
| 3 | `status` | `keep` (improved, per `metric_direction`) or `discard` (regressed / verifier crash) |
| 4 | `hypothesis` | short description of what this iteration tried |

Example:

```
commit	combined_score	status	hypothesis
a1b2c3d	0.682	keep	baseline (concentric rings)
b2c3d4e	0.949	keep	hex lattice [5,4,5,4,5,3] s=0.1875
c3d4e5f	0.651	discard	switch to rectangular grid (regressed)
```

`discard` rows still carry a real commit hash; the commit was reset off the branch but remains in git reflog (~30 days), so `git -C <workspace> checkout <hash>` recovers any discarded experiment.

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

## Checking OpenCode Step Count

OpenCode enforces the per-agent `steps` cap (defined in each `.opencode/agents/*.md` frontmatter). Step count lives in OpenCode's session inspection tools (see the `inspecting-opencode-runs` skill), not in `.search/`. The runtime does not sync host state into MCP records.

When the step cap is reached OpenCode injects a system prompt instructing the agent to summarize and stop. Tools may be disabled during that final summary. OpenCode then notifies the main agent that the Task returned; the main agent runs `search_run_verifier` (without `agent_session_id`) to record the final score.

## Common Failure Modes

### Subagent appears idle in OpenCode but no iteration history

- **Look at**: `.search/runs/<run_id>/candidates/<id>/candidate.json` `iterations` and OpenCode SQLite `session` / `part` rows containing the `agent_session_id`.
- **Cause**: The subagent never called `search_run_verifier`. Inspect OpenCode SQLite for what it actually did (bash commands, tool calls).
- **Verification**: Confirm the OpenCode child session exists and ran to step cap or self-decision. The runtime only records what verifier calls actually happened.

### Agent ran evaluator via bash (MCP bypass)

- **Look at**: SQLite `part` table for the session
  ```sql
  SELECT json_extract(data, '$.tool'), count(*)
  FROM part WHERE session_id='<SID>' AND json_extract(data, '$.type')='tool'
  GROUP BY 1;
  ```
- **Symptom**: `bash` count high, `search-runtime_search_run_verifier` count 0 or low
- **Cause**: Agent didn't trust MCP path, or prompt was unclear about MCP being the official scorer
- **Verification**: Look at bash command contents — if `python evaluator.py` appears, agent bypassed MCP

### Candidate has 0 iterations but launch payload exists

- **Look at**: `agent_sessions/<id>.json` `counters.verifier_runs` vs `candidates/<id>/candidate.json` `iterations` length
- **Cause**: They should always match (every run_verifier call appends an iteration). If they don't, the subagent called `run_verifier` against a different candidate_id, or the main agent called it without `agent_session_id`.
- **Verification**: Check the iteration's `agent_session_id` field — it tells you which session (if any) the verifier call was attributed to.

### Same-session continuation is unavailable

- **Look at**: `agent_sessions/<id>.json` `opencode_session_id`
- **Cause**: Main agent did not call `search_bind_opencode_session` with the Task `metadata.sessionId`.
- **Verification**: `search_continue_agent_session` should return a launch payload containing `task_id`; without a binding it raises an error.

### Subagent is still running but I want to stop it

- **Cause**: Stopping a running subagent is an OpenCode/user interruption concern. There is no MCP abort tool.
- **Action**: Interrupt the OpenCode Task from the OpenCode UI or kill the OpenCode child session directly. The runtime does not need to be notified.

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
| `search_list_iterations(run_id, candidate_id)` | Full iteration history for a candidate |
| `search_get_agent_context(agent_session_id)` | What a specific subagent sees (including its own iterations) |

## Cross-Referencing Layers

When something goes wrong, cross-reference both layers:

1. **OpenCode SQLite** — what the agent *did* (tool calls, bash commands, messages) and what the *lifecycle* did (start, step cap, stop)
2. **`.search/` runtime state** — what the runtime *recorded* (scores, iterations, verifier logs)

Example: "OpenCode child session finished but the candidate shows no score"
- SQLite shows: matching OpenCode child session has equal `step-start` / `step-finish` counts and the agent never called `search_run_verifier`
- Runtime shows: `candidate.json` with empty `iterations`

→ Diagnosis: the subagent did not self-score. Have the main agent run `search_run_verifier(run_id, candidate_id, "process")` (without `agent_session_id`) to record the final score against the workspace state the subagent left behind.
