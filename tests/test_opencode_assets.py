from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_opencode_config_registers_search_runtime_mcp() -> None:
    config = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))

    server = config["mcp"]["search-runtime"]
    assert server["type"] == "local"
    assert server["command"] == [
        "agentic-any-search-mcp",
        "--root",
        ".search",
    ]
    assert "environment" not in server
    assert server["timeout"] >= 300000
    assert server["enabled"] is True


def test_search_skill_is_slash_command_ready() -> None:
    skill = (ROOT / ".opencode" / "skills" / "search" / "SKILL.md").read_text(encoding="utf-8")

    assert "name: search" in skill
    assert "search-runtime_search_freeze_spec" in skill
    assert "search-runtime_search_bind_opencode_session" in skill
    assert "search-runtime_search_continue_agent_session" in skill
    assert "Do not start candidate execution before" in skill
    assert "k_module" in skill


def test_search_skill_requires_opencode_background_subagents() -> None:
    skill = (ROOT / ".opencode" / "skills" / "search" / "SKILL.md").read_text(encoding="utf-8")
    orchestrator = (ROOT / ".opencode" / "agents" / "search-orchestrator.md").read_text(
        encoding="utf-8"
    )

    combined = skill + "\n" + orchestrator
    assert "OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true" in combined
    assert "background: true" in combined
    assert "Task(task_id=launch.task_id" in combined
    assert "metadata.sessionId" in combined
    assert "no `timeout` parameter" in combined


def test_subagent_contract_derives_identifiers_from_context() -> None:
    skill = (ROOT / ".opencode" / "skills" / "search" / "SKILL.md").read_text(encoding="utf-8")
    agent = (ROOT / ".opencode" / "agents" / "AnySearchAgent.md").read_text(
        encoding="utf-8"
    )
    orchestrator = (ROOT / ".opencode" / "agents" / "search-orchestrator.md").read_text(
        encoding="utf-8"
    )

    combined = skill + "\n" + agent + "\n" + orchestrator
    assert "Do not hard-code `run_id`, `candidate_id`, or workspace paths" in combined
    assert "context.run_id" in combined
    assert "context.candidate_id" in combined
    assert "context.workspace" in combined
    assert "search_get_agent_context" in combined
    assert "The only required MCP calls" in combined


def test_k_module_example_spec_is_valid_json() -> None:
    spec_path = ROOT / "examples" / "k_module_search_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    assert spec["source_path"] == "tests/fixtures/k_module_problem"
    assert spec["metric_name"] == "combined_score"
    assert spec["edit_surface"]["deny"] == ["evaluator.py", "config.yaml"]


def test_search_skill_does_not_store_case_specific_references() -> None:
    assert not (ROOT / ".opencode" / "skills" / "search" / "references").exists()


def test_any_search_agent_denies_destructive_shell_commands() -> None:
    agent = (ROOT / ".opencode" / "agents" / "AnySearchAgent.md").read_text(
        encoding="utf-8"
    )

    assert "bash:" in agent
    for pattern in [
        '"rm*": deny',
        '"mv*": deny',
        '"rmdir*": deny',
        '"unlink*": deny',
        '"trash*": deny',
        '"find*delete*": deny',
    ]:
        assert pattern in agent
    for pattern in [
        '"git reset*": deny',
        '"git restore*": deny',
        '"git checkout*": deny',
        '"git clean*": deny',
    ]:
        assert pattern not in agent


def test_any_search_agent_documents_autoresearch_loop() -> None:
    agent = (ROOT / ".opencode" / "agents" / "AnySearchAgent.md").read_text(
        encoding="utf-8"
    )

    assert "## Iteration Loop" in agent
    assert "git init" in agent
    assert "search_run_verifier" in agent
    assert "results.tsv" in agent
    assert "agent_session_id" in agent


@pytest.mark.parametrize(
    "agent_file,expected_steps",
    [
        ("AnySearchAgentFlash.md", 15),
        ("AnySearchAgent.md", 50),
        ("AnySearchAgentDeep.md", 100),
        ("AnySearchAgentExtraDeep.md", 150),
    ],
)
def test_any_search_agent_tier_has_expected_step_cap(
    agent_file: str, expected_steps: int
) -> None:
    text = (ROOT / ".opencode" / "agents" / agent_file).read_text(encoding="utf-8")
    assert f"steps: {expected_steps}" in text
    assert "mode: subagent" in text


@pytest.mark.parametrize(
    "relative_path",
    [
        ".opencode/skills/search/SKILL.md",
        ".opencode/agents/AnySearchAgent.md",
        ".opencode/agents/AnySearchAgentDeep.md",
        ".opencode/agents/AnySearchAgentExtraDeep.md",
        ".opencode/agents/AnySearchAgentFlash.md",
        ".opencode/agents/search-orchestrator.md",
        "docs/flow-view.md",
        "docs/design.md",
        "docs/opencode.md",
        "docs/toy-example.md",
        "docs/debugging-runtime.md",
    ],
)
def test_deleted_lifecycle_apis_are_absent_from_opencode_assets(
    relative_path: str,
) -> None:
    """The old lifecycle/observation/sqlite APIs must not appear anywhere
    an agent could rediscover them. Only the plan file and this test may
    mention them."""
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    forbidden = [
        "search_wait_agent_events",
        "search_finish_agent_session",
        "search_update_agent_status",
        "search_list_agent_status",
        "search_abort_agent_session",
        "search_abort_all_agent_sessions",
        "search_publish_observation",
        "search_list_observations",
        "search_submit_candidate",
        "search_next_batch",
        "--opencode-db",
        "opencode_db_path",
        "sync_host_agent_sessions",
        "host sync",
        "sqlite",
    ]
    for token in forbidden:
        assert token not in text, f"{relative_path} mentions deleted API: {token}"


def test_subagent_only_two_mcp_calls_documented() -> None:
    """The subagent prompt contract must restrict the subagent to the two
    MCP calls it is allowed to make."""
    agent = (ROOT / ".opencode" / "agents" / "AnySearchAgent.md").read_text(encoding="utf-8")
    assert "search_get_agent_context" in agent
    assert "search_run_verifier" in agent
