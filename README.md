# Goal Plus (GP)

English | [简体中文](README_zh.md)

Goal Plus is a host-neutral runtime for long-running agent work. `/goal-plus`
handles ordinary goals directly and upgrades measurable optimization tasks to
Search Mode: freeze the evaluation contract, explore isolated candidates, and
promote the best verifier-backed result.

Pi and Codex are the maintained host paths. OpenCode and Claude Code assets
remain in the repository as unsupported references; they receive no
compatibility guarantee and are excluded from the default test gate.

## Quick Start

Install from Git or an existing checkout:

```bash
python -m pip install --user "git+https://github.com/ck0123/goal-plus.git"
# or
python -m pip install -e ".[dev]"
# add the optional self-contained Plotly trajectory to HTML reports
python -m pip install -e ".[dev,report]"
```

Every host launches the same stdio MCP server:

```text
goal-plus --root .gp
```

Then start a goal in the host:

```text
/goal-plus Fix this bug and verify the test suite.
/goal-plus Optimize p95 latency for two hours without changing correctness.
/goal-plus mode=probe Check whether vectorization is viable.
/goal-plus mode=autonomous Deeply optimize the kernel.
```

Codex and Pi also expose:

```text
/goal-plus edit <full revised goal>
/goal-plus resume
/goal-plus-with-final-check <goal>
```

One request starts an autonomous run. The agent decides whether Goal Mode is
enough or a frozen verifier makes parallel Search useful; entering Search does
not require an extra approval step. `mode=autonomous` (the default) gives
every initial candidate substantial, renewable same-workspace exploration leases;
`mode=probe` asks for short feasibility probes first. This exploration mode is
stored as guidance in the final line of `raw_goal`, not as a scheduler state.

## Hosts

| Host | Project assets | Entry | Search worker path |
|---|---|---|---|
| Pi | `.pi/` | `/goal-plus` or `pi -p "/goal-plus ..."` | durable Pi RPC pool; see [Pi](docs/pi.md) |
| Codex | `.codex/` | `goal-plus` skill or `/goal-plus` prompt | fixed parallel loops with native same-worker continuation; Codex 0.144.1+ hooks cover `UserPromptSubmit`, `PreToolUse`, and `SubagentStop`; see [Codex](docs/codex.md) |
| Claude Code | `.mcp.json`, `.claude/` | unsupported reference assets | not maintained; see [Claude Code](docs/claude-code.md) |
| OpenCode | `opencode.json`, `.opencode/` | unsupported reference assets | not maintained; see [OpenCode](docs/opencode.md) |

For Codex, copy `.codex/config.example.toml` to the ignored local
`.codex/config.toml`. Host differences and strategy coverage are summarized in
[Agent Host Adapters](docs/agent-host-adapters.md).

## Mental Model

- A **Goal Plus record** is the complete user task.
- A **search task** is one `run_id` over one frozen spec. A goal may link more
  than one search task.
- A **round** is a persisted planning decision. New Pi/Codex
  `parallel_loops` runs have exactly one initial round.
- A **candidate** is a long-lived autonomous loop in one isolated workspace
  with verifier history.
- A **worker session** is a host context/provenance handle. Worker lifecycle
  belongs to the host, not the Search runtime.
- A **verifier concern** is worker advice. Only the main agent can confirm it;
  confirmation fences the run before all host workers are stopped and a
  successor spec/run is created.

New Pi/Codex Search uses fixed parallel loops: create the initial candidates
once, validate every completion, update the verifier-backed global best, and
resume that same candidate while no global stop condition is true. Main does
not choose later technical directions or replace low-scoring candidates.
Slower workers do not block completed work from being evaluated. See
[Flow](docs/flow-view.md).

Keep one run for one valid evaluation/edit contract. If a successor is
unavoidable, `source_run_id` preserves bounded frontier/features/scoped
pitfalls as research context, never as reusable scores.

Runtime state lives under `.gp/`. `search_tasks` is append-only; `linked_search`
is only the compatibility view of the current task.

When `promotion_verifiers` are configured, promotion is an independent check,
not a cached pass-through. The runtime checks out the selected verifier-backed
revision, reruns each promotion gate with
`GOAL_PLUS_VERIFIER_PHASE=promotion`, binds the evidence to the selected Git
head and artifact hash, and only then emits a Git-applyable patch. A failed
promotion stays retryable in `ready_to_promote` and emits no patch.

## Documentation

| Need | Read |
|---|---|
| End-to-end ownership and parallel-loop flow | [Flow](docs/flow-view.md) |
| Architecture, state, and invariants | [Design](docs/design.md) |
| Current MCP and Pi-local tools | [API](docs/api.md) |
| Host capability comparison | [Agent Host Adapters](docs/agent-host-adapters.md) |
| Runtime and host logs | [Debugging](docs/debugging-runtime.md) |
| Specs and runnable examples | [Examples](examples/README.md) |
| Tests and real-host evidence | [Tests](tests/README.md) |

## Development

```bash
python -m pytest -q
git diff --check
```

The maintained strategy set for Pi and Codex is `agent_guided`
(`agent`/`default`) and `random` (`random_mode`). OpenCode/Claude tests are
explicit opt-in slices and do not run in `python -m pytest -q`.
