# AGENTS.md

This file is the entry point for agents developing or maintaining this
repository. It applies to the whole repository unless a nested `AGENTS.md`
overrides it.

## Read First

Read only the page that owns the question:

- [README.md](README.md): install, quick start, and document map.
- [docs/flow-view.md](docs/flow-view.md): canonical end-to-end flow.
- [docs/design.md](docs/design.md): architecture, data, and invariants.
- [docs/api.md](docs/api.md): current MCP and Pi-local tool index.
- [docs/agent-host-adapters.md](docs/agent-host-adapters.md): capability matrix
  and shared host-pool contract.
- [docs/debugging-runtime.md](docs/debugging-runtime.md): state and host logs.
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

This project is a `/goal-plus` runtime with an internal Search Mode engine. The
Search runtime is not a worker supervisor; host integrations may provide a
supervisor behind the documented host-pool contract.

The runtime owns:

- goal-plus intake, triage, spec drafts, verifier confirmation state, and final
  audit evidence
- frozen specs and verifier artifacts
- candidate workspace creation
- planning and strategy state
- verifier execution and score reports
- durable `.gp/` run state
- report generation and promotion patches

The host code-agent or host-local supervisor owns:

- worker launch, lifecycle, stop/interrupt, and return values
- host step, turn, or time budget enforcement
- native logs and transcripts

Do not add SearchTools/runtime-owned wait loops, abort APIs, heartbeats,
lifecycle status, observation buses, or host-sync state. Host-local pool state
must stay outside Search run records and preserve the ownership boundary.

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

- `src/goal_plus/models.py`: strict Pydantic data models and
  validation.
- `src/goal_plus/goal_plus.py`: file-backed goal-plus state
  machine for raw goal intake, triage, spec drafts, gates, and search links.
- `src/goal_plus/runtime.py`: file-backed Search Mode state
  machine for workspace copy, verifier execution, selection, reports, and
  promotion.
- `src/goal_plus/agent_hosts.py`: host adapters for OpenCode,
  Codex, and Claude Code. Keep host launch/continue/budget mapping here.
- `src/goal_plus/tools.py`: JSON-friendly facade used by tests and
  MCP.
