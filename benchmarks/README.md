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
