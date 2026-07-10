# EdgeBench-Lite Ad Placement Workspace

Write the solver in `initial_program.py`.

Given each public case from `cases.py`, place one integer rectangle per ad on a
`10000 x 10000` grid. Rectangle `i` must contain the ad anchor point
`(x_i + 0.5, y_i + 0.5)`. Rectangles must stay inside the grid and must not
overlap. The score rewards rectangle areas close to each ad's target area.

Implement either:

- `solve_case(case) -> list[list[int]]`, returning rectangles
  `[x1, y1, x2, y2]` in ad order for one case.
- `solve_all(cases)`, returning either a list of per-case rectangle lists or a
  dict keyed by `case_id`.

Only edit `initial_program.py`. Do not edit `cases.py` or `evaluator.py`.

Check your score with:

```bash
python -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), sort_keys=True))"
```

Practical feasibility hint: if a rectangle covers another ad's anchor point,
that other ad may become impossible to place later without overlap. Expansion
heuristics should usually avoid covering any anchor point except their own.
