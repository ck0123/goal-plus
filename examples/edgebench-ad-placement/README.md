# EdgeBench-Format Ad Placement Example

This is a local, public fixture for Goal Plus. Its agent-visible contract now
matches EdgeBench's `ad_placement_optimization` task rather than approximating
it with a Python program:

- the only submitted/editable artifact is `solution.cpp`
- compilation uses C++17 with `g++ -std=c++17 -O2`
- input is `n` followed by `n` lines of `x y r`
- output is `n` lines of integer `x1 y1 x2 y2` rectangles
- `tools/bin/gen SEEDS_FILE -d OUTPUT_DIR` writes `0000.txt`, `0001.txt`, ...
- `tools/bin/tester INPUT OUTPUT` writes `Score = <integer>` to stderr
- the solution has a five-second per-case limit; the local verifier also caps
  each tester call at five seconds for smoke-test safety
- the documented work environment is CPU-only, offline, with 1 GB of memory

The local cases are deterministic synthetic public data. The checked-in tester
implements the public AHC-style validity and satisfaction formula. It is not
the EdgeBench hidden judge, and the example deliberately has no promotion
verifier. Scores prove the local workflow, not benchmark performance.

The workspace contains:

- `solution.cpp`: legal one-cell baseline and the only allowed edit
- `tools/bin/gen`: local generator with the EdgeBench CLI contract
- `tools/bin/tester`: local tester with the EdgeBench CLI/output contract
- `.goal-plus-verifiers/ad_local_score.py`: compiles and evaluates ten public
  cases, then emits `{"local_score_sum": ...}` for Goal Plus

Run the public verifier directly:

```bash
cd examples/edgebench-ad-placement/workspace
python .goal-plus-verifiers/ad_local_score.py
```

Freeze all three verifier artifacts named above when using
`examples/edgebench_ad_placement_search_spec.json`. Do not call an EdgeBench
judge from this fixture.

[`report.md`](report.md) is retained as historical evidence from the earlier
Python-shaped EdgeBench-lite fixture. Its scores and edited artifact do not
describe the current C++/text-I/O example and should not be replayed as current
evidence.
