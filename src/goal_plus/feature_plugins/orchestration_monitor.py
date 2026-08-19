from __future__ import annotations

from collections import Counter
import re
from typing import Any

from goal_plus.feature_plugins.base import GoalMonitorContext
from goal_plus.models import GoalPlusWorkItem

PLUGIN_NAME = "orchestration"
METADATA_NAMESPACE = "orchestration_monitor"

_OPERATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,239}$")
_SEMANTIC_EVENTS = {
    "assignment",
    "worker_update",
    "result",
    "stale_result",
    "decision",
}
_DIRECTIONS = {"main_to_subagent", "subagent_to_main"}
_EVENT_SEMANTICS = {
    "dispatch": "assignment",
    "bind": "worker_update",
    "message": "worker_update",
    "result": "result",
    "stale_result": "stale_result",
    "accepted": "decision",
    "rework": "decision",
    "blocked": "worker_update",
    "failed": "worker_update",
    "cancelled": "decision",
}


def orchestration_metadata(
    native_operation: str,
    *,
    direction: str | None = None,
    semantic_event: str | None = None,
) -> dict[str, Any]:
    """Build the namespaced metadata consumed by this feature plugin."""
    native_operation = native_operation.strip()
    if not _OPERATION_PATTERN.fullmatch(native_operation):
        raise ValueError("native_operation must be a host tool or command name")
    if direction is not None and direction not in _DIRECTIONS:
        raise ValueError(f"unsupported orchestration direction: {direction}")
    if semantic_event is not None and semantic_event not in _SEMANTIC_EVENTS:
        raise ValueError(f"unsupported orchestration semantic event: {semantic_event}")
    payload = {"native_operation": native_operation}
    if direction is not None:
        payload["direction"] = direction
    if semantic_event is not None:
        payload["semantic_event"] = semantic_event
    return {METADATA_NAMESPACE: payload}


def _plugin_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    plugin = metadata.get(METADATA_NAMESPACE)
    return plugin if isinstance(plugin, dict) else {}


def _native_operation(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("native_operation")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _OPERATION_PATTERN.fullmatch(value) else None


def _semantic_event(event: str, metadata: dict[str, Any]) -> str:
    override = metadata.get("semantic_event")
    if isinstance(override, str) and override in _SEMANTIC_EVENTS:
        return override
    return _EVENT_SEMANTICS.get(event, "worker_update")


def _direction(event: str, metadata: dict[str, Any]) -> str:
    override = metadata.get("direction")
    if isinstance(override, str) and override in _DIRECTIONS:
        return override
    if event in {"dispatch", "accepted", "rework", "cancelled"}:
        return "main_to_subagent"
    return "subagent_to_main"


def _actor(role: str, item: GoalPlusWorkItem, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "id": (payload.get("agent_id") or item.agent_id) if role == "subagent" else None,
    }


class OrchestrationMonitorFeature:
    name = PLUGIN_NAME

    def snapshot(self, context: GoalMonitorContext) -> dict[str, Any]:
        items = [
            item
            for item in context.goal.work_items
            if item.goal_revision == context.goal.goal_revision and item.route == "subagent"
        ]
        by_id = {item.work_item_id: item for item in items}
        item_payloads = {
            item.work_item_id: {
                "work_item_id": item.work_item_id,
                "status": item.status,
                "required": item.required,
                "task_packet": {
                    "title": item.title,
                    "objective": item.objective,
                    "scope": list(item.scope),
                    "depends_on": list(item.depends_on),
                    "acceptance": list(item.acceptance),
                },
                "worker": {
                    "host": item.host,
                    "task_name": item.task_name,
                    "agent_id": item.agent_id,
                    "transcript_available": bool(item.transcript_path),
                },
                "attempt": {
                    "attempt_id": item.attempt_id,
                    "generation": item.generation,
                    "launch_state": (
                        "launching"
                        if item.status == "launching"
                        else "bound"
                        if item.bound_at is not None
                        else "unclaimed"
                    ),
                    "launch_deadline": item.launch_deadline,
                    "bound_at": item.bound_at,
                    "stale_results": 0,
                },
                "events": [],
            }
            for item in items
        }

        interactions: list[dict[str, Any]] = []
        for event_record in context.events:
            if event_record.get("event_type") != "execution_work_event":
                continue
            payload = event_record.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("goal_revision") != context.goal.goal_revision:
                continue
            work_item_id = str(payload.get("work_item_id") or "")
            item = by_id.get(work_item_id)
            if item is None:
                continue
            event = str(payload.get("event") or "")
            metadata = _plugin_metadata(payload)
            direction = _direction(event, metadata)
            sender_role, recipient_role = (
                ("main", "subagent")
                if direction == "main_to_subagent"
                else ("subagent", "main")
            )
            evidence = payload.get("evidence")
            interaction = {
                "sequence": len(interactions) + 1,
                "at": event_record.get("created_at"),
                "work_item_id": work_item_id,
                "work_event": event,
                "semantic_event": _semantic_event(event, metadata),
                "direction": direction,
                "sender": _actor(sender_role, item, payload),
                "recipient": _actor(recipient_role, item, payload),
                "summary": payload.get("summary"),
                "host": payload.get("host") or item.host,
                "native_operation": _native_operation(metadata),
                "task_name": payload.get("task_name") or item.task_name,
                "agent_id": payload.get("agent_id") or item.agent_id,
                "attempt_id": payload.get("attempt_id"),
                "generation": payload.get("generation"),
                "current_generation": payload.get("current_generation"),
                "submitted_event": payload.get("submitted_event"),
                "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
            }
            interactions.append(interaction)
            item_payloads[work_item_id]["events"].append(interaction)
            if event == "stale_result":
                item_payloads[work_item_id]["attempt"]["stale_results"] += 1

        execution = context.goal.policy.get("execution")
        host = execution.get("host") if isinstance(execution, dict) else None
        host = host if isinstance(host, dict) else {}
        statuses = Counter(item.status for item in items)
        semantics = Counter(event["semantic_event"] for event in interactions)
        operations = list(
            dict.fromkeys(
                str(event["native_operation"])
                for event in interactions
                if event["native_operation"] is not None
            )
        )
        return {
            "schema_version": 2,
            "protocol": host.get("protocol"),
            "host": {
                key: host[key]
                for key in (
                    "host_id",
                    "native_reasoning_effort",
                    "enforcement",
                    "operations",
                )
                if key in host
            },
            "semantic_contract": {
                "task_packet_fields": [
                    "title",
                    "objective",
                    "scope",
                    "depends_on",
                    "acceptance",
                ],
                "event_flow": [
                    "assignment",
                    "worker_update",
                    "result",
                    "stale_result",
                    "decision",
                ],
                "acceptance_owner": "main",
            },
            "summary": {
                "subagent_items_total": len(items),
                "subagent_statuses": dict(sorted(statuses.items())),
                "interaction_events_total": len(interactions),
                "interaction_event_counts": dict(sorted(semantics.items())),
                "native_operations": operations,
                "stale_results_total": sum(
                    item["attempt"]["stale_results"] for item in item_payloads.values()
                ),
            },
            "work_items": list(item_payloads.values()),
            "interactions": interactions,
        }
