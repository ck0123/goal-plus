# swe_bench_20212

Self-contained reproduction of SWE-bench instance **`sympy__sympy-20212`** (sympy
issue [#19572](https://github.com/sympy/sympy/issues/19572)). Used by
`examples/swe_bench_20212_search_spec.json`.

This fixture is intentionally dependency-free: only the Python standard library
is required. It does not import sympy and does not need docker.

## The bug

`0 ** -oo` should evaluate to `zoo` (ComplexInfinity), but the buggy baseline
returns `0`. Upstream fix is two lines in `sympy/core/power.py::Pow.__new__`:

```python
if evaluate:
    if b is S.Zero and e is S.NegativeInfinity:
        return S.ComplexInfinity
```

In this fixture, `evaluate_power(base, exponent)` mirrors that decision logic
using local singleton symbols (`ZERO`, `NEG_INFINITY`, `COMPLEX_INFINITY`, ...).
The candidate must fix `evaluate_power` so that
`evaluate_power(ZERO, NEG_INFINITY)` returns `COMPLEX_INFINITY` while every
other case keeps its current behavior.

## Files

| File | Role |
|---|---|
| `initial_program.py` | Buggy baseline. The search runtime copies this into each candidate workspace. Editable by the worker. |
| `evaluator.py` | Frozen scorer. Loads the candidate program, runs the assertions, returns `combined_score`. Not editable. |
| `config.yaml` | Fixture metadata. Not editable. |

## Score semantics

`evaluator.py::evaluate` runs six assertions split into two groups, mirroring
the SWE-bench contract:

- **FAIL_TO_PASS** (2 rows, from the upstream `test_patch`): must fail on the
  baseline and pass after the fix.
- **PASS_TO_PASS** (4 rows): behavior the baseline already gets right. These
  guard against trivial fixes that break other cases.

The reported `combined_score` is:

```
ftp_rate * (0.7 + 0.3 * ptp_rate)
```

A candidate that hard-codes `return COMPLEX_INFINITY` still passes both
FAIL_TO_PASS rows but fails the `0**0 is 1` / `0**oo is 0` / `1**-oo is 1`
PASS_TO_PASS rows, so it caps at `1.0 * (0.7 + 0.3 * 0.25) = 0.775` rather
than 1.0.

## Run locally

From this directory:

```bash
# Buggy baseline — expect combined_score = 0.0
python3 -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2))"
```

To verify the gold patch reaches 1.0, add the missing branch into
`evaluate_power`:

```python
if base is ZERO and exponent is NEG_INFINITY:
    return COMPLEX_INFINITY
```

place it ahead of the generic `if base is ONE:` line, then re-run the
evaluator — `combined_score` should jump to `1.0`, with
`fail_to_pass_passed = 2/2` and `pass_to_pass_passed = 4/4`.

## Run via the MCP runtime

Use `examples/swe_bench_20212_search_spec.json` as the SearchSpec. The
`process_verifiers` block invokes the local evaluator command directly, so no
container or external service is required.
