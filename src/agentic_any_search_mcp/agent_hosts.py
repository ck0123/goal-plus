from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agentic_any_search_mcp.models import AgentHostKind
from agentic_any_search_mcp.paths import DEFAULT_RUNTIME_ROOT


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
    supports_trace_export: bool
    uses_background_workers: bool = False
    continuation: str | None = None
    supports_soft_closeout: bool = False
    supports_model_override: bool = False
    supports_reasoning_effort: bool = False
    supports_service_tier: bool = False
    supports_usage_metadata: bool = False
    supports_process_kill: bool = False


class AgentHostAdapter(Protocol):
    name: AgentHostKind
    capabilities: HostCapabilities

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
    "reporting, promotion, and final audit are outside your role."
)


class OpenCodeAdapter:
    name: AgentHostKind = "opencode"
    capabilities = HostCapabilities(
        supports_bind_handle=True,
        supports_same_worker_continue=True,
        supports_trace_export=True,
    )

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
            "subagent_type": worker_agent_type or "AnySearchAgent",
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
    ) -> dict[str, Any]:
        if not external_id:
            raise UnsupportedHostCapability(
                "opencode continuation requires a bound OpenCode session id"
            )
        return {
            "task_id": external_id,
            "subagent_type": worker_agent_type or "AnySearchAgent",
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
        supports_same_worker_continue=False,
        supports_trace_export=False,
        supports_soft_closeout=True,
        supports_model_override=True,
        supports_reasoning_effort=True,
        supports_service_tier=True,
    )

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
        payload = {
            "tool": "spawn_agent",
            "task_name": task_name,
            "agent_type": worker_agent_type or "any_search_agent",
            "fork_turns": "none",
            "message": (
                f"{CODEX_WORKER_BOUNDARY}\n\n"
                f"agent_session_id={agent_session_id}; "
                f"candidate_id={candidate_id}; "
                f"idea: {one_paragraph_idea}"
            ),
        }
        if worker_launch:
            payload.update(
                {
                    key: value
                    for key, value in worker_launch.items()
                    if key in {"model", "reasoning_effort", "service_tier"}
                    and value is not None
                }
            )
        if worker_budget:
            max_runtime_seconds = worker_budget.get("max_runtime_seconds")
            budget_control: dict[str, Any] = {
                "mode": "parent_watchdog",
                "max_runtime_seconds": max_runtime_seconds,
                "on_exceed": worker_budget.get("on_exceed", "interrupt"),
                "interrupt_tool": "interrupt_agent",
                "interrupt_target": task_name,
            }
            max_runtime_ms = _budget_max_runtime_ms(worker_budget)
            if max_runtime_seconds is not None and max_runtime_ms is not None:
                soft_closeout_seconds = _soft_closeout_seconds(int(max_runtime_seconds))
                final_wait_timeout_ms = soft_closeout_seconds * 1000
                budget_control.update(
                    {
                        "initial_wait_timeout_ms": max_runtime_ms - final_wait_timeout_ms,
                        "soft_closeout_seconds": soft_closeout_seconds,
                        "closeout_tool": "send_message",
                        "closeout_target": task_name,
                        "closeout_message": CODEX_CLOSEOUT_MESSAGE,
                        "final_wait_timeout_ms": final_wait_timeout_ms,
                    }
                )
            if worker_budget.get("max_turns") is not None:
                budget_control["max_turns_hint"] = worker_budget["max_turns"]
            payload["budget_control"] = budget_control
        return payload

    def build_continue_payload(self, **_: Any) -> dict[str, Any]:
        raise UnsupportedHostCapability(
            "codex does not expose an equivalent same-worker continuation in this adapter"
        )


class ClaudeCodeAdapter:
    name: AgentHostKind = "claude-code"
    capabilities = HostCapabilities(
        supports_bind_handle=True,
        supports_same_worker_continue=True,
        supports_trace_export=False,
        uses_background_workers=False,
    )

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
            "agent_type": worker_agent_type or "any-search-agent",
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
        supports_same_worker_continue=False,
        supports_trace_export=False,
        uses_background_workers=False,
        continuation="state_redispatch",
        supports_soft_closeout=True,
        supports_model_override=True,
        supports_reasoning_effort=True,
        supports_usage_metadata=True,
        supports_process_kill=True,
    )

    def _budget_control(
        self,
        worker_budget: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not worker_budget:
            return None
        max_runtime_seconds = worker_budget.get("max_runtime_seconds")
        budget_control: dict[str, Any] = {
            "mode": "pi_rpc_process_watchdog",
            "continuation": "state_redispatch",
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
        resume: bool = False,
    ) -> str:
        header = (worker_prompt or "First call search_get_agent_context.").strip()
        labels = (
            f"agent_session_id={agent_session_id}; "
            f"candidate_id={candidate_id}; "
            f"idea: {one_paragraph_idea}"
        )
        if resume:
            labels = "continue_existing_agent_session=true; " + labels
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
            "continuation": "state_redispatch",
            "prompt": self._base_prompt(
                worker_prompt=worker_prompt,
                agent_session_id=agent_session_id,
                candidate_id=candidate_id,
                one_paragraph_idea=one_paragraph_idea,
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
    ) -> dict[str, Any]:
        raise UnsupportedHostCapability(
            "pi-rpc workers do not persist full session JSONL; use "
            "search_redispatch_candidate for state-level resume"
        )


_ADAPTERS: dict[AgentHostKind, AgentHostAdapter] = {
    "opencode": OpenCodeAdapter(),
    "codex": CodexAdapter(),
    "claude-code": ClaudeCodeAdapter(),
    "pi-rpc": PiRpcAdapter(),
}


def get_agent_host_adapter(host: AgentHostKind) -> AgentHostAdapter:
    return _ADAPTERS[host]
