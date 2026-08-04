from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class RunState(str, Enum):
    FROZEN_SPEC = "frozen_spec"
    RUNNING = "running"
    WAITING_FOR_WORKERS = "waiting_for_workers"
    EVALUATING = "evaluating"
    SELECTING = "selecting"
    SELECTION_BLOCKED = "selection_blocked"
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
    max_parallel: int = Field(
        gt=0,
        description=(
            "一个 Search run 初始创建并实际并行工作的候选 Agent 数量。"
            "parallel_loops 只创建这一组长期候选，后续继续已有 candidate/session。"
        ),
    )
    max_tokens: int | None = Field(default=None, gt=0)


WorkspaceBackend = Literal["copy", "git_worktree"]
IterationDisposition = Literal["keep", "retain", "discard", "failure"]
VerifierInvalidationReason = Literal[
    "verifier_contract_invalid",
    "verifier_coverage_inadequate",
    "verifier_nondeterministic",
    "verifier_target_mismatch",
    "verifier_infrastructure_failure",
]


class WorkspaceSpec(SearchModel):
    backend: WorkspaceBackend = "git_worktree"


class EditSurface(SearchModel):
    allow: list[str] = Field(min_length=1)
    deny: list[str] = Field(default_factory=list)
    max_file_changes: int | None = Field(default=None, gt=0)


AgentHostKind = Literal["codex", "pi-rpc"]


class AgentHostHandle(SearchModel):
    host: AgentHostKind = "codex"
    external_id: str | None = None
    task_name: str | None = None
    nickname: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerBudget(SearchModel):
    max_runtime_seconds: int | None = Field(default=None, gt=0)
    max_turns: int | None = Field(default=None, gt=0)
    on_exceed: Literal["interrupt"] = "interrupt"
    min_runtime_seconds: int | None = Field(default=None, gt=0)
    min_verifier_runs: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_runtime_or_turn_limit(self) -> "WorkerBudget":
        if self.max_runtime_seconds is None and self.max_turns is None:
            raise ValueError(
                "worker_budget requires max_runtime_seconds or max_turns"
            )
        if (
            self.min_runtime_seconds is not None
            and self.max_runtime_seconds is None
        ):
            raise ValueError(
                "worker_budget.min_runtime_seconds requires max_runtime_seconds"
            )
        if (
            self.min_runtime_seconds is not None
            and self.max_runtime_seconds is not None
            and self.min_runtime_seconds >= self.max_runtime_seconds
        ):
            raise ValueError(
                "worker_budget.min_runtime_seconds must be less than "
                "max_runtime_seconds"
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


class ModelSpec(SearchModel):
    """User-facing model request, normalized before the spec is frozen."""

    model: str = Field(min_length=1)
    count: int | None = Field(default=None, gt=0)
    provider: str | None = None
    adapter_version: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    context_policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "model", "provider", "adapter_version", "reasoning_effort", "service_tier"
    )
    @classmethod
    def string_values_must_be_nonempty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("model option must be non-empty when provided")
        return value


class SelectedModel(SearchModel):
    """Exact host model bound immutably to one candidate slot."""

    slot: int = Field(ge=1)
    model: str = Field(min_length=1)
    provider: str | None = None
    adapter_version: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    context_policy: dict[str, Any] = Field(default_factory=dict)


class EvidenceAnnotatorSpec(SearchModel):
    host: AgentHostKind | None = None
    model: str | None = None
    pi_provider: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int = Field(default=1800, gt=0, le=1800)
    provider: "EvidenceAnnotatorProviderSpec | None" = None

    @field_validator("model", "pi_provider", "reasoning_effort")
    @classmethod
    def values_must_be_nonempty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("annotator option must be non-empty when provided")
        return value

    @model_validator(mode="after")
    def provider_options_are_host_specific(self) -> "EvidenceAnnotatorSpec":
        if self.provider is not None and self.pi_provider is not None:
            raise ValueError(
                "annotator provider and pi_provider cannot both be configured"
            )
        return self


class EvidenceAnnotatorProviderSpec(SearchModel):
    provider_id: str = Field(
        default="goal-plus-evidence", pattern=r"^[A-Za-z0-9_-]+$"
    )
    name: str = Field(default="Goal Plus Evidence provider", min_length=1)
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    wire_api: str = Field(default="responses", min_length=1)


class ResolvedCodexProvider(SearchModel):
    provider_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    base_url: str | None = None
    base_url_env: str | None = None
    base_url_sha256: str | None = None
    api_key_env: str = Field(min_length=1)
    wire_api: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_provider_location(self) -> "ResolvedCodexProvider":
        if bool(self.base_url) == bool(self.base_url_env):
            raise ValueError("provider requires exactly one base URL source")
        if self.base_url_env and not self.base_url_sha256:
            raise ValueError("environment-backed provider requires a URL hash")
        return self


