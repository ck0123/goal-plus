# Monitor Feature Plugins

Goal Plus monitor feature plugins are read-only projections over the durable
goal record and append-only event log. They may add derived views to
`goal_plus_monitor_snapshot.feature_plugins`; they do not launch, wait for,
message, interrupt, or supervise workers.

## Built-in Orchestration Plugin

The `orchestration` plugin projects ordinary `route="subagent"` work into one
host-neutral interaction contract:

```text
assignment -> worker_update(bound) -> result -> decision
```

Every work item exposes its task packet (`title`, `objective`, `scope`,
`depends_on`, and `acceptance`), opaque worker identity, current status, and
directional event summaries. Its attempt projection includes generation,
`launching`/`bound` state, launch deadline, and stale-result count. A stale
generation remains visible as `stale_result` but never replaces the current
worker. Transcript content and paths are excluded; only a
`transcript_available` boolean is returned.

The plugin is enabled by default. Select it explicitly or disable all feature
plugins with:

```text
goal_plus_monitor_snapshot(feature_plugins=["orchestration"])
goal_plus_monitor_snapshot(feature_plugins=[])
```

Unknown names fail instead of silently changing the requested monitoring
surface.

## Host Metadata

Native transport remains host-owned. A host records its real operation name in
the existing `goal_plus_record_work_event.metadata` field:

```json
{
  "orchestration_monitor": {
    "native_operation": "spawn_agent",
    "direction": "main_to_subagent",
    "semantic_event": "assignment"
  }
}
```

Only identifier-shaped operation names are exposed. Prompt-like or malformed
values are ignored. `direction` and `semantic_event` are optional constrained
overrides; the plugin otherwise derives them from the durable work-event kind.

Codex normally records `spawn_agent`, `wait_agent`, and `followup_task`. Pi's
wrapper records `pi_goal_plus_run_work_item` and `goal-plus-pi-worker`. Their
native commands and wait behavior differ, while the task packet, result, and
Main-owned acceptance semantics remain comparable.

Run the deterministic comparison:

```bash
python examples/orchestration-monitor/compare_hosts.py \
  .tmp/orchestration-monitor-comparison
```

This example validates the persisted protocol. It is not a live-model quality
benchmark.
