from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.pi


def test_pi_assets_exist() -> None:
    for path in (
        ".pi/prompts/goal-plus.md",
        ".pi/skills/goal-plus/SKILL.md",
        ".pi/prompts/search-candidate-worker.md",
        ".pi/extensions/goal-plus.ts",
    ):
        assert (ROOT / path).exists(), f"missing {path}"

    skill_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".pi" / "skills").glob("*/SKILL.md")
    )
    assert skill_files == [".pi/skills/goal-plus/SKILL.md"]


def test_pyproject_exposes_pi_console_scripts() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10"' in text
    assert 'goal-plus-pi-tool = "goal_plus.pi_tool:main"' in text
    assert 'goal-plus-pi-worker = "goal_plus.pi_worker:main"' in text


def test_pi_goal_plus_prompt_starts_with_create_call() -> None:
    text = (ROOT / ".pi" / "prompts" / "goal-plus.md").read_text(encoding="utf-8")

    assert 'goal_plus_create(raw_goal="$ARGUMENTS")' in text
    assert 'worker_host: "pi-rpc"' in text
    assert 'worker_mode: "agent-session-pool"' in text
    assert 'numeric `spec.metric_name`' in text
    assert ".goal-plus-verifiers/" in text
    assert "`expected_outputs` lists artifact paths/globs only" in text
    assert "GOAL_PLUS_VERIFIER_TMPDIR" in text
    assert "fixed `/tmp`" in text
    assert "do not read or audit target files before `goal_plus_record_triage`" in text
    assert ".goal-plus-verifiers/" in text
    assert "`expected_outputs` lists" in text
    assert "{{input}}" not in text
    assert text.index("goal_plus_create") < text.index("Goal Plus")
    assert text.index("goal_plus_create") < text.index("goal_plus_record_triage")


