from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from goal_plus.agent_pool import HostPoolContract
from goal_plus.host_observability import (
    collect_codex_observability,
    collect_metadata_observability,
    collect_pi_observability,
)
from goal_plus.models import AgentHostKind, AgentSessionRecord
from goal_plus.paths import DEFAULT_RUNTIME_ROOT


PORTABLE_STRATEGY_MODES = {
    "agent",
    "agent_guided",
    "default",
    "random",
    "random_mode",
}


class UnsupportedHostCapability(RuntimeError):
    """Raised when a host cannot provide a requested worker lifecycle action."""


@dataclass(frozen=True)
class HostCapabilities:
    supports_bind_handle: bool
    supports_same_worker_continue: bool
    uses_background_workers: bool = False
    continuation: str | None = None
    supports_soft_closeout: bool = False
    supports_model_override: bool = False
    supports_reasoning_effort: bool = False
    supports_service_tier: bool = False
    supports_usage_metadata: bool = False
    supports_process_kill: bool = False
    pool: HostPoolContract = field(default_factory=HostPoolContract)


class AgentHostAdapter(Protocol):
    name: AgentHostKind
    capabilities: HostCapabilities

    def collect_observability(
        self,
        session: AgentSessionRecord,
    ) -> dict[str, Any]:
        ...

    def build_launch_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        short_intent: str,
        one_paragraph_idea: str,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
    ) -> dict[str, Any]:
        ...

    def build_continue_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        external_id: str | None,
        task_name: str | None,
        short_intent: str,
        one_paragraph_idea: str,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        host_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