class ResolvedEvidenceAnnotatorProfile(SearchModel):
    host: AgentHostKind = "codex"
    model: str | None = None
    pi_provider: str | None = Field(default=None, min_length=1)
    reasoning_effort: str | None = None
    timeout_seconds: int = Field(gt=0, le=1800)
    codex_home: str | None = None
    pi_home: str | None = None
    provider: ResolvedCodexProvider | None = None


AcceptanceCriterionStatus = Literal[
    "covered",
    "partial",
    "missing",
    "unknown",
    "not_applicable",
]
AcceptanceConfidence = Literal["high", "medium", "low"]


class AcceptanceCriterion(SearchModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    importance: Literal["high", "medium", "low"] = "medium"
    evidence_hints: list[str] = Field(default_factory=list)

    @field_validator("category", "description")
    @classmethod
    def text_must_be_nonempty(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("acceptance criterion text must be non-empty")
        return normalized


class AcceptanceViewSpec(SearchModel):
    rubric_name: str = Field(min_length=1)
    benchmark_context: str = Field(min_length=1)
    criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=12)
    tie_policy: Literal["retain_latest"] = "retain_latest"
    affects_final_result: Literal[False] = False

    @field_validator("rubric_name", "benchmark_context")
    @classmethod
    def text_must_be_nonempty(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("acceptance view text must be non-empty")
        return normalized

    @model_validator(mode="after")
    def criterion_ids_must_be_unique(self) -> "AcceptanceViewSpec":
        criterion_ids = [criterion.id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion ids must be unique")
        return self


class AcceptanceCriterionAssessment(SearchModel):
    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    status: AcceptanceCriterionStatus
    confidence: AcceptanceConfidence
    evidence: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("rationale", mode="before")
    @classmethod
    def rationale_must_be_one_line(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if "\n" in value or "\r" in value:
            raise ValueError("acceptance rationale must be one line")
        return " ".join(value.strip().split())


class AcceptanceViewAssessment(SearchModel):
    summary: str = Field(min_length=1, max_length=1000)
    criteria: list[AcceptanceCriterionAssessment] = Field(min_length=1)

    @field_validator("summary", mode="before")
    @classmethod
    def summary_must_be_one_line(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if "\n" in value or "\r" in value:
            raise ValueError("acceptance summary must be one line")
        return " ".join(value.strip().split())


class EvidenceViewRecord(SearchModel):
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    iteration: int = Field(ge=1)
    attempt_commit: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=1000)
    acceptance_view: AcceptanceViewAssessment | None = None
    created_at: str

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if "\n" in value or "\r" in value:
            raise ValueError("evidence view description must be one line")
        return " ".join(value.strip().split())


class EvidenceAnnotationTask(SearchModel):
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    iteration: int = Field(ge=1)
    attempt_base_commit: str = Field(min_length=1)
    attempt_commit: str = Field(min_length=1)
    attempt_changed_files: list[str] = Field(default_factory=list)
    profile: ResolvedEvidenceAnnotatorProfile | None = None
    outer_deadline_at: str | None = None
    state: Literal["pending", "retry_wait", "completed", "terminal_error"] = (
        "pending"
    )
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: str | None = None
    error_fingerprint: str | None = None
    last_error: str | None = None
    attempt_history: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int | float] = Field(default_factory=dict)
    view: EvidenceViewRecord | None = None
    created_at: str
    updated_at: str


class StrategySpec(SearchModel):
    name: str = "agent_guided"
    orchestration_mode: Literal["rolling_candidates", "parallel_loops"] = (
        "parallel_loops"
    )
    worker_host: AgentHostKind = "codex"
    worker_agent_type: str | None = None
    worker_budget: WorkerBudget | None = None
    worker_launch: WorkerLaunchOptions | None = None
    evidence_annotator: EvidenceAnnotatorSpec = Field(
        default_factory=EvidenceAnnotatorSpec
    )
    models: list[ModelSpec] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )
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
    resource_lock: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "可选的 host 级独占资源名。值相同的 verifier 命令会跨候选和 run 串行执行。"
        ),
    )
    feedback_policy: FeedbackPolicy = FeedbackPolicy.VISIBLE_TO_WORKERS
    expected_outputs: list[str] = Field(
        default_factory=list,
        description=(
            "候选工作区中预期的产物路径或 glob；此字段不解析 verifier stdout metric。"
        ),
    )

    @field_validator("resource_lock")
    @classmethod
    def resource_lock_must_be_nonempty(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource_lock must be non-empty when provided")
        return normalized


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
    acceptance_view: AcceptanceViewSpec | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    strategy: StrategySpec = Field(default_factory=StrategySpec)
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)

    @field_validator("source_path")
    @classmethod
    def source_path_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_path must be non-empty")
        return value


