from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_opencode_config_registers_search_runtime_mcp() -> None:
    config = json.loads((ROOT / ".opencode" / "opencode.json").read_text(encoding="utf-8"))

    server = config["mcp"]["search-runtime"]
    assert server["type"] == "local"
    assert server["command"] == [
        "python",
        "-m",
        "agentic_any_search_mcp.server",
        "--root",
        ".search",
    ]
    assert server["environment"]["PYTHONPATH"] == "src"
    assert server["timeout"] >= 300000


def test_search_skill_is_slash_command_ready() -> None:
    skill = (ROOT / ".opencode" / "skills" / "search" / "SKILL.md").read_text(encoding="utf-8")

    assert "name: search" in skill
    assert "search-runtime_search_freeze_spec" in skill
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
    assert "no `timeout` parameter" in combined
    assert "MCP server subprocess" in combined


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
        '"find*delete*": deny',
        '"git clean*": deny',
        '"git reset*": deny',
        '"git restore*": deny',
        '"git checkout*": deny',
    ]:
        assert pattern in agent
