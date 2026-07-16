from __future__ import annotations

import pytest

from goal_plus.agent_hosts import (
    UnsupportedHostCapability,
    get_agent_host_adapter,
    portable_strategy_mode,
)


def test_get_agent_host_adapter_returns_all_supported_hosts() -> None:
    assert get_agent_host_adapter("opencode").name == "opencode"
    assert get_agent_host_adapter("codex").name == "codex"
    assert get_agent_host_adapter("claude-code").name == "claude-code"
    assert get_agent_host_adapter("pi-rpc").name == "pi-rpc"


def test_portable_strategy_mode_classifies_all_builtin_aliases() -> None:
    portable = (
        "agent_guided",
        "agent",
        "default",
        "random",
        "random-mode",
        "random_mode",
    )
    for name in portable:
        assert portable_strategy_mode(name) is True
    for name in ("independent_branches", "evolve", "openevolve", "mcts"):
        assert portable_strategy_mode(name) is False


def test_opencode_adapter_builds_existing_task_payload() -> None:
    adapter = get_agent_host_adapter("opencode")

    payload = adapter.build_launch_payload(
        worker_agent_type="SearchCandidateAgent",
        candidate_id="cand_0001",
        agent_session_id="agent_0001",
        short_intent="try a new branch",
        one_paragraph_idea="goal: try a new branch",
    )

    assert payload == {
        "subagent_type": "SearchCandidateAgent",
        "description": "cand_0001 try a new branch",
        "prompt": (
            "agent_session_id=agent_0001; "
            "candidate_id=cand_0001; "
            "idea: goal: try a new branch"
        ),
    }


@pytest.mark.codex
def test_codex_adapter_builds_foreground_spawn_payload() -> None:
    adapter = get_agent_host_adapter("codex")

    payload = adapter.build_launch_payload(
        worker_agent_type=None,
        candidate_id="cand-0001",
        agent_session_id="agent-0001",
        short_intent="try",
        one_paragraph_idea="try",
    )

    assert payload["tool"] == "spawn_agent"
    assert payload["agent_type"] == "default"
    assert payload["fork_turns"] == "none"
    assert payload["task_name"] == "search_agent_0001"
    assert "agent_session_id=agent-0001" in payload["message"]
    assert "assigned_worker_budget=host default" in payload["message"]


@pytest.mark.codex
def test_codex_launch_maps_candidate_contract_to_builtin_default_role() -> None:
    adapter = get_agent_host_adapter("codex")

    payload = adapter.build_launch_payload(
        worker_agent_type="search_candidate_agent",
        candidate_id="c001",
        agent_session_id="agent-0001",
        short_intent="try",
        one_paragraph_idea="try",
    )

    message = payload["message"]
    # Codex's built-in default role has no config layer, so it preserves the
    # inherited parent model. The project search role would reload config after
    # inheritance and can clear a runtime-only model before tier validation.
    assert payload["agent_type"] == "default"
    assert "candidate worker, not the search orchestrator" in message
    assert "search_get_agent_context" in message
    assert "search_run_verifier" in message
    assert "search_plan_next" in message
    assert "search_start_batch" in message
    assert "search_select" in message
    assert "search_report" in message
    assert "search_promote" in message
    assert "Do not call any `goal_plus_*` tool" in message


@pytest.mark.codex
def test_codex_launch_preserves_explicit_nondefault_agent_type() -> None:
    adapter = get_agent_host_adapter("codex")

    payload = adapter.build_launch_payload(
        worker_agent_type="search_candidate_agent_deep",
        candidate_id="c001",
        agent_session_id="agent-0001",
        short_intent="try",
        one_paragraph_idea="try",
    )

    assert payload["agent_type"] == "search_candidate_agent_deep"


@pytest.mark.codex
def test_codex_launch_and_continue_embed_full_worker_prompt() -> None:
    adapter = get_agent_host_adapter("codex")
    worker_prompt = "FULL WORKER CONTRACT: write .tmp/handoff.json before returning."

    launch = adapter.build_launch_payload(
        worker_agent_type="search_candidate_agent",
        candidate_id="c001",
        agent_session_id="agent-0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_prompt=worker_prompt,
    )
    continued = adapter.build_continue_payload(
        worker_agent_type="search_candidate_agent",
        candidate_id="c001",
        agent_session_id="agent-0001",
        external_id=None,
        task_name=launch["task_name"],
        short_intent="continue",
        one_paragraph_idea="continue",
        worker_prompt=worker_prompt,
    )

    assert worker_prompt in launch["message"]
    assert worker_prompt in continued["message"]
    assert "candidate worker, not the search orchestrator" in launch["message"]
    assert "candidate worker, not the search orchestrator" in continued["message"]


@pytest.mark.codex
def test_codex_adapter_maps_native_worker_launch_options() -> None:
    adapter = get_agent_host_adapter("codex")

    payload = adapter.build_launch_payload(
        worker_agent_type=None,
        candidate_id="cand-0001",
        agent_session_id="agent-0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_launch={
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
            "service_tier": "priority",
        },
    )

    assert payload["model"] == "gpt-5.6-terra"
    assert payload["reasoning_effort"] == "high"
    assert payload["service_tier"] == "priority"