class SearchSpecDraft(SearchModel):
    """Partially discovered SearchSpec with the same nested field contracts."""

    objective: str | None = Field(default=None, min_length=1)
    metric_name: str | None = Field(default=None, min_length=1)
    metric_direction: Literal["minimize", "maximize"] | None = None
    source_path: str | None = Field(default=None, min_length=1)
    edit_surface: EditSurface | None = None
    budget: Budget | None = None
    process_verifiers: list[VerifierCommand] | None = Field(default=None, min_length=1)
    promotion_verifiers: list[VerifierCommand] | None = None
    constraints: dict[str, Any] | None = None
    root_hypotheses: list[str] | None = None
    acceptance_view: AcceptanceViewSpec | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    strategy: StrategySpec | None = None
    workspace: WorkspaceSpec | None = None


GoalPlusStatus = Literal["active", "needs_user", "blocked", "complete", "abandoned"]
GoalPlusPhase = Literal[
    "intake",
    "goal",
    "spec_discovery",
    "search",
    "final_audit",
    "final_check",
]
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


GoalPlusFinalCheckMode = Literal["disabled", "required"]
GoalPlusFinalCheckStatus = Literal[
    "pending",
    "passed",
    "failed",
    "interrupted",
    "superseded",
]
GoalPlusFinalCheckerHost = Literal["codex", "pi"]


class GoalPlusGoalRevision(SearchModel):
    revision: int = Field(ge=1)
    raw_goal: str = Field(min_length=1)
    reason: str | None = None
    created_at: str


class GoalPlusFinalCheck(SearchModel):
    check_id: str = Field(min_length=1)
    goal_revision: int = Field(ge=1)
    checker_host: GoalPlusFinalCheckerHost
    status: GoalPlusFinalCheckStatus = "pending"
    requested_phase: GoalPlusPhase
    requested_at: str
    completed_at: str | None = None
    summary: str | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    checker_metadata: dict[str, Any] = Field(default_factory=dict)


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
    search_spec: SearchSpecDraft | dict[str, Any] = Field(union_mode="left_to_right")
    promotion_rule: str = Field(min_length=1)
    confidence: GoalPlusConfidence
    origin: GoalPlusDiscoveryOrigin | None = None
    open_questions: list[str] = Field(default_factory=list)

    @field_serializer("search_spec")
    def serialize_search_spec(
        self, value: SearchSpecDraft | dict[str, Any]
    ) -> dict[str, Any]:
        if isinstance(value, SearchSpecDraft):
            return value.model_dump(mode="json", exclude_none=True)
        return value


class GoalPlusSpecDraftInput(GoalPlusSpecDraft):
    """Strict tool-input shape; persisted legacy drafts remain backward-readable."""

    search_spec: SearchSpecDraft


class GoalPlusLinkedSearch(SearchModel):
    goal_revision: int = Field(default=1, ge=1)
    frozen_spec_id: str | None = None
    run_id: str | None = None
    linked_at: str | None = None
    selected_candidate_id: str | None = None
    report_path: str | None = None
    html_report_path: str | None = None
    promotion_artifact_path: str | None = None
    summary: str | None = None
    result_recorded_at: str | None = None


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
    goal_revision: int = Field(default=1, ge=1)
    goal_revisions: list[GoalPlusGoalRevision] = Field(default_factory=list)
    final_checks: list[GoalPlusFinalCheck] = Field(default_factory=list)
    triage: GoalPlusTriage | None = None
    spec_draft: GoalPlusSpecDraft | None = None
    search_tasks: list[GoalPlusLinkedSearch] = Field(default_factory=list)
    linked_search: GoalPlusLinkedSearch | None = None
    next_action: GoalPlusNextAction | None = None
    active_session: GoalPlusActiveSession | None = None
    hook_counters: dict[str, int] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def synchronize_search_task_compatibility_view(self) -> "GoalPlusRecord":
        if not self.goal_revisions:
            self.goal_revisions = [
                GoalPlusGoalRevision(
                    revision=self.goal_revision,
                    raw_goal=self.raw_goal,
                    reason="legacy record imported",
                    created_at=self.created_at,
                )
            ]
        latest_revision = self.goal_revisions[-1]
        if latest_revision.revision != self.goal_revision or latest_revision.raw_goal != self.raw_goal:
            raise ValueError("raw_goal and goal_revision must match the latest goal revision")
        if not self.search_tasks and self.linked_search is not None:
            self.search_tasks = [self.linked_search.model_copy(deep=True)]
        elif self.search_tasks:
            latest_task = self.search_tasks[-1]
            self.linked_search = (
                latest_task.model_copy(deep=True)
                if latest_task.goal_revision == self.goal_revision
                else None
            )
        return self


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
    selected_model: SelectedModel | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    model_provenance: dict[str, Any] = Field(default_factory=dict)


