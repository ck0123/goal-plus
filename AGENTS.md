# AGENTS.md

This file is the entry point for agents developing or maintaining this
repository. It applies to the whole repository unless a nested `AGENTS.md`
overrides it.

## Read First

Before changing behavior, read the smallest relevant set of docs:

- [README.md](README.md): project overview, install path, host summary.
- [docs/design.md](docs/design.md): runtime architecture, data model, state
  flow, and ownership boundaries.
- [docs/flow-view.md](docs/flow-view.md): who calls which MCP tool, what each
  agent sees, and OpenCode-specific platform constraints.
- [docs/agent-host-adapters.md](docs/agent-host-adapters.md): OpenCode, Codex,
  and Claude Code adapter contract, capability matrix, budget rules, and
  current strategy support.
- [docs/debugging-runtime.md](docs/debugging-runtime.md): `.gp` state,
  host-native logs, OpenCode SQLite inspection, Codex rollout logs, and Claude
  Code transcript/debug paths.
- [docs/opencode.md](docs/opencode.md), [docs/codex.md](docs/codex.md), and
  [docs/claude-code.md](docs/claude-code.md), and [docs/pi.md](docs/pi.md):
  host-specific setup and behavior.
- [examples/README.md](examples/README.md): example specs, strategy modes, and
  scenario prompts.
- [tests/README.md](tests/README.md): unit/integration/system test layout and
  commands.

Strategy-specific docs:

- [docs/strategy-openevolve.md](docs/strategy-openevolve.md)
- [docs/strategy-adaptevolve.md](docs/strategy-adaptevolve.md)

Recent implementation evidence:

- [docs/worker-budget-smoke.md](docs/worker-budget-smoke.md)

## Code Discovery

Prefer the codebase knowledge graph tools when available:

- `search_graph` for functions, classes, and symbols.
- `get_code_snippet` for exact source after locating a symbol.
- `trace_path` or `query_graph` when available for call relationships and
  structural queries.

If the graph server is unavailable or the target is a non-code file, use `rg`
or `rg --files`. Use `rg` for docs, config values, string literals, prompts,
fixtures, and test assets.

## Core Boundary

This project is a `/goal-plus` runtime with an internal Search Mode engine, not
a worker supervisor.

The runtime owns:

- goal-plus intake, triage, spec drafts, verifier confirmation state, and final
  audit evidence
- frozen specs and verifier artifacts
- candidate workspace creation
- planning and strategy state
- verifier execution and score reports
- durable `.gp/` run state
- report generation and promotion patches

The host code-agent owns:

- worker launch, lifecycle, stop/interrupt, and return values
- host step, turn, or time budget enforcement
- native logs and transcripts

Do not add runtime-owned wait loops, abort APIs, heartbeats, lifecycle status,
observation buses, or host-sync state unless the runtime contract is explicitly
redesigned and documented.

`AgentSessionRecord` is a context/provenance handle, not a lifecycle record.
`search_start_agent_session` returns a host-native launch payload. The main
agent must treat that launch payload as authoritative and spawn a foreground
worker through the selected host.

## Hidden-Answer Benchmarking

Standard-answer benchmarks such as MMLU/formal_logic, ARC, WinoGrande,
TruthfulQA, and GSM8K require a benchmark protocol that keeps correctness
feedback hidden from workers. Do not report those results from normal Search
Mode when workers can call a verifier that returns correctness, score, gold
labels, predictions, or any other hidden-answer signal. Even a bare correct /
incorrect result is an oracle for multiple-choice tasks.

It is fine to expose a public verifier that only checks submission format, such
as `answer.json` shape, a valid choice label, or a parseable numeric answer.
For hidden-answer benchmarks, keep the scoring grader outside the worker-visible
SearchSpec and run it only after all candidate answers are final.

When building or running such benchmarks:

- For comparison against an existing completed run, reuse the repository case
  identity manifest and verify `benchmark`, `case_index`, and
  `question_sha256`. Do not silently replace it with "first N" cases. The
  checked-in manifest must not include raw questions, gold answers, result
  history, or local filesystem paths.
- Do not put gold labels, answer keys, gold-file paths, or hidden-answer scoring
  commands in frozen specs, worker context, candidate workspaces, public
  process verifiers, or promotion verifiers visible to workers.
- Answering agents must not use web search, external lookup, or local answer
  search outside the candidate workspace. Do not inspect Hugging Face caches,
  benchmark reports/runs, other repositories, or dataset files to recover
  answers.
- Do not select a final answer by verifier score or `search_select` when the
  score uses hidden gold. Use a gold-independent rule such as majority vote,
  first valid answer with a fixed tie-break, or another predeclared aggregator.
- Store hidden gold only in the parent evaluator after worker execution, or keep
  it in memory until scoring. Do not create adjacent `_gold` files before worker
  runs.