@pytest.mark.codex
def test_codex_adapter_builds_watchdog_budget_payload() -> None:
    adapter = get_agent_host_adapter("codex")

    payload = adapter.build_launch_payload(
        worker_agent_type=None,
        candidate_id="cand-0001",
        agent_session_id="agent-0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_budget={
            "max_runtime_seconds": 600,
            "max_turns": 8,
            "on_exceed": "interrupt",
        },
    )

    assert payload["budget_control"] == {
        "mode": "parent_watchdog",
        "max_runtime_seconds": 600,
        "initial_wait_timeout_ms": 555000,
        "soft_closeout_seconds": 45,
        "closeout_tool": "send_message",
        "closeout_target": "search_agent_0001",
        "closeout_message": (
            "Worker deadline is approaching. Stop starting new work, run one final "
            "search_run_verifier if needed, write .tmp/handoff.json, and return a concise summary."
        ),
        "final_wait_timeout_ms": 45000,
        "on_exceed": "interrupt",
        "interrupt_tool": "interrupt_agent",
        "interrupt_target": "search_agent_0001",
        "max_turns_hint": 8,
    }


@pytest.mark.pi
def test_pi_rpc_adapter_builds_worker_payload() -> None:
    adapter = get_agent_host_adapter("pi-rpc")

    payload = adapter.build_launch_payload(
        worker_agent_type=None,
        candidate_id="c001",
        agent_session_id="agent_0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_budget={
            "max_runtime_seconds": 600,
            "max_turns": 8,
            "on_exceed": "interrupt",
        },
        root="/tmp/project/.search",
        cwd="/tmp/project/.search/runs/run_1/candidates/c001/workspace",
        worker_prompt="first call search_get_agent_context",
    )

    assert payload["tool"] == "pi_rpc_worker"
    assert payload["agent_session_id"] == "agent_0001"
    assert payload["candidate_id"] == "c001"
    assert payload["root"] == "/tmp/project/.search"
    assert payload["cwd"].endswith("/c001/workspace")
    assert "session_dir" not in payload
    assert payload["continuation"] == "state_redispatch"
    assert "search_get_agent_context" in payload["prompt"]
    assert "agent_session_id=agent_0001" in payload["prompt"]
    assert "assigned_worker_budget={'max_runtime_seconds': 600" in payload["prompt"]
    assert payload["budget_control"] == {
        "mode": "pi_rpc_process_watchdog",
        "continuation": "state_redispatch",
        "max_runtime_seconds": 600,
        "max_turns_hint": 8,
        "soft_closeout_seconds": 45,
        "on_exceed": "interrupt",
    }


@pytest.mark.pi
def test_pi_rpc_adapter_maps_native_worker_launch_options() -> None:
    adapter = get_agent_host_adapter("pi-rpc")

    payload = adapter.build_launch_payload(
        worker_agent_type=None,
        candidate_id="c001",
        agent_session_id="agent_0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_launch={
            "model": "openai-codex/gpt-5.6-sol",
            "reasoning_effort": "high",
        },
    )

    assert payload["model_pattern"] == "openai-codex/gpt-5.6-sol"
    assert payload["thinking_level"] == "high"


@pytest.mark.pi
def test_pi_rpc_adapter_rejects_same_session_continuation() -> None:
    adapter = get_agent_host_adapter("pi-rpc")

    with pytest.raises(UnsupportedHostCapability, match="search_redispatch_candidate"):
        adapter.build_continue_payload(
            worker_agent_type=None,
            candidate_id="c001",
            agent_session_id="agent_0001",
            external_id="agent_0001",
            task_name=None,
            short_intent="continue",
            one_paragraph_idea="continue from runtime context",
            root="/tmp/project/.search",
            cwd="/tmp/project/.search/runs/run_1/candidates/c001/workspace",
            worker_prompt="first call search_get_agent_context",
        )


def test_claude_adapter_builds_foreground_agent_payload() -> None:
    adapter = get_agent_host_adapter("claude-code")

    payload = adapter.build_launch_payload(
        worker_agent_type=None,
        candidate_id="cand_0001",
        agent_session_id="agent_0001",
        short_intent="try",
        one_paragraph_idea="try",
    )

    assert payload["tool"] == "Agent"
    assert payload["agent_type"] == "search-candidate-agent"
    assert payload["background"] is False
    assert "agent_session_id=agent_0001" in payload["message"]


def test_claude_adapter_builds_turn_budget_payload() -> None:
    adapter = get_agent_host_adapter("claude-code")

    payload = adapter.build_launch_payload(
        worker_agent_type="search-candidate-agent-deep",
        candidate_id="cand_0001",
        agent_session_id="agent_0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_budget={"max_turns": 16, "on_exceed": "interrupt"},
    )

    assert payload["agent_type"] == "search-candidate-agent-deep"
    assert payload["budget_control"] == {
        "mode": "host_turn_limit",
        "max_turns": 16,
        "on_exceed": "interrupt",
    }


@pytest.mark.codex
def test_codex_continue_uses_followup_task_with_watchdog() -> None:
    adapter = get_agent_host_adapter("codex")

    payload = adapter.build_continue_payload(
        worker_agent_type="search_candidate_agent",
        candidate_id="cand_0001",
        agent_session_id="agent_0001",
        external_id=None,
        task_name="search_agent_0001",
        short_intent="continue",
        one_paragraph_idea="continue",
        worker_budget={"max_runtime_seconds": 900, "on_exceed": "interrupt"},
    )

    assert payload["tool"] == "followup_task"
    assert payload["target"] == "search_agent_0001"
    assert "continue_existing_agent_session=true" in payload["message"]
    assert payload["budget_control"]["max_runtime_seconds"] == 900
    assert payload["budget_control"]["interrupt_target"] == "search_agent_0001"


@pytest.mark.codex
def test_codex_continue_requires_a_bound_native_handle() -> None:
    adapter = get_agent_host_adapter("codex")

    with pytest.raises(UnsupportedHostCapability, match="bound task name"):
        adapter.build_continue_payload(
            worker_agent_type="search_candidate_agent",
            candidate_id="cand_0001",
            agent_session_id="agent_0001",
            external_id=None,
            task_name=None,
            short_intent="continue",
            one_paragraph_idea="continue",
        )