- `src/goal_plus/server.py`: FastMCP stdio server.
- `src/goal_plus/strategies/`: strategy plugins and helpers.
- `src/goal_plus/trace_export.py`: OpenCode trace export tooling.
- `src/goal_plus/pi_tool.py` and
  `src/goal_plus/pi_worker.py`: Pi extension facade and Pi RPC
  worker runner. `src/goal_plus/pi_pool.py` is the durable host-local pool
  supervisor.
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
  an initial `wait_agent`, one `send_message` closeout, a final wait, then
  `interrupt_agent` when the deadline expires. Search candidate PostTool hooks
  may inject one advisory when available time is below the observed average
  verifier-submission time; this does not stop the worker. Codex 0.144.1+ ships
  `UserPromptSubmit`, `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, and
  `SubagentStop` hooks with session ownership binding and terminal stats.
  New Codex specs use `orchestration_mode="parallel_loops"`: create initial
  candidates once, validate each completion, observe the verifier-backed best,
  and continue the same native worker through
  `search_continue_agent_session` plus `followup_task` while no global stop
  condition is true. Main must not choose later technical directions or create
  quality-based replacements.
- Claude Code supports the portable builtin strategy subset. `worker_budget`
  requires `max_turns` and maps known budgets to `.claude/agents/*.md`
  `maxTurns` definitions. Claude Code ships `PostToolUse(goal_plus_create)`
  session binding and a session-scoped Stop hook backstop;
  it does not yet wire PreToolUse or SubagentStop host hooks.
- Pi RPC supports the portable builtin strategy subset. `worker_budget`
  requires `max_runtime_seconds`; `max_turns` is only a prompt hint. Pi uses
  `goal-plus-pi-worker` to launch foreground `pi --mode rpc` workers
  with `--no-session` from candidate workspaces and explicitly loads
  `.pi/extensions/goal-plus.ts`. The runner may send one advisory `steer` after
  a Search candidate tool completes when available time is below the observed
  average verifier-submission time. Pi RPC does not support same-worker
  continuation; `pi_search_pool_continue` performs logical same-candidate
  continuation through `search_redispatch_candidate`, MCP history, verifier
  evidence, Git state, and bounded progress handoff metadata. New Pi specs use
  `orchestration_mode="parallel_loops"`; a fresh Pi session resumes the same
  workspace after each validated completion while no global stop condition is
  true. Pi has
  extension pre-tool guarding and skill stop gates, but no Codex Stop hook
  parity. Its main-agent pool is a durable host supervisor under
  `.gp/host-pools/pi/` with explicit open/submit/wait-any/snapshot/continue/
  close tools; it never plans or auto-refills candidates.

Portable strategy names for non-OpenCode hosts are currently:

- `agent_guided`
- `agent`
- `default`
- `random`
- `random_mode`

Do not enable additional Codex or Claude Code strategies without adding unit or
mock coverage for launch payloads and at least one real smoke path when the
behavior depends on host execution.

Host worker execution may be synchronous or managed asynchronous according to
`HostPoolContract`. Do not add untracked background work: every asynchronous
path needs an explicit wait/snapshot/close contract, budget enforcement,
matching host skills, and deterministic tests.

Do not describe a host as fully hook-enforced Goal Plus unless the repository
ships and tests hook wiring at all relevant Stop, SubagentStop, and PreToolUse
checkpoints. Codex provides those Goal Plus lifecycle checkpoints, but this is
not host process supervision. Claude Code currently provides ownership binding
plus a session-scoped Stop backstop.

## Asset And Prompt Changes

Host assets are executable product surface, not decorative docs. When changing
the runtime contract, update the matching assets and tests:

- OpenCode: `.opencode/skills/search/SKILL.md`,
  `.opencode/skills/goal-plus/SKILL.md`, `.opencode/command/goal-plus.md`,
  `.opencode/command/goal-any-optimize.md`,
  `.opencode/agents/goal-plus-orchestrator.md`,
  `.opencode/agents/SearchCandidateAgent*.md`,
  `.opencode/agents/search-orchestrator.md`, and
  `tests/test_opencode_assets.py`.
- Codex: `.codex/skills/goal-plus/SKILL.md`,
  `.codex/skills/goal-plus-with-final-check/SKILL.md`,
  `.codex/skills/search/SKILL.md`,
  `.codex/agents/search_candidate_agent.toml`,
  `.codex/agents/goal_plus_final_checker.toml`, `.codex/config.example.toml`, and
  `tests/test_codex_assets.py`.
- Claude Code: `.claude/skills/goal-plus/SKILL.md`,
  `.claude/skills/search/SKILL.md`,
  `.claude/agents/search-candidate-agent*.md`, `.mcp.json`, and
  `tests/test_claude_assets.py`.
- Pi: `.pi/prompts/goal-plus.md`, `.pi/prompts/search-candidate-worker.md`,
  `.pi/skills/goal-plus/SKILL.md`, `.pi/extensions/goal-plus.ts`, and
  `tests/test_pi_assets.py`.

Do not let agents rediscover retired runtime APIs. The deleted lifecycle,
observation, submit, abort, and host-sync APIs must not reappear in host assets.

## Testing

`pytest.ini` registers markers and defaults to `-n 4 --dist=load`. The default
gate runs fast unit tests in parallel; `integration`, `example`, `st`, and
`st_pi` tests are skipped unless named in `-m`. See [tests/README.md](tests/README.md)
for the full marker matrix.

Default verification:

```bash
python -m pytest -q          # parallel fast gate (default: -n 4 --dist=load)
git diff --check
```

Parallelism policy: use at most 50% of available CPUs for the default gate,
capped at `--numprocesses 16`. `pytest.ini` sets `-n 4` as a conservative
default for laptops; on large servers override upward:

```bash
python -m pytest -n 8 -q              # 16-core+ server
python -m pytest -n 16 -q             # 32-core+ server (do not exceed)
python -m pytest -p no:xdist -q       # disable parallelism entirely
```

When `python -c "import os; print(os.cpu_count())"` returns N, pick
`-n min(N // 2, 16)`. Larger values cause git lock contention inside the
runtime tests and stop scaling.

Focused tests:

```bash
python -m pytest tests/test_runtime_unit.py -q
python -m pytest tests/test_agent_hosts.py tests/test_models.py -q
python -m pytest tests/test_opencode_assets.py tests/test_codex_assets.py tests/test_claude_assets.py -q
python -m pytest -m codex -q          # all codex-slice tests
python -m pytest -m pi -q             # all pi-slice tests
```

Opt-in slices:

```bash
python -m pytest -m integration -q   # multi-round search end-to-end
python -m pytest -m example -q         # examples/* fixtures drive real assets
python -m pytest -m "st or st_pi" -v -s  # real-host system tests
```

Run ST only when host credentials, `opencode`, and the `goal-plus` MCP
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
  search strategy and latest-plan state, selected candidate, candidate scores,
  verifier counts, subagent liveness, Pi token/cost/context metrics, stale
  warnings, and report/promotion paths. Use `strategy.name` and
  `strategy.driver` to identify the algorithm; `plans_count` is only a round
  count.
- If the MCP tool is not directly exposed in the current host, use the matching
  facade instead of manually tailing files, for example:

  ```bash
  goal-plus-pi-tool goal_plus_monitor_snapshot \
    --root .gp \
    --args-json '{"goal_plus_id":"gp_...","run_id":"run_...","stale_after_seconds":120}' \
    --pretty
  ```

- Use raw `.gp/` files and host logs as a fallback for missing fields,
  transcript details, or verifier log inspection. Do not use manual file tailing
  as the primary monitoring path.

### HTML Report Export

`report.html` is an on-demand rendering of durable `.gp` state, not a live UI
owned by Pi, Codex, or a worker. `search_report(run_id)` reads the current
Goal Plus record, every linked Search task, plans, candidates, verifier
iterations, promotion evidence, and available host observability through the
same monitor/statistics layer, then writes both canonical artifacts:

- `.gp/runs/<run_id>/report.md`
- `.gp/runs/<run_id>/report.html`

In the normal Pi or Codex Search completion flow, the main agent must call
`search_report` after `search_select` and before `search_promote`, return both
paths to the user, and pass the Markdown path to
`goal_plus_record_search_result`. `search_promote` refreshes an existing report
after promotion, and the Goal Plus record stores the canonical HTML path.
Merely reaching a budget limit, finishing workers, or selecting a candidate
does not make the runtime generate a report by itself; the normal host skills
make the explicit `search_report` call.

Reports are reproducible views over saved evidence. To generate a missing
report or refresh an old one, call the logical MCP tool again with the existing
run id:

```text
search_report(run_id="run_...")
```

This recovery path remains valid after the original Pi/Codex main-agent or
worker sessions have exited. A later agent does not need the old conversation
context: it only needs the existing `run_id` and access to the same configured
`.gp` root. It should call `goal_plus_monitor_snapshot` first when the run id or
artifact state is uncertain, then call `search_report`. If native host logs or
observability were never persisted or have since been removed, generate the
report anyway and leave those metrics unavailable; do not rerun the Goal Plus
task merely to fill presentation gaps.

When MCP tools are unavailable in Pi, use the local facade:

```bash
goal-plus-pi-tool search_report \
  --root .gp \
  --args-json '{"run_id":"run_..."}' \
  --pretty
```

The HTML file is self-contained and opens directly without a web server. Never
invent absent metrics from transcript text: unavailable persisted evidence must
remain `Not observed`/unavailable in the report.

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
