# EdgeBench-Lite Ad Placement Example

This example adapts the public shape of EdgeBench's
`ad_placement_optimization` task into a cheap, local Goal Plus/Search Mode
fixture. It is a reference example, not an official EdgeBench evaluation. Its
public synthetic cases, Python verifier, runtime limits, and score scale differ
from the official benchmark, so its scores must not be compared directly with
EdgeBench leaderboard results.

See [report.md](report.md) for a recorded 60.95-minute Pi E2E run with 16
candidates, eight sequential batches, bounded concurrency of two, Git-backed
candidate inheritance, final selection verification, and promotion.

The workspace under `workspace/` is the agent-visible work environment:

- `initial_program.py` is the only editable file.
- `cases.py` defines deterministic public cases.
- `evaluator.py` is the public process verifier used by GP during search.

The verifier boundary intentionally mirrors the useful part of EdgeBench for a
local example:

- GP/Search sees only the public process verifier.
- The SearchSpec leaves `promotion_verifiers` empty.
- A real hidden judge should stay outside GP, owned by a benchmark harness such
  as SForge.

Run the public verifier directly:

```bash
cd examples/edgebench-ad-placement/workspace
python -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), sort_keys=True))"
```

Run through the example SearchSpec with `/goal-plus` or the local MCP tools by
freezing `examples/edgebench-ad-placement/workspace/evaluator.py` as the
verifier artifact.

Worker verifier results are search evidence, not the final result. Selection
ranks verifier-backed committed iterations, checks out each exact Git commit,
and uses the main-agent verifier result as authoritative. If a historically
higher-scoring iteration fails final verification or times out, selection may
fall back to the next ranked iteration.
