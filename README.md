# Goal Plus (GP)

English | [简体中文](README_zh.md)

Goal Plus is a host-neutral runtime for long-running agent work. `/goal-plus`
handles ordinary goals directly and upgrades measurable optimization tasks to
Search Mode: freeze the evaluation contract, explore isolated candidates, and
promote the best verifier-backed result.

Pi is the primary host path; Codex is the primary native multi-agent path.
Claude Code and OpenCode remain supported compatibility hosts.

## Quick Start

Install from Git or an existing checkout:

```bash
python -m pip install --user "git+https://github.com/ck0123/goal-plus.git"
# or
python -m pip install -e ".[dev]"
```

Every host launches the same stdio MCP server:

```text
goal-plus --root .gp
```

Then start a goal in the host:

```text
/goal-plus Fix this bug and verify the test suite.
/goal-plus Optimize p95 latency for two hours without changing correctness.
```

Codex and Pi also expose:

```text
/goal-plus edit <full revised goal>
/goal-plus resume
/goal-plus-with-final-check <goal>
```

One request starts an autonomous run. The agent decides whether Goal Mode is
enough or a frozen verifier makes parallel Search useful; entering Search does
not require an extra approval step.

## Hosts

| Host | Project assets | Entry | Search worker path |
|---|---|---|---|
| Pi | `.pi/` | `/goal-plus` or `pi -p "/goal-plus ..."` | durable Pi RPC pool; see [Pi](docs/pi.md) |
| Codex | `.codex/` | `goal-plus` skill or `/goal-plus` prompt | native rolling `spawn_agent` pool; Codex 0.144.1+ hooks cover `UserPromptSubmit`, `PreToolUse`, and `SubagentStop`; see [Codex](docs/codex.md) |
| Claude Code | `.mcp.json`, `.claude/` | `goal-plus` skill | foreground Agent compatibility path; see [Claude Code](docs/claude-code.md) |
| OpenCode | `opencode.json`, `.opencode/` | `/goal-plus` | broadest legacy strategy coverage; see [OpenCode](docs/opencode.md) |

For Codex, copy `.codex/config.example.toml` to the ignored local
`.codex/config.toml`. Host differences and strategy coverage are summarized in
[Agent Host Adapters](docs/agent-host-adapters.md).

## Mental Model

- A **Goal Plus record** is the complete user task.
- A **search task** is one `run_id` over one frozen spec. A goal may link more
  than one search task.
- A **round** is a persisted planning decision, not a synchronization barrier.
- A **candidate** is an isolated workspace with verifier history.
- A **worker session** is a host context/provenance handle. Worker lifecycle
  belongs to the host, not the Search runtime.

Search uses a rolling pool: fill up to `budget.max_parallel`, react whenever
any worker finishes, and immediately continue that direction, launch another
candidate, leave the slot idle, or drain for selection. Slower workers do not
block completed work from being evaluated. See [Flow](docs/flow-view.md).

Runtime state lives under `.gp/`. `search_tasks` is append-only; `linked_search`
is only the compatibility view of the current task.

## Documentation

| Need | Read |
|---|---|
| End-to-end ownership and rolling pool flow | [Flow](docs/flow-view.md) |
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

The portable strategy set for Pi, Codex, and Claude Code is `agent_guided`
(`agent`/`default`) and `random` (`random_mode`). OpenCode remains the
compatibility host for the existing higher-touch strategies and trace export.
