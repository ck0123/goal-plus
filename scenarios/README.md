# Scenarios

Domain bundles that show how to drive the Search MCP runtime for a specific
class of tasks. A bundle is just a README + reference scripts; it does **not**
modify the runtime, the `AnySearchAgent` subagent, or the `search` skill.

| Bundle | Use case |
|---|---|
| [`kernel-optimize/`](kernel-optimize/README.md) | Iterative optimization of an operator kernel against a PyTorch reference, with latency as the metric. |

A new bundle should follow the same shape: a short README, a DSL-agnostic
verifier reference, an optional worked prompt. Anti-cheat and workspace
isolation come from the runtime for free; the bundle only contributes domain
knowledge.