class CandidateProposal(SearchModel):
    hypothesis: str | None = None
    intent: str = Field(min_length=1)
    expected_tradeoff: str = ""
    instructions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateWorkOrder(SearchModel):
    slot: int = Field(gt=0)
    intent: str = Field(min_length=1)
    hypothesis: str | None = None
    instructions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchPlan(SearchModel):
    run_id: str
    plan_id: str
    status: Literal["planned", "started"] = "planned"
    strategy: StrategySpec
    requested_k: int = Field(gt=0)
    planned_k: int = Field(ge=0)
    remaining_budget: int = Field(ge=0)
    requires_agent_proposals: bool = False
    worker_policy: dict[str, Any] = Field(default_factory=dict)
    work_orders: list[CandidateWorkOrder] = Field(default_factory=list)
    strategy_trace: dict[str, Any] = Field(default_factory=dict)
    started_candidate_ids: list[str] = Field(default_factory=list)
    selected_models: list[SelectedModel] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )
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
    disposition: IterationDisposition | None = None
    best_iteration: int | None = Field(default=None, ge=1)
    best_git_head: str | None = None
    workspace_git_head_after_settlement: str | None = None


class PromotionEvidence(SearchModel):
    candidate_id: str
    selected_git_head: str | None = None
    git_head: str | None = None
    artifact_hash: str
    passed: bool
    created_at: str


class IterationRecord(SearchModel):
    iteration: int
    agent_session_id: str | None = None
    selected_model: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    exact_model_ref: str | None = None
    adapter_version: str | None = None
    model_provenance: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    process_passed: bool | None = None
    git_head: str | None = None
    attempt_base_git_head: str | None = None
    attempt_changed_files: list[str] = Field(default_factory=list)
    ledger_git_head: str | None = None
    git_artifact_clean: bool | None = None
    git_status: list[str] = Field(default_factory=list)
    failure_class: str | None = None
    summary: str = ""
    hypothesis: str = ""
    changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    artifact_hash: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    log_paths: list[str] = Field(default_factory=list)
    disposition: IterationDisposition | None = None
    restored_to_iteration: int | None = Field(default=None, ge=1)
    restored_to_git_head: str | None = None
    workspace_git_head_after_settlement: str | None = None
    created_at: str


class ResultLedgerEntry(SearchModel):
    source_run_id: str
    source_candidate_id: str
    iteration: int | None = Field(default=None, ge=1)
    git_head: str | None = None
    ledger_git_head: str | None = None
    metric_name: str = Field(min_length=1)
    score: float | None = None
    status: str = Field(min_length=1)
    hypothesis: str = ""
    failure_class: str | None = None
    created_at: str | None = None


class RunSummary(SearchModel):
    run_id: str
    state: RunState
    frozen_spec_id: str
    candidates_total: int
    candidates_evaluated: int
    best_candidate_id: str | None = None
    best_score: float | None = None
    budget_used: dict[str, Any] = Field(default_factory=dict)
    source_run_id: str | None = None
    invalidated_at: str | None = None
    invalidation_reason: VerifierInvalidationReason | None = None
    replacement_run_id: str | None = None


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
    selected_artifact_hash: str | None = None
    budget_used: dict[str, Any] = Field(default_factory=dict)
    source_run_id: str | None = None
    inherited_research: dict[str, Any] = Field(default_factory=dict)
    invalidated_at: str | None = None
    invalidation_reason: VerifierInvalidationReason | None = None
    invalidation_summary: str | None = None
    invalidation_evidence: list[dict[str, Any]] = Field(default_factory=list)
    replacement_run_id: str | None = None
    selected_models: list[SelectedModel] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )


class CandidateRecord(SearchModel):
    candidate_id: str
    status: Literal["created", "evaluated", "failed"]
    task: CandidateTask
    detected_changed_files: list[str] = Field(default_factory=list)
    touched_denied_files: bool = False
    changed_outside_allowed: bool = False
    score_report: ScoreReport | None = None
    promotion_report: ScoreReport | None = None
    promotion_evidence: PromotionEvidence | None = None
    iterations: list[IterationRecord] = Field(default_factory=list)
    results_ledger: list[ResultLedgerEntry] = Field(default_factory=list)
    results_ledger_git_head: str | None = None


class AgentSessionRecord(SearchModel):
    agent_session_id: str
    run_id: str
    candidate_id: str
    host: AgentHostKind = "codex"
    host_handle: AgentHostHandle = Field(default_factory=AgentHostHandle)
    created_at: str
    updated_at: str
    directive: dict[str, Any] = Field(default_factory=dict)
    selected_model: SelectedModel | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    model_provenance: dict[str, Any] = Field(default_factory=dict)
    workspace: Path
    launch: dict[str, Any] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
