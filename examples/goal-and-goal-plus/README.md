# `/goal` vs `/goal-plus` Ordinary Goal Experiment

This directory compares Codex `/goal` and `/goal-plus` on the same ordinary
single-path bug fix. The point is to test whether `/goal-plus` stays in Goal
Mode instead of spending Search Mode budget when the task is not search-shaped.

## Task

Both subdirectories start from the same self-contained SWE-bench style fixture:

- `initial_program.py` has a local reproduction of `sympy__sympy-20212`.
- `evaluator.py` scores the fix without external dependencies.
- Baseline score is `combined_score = 0.0`.
- Success is `combined_score = 1.0`.

The bug is that `evaluate_power(ZERO, NEG_INFINITY)` returns `ZERO`; it should
return `COMPLEX_INFINITY`.

## Runs

Both runs used the same model:

```bash
gpt-5.3-codex-spark
```

The `/goal` run:

```bash
cd examples/goal-and-goal-plus/goal
codex exec -C . --skip-git-repo-check \
  --dangerously-bypass-hook-trust \
  --dangerously-bypass-approvals-and-sandbox \
  --color never \
  -m gpt-5.3-codex-spark \
  -o final.md \
  - < prompt.md
```

The `/goal-plus` run:

```bash
cd examples/goal-and-goal-plus/goal-plus
codex exec -C . --skip-git-repo-check \
  --dangerously-bypass-hook-trust \
  --dangerously-bypass-approvals-and-sandbox \
  --color never \
  -m gpt-5.3-codex-spark \
  -o final.md \
  - < prompt.md
```

Raw Codex stdout was not stored because it can include local session and plugin
runtime details. The final agent messages are saved as `goal/final.md` and
`goal-plus/final.md`.

## Results

| Entry | Mode Used | Search Mode | Goal Plus MCP State | Final Score | Changed File |
|---|---:|---:|---:|---:|---|
| `/goal` | Goal | No | No | 1.0 | `initial_program.py` |
| `/goal-plus` | Goal | No | Yes | 1.0 | `initial_program.py` |

Both runs fixed the task and verified:

```bash
python -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2, sort_keys=True))"
```

Both final verifier summaries were:

```text
combined_score = 1.0
fail_to_pass = 2/2
pass_to_pass = 4/4
valid = true
```

## Patch Difference

The `/goal` run added the special case inside the `base is ZERO` branch:

```python
if base is ZERO:
    if exponent is NEG_INFINITY:
        return COMPLEX_INFINITY
    return ZERO
```

The `/goal-plus` run added the equivalent special case before the generic
`base is ONE` / `base is ZERO` handling:

```python
if base is ZERO and exponent is NEG_INFINITY:
    return COMPLEX_INFINITY
```

Both are minimal and pass the same evaluator.

## Goal Plus State

The `/goal-plus` run explicitly exercised the Goal Plus lifecycle tools:

1. `goal_plus_create`
2. `goal_plus_record_triage`
3. `goal_plus_set_status`
4. `goal_plus_gate(event="stop")`

The recorded state stayed in ordinary Goal Mode:

```json
{
  "status": "complete",
  "phase": "goal",
  "triage": {
    "is_optimization": false,
    "confidence": "high",
    "recommended_phase": "goal",
    "scenario": "local-python-bugfix"
  },
  "linked_search": null,
  "spec_draft": null
}
```

This is the expected behavior for a normal bug fix: `/goal-plus` should act like
`/goal` plus explicit triage and final audit, not like a multi-candidate search.

## Interpretation

For this ordinary task, `/goal-plus` did not improve the final score over
`/goal`; both reached 1.0. The useful difference is procedural:

- `/goal` solved the task directly with fewer lifecycle steps.
- `/goal-plus` added explicit triage and stop-gate evidence, while correctly
  avoiding Search Mode.

This supports the intended design: `/goal-plus` should be a drop-in replacement
for normal goal work, with extra guardrails, and only upgrade to Search Mode
when the task has a real measurable multi-approach search opportunity.
