from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from goal_plus.feature_plugins.base import GoalMonitorContext, GoalMonitorFeature
from goal_plus.feature_plugins.orchestration_monitor import (
    OrchestrationMonitorFeature,
    orchestration_metadata,
)
from goal_plus.models import GoalPlusRecord

BUILTIN_GOAL_MONITOR_FEATURES: dict[str, GoalMonitorFeature] = {
    "orchestration": OrchestrationMonitorFeature(),
}


def collect_goal_monitor_features(
    root_dir: Path,
    goal: GoalPlusRecord | None,
    events: Iterable[dict[str, Any]],
    enabled: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    names = list(BUILTIN_GOAL_MONITOR_FEATURES) if enabled is None else list(enabled)
    unknown = sorted(set(names) - BUILTIN_GOAL_MONITOR_FEATURES.keys())
    if unknown:
        raise ValueError(f"unknown Goal Plus monitor feature plugin(s): {', '.join(unknown)}")
    if goal is None:
        return {}
    context = GoalMonitorContext(root_dir=root_dir, goal=goal, events=tuple(events))
    return {
        name: BUILTIN_GOAL_MONITOR_FEATURES[name].snapshot(context)
        for name in dict.fromkeys(names)
    }


__all__ = [
    "BUILTIN_GOAL_MONITOR_FEATURES",
    "GoalMonitorContext",
    "GoalMonitorFeature",
    "collect_goal_monitor_features",
    "orchestration_metadata",
]
