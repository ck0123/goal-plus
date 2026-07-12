---
name: search-result-dump
description: >
  Dump a completed Agentic Search result by exporting its .gp runtime
  artifacts as Chrome Trace Event JSON for Perfetto or chrome://tracing.
argument-hint: >
  run_id, optional .gp root, optional opencode/ST log path.
---

# Search Result Dump

Use this skill after an Agentic Search run has produced a `.gp/runs/<run_id>/`
directory. It exports a lightweight timeline file rather than building a custom
dashboard.

## Inputs

Required:

- `run_id`

Optional:

- runtime root, default `.gp`
- OpenCode log path, default `~/.local/share/opencode/log/opencode.log`

## Workflow

1. Confirm `.gp/runs/<run_id>/run.json` exists.
2. Export the trace:

```bash
goal-plus-trace --root .gp --run-id <run_id>
```

Use `--opencode-log <path>` when the run came from a non-default OpenCode log:

```bash
goal-plus-trace --root .gp --run-id <run_id> --opencode-log <path>
```

The default output is:

```text
.gp/runs/<run_id>/trace.json
```

3. Tell the user to open the generated trace in one of:

```text
https://ui.perfetto.dev/
chrome://tracing
```

## What The Trace Shows

The trace is derived from runtime-owned evidence:

- run span from `run.json`
- plan events from `plans/*.json`
- subagent identity, workspace, directive, and counters from `agent_sessions/*.json`
- verifier spans from candidate `iterations`
- report and promotion artifacts from file timestamps

When an OpenCode log is available, subagent timing comes from OpenCode-observed
events:

- start from `message=created id=<opencode_session_id>`
- end from `message="exiting loop" session.id=<opencode_session_id>`
- loop/process/stream points as instant events

If those OpenCode records are missing, timing falls back to MCP session
`created_at` and `updated_at`.

## Output

Report the trace path, the source report path if present, and any limitation
that affects timing precision.