- If a user asks to benchmark `/goal-plus` on hidden-answer datasets, explain
  this protocol distinction before running: normal verifier-guided Search Mode
  measures optimization with feedback, while hidden-answer QA benchmarks measure
  answer quality under verifier-hidden evaluation.

## Directory Map

- `src/agentic_any_search_mcp/models.py`: strict Pydantic data models and
  validation.
- `src/agentic_any_search_mcp/goal_plus.py`: file-backed goal-plus state
  machine for raw goal intake, triage, spec drafts, gates, and search links.
- `src/agentic_any_search_mcp/runtime.py`: file-backed Search Mode state
  machine for workspace copy, verifier execution, selection, reports, and
  promotion.
- `src/agentic_any_search_mcp/agent_hosts.py`: host adapters for OpenCode,
  Codex, and Claude Code. Keep host launch/continue/budget mapping here.
- `src/agentic_any_search_mcp/tools.py`: JSON-friendly facade used by tests and
  MCP.
- `src/agentic_any_search_mcp/server.py`: FastMCP stdio server.
- `src/agentic_any_search_mcp/strategies/`: strategy plugins and helpers.
- `src/agentic_any_search_mcp/trace_export.py`: OpenCode trace export tooling.
- `src/agentic_any_search_mcp/pi_tool.py` and
  `src/agentic_any_search_mcp/pi_worker.py`: Pi extension facade and Pi RPC
  worker runner.
- `.opencode/`: OpenCode goal-plus/search skills, commands, and worker agents.
- `.codex/`: Codex goal-plus/search skills and worker agent assets.
- `.claude/`: Claude Code goal-plus/search skills and worker agents.
- `.pi/`: Pi prompt templates, skills, and extension tools.
- `docs/`: design, adapter, host, debug, and strategy documentation.
- `examples/`: example SearchSpec files.
- `tests/`: unit/integration tests, asset tests, fixtures, and opt-in
  OpenCode system tests.

Generated or local-only state:

- `.gp/` is runtime output and is gitignored.
- `.tmp/` is local scratch and is gitignored.
- `docs/superpowers/` is gitignored.
- Raw host logs and transcripts should stay in ignored locations such as
  `.gp/host-logs/`.

## Host Adapter Rules

Keep runtime behavior host-neutral. Host-specific behavior belongs in
`agent_hosts.py`, host asset files, and host docs/tests.

Current host expectations:

- OpenCode is the compatibility baseline. It supports the existing
  OpenCode-tested strategies, `Task(task_id=...)` continuation, step-tiered
  agents, and OpenCode trace export. It does not currently provide
  hook-enforced Goal Plus lifecycle gates in this repository.
- Codex supports the portable builtin strategy subset. `worker_budget` requires
  `max_runtime_seconds` and is enforced by parent watchdog metadata:
  `wait_agent(timeout_ms=...)`, then `interrupt_agent` or
  `send_input(..., interrupt=true)` when the deadline expires. Codex ships
  `PostToolUse(goal_plus_create)` session binding and a session-scoped Stop
  hook backstop; PreToolUse/SubagentStop gates remain manual.
- Claude Code supports the portable builtin strategy subset. `worker_budget`
  requires `max_turns` and maps known budgets to `.claude/agents/*.md`
  `maxTurns` definitions. Claude Code ships `PostToolUse(goal_plus_create)`
  session binding and a session-scoped Stop hook backstop;
  PreToolUse/SubagentStop gates remain manual.
- Pi RPC supports the portable builtin strategy subset. `worker_budget`
  requires `max_runtime_seconds`; `max_turns` is only a prompt hint. Pi uses
  `agentic-any-search-pi-worker` to launch foreground `pi --mode rpc` workers
  with `--no-session` from candidate workspaces and explicitly loads
  `.pi/extensions/search-runtime.ts`. Pi RPC does not support same-worker
  continuation; recover with `search_redispatch_candidate` and runtime/Git
  state. Pi has extension pre-tool guarding and skill stop gates, but no Codex
  Stop hook parity.

Portable strategy names for non-OpenCode hosts are currently:

- `agent_guided`
- `agent`
- `default`
- `random`
- `random_mode`

Do not enable additional Codex or Claude Code strategies without adding unit or
mock coverage for launch payloads and at least one real smoke path when the
behavior depends on host execution.

Host workers are foreground by design. Do not switch the adapter flow to
background subagents unless the design docs, host skills, runtime validation,
and tests are updated together.

Do not describe a host as fully hook-enforced Goal Plus unless the repository
ships and tests hook wiring at all relevant Stop, SubagentStop, and PreToolUse
checkpoints. Codex and Claude Code currently provide ownership binding plus a
session-scoped Stop backstop, not full process supervision.

## Asset And Prompt Changes

Host assets are executable product surface, not decorative docs. When changing
the runtime contract, update the matching assets and tests:

