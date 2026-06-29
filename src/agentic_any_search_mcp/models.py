from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class RunState(str, Enum):
    FROZEN_SPEC = "frozen_spec"
    RUNNING = "running"
    WAITING_FOR_WORKERS = "waiting_for_workers"
    EVALUATING = "evaluating"
    SELECTING = "selecting"
    READY_TO_PROMOTE = "ready_to_promote"
    PROMOTED = "promoted"
    ABORTED = "aborted"
    FAILED = "failed"


class AgentSessionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class AgentSessionPhase(str, Enum):
    PROBING = "probing"
    IMPLEMENTING = "implementing"
    EXPERIMENTING = "experimenting"
    BLOCKED = "blocked"
    SUBMITTING = "submitting"
    FINALIZING = "finalizing"
    IDLE = "idle"


class VisibilityMode(str, Enum):
    NONE = "none"
    STATUS_ONLY = "status_only"
    OBSERVATIONS = "observations"
    TOP_HISTORY = "top_history"
    FULL = "full"


TERMINAL_AGENT_SESSION_STATUSES = {
    AgentSessionStatus.COMPLETED.value,
    AgentSessionStatus.FAILED.value,
    AgentSessionStatus.ABORTED.value,
}


class VerifierRole(str, Enum):
    VALIDITY_GATE = "validity_gate"
    PROCESS_GATE = "process_gate"
    RANKING_SIGNAL = "ranking_signal"
    DIAGNOSTIC_SIGNAL = "diagnostic_signal"
    PROMOTION_GATE = "promotion_gate"
    ANTI_CHEAT_GATE = "anti_cheat_gate"


class FeedbackPolicy(str, Enum):
    VISIBLE_TO_WORKERS = "visible_to_workers"
    SUMMARY_ONLY = "summary_only"
    FINAL_ONLY = "final_only"


class Budget(SearchModel):
    max_candidates: int = Field(gt=0)
    max_parallel: int = Field(gt=0)
    max_tokens: int | None = Field(default=None, gt=0)


class EditSurface(SearchModel):
    allow: list[str] = Field(min_length=1)
    deny: list[str] = Field(default_factory=list)
    max_file_changes: int | None = Field(default=None, gt=0)


class HistoryPolicy(SearchModel):
    scope: Literal[
        "top_n",
        "last_batch",
        "all",
        "selected_parent_and_inspirations",
        "frontier",
    ] = "top_n"
    top_n: int = Field(default=5, gt=0)
    include: list[str] = Field(
        default_factory=lambda: [
            "summary",
            "score",
            "key_metrics",
            "parent_id",
            "changed_files",
        ]
    )


class StrategySpec(SearchModel):
    name: str = "independent_branches"
    driver: Literal["builtin", "python", "external_mcp"] = "builtin"
    ref: str | None = None
    agent_role: str = "planner_and_mutator"
    worker_mode: Literal["agent-session-pool"] = "agent-session-pool"
    worker_agent_type: str | None = None
    history_policy: HistoryPolicy = Field(default_factory=HistoryPolicy)
    parent_policy: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("strategy name must be non-empty")
        return value

    @field_validator("worker_mode", mode="before")
    @classmethod
    def worker_mode_accepts_legacy_dispatch(cls, value: Any) -> Any:
        # Legacy values are normalized to the only supported mode.
        if value in {
            "sub-agent-search-dispatch",
            "main-agent-search-direct",
            "auto",
        }:
            return "agent-session-pool"
        return value

    @field_validator("worker_agent_type")
    @classmethod
    def worker_agent_type_must_be_nonempty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("worker_agent_type must be non-empty when provided")
        return value


class VerifierCommand(SearchModel):
    name: str = Field(min_length=1)
    role: VerifierRole
    command: list[str] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=300, gt=0)
    feedback_policy: FeedbackPolicy = FeedbackPolicy.VISIBLE_TO_WORKERS
    expected_outputs: list[str] = Field(default_factory=list)


class SearchSpec(SearchModel):
    objective: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    metric_direction: Literal["minimize", "maximize"]
    source_path: str = "."
    edit_surface: EditSurface
    budget: Budget
    process_verifiers: list[VerifierCommand] = Field(min_length=1)
    promotion_verifiers: list[VerifierCommand] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    root_hypotheses: list[str] = Field(default_factory=list)
    strategy: StrategySpec = Field(default_factory=StrategySpec)

    @field_validator("source_path")
    @classmethod
    def source_path_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_path must be non-empty")
        return value

    @field_validator("strategy", mode="before")
    @classmethod
    def strategy_accepts_legacy_string(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, str):
            return {"name": value}
        return value


class FrozenSpec(SearchModel):
    frozen_spec_id: str
    spec_hash: str
    spec: SearchSpec
    verifier_hashes: dict[str, str]
    frozen_verifier_paths: dict[str, str]
    created_at: str


