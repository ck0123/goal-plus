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
    strategy: str = "independent_branches"

    @field_validator("source_path")
    @classmethod
    def source_path_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_path must be non-empty")
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
    hypothesis: str
    workspace: Path
    allowed_files: list[str]
    denied_files: list[str]
    instructions: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    stop_conditions: dict[str, Any] = Field(default_factory=dict)


class ArtifactBundle(SearchModel):
    candidate_id: str
    status: Literal["patch_ready", "answer_ready", "abandoned", "failed"]
    changed_files: list[str] = Field(default_factory=list)
    patch_path: Path | None = None
    result_path: Path | None = None
    notes_path: Path | None = None
    logs: list[Path] = Field(default_factory=list)
    summary: str = ""
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
