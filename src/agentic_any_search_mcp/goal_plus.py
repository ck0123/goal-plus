from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_any_search_mcp.models import (
    GoalPlusGateEvent,
    GoalPlusGateResult,
    GoalPlusLinkedSearch,
    GoalPlusNextAction,
    GoalPlusRecord,
    GoalPlusSpecDraft,
    GoalPlusStatus,
    GoalPlusTriage,
)


TERMINAL_STATUSES: set[GoalPlusStatus] = {"blocked", "complete", "abandoned"}
SEARCH_TOOL_SUFFIXES = (
    "search_freeze_spec",
    "search_create",
    "search_plan_next",
    "search_start_batch",
    "search_start_agent_session",
    "search_select",
    "search_report",
    "search_promote",
)


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    tmp_path.replace(path)


class FileGoalPlusRuntime:
    """Small file-backed state machine for goal-plus orchestration."""

    def __init__(self, root_dir: Path | str = ".search") -> None:
        self.root_dir = Path(root_dir).resolve()
        self.goals_dir = self.root_dir / "goal-plus"
        self.goals_dir.mkdir(parents=True, exist_ok=True)

    def create_goal(
        self,
        raw_goal: str,
        source_path: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> GoalPlusRecord:
        goal_plus_id = self._next_goal_id()
        now = utc_timestamp()
        record = GoalPlusRecord(
            goal_plus_id=goal_plus_id,
            raw_goal=raw_goal.strip(),
            source_path=source_path,
            status="active",
            phase="intake",
            policy=policy or {},
            next_action=GoalPlusNextAction(
                kind="record_triage",
                description="Classify whether the raw goal should run like /goal or upgrade to Search Mode.",
                required=True,
            ),
            created_at=now,
            updated_at=now,
        )
        self._write_record(record)
        self._append_event(goal_plus_id, "created", {"raw_goal": record.raw_goal})
        return record

    def status(self, goal_plus_id: str) -> GoalPlusRecord:
        return self._load_record(goal_plus_id)

    def list_events(self, goal_plus_id: str) -> list[dict[str, Any]]:
        event_path = self._events_path(goal_plus_id)
        if not event_path.exists():
            return []
        events = []
        with event_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def record_triage(
        self,
        goal_plus_id: str,
        triage: GoalPlusTriage | dict[str, Any],
    ) -> GoalPlusRecord:
        parsed = (
            triage if isinstance(triage, GoalPlusTriage) else GoalPlusTriage.model_validate(triage)
        )
        record = self._load_record(goal_plus_id)
        phase, next_action = self._triage_phase_and_action(parsed)
        updated = record.model_copy(
            update={
                "triage": parsed,
                "phase": phase,
                "next_action": next_action,
                "updated_at": utc_timestamp(),
            }
        )
        self._write_record(updated)
        self._append_event(goal_plus_id, "triage_recorded", parsed.model_dump(mode="json"))
        return updated

    def save_spec_draft(
        self,
        goal_plus_id: str,
        spec_draft: GoalPlusSpecDraft | dict[str, Any],
    ) -> GoalPlusRecord:
        parsed = (
            spec_draft
            if isinstance(spec_draft, GoalPlusSpecDraft)
            else GoalPlusSpecDraft.model_validate(spec_draft)
        )
        record = self._load_record(goal_plus_id)
        origin = parsed.origin or (
            record.triage.identified_at if record.triage is not None else "in_progress"
        )
        parsed = parsed.model_copy(update={"origin": origin})
        next_action = self._spec_draft_next_action(parsed)
        updated = record.model_copy(
            update={
                "phase": "spec_discovery",
                "spec_draft": parsed,
                "next_action": next_action,
                "updated_at": utc_timestamp(),
            }
        )
        self._write_record(updated)
        self._append_event(goal_plus_id, "spec_draft_saved", parsed.model_dump(mode="json"))
        return updated

    def confirm_frozen_verifier(
        self,
        goal_plus_id: str,
        confirmed_by: str = "user",
        evidence: dict[str, Any] | None = None,
    ) -> GoalPlusRecord:
        record = self._load_record(goal_plus_id)
        if record.spec_draft is None:
            raise ValueError("Cannot confirm frozen verifier before saving a spec draft.")
        if record.spec_draft.confidence != "high" or record.spec_draft.open_questions:
            raise ValueError("Cannot confirm a spec draft that is not search-ready.")

        spec_draft = record.spec_draft.model_copy(
            update={"user_confirmed_frozen_verifier": True}
        )
        updated = record.model_copy(
            update={
                "spec_draft": spec_draft,
                "next_action": GoalPlusNextAction(
                    kind="freeze_search_spec",
                    description="Freeze the confirmed SearchSpec and verifier artifacts, then create a search run.",
                    required=True,
                ),
                "updated_at": utc_timestamp(),
            }
        )
        self._write_record(updated)
        self._append_event(
            goal_plus_id,
            "frozen_verifier_confirmed",
            {"confirmed_by": confirmed_by, "evidence": evidence or {}},
        )
        return updated

    def link_search_run(
        self,
        goal_plus_id: str,
        frozen_spec_id: str,
        run_id: str,
    ) -> GoalPlusRecord:
        record = self._load_record(goal_plus_id)
        linked = (record.linked_search or GoalPlusLinkedSearch()).model_copy(
            update={"frozen_spec_id": frozen_spec_id, "run_id": run_id}
        )
        updated = record.model_copy(
            update={
                "phase": "search",
                "linked_search": linked,
                "next_action": GoalPlusNextAction(
                    kind="drive_search_run",
                    description="Drive the linked Search MCP run through candidate verification, selection, report, and promotion.",
                    required=True,
                ),
                "updated_at": utc_timestamp(),
            }
        )
        self._write_record(updated)
        self._append_event(
            goal_plus_id,
            "search_linked",
            {"frozen_spec_id": frozen_spec_id, "run_id": run_id},
        )
        return updated

    def record_search_result(
        self,
        goal_plus_id: str,
        run_id: str,
        selected_candidate_id: str | None = None,
        report_path: str | None = None,
        promotion_artifact_path: str | None = None,
        summary: str | None = None,
    ) -> GoalPlusRecord:
        record = self._load_record(goal_plus_id)
        linked = (record.linked_search or GoalPlusLinkedSearch()).model_copy(
            update={
                "run_id": run_id,
                "selected_candidate_id": selected_candidate_id,
                "report_path": report_path,
                "promotion_artifact_path": promotion_artifact_path,
                "summary": summary,
            }
        )
        updated = record.model_copy(
            update={
                "phase": "final_audit",
                "linked_search": linked,
                "next_action": GoalPlusNextAction(
                    kind="audit_raw_goal",
                    description="audit the original raw goal against current evidence before marking goal-plus complete.",
                    required=True,
                ),
                "updated_at": utc_timestamp(),
            }
        )
        self._write_record(updated)
        self._append_event(
            goal_plus_id,
            "search_result_recorded",
            linked.model_dump(mode="json"),
        )
        return updated

    def set_status(
        self,
        goal_plus_id: str,
        status: GoalPlusStatus,
        reason: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
        next_action: GoalPlusNextAction | dict[str, Any] | None = None,
    ) -> GoalPlusRecord:
        record = self._load_record(goal_plus_id)
        parsed_next_action = (
            GoalPlusNextAction.model_validate(next_action)
            if isinstance(next_action, dict)
            else next_action
        )
        updated = record.model_copy(
            update={
                "status": status,
                "next_action": None if status in TERMINAL_STATUSES else parsed_next_action,
                "updated_at": utc_timestamp(),
            }
        )
        self._write_record(updated)
        self._append_event(
            goal_plus_id,
            "status_changed",
            {"status": status, "reason": reason, "evidence": evidence or []},
        )
        return updated

    def gate(
        self,
        goal_plus_id: str,
        event: GoalPlusGateEvent,
        context: dict[str, Any],
    ) -> GoalPlusGateResult:
        record = self._load_record(goal_plus_id)
        if record.status != "active":
            return self._record_gate(record, event, "allow")

        if event in {"stop", "subagent_stop"} and record.next_action is not None:
            if record.next_action.required:
                return self._record_gate(
                    record,
                    event,
                    "block",
                    reason=record.next_action.description,
                    continuation_prompt=self._continuation_prompt(record),
                )
            return self._record_gate(record, event, "allow")

        if event == "pre_tool_use":
            tool_name = self._tool_name(context)
            if self._is_search_tool(tool_name) and not self._has_search_ready_spec(record):
                return self._record_gate(
                    record,
                    event,
                    "block",
                    reason=self._search_block_reason(record),
                )
            if self._tool_matches(tool_name, "search_promote") and not (
                record.linked_search and record.linked_search.selected_candidate_id
            ):
                return self._record_gate(
                    record,
                    event,
                    "block",
                    reason="Promotion is blocked until a selected candidate is recorded.",
                )

        return self._record_gate(record, event, "allow")

    def _triage_phase_and_action(
        self,
        triage: GoalPlusTriage,
    ) -> tuple[str, GoalPlusNextAction]:
        if not triage.is_optimization or triage.recommended_phase == "goal":
            return (
                "goal",
                GoalPlusNextAction(
                    kind="work_goal_like",
                    description="Continue as an ordinary goal-like task using current workspace evidence.",
                    required=False,
                ),
            )
        missing = ", ".join(triage.missing) if triage.missing else "spec details"
        if triage.recommended_phase == "search" and triage.identified_at == "initial":
            missing = "user confirmation for the frozen verifier"
        return (
            "spec_discovery",
            GoalPlusNextAction(
                kind=(
                    "draft_initial_search_spec"
                    if triage.recommended_phase == "search"
                    else "discover_spec"
                ),
                description=f"Complete spec discovery before search. Missing: {missing}.",
                required=True,
                metadata={"missing": triage.missing},
            ),
        )

    def _record_gate(
        self,
        record: GoalPlusRecord,
        event: GoalPlusGateEvent,
        decision: str,
        reason: str | None = None,
        continuation_prompt: str | None = None,
    ) -> GoalPlusGateResult:
        counters = dict(record.hook_counters)
        counters[event] = counters.get(event, 0) + 1
        updated = record.model_copy(
            update={"hook_counters": counters, "updated_at": utc_timestamp()}
        )
        self._write_record(updated)
        self._append_event(
            record.goal_plus_id,
            f"gate_{decision}ed",
            {"event": event, "reason": reason},
        )
        return GoalPlusGateResult(
            decision=decision,  # type: ignore[arg-type]
            phase=record.phase,
            status=record.status,
            reason=reason,
            continuation_prompt=continuation_prompt,
        )

    def _continuation_prompt(self, record: GoalPlusRecord) -> str:
        action = record.next_action
        action_text = action.description if action else "Continue the active goal-plus task."
        return (
            f"Goal Plus is still active in phase {record.phase}.\n"
            "Do not stop yet. The next required action is:\n"
            f"  {action_text}\n"
            "After completing that action, update the goal-plus state before stopping."
        )

    def _spec_draft_next_action(self, spec_draft: GoalPlusSpecDraft) -> GoalPlusNextAction:
        if spec_draft.confidence != "high" or spec_draft.open_questions:
            return GoalPlusNextAction(
                kind="resolve_spec_questions",
                description="Resolve open questions before freezing the SearchSpec.",
                required=True,
                metadata={"open_questions": spec_draft.open_questions},
            )
        if (
            spec_draft.origin == "initial"
            and not spec_draft.user_confirmed_frozen_verifier
        ):
            return GoalPlusNextAction(
                kind="confirm_frozen_verifier",
                description="Ask the user to confirm the frozen verifier, metric, edit surface, and promotion rule before Search Mode.",
                required=True,
            )
        return GoalPlusNextAction(
            kind="freeze_search_spec",
            description="Freeze the high-confidence SearchSpec and verifier artifacts, then create a search run.",
            required=True,
        )

    def _has_search_ready_spec(self, record: GoalPlusRecord) -> bool:
        return (
            record.spec_draft is not None
            and record.spec_draft.confidence == "high"
            and not record.spec_draft.open_questions
            and (
                record.spec_draft.origin != "initial"
                or record.spec_draft.user_confirmed_frozen_verifier
            )
        )

    def _search_block_reason(self, record: GoalPlusRecord) -> str:
        spec_draft = record.spec_draft
        if spec_draft is None:
            return "Search tools require a high-confidence frozen spec draft first."
        if spec_draft.confidence != "high" or spec_draft.open_questions:
            return "Search tools require a high-confidence frozen spec draft first."
        if spec_draft.origin == "initial" and not spec_draft.user_confirmed_frozen_verifier:
            return "Search tools require user confirmation of the initial frozen verifier first."
        return "Search tools require a search-ready spec draft first."

    def _tool_name(self, context: dict[str, Any]) -> str:
        value = context.get("tool_name") or context.get("toolName") or ""
        return str(value)

    def _is_search_tool(self, tool_name: str) -> bool:
        return any(self._tool_matches(tool_name, suffix) for suffix in SEARCH_TOOL_SUFFIXES)

    def _tool_matches(self, tool_name: str, suffix: str) -> bool:
        return tool_name == suffix or tool_name.endswith(f"__{suffix}") or tool_name.endswith(
            f".{suffix}"
        )

    def _next_goal_id(self) -> str:
        max_index = 0
        for path in self.goals_dir.glob("gp_*"):
            try:
                max_index = max(max_index, int(path.name.removeprefix("gp_")))
            except ValueError:
                continue
        return f"gp_{max_index + 1:04d}"

    def _goal_dir(self, goal_plus_id: str) -> Path:
        return self.goals_dir / goal_plus_id

    def _goal_path(self, goal_plus_id: str) -> Path:
        return self._goal_dir(goal_plus_id) / "goal.json"

    def _events_path(self, goal_plus_id: str) -> Path:
        return self._goal_dir(goal_plus_id) / "events.jsonl"

    def _load_record(self, goal_plus_id: str) -> GoalPlusRecord:
        return GoalPlusRecord.model_validate(read_json(self._goal_path(goal_plus_id)))

    def _write_record(self, record: GoalPlusRecord) -> None:
        write_json(self._goal_path(record.goal_plus_id), record.model_dump(mode="json"))

    def _append_event(
        self,
        goal_plus_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event_path = self._events_path(goal_plus_id)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event_id": f"evt_{uuid4().hex[:12]}",
            "event_type": event_type,
            "created_at": utc_timestamp(),
            "payload": payload,
        }
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True))
            handle.write("\n")
