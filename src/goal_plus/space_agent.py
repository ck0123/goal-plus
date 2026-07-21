from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import calendar
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any, Literal
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from pydantic import Field, field_validator, model_validator

from goal_plus.models import AgentSessionRecord, SearchModel


SEARCH_SPACE_DIR = "search-space"
SPACE_EXPERIMENT_DIR = "space-experiment"
SPACE_PROTOCOL_VERSION = "search-space-v1"
LEGACY_SPACE_PROTOCOL_VERSION = "vliw-serial-space-v1"
SEARCH_EVIDENCE_PROTOCOL_VERSION = "search-evidence-v1"
SEARCH_SCHEMA_SNAPSHOT_PROTOCOL_VERSION = "search-schema-snapshot-v1"
DEFAULT_SCHEMA_CONSOLIDATION_INTERVAL = 20
MAX_PLAN_CARD_TEXT_CHARS = 2_000
SPACE_REVIEW_DIFF_EXCERPT_CHARS = 1_000
SPACE_REVIEW_DIFF_STAT_CHARS = 400
SPACE_REVIEW_LIST_ITEMS = 12
SPACE_REVIEW_LIST_CHARS = 1_200
SPACE_REVIEW_COVERAGE_TEXT_CHARS = 1_200
SPACE_REVIEW_COVERAGE_REF_LIMIT = 4
SPACE_REVIEW_SOCKET_ENV = "GOAL_PLUS_SPACE_REVIEW_SOCKET"
MAX_REVIEW_PACKET_BYTES = 8 * 1024 * 1024
CODEX_REVIEW_MAX_ATTEMPTS = 5
CODEX_REVIEW_RETRY_BACKOFF_SECONDS = (2, 4, 8, 16)
CODEX_REVIEW_CAPACITY_MESSAGES = ("selected model is at capacity",)
SPACE_VIEWS = (
    "artifact",
    "configuration",
    "mechanism",
    "context",
    "epistemic",
    "behavior",
)
DEFAULT_SPACE_SCHEMA = {
    "schema_version": "universal-intervention-space-v1",
    "views": {
        "artifact": {"description": "The concrete artifact or code surface changed."},
        "configuration": {"description": "Concrete parameters and structural settings."},
        "mechanism": {"description": "The claimed causal mechanism of the intervention."},
        "context": {"description": "The relevant baseline, workload, and preconditions."},
        "epistemic": {"description": "The uncertainty the intervention resolves."},
        "behavior": {"description": "The measurable behavior expected from the change."},
    },
    "duplicate_policy": {
        "covered_unit": "A verifier-backed completed plan or active reservation.",
        "duplicate": (
            "A materially equivalent intervention in the same relevant context that "
            "seeks no substantive new information."
        ),
        "uncertainty_policy": "accept",
    },
}
OUTSTANDING_PLAN_STATUSES = {"reviewing", "accepted", "verifying"}

SearchSpaceMode = Literal["observe", "enforce", "b1", "b4"]
# Backward-compatible name used by the frozen VLIW experiment.
SpaceExperimentMode = SearchSpaceMode
PlanRelation = Literal[
    "new_axis",
    "refinement",
    "replication",
    "interaction_test",
    "alternative_implementation",
    "representation_change",
]
OverlapLevel = Literal["none", "low", "medium", "high", "exact"]
SpaceOutcome = Literal[
    "improved",
    "neutral",
    "regressed",
    "invalid",
    "infrastructure_failure",
]


