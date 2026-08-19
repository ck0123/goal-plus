from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from goal_plus.models import GoalPlusRecord


@dataclass(frozen=True)
class GoalMonitorContext:
    root_dir: Path
    goal: GoalPlusRecord
    events: tuple[dict[str, Any], ...]


class GoalMonitorFeature(Protocol):
    name: str

    def snapshot(self, context: GoalMonitorContext) -> dict[str, Any]: ...