- OpenCode: `.opencode/skills/search/SKILL.md`,
  `.opencode/skills/goal-plus/SKILL.md`, `.opencode/command/goal-plus.md`,
  `.opencode/command/goal-any-optimize.md`,
  `.opencode/agents/goal-plus-orchestrator.md`,
  `.opencode/agents/AnySearchAgent*.md`,
  `.opencode/agents/search-orchestrator.md`, and
  `tests/test_opencode_assets.py`.
- Codex: `.codex/skills/goal-plus/SKILL.md`,
  `.codex/skills/search/SKILL.md`,
  `.codex/agents/any_search_agent.toml`, `.codex/config.example.toml`, and
  `tests/test_codex_assets.py`.
- Claude Code: `.claude/skills/goal-plus/SKILL.md`,
  `.claude/skills/search/SKILL.md`,
  `.claude/agents/any-search-agent*.md`, `.mcp.json`, and
  `tests/test_claude_assets.py`.
- Pi: `.pi/prompts/goal-plus.md`, `.pi/prompts/any-search-worker.md`,
  `.pi/skills/goal-plus/SKILL.md`, `.pi/extensions/search-runtime.ts`, and
  `tests/test_pi_assets.py`.

Do not let agents rediscover retired runtime APIs. The deleted lifecycle,
observation, submit, abort, and host-sync APIs must not reappear in host assets.

## Testing

Default verification:

```bash
python -m pytest -q
git diff --check
```

Focused tests:

```bash
python -m pytest tests/test_runtime_unit.py -q
python -m pytest tests/test_agent_hosts.py tests/test_models.py -q
python -m pytest tests/test_opencode_assets.py tests/test_codex_assets.py tests/test_claude_assets.py -q
```

Opt-in system tests drive real OpenCode and are skipped by default:

```bash
python -m pytest -m st -k k_module_smoke -v -s
python -m pytest -m st -v -s
```

Run ST only when host credentials, `opencode`, and the `search-runtime` MCP
connection are available. See [tests/README.md](tests/README.md) for preflight
checks and environment variables.

## Debugging

Use [docs/debugging-runtime.md](docs/debugging-runtime.md) as the first debug
entry point.

High-level rule:

1. Use the read-only monitor tool first for Goal Plus/Search status.
2. Inspect `.gp/runs/<run_id>/...` only when the monitor output does not
   include the field or artifact you need.
3. Inspect the host-native transcript/log only when debugging worker behavior.
4. Cross-reference by `agent_session_id`, `candidate_id`, and host handle.

Goal Plus/Search monitoring:

- Prefer the MCP monitor tool `goal_plus_monitor_snapshot` for active or
  completed Goal Plus/Search runs. It summarizes goal status, linked run state,
  selected candidate, candidate scores, verifier counts, subagent liveness,
  Pi token/cost/context metrics, stale warnings, and report/promotion paths.
- If the MCP tool is not directly exposed in the current host, use the matching
  facade instead of manually tailing files, for example:

  ```bash
  agentic-any-search-pi-tool goal_plus_monitor_snapshot \
    --root .gp \
    --args-json '{"goal_plus_id":"gp_...","run_id":"run_...","stale_after_seconds":120}' \
    --pretty
  ```

- Use raw `.gp/` files and host logs as a fallback for missing fields,
  transcript details, or verifier log inspection. Do not use manual file tailing
  as the primary monitoring path.

Host log sources:

- OpenCode: `~/.local/share/opencode/opencode.db` and
  `~/.local/share/opencode/log/opencode.log`.
- Codex: `codex exec --json`, `${CODEX_HOME:-~/.codex}/sessions/...`, and
  optional `RUST_LOG=debug codex -c log_dir=./.codex-log`.
- Claude Code: `claude -p --output-format stream-json`, `--debug-file`, and
  `~/.claude/projects/<encoded-project>/...`.
- Pi RPC: metadata-only `.gp/host-logs/pi-rpc-<agent_session_id>.jsonl` and,
  only when raw logging is explicitly enabled,
  `.gp/host-logs/pi-rpc-<agent_session_id>.txt`.

Never commit raw logs, transcripts, `.gp/`, or credentials.

## Implementation Style

- Keep changes scoped to the runtime/adapter/asset boundary implied by the
  task.
- Prefer existing Pydantic models and runtime helper methods over ad hoc JSON
  manipulation.
- Keep `.gp` state shape backward-readable unless a migration is explicitly
  designed.
- Keep verifier execution deterministic and runtime-owned.
- Do not edit frozen verifier artifacts or candidate workspaces by hand except
  inside tests that intentionally create fixtures.
- Use `apply_patch` for manual file edits.
- Use ASCII unless the target file already uses non-ASCII or the content
  clearly needs it.

## Commit Hygiene

- Keep commits focused.
- Do not commit `.gp/`, `.tmp/`, raw host logs, local transcripts, auth
  files, or generated caches.
- Before committing behavior changes, run `python -m pytest -q` and
  `git diff --check`.
