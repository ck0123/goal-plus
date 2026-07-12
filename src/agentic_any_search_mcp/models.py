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
    max_tokens: int | None = Field(default=None, gt=0)


WorkspaceBackend = Literal["copy", "git_worktree"]


class WorkspaceSpec(SearchModel):
    backend: WorkspaceBackend = "copy"


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


AgentHostKind = Literal["opencode", "codex", "claude-code", "pi-rpc"]


class AgentHostHandle(SearchModel):
    host: AgentHostKind = "opencode"
    external_id: str | None = None
    task_name: str | None = None
    nickname: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerBudget(SearchModel):
    max_runtime_seconds: int | None = Field(default=None, gt=0)
    max_turns: int | None = Field(default=None, gt=0)
    on_exceed: Literal["interrupt"] = "interrupt"

    @model_validator(mode="after")
    def require_runtime_or_turn_limit(self) -> "WorkerBudget":
        if self.max_runtime_seconds is None and self.max_turns is None:
            raise ValueError(
                "worker_budget requires max_runtime_seconds or max_turns"
            )
        return self


class WorkerLaunchOptions(SearchModel):
    model: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None

    @field_validator("model", "reasoning_effort", "service_tier")
    @classmethod
    def values_must_be_nonempty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("worker launch option must be non-empty when provided")
        return value


class StrategySpec(SearchModel):
    name: str = "agent_guided"
    driver: Literal["builtin", "python", "external_mcp"] = "builtin"
    ref: str | None = None
    agent_role: str = "planner_and_mutator"
    worker_mode: Literal["agent-session-pool"] = "agent-session-pool"
    worker_host: AgentHostKind = "opencode"
    worker_agent_type: str | None = None
    worker_budget: WorkerBudget | None = None
    worker_launch: WorkerLaunchOptions | None = None
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
    source_path: str
    edit_surface: EditSurface
    budget: Budget
    process_verifiers: list[VerifierCommand] = Field(min_length=1)
    promotion_verifiers: list[VerifierCommand] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    root_hypotheses: list[str] = Field(default_factory=list)
    strategy: StrategySpec = Field(default_factory=StrategySpec)
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)

    @field_validator("source_path")
    @classmethod
    def source_path_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_path must be non-empty")
        return value


GoalPlusStatus = Literal["active", "needs_user", "blocked", "complete", "abandoned"]
GoalPlusPhase = Literal["intake", "goal", "spec_discovery", "search", "final_audit"]
GoalPlusConfidence = Literal["high", "medium", "low"]
GoalPlusRecommendedPhase = Literal["goal", "spec_discovery", "search"]
GoalPlusDiscoveryOrigin = Literal["initial", "in_progress"]
GoalPlusGateEvent = Literal["stop", "subagent_stop", "pre_tool_use", "user_prompt_submit"]
GoalPlusGateDecision = Literal["allow", "block"]
GoalPlusSessionState = Literal["attached", "stale", "detached"]


class GoalPlusNextAction(SearchModel):
    kind: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalPlusTriage(SearchModel):
    is_optimization: bool
    confidence: GoalPlusConfidence
    recommended_phase: GoalPlusRecommendedPhase
    identified_at: GoalPlusDiscoveryOrigin = "initial"
    scenario: str | None = None
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class GoalPlusSpecDraft(SearchModel):
    baseline: dict[str, Any]
    metric: dict[str, Any]
    correctness_gate: dict[str, Any]
    edit_surface: dict[str, Any]
    verifier_artifacts: list[str] = Field(default_factory=list)
    search_spec: dict[str, Any]
    promotion_rule: str = Field(min_length=1)
    confidence: GoalPlusConfidence
    origin: GoalPlusDiscoveryOrigin | None = None
    user_confirmed_frozen_verifier: bool = False
    open_questions: list[str] = Field(default_factory=list)


class GoalPlusLinkedSearch(SearchModel):
    frozen_spec_id: str | None = None
    run_id: str | None = None
    selected_candidate_id: str | None = None
    report_path: str | None = None
    promotion_artifact_path: str | None = None
    summary: str | None = None


class GoalPlusActiveSession(SearchModel):
    host: AgentHostKind
    session_id: str = Field(min_length=1)
    transcript_path: str | None = None
    tool_use_id: str | None = None
    state: GoalPlusSessionState = "attached"
    attached_at: str
    last_seen_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalPlusRecord(SearchModel):
    goal_plus_id: str
    raw_goal: str = Field(min_length=1)
    source_path: str | None = None
    status: GoalPlusStatus = "active"
    phase: GoalPlusPhase = "intake"
    policy: dict[str, Any] = Field(default_factory=dict)
    triage: GoalPlusTriage | None = None
    spec_draft: GoalPlusSpecDraft | None = None
    linked_search: GoalPlusLinkedSearch | None = None
    next_action: GoalPlusNextAction | None = None
    active_session: GoalPlusActiveSession | None = None
    hook_counters: dict[str, int] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class GoalPlusGateResult(SearchModel):
    decision: GoalPlusGateDecision
    phase: GoalPlusPhase
    status: GoalPlusStatus
    reason: str | None = None
    continuation_prompt: str | None = None


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
    workspace_backend: WorkspaceBackend = "copy"
    workspace_branch: str | None = None
    workspace_base_revision: str | None = None
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
    process_passed: bool | None = None
    git_head: str | None = None
    git_artifact_clean: bool | None = None
    git_status: list[str] = Field(default_factory=list)
    failure_class: str | None = None
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    artifact_hash: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RunSummary(SearchModel):
    run_id: str
    state: RunState
    frozen_spec_id: str
    candidates_total: int
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
    candidates_total: int = 0
    candidates_evaluated: int = 0
    best_candidate_id: str | None = None
    best_score: float | None = None
    selected_candidate_id: str | None = None
    selected_score: float | None = None
    selected_iteration: int | None = None
    selected_git_head: str | None = None
    budget_used: dict[str, Any] = Field(default_factory=dict)


class CandidateRecord(SearchModel):
    candidate_id: str
    status: Literal["created", "evaluated", "failed"]
    task: CandidateTask
    detected_changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    score_report: ScoreReport | None = None
    iterations: list[IterationRecord] = Field(default_factory=list)


class AgentSessionRecord(SearchModel):
    agent_session_id: str
    run_id: str
    candidate_id: str
    opencode_session_id: str | None = None
    host: AgentHostKind = "opencode"
    host_handle: AgentHostHandle = Field(default_factory=AgentHostHandle)
    created_at: str
    updated_at: str
    directive: dict[str, Any] = Field(default_factory=dict)
    workspace: Path
    launch: dict[str, Any] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
