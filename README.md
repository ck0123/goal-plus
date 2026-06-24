# Agentic Any Search MCP

`agentic-any-search-mcp` is a small MCP-first Search Runtime prototype.

V0 focuses on the control plane:

- freeze a `SearchSpec` and verifier hashes
- create isolated candidate workspaces under `.search/runs/<run_id>/workspace/<candidate_id>/`
- let a main agent or test harness submit candidate artifacts
- run frozen verifiers from the runtime
- select best-seen candidate and export a patch/report

It does not try to control a specific agent implementation. Real host adapters can be added later.

## Run Tests

```bash
python -m pytest
```

## Start MCP Server

```bash
agentic-any-search-mcp --root .search
```

The MCP tool surface mirrors the runtime methods:

- `search_freeze_spec`
- `search_create`
- `search_status`
- `search_next_batch`
- `search_submit_candidate`
- `search_run_verifier`
- `search_select`
- `search_report`
- `search_promote`
- `search_abort`

