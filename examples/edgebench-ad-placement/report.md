# EdgeBench-Lite Pi Goal Plus E2E Report

This report records a sustained Pi Goal Plus/Search run of the local
EdgeBench-inspired ad-placement example. It demonstrates the GP workflow; it
is a reference example, not an official EdgeBench hidden-judge result. Its
`combined_score` is not directly comparable to EdgeBench leaderboard scores:
the example uses eight public synthetic cases and an unscaled 0-100 average,
while EdgeBench uses its official work/judge containers, hidden cases, runtime
limits, and score rescaling.

## Run Summary

- Date: 2026-07-10
- Host: Pi RPC
- Model: `openai-codex/gpt-5.6-sol`, thinking `low`
- Run: `run_20260710_053759_0c27d006`
- Duration: 3656.88 seconds (60.95 minutes)
- Search shape: 8 sequential batches, 2 concurrent candidates per batch
- Candidates: 16
- Public verifier calls: 107
- Editable surface: `initial_program.py` only
- Public workload: 8 deterministic cases, 1488 ads total
- Worker budget: 480 seconds, 30-turn prompt hint, interrupt on exceed
- CPU constraint: one thread through the standard BLAS/OpenMP/Torch thread
  environment settings

The run completed the full path:

```text
/goal-plus -> frozen spec -> search -> candidate workspaces -> Pi workers
  -> verifier-backed Git iterations -> selection -> report -> promotion
  -> final Goal Plus audit
```

The Goal Plus state reached `complete`, and the Search run reached `promoted`.

## Search Result

| Batch | Candidates | Best score after batch |
|---:|---|---:|
| 1 | c001, c002 | 98.55661918035690 |
| 2 | c003, c004 | 98.60556683402933 |
| 3 | c005, c006 | 98.62881399236852 |
| 4 | c007, c008 | 98.69973782437563 |
| 5 | c009, c010 | 98.71807195497317 |
| 6 | c011, c012 | 98.87049624594145 |
| 7 | c013, c014 | 98.93116731402698 |
| 8 | c015, c016 | 99.07634533278639 |

The main improvement lineage was:

```text
c002 -> c003 -> c005 -> c008 -> c010 -> c012 -> c013 -> c015
```

Candidate workspaces used one concrete code parent. Additional
`parent_candidate_ids` supplied official history or inspiration only; the
runtime did not recursively merge candidate workspaces.

## Final Selection

Worker verifier history is search evidence and a ranking signal, not the final
authority. `search_select` checks out ranked committed iterations and runs the
main-agent verifier again. The first iteration that passes that final verifier
becomes the selected result.

For `c015`:

- Historical best: iteration 4, score `99.07634533278639`
- Historical best Git head: `1fbc0d7242844b0e9bbbe0dddf9a45cbb29c1e52`
- Final re-verification of iteration 4: exceeded the 30-second verifier limit
- Selected fallback: iteration 7
- Selected score from the main-agent verifier: `99.0763453320608`
- Selected Git head: `09634f84d2d28981b47bc0d97918045543c8a058`

The score difference was approximately `7.3e-10`. The selected iteration used
slightly smaller local-search bounds and completed final verification in 28.9
seconds. This is expected selection behavior: final main-agent verification is
authoritative even when worker-side measurements fluctuate.

## Runtime Evidence

- Concurrency remained bounded at two workers.
- All eight batches completed before the next batch was dispatched.
- Runner failures: 0
- Redispatches: 0
- Hard watchdog timeouts: 1 (`c016`)
- Soft closeout sent: `c004`, `c008`, `c015`, `c016`
- `c016` timed out only after producing four verifier-backed iterations, so no
  redispatch was needed.
- One candidate recovered from several invalid verifier attempts within the
  same worker and later produced the batch's best result.
- All candidate changes stayed within `initial_program.py`.
- Promotion patch passed `git apply --check` against the original workspace.

Pi usage recorded by the monitor:

- Input tokens: 741,357
- Output tokens: 76,233
- Cache-read tokens: 9,638,912
- Estimated cost: `$10.813231`
- Maximum worker context: 55,519 tokens (14.92%)

Compact event logs totaled 2,437,487 bytes. No raw text logs or persistent Pi
session transcripts were retained. The complete local run directory occupied
approximately 8.3 MB.

## What This Proves

This run provides evidence that the Pi GP path can sustain an hour-long,
multi-batch optimization with bounded concurrency, Git-backed inheritance,
iterative public verification, timeout recovery, final selection verification,
report generation, and promotion.

It does not measure official EdgeBench quality, use an equivalent benchmark
dataset, or establish a leaderboard-comparable score. The cases and process
verifier are public to workers; an official hidden judge must remain outside GP
and be owned by the benchmark harness. Treat this only as a reference example
for the GP workflow and its operational behavior.
