# Goal Plus

## Status

Baseline implemented, 2026-07-06.

`goal-plus` is a goal entrypoint that can fall back to ordinary goal-style
progress or upgrade an optimization-shaped task into the existing Search MCP
workflow.

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
/goal
  persistent progress toward a possibly fuzzy objective

/goal-any-optimize
  explicit multi-candidate search with a frozen spec and verifier

/goal-plus
  goal-style entrypoint that first asks:
    "Can this be safely upgraded into a verifiable search?"
```

`goal-plus` does not replace the Search MCP runtime. It sits above it: the host
agent or skill performs intake, decides whether a frozen spec is available, and
only then calls the existing search tools.