class CandidateTask(SearchModel):
    run_id: str
    candidate_id: str
    parent_id: str | None = None
    parent_candidate_ids: list[str] = Field(default_factory=list)
    base_candidate_id: str | None = None
    plan_id: str | None = None
    hypothesis: str
    workspace: Path
    allowed_files: list[str]
    denied_files: list[str]
    instructions: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    stop_conditions: dict[str, Any] = Field(default_factory=dict)
    proposal: "CandidateProposal | None" = None
    strategy_metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateProposal(SearchModel):
    parent_candidate_ids: list[str] = Field(default_factory=list)
    base_candidate_id: str | None = None
    hypothesis: str | None = None
    intent: str = Field(min_length=1)
    expected_tradeoff: str = ""
    instructions: list[str] = Field(default_factory=list)
    history_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateWorkOrder(SearchModel):
    slot: int = Field(gt=0)
    base_candidate_id: str | None = None
    parent_candidate_ids: list[str] = Field(default_factory=list)
    inspiration_candidate_ids: list[str] = Field(default_factory=list)
    intent: str = Field(min_length=1)
    hypothesis: str | None = None
    instructions: list[str] = Field(default_factory=list)
    must_derive_from: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProposalContract(SearchModel):
    count: int = Field(ge=0)
    must_reference_one_of: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(
        default_factory=lambda: ["parent_candidate_ids", "intent", "expected_tradeoff"]
    )
    notes: list[str] = Field(default_factory=list)


class SearchPlan(SearchModel):
    run_id: str
    plan_id: str
    status: Literal["planned", "started"] = "planned"
    strategy: StrategySpec
    requested_k: int = Field(gt=0)
    planned_k: int = Field(ge=0)
    remaining_budget: int = Field(ge=0)
    requires_agent_proposals: bool = False
    official_history: dict[str, Any] = Field(default_factory=dict)
    derivation_policy: dict[str, Any] = Field(default_factory=dict)
    worker_policy: dict[str, Any] = Field(default_factory=dict)
    proposal_contract: ProposalContract | None = None
    work_orders: list[CandidateWorkOrder] = Field(default_factory=list)
    strategy_trace: dict[str, Any] = Field(default_factory=dict)
    started_candidate_ids: list[str] = Field(default_factory=list)
    created_at: str


class ArtifactBundle(SearchModel):
    candidate_id: str
    status: Literal["patch_ready", "answer_ready", "abandoned", "failed"]
    agent_session_id: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    patch_path: Path | None = None
    result_path: Path | None = None
    notes_path: Path | None = None
    logs: list[Path] = Field(default_factory=list)
    summary: str = ""
    next_ideas: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class VerifierResult(SearchModel):
    name: str
    role: VerifierRole
    passed: bool
    score: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    log_path: Path | None = None
    failure_class: str | None = None


class ScoreReport(SearchModel):
    run_id: str
    candidate_id: str
    parent_id: str | None = None
    validity_passed: bool
    process_passed: bool
    promotion_passed: bool | None = None
    aggregate_score: float | None = None
    verifier_results: list[VerifierResult]
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    hardcoding_suspected: bool = False


class IterationRecord(SearchModel):
    iteration: int
    agent_session_id: str | None = None
    score: float | None = None
    failure_class: str | None = None
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RunSummary(SearchModel):
    run_id: str
    state: RunState
    frozen_spec_id: str
    candidates_total: int
    candidates_running: int
    candidates_evaluated: int
    best_candidate_id: str | None = None
    best_score: float | None = None
    budget_used: dict[str, Any] = Field(default_factory=dict)


class RunRecord(SearchModel):
    run_id: str
    state: RunState
    frozen_spec_id: str
    source_path: str
    created_at: str
    next_candidate_index: int = 1
    next_plan_index: int = 1
    next_agent_session_index: int = 1
    next_agent_event_index: int = 1
    next_observation_index: int = 1
    candidates_total: int = 0
    candidates_evaluated: int = 0
    best_candidate_id: str | None = None
    best_score: float | None = None
    selected_candidate_id: str | None = None
    budget_used: dict[str, Any] = Field(default_factory=dict)


class CandidateRecord(SearchModel):
    candidate_id: str
    status: Literal["created", "submitted", "evaluated", "failed"]
    task: CandidateTask
    artifact: ArtifactBundle | None = None
    detected_changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    score_report: ScoreReport | None = None
    iterations: list[IterationRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def artifact_matches_candidate(self) -> CandidateRecord:
        if self.artifact and self.artifact.candidate_id != self.candidate_id:
            raise ValueError("artifact candidate_id does not match candidate record")
        return self


class AgentSessionBudget(SearchModel):
    stale_after_seconds: int = Field(default=90, gt=0)


class AgentSessionRecord(SearchModel):
    agent_session_id: str
    run_id: str
    candidate_id: str | None = None
    created_at: str
    updated_at: str
    last_heartbeat_at: str
    status: AgentSessionStatus = AgentSessionStatus.RUNNING
    phase: AgentSessionPhase = AgentSessionPhase.PROBING
    visibility_mode: VisibilityMode = VisibilityMode.OBSERVATIONS
    directive: dict[str, Any] = Field(default_factory=dict)
    workspace: Path | None = None
    budget: AgentSessionBudget
    current_goal: str = ""
    last_action: str = ""
    next_step: str = ""
    blockers: list[str] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)
    summary: str = ""
    result: dict[str, Any] = Field(default_factory=dict)


class AgentSessionEvent(SearchModel):
    event_id: str
    run_id: str
    agent_session_id: str | None = None
    type: str = Field(min_length=1)
    created_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentObservation(SearchModel):
    observation_id: str
    run_id: str
    agent_session_id: str
    created_at: str
    summary: str = Field(min_length=1)
    evidence: str = ""
    next_ideas: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    visibility: VisibilityMode = VisibilityMode.OBSERVATIONS


class AgentSessionWaitResult(SearchModel):
    run_id: str
    poll_window_expired: bool
    last_event_id: str | None = None
    events: list[AgentSessionEvent] = Field(default_factory=list)
    sessions: list[AgentSessionRecord] = Field(default_factory=list)
    active_count: int = 0
    max_concurrent_agents: int = Field(gt=0)