class InterventionFootprint(SearchModel):
    artifact: list[str] = Field(min_length=1)
    configuration: list[str] = Field(min_length=1)
    mechanism: list[str] = Field(min_length=1)
    context: list[str] = Field(min_length=1)
    epistemic: list[str] = Field(min_length=1)
    behavior: list[str] = Field(min_length=1)

    @field_validator(*SPACE_VIEWS)
    @classmethod
    def normalize_values(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("footprint values must be non-empty")
            if item not in normalized:
                normalized.append(item)
        return normalized


class SearchSpacePlanCard(SearchModel):
    """The deliberately small plan contract exposed to candidate workers."""

    intervention: str = Field(min_length=1, max_length=MAX_PLAN_CARD_TEXT_CHARS)
    scope: str = Field(min_length=1, max_length=MAX_PLAN_CARD_TEXT_CHARS)
    expected_new_information: str = Field(
        min_length=1,
        max_length=MAX_PLAN_CARD_TEXT_CHARS,
    )

    @field_validator("intervention", "scope", "expected_new_information")
    @classmethod
    def normalize_plan_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("plan text must be non-empty")
        return normalized

    def to_proposal(self) -> "InterventionPlanProposal":
        return InterventionPlanProposal(**self.model_dump(mode="json"))


class InterventionPlanProposal(SearchModel):
    """Persisted proposal, including fields from the original B1/B4 protocol."""

    intervention: str | None = None
    scope: str | None = None
    expected_new_information: str | None = None
    base_git_head: str | None = None
    base_score: float | None = None
    target: str | None = None
    bottleneck: str | None = None
    mechanism: str | None = None
    proposed_change: str | None = None
    parameters: dict[str, str] = Field(default_factory=dict)
    expected_observation: str | None = None
    success_criterion: str | None = None
    failure_criterion: str | None = None
    relation: PlanRelation = "new_axis"
    relation_evidence_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    footprint: InterventionFootprint | None = None

    @field_validator(
        "intervention",
        "scope",
        "expected_new_information",
        "target",
        "bottleneck",
        "mechanism",
        "proposed_change",
        "expected_observation",
        "success_criterion",
        "failure_criterion",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("plan text must be non-empty")
        return normalized

    @field_validator("base_git_head")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_minimal_plan_card(self) -> "InterventionPlanProposal":
        if not (self.intervention or self.proposed_change):
            raise ValueError("plan requires intervention or proposed_change")
        if not (self.scope or self.target):
            raise ValueError("plan requires scope or target")
        if not (
            self.expected_new_information
            or self.expected_observation
            or (self.footprint and self.footprint.epistemic)
        ):
            raise ValueError(
                "plan requires expected_new_information or expected_observation"
            )
        return self

    def plan_card(self) -> dict[str, Any]:
        return {
            "intervention": self.intervention or self.proposed_change,
            "scope": self.scope or self.target,
            "expected_new_information": (
                self.expected_new_information
                or self.expected_observation
                or (self.footprint.epistemic[0] if self.footprint else None)
            ),
            "parameters": dict(self.parameters),
            "base_git_head": self.base_git_head,
            "base_score": self.base_score,
        }


class SpaceOverlap(SearchModel):
    artifact: OverlapLevel
    configuration: OverlapLevel
    mechanism: OverlapLevel
    context: OverlapLevel
    epistemic: OverlapLevel
    behavior: OverlapLevel


class SpaceRealizedEvidence(SearchModel):
    """Deterministic verifier evidence; no model is called to construct it."""

    base_git_head: str | None = None
    result_git_head: str | None = None
    artifact_hash: str | None = None
    artifact_delta_sha256: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    delta_files: list[str] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)
    diff_stat: str = ""
    diff_patch: str = ""
    diff_truncated: bool = False
    metric_name: str
    metric_direction: Literal["minimize", "maximize"]
    score_before: float | None = None
    score_after: float | None = None
    score_delta: float | None = None
    outcome: SpaceOutcome
    validity_passed: bool
    process_passed: bool
    infrastructure_failure: bool = False
    failure_class: str | None = None
    completed_at: str


class SpaceCoverageEntry(SearchModel):
    coverage_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    context: str = Field(min_length=1)
    evidence_event_ids: list[str] = Field(min_length=1)
    evidence_plan_ids: list[str] = Field(min_length=1)
    outcomes: list[SpaceOutcome] = Field(min_length=1)

    @field_validator(
        "coverage_id",
        "description",
        "context",
    )
    @classmethod
    def normalize_coverage_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("coverage text must be non-empty")
        return normalized

    @field_validator("evidence_event_ids", "evidence_plan_ids", "outcomes")
    @classmethod
    def normalize_coverage_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized


class SpaceSchemaUpdate(SearchModel):
    space_schema: dict[str, Any]
    coverage: list[SpaceCoverageEntry]
    revision_summary: str = Field(min_length=1)
    revision_evidence_event_ids: list[str] = Field(min_length=1)

    @field_validator("revision_summary")
    @classmethod
    def normalize_revision_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("revision summary must be non-empty")
        return normalized

    @field_validator("revision_evidence_event_ids")
    @classmethod
    def normalize_revision_refs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized


class SpaceReviewDecision(SearchModel):
    decision: Literal["accept", "reject"]
    duplicate_of: list[str]
    reason_code: Literal[
        "novel",
        "duplicate_prior_intervention",
        "active_plan_collision",
    ]
    overlap: SpaceOverlap
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    region_key: str | None = None
    point_key: str | None = None

    @field_validator("duplicate_of")
    @classmethod
    def normalize_duplicate_refs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("region_key", "point_key")
    @classmethod
    def normalize_semantic_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def validate_decision_refs(self) -> "SpaceReviewDecision":
        if self.decision == "reject":
            if not self.duplicate_of:
                raise ValueError("rejected review requires duplicate_of")
            if self.reason_code not in {
                "duplicate_prior_intervention",
                "active_plan_collision",
            }:
                raise ValueError("rejected review requires duplicate reason")
        else:
            if self.duplicate_of:
                raise ValueError("accepted review cannot name duplicate plans")
            if self.reason_code != "novel":
                raise ValueError("accepted review requires novel reason")
        return self


class SearchSpaceConfig(SearchModel):
    protocol_version: str = SPACE_PROTOCOL_VERSION
    experiment_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    mode: SearchSpaceMode
    schema_path: str = Field(min_length=1)
    schema_sha256: str = Field(min_length=1)
    space_schema: dict[str, Any]
    reviewer_model: str = Field(min_length=1)
    reviewer_reasoning_effort: Literal["low", "medium", "high", "xhigh"]
    reviewer_timeout_seconds: int = Field(gt=0, le=600)
    schema_consolidation_interval: int = Field(
        default=DEFAULT_SCHEMA_CONSOLIDATION_INTERVAL,
        ge=2,
        le=100,
    )
    created_at: str


# Frozen experiment helpers import the old name directly.
SpaceExperimentConfig = SearchSpaceConfig


SpacePlanStatus = Literal[
    "reviewing",
    "accepted",
    "rejected",
    "verifying",
    "completed",
    "aborted",
]


class SpacePlanRecord(SearchModel):
    protocol_version: str = SPACE_PROTOCOL_VERSION
    plan_id: str
    proposal_index: int = Field(gt=0)
    run_id: str
    candidate_id: str
    agent_session_id: str
    proposal: InterventionPlanProposal
    proposal_sha256: str
    status: SpacePlanStatus
    admission_source: Literal[
        "allow_all",
        "reviewer_observe",
        "reviewer",
        "reviewer_fail_open",
    ] | None = None
    review: SpaceReviewDecision | None = None
    reviewer_latency_ms: int | None = Field(default=None, ge=0)
    reviewer_usage: dict[str, int | float] = Field(default_factory=dict)
    reviewer_error: str | None = None
    verifier: dict[str, Any] | None = None
    review_attempts: int = Field(default=0, ge=0)
    reviewed_admission_revision: int | None = Field(default=None, ge=0)
    conflict_scope: Literal["completed", "active", "mixed"] | None = None
    coverage_eligible: bool | None = None
    realized_evidence: SpaceRealizedEvidence | None = None
    search_event_id: str | None = None
    aborted_at: str | None = None
    abort_reason: str | None = None
    created_at: str
    reviewed_at: str | None = None
    verifier_started_at: str | None = None
    completed_at: str | None = None


class SearchEvidenceEvent(SearchModel):
    protocol_version: str = SEARCH_EVIDENCE_PROTOCOL_VERSION
    event_id: str
    event_index: int = Field(ge=1)
    previous_event_id: str | None = None
    previous_event_sha256: str | None = None
    run_id: str
    candidate_id: str
    agent_session_id: str
    plan_id: str
    proposal: InterventionPlanProposal
    realized_evidence: SpaceRealizedEvidence
    coverage_eligible: bool
    created_at: str
    content_sha256: str


class SearchSchemaSnapshot(SearchModel):
    protocol_version: str = SEARCH_SCHEMA_SNAPSHOT_PROTOCOL_VERSION
    snapshot_version: int = Field(ge=1)
    parent_snapshot_version: int | None = Field(default=None, ge=1)
    parent_snapshot_sha256: str | None = None
    run_id: str
    built_through_event_index: int = Field(default=0, ge=0)
    built_through_event_id: str | None = None
    space_schema: dict[str, Any]
    coverage: list[SpaceCoverageEntry] = Field(default_factory=list)
    revision_summary: str
    revision_evidence_event_ids: list[str] = Field(default_factory=list)
    created_at: str
    content_sha256: str


class SchemaConsolidationClaim(SearchModel):
    attempt_id: str = Field(min_length=1)
    base_schema_revision: int = Field(ge=1)
    target_event_index: int = Field(ge=1)
    target_event_id: str = Field(min_length=1)
    started_at: str = Field(min_length=1)


class SearchSpaceState(SearchModel):
    protocol_version: str = SPACE_PROTOCOL_VERSION
    run_id: str
    state_version: int = Field(default=1, ge=1)
    admission_revision: int = Field(default=0, ge=0)
    evidence_revision: int = Field(default=0, ge=0)
    schema_revision: int = Field(default=1, ge=1)
    next_plan_index: int = Field(default=1, ge=1)
    active_reservations: list[str] = Field(default_factory=list)
    completed_coverage: list[str] = Field(default_factory=list)
    schema_consolidation_claim: SchemaConsolidationClaim | None = None
    schema_consolidation_attempts: int = Field(default=0, ge=0)
    schema_consolidation_successes: int = Field(default=0, ge=0)
    schema_consolidation_failures: int = Field(default=0, ge=0)
    schema_reviewer_latency_ms_total: int = Field(default=0, ge=0)
    schema_reviewer_usage: dict[str, int | float] = Field(default_factory=dict)
    last_schema_consolidation_error: str | None = None
    created_at: str
    updated_at: str

    @field_validator("active_reservations", "completed_coverage")
    @classmethod
    def normalize_plan_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def require_disjoint_coverage_and_reservations(self) -> "SearchSpaceState":
        overlap = set(self.active_reservations).intersection(self.completed_coverage)
        if overlap:
            raise ValueError(
                "plans cannot be both active reservations and completed coverage: "
                + ", ".join(sorted(overlap))
            )
        return self


@dataclass(frozen=True)
class ReviewerExecution:
    result: SpaceReviewDecision
    latency_ms: int
    usage: dict[str, int | float]


@dataclass(frozen=True)
class SchemaReviewerExecution:
    result: SpaceSchemaUpdate
    latency_ms: int
    usage: dict[str, int | float]


class SpaceReviewerError(RuntimeError):
    pass


def space_review_socket_address(value: str) -> str:
    return "\0" + value[1:] if value.startswith("@") else value


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise SpaceReviewerError("review socket closed before response completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_review_packet(connection: socket.socket, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    if len(encoded) > MAX_REVIEW_PACKET_BYTES:
        raise SpaceReviewerError("review packet exceeds size limit")
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)


def receive_review_packet(connection: socket.socket) -> dict[str, Any]:
    (size,) = struct.unpack("!I", _recv_exact(connection, 4))
    if size > MAX_REVIEW_PACKET_BYTES:
        raise SpaceReviewerError("review packet exceeds size limit")
    payload = json.loads(_recv_exact(connection, size).decode("utf-8"))
    if not isinstance(payload, dict):
        raise SpaceReviewerError("review socket payload must be a JSON object")
    return payload


class SocketSpaceReviewer:
    def __init__(self, address: str) -> None:
        self.address = address

    def review(
        self,
        config: SearchSpaceConfig,
        proposal: InterventionPlanProposal,
        covered_plans: list[SpacePlanRecord],
    ) -> ReviewerExecution:
        request = {
            "operation": "review",
            "config": config.model_dump(mode="json"),
            "proposal": proposal.model_dump(mode="json"),
            # Keep the legacy packet key for the frozen experiment server. In
            # formal modes, checkpointed coverage lives in config's Search State.
            "completed_plans": [
                plan.model_dump(mode="json") for plan in covered_plans
            ],
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(config.reviewer_timeout_seconds + 15)
            connection.connect(space_review_socket_address(self.address))
            send_review_packet(connection, request)
            response = receive_review_packet(connection)
        error = response.get("error")
        if isinstance(error, str) and error:
            raise SpaceReviewerError(error)
        try:
            return ReviewerExecution(
                result=SpaceReviewDecision.model_validate(response["result"]),
                latency_ms=int(response["latency_ms"]),
                usage={
                    str(key): value
                    for key, value in dict(response.get("usage") or {}).items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SpaceReviewerError(f"invalid review socket response: {exc}") from exc

    def consolidate(self, config: SearchSpaceConfig) -> SchemaReviewerExecution:
        request = {
            "operation": "consolidate",
            "config": config.model_dump(mode="json"),
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(config.reviewer_timeout_seconds + 15)
            connection.connect(space_review_socket_address(self.address))
            send_review_packet(connection, request)
            response = receive_review_packet(connection)
        error = response.get("error")
        if isinstance(error, str) and error:
            raise SpaceReviewerError(error)
        try:
            return SchemaReviewerExecution(
                result=SpaceSchemaUpdate.model_validate(response["result"]),
                latency_ms=int(response["latency_ms"]),
                usage={
                    str(key): value
                    for key, value in dict(response.get("usage") or {}).items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SpaceReviewerError(
                f"invalid schema review socket response: {exc}"
            ) from exc


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    temporary.replace(path)


def write_immutable_json(path: Path, data: Any) -> None:
    """Atomically publish one JSON object without an overwrite path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise RuntimeError(f"immutable search object already exists: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _content_sha256(data: dict[str, Any]) -> str:
    payload = dict(data)
    payload.pop("content_sha256", None)
    return _sha256_text(canonical_json(payload))


@contextmanager
def exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return

    lock_dir = path.with_suffix(path.suffix + ".dir")
    while True:  # pragma: no cover - non-POSIX fallback
        try:
            lock_dir.mkdir(parents=True)
            break
        except FileExistsError:
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_dir.rmdir()


def _tail(value: str, limit: int = 4000) -> str:
    return value if len(value) <= limit else value[-limit:]


def _bounded_review_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    marker = "\n[... prompt excerpt clipped ...]\n"
    if limit <= len(marker):
        return marker[:limit]
    available = max(0, limit - len(marker))
    head = (available * 3) // 4
    tail = available - head
    suffix = value[-tail:] if tail else ""
    return value[:head] + marker + suffix


def _bounded_review_list(values: list[str]) -> list[str]:
    if len(values) <= SPACE_REVIEW_LIST_ITEMS:
        candidates = values
    else:
        head = SPACE_REVIEW_LIST_ITEMS // 2
        candidates = [*values[:head], *values[-head:]]
    bounded: list[str] = []
    used_chars = 0
    for value in candidates:
        remaining = SPACE_REVIEW_LIST_CHARS - used_chars
        if remaining <= 0:
            break
        item = _bounded_review_text(value, min(remaining, 240))
        bounded.append(item)
        used_chars += len(item)
    return bounded


def _representative_refs(values: list[str]) -> list[str]:
    if len(values) <= SPACE_REVIEW_COVERAGE_REF_LIMIT:
        return list(values)
    head = SPACE_REVIEW_COVERAGE_REF_LIMIT // 2
    return [*values[:head], *values[-head:]]


def _usage_from_jsonl(text: str) -> dict[str, int | float]:
    usage: dict[str, int | float] = {}
    for line in text.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("usage")
        if not isinstance(candidate, dict):
            item = payload.get("item")
            candidate = item.get("usage") if isinstance(item, dict) else None
        if not isinstance(candidate, dict):
            continue
        for key, value in candidate.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[str(key)] = value
    return usage


def _is_codex_capacity_error(stdout: str, stderr: str) -> bool:
    response = f"{stderr}\n{stdout}".casefold()
    return any(message in response for message in CODEX_REVIEW_CAPACITY_MESSAGES)


class CodexSpaceReviewer:
    def __init__(
        self,
        *,
        hidden_paths: list[Path] | None = None,
        codex_home: Path | None = None,
        scratch_root: Path | None = None,
        use_output_schema: bool = True,
    ) -> None:
        self.hidden_paths = [path.resolve() for path in hidden_paths or []]
        self.codex_home = codex_home.resolve() if codex_home is not None else None
        self.scratch_root = (
            scratch_root.resolve() if scratch_root is not None else None
        )
        if self.scratch_root is not None:
            self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.use_output_schema = use_output_schema

    def review(
        self,
        config: SearchSpaceConfig,
        proposal: InterventionPlanProposal,
        _covered_plans: list[SpacePlanRecord],
    ) -> ReviewerExecution:
        result, latency_ms, usage = self._execute(
            config,
            prompt=self._prompt(config, proposal),
            output_model=SpaceReviewDecision,
        )
        assert isinstance(result, SpaceReviewDecision)
        return ReviewerExecution(result=result, latency_ms=latency_ms, usage=usage)

    def consolidate(self, config: SearchSpaceConfig) -> SchemaReviewerExecution:
        result, latency_ms, usage = self._execute(
            config,
            prompt=self._schema_prompt(config),
            output_model=SpaceSchemaUpdate,
        )
        assert isinstance(result, SpaceSchemaUpdate)
        return SchemaReviewerExecution(
            result=result,
            latency_ms=latency_ms,
            usage=usage,
        )

    def _execute(
        self,
        config: SearchSpaceConfig,
        *,
        prompt: str,
        output_model: type[SearchModel],
    ) -> tuple[SearchModel, int, dict[str, int | float]]:
        codex = shutil.which("codex")
        if codex is None:
            raise SpaceReviewerError("codex executable not found")

        started = time.monotonic()
        deadline = started + config.reviewer_timeout_seconds
        with tempfile.TemporaryDirectory(
            prefix="goal-plus-space-review-",
            dir=self.scratch_root,
        ) as raw_dir:
            directory = Path(raw_dir)
            schema_path = directory / "review-output-schema.json"
            output_path = directory / "review-result.json"
            write_json(schema_path, output_model.model_json_schema())
            codex_command = [
                codex,
                "exec",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "-C",
                str(directory),
                "--sandbox",
                "read-only",
                "-m",
                config.reviewer_model,
                "-c",
                f'model_reasoning_effort="{config.reviewer_reasoning_effort}"',
            ]
            if self.use_output_schema:
                codex_command.extend(["--output-schema", str(schema_path)])
            codex_command.extend(
                [
                "--json",
                "-o",
                str(output_path),
                "-",
                ]
            )
            command = self._isolated_command(directory, codex_command)
            environment = {
                **os.environ,
                "GOAL_PLUS_HOST_HOOK_DISABLED": "1",
                "GOAL_PLUS_STOP_HOOK_DISABLED": "1",
            }
            if self.codex_home is not None:
                environment["CODEX_HOME"] = str(self.codex_home)
            total_usage: dict[str, int | float] = {}
            for attempt in range(1, CODEX_REVIEW_MAX_ATTEMPTS + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SpaceReviewerError(
                        f"reviewer timed out after {config.reviewer_timeout_seconds}s "
                        f"during {attempt - 1} capacity retries"
                    )
                output_path.unlink(missing_ok=True)
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = process.communicate(prompt, timeout=remaining)
                except subprocess.TimeoutExpired as exc:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:  # pragma: no cover - Windows fallback
                        process.kill()
                    stdout, stderr = process.communicate()
                    raise SpaceReviewerError(
                        f"reviewer timed out after "
                        f"{config.reviewer_timeout_seconds}s; "
                        f"stderr={_tail(stderr)!r}"
                    ) from exc

                for key, value in _usage_from_jsonl(stdout).items():
                    total_usage[key] = total_usage.get(key, 0) + value
                if process.returncode == 0:
                    latency_ms = int(round((time.monotonic() - started) * 1000))
                    try:
                        raw_result = output_path.read_text(encoding="utf-8")
                        result = output_model.model_validate_json(raw_result)
                    except (OSError, ValueError) as exc:
                        raise SpaceReviewerError(
                            f"invalid reviewer output: {type(exc).__name__}: {exc}"
                        ) from exc
                    return result, latency_ms, total_usage

                capacity_error = _is_codex_capacity_error(stdout, stderr)
                if not capacity_error or attempt == CODEX_REVIEW_MAX_ATTEMPTS:
                    attempts = (
                        f" after {attempt} capacity attempts" if capacity_error else ""
                    )
                    raise SpaceReviewerError(
                        f"reviewer exited {process.returncode}{attempts}; "
                        f"stderr={_tail(stderr)!r}; stdout={_tail(stdout)!r}"
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SpaceReviewerError(
                        f"reviewer timed out after {config.reviewer_timeout_seconds}s "
                        f"during {attempt} capacity retries"
                    )
                backoff = CODEX_REVIEW_RETRY_BACKOFF_SECONDS[attempt - 1]
                time.sleep(min(backoff, remaining))

            raise AssertionError("unreachable")

    def _isolated_command(
        self,
        directory: Path,
        codex_command: list[str],
    ) -> list[str]:
        if not self.hidden_paths:
            return codex_command
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise SpaceReviewerError(
                "bubblewrap is required for hidden-path reviewer isolation"
            )
        command = [
            bwrap,
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for hidden_path in self.hidden_paths:
            if hidden_path.is_dir():
                command.extend(["--tmpfs", str(hidden_path)])
            elif hidden_path.exists():
                command.extend(["--ro-bind", "/dev/null", str(hidden_path)])
        command.extend(["--bind", str(directory), str(directory)])
        if self.codex_home is not None:
            command.extend(
                ["--bind", str(self.codex_home), str(self.codex_home)]
            )
        command.extend(["--chdir", str(directory), *codex_command])
        return command

    @staticmethod
    def _prompt(
        config: SearchSpaceConfig,
        proposal: InterventionPlanProposal,
    ) -> str:
        payload = {
            "schema": config.space_schema,
            "candidate_plan_card": proposal.plan_card(),
            "candidate_declared_proposal": proposal.model_dump(mode="json"),
            "required_output_json_schema": SpaceReviewDecision.model_json_schema(),
        }
        return (
            "You are SpaceAgent, a pure duplicate and active-collision discriminator "
            "for evaluator-guided search. Decide only whether the candidate intervention "
            "revisits completed coverage or collides with an active reservation in the "
            "provided Search State. Do not propose "
            "directions, improvements, alternatives, unexplored regions, or rewritten "
            "plans.\n\n"
            "Compare the six views independently: artifact, configuration, mechanism, "
            "context, epistemic question, and expected behavior. Reject only when the "
            "material intervention and information sought are already covered. Textual "
            "paraphrases count as duplicates. A declared refinement, replication, "
            "interaction test, alternative implementation, or representation change is "
            "not automatically novel: accept it only when its concrete difference and "
            "expected new information are substantive. More detailed wording is not a "
            "new point when the underlying intervention and information target are the "
            "same. When uncertain, accept.\n\n"
            "For a completed plan, the proposal is intent, while the tail event's compact "
            "realized_evidence view is execution truth. Its deterministic diff excerpt, "
            "delta files, symbols, verifier validity, and outcome take precedence over broad or "
            "inaccurate declared wording. A valid neutral or regressed concrete point "
            "remains covered. Do not treat one failed concrete parameter choice as coverage "
            "of its whole broad family. An invalid or infrastructure-failed attempt is "
            "not supplied as completed coverage.\n\n"
            "Assign concise internal region_key and point_key values when possible. "
            "region_key identifies the broader intervention family; point_key identifies "
            "the concrete material experiment. These keys are review audit metadata; they "
            "do not mutate the global schema.\n\n"
            "The schema contains a private _runtime_search_state with the current immutable "
            "schema snapshot, aggregated coverage, raw SearchEvidence tail, and active "
            "reservations.\n\n"
            "Return only the required classification object. The rationale is internal "
            "audit evidence and will never be shown to the candidate. For accept, use "
            "reason_code=novel and duplicate_of=[]. For reject, use "
            "reason_code=duplicate_prior_intervention for completed coverage or "
            "reason_code=active_plan_collision for an active reservation, and cite every "
            "directly relevant prior plan id.\n\n"
            f"INPUT={json.dumps(payload, sort_keys=True, ensure_ascii=True)}"
        )

    @staticmethod
    def _schema_prompt(config: SearchSpaceConfig) -> str:
        payload = {
            "schema": config.space_schema,
            "required_output_json_schema": SpaceSchemaUpdate.model_json_schema(),
        }
        return (
            "You are SpaceAgent performing a periodic Search Schema consolidation. "
            "Describe only the intervention space already supported by immutable verifier "
            "evidence. Do not propose directions, improvements, alternatives, unexplored "
            "regions, or next steps.\n\n"
            "The schema contains a private _runtime_search_state with the current immutable "
            "snapshot coverage and a frozen SearchEvidence tail ending at target_event_id. "
            "Return a complete new schema and coverage read model through that target. "
            "Retain every prior eligible evidence reference and include every eligible tail "
            "event. You may merge, split, rename, or refine semantic coverage cells only when "
            "the evidence supports it. A valid neutral or regressed concrete point remains "
            "covered; invalid and infrastructure-failed events remain facts but must not enter "
            "coverage.\n\n"
            "The declared proposal is intent and realized_evidence is execution truth. Use "
            "artifact deltas, changed symbols, verifier validity, and outcome to make broad "
            "wording more precise. Never copy _runtime_search_state into the persisted "
            "space_schema. A no-op consolidation must still return the unchanged schema and "
            "complete coverage so the checkpoint can advance. Return only the required JSON "
            "object and do not include recommendations.\n\n"
            f"INPUT={json.dumps(payload, sort_keys=True, ensure_ascii=True)}"
        )


class FileSearchSpaceRuntime:
    """Run-scoped semantic admission with verifier-backed coverage."""

    def __init__(
        self,
        root_dir: Path | str,
        reviewer: CodexSpaceReviewer | SocketSpaceReviewer | None = None,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        socket_address = os.environ.get(SPACE_REVIEW_SOCKET_ENV)
        self.reviewer = reviewer or (
            SocketSpaceReviewer(socket_address)
            if socket_address
            else CodexSpaceReviewer()
        )

    def open(
        self,
        *,
        run_id: str,
        source_path: Path,
        mode: SearchSpaceMode,
        schema_path: str | None = None,
        experiment_id: str | None = None,
        reviewer_model: str,
        reviewer_reasoning_effort: Literal["low", "medium", "high", "xhigh"],
        reviewer_timeout_seconds: int,
        schema_consolidation_interval: int = DEFAULT_SCHEMA_CONSOLIDATION_INTERVAL,
    ) -> dict[str, Any]:
        source = source_path.resolve()
        if schema_path is None:
            schema = json.loads(canonical_json(DEFAULT_SPACE_SCHEMA))
            relative_schema = "<builtin>"
        else:
            requested_schema = Path(schema_path).expanduser()
            resolved_schema = (
                requested_schema.resolve()
                if requested_schema.is_absolute()
                else (source / requested_schema).resolve()
            )
            if not resolved_schema.is_relative_to(source):
                raise ValueError("space schema_path must stay inside run source_path")
            try:
                schema = load_json(resolved_schema)
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"space schema not found: {resolved_schema}"
                ) from exc
            relative_schema = resolved_schema.relative_to(source).as_posix()
        self._validate_schema(schema)
        protocol_version = (
            LEGACY_SPACE_PROTOCOL_VERSION
            if mode in {"b1", "b4"}
            else SPACE_PROTOCOL_VERSION
        )
        config = SearchSpaceConfig(
            protocol_version=protocol_version,
            experiment_id=experiment_id or f"search-space-{run_id}",
            run_id=run_id,
            mode=mode,
            schema_path=relative_schema,
            schema_sha256=_sha256_text(canonical_json(schema)),
            space_schema=schema,
            reviewer_model=reviewer_model,
            reviewer_reasoning_effort=reviewer_reasoning_effort,
            reviewer_timeout_seconds=reviewer_timeout_seconds,
            schema_consolidation_interval=schema_consolidation_interval,
            created_at=_utc_timestamp(),
        )
        target_dir = self._space_dir_for_mode(run_id, mode)
        with self._transaction(run_id):
            existing = self._load_config(run_id, required=False)
            if existing is not None:
                comparable = config.model_dump(mode="json", exclude={"created_at"})
                existing_comparable = existing.model_dump(
                    mode="json", exclude={"created_at"}
                )
                if comparable != existing_comparable:
                    raise RuntimeError(
                        "search space is already open with different config"
                    )
                self._ensure_initial_schema_snapshot(existing)
                return self._open_response(existing)
            write_json(
                target_dir / "config.json",
                config.model_dump(mode="json"),
            )
            now = _utc_timestamp()
            self._write_state(
                run_id,
                SearchSpaceState(
                    protocol_version=protocol_version,
                    run_id=run_id,
                    created_at=now,
                    updated_at=now,
                ),
                directory=target_dir,
            )
            self._write_initial_schema_snapshot(config, directory=target_dir)
            write_json(
                target_dir / "review-output-schema.json",
                SpaceReviewDecision.model_json_schema(),
            )
            write_json(
                target_dir / "schema-update-output-schema.json",
                SpaceSchemaUpdate.model_json_schema(),
            )
        return self._open_response(config)

    def propose(
        self,
        session: AgentSessionRecord,
        proposal: InterventionPlanProposal,
    ) -> dict[str, Any]:
        config = self._load_config(session.run_id)
        assert config is not None
        proposal_sha256 = _sha256_text(
            canonical_json(
                proposal.model_dump(
                    mode="json",
                    exclude={"base_git_head", "base_score"},
                )
            )
        )
        with self._transaction(session.run_id):
            state = self._load_state(session.run_id)
            plans = self._load_plans(session.run_id)
            outstanding = self._outstanding_for_candidate(
                plans,
                state,
                session.candidate_id,
            )
            if outstanding is not None:
                same_request = (
                    outstanding.agent_session_id == session.agent_session_id
                    and outstanding.proposal_sha256 == proposal_sha256
                )
                if same_request and outstanding.status == "accepted":
                    return self._candidate_response(outstanding, state)
                if same_request and outstanding.status == "reviewing":
                    plan = outstanding
                else:
                    raise RuntimeError(
                        f"candidate already has outstanding intervention plan "
                        f"{outstanding.plan_id}; complete its verifier before proposing another"
                    )
            else:
                proposal_index = state.next_plan_index
                plan = SpacePlanRecord(
                    protocol_version=config.protocol_version,
                    plan_id=f"ip-{proposal_index:04d}",
                    proposal_index=proposal_index,
                    run_id=session.run_id,
                    candidate_id=session.candidate_id,
                    agent_session_id=session.agent_session_id,
                    proposal=proposal,
                    proposal_sha256=proposal_sha256,
                    status="reviewing",
                    created_at=_utc_timestamp(),
                )
                allocated_state = state.model_copy(
                    update={
                        "state_version": state.state_version + 1,
                        "next_plan_index": proposal_index + 1,
                        "updated_at": _utc_timestamp(),
                    }
                )
                self._write_plan(plan)
                self._write_state(session.run_id, allocated_state)

        if config.mode == "b1":
            return self._accept_without_review(session.run_id, plan.plan_id)

        review_attempts = 0
        total_latency_ms = 0
        total_usage: dict[str, int | float] = {}
        reviewer_errors: list[str] = []
        while True:
            with self._transaction(session.run_id):
                current = self._load_plan(session.run_id, plan.plan_id)
                if current.status != "reviewing":
                    raise RuntimeError(
                        f"intervention plan {plan.plan_id} changed during review"
                    )
                snapshot_state = self._load_state(session.run_id)
                schema_snapshot = self._load_schema_snapshot(
                    session.run_id,
                    snapshot_state.schema_revision,
                )
                evidence_events = self._load_evidence_events(session.run_id)
                covered = self._review_plans(
                    session.run_id,
                    snapshot_state,
                    schema_snapshot,
                    evidence_events,
                )
                tail_events = [
                    event
                    for event in evidence_events
                    if event.event_index
                    > schema_snapshot.built_through_event_index
                ]
            snapshot_revision = snapshot_state.admission_revision
            snapshot_evidence_revision = snapshot_state.evidence_revision
            snapshot_schema_revision = snapshot_state.schema_revision
            review_attempts += 1
            execution: ReviewerExecution | None = None
            review_error: str | None = None
            try:
                execution = self.reviewer.review(
                    self._review_config(
                        config,
                        snapshot_state,
                        schema_snapshot,
                        tail_events,
                        covered,
                    ),
                    proposal,
                    covered,
                )
                total_latency_ms += execution.latency_ms
                self._merge_usage(total_usage, execution.usage)
                self._validate_review_references(
                    execution.result,
                    covered,
                    schema_snapshot=schema_snapshot,
                )
                execution = ReviewerExecution(
                    result=self._normalize_review_reason(
                        execution.result,
                        snapshot_state,
                    ),
                    latency_ms=execution.latency_ms,
                    usage=execution.usage,
                )
            except Exception as exc:
                review_error = f"{type(exc).__name__}: {exc}"
                reviewer_errors.append(review_error)
                execution = None

            with self._transaction(session.run_id):
                current = self._load_plan(session.run_id, plan.plan_id)
                if current.status != "reviewing":
                    raise RuntimeError(
                        f"intervention plan {plan.plan_id} changed during review"
                    )
                latest_state = self._load_state(session.run_id)
                stale = (
                    latest_state.admission_revision != snapshot_revision
                    or latest_state.evidence_revision != snapshot_evidence_revision
                    or latest_state.schema_revision != snapshot_schema_revision
                )
                if stale:
                    continue

                review = execution.result if execution is not None else None
                should_accept = (
                    review_error is not None
                    or config.mode == "observe"
                    or (review is not None and review.decision == "accept")
                )
                source: str
                if review_error is not None:
                    source = "reviewer_fail_open"
                elif config.mode == "observe":
                    source = "reviewer_observe"
                else:
                    source = "reviewer"
                updated_state = latest_state.model_copy(deep=True)
                if should_accept:
                    updated_state.active_reservations.append(current.plan_id)
                    updated_state.admission_revision += 1
                updated_state.state_version += 1
                updated_state.updated_at = _utc_timestamp()
                conflict_scope = self._conflict_scope(review, snapshot_state)
                updated_plan = current.model_copy(
                    update={
                        "status": "accepted" if should_accept else "rejected",
                        "admission_source": source,
                        "review": review,
                        "reviewer_latency_ms": total_latency_ms or None,
                        "reviewer_usage": total_usage,
                        "reviewer_error": "; ".join(reviewer_errors) or None,
                        "review_attempts": review_attempts,
                        "reviewed_admission_revision": snapshot_revision,
                        "conflict_scope": conflict_scope,
                        "reviewed_at": _utc_timestamp(),
                    }
                )
                self._write_plan(updated_plan)
                self._write_state(session.run_id, updated_state)
                return self._candidate_response(updated_plan, updated_state)

    def begin_verifier(
        self,
        *,
        run_id: str,
        candidate_id: str,
        agent_session_id: str,
        plan_id: str,
    ) -> SpacePlanRecord:
        self._load_config(run_id)
        with self._transaction(run_id):
            state = self._load_state(run_id)
            plan = self._load_plan(run_id, plan_id)
            if plan.candidate_id != candidate_id:
                raise ValueError("intervention_plan_id does not belong to this candidate")
            if plan.agent_session_id != agent_session_id:
                raise ValueError(
                    "intervention_plan_id does not belong to this agent session"
                )
            if plan.status != "accepted" or plan_id not in state.active_reservations:
                raise RuntimeError(
                    f"intervention plan {plan_id} is {plan.status}, expected accepted"
                )
            updated = plan.model_copy(
                update={
                    "status": "verifying",
                    "verifier_started_at": _utc_timestamp(),
                }
            )
            updated_state = state.model_copy(
                update={
                    "state_version": state.state_version + 1,
                    "updated_at": _utc_timestamp(),
                }
            )
            self._write_plan(updated)
            self._write_state(run_id, updated_state)
            return updated

    def restore_after_verifier_error(self, run_id: str, plan_id: str) -> None:
        with self._transaction(run_id):
            plan = self._load_plan(run_id, plan_id)
            if plan.status != "verifying":
                return
            state = self._load_state(run_id)
            restored = plan.model_copy(
                update={"status": "accepted", "verifier_started_at": None}
            )
            updated_state = state.model_copy(
                update={
                    "state_version": state.state_version + 1,
                    "updated_at": _utc_timestamp(),
                }
            )
            self._write_plan(restored)
            self._write_state(run_id, updated_state)

    def complete_verifier(
        self,
        *,
        run_id: str,
        plan_id: str,
        iteration: int,
        score: float | None,
        process_passed: bool,
        git_head: str | None,
        artifact_hash: str | None,
        changed_files: list[str] | None = None,
        failure_class: str | None = None,
        verifier_metrics: dict[str, Any] | None = None,
        realized_evidence: SpaceRealizedEvidence | None = None,
    ) -> None:
        config = self._load_config(run_id)
        assert config is not None
        evidence_published = False
        with self._transaction(run_id):
            state = self._load_state(run_id)
            plan = self._load_plan(run_id, plan_id)
            if plan.status == "completed":
                if plan.verifier and plan.verifier.get("iteration") == iteration:
                    return
                raise RuntimeError(f"intervention plan {plan_id} is already completed")
            if plan.status != "verifying" or plan_id not in state.active_reservations:
                raise RuntimeError(
                    f"intervention plan {plan_id} is {plan.status}, expected verifying"
                )
            completed_at = _utc_timestamp()
            metrics = dict(verifier_metrics or {})
            infrastructure_failure = any(
                isinstance(value, dict) and value.get("infrastructure_failure") is True
                for value in metrics.values()
            )
            if realized_evidence is not None:
                infrastructure_failure = realized_evidence.infrastructure_failure
                coverage_eligible = realized_evidence.outcome in {
                    "improved",
                    "neutral",
                    "regressed",
                }
            else:
                coverage_eligible = (
                    process_passed
                    and not infrastructure_failure
                    and failure_class
                    not in {
                        "VerifierWorkspaceSideEffect",
                        "VerifierStartFailed",
                        "FrozenVerifierModified",
                    }
                )
            evidence_event = (
                self._append_evidence_event(
                    plan=plan,
                    realized_evidence=realized_evidence,
                    coverage_eligible=coverage_eligible,
                )
                if realized_evidence is not None
                else None
            )
            evidence_published = evidence_event is not None
            completed = plan.model_copy(
                update={
                    "status": "completed",
                    "coverage_eligible": coverage_eligible,
                    "realized_evidence": realized_evidence,
                    "search_event_id": (
                        evidence_event.event_id if evidence_event is not None else None
                    ),
                    "completed_at": completed_at,
                    "verifier": {
                        "iteration": iteration,
                        "score": score,
                        "process_passed": process_passed,
                        "git_head": git_head,
                        "artifact_hash": artifact_hash,
                        "changed_files": list(changed_files or []),
                        "failure_class": failure_class,
                        "metrics": metrics,
                        "outcome": (
                            realized_evidence.outcome
                            if realized_evidence is not None
                            else None
                        ),
                        "completed_at": completed_at,
                    },
                }
            )
            updated_state = state.model_copy(deep=True)
            updated_state.active_reservations.remove(plan_id)
            if coverage_eligible and plan_id not in updated_state.completed_coverage:
                updated_state.completed_coverage.append(plan_id)
            updated_state.admission_revision += 1
            updated_state.evidence_revision = max(
                updated_state.evidence_revision + 1,
                evidence_event.event_index if evidence_event is not None else 0,
            )
            updated_state.state_version += 1
            updated_state.updated_at = completed_at
            self._write_plan(completed)
            self._write_state(run_id, updated_state)
        if evidence_published and config.mode not in {"b1", "b4"}:
            self._maybe_consolidate_schema(run_id, config)

    def candidate_context(
        self,
        run_id: str,
        candidate_id: str,
    ) -> dict[str, Any] | None:
        config = self._load_config(run_id, required=False)
        if config is None:
            return None
        with self._transaction(run_id):
            state = self._load_state(run_id)
            plans = self._load_plans(run_id)
            outstanding = self._outstanding_for_candidate(
                plans,
                state,
                candidate_id,
            )
        outstanding_card = outstanding.proposal.plan_card() if outstanding else None
        return {
            "enabled": True,
            "protocol_version": config.protocol_version,
            "schema_version": config.space_schema.get("schema_version"),
            "plan_contract": [
                "intervention",
                "scope",
                "expected_new_information",
            ],
            "outstanding_plan_id": outstanding.plan_id if outstanding else None,
            "outstanding_plan_status": outstanding.status if outstanding else None,
            "outstanding_plan_card": (
                {
                    "intervention": outstanding_card["intervention"],
                    "scope": outstanding_card["scope"],
                    "expected_new_information": outstanding_card[
                        "expected_new_information"
                    ],
                }
                if outstanding_card is not None
                else None
            ),
            "required_flow": [
                "Before every material edit or evaluator execution, call search_space_propose.",
                "If rejected, do not execute that plan; independently submit a new plan.",
                "If accepted, execute only that plan and pass its plan_id to search_run_verifier.",
                "Complete one accepted plan with one verifier call before proposing another.",
            ],
        }

    def status(self, run_id: str) -> dict[str, Any]:
        config = self._load_config(run_id)
        assert config is not None
        with self._transaction(run_id):
            state = self._load_state(run_id)
            plans = self._load_plans(run_id)
            evidence_events = self._load_evidence_events(run_id)
            schema_snapshot = self._load_schema_snapshot(
                run_id,
                state.schema_revision,
            )
        counts: dict[str, int] = {}
        for plan in plans:
            counts[plan.status] = counts.get(plan.status, 0) + 1
        outcome_counts: dict[str, int] = {}
        for plan in plans:
            evidence = plan.realized_evidence
            if evidence is None:
                continue
            outcome_counts[evidence.outcome] = outcome_counts.get(evidence.outcome, 0) + 1
        reviews = [plan.review for plan in plans if plan.review is not None]
        duplicate_reviews = [review for review in reviews if review.decision == "reject"]
        reviewer_latencies = [
            plan.reviewer_latency_ms
            for plan in plans
            if plan.reviewer_latency_ms is not None
        ]
        return {
            "experiment_id": config.experiment_id,
            "run_id": run_id,
            "mode": config.mode,
            "protocol_version": config.protocol_version,
            "schema_path": config.schema_path,
            "schema_sha256": config.schema_sha256,
            "reviewer_model": config.reviewer_model,
            "reviewer_reasoning_effort": config.reviewer_reasoning_effort,
            "state_version": state.state_version,
            "admission_revision": state.admission_revision,
            "evidence_revision": state.evidence_revision,
            "schema_revision": state.schema_revision,
            "plans_total": len(plans),
            "plan_counts": counts,
            "active_reservations": list(state.active_reservations),
            "completed_coverage": list(state.completed_coverage),
            "realized_outcomes": outcome_counts,
            "evidence_event_count": len(evidence_events),
            "evidence_event_head": (
                evidence_events[-1].event_id if evidence_events else None
            ),
            "schema_snapshot_version": schema_snapshot.snapshot_version,
            "schema_built_through_event_id": (
                schema_snapshot.built_through_event_id
            ),
            "schema_tail_event_count": sum(
                event.event_index > schema_snapshot.built_through_event_index
                for event in evidence_events
            ),
            "schema_consolidation_in_progress": (
                state.schema_consolidation_claim is not None
            ),
            "schema_consolidation_target_event_id": (
                state.schema_consolidation_claim.target_event_id
                if state.schema_consolidation_claim is not None
                else None
            ),
            "schema_consolidation_attempts": state.schema_consolidation_attempts,
            "schema_consolidation_successes": state.schema_consolidation_successes,
            "schema_consolidation_failures": state.schema_consolidation_failures,
            "schema_reviewer_latency_ms_total": (
                state.schema_reviewer_latency_ms_total
            ),
            "schema_reviewer_usage": dict(state.schema_reviewer_usage),
            "last_schema_consolidation_error": (
                state.last_schema_consolidation_error
            ),
            "schema_coverage": [
                entry.model_dump(mode="json")
                for entry in schema_snapshot.coverage
            ],
            "reviewed_plans": len(reviews),
            "semantic_duplicate_reviews": len(duplicate_reviews),
            "semantic_duplicate_probability": (
                len(duplicate_reviews) / len(reviews) if reviews else None
            ),
            "active_collision_reviews": sum(
                review.reason_code == "active_plan_collision"
                for review in duplicate_reviews
            ),
            "enforced_rejections": sum(plan.status == "rejected" for plan in plans),
            "reviewer_fail_open": sum(
                plan.admission_source == "reviewer_fail_open" for plan in plans
            ),
            "reviewer_latency_ms_total": sum(reviewer_latencies),
            "reviewer_latency_ms_mean": (
                sum(reviewer_latencies) / len(reviewer_latencies)
                if reviewer_latencies
                else None
            ),
            "candidate_loop_signals": self._candidate_loop_signals(plans),
            "outstanding": [
                plan.plan_id
                for plan in plans
                if plan.status == "reviewing"
                or plan.plan_id in state.active_reservations
            ],
        }

    def is_enabled(self, run_id: str) -> bool:
        return bool(self._configured_space_dirs(run_id))

    def has_accepted_plan(self, run_id: str, candidate_id: str) -> bool:
        if not self.is_enabled(run_id):
            return False
        with self._transaction(run_id):
            state = self._load_state(run_id)
            for plan_id in state.active_reservations:
                plan = self._load_plan(run_id, plan_id)
                if (
                    plan.candidate_id == candidate_id
                    and plan.status in {"accepted", "verifying"}
                ):
                    return True
        return False

    def abort_outstanding(self, run_id: str, *, reason: str) -> list[str]:
        if not reason.strip():
            raise ValueError("abort reason must be non-empty")
        if not self.is_enabled(run_id):
            return []
        with self._transaction(run_id):
            state = self._load_state(run_id)
            plans = self._load_plans(run_id)
            verifying = [plan.plan_id for plan in plans if plan.status == "verifying"]
            if verifying:
                raise RuntimeError(
                    "cannot close search space while verifier plans are active: "
                    + ", ".join(verifying)
                )
            outstanding = [
                plan for plan in plans if plan.status in {"reviewing", "accepted"}
            ]
            if not outstanding:
                return []
            now = _utc_timestamp()
            updated_state = state.model_copy(deep=True)
            admission_changed = False
            for plan in outstanding:
                self._write_plan(
                    plan.model_copy(
                        update={
                            "status": "aborted",
                            "aborted_at": now,
                            "abort_reason": reason.strip(),
                        }
                    )
                )
                if plan.plan_id in updated_state.active_reservations:
                    updated_state.active_reservations.remove(plan.plan_id)
                    admission_changed = True
            if admission_changed:
                updated_state.admission_revision += 1
            updated_state.state_version += 1
            updated_state.updated_at = now
            self._write_state(run_id, updated_state)
            return [plan.plan_id for plan in outstanding]

    def _accept_without_review(self, run_id: str, plan_id: str) -> dict[str, Any]:
        with self._transaction(run_id):
            current = self._load_plan(run_id, plan_id)
            if current.status != "reviewing":
                raise RuntimeError(f"intervention plan {plan_id} changed during review")
            state = self._load_state(run_id)
            accepted = current.model_copy(
                update={
                    "status": "accepted",
                    "admission_source": "allow_all",
                    "reviewed_admission_revision": state.admission_revision,
                    "reviewed_at": _utc_timestamp(),
                }
            )
            updated_state = state.model_copy(deep=True)
            updated_state.active_reservations.append(plan_id)
            updated_state.admission_revision += 1
            updated_state.state_version += 1
            updated_state.updated_at = _utc_timestamp()
            self._write_plan(accepted)
            self._write_state(run_id, updated_state)
            return self._candidate_response(accepted, updated_state)

    def _run_dir(self, run_id: str) -> Path:
        return self.root_dir / "runs" / run_id

    def _configured_space_dirs(self, run_id: str) -> list[Path]:
        run_dir = self._run_dir(run_id)
        return [
            directory
            for directory in (
                run_dir / SEARCH_SPACE_DIR,
                run_dir / SPACE_EXPERIMENT_DIR,
            )
            if (directory / "config.json").is_file()
        ]

    def _space_dir_for_mode(self, run_id: str, mode: SearchSpaceMode) -> Path:
        name = SPACE_EXPERIMENT_DIR if mode in {"b1", "b4"} else SEARCH_SPACE_DIR
        return self._run_dir(run_id) / name

    def _space_dir(self, run_id: str) -> Path:
        configured = self._configured_space_dirs(run_id)
        if len(configured) > 1:
            raise RuntimeError(f"run {run_id} has multiple search-space configs")
        if configured:
            return configured[0]
        return self._run_dir(run_id) / SEARCH_SPACE_DIR

    def _config_path(self, run_id: str) -> Path:
        return self._space_dir(run_id) / "config.json"

    @contextmanager
    def _transaction(self, run_id: str):
        with exclusive_file_lock(self._run_dir(run_id) / "search-space.lock"):
            yield

    def _state_path(self, run_id: str, *, directory: Path | None = None) -> Path:
        return (directory or self._space_dir(run_id)) / "state.json"

    def _plan_path(self, run_id: str, plan_id: str) -> Path:
        return self._space_dir(run_id) / "plans" / f"{plan_id}.json"

    def _evidence_event_path(self, run_id: str, event_index: int) -> Path:
        return self._space_dir(run_id) / "events" / f"se-{event_index:06d}.json"

    def _schema_snapshot_path(self, run_id: str, snapshot_version: int) -> Path:
        return (
            self._space_dir(run_id)
            / "schemas"
            / f"schema-{snapshot_version:06d}.json"
        )

    def _load_config(
        self,
        run_id: str,
        *,
        required: bool = True,
    ) -> SearchSpaceConfig | None:
        configured = self._configured_space_dirs(run_id)
        if len(configured) > 1:
            raise RuntimeError(f"run {run_id} has multiple search-space configs")
        if not configured:
            if required:
                raise RuntimeError(f"search space is not open for run {run_id}")
            return None
        return SearchSpaceConfig.model_validate(load_json(configured[0] / "config.json"))

    def _load_state(self, run_id: str) -> SearchSpaceState:
        path = self._state_path(run_id)
        if path.is_file():
            payload = load_json(path)
            # Forward migration from the pre-snapshot prototype.
            payload.pop("features", None)
            state = SearchSpaceState.model_validate(payload)
            state = self._reconcile_committed_plan_files(run_id, state)
            state = self._reconcile_published_evidence_events(run_id, state)
            return self._reconcile_published_schema_head(run_id, state)
        config = self._load_config(run_id)
        assert config is not None
        plans = self._load_plans(run_id)
        active = [
            plan.plan_id
            for plan in plans
            if plan.status in {"accepted", "verifying"}
        ]
        completed = [
            plan.plan_id
            for plan in plans
            if plan.status == "completed" and plan.coverage_eligible is not False
        ]
        next_index = max((plan.proposal_index for plan in plans), default=0) + 1
        return SearchSpaceState(
            protocol_version=config.protocol_version,
            run_id=run_id,
            state_version=max(1, len(plans) + 1),
            admission_revision=len(active) + len(completed),
            evidence_revision=len(completed),
            next_plan_index=next_index,
            active_reservations=active,
            completed_coverage=completed,
            created_at=config.created_at,
            updated_at=_utc_timestamp(),
        )

    def _reconcile_committed_plan_files(
        self,
        run_id: str,
        state: SearchSpaceState,
    ) -> SearchSpaceState:
        """Finish plan/state commits interrupted after a mutable plan write."""

        repaired = state.model_copy(deep=True)
        changed = False
        evidence_commits = 0
        plans = self._load_plans(run_id)
        for plan_id in list(repaired.active_reservations):
            plan = self._load_plan(run_id, plan_id)
            if plan.status in {"accepted", "verifying"}:
                continue
            if plan.status == "completed":
                repaired.active_reservations.remove(plan_id)
                if (
                    plan.coverage_eligible is not False
                    and plan_id not in repaired.completed_coverage
                ):
                    repaired.completed_coverage.append(plan_id)
                evidence_commits += 1
                changed = True
                continue
            if plan.status == "aborted":
                repaired.active_reservations.remove(plan_id)
                changed = True
                continue
            raise RuntimeError(
                f"active reservation {plan_id} has inconsistent plan status "
                f"{plan.status}"
            )
        for plan in plans:
            if plan.status in {"accepted", "verifying"}:
                if plan.plan_id not in repaired.active_reservations:
                    repaired.active_reservations.append(plan.plan_id)
                    changed = True
                continue
            if plan.status == "completed":
                if plan.plan_id in repaired.active_reservations:
                    continue
                if (
                    plan.coverage_eligible is not False
                    and plan.plan_id not in repaired.completed_coverage
                ):
                    repaired.completed_coverage.append(plan.plan_id)
                    evidence_commits += 1
                    changed = True
        next_plan_index = max(
            repaired.next_plan_index,
            max((plan.proposal_index for plan in plans), default=0) + 1,
        )
        if next_plan_index != repaired.next_plan_index:
            repaired.next_plan_index = next_plan_index
            changed = True
        if not changed:
            return state
        repaired.admission_revision += 1
        repaired.evidence_revision += evidence_commits
        repaired.state_version += 1
        repaired.updated_at = _utc_timestamp()
        self._write_state(run_id, repaired)
        return repaired

    def _reconcile_published_evidence_events(
        self,
        run_id: str,
        state: SearchSpaceState,
    ) -> SearchSpaceState:
        """Project immutable verifier facts into mutable plan/state read models."""

        events = self._load_evidence_events(run_id)
        if not events:
            return state
        repaired = state.model_copy(deep=True)
        changed = repaired.evidence_revision < events[-1].event_index
        admission_changed = False
        for event in events:
            plan = self._load_plan(run_id, event.plan_id)
            if plan.status not in {"accepted", "verifying", "completed"}:
                raise RuntimeError(
                    f"evidence event {event.event_id} references plan {event.plan_id} "
                    f"in status {plan.status}"
                )
            event_matches_plan = (
                plan.status == "completed"
                and plan.proposal == event.proposal
                and plan.coverage_eligible == event.coverage_eligible
                and plan.realized_evidence == event.realized_evidence
                and plan.search_event_id == event.event_id
            )
            if not event_matches_plan:
                completed = plan.model_copy(
                    update={
                        "status": "completed",
                        "proposal": event.proposal,
                        "coverage_eligible": event.coverage_eligible,
                        "realized_evidence": event.realized_evidence,
                        "search_event_id": event.event_id,
                        "completed_at": event.realized_evidence.completed_at,
                        "verifier": plan.verifier
                        or self._verifier_summary_from_event(event),
                    }
                )
                self._write_plan(completed)
                changed = True
            if event.plan_id in repaired.active_reservations:
                repaired.active_reservations.remove(event.plan_id)
                admission_changed = True
                changed = True
            if (
                event.coverage_eligible
                and event.plan_id not in repaired.completed_coverage
            ):
                repaired.completed_coverage.append(event.plan_id)
                admission_changed = True
                changed = True
            if (
                not event.coverage_eligible
                and event.plan_id in repaired.completed_coverage
            ):
                repaired.completed_coverage.remove(event.plan_id)
                admission_changed = True
                changed = True
        if not changed:
            return state
        repaired.evidence_revision = max(
            repaired.evidence_revision,
            events[-1].event_index,
        )
        if admission_changed:
            repaired.admission_revision += 1
        repaired.state_version += 1
        repaired.updated_at = _utc_timestamp()
        self._write_state(run_id, repaired)
        return repaired

    @staticmethod
    def _verifier_summary_from_event(event: SearchEvidenceEvent) -> dict[str, Any]:
        evidence = event.realized_evidence
        return {
            "iteration": None,
            "score": evidence.score_after,
            "process_passed": evidence.process_passed,
            "git_head": evidence.result_git_head,
            "artifact_hash": evidence.artifact_hash,
            "changed_files": list(evidence.changed_files),
            "failure_class": evidence.failure_class,
            "metrics": {},
            "outcome": evidence.outcome,
            "completed_at": evidence.completed_at,
            "recovered_from_search_event": event.event_id,
        }

    def _reconcile_published_schema_head(
        self,
        run_id: str,
        state: SearchSpaceState,
    ) -> SearchSpaceState:
        """Advance the read model after an immutable snapshot was published."""

        schemas_dir = self._space_dir(run_id) / "schemas"
        versions = [
            int(match.group(1))
            for path in schemas_dir.glob("schema-*.json")
            if (match := re.fullmatch(r"schema-(\d{6})\.json", path.name))
        ]
        if not versions or max(versions) <= state.schema_revision:
            return state

        published_head = max(versions)
        self._load_schema_snapshot(run_id, published_head)
        recovered_claim = state.schema_consolidation_claim
        repaired = state.model_copy(
            update={
                "schema_revision": published_head,
                "schema_consolidation_claim": None,
                "schema_consolidation_successes": (
                    state.schema_consolidation_successes
                    + (1 if recovered_claim is not None else 0)
                ),
                "last_schema_consolidation_error": None,
                "state_version": state.state_version + 1,
                "updated_at": _utc_timestamp(),
            }
        )
        self._write_state(run_id, repaired)
        return repaired

    def _write_state(
        self,
        run_id: str,
        state: SearchSpaceState,
        *,
        directory: Path | None = None,
    ) -> None:
        write_json(
            self._state_path(run_id, directory=directory),
            state.model_dump(mode="json"),
        )

    def _load_plan(self, run_id: str, plan_id: str) -> SpacePlanRecord:
        path = self._plan_path(run_id, plan_id)
        if not path.is_file():
            raise FileNotFoundError(f"intervention plan not found: {plan_id}")
        return SpacePlanRecord.model_validate(self._migrate_plan_payload(load_json(path)))

    def _load_plans(self, run_id: str) -> list[SpacePlanRecord]:
        plans_dir = self._space_dir(run_id) / "plans"
        if not plans_dir.is_dir():
            return []
        return [
            SpacePlanRecord.model_validate(self._migrate_plan_payload(load_json(path)))
            for path in sorted(plans_dir.glob("ip-*.json"))
        ]

    @staticmethod
    def _migrate_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(payload)
        migrated.pop("realized_projection", None)
        evidence = migrated.get("realized_evidence")
        if isinstance(evidence, dict):
            evidence = dict(evidence)
            evidence.pop("workspace_disposition", None)
            evidence.pop("workspace_git_head", None)
            evidence.pop("disposition_observed_at", None)
            migrated["realized_evidence"] = evidence
        review = migrated.get("review")
        if isinstance(review, dict):
            review = dict(review)
            review.pop("feature_observations", None)
            review.pop("history_updates", None)
            review.pop("schema_update", None)
            migrated["review"] = review
        return migrated

    def _write_plan(self, plan: SpacePlanRecord) -> None:
        write_json(
            self._plan_path(plan.run_id, plan.plan_id),
            plan.model_dump(mode="json"),
        )

    def _load_evidence_events(self, run_id: str) -> list[SearchEvidenceEvent]:
        events_dir = self._space_dir(run_id) / "events"
        if not events_dir.is_dir():
            return []
        events: list[SearchEvidenceEvent] = []
        previous: SearchEvidenceEvent | None = None
        seen_plan_ids: set[str] = set()
        for expected_index, path in enumerate(
            sorted(events_dir.glob("se-*.json")),
            start=1,
        ):
            event = SearchEvidenceEvent.model_validate(load_json(path))
            if event.run_id != run_id:
                raise RuntimeError(f"evidence event belongs to another run: {path}")
            if event.event_index != expected_index or event.event_id != (
                f"se-{expected_index:06d}"
            ):
                raise RuntimeError(f"non-contiguous evidence event chain: {path}")
            if event.content_sha256 != _content_sha256(
                event.model_dump(mode="json")
            ):
                raise RuntimeError(f"evidence event content hash mismatch: {path}")
            expected_previous_id = previous.event_id if previous is not None else None
            expected_previous_hash = (
                previous.content_sha256 if previous is not None else None
            )
            if (
                event.previous_event_id != expected_previous_id
                or event.previous_event_sha256 != expected_previous_hash
            ):
                raise RuntimeError(f"broken evidence event parent chain: {path}")
            if event.plan_id in seen_plan_ids:
                raise RuntimeError(
                    f"multiple evidence events reference plan {event.plan_id}: {path}"
                )
            events.append(event)
            seen_plan_ids.add(event.plan_id)
            previous = event
        return events

    def _append_evidence_event(
        self,
        *,
        plan: SpacePlanRecord,
        realized_evidence: SpaceRealizedEvidence,
        coverage_eligible: bool,
    ) -> SearchEvidenceEvent:
        events = self._load_evidence_events(plan.run_id)
        existing = next(
            (event for event in events if event.plan_id == plan.plan_id),
            None,
        )
        if existing is not None:
            if (
                existing.proposal != plan.proposal
                or existing.realized_evidence != realized_evidence
                or existing.coverage_eligible != coverage_eligible
            ):
                raise RuntimeError(
                    f"plan {plan.plan_id} has conflicting immutable evidence"
                )
            return existing
        previous = events[-1] if events else None
        event_index = len(events) + 1
        event = SearchEvidenceEvent(
            event_id=f"se-{event_index:06d}",
            event_index=event_index,
            previous_event_id=previous.event_id if previous is not None else None,
            previous_event_sha256=(
                previous.content_sha256 if previous is not None else None
            ),
            run_id=plan.run_id,
            candidate_id=plan.candidate_id,
            agent_session_id=plan.agent_session_id,
            plan_id=plan.plan_id,
            proposal=plan.proposal,
            realized_evidence=realized_evidence,
            coverage_eligible=coverage_eligible,
            created_at=realized_evidence.completed_at,
            content_sha256="pending",
        )
        event = event.model_copy(
            update={
                "content_sha256": _content_sha256(event.model_dump(mode="json"))
            }
        )
        write_immutable_json(
            self._evidence_event_path(plan.run_id, event_index),
            event.model_dump(mode="json"),
        )
        return event

    def _write_initial_schema_snapshot(
        self,
        config: SearchSpaceConfig,
        *,
        directory: Path | None = None,
    ) -> SearchSchemaSnapshot:
        snapshot = SearchSchemaSnapshot(
            snapshot_version=1,
            run_id=config.run_id,
            space_schema=json.loads(canonical_json(config.space_schema)),
            revision_summary="Initial search schema snapshot.",
            created_at=config.created_at,
            content_sha256="pending",
        )
        snapshot = snapshot.model_copy(
            update={
                "content_sha256": _content_sha256(snapshot.model_dump(mode="json"))
            }
        )
        path = (
            (directory or self._space_dir(config.run_id))
            / "schemas"
            / "schema-000001.json"
        )
        write_immutable_json(path, snapshot.model_dump(mode="json"))
        return snapshot

    def _ensure_initial_schema_snapshot(
        self,
        config: SearchSpaceConfig,
    ) -> SearchSchemaSnapshot:
        path = self._schema_snapshot_path(config.run_id, 1)
        if not path.is_file():
            return self._write_initial_schema_snapshot(config)
        return self._load_schema_snapshot(config.run_id, 1)

    def _load_schema_snapshot(
        self,
        run_id: str,
        snapshot_version: int,
    ) -> SearchSchemaSnapshot:
        config = self._load_config(run_id)
        assert config is not None
        initial_path = self._schema_snapshot_path(run_id, 1)
        if not initial_path.is_file():
            self._write_initial_schema_snapshot(config)
        evidence_events = self._load_evidence_events(run_id)
        previous: SearchSchemaSnapshot | None = None
        selected: SearchSchemaSnapshot | None = None
        for version in range(1, snapshot_version + 1):
            path = self._schema_snapshot_path(run_id, version)
            if not path.is_file():
                raise RuntimeError(f"missing search schema snapshot: {path}")
            snapshot = SearchSchemaSnapshot.model_validate(load_json(path))
            if snapshot.run_id != run_id or snapshot.snapshot_version != version:
                raise RuntimeError(f"invalid search schema snapshot identity: {path}")
            if snapshot.content_sha256 != _content_sha256(
                snapshot.model_dump(mode="json")
            ):
                raise RuntimeError(f"schema snapshot content hash mismatch: {path}")
            expected_parent_version = (
                previous.snapshot_version if previous is not None else None
            )
            expected_parent_hash = (
                previous.content_sha256 if previous is not None else None
            )
            if (
                snapshot.parent_snapshot_version != expected_parent_version
                or snapshot.parent_snapshot_sha256 != expected_parent_hash
            ):
                raise RuntimeError(f"broken schema snapshot parent chain: {path}")
            previous_built_index = (
                previous.built_through_event_index if previous is not None else 0
            )
            if snapshot.built_through_event_index < previous_built_index:
                raise RuntimeError(f"schema snapshot evidence head moved backward: {path}")
            newly_built_event_ids = {
                event.event_id
                for event in evidence_events[
                    previous_built_index : snapshot.built_through_event_index
                ]
            }
            missing_revision_refs = sorted(
                newly_built_event_ids - set(snapshot.revision_evidence_event_ids)
            )
            if missing_revision_refs:
                raise RuntimeError(
                    f"schema snapshot omitted newly built evidence: {path}: "
                    + ", ".join(missing_revision_refs)
                )
            self._validate_schema_snapshot_evidence(snapshot, evidence_events)
            previous = snapshot
            selected = snapshot
        assert selected is not None
        return selected

    def _write_schema_snapshot(self, snapshot: SearchSchemaSnapshot) -> None:
        write_immutable_json(
            self._schema_snapshot_path(snapshot.run_id, snapshot.snapshot_version),
            snapshot.model_dump(mode="json"),
        )

    @staticmethod
    def _schema_snapshot_from_update(
        *,
        run_id: str,
        snapshot_version: int,
        parent: SearchSchemaSnapshot,
        update: SpaceSchemaUpdate,
        evidence_events: list[SearchEvidenceEvent],
    ) -> SearchSchemaSnapshot:
        latest_event = evidence_events[-1] if evidence_events else None
        snapshot = SearchSchemaSnapshot(
            snapshot_version=snapshot_version,
            parent_snapshot_version=parent.snapshot_version,
            parent_snapshot_sha256=parent.content_sha256,
            run_id=run_id,
            built_through_event_index=(
                latest_event.event_index if latest_event is not None else 0
            ),
            built_through_event_id=(
                latest_event.event_id if latest_event is not None else None
            ),
            space_schema=update.space_schema,
            coverage=update.coverage,
            revision_summary=update.revision_summary,
            revision_evidence_event_ids=update.revision_evidence_event_ids,
            created_at=_utc_timestamp(),
            content_sha256="pending",
        )
        return snapshot.model_copy(
            update={
                "content_sha256": _content_sha256(snapshot.model_dump(mode="json"))
            }
        )

    @staticmethod
    def _schema_refresh_due(
        config: SearchSpaceConfig,
        tail_events: list[SearchEvidenceEvent],
    ) -> bool:
        if config.mode in {"b1", "b4"}:
            return False
        eligible = sum(event.coverage_eligible for event in tail_events)
        return (
            eligible >= config.schema_consolidation_interval
            or len(tail_events) >= config.schema_consolidation_interval * 2
        )

    def _maybe_consolidate_schema(
        self,
        run_id: str,
        config: SearchSpaceConfig,
    ) -> None:
        attempt_id = uuid.uuid4().hex
        with self._transaction(run_id):
            state = self._load_state(run_id)
            parent = self._load_schema_snapshot(run_id, state.schema_revision)
            evidence_events = self._load_evidence_events(run_id)
            tail_events = [
                event
                for event in evidence_events
                if event.event_index > parent.built_through_event_index
            ]
            if not self._schema_refresh_due(config, tail_events):
                return
            if self._schema_claim_is_live(config, state.schema_consolidation_claim):
                return
            target = tail_events[-1]
            claim = SchemaConsolidationClaim(
                attempt_id=attempt_id,
                base_schema_revision=state.schema_revision,
                target_event_index=target.event_index,
                target_event_id=target.event_id,
                started_at=_utc_timestamp(),
            )
            claimed_state = state.model_copy(deep=True)
            claimed_state.schema_consolidation_claim = claim
            claimed_state.schema_consolidation_attempts += 1
            claimed_state.state_version += 1
            claimed_state.updated_at = _utc_timestamp()
            self._write_state(run_id, claimed_state)

        execution: SchemaReviewerExecution | None = None
        try:
            consolidate = getattr(self.reviewer, "consolidate")
            execution = consolidate(
                self._schema_review_config(
                    config,
                    claimed_state,
                    parent,
                    tail_events,
                )
            )
            update = self._normalize_schema_update(
                execution.result,
                parent=parent,
                evidence_events=evidence_events,
                tail_events=tail_events,
            )
            self._validate_schema_update(
                update,
                evidence_events=evidence_events,
                tail_events=tail_events,
            )
        except Exception as exc:
            self._record_schema_consolidation_failure(
                run_id,
                attempt_id,
                f"{type(exc).__name__}: {exc}",
            )
            return

        with self._transaction(run_id):
            latest_state = self._load_state(run_id)
            latest_claim = latest_state.schema_consolidation_claim
            if latest_claim is None or latest_claim.attempt_id != attempt_id:
                return
            if latest_state.schema_revision != claim.base_schema_revision:
                released = latest_state.model_copy(deep=True)
                released.schema_consolidation_claim = None
                released.state_version += 1
                released.updated_at = _utc_timestamp()
                self._write_state(run_id, released)
                return

            latest_events = self._load_evidence_events(run_id)
            frozen_events = latest_events[: claim.target_event_index]
            if (
                not frozen_events
                or frozen_events[-1].event_id != claim.target_event_id
            ):
                raise RuntimeError(
                    "schema consolidation target no longer matches immutable evidence"
                )
            self._validate_schema_update(
                update,
                evidence_events=frozen_events,
                tail_events=tail_events,
            )
            next_schema_revision = latest_state.schema_revision + 1
            self._write_schema_snapshot(
                self._schema_snapshot_from_update(
                    run_id=run_id,
                    snapshot_version=next_schema_revision,
                    parent=parent,
                    update=update,
                    evidence_events=frozen_events,
                )
            )
            committed = latest_state.model_copy(deep=True)
            committed.schema_revision = next_schema_revision
            committed.schema_consolidation_claim = None
            committed.schema_consolidation_successes += 1
            committed.schema_reviewer_latency_ms_total += execution.latency_ms
            self._merge_usage(committed.schema_reviewer_usage, execution.usage)
            committed.last_schema_consolidation_error = None
            committed.state_version += 1
            committed.updated_at = _utc_timestamp()
            # state.json is the commit point; a published immutable snapshot is
            # reconciled on load if the process stops before this write.
            self._write_state(run_id, committed)

    @staticmethod
    def _schema_claim_is_live(
        config: SearchSpaceConfig,
        claim: SchemaConsolidationClaim | None,
    ) -> bool:
        if claim is None:
            return False
        try:
            started_at = calendar.timegm(
                time.strptime(claim.started_at, "%Y-%m-%dT%H:%M:%SZ")
            )
        except ValueError:
            return False
        return time.time() - started_at <= config.reviewer_timeout_seconds + 30

    def _record_schema_consolidation_failure(
        self,
        run_id: str,
        attempt_id: str,
        error: str,
    ) -> None:
        with self._transaction(run_id):
            state = self._load_state(run_id)
            claim = state.schema_consolidation_claim
            if claim is None or claim.attempt_id != attempt_id:
                return
            failed = state.model_copy(deep=True)
            failed.schema_consolidation_claim = None
            failed.schema_consolidation_failures += 1
            failed.last_schema_consolidation_error = error
            failed.state_version += 1
            failed.updated_at = _utc_timestamp()
            self._write_state(run_id, failed)

    def _review_plans(
        self,
        run_id: str,
        state: SearchSpaceState,
        schema_snapshot: SearchSchemaSnapshot,
        evidence_events: list[SearchEvidenceEvent],
    ) -> list[SpacePlanRecord]:
        event_by_plan_id = {event.plan_id: event for event in evidence_events}
        tail_plan_ids = {
            event.plan_id
            for event in evidence_events
            if event.event_index > schema_snapshot.built_through_event_index
        }
        plan_ids = [
            *(
                plan_id
                for plan_id in state.completed_coverage
                if plan_id in tail_plan_ids
            ),
            *state.active_reservations,
        ]
        covered: list[SpacePlanRecord] = []
        for plan_id in plan_ids:
            plan = self._load_plan(run_id, plan_id)
            event = event_by_plan_id.get(plan_id)
            if plan_id in state.completed_coverage and event is not None:
                plan = plan.model_copy(
                    update={
                        "proposal": event.proposal,
                        "coverage_eligible": event.coverage_eligible,
                        "realized_evidence": event.realized_evidence,
                        "search_event_id": event.event_id,
                    }
                )
            covered.append(plan)
        return covered

    @staticmethod
    def _outstanding_for_candidate(
        plans: list[SpacePlanRecord],
        state: SearchSpaceState,
        candidate_id: str,
    ) -> SpacePlanRecord | None:
        active = set(state.active_reservations)
        return next(
            (
                plan
                for plan in plans
                if plan.candidate_id == candidate_id
                and (plan.status == "reviewing" or plan.plan_id in active)
            ),
            None,
        )

    @staticmethod
    def _review_event_view(event: SearchEvidenceEvent) -> dict[str, Any]:
        card = event.proposal.plan_card()
        evidence = event.realized_evidence
        diff_excerpt = _bounded_review_text(
            evidence.diff_patch,
            SPACE_REVIEW_DIFF_EXCERPT_CHARS,
        )
        return {
            "event_id": event.event_id,
            "event_index": event.event_index,
            "candidate_id": event.candidate_id,
            "plan_id": event.plan_id,
            "proposal": {
                "intervention": card["intervention"],
                "scope": card["scope"],
                "expected_new_information": card["expected_new_information"],
            },
            "realized_evidence": {
                "artifact_delta_sha256": evidence.artifact_delta_sha256,
                "delta_files": _bounded_review_list(evidence.delta_files),
                "delta_file_count": len(evidence.delta_files),
                "changed_symbols": _bounded_review_list(evidence.changed_symbols),
                "changed_symbol_count": len(evidence.changed_symbols),
                "diff_stat": _bounded_review_text(
                    evidence.diff_stat,
                    SPACE_REVIEW_DIFF_STAT_CHARS,
                ),
                "diff_excerpt": diff_excerpt,
                "diff_excerpt_truncated": (
                    evidence.diff_truncated
                    or len(evidence.diff_patch) > SPACE_REVIEW_DIFF_EXCERPT_CHARS
                ),
                "metric_name": evidence.metric_name,
                "metric_direction": evidence.metric_direction,
                "score_before": evidence.score_before,
                "score_after": evidence.score_after,
                "score_delta": evidence.score_delta,
                "outcome": evidence.outcome,
                "validity_passed": evidence.validity_passed,
                "process_passed": evidence.process_passed,
                "infrastructure_failure": evidence.infrastructure_failure,
                "failure_class": evidence.failure_class,
            },
            "coverage_eligible": event.coverage_eligible,
            "created_at": event.created_at,
        }

    @staticmethod
    def _review_coverage_view(
        entry: SpaceCoverageEntry,
        *,
        include_all_refs: bool,
    ) -> dict[str, Any]:
        payload = entry.model_dump(mode="json")
        payload["description"] = _bounded_review_text(
            entry.description,
            SPACE_REVIEW_COVERAGE_TEXT_CHARS,
        )
        payload["context"] = _bounded_review_text(
            entry.context,
            SPACE_REVIEW_COVERAGE_TEXT_CHARS,
        )
        if include_all_refs:
            return payload
        payload["evidence_event_ids"] = _representative_refs(
            entry.evidence_event_ids
        )
        payload["evidence_plan_ids"] = _representative_refs(
            entry.evidence_plan_ids
        )
        payload["evidence_event_count"] = len(entry.evidence_event_ids)
        payload["evidence_plan_count"] = len(entry.evidence_plan_ids)
        payload["refs_truncated"] = (
            len(payload["evidence_event_ids"]) < len(entry.evidence_event_ids)
            or len(payload["evidence_plan_ids"]) < len(entry.evidence_plan_ids)
        )
        return payload

    @staticmethod
    def _review_config(
        config: SearchSpaceConfig,
        state: SearchSpaceState,
        schema_snapshot: SearchSchemaSnapshot,
        tail_events: list[SearchEvidenceEvent],
        covered: list[SpacePlanRecord],
    ) -> SearchSpaceConfig:
        schema = json.loads(canonical_json(schema_snapshot.space_schema))
        active = [
            plan
            for plan in covered
            if plan.status in {"accepted", "verifying"}
        ]
        schema["_runtime_search_state"] = {
            "state_version": state.state_version,
            "admission_revision": state.admission_revision,
            "evidence_revision": state.evidence_revision,
            "schema_snapshot_version": schema_snapshot.snapshot_version,
            "built_through_event_id": schema_snapshot.built_through_event_id,
            "coverage": [
                FileSearchSpaceRuntime._review_coverage_view(
                    entry,
                    include_all_refs=False,
                )
                for entry in schema_snapshot.coverage
            ],
            "tail_events": [
                FileSearchSpaceRuntime._review_event_view(event)
                for event in tail_events
            ],
            "active_reservations": [
                {
                    "plan_id": plan.plan_id,
                    "candidate_id": plan.candidate_id,
                    "plan_card": plan.proposal.plan_card(),
                }
                for plan in active
            ],
            "schema_refresh_due": False,
            "target_event_id": None,
        }
        return config.model_copy(update={"space_schema": schema})

    @staticmethod
    def _schema_review_config(
        config: SearchSpaceConfig,
        state: SearchSpaceState,
        schema_snapshot: SearchSchemaSnapshot,
        tail_events: list[SearchEvidenceEvent],
    ) -> SearchSpaceConfig:
        schema = json.loads(canonical_json(schema_snapshot.space_schema))
        schema["_runtime_search_state"] = {
            "state_version": state.state_version,
            "evidence_revision": state.evidence_revision,
            "schema_snapshot_version": schema_snapshot.snapshot_version,
            "built_through_event_id": schema_snapshot.built_through_event_id,
            "coverage": [
                FileSearchSpaceRuntime._review_coverage_view(
                    entry,
                    include_all_refs=True,
                )
                for entry in schema_snapshot.coverage
            ],
            "tail_events": [
                FileSearchSpaceRuntime._review_event_view(event)
                for event in tail_events
            ],
            "schema_refresh_due": True,
            "target_event_id": tail_events[-1].event_id,
        }
        return config.model_copy(update={"space_schema": schema})

    @staticmethod
    def _merge_usage(
        total: dict[str, int | float],
        current: dict[str, int | float],
    ) -> None:
        for key, value in current.items():
            total[key] = total.get(key, 0) + value

    @staticmethod
    def _validate_review_references(
        review: SpaceReviewDecision,
        covered: list[SpacePlanRecord],
        *,
        schema_snapshot: SearchSchemaSnapshot,
    ) -> None:
        known_ids = {plan.plan_id for plan in covered}
        known_ids.update(
            plan_id
            for entry in schema_snapshot.coverage
            for plan_id in entry.evidence_plan_ids
        )
        unknown = sorted(set(review.duplicate_of) - known_ids)
        if unknown:
            raise SpaceReviewerError(
                "reviewer cited unknown covered intervention plan ids: "
                + ", ".join(unknown)
            )

    @staticmethod
    def _normalize_schema_update(
        update: SpaceSchemaUpdate,
        *,
        parent: SearchSchemaSnapshot,
        evidence_events: list[SearchEvidenceEvent],
        tail_events: list[SearchEvidenceEvent],
    ) -> SpaceSchemaUpdate:
        normalized_schema = json.loads(canonical_json(update.space_schema))
        normalized_schema.pop("_runtime_search_state", None)
        parent_schema = json.loads(canonical_json(parent.space_schema))
        if not isinstance(normalized_schema.get("schema_version"), str):
            normalized_schema["schema_version"] = parent_schema["schema_version"]
        proposed_views = normalized_schema.get("views")
        if not isinstance(proposed_views, dict):
            proposed_views = {}
        parent_views = parent_schema["views"]
        normalized_schema["views"] = {
            name: (
                proposed_views[name]
                if isinstance(proposed_views.get(name), dict)
                and isinstance(proposed_views[name].get("description"), str)
                and proposed_views[name]["description"].strip()
                else parent_views[name]
            )
            for name in SPACE_VIEWS
        }
        event_by_id = {event.event_id: event for event in evidence_events}
        normalized_coverage: list[SpaceCoverageEntry] = []
        used_coverage_ids: set[str] = set()
        covered_event_ids: set[str] = set()

        for entry in update.coverage:
            known_events = [
                event_by_id[event_id]
                for event_id in entry.evidence_event_ids
                if event_id in event_by_id
                and event_by_id[event_id].coverage_eligible
            ]
            normalized = entry
            if len(known_events) == len(entry.evidence_event_ids):
                normalized = entry.model_copy(
                    update={
                        "evidence_plan_ids": list(
                            dict.fromkeys(event.plan_id for event in known_events)
                        ),
                        "outcomes": list(
                            dict.fromkeys(
                                event.realized_evidence.outcome
                                for event in known_events
                            )
                        ),
                    }
                )
            normalized_coverage.append(normalized)
            used_coverage_ids.add(normalized.coverage_id)
            covered_event_ids.update(normalized.evidence_event_ids)

        for entry in parent.coverage:
            missing = [
                event_id
                for event_id in entry.evidence_event_ids
                if event_id not in covered_event_ids
            ]
            if (
                missing == entry.evidence_event_ids
                and entry.coverage_id not in used_coverage_ids
            ):
                normalized_coverage.append(entry)
                used_coverage_ids.add(entry.coverage_id)
                covered_event_ids.update(entry.evidence_event_ids)

        for event in evidence_events:
            if not event.coverage_eligible or event.event_id in covered_event_ids:
                continue
            card = event.proposal.plan_card()
            coverage_id = f"evidence:{event.event_id}"
            suffix = 2
            while coverage_id in used_coverage_ids:
                coverage_id = f"evidence:{event.event_id}:{suffix}"
                suffix += 1
            normalized_coverage.append(
                SpaceCoverageEntry(
                    coverage_id=coverage_id,
                    description=str(card.get("intervention") or event.plan_id),
                    context=str(card.get("scope") or "unspecified context"),
                    evidence_event_ids=[event.event_id],
                    evidence_plan_ids=[event.plan_id],
                    outcomes=[event.realized_evidence.outcome],
                )
            )
            used_coverage_ids.add(coverage_id)
            covered_event_ids.add(event.event_id)

        return update.model_copy(
            update={
                "space_schema": normalized_schema,
                "coverage": normalized_coverage,
                "revision_evidence_event_ids": [
                    event.event_id for event in tail_events
                ],
            }
        )

    @staticmethod
    def _validate_schema_update(
        update: SpaceSchemaUpdate,
        *,
        evidence_events: list[SearchEvidenceEvent],
        tail_events: list[SearchEvidenceEvent],
    ) -> None:
        FileSearchSpaceRuntime._validate_schema(update.space_schema)
        if "_runtime_search_state" in update.space_schema:
            raise SpaceReviewerError(
                "schema update cannot persist private runtime search state"
            )
        expected_revision_refs = [event.event_id for event in tail_events]
        if update.revision_evidence_event_ids != expected_revision_refs:
            raise SpaceReviewerError(
                "schema update evidence watermark does not match the frozen tail"
            )
        FileSearchSpaceRuntime._validate_coverage_entries(
            update.coverage,
            evidence_events,
            required_event_ids={
                event.event_id for event in evidence_events if event.coverage_eligible
            },
            source="schema update",
        )

    @staticmethod
    def _validate_schema_snapshot_evidence(
        snapshot: SearchSchemaSnapshot,
        evidence_events: list[SearchEvidenceEvent],
    ) -> None:
        FileSearchSpaceRuntime._validate_schema(snapshot.space_schema)
        if "_runtime_search_state" in snapshot.space_schema:
            raise RuntimeError("schema snapshot contains private runtime search state")
        if snapshot.built_through_event_index > len(evidence_events):
            raise RuntimeError(
                "schema snapshot advances beyond the immutable evidence head"
            )
        expected_event_id = (
            evidence_events[snapshot.built_through_event_index - 1].event_id
            if snapshot.built_through_event_index
            else None
        )
        if snapshot.built_through_event_id != expected_event_id:
            raise RuntimeError("schema snapshot has inconsistent evidence head")
        built_events = evidence_events[: snapshot.built_through_event_index]
        built_event_ids = {event.event_id for event in built_events}
        unknown_revision_refs = sorted(
            set(snapshot.revision_evidence_event_ids) - built_event_ids
        )
        if unknown_revision_refs:
            raise RuntimeError(
                "schema snapshot cites evidence beyond its head: "
                + ", ".join(unknown_revision_refs)
            )
        FileSearchSpaceRuntime._validate_coverage_entries(
            snapshot.coverage,
            built_events,
            required_event_ids={
                event.event_id for event in built_events if event.coverage_eligible
            },
            source="schema snapshot",
        )

    @staticmethod
    def _validate_coverage_entries(
        coverage: list[SpaceCoverageEntry],
        evidence_events: list[SearchEvidenceEvent],
        *,
        required_event_ids: set[str],
        source: str,
    ) -> None:
        coverage_ids = [entry.coverage_id for entry in coverage]
        if len(coverage_ids) != len(set(coverage_ids)):
            raise SpaceReviewerError(f"{source} contains duplicate coverage ids")
        event_by_id = {event.event_id: event for event in evidence_events}
        known_event_ids = set(event_by_id)
        covered_event_ids = {
            event_id for entry in coverage for event_id in entry.evidence_event_ids
        }
        unknown_refs = sorted(covered_event_ids - known_event_ids)
        if unknown_refs:
            raise SpaceReviewerError(
                f"{source} coverage cited unknown evidence events: "
                + ", ".join(unknown_refs)
            )
        ineligible_refs = sorted(
            event_id
            for event_id in covered_event_ids
            if not event_by_id[event_id].coverage_eligible
        )
        if ineligible_refs:
            raise SpaceReviewerError(
                f"{source} coverage cited ineligible evidence events: "
                + ", ".join(ineligible_refs)
            )
        forgotten = sorted(required_event_ids - covered_event_ids)
        if forgotten:
            raise SpaceReviewerError(
                f"{source} cannot forget verified coverage events: "
                + ", ".join(forgotten)
            )
        for entry in coverage:
            expected_plan_ids = {
                event_by_id[event_id].plan_id
                for event_id in entry.evidence_event_ids
            }
            if expected_plan_ids != set(entry.evidence_plan_ids):
                raise SpaceReviewerError(
                    f"coverage entry {entry.coverage_id} has inconsistent evidence plan ids"
                )
            expected_outcomes = {
                event_by_id[event_id].realized_evidence.outcome
                for event_id in entry.evidence_event_ids
            }
            if expected_outcomes != set(entry.outcomes):
                raise SpaceReviewerError(
                    f"coverage entry {entry.coverage_id} has inconsistent outcomes"
                )

    @staticmethod
    def _conflict_scope(
        review: SpaceReviewDecision | None,
        state: SearchSpaceState,
    ) -> Literal["completed", "active", "mixed"] | None:
        if review is None or review.decision != "reject":
            return None
        refs = set(review.duplicate_of)
        completed = bool(refs.intersection(state.completed_coverage))
        active = bool(refs.intersection(state.active_reservations))
        if completed and active:
            return "mixed"
        if active:
            return "active"
        if completed:
            return "completed"
        return None

    @staticmethod
    def _normalize_review_reason(
        review: SpaceReviewDecision,
        state: SearchSpaceState,
    ) -> SpaceReviewDecision:
        if review.decision != "reject":
            return review
        reason_code = (
            "active_plan_collision"
            if set(review.duplicate_of).intersection(state.active_reservations)
            else "duplicate_prior_intervention"
        )
        if review.reason_code == reason_code:
            return review
        return review.model_copy(update={"reason_code": reason_code})

    @staticmethod
    def _candidate_loop_signals(
        plans: list[SpacePlanRecord],
    ) -> list[dict[str, Any]]:
        candidate_ids = sorted({plan.candidate_id for plan in plans})
        signals: list[dict[str, Any]] = []
        for candidate_id in candidate_ids:
            candidate_plans = sorted(
                (plan for plan in plans if plan.candidate_id == candidate_id),
                key=lambda plan: plan.proposal_index,
            )
            duplicate_suffix: list[SpacePlanRecord] = []
            for plan in reversed(candidate_plans):
                if plan.status == "reviewing":
                    continue
                if plan.review is None or plan.review.decision != "reject":
                    break
                duplicate_suffix.append(plan)
            conflict_counts: dict[str, int] = {}
            point_counts: dict[str, int] = {}
            region_counts: dict[str, int] = {}
            for plan in duplicate_suffix:
                assert plan.review is not None
                for conflict in plan.review.duplicate_of:
                    conflict_counts[conflict] = conflict_counts.get(conflict, 0) + 1
                if plan.review.point_key:
                    point_counts[plan.review.point_key] = (
                        point_counts.get(plan.review.point_key, 0) + 1
                    )
                if plan.review.region_key:
                    region_counts[plan.review.region_key] = (
                        region_counts.get(plan.review.region_key, 0) + 1
                    )
            streak = len(duplicate_suffix)
            repeated_conflicts = sorted(
                key for key, count in conflict_counts.items() if count == streak
            )
            repeated_points = sorted(
                key for key, count in point_counts.items() if count == streak
            )
            repeated_regions = sorted(
                key for key, count in region_counts.items() if count == streak
            )
            signals.append(
                {
                    "candidate_id": candidate_id,
                    "consecutive_duplicate_reviews": streak,
                    "possible_spinning": bool(
                        streak >= 3
                        and (repeated_conflicts or repeated_points or repeated_regions)
                    ),
                    "repeated_conflict_refs": repeated_conflicts,
                    "repeated_point_keys": repeated_points,
                    "repeated_region_keys": repeated_regions,
                }
            )
        return signals

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        if not isinstance(schema.get("schema_version"), str):
            raise ValueError("space schema requires string schema_version")
        views = schema.get("views")
        if not isinstance(views, dict) or set(views) != set(SPACE_VIEWS):
            raise ValueError(
                "space schema views must be exactly: " + ", ".join(SPACE_VIEWS)
            )
        for name in SPACE_VIEWS:
            value = views.get(name)
            if not isinstance(value, dict) or not isinstance(
                value.get("description"), str
            ):
                raise ValueError(f"space schema view {name} requires description")

    @staticmethod
    def _open_response(config: SearchSpaceConfig) -> dict[str, Any]:
        return {
            "experiment_id": config.experiment_id,
            "run_id": config.run_id,
            "mode": config.mode,
            "protocol_version": config.protocol_version,
            "schema_path": config.schema_path,
            "schema_sha256": config.schema_sha256,
            "reviewer_model": config.reviewer_model,
            "reviewer_reasoning_effort": config.reviewer_reasoning_effort,
            "schema_consolidation_interval": config.schema_consolidation_interval,
            "status": "open",
        }

    def _candidate_response(
        self,
        plan: SpacePlanRecord,
        state: SearchSpaceState,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "decision": "reject" if plan.status == "rejected" else "accept",
        }
        if plan.status != "rejected" or plan.review is None:
            return response
        response["duplicate_of"] = list(plan.review.duplicate_of)
        duplicate_plans: list[dict[str, Any]] = []
        for duplicate_id in plan.review.duplicate_of:
            duplicate = self._load_plan(plan.run_id, duplicate_id)
            card = duplicate.proposal.plan_card()
            duplicate_plans.append(
                {
                    "plan_id": duplicate_id,
                    "coverage_status": (
                        "completed_coverage"
                        if duplicate_id in state.completed_coverage
                        else "active_reservation"
                    ),
                    "plan_card": {
                        "intervention": card["intervention"],
                        "scope": card["scope"],
                        "expected_new_information": card[
                            "expected_new_information"
                        ],
                    },
                }
            )
        response["duplicate_plans"] = duplicate_plans
        return response


# Source compatibility for the frozen serial experiment and older callers.
FileSpaceExperimentRuntime = FileSearchSpaceRuntime


def _session_from_root(
    root_dir: Path,
    agent_session_id: str,
) -> AgentSessionRecord | None:
    matches = sorted(
        (root_dir / "runs").glob(f"*/agent_sessions/{agent_session_id}.json")
    )
    if len(matches) != 1:
        return None
    try:
        return AgentSessionRecord.model_validate(load_json(matches[0]))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _verified_solution_restore(
    root_dir: Path,
    session: AgentSessionRecord,
    input_text: str,
) -> bool:
    refs = [
        *re.findall(
            r"\bgit\s+restore\s+--source(?:=|\s+)([0-9a-fA-F]{7,40})"
            r"[^\n\r;]*--\s+(?:[^\s;]+/)?solution\.py\b",
            input_text,
        ),
        *re.findall(
            r"\bgit\s+checkout\s+([0-9a-fA-F]{7,40})"
            r"\s+--\s+(?:[^\s;]+/)?solution\.py\b",
            input_text,
        ),
    ]
    if len(refs) != 1:
        return False
    candidate_path = (
        root_dir
        / "runs"
        / session.run_id
        / "candidates"
        / session.candidate_id
        / "candidate.json"
    )
    try:
        candidate = load_json(candidate_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    verified_heads = {
        str(iteration.get("git_head"))
        for iteration in candidate.get("iterations") or []
        if isinstance(iteration, dict)
        and iteration.get("process_passed") is True
        and isinstance(iteration.get("git_head"), str)
    }
    requested = refs[0].lower()
    return sum(head.lower().startswith(requested) for head in verified_heads) == 1


def candidate_pre_tool_block_reason(
    root_dir: Path | str,
    agent_session_id: str,
    tool_name: str,
    raw_tool_input: Any,
) -> str | None:
    """Return a neutral admission block for mutation/evaluation without a plan."""

    root = Path(root_dir).resolve()
    session = _session_from_root(root, agent_session_id)
    if session is None:
        return None
    runtime = FileSearchSpaceRuntime(root)
    if not runtime.is_enabled(session.run_id):
        return None

    normalized = tool_name.strip().lower().replace("-", "_")
    logical = normalized.rsplit("__", 1)[-1]
    input_text = (
        raw_tool_input
        if isinstance(raw_tool_input, str)
        else json.dumps(raw_tool_input, sort_keys=True, ensure_ascii=True)
    )
    proposes = "search_space_propose" in input_text or logical == "search_space_propose"

    patch_targets = re.findall(
        r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$",
        input_text,
        flags=re.MULTILINE,
    )
    handoff_only_mutation = bool(patch_targets) and all(
        target.replace("\\", "/").endswith("/.tmp/handoff.json")
        or target.replace("\\", "/") == ".tmp/handoff.json"
        for target in patch_targets
    )
    verified_restore = _verified_solution_restore(root, session, input_text)

    direct_mutation = (
        logical in {"apply_patch", "edit", "write"}
        and not handoff_only_mutation
    )
    nested_mutation = not handoff_only_mutation and bool(
        re.search(r"tools\.(?:apply_patch|edit|write)\s*\(", input_text)
        or re.search(r"\bapply_patch\b", input_text)
    )
    direct_verifier = logical == "search_run_verifier"
    nested_verifier = "search_run_verifier" in input_text
    evaluator_execution = bool(
        re.search(
            r"(?:python(?:3)?\s+[^\n\r;]*|\./)(?:runner|evaluate)\.py\b",
            input_text,
            flags=re.IGNORECASE,
        )
    )
    solution_shell_mutation = not verified_restore and bool(
        re.search(
            r"(?:sed\s+-i|perl\s+-pi|tee\s+|(?:cp|mv|rm)\s+)[^\n\r;]*solution\.py",
            input_text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r">{1,2}\s*(?:[^\s;]+/)?solution\.py\b",
            input_text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"(?:write_text|write_bytes|open)\s*\([^\n\r)]*solution\.py",
            input_text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"(?:git\s+(?:apply|checkout|restore)|ruff\s+format|black)"
            r"[^\n\r;]*solution\.py",
            input_text,
            flags=re.IGNORECASE,
        )
    )
    material_operation = any(
        (
            direct_mutation,
            nested_mutation,
            direct_verifier,
            nested_verifier,
            evaluator_execution,
            solution_shell_mutation,
        )
    )
    if proposes and material_operation:
        return (
            "Search-space proposals must be submitted in a separate tool call. "
            "Do not combine search_space_propose with editing, evaluator execution, "
            "or search_run_verifier."
        )
    if proposes or runtime.has_accepted_plan(session.run_id, session.candidate_id):
        return None
    if material_operation:
        return (
            "Search-space admission requires an accepted intervention plan before "
            "editing solution.py, executing the evaluator, or calling search_run_verifier. "
            "Call search_space_propose first; a rejected plan must be replaced rather "
            "than executed."
        )
    return None