def _normalize_mode(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def portable_strategy_mode(value: str) -> bool:
    return _normalize_mode(value) in PORTABLE_STRATEGY_MODES


def _codex_task_name(agent_session_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", agent_session_id.lower()).strip("_")
    return f"search_{normalized or 'agent'}"


def _budget_max_runtime_ms(worker_budget: dict[str, Any]) -> int | None:
    seconds = worker_budget.get("max_runtime_seconds")
    if seconds is None:
        return None
    return int(seconds) * 1000


def _soft_closeout_seconds(max_runtime_seconds: int) -> int:
    return min(45, max(5, int(max_runtime_seconds) // 5))


CODEX_CLOSEOUT_MESSAGE = (
    "Worker deadline is approaching. Stop starting new work, run one final "
    "search_run_verifier if needed, write .tmp/handoff.json, and return a concise summary."
)

CODEX_WORKER_BOUNDARY = (
    "You are a Search candidate worker, not the search orchestrator. "
    "First call search_get_agent_context with the supplied agent_session_id, "
    "work only in that candidate workspace, and call search_run_verifier for "
    "that agent session before returning. Do not call search_plan_next, "
    "search_start_batch, search_select, search_report, or search_promote. "
    "Do not call any `goal_plus_*` tool. Parent-run planning, selection, "
    "reporting, promotion, and final audit are outside your role. If a verifier "
    "returns failure_class=VerifierWorkspaceSideEffect or "
    "candidate_action=stop_and_report, do not clean verifier outputs or retry; "
    "record the infrastructure blocker and return immediately."
)


def _codex_worker_contract(worker_prompt: str | None) -> str:
    """Keep the portable worker boundary even when agent metadata is hidden."""
    prompt = (worker_prompt or "").strip()
    if not prompt or prompt == CODEX_WORKER_BOUNDARY:
        return CODEX_WORKER_BOUNDARY
    return f"{CODEX_WORKER_BOUNDARY}\n\n{prompt}"


def _codex_budget_control(
    target: str,
    worker_budget: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not worker_budget:
        return None
    max_runtime_seconds = worker_budget.get("max_runtime_seconds")
    budget_control: dict[str, Any] = {
        "mode": "parent_watchdog",
        "max_runtime_seconds": max_runtime_seconds,
        "on_exceed": worker_budget.get("on_exceed", "interrupt"),
        "interrupt_tool": "interrupt_agent",
        "interrupt_target": target,
    }
    max_runtime_ms = _budget_max_runtime_ms(worker_budget)
    if max_runtime_seconds is not None and max_runtime_ms is not None:
        soft_closeout_seconds = _soft_closeout_seconds(int(max_runtime_seconds))
        final_wait_timeout_ms = soft_closeout_seconds * 1000
        min_runtime_seconds = worker_budget.get("min_runtime_seconds")
        if (
            min_runtime_seconds is not None
            and int(min_runtime_seconds)
            >= int(max_runtime_seconds) - soft_closeout_seconds
        ):
            raise ValueError(
                "codex worker_budget.min_runtime_seconds must end before the "
                "parent watchdog soft-closeout point; increase "
                "max_runtime_seconds to reserve worker closeout time"
            )
        budget_control.update(
            {
                "initial_wait_timeout_ms": max_runtime_ms - final_wait_timeout_ms,
                "soft_closeout_seconds": soft_closeout_seconds,
                "closeout_tool": "send_message",
                "closeout_target": target,
                "closeout_message": CODEX_CLOSEOUT_MESSAGE,
                "final_wait_timeout_ms": final_wait_timeout_ms,
            }
        )
        if min_runtime_seconds is not None or worker_budget.get(
            "min_verifier_runs"
        ) is not None:
            budget_control["autoresearch_lease"] = {
                "mode": "subagent_stop",
                "min_runtime_seconds": int(min_runtime_seconds or 0),
                "min_verifier_runs": int(
                    worker_budget.get("min_verifier_runs") or 1
                ),
                "start_event": "native_child_session",
                "release_before_parent_closeout": True,
            }
    if worker_budget.get("max_turns") is not None:
        budget_control["max_turns_hint"] = worker_budget["max_turns"]
    return budget_control


class OpenCodeAdapter:
    name: AgentHostKind = "opencode"
    capabilities = HostCapabilities(
        supports_bind_handle=True,
        supports_same_worker_continue=True,
    )

    def collect_observability(self, session: AgentSessionRecord) -> dict[str, Any]:
        return collect_metadata_observability(session)

    def build_launch_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        short_intent: str,
        one_paragraph_idea: str,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
    ) -> dict[str, Any]:
        return {
            "subagent_type": worker_agent_type or "SearchCandidateAgent",
            "description": f"{candidate_id} {short_intent}",
            "prompt": (
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                f"idea: {one_paragraph_idea}"
            ),
        }

    def build_continue_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        external_id: str | None,
        task_name: str | None,
        short_intent: str,
        one_paragraph_idea: str,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        host_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not external_id:
            raise UnsupportedHostCapability(
                "opencode continuation requires a bound OpenCode session id"
            )
        return {
            "task_id": external_id,
            "subagent_type": worker_agent_type or "SearchCandidateAgent",
            "description": f"{candidate_id} continue {short_intent}",
            "prompt": (
                "continue_existing_agent_session=true; "
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                "refresh authoritative runtime context with search_get_agent_context "
                "before editing; continue the same candidate and workspace; "
                f"directive: {one_paragraph_idea}"
            ),
        }


class CodexAdapter:
    name: AgentHostKind = "codex"
    capabilities = HostCapabilities(
        supports_bind_handle=True,
        supports_same_worker_continue=True,
        supports_soft_closeout=True,
        supports_model_override=True,
        supports_reasoning_effort=True,
        supports_service_tier=True,
        supports_usage_metadata=True,
        pool=HostPoolContract(
            launch_mode="async",
            wait_mode="wait_any",
            continuation_mode="same_worker",
            deadline_mode="parent_watchdog",
            recovery_mode="host_resident",
            completion_stage="candidate_ready",
            submit_tool="spawn_agent",
            wait_tool="wait_agent",
            snapshot_tool="list_agents",
            continue_tool="followup_task",
            closeout_tool="send_message",
            interrupt_tool="interrupt_agent",
        ),
    )

    def collect_observability(self, session: AgentSessionRecord) -> dict[str, Any]:
        return collect_codex_observability(session)

    def build_launch_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        short_intent: str,
        one_paragraph_idea: str,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
    ) -> dict[str, Any]:
        task_name = _codex_task_name(agent_session_id)
        worker_contract = _codex_worker_contract(worker_prompt)
        payload = {
            "tool": "spawn_agent",
            "task_name": task_name,
            "agent_type": "default",
            "fork_turns": "none",
            "message": (
                f"{worker_contract}\n\n"
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                f"assigned_worker_budget={worker_budget or 'host default'}; "
                f"idea: {one_paragraph_idea}"
            ),
        }
        # The default worker contract is already embedded in ``message``. Map
        # it to Codex's built-in no-config role: selecting the project-local
        # role reloads config after model inheritance and can discard a
        # runtime-only parent model before service-tier validation. An explicit
        # ``default`` also prevents the orchestrator from inventing that custom
        # role when projecting the returned payload. Non-default roles remain
        # an explicit opt-in.
        if worker_agent_type and worker_agent_type != "search_candidate_agent":
            payload["agent_type"] = worker_agent_type
        if worker_launch:
            payload.update(
                {
                    key: value
                    for key, value in worker_launch.items()
                    if key in {"model", "reasoning_effort", "service_tier"}
                    and value is not None
                }
            )
        budget_control = _codex_budget_control(task_name, worker_budget)
        if budget_control:
            payload["budget_control"] = budget_control
        return payload

    def build_continue_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        external_id: str | None,
        task_name: str | None,
        short_intent: str,
        one_paragraph_idea: str,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        host_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = task_name or external_id
        if not target:
            raise UnsupportedHostCapability(
                "codex continuation requires a bound task name or agent id"
            )
        worker_contract = _codex_worker_contract(worker_prompt)
        payload: dict[str, Any] = {
            "tool": "followup_task",
            "target": target,
            "message": (
                f"{worker_contract}\n\n"
                "continue_existing_agent_session=true; "
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                "refresh authoritative runtime context with "
                "search_get_agent_context before editing; continue the same "
                f"candidate and workspace; directive: {one_paragraph_idea}"
            ),
        }
        budget_control = _codex_budget_control(target, worker_budget)
        if budget_control:
            payload["budget_control"] = budget_control
        return payload


class ClaudeCodeAdapter:
    name: AgentHostKind = "claude-code"
    capabilities = HostCapabilities(
        supports_bind_handle=True,
        supports_same_worker_continue=True,
        uses_background_workers=False,
    )

    def collect_observability(self, session: AgentSessionRecord) -> dict[str, Any]:
        return collect_metadata_observability(session)

    def build_launch_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        short_intent: str,
        one_paragraph_idea: str,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "tool": "Agent",
            "agent_type": worker_agent_type or "search-candidate-agent",
            "description": f"{candidate_id} {short_intent}",
            "background": False,
            "message": (
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                f"idea: {one_paragraph_idea}"
            ),
        }
        if worker_budget:
            payload["budget_control"] = {
                "mode": "host_turn_limit",
                "max_turns": worker_budget.get("max_turns"),
                "on_exceed": worker_budget.get("on_exceed", "interrupt"),
            }
        return payload

    def build_continue_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        external_id: str | None,
        task_name: str | None,
        short_intent: str,
        one_paragraph_idea: str,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        host_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = external_id or task_name
        if not target:
            raise UnsupportedHostCapability(
                "claude-code continuation requires a bound agent id or name"
            )
        return {
            "tool": "SendMessage",
            "agent": target,
            "message": (
                "continue_existing_agent_session=true; "
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                f"idea: {one_paragraph_idea}"
            ),
        }


class PiRpcAdapter:
    name: AgentHostKind = "pi-rpc"
    capabilities = HostCapabilities(
        supports_bind_handle=True,
        supports_same_worker_continue=True,
        uses_background_workers=False,
        continuation="native_session",
        supports_soft_closeout=True,
        supports_model_override=True,
        supports_reasoning_effort=True,
        supports_usage_metadata=True,
        supports_process_kill=True,
        pool=HostPoolContract(
            launch_mode="async",
            wait_mode="wait_any",
            continuation_mode="native_session",
            deadline_mode="worker_watchdog",
            recovery_mode="supervisor_persisted",
            completion_stage="candidate_ready",
            open_tool="pi_search_pool_open",
            wait_tool="pi_search_pool_wait_any",
            snapshot_tool="pi_search_pool_snapshot",
            continue_tool="pi_search_pool_continue",
            closeout_tool="pi_search_pool_close",
            interrupt_tool="pi_search_pool_close",
        ),
    )

    def collect_observability(self, session: AgentSessionRecord) -> dict[str, Any]:
        return collect_pi_observability(session)

    def _budget_control(
        self,
        worker_budget: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not worker_budget:
            return None
        max_runtime_seconds = worker_budget.get("max_runtime_seconds")
        budget_control: dict[str, Any] = {
            "mode": "pi_rpc_process_watchdog",
            "continuation": "native_session",
            "max_runtime_seconds": max_runtime_seconds,
            "on_exceed": worker_budget.get("on_exceed", "interrupt"),
        }
        if max_runtime_seconds is not None:
            budget_control["soft_closeout_seconds"] = _soft_closeout_seconds(
                int(max_runtime_seconds)
            )
        if worker_budget.get("max_turns") is not None:
            budget_control["max_turns_hint"] = worker_budget["max_turns"]
        return budget_control

    def _base_prompt(
        self,
        *,
        worker_prompt: str | None,
        agent_session_id: str,
        candidate_id: str,
        one_paragraph_idea: str,
        worker_budget: dict[str, Any] | None = None,
        resume: bool = False,
    ) -> str:
        header = (worker_prompt or "First call search_get_agent_context.").strip()
        labels = (
            f"agent_session_id={agent_session_id}; "
            f"candidate_id={candidate_id}; "
            f"assigned_worker_budget={worker_budget or 'host default'}; "
            f"idea: {one_paragraph_idea}"
        )
        if resume:
            labels = "continue_existing_agent_session=true; " + labels
            header += (
                "\n\nA new host dispatch starts with this launch message. Any "
                "deadline, closeout, or time-advisory message earlier in the "
                "native conversation belongs to a previous dispatch and is no "
                "longer active. Only warnings delivered after this launch apply."
            )
        return f"{header}\n\nLaunch labels: {labels}"

    def build_launch_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        short_intent: str,
        one_paragraph_idea: str,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": "pi_rpc_worker",
            "agent_session_id": agent_session_id,
            "candidate_id": candidate_id,
            "session_id": agent_session_id,
            "root": root or DEFAULT_RUNTIME_ROOT,
            "cwd": cwd or ".",
            "description": f"{candidate_id} {short_intent}",
            "continuation": "native_session",
            "session_persistence": "cross_process",
            "prompt": self._base_prompt(
                worker_prompt=worker_prompt,
                agent_session_id=agent_session_id,
                candidate_id=candidate_id,
                one_paragraph_idea=one_paragraph_idea,
                worker_budget=worker_budget,
            ),
        }
        if worker_agent_type:
            payload["worker_agent_type"] = worker_agent_type
        if worker_launch:
            if worker_launch.get("model") is not None:
                payload["model_pattern"] = worker_launch["model"]
            if worker_launch.get("reasoning_effort") is not None:
                payload["thinking_level"] = worker_launch["reasoning_effort"]
        budget_control = self._budget_control(worker_budget)
        if budget_control:
            payload["budget_control"] = budget_control
        return payload

    def build_continue_payload(
        self,
        *,
        worker_agent_type: str | None,
        candidate_id: str,
        agent_session_id: str,
        external_id: str | None,
        task_name: str | None,
        short_intent: str,
        one_paragraph_idea: str,
        root: str | None = None,
        cwd: str | None = None,
        worker_prompt: str | None = None,
        worker_budget: dict[str, Any] | None = None,
        worker_launch: dict[str, Any] | None = None,
        host_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = (external_id or "").strip()
        if not session_id:
            raise UnsupportedHostCapability(
                "pi-rpc continuation requires a bound native session id"
            )
        payload: dict[str, Any] = {
            "tool": "pi_rpc_worker",
            "agent_session_id": agent_session_id,
            "candidate_id": candidate_id,
            "session_id": session_id,
            "root": root or DEFAULT_RUNTIME_ROOT,
            "cwd": cwd or ".",
            "description": f"{candidate_id} {short_intent}",
            "continuation": "native_session",
            "session_persistence": "cross_process",
            "prompt": self._base_prompt(
                worker_prompt=worker_prompt,
                agent_session_id=agent_session_id,
                candidate_id=candidate_id,
                one_paragraph_idea=one_paragraph_idea,
                worker_budget=worker_budget,
                resume=True,
            ),
        }
        if worker_agent_type:
            payload["worker_agent_type"] = worker_agent_type
        if worker_launch:
            if worker_launch.get("model") is not None:
                payload["model_pattern"] = worker_launch["model"]
            if worker_launch.get("reasoning_effort") is not None:
                payload["thinking_level"] = worker_launch["reasoning_effort"]
        metrics = (host_metadata or {}).get("pi_metrics")
        if isinstance(metrics, dict):
            payload["metrics_baseline"] = {
                "last_entry_id": metrics.get("final_last_entry_id"),
                "entry_count": metrics.get("final_entry_count"),
                "usage_total": metrics.get("usage_total"),
                "duration_seconds": metrics.get("duration_seconds"),
                "started_at": metrics.get("started_at"),
            }
        budget_control = self._budget_control(worker_budget)
        if budget_control:
            payload["budget_control"] = budget_control
        return payload


_ADAPTERS: dict[AgentHostKind, AgentHostAdapter] = {
    "opencode": OpenCodeAdapter(),
    "codex": CodexAdapter(),
    "claude-code": ClaudeCodeAdapter(),
    "pi-rpc": PiRpcAdapter(),
}


def get_agent_host_adapter(host: AgentHostKind) -> AgentHostAdapter:
    return _ADAPTERS[host]
