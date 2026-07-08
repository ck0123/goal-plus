# Benchmarks

Small reasoning benchmarks for comparing paper-style evaluation with MCP Search
Mode. Data is loaded through Hugging Face `datasets`; local caches and run
outputs are ignored by git.

## Supported

- `formal_logic`: `cais/mmlu`, config `formal_logic`, split `test`
- `arc`: `allenai/ai2_arc`, config `ARC-Challenge`, split `test`
- `winogrande`: `allenai/winogrande`, config `winogrande_xl`, split `validation`
- `truthfulqa`: `truthfulqa/truthful_qa`, config `multiple_choice`, split `validation`
- `gsm8k`: `openai/gsm8k`, config `main`, split `test`

For comparisons with external runs, reuse the checked-in case identity manifest
instead of resampling:

```text
benchmarks/manifests/gpt54mini_n2_r3_persona_full126.case_ids.jsonl
```

The manifest contains 628 case identities: formal_logic 126, ARC 126,
WinoGrande 126, TruthfulQA 124, and GSM8K 126. It records only
`manifest_id`, `benchmark`, `case_index`, and `question_sha256`; it must not
contain raw questions, gold answers, model responses, result history, or local
filesystem paths. For comparable runs, load cases by `benchmark` and
`case_index`, verify `question_sha256`, and stop rather than silently falling
back to "first 126" if the manifest is missing.

## Hidden-Answer Protocol

These benchmarks have fixed hidden answers. They must not be scored with a
worker-visible verifier that returns correctness, score, gold, or
prediction-vs-gold details. The worker-visible verifier may only validate the
submission format.

Worker-visible submission format:

```json
{
  "answer": "A"
}
```

For MCQ benchmarks, `answer` is a displayed choice label. For GSM8K, `answer`
is a parseable finite number, either as a JSON number or a numeric string.

Allowed worker-visible verifier behavior:

- Check that `answer.json` exists.
- Check that the file is valid JSON.
- Check that exactly one `answer` field is present.
- Check that MCQ answers are among the displayed labels, or that numeric
  answers are parseable.

Forbidden worker-visible verifier behavior:

- Returning or logging correctness, score, gold, answer key, or
  prediction-vs-gold details.
- Reading hidden gold files from a candidate workspace or frozen spec.
- Providing hints derived from hidden answers.

The private grader may use hidden gold only after all worker answers are final.
The main agent must not change this verifier protocol, submission format,
sample policy, or private-grader boundary during a run. Final aggregation must
be gold-independent, for example majority vote with a fixed tie-break or first
valid answer.

Answering agents must not use internet search, external lookup, or local answer
search. They must not inspect Hugging Face caches, previous benchmark
reports/runs, other local repositories, dataset files, or any path outside the
candidate workspace to recover answers.

## Effect Comparison

The table below compares the external SafeRL N=2/R=3 multi-persona result with
the hidden-answer Pi/gpt-5.4-mini `k=2` result on the same case identities.
`Ours Selected` is the deployable gold-independent selected answer score. Deltas
are percentage-point changes against SafeRL final R3 and against the best
SafeRL round for that benchmark.

| Benchmark | Cases | SafeRL Final R3 | SafeRL Best | Ours Selected | Delta vs R3 | Delta vs Best | Format OK | Forbidden Hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| formal_logic | 126 | 118/126, 93.65% | R2 121/126, 96.03% | 118/126, 93.65% | +0.00 pp | -2.38 pp | 248/252 | 0 |
| arc | 126 | 117/126, 92.86% | R1/R3 117/126, 92.86% | 120/126, 95.24% | +2.38 pp | +2.38 pp | 252/252 | 0 |
| winogrande | 126 | 110/126, 87.30% | R2 111/126, 88.10% | 111/126, 88.10% | +0.79 pp | +0.00 pp | 252/252 | 0 |
| truthfulqa | 124 | 108/124, 87.10% | R0 109/124, 87.90% | 114/124, 91.94% | +4.84 pp | +4.03 pp | 248/248 | 0 |
| gsm8k | 126 | 110/126, 87.30% | R2 111/126, 88.10% | 121/126, 96.03% | +8.73 pp | +7.94 pp | 252/252 | 0 |

`Any-Correct@2` is useful only as a private diagnostic because it asks whether
either candidate was correct using hidden gold. It must not be used as a final
answer selector.

## Commands

Install optional dataset support:

```bash
python -m pip install -e ".[bench,dev]"
```

Sample one case:

```bash
agentic-any-search-bench sample --benchmark formal_logic --limit 1 --out benchmarks/reports/formal_logic-case.json
```

Run a paper-compatible direct row from a local case:

```bash
agentic-any-search-bench run-one \
  --case-json benchmarks/reports/formal_logic-case.json \
  --mode direct \
  --prediction B
```

Run the same case through MCP Search with Pi RPC:

```bash
agentic-any-search-bench run-one \
  --case-json benchmarks/reports/formal_logic-case.json \
  --mode search \
  --worker-backend pi-rpc \
  --pi-provider openai \
  --pi-model-id gpt-5.4-mini \
  --max-candidates 1 \
  --root benchmarks/runs/formal_logic-pi
```

Compare paper-compatible JSONL rows:

```bash
agentic-any-search-bench compare \
  --ours benchmarks/reports/ours.jsonl \
  --paper benchmarks/reports/paper.jsonl
```
