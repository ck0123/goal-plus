# Goal Plus

## Status

Baseline implemented, 2026-07-06.

`goal-plus` is the unified goal entrypoint. It behaves like a normal goal run
by default and can upgrade an optimization-shaped task into the existing Search
MCP workflow.

The design target is pragmatic: let more domains use the candidate workspace,
verifier, scoring, and promotion machinery without forcing every user request
to start as a fully specified search problem.

## Documents

- [Flow and Design](flow-and-design.md): modes, triage flow, MCP
  boundary, frozen-spec rules, and implementation shape.
- [MCP API Impact](api-impact.md): current `search_*` API assessment and the
  implemented minimal `goal_plus_*` API surface.

## Short Version

```text
/goal-plus
  persistent progress toward a possibly fuzzy objective

/goal-any-optimize
  legacy alias that routes through /goal-plus

search_*
  internal Search Mode engine used only after /goal-plus upgrades
```

`goal-plus` replaces direct search as the user-facing flow. It does not delete
the Search MCP runtime; it sits above it. The host agent or skill performs
intake, decides whether a frozen spec is available, and only then calls the
existing search tools.

Codex and Claude Code also ship a narrow Stop hook backstop through
`scripts/hooks/goal_plus_stop.py`. It catches unfinished active Goal Plus
records before the top-level agent stops. OpenCode and all PreToolUse /
SubagentStop checkpoints remain instruction-driven.
