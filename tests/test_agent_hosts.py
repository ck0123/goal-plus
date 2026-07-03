from __future__ import annotations

import pytest

from agentic_any_search_mcp.agent_hosts import (
    UnsupportedHostCapability,
    get_agent_host_adapter,
    portable_strategy_mode,
)


def test_get_agent_host_adapter_returns_all_supported_hosts() -> None:
    assert get_agent_host_adapter("opencode").name == "opencode"
    assert get_agent_host_adapter("codex").name == "codex"
    assert get_agent_host_adapter("claude-code").name == "claude-code"


def test_portable_strategy_mode_accepts_default_and_random_aliases() -> None:
    assert portable_strategy_mode("agent_guided") is True
    assert portable_strategy_mode("agent") is True
    assert portable_strategy_mode("default") is True
    assert portable_strategy_mode("random") is True
    assert portable_strategy_mode("random-mode") is True
    assert portable_strategy_mode("openevolve") is False


def test_opencode_adapter_builds_existing_task_payload() -> None:
    adapter = get_agent_host_adapter("opencode")

    payload = adapter.build_launch_payload(
        worker_agent_type="AnySearchAgent",
        candidate_id="cand_0001",
        agent_session_id="agent_0001",
        short_intent="try a new branch",
        one_paragraph_idea="goal: try a new branch",
    )

    assert payload == {
        "subagent_type": "AnySearchAgent",
        "description": "cand_0001 try a new branch",
        "prompt": (
            "agent_session_id=agent_0001; "
            "candidate_id=cand_0001; "
            "idea: goal: try a new branch"
        ),
    }


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
    assert payload["agent_type"] == "any_search_agent"
    assert payload["fork_turns"] == "none"
    assert payload["task_name"] == "search_agent_0001"
    assert "agent_session_id=agent-0001" in payload["message"]


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
        "wait_timeout_ms": 600000,
        "on_exceed": "interrupt",
        "interrupt_target": "search_agent_0001",
        "max_turns_hint": 8,
    }


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
    assert payload["agent_type"] == "any-search-agent"
    assert payload["background"] is False
    assert "agent_session_id=agent_0001" in payload["message"]


def test_claude_adapter_builds_turn_budget_payload() -> None:
    adapter = get_agent_host_adapter("claude-code")

    payload = adapter.build_launch_payload(
        worker_agent_type="any-search-agent-deep",
        candidate_id="cand_0001",
        agent_session_id="agent_0001",
        short_intent="try",
        one_paragraph_idea="try",
        worker_budget={"max_turns": 16, "on_exceed": "interrupt"},
    )

    assert payload["agent_type"] == "any-search-agent-deep"
    assert payload["budget_control"] == {
        "mode": "host_turn_limit",
        "max_turns": 16,
        "on_exceed": "interrupt",
    }


def test_codex_continue_is_explicitly_unsupported() -> None:
    adapter = get_agent_host_adapter("codex")

    with pytest.raises(UnsupportedHostCapability, match="codex"):
        adapter.build_continue_payload(
            worker_agent_type="any_search_agent",
            candidate_id="cand_0001",
            agent_session_id="agent_0001",
            external_id=None,
            task_name="search_agent_0001",
            short_intent="continue",
            one_paragraph_idea="continue",
        )
