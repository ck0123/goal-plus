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
    wall_clock_seconds: int = Field(gt=0)
    max_worker_seconds: int | None = Field(default=None, gt=0)
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
    worker_mode: Literal[
        "main-agent-search-direct",
        "sub-agent-search-dispatch",
        "auto",
    ] = "main-agent-search-direct"
    worker_agent_type: str | None = None
    worker_timeout_seconds: int = Field(default=600, gt=0)
    worker_local_verifier_max_runs: int = Field(default=0, ge=0)
    history_policy: HistoryPolicy = Field(default_factory=HistoryPolicy)
    parent_policy: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("strategy name must be non-empty")
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
    dispatch_id: str | None = None
    context_hash: str | None = None
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
    next_dispatch_index: int = 1
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

    @model_validator(mode="after")
    def artifact_matches_candidate(self) -> CandidateRecord:
        if self.artifact and self.artifact.candidate_id != self.candidate_id:
            raise ValueError("artifact candidate_id does not match candidate record")
        return self


class WorkerDispatch(SearchModel):
    dispatch_id: str
    run_id: str
    candidate_id: str
    plan_id: str | None = None
    created_at: str
    main_directive: dict[str, Any] = Field(default_factory=dict)
    context_hash: str
    worker_brief: str
    dispatch_path: Path
    brief_path: Path
    context: dict[str, Any] = Field(default_factory=dict)
