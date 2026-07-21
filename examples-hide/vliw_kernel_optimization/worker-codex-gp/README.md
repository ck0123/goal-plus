# VLIW Goal Plus SpaceAgent Experiment

This workspace is the public, agent-facing portion of the SE-Bench task. The
complete task statement is in `problem.md`.

The current experiment runs one global SpaceAgent in `enforce` mode over three
concurrent Goal Plus candidate lanes. Each lane has a one-hour worker lease.
There is no control arm and SpaceAgent only accepts a plan or rejects it with
the plans it duplicates; it never suggests an optimization direction.

The experiment assets are:

- `experiment.json`: frozen 3-lane runtime and reviewer configuration;
- `../prompts/codex-gp-space-3x1h.txt`: top-level orchestration and candidate prompt;
- `space-schema.json`: initial semantic intervention schema;
- `.goal-plus-verifiers/vliw_score.py`: public ranking verifier adapter;
- `run_experiment.py`: in-place launcher, reviewer service, and result collector.

The current reviewer uses strict Pydantic validation over plain JSON because
the configured provider rejects the Codex CLI `--output-schema` transport. The
launcher inherits its Codex profile from `CODEX_HOME`, runs real admission and
Schema-consolidation preflights before Search creation, and aborts the experiment
on any later `reviewer_fail_open` admission.

Run from the Goal Plus repository root:

```bash
python examples-hide/vliw_kernel_optimization/worker-codex-gp/run_experiment.py \
  --experiment-id "vliw-space-3x1h-$(date +%Y%m%d-%H%M%S)"
```

The launcher verifies the starter solution, initializes a nested Git baseline,
and creates candidate worktrees under the repository-level `.gp/`. Host logs and the final summary are
stored under the repository's ignored
`output/vliw-space-agent-current/<experiment-id>/`, outside the candidate
source tree. No experiment state is written under `/root`.
Use `--prepare-only` to validate and materialize the frozen prompt without
starting Codex.

## Quick Start

Edit `solution.py`, then run:

```bash
python3 runner.py
```

The runner loads `test_cases/public_cases.json`, calls `solution.py`, and uses
`verifier.py` to check correctness, report simulated cycle counts, and print
`Score: <value>`.

The public cases are only a smoke test. Official evaluation uses private test
cases and the frozen simulator under the evaluator's private files.

## Score

Correctness is mandatory. If any case is incorrect, the submission is invalid
and is not eligible for best-score comparison. Otherwise, `cycles` is the
maximum cycle count across performance cases, and score is computed directly
from that cycle count:

```text
Score = cycles
```

Lower scores are better. The verifier still reports the benchmark thresholds as
interpretability markers.

## Constraints

- Edit `solution.py`; do not change `problem.py`, `runner.py`, `verifier.py`, or
  files under `test_cases/`.
- Do not use network access.
- Do not hard-code public or hidden random seeds.
