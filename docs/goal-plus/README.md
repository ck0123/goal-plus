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
- [Bounded Optimization Boundary](bounded-optimization-boundary.md): narrow
  Goal Plus boundaries for root harness evidence, resource audit links, and
  multi-card allocation without adding nested orchestration.
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

Codex and Claude Code ship Goal Plus host hooks through
`agentic-any-search-mcp --goal-plus-host-hook`.
`PostToolUse(goal_plus_create)` binds the created Goal Plus record to the
current top-level session, and `Stop` catches unfinished records only when the
bound session matches or `GOAL_PLUS_ID` explicitly selects the record.
Pi uses its project extension instead: native `/goal-plus` pre-create for
interactive/RPC sessions, pre-tool gates for Search Mode and mutating tools,
and a turn-end gate. OpenCode and Codex/Claude PreToolUse or SubagentStop
checkpoints remain instruction-driven.