def test_pi_goal_plus_skill_records_modes_and_gate() -> None:
    text = (ROOT / ".pi" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(text.split())

    assert "name: goal-plus" in text
    assert "Goal Mode" in text
    assert "Spec Discovery Mode" in text
    assert "Search Mode" in text
    assert "upgrade to Search Mode automatically" in text
    assert "Do not ask the user" in text
    assert "User hints are useful but optional" in text
    assert "optional audit evidence" in text
    assert "goal_plus_create" in text
    assert "goal_plus_gate" in text
    assert "goal_plus_monitor_snapshot" in text
    assert "primary read-only monitoring path" in text
    assert "goal-plus-pi-tool goal_plus_monitor_snapshot" in text
    assert "Do not use manual file tailing as the primary monitoring path" in text
    assert "goal_plus_link_search_run" in text
    assert 'worker_host: "pi-rpc"' in text
    assert 'worker_mode: "agent-session-pool"' in text
    assert "runtime default is OpenCode and is wrong for Pi" in text
    assert "Pi-supported strategy names" in text
    assert "`agent_guided`, `agent`, or `default`" in text
    assert "`random` or `random_mode`" in text
    assert "reuse an existing `frozen_spec_id`" in text
    assert "pi_search_run_batch" in text
    assert "max_parallel=<budget.max_parallel>" in text
    assert "GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER=1" in text
    assert "pi_search_run_candidate" in text
    assert "search_start_agent_session" in text
    assert "pi_rpc_run_worker" in text
    assert "search_bind_agent_handle" in text
    assert "final `search_run_verifier`" in text
    assert "search_select" in text
    assert "search_report" in text
    assert "search_promote" in text
    assert "state-level resume" in text
    assert "search_redispatch_candidate" in text
    assert "session_jsonl_restart" not in text
    assert "early `search_run_verifier`" in text
    assert "verification of the unmodified starting point" in text
    assert "verifier-recorded runtime iterations" in text
    assert "complete user-facing skill" in text
    assert "goal_plus_record_search_result" in text
    assert "final raw-goal audit" in text
    assert "/goal-plus-with-final-check" in text
    assert "goal_plus_update_goal" in text
    assert "treat the latest user message as authoritative" in normalized
    assert "scope, deliverables, or success criteria" in normalized
    assert "clarify before revising or resuming" in normalized
    assert "merely because the Goal Plus record is active" in normalized
    assert "goal_plus_prepare_final_check" in text
    assert "pi_goal_plus_run_final_check" in text
    assert "goal_plus_submit_final_check" in text
    assert "native Pi `/goal-plus` command creates" in text
    assert "queues" in text
    assert "the continuation prompt" in text
    assert "do not read or audit target files before `goal_plus_record_triage`" in text


def test_pi_goal_plus_skill_documents_whole_run_budget_planning() -> None:
    text = (ROOT / ".pi" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(text.split())

    assert "### Search Run Budget Planning" in text
    assert "total number of distinct candidate workspaces across all rounds" in normalized
    assert "`ceil(max_candidates / max_parallel)`" in text
    assert "recommend 4" in text
    assert "`max_candidates = rounds * max_parallel`" in text
    assert "set `max_candidates=15`" in text
    assert "default value 4 as the whole-run budget" in normalized
    assert "Do not call `search_select` while" in normalized


def test_pi_worker_prompt_requires_runtime_context_and_verifier() -> None:
    text = (ROOT / ".pi" / "prompts" / "search-candidate-worker.md").read_text(
        encoding="utf-8"
    )

    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text
    assert "complete candidate artifact early" in text
    assert "before any long optimization loop" in text
    assert ".tmp/handoff.json" in text
    assert "what_was_tried" in text
    assert "next_steps" in text
    assert "edit the allowed candidate artifact first" in text
    assert "verifying the unmodified starting point" in text
    assert "valid baseline iteration first" in text
    assert "verifier-recorded iterations" in text
    assert "Stop starting new optimization iterations" in text
    assert "final verifier" in text
    assert "time advisory after a tool result is informational" in text
    assert "trust direct reads and the runtime context" in text
    assert "workspace only" in text
    assert "runtime history" in text
    assert "do not rely on transcript" in text
    assert "VerifierWorkspaceSideEffect" in text
    assert "candidate_action=stop_and_report" in text
    assert "return immediately" in text


def test_pi_skill_documents_post_tool_time_advisory() -> None:
    text = (ROOT / ".pi" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(text.split())

    assert "advisory time estimate after completed worker tools" in normalized
    assert "last subagent verifier - first candidate session" in normalized
    assert "GOAL_PLUS_OUTER_DEADLINE_AT" in text
    assert "one informational `steer`" in text
    assert "does not stop the worker" in text


def test_pi_extension_registers_role_tools_gate_and_workspace_guard() -> None:
    text = (ROOT / ".pi" / "extensions" / "goal-plus.ts").read_text(
        encoding="utf-8"
    )

    assert "GOAL_PLUS_PI_ROLE" in text
    assert 'role === "main"' in text
    assert 'role === "worker"' in text
    assert "goal_plus_create" in text
    assert "search_get_agent_context" in text
    assert "search_run_verifier" in text
    assert "VerifierWorkspaceSideEffect" in text
    assert "GOAL_PLUS_VERIFIER_TMPDIR" in text
    assert "pi_rpc_run_worker" in text
    assert "pi_search_run_batch" in text
    assert "pi_search_run_candidate" in text
    assert 'pi.registerCommand("goal-plus"' in text
    assert 'pi.registerCommand("goal-plus-with-final-check"' in text
    assert "goal-plus-native-state" in text
    assert 'pi.on("session_start"' in text
    assert 'pi.on("before_agent_start"' in text
    assert 'pi.on("agent_end"' in text
    assert 'lastMessage?.role === "assistant"' in text
    assert 'lastMessage.stopReason === "error"' in text
    assert 'lastMessage.stopReason === "aborted"' in text
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
    assert "GOAL_PLUS_SOURCE_PATH" in text
    assert "sys.path.insert" in text
    assert "goal_plus.pi_tool" in text
    assert "goal_plus.pi_worker" in text
    assert "isPrintLikeInvocation" in text
    assert 'process.argv.includes("-p")' in text
    assert "if (!isPrintLikeInvocation)" in text
    assert 'mode !== "print"' in text
    assert "function canPersistGoalState" in text
    assert 'pi.on("input"' in text
    assert 'action: "transform"' in text
    assert "goalPlusRequestFromSlashInput" in text
    assert "createGoalPlusStart" in text
    assert "updateGoalPlusStart" in text
    assert "resumeGoalPlusStart" in text
    assert "/goal-plus resume" in text
    assert 'action: "resume"' in text
    assert "do not downgrade it to ordinary Goal Mode" in text
    assert "Treat the latest user message as authoritative" in text
    assert "scope, deliverables, or success criteria" in text
    assert "clarify ambiguous intent before resuming" in text
    assert "Never invent frozen_spec_id" in text
    assert '"goal_plus_update_goal", "goal_plus_submit_final_check"' in text
    assert "activateGoal(pi, result.details, startEntryCount, canPersistPiState)" in text
    assert 'if (name === "goal_plus_create" && canPersistPiState)' not in text
    assert "activateGoal(pi, status, startEntryCount, canPersistGoalState(ctx.mode))" in text
    assert "await ctx.waitForIdle()" not in text
    assert "do not read or audit target files before goal_plus_record_triage" in text
    assert "workspaceGuard" in text
    assert "MAIN_GATED_TOOLS" in text
    assert "pi_rpc_run_worker" in text
    assert "GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER" in text
    assert 'process.env.GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER === "1"' in text
    assert "role === \"main\" && exposeLowLevelWorker" in text
    assert '"pi_search_run_candidate"' in text
    assert '"pi_search_run_batch"' in text
    assert "block" in text
    assert 'role === "final-checker"' in text
    assert "registerPiFinalCheckTool" in text
    assert "Final-check reviewers are read-only" in text
    assert 'verdict: "interrupted"' in text
    assert "timed out before submitting a verdict" in text


def test_pi_extension_has_precise_tool_schemas_and_error_classification() -> None:
    text = (ROOT / ".pi" / "extensions" / "goal-plus.ts").read_text(
        encoding="utf-8"
    )

    assert "RuntimeToolSchemas" in text
    assert "parameters: toolParameters(name)" in text
    assert "parameters: JsonArgs" not in text
    assert "goal_plus_record_triage: Type.Object" in text
    assert "const SearchSpecSchema = Type.Object" in text
    assert "const SearchSpecDraftSchema = Type.Partial(SearchSpecSchema)" in text
    assert "spec: SearchSpecSchema" in text
    assert "search_spec: SearchSpecDraftSchema" in text
    assert "metric_direction: Type.Union" in text
    assert "process_verifiers: Type.Array(VerifierCommand" in text
    assert "worker_budget: Type.Optional(Type.Union" in text
    assert "const RuntimeToolDescriptions" in text
    assert "RuntimeToolDescriptions[name]" in text
    assert "Hard cap on total distinct candidate workspaces" in text
    assert "This is not a per-round limit" in text
    assert "Maximum candidates that search_plan_next may place in one planned batch" in text
    assert "default 4 is a batch-size request, not a whole-run budget" in text
    assert "planned_k is min(requested_k, remaining max_candidates, max_parallel)" in text
    freeze_schema = text.split("search_freeze_spec: Type.Object", 1)[1].split(
        "search_create: Type.Object", 1
    )[0]
    assert "spec: LooseObject" not in freeze_schema
    assert "goal_plus_monitor_snapshot: Type.Object" in text
    assert "goal_plus_update_goal: Type.Object" in text
    assert "goal_plus_prepare_final_check: Type.Object" in text
    assert "goal_plus_submit_final_check: Type.Object" in text
    assert "pi_goal_plus_run_final_check: Type.Object" in text
    assert "pi_search_run_candidate: Type.Object" in text
    assert "runtime_multiplier" in text
    assert "pi_search_run_batch: Type.Object" in text
    assert "redispatch: Type.Optional(Type.Boolean())" in text
    assert "candidate_ids: Type.Array(Type.String())" in text
    assert "max_parallel: Type.Optional(Type.Number())" in text
    candidate_schema = text.split("pi_search_run_candidate: Type.Object", 1)[1].split(
        "pi_search_run_batch: Type.Object", 1
    )[0]
    assert "model_pattern" not in candidate_schema
    assert "provider" not in candidate_schema

    main_tools = text.split("const mainTools = [", 1)[1].split("];", 1)[0]
    assert '"search_start_agent_session"' not in main_tools
    assert '"search_bind_agent_handle"' not in main_tools
    assert '"search_continue_agent_session"' not in main_tools
    assert "final_verify: Type.Optional(Type.Boolean())" in text
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
    assert "goal-plus-pi-worker" in combined
    assert "goal-plus-pi-tool" in combined
    assert "pi_search_run_batch" in combined
    assert "GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER=1" in combined
    assert "pi_search_run_candidate" in combined
    assert "goal_plus_monitor_snapshot" in combined
    assert "read-only" in combined
    assert "one user-facing `goal-plus` skill" in combined
    assert "does not expose a separate user-facing `search` skill" in combined
    assert ".pi/skills/goal-plus/" in combined
    assert "automatically start the agent session" in combined
    assert "run the Pi" in combined
    assert "RPC worker, bind the handle, and can run the final verifier" in combined
    assert "How Pi Differs From Other Hosts" in combined
    assert "Pi currently supports the portable builtin strategies only" in combined
    assert "pre-model `/goal-plus` creation" in combined
    assert "pi -p" in combined
    assert "--no-session" in combined
    assert "metadata-only" in combined
    assert "state-level redispatch" in combined
    assert "session_jsonl_restart" not in combined
    assert ".gp/host-logs/pi-rpc-" in combined
    assert "metadata-only event log" in combined
    assert "GOAL_PLUS_PI_RAW_LOG=1" in combined
    assert "native turn-level stop gate" in combined
    assert "Goal Plus stats" in combined
    assert "custom entry" in combined
    assert "does not trigger another assistant turn" in combined
    assert "no host process Stop hook" in combined


def test_pi_goal_plus_skill_documents_multiple_search_tasks_and_monitoring() -> None:
    text = (ROOT / ".pi" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    extension = (ROOT / ".pi" / "extensions" / "goal-plus.ts").read_text(
        encoding="utf-8"
    )

    assert "same `goal_plus_id`" in text
    assert "`search_tasks` is its" in text
    assert "append-only search-task history" in text
    assert "per-task planning/started round counts" in text
    assert "aggregate task" in text
    assert "search_tasks?: unknown[]" in extension
    assert "search_tasks_total?: number" in extension
    assert "status.search_tasks_total" in extension


def test_pi_goal_plus_reassesses_spec_after_real_result() -> None:
    skill = (ROOT / ".pi" / "skills" / "goal-plus" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    prompt = (ROOT / ".pi" / "prompts" / "goal-plus.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join([skill, prompt])
    flattened_skill = " ".join(skill.split())

    assert "After the first meaningful optimization result" in combined
    assert "large relative improvement" in combined
    assert "absolute target" in skill
    assert "acceptance threshold" in skill
    assert "success criterion" in flattened_skill
    assert "deeper structural optimization" in skill
    assert "`upgrade_spec`" in skill
    assert "`keep_spec_with_justification`" in skill
    assert "`revise_goal`" in skill
    assert "goal_plus_update_goal" in skill
    assert "not new runtime states" in skill
    assert "not a new runtime phase" in prompt
    assert "bootstrap" not in combined.lower()
