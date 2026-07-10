# Workspace Backend Runtime E2E

This small host-free example exercises the `git_worktree` candidate workspace
backend through the public `SearchTools` facade. It is intentionally a numeric
fixture so the example isolates workspace engineering from model quality.

The demo:

1. freezes `git_worktree_search_spec.json`;
2. creates `c001` and `c002` from the same run baseline;
3. commits and verifies independent values on their distinct branches;
4. lets the `evolve` strategy choose higher-scoring `c002`;
5. creates `c003` from `c002`'s best verifier-backed commit;
6. verifies `c003`, selects it, and writes the normal Search report.

Run it with a new runtime path:

```bash
python examples/workspace-backends/run_demo.py \
  --runtime-root .tmp/workspace-backend-demo
```

The final stdout line is JSON containing candidate scores, branch names,
parent and child revisions, selection, report path, and whether every candidate
resolved to the same Git common directory.

This is a runtime E2E. It does not launch a real OpenCode, Codex, Claude Code,
or Pi worker and therefore is not evidence for host lifecycle or `/goal-plus`
hook behavior.
