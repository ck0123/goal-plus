from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pi_assets_exist() -> None:
    for path in (
        ".pi/prompts/goal-plus.md",
        ".pi/skills/goal-plus/SKILL.md",
        ".pi/skills/search/SKILL.md",
        ".pi/prompts/any-search-worker.md",
        ".pi/extensions/search-runtime.ts",
    ):
        assert (ROOT / path).exists(), f"missing {path}"


def test_pyproject_exposes_pi_console_scripts() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10"' in text
    assert 'agentic-any-search-pi-tool = "agentic_any_search_mcp.pi_tool:main"' in text
    assert 'agentic-any-search-pi-worker = "agentic_any_search_mcp.pi_worker:main"' in text


def test_pi_goal_plus_prompt_starts_with_create_call() -> None:
    text = (ROOT / ".pi" / "prompts" / "goal-plus.md").read_text(encoding="utf-8")

    assert 'goal_plus_create(raw_goal="$ARGUMENTS")' in text
    assert "do not read or audit target files before `goal_plus_record_triage`" in text
    assert "{{input}}" not in text
    assert text.index("goal_plus_create") < text.index("Goal Plus")
    assert text.index("goal_plus_create") < text.index("goal_plus_record_triage")


def test_pi_goal_plus_skill_records_modes_and_gate() -> None:
    text = (ROOT / ".pi" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "name: goal-plus" in text
    assert "Goal Mode" in text
    assert "Spec Discovery Mode" in text
    assert "Search Mode" in text
    assert "goal_plus_create" in text
    assert "goal_plus_gate" in text
    assert "goal_plus_link_search_run" in text
    assert "goal_plus_record_search_result" in text
    assert "final raw-goal audit" in text
    assert "native Pi `/goal-plus` command creates" in text
    assert "queues the continuation prompt" in text
    assert "do not read or audit target files before `goal_plus_record_triage`" in text


def test_pi_search_skill_uses_rpc_worker_and_final_verifier() -> None:
    text = (ROOT / ".pi" / "skills" / "search" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "search_start_agent_session" in text
    assert "pi_rpc_run_worker" in text
    assert "search_bind_agent_handle" in text
    assert "final search_run_verifier" in text
    assert "search_select" in text
    assert "search_report" in text
    assert "search_promote" in text
    assert "session_jsonl_restart" in text
    assert "early `search_run_verifier`" in text
    assert "verification of the unmodified starting point" in text
    assert "verifier-recorded runtime iterations" in text


def test_pi_worker_prompt_requires_runtime_context_and_verifier() -> None:
    text = (ROOT / ".pi" / "prompts" / "any-search-worker.md").read_text(
        encoding="utf-8"
    )

    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text
    assert "complete candidate artifact early" in text
    assert "before any long optimization loop" in text
    assert "edit the allowed candidate artifact first" in text
    assert "verifying the unmodified starting point" in text
    assert "valid baseline iteration first" in text
    assert "verifier-recorded iterations" in text
    assert "trust direct reads and the runtime context" in text
    assert "workspace only" in text
    assert "runtime history" in text
    assert "do not rely on transcript" in text


def test_pi_extension_registers_role_tools_gate_and_workspace_guard() -> None:
    text = (ROOT / ".pi" / "extensions" / "search-runtime.ts").read_text(
        encoding="utf-8"
    )

    assert "AGENTIC_ANY_SEARCH_PI_ROLE" in text
    assert 'role === "main"' in text
    assert 'role === "worker"' in text
    assert "goal_plus_create" in text
    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text
    assert "pi_rpc_run_worker" in text
    assert 'pi.registerCommand("goal-plus"' in text
    assert "goal-plus-native-state" in text
    assert 'pi.on("session_start"' in text
    assert 'pi.on("before_agent_start"' in text
    assert 'pi.on("agent_end"' in text
    assert 'pi.on("tool_call"' in text
    assert 'goal_plus_gate' in text
    assert "tool_name" in text
    assert "goal-plus-stop-continuation" in text
    assert "goal-plus-stats" in text
    assert "registerEntryRenderer<GoalPlusStatsEntry>" in text
    assert "appendEntry<GoalPlusStatsEntry>" in text
    assert 'customType: "goal-plus-stats"' not in text
    assert "assistantMessages" in text
    assert "estimated_cost" in text
    assert "sendUserMessage" in text
    assert "AGENTIC_ANY_SEARCH_SOURCE_PATH" in text
    assert "sys.path.insert" in text
    assert "agentic_any_search_mcp.pi_tool" in text
    assert "agentic_any_search_mcp.pi_worker" in text
    assert "isPrintInvocation" in text
    assert 'process.argv.includes("-p")' in text
    assert 'ctx.mode === "print"' in text
    assert "buildGoalPlusCommandPrompt" in text
    assert "do not read or audit target files before goal_plus_record_triage" in text
    assert "workspaceGuard" in text
    assert "MAIN_GATED_TOOLS" in text
    assert "pi_rpc_run_worker" in text
    assert "block" in text


def test_pi_extension_has_precise_tool_schemas_and_error_classification() -> None:
    text = (ROOT / ".pi" / "extensions" / "search-runtime.ts").read_text(
        encoding="utf-8"
    )

    assert "RuntimeToolSchemas" in text
    assert "parameters: toolParameters(name)" in text
    assert "parameters: JsonArgs" not in text
    assert "goal_plus_record_triage: Type.Object" in text
    assert "triage: GoalPlusTriage" in text
    assert "is_optimization: Type.Boolean()" in text
    assert 'Type.Literal("spec_discovery")' in text
    assert "isEnvironmentFailure" in text
    assert "ModuleNotFoundError" in text
    assert "INSTALL_HINT" in text


def test_pi_docs_record_runner_logs_and_native_stop_gate() -> None:
    pi_doc = (ROOT / "docs" / "pi.md").read_text(encoding="utf-8")
    adapters = (ROOT / "docs" / "agent-host-adapters.md").read_text(encoding="utf-8")
    debug = (ROOT / "docs" / "debugging-runtime.md").read_text(encoding="utf-8")
    examples = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")

    combined = "\n".join([pi_doc, adapters, debug, examples])
    assert "worker_host=\"pi-rpc\"" in combined
    assert "agentic-any-search-pi-worker" in combined
    assert "agentic-any-search-pi-tool" in combined
    assert "How Pi Differs From Other Hosts" in combined
    assert "native `/goal-plus` pre-create" in combined
    assert "pi -p" in combined
    assert "session_jsonl_restart" in combined
    assert "not a live stdin continuation" in combined
    assert ".search/host-logs/pi-rpc-" in combined
    assert "native turn-level stop gate" in combined
    assert "Goal Plus stats" in combined
    assert "custom entry" in combined
    assert "does not trigger another assistant turn" in combined
    assert "no host process Stop hook" in combined
