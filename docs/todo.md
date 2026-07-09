# TODO

This file tracks design follow-ups that should be resolved before broadening
the runtime to more host and hardware-heavy scenarios.

## Main Agent Resource Allocation

Resource allocation for scenario-specific assets belongs to the main agent or
strategy layer, not to the runtime state machine.

The broader Goal Plus boundary for optimization-shaped scenarios is tracked in
[goal-plus/bounded-optimization-boundary.md](goal-plus/bounded-optimization-boundary.md).
This TODO captures the near-term resource-allocation slice of that boundary.

Examples include:

- NPU/GPU card assignment for KernelAgent-style runs.
- Exclusive TCP port ranges.
- Benchmark dataset shards.
- Rate-limited external services.
- Per-worker scratch directories outside the candidate workspace.

The runtime should not grow hard-coded concepts such as "NPU", "GPU", or
device-count-specific scheduling. Instead, the main agent should assign
resources before launching workers and pass the assignment through the worker
directive, host launch payload, or opaque candidate/session metadata.

Recommended main-agent behavior:

1. Inspect the problem context and decide whether resource slots are required.
2. Allocate one slot per candidate or worker before `search_start_agent_session`.
3. Include the assignment in the worker directive, for example
   `ASCEND_RT_VISIBLE_DEVICES=3`.
4. Ask the worker to use only its assigned resource.
5. Record the assignment in candidate/session metadata for debugging.

Suggested opaque metadata shape:

```json
{
  "resource_assignment": {
    "kind": "npu",
    "slot": "3",
    "env": {
      "ASCEND_RT_VISIBLE_DEVICES": "3"
    }
  }
}
```

For strong isolation, a prompt-only instruction is not enough. The host launch
path or wrapper command should also set the relevant environment variable when
the host supports it. The runtime may persist this metadata, but it should not
interpret or schedule the resource type.

Potential future extension:

```json
{
  "strategy": {
    "config": {
      "resource_slots": {
        "kind": "npu",
        "slots": ["0", "1", "2", "3", "4", "5", "6", "7"],
        "env_var": "ASCEND_RT_VISIBLE_DEVICES",
        "scope": "candidate"
      }
    }
  }
}
```

This remains a generic slot abstraction. A strategy or main agent may consume
it, but the runtime should treat it as opaque metadata unless a separate
resource-allocation contract is explicitly designed.

## Runtime State Consistency

The runtime must guarantee its own durable state consistency independently from
scenario-specific resource allocation.

Current risk:

- `search_run_verifier` is exposed as an MCP tool and can be called by multiple
  workers or by a main agent while workers are active.
- `run_verifier` changes run-level state to `EVALUATING`, runs verifier
  commands, updates candidate records, updates best-score fields, updates
  evaluated counters, and updates agent-session counters.
- `write_json` makes an individual file replacement atomic, but it does not make
  the full read-modify-write sequence atomic.

Recommended implementation direction:

1. Add short-lived per-run locking around load-modify-write transactions.
2. Do not hold the run lock while verifier subprocesses are executing.
3. Avoid using run-level `EVALUATING` to represent a single candidate verifier
   in progress, because it blocks or confuses unrelated candidate evaluations.
4. Move in-progress verification status to candidate/session-level metadata, or
   remove the transient run-level state transition for verifier execution.
5. Re-load run and candidate state under lock immediately before committing
   verifier results.
6. Update `best_candidate_id`, `best_score`, `candidates_evaluated`, and session
   counters under the same per-run lock.
7. Add concurrency tests with two verifier calls completing in different orders.

Concrete shape:

```python
with self._run_transaction(run_id) as txn:
    run = txn.load_run()
    record = txn.load_candidate(candidate_id)
    # validate state and mark candidate/session as evaluating if needed
    txn.write_candidate(record)

# Run subprocesses without holding the lock.
report = self._run_commands(...)

with self._run_transaction(run_id) as txn:
    run = txn.load_run()
    record = txn.load_candidate(candidate_id)
    # merge verifier result into the latest candidate record
    txn.write_candidate(record)
    txn.update_best_seen(report)
    txn.recompute_or_increment_counters()
    txn.write_run(run)
```

Implementation notes:

- A stdlib file lock is enough for macOS/Linux hosts (`fcntl.flock` on a
  `.search/runs/<run_id>/run.lock` file). If Windows support is required, use a
  small cross-platform lock dependency or an atomic lock-directory fallback.
- The transaction should be a runtime-internal helper, not part of the MCP API.
- Keep lock scope narrow. Long-running verifier subprocesses should run outside
  the lock so parallel candidate verification remains possible.
- Candidate-level locks may be added later if the same candidate can be verified
  concurrently by a worker and the main agent.

Open design question:

- Should duplicate verification of the same candidate be serialized, rejected,
  or allowed as separate iterations? The answer affects whether candidate-level
  locks are required in addition to per-run transaction locks.
