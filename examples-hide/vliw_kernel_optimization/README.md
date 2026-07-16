# VLIW kernel optimization experiment

This internal experiment was extracted from the actual EdgeBench worker and
judge images. It provides three clean, independent agent workspaces with the
same starter solution:

- `worker/`: native Codex;
- `worker-claude/`: native Claude Code;
- `worker-codex-gp/`: single-lane Codex plus Goal Plus.

The directory also contains the private evaluator under `judge/`. Do not let an
optimizing agent inspect `judge/`, hidden cases, sibling workspaces, or files
outside its assigned worker directory.

## Worker and judge equivalence

- `problem.py` is byte-identical in the worker and judge images.
- `verifier.py` is byte-identical in the worker and judge images.
- `runner.py` differs only in its default public versus hidden case file.
- The judge adds hidden cases, a frozen simulator copy, and submission audits.
- The public and hidden performance cases have the same workload shape:
  `forest_height=10`, `rounds=16`, and `batch_size=256`, with different seeds.

All three committed worker solutions are the original starter. Its verified
public and hidden cycle count is `147734`.

## Evaluate

From `examples-hide/vliw_kernel_optimization/`:

```bash
python3 evaluate.py
python3 evaluate.py worker-claude/solution.py
python3 evaluate.py worker-codex-gp/solution.py
```

Add `--docker` to compare with the exact original images:

```bash
python3 evaluate.py --docker
```

The image tags are:

- `edgebench.work.vliw_kernel_optimization:9fa380a0ebef`
- `edgebench.judge.vliw_kernel_optimization:5cdef0021634`

## Native Codex

```bash
cd examples-hide/vliw_kernel_optimization/worker
codex --model gpt-5.5 -c 'model_reasoning_effort="medium"'
```

Paste the complete contents of `../prompts/manual-autonomous.txt`. If the agent
returns early, send this neutral continuation in the same conversation:

```text
Continue autonomously optimizing solution.py.
```

Resume a closed CLI from the same worker directory with:

```bash
codex resume --last
```

`run_codex.sh` is an optional wrapper that launches this same native workspace
with GPT-5.5 medium.

## Native Claude Code

```bash
cd examples-hide/vliw_kernel_optimization/worker-claude
claude --model opus --effort medium
```

Paste the same `../prompts/manual-autonomous.txt`. Continue an early return with
the same neutral sentence. Resume a closed CLI from the same directory with:

```bash
claude --continue
```

This controls the task, starter, public evaluator, and visible prompt across
the two native runs. It does not control the model: native Claude Code does not
run GPT-5.5.

## Single-lane Codex plus Goal Plus

```bash
cd examples-hide/vliw_kernel_optimization/worker-codex-gp
codex --model gpt-5.5 -c 'model_reasoning_effort="medium"'
```

Paste `../prompts/codex-gp-single-lane.txt`. The prompt begins with
`/goal-plus mode=autonomous` and requires:

- `budget.max_candidates=1` and `budget.max_parallel=1`;
- one 7200-second initial Codex worker dispatch;
- same-agent continuation through `search_continue_agent_session` and
  `followup_task` when the worker returns early;
- same-candidate redispatch only if native continuation is unavailable;
- no technical direction from the main agent beyond evaluation-contract and
  lifecycle enforcement.

If the top-level Codex session is interrupted, resume the durable goal from the
same directory:

```bash
codex resume --last "/goal-plus resume"
```

Monitor it read-only from the Goal Plus repository root:

```bash
./scripts/monitor_goal_plus.sh --no-clear \
  examples-hide/vliw_kernel_optimization/worker-codex-gp
```

## Reset

From `examples-hide/vliw_kernel_optimization/`, reset any worker to the shared
starter without touching the other experiments:

```bash
cp snapshots/starter_solution.py worker/solution.py
cp snapshots/starter_solution.py worker-claude/solution.py
cp snapshots/starter_solution.py worker-codex-gp/solution.py
```

Runtime `.gp/`, `results.tsv`, bytecode caches, nested Git repositories, and
raw host transcripts are intentionally absent from the committed fixture.
`session_timeline.py` can be pointed at a separately retained Codex session
JSONL when transcript-level tool and message inspection is needed.
