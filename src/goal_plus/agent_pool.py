from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


PoolLaunchMode = Literal["async", "blocking"]
PoolWaitMode = Literal["wait_any", "host_return", "batch_barrier"]
PoolContinuationMode = Literal["same_worker", "state_redispatch", "none"]
PoolDeadlineMode = Literal["parent_watchdog", "worker_watchdog", "host_limit"]
PoolRecoveryMode = Literal["host_resident", "supervisor_persisted", "none"]
PoolCompletionStage = Literal["worker_return", "candidate_ready"]


@dataclass(frozen=True)
class HostPoolContract:
    """Declarative host-worker pool contract.

    The Search runtime publishes this contract but never executes it. Host
    integrations translate the named operations onto their native tool or
    process surfaces.
    """

    launch_mode: PoolLaunchMode = "blocking"
    wait_mode: PoolWaitMode = "host_return"
    continuation_mode: PoolContinuationMode = "none"
    deadline_mode: PoolDeadlineMode = "host_limit"
    recovery_mode: PoolRecoveryMode = "none"
    completion_stage: PoolCompletionStage = "worker_return"
    open_tool: str | None = None
    submit_tool: str | None = None
    wait_tool: str | None = None
    snapshot_tool: str | None = None
    continue_tool: str | None = None
    closeout_tool: str | None = None
    interrupt_tool: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerPoolEvent:
    """Host-neutral event returned after a worker reaches a scheduling point."""

    event_id: str
    host: str
    pool_id: str
    kind: Literal["candidate_ready", "failed", "interrupted", "timed_out"]
    run_id: str
    candidate_id: str
    job_id: str
    terminal: bool = True
    agent_session_id: str | None = None
    result: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
