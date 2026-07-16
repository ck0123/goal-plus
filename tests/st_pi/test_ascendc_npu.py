from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess

import pytest

from goal_plus.runtime import FileSearchRuntime


ROOT = Path(__file__).resolve().parents[2]
PROMPT = Path(__file__).with_name("ascendc_cannbench_gelu.md")
RUN_ENV = "GOAL_PLUS_RUN_ASCENDC_NPU_ST"
CANNBENCH_REPOSITORY = "https://gitcode.com/cann/cann-bench.git"
CANNBENCH_COMMIT = "da92996f420c59727c1769aecd30c7cd07549b31"
AKG_REPOSITORY = "https://gitcode.com/mindspore/akg.git"
AKG_COMMIT = "a2c1a23fd371e234b7e767247e8c4753462ecdca"
CANNBOT_REPOSITORY = "https://gitcode.com/cann/cannbot-skills.git"
CANNBOT_COMMIT = "d5ddcacc6e51eeaa8b52fa446c3b768c6813602e"
AKG_SKILLS_RELATIVE = Path(
    "akg_agents/python/akg_agents/op/resources/skills/ascendc"
)
NPU_ENV_SETUP = """if [[ -n \"${GOAL_PLUS_NPU_CONDA_SH:-}\" ]]; then
  source \"$GOAL_PLUS_NPU_CONDA_SH\"
fi
if [[ -n \"${GOAL_PLUS_NPU_ENV_SH:-}\" ]]; then
  source \"$GOAL_PLUS_NPU_ENV_SH\"
fi"""


def _pi_base_command(session_dir: Path) -> list[str]:
    command = [
        os.environ.get("ST_PI_BINARY", "pi"),
        "--approve",
        "--session-dir",
        str(session_dir),
        "--session-id",
        "st-pi-ascendc-cannbench-gelu",
    ]
    model = os.environ.get("ST_PI_MODEL")
    if model:
        command.extend(["--model", model])
    thinking = os.environ.get("ST_PI_THINKING")
    if thinking:
        command.extend(["--thinking", thinking])
    return command


def _git_head(repository: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_repository_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def _materialize_repository(
    target: Path,
    repository_url: str,
    revision: str,
    source: Path | None,
) -> Path:
    if source is None:
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--filter=blob:none",
                "--no-checkout",
                repository_url,
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        source_root = _git_repository_root(source)
        subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "init", "-q", str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "fetch",
                "--quiet",
                "--no-tags",
                "--depth=1",
                str(source_root),
                revision,
            ],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", repository_url],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", revision],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    fetch_head = target / ".git" / "FETCH_HEAD"
    if fetch_head.exists():
        fetch_head.unlink()
    assert _git_head(target) == revision
    return target


def _source_override(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).resolve() if value else None


def _materialize_dependencies(run_root: Path) -> tuple[Path, Path, Path]:
    dependencies = run_root / "dependencies"
    dependencies.mkdir(parents=True, exist_ok=True)
    cannbench = _materialize_repository(
        dependencies / "cann-bench",
        CANNBENCH_REPOSITORY,
        CANNBENCH_COMMIT,
        _source_override("CANNBENCH_ROOT"),
    )
    akg = _materialize_repository(
        dependencies / "akg",
        AKG_REPOSITORY,
        AKG_COMMIT,
        _source_override("AKG_ASCENDC_SKILLS_ROOT"),
    )
    cannbot = _materialize_repository(
        dependencies / "cannbot-skills",
        CANNBOT_REPOSITORY,
        CANNBOT_COMMIT,
        _source_override("CANNBOT_SKILLS_ROOT"),
    )
    return cannbench, akg / AKG_SKILLS_RELATIVE, cannbot


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _tool_calls(entries: list[dict], name: str) -> list[dict]:
    calls: list[dict] = []
    for entry in entries:
        message = entry.get("message") or {}
        if message.get("role") != "assistant":
            continue
        for item in message.get("content") or []:
            if item.get("type") == "toolCall" and item.get("name") == name:
                calls.append(item)
    return calls


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def _render_prompt() -> str:
    text = PROMPT.read_text(encoding="utf-8")
    replacements = {
        "{{PROJECT_ROOT}}": "$GOAL_PLUS_PROJECT_ROOT",
        "{{ST_PI_RUN_ROOT}}": "$GOAL_PLUS_ST_RUN_ROOT",
        "{{CANNBENCH_ROOT}}": "$GOAL_PLUS_CANNBENCH_ROOT",
        "{{AKG_ASCENDC_SKILLS_ROOT}}": "$GOAL_PLUS_AKG_ASCENDC_SKILLS_ROOT",
        "{{CANNBOT_SKILLS_ROOT}}": "$GOAL_PLUS_CANNBOT_SKILLS_ROOT",
        "{{NPU_ENV_SETUP}}": NPU_ENV_SETUP,
    }
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    assert "{{" not in text
    return text


def _redact_output(text: str, run_root: Path) -> str:
    replacements: dict[str, str] = {
        str(ROOT.parent): "$WORKSPACE_ROOT",
        str(run_root): "$GOAL_PLUS_ST_RUN_ROOT",
    }
    for variable in (
        "GOAL_PLUS_NPU_CONDA_SH",
        "GOAL_PLUS_NPU_ENV_SH",
        "CONDA_PREFIX",
        "ASCEND_HOME_PATH",
    ):
        value = os.environ.get(variable)
        if value:
            replacements[str(Path(value).expanduser())] = f"${variable}"
    for value, marker in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(value, marker)
    return text


def test_ascendc_prompt_uses_environment_paths() -> None:
    text = _render_prompt()
    assert str(ROOT) not in text
    assert "{{" not in text
    for variable in (
        "$GOAL_PLUS_PROJECT_ROOT",
        "$GOAL_PLUS_ST_RUN_ROOT",
        "$GOAL_PLUS_CANNBENCH_ROOT",
        "$GOAL_PLUS_AKG_ASCENDC_SKILLS_ROOT",
        "$GOAL_PLUS_CANNBOT_SKILLS_ROOT",
        "$GOAL_PLUS_NPU_ENV_SH",
    ):
        assert variable in text


def test_ascendc_launcher_uses_pinned_public_dependencies() -> None:
    text = (ROOT / "scripts/run_ascendc_cannbench_e2e.sh").read_text(
        encoding="utf-8"
    )
    assert "/home/" not in text
    for required in (
        CANNBENCH_REPOSITORY,
        CANNBENCH_COMMIT,
        AKG_REPOSITORY,
        AKG_COMMIT,
        CANNBOT_REPOSITORY,
        CANNBOT_COMMIT,
        "GOAL_PLUS_E2E_CACHE_DIR",
        "default_dependency_cache",
        "${TMPDIR:-/tmp}/goal-plus-ascendc-e2e-cache",
        "GOAL_PLUS_NPU_CONDA_SH",
        "GOAL_PLUS_NPU_ENV_SH",
        "source_private_environment",
        "mv -T",
        "run_redacted",
        "redact_stream",
        "cannbench_override=${CANNBENCH_ROOT:-}",
        "akg_skills_override=${AKG_ASCENDC_SKILLS_ROOT:-}",
        "cannbot_override=${CANNBOT_SKILLS_ROOT:-}",
    ):
        assert required in text


def test_materialize_repository_pins_revision_and_public_origin(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private-source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Goal Plus Test"],
        cwd=source,
        check=True,
    )
    nested = source / "nested"
    nested.mkdir()
    (nested / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)
    revision = _git_head(source)

    target = _materialize_repository(
        tmp_path / "run-dependency",
        "https://example.invalid/public.git",
        revision,
        nested,
    )
    assert _git_head(target) == revision
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert origin == "https://example.invalid/public.git"
    assert not (target / ".git" / "objects" / "info" / "alternates").exists()
    assert not (target / ".git" / "FETCH_HEAD").exists()
    assert str(source) not in (target / ".git" / "config").read_text(
        encoding="utf-8"
    )


@pytest.mark.st_pi
@pytest.mark.st_npu
@pytest.mark.skipif(
    os.environ.get(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run the real Pi AscendC NPU search",
)
def test_pi_goal_plus_ascendc_cannbench_gelu_end_to_end(
    st_pi_run_root: Path,
) -> None:
    search_root = st_pi_run_root / ".gp"
    session_dir = st_pi_run_root / "sessions"
    session_dir.mkdir(parents=True)
    cannbench, akg_skills, cannbot = _materialize_dependencies(st_pi_run_root)
    prompt = _render_prompt()
    for required in (
        cannbench / "tasks" / "level1" / "gelu" / "proto.yaml",
        cannbench / "tasks" / "level1" / "gelu" / "golden.py",
        cannbench / "tasks" / "level1" / "gelu" / "cases.yaml",
        akg_skills,
        cannbot / "LICENSE",
    ):
        assert required.exists(), required

    command = [*_pi_base_command(session_dir), "-p", f"/goal-plus {prompt}"]
    env = {
        **os.environ,
        "GOAL_PLUS_ROOT": str(search_root),
        "GOAL_PLUS_SOURCE_PATH": str(ROOT),
        "CANNBENCH_ROOT": str(cannbench),
        "AKG_ASCENDC_SKILLS_ROOT": str(akg_skills),
        "CANNBOT_SKILLS_ROOT": str(cannbot),
        "GOAL_PLUS_PROJECT_ROOT": str(ROOT),
        "GOAL_PLUS_ST_RUN_ROOT": str(st_pi_run_root),
        "GOAL_PLUS_CANNBENCH_ROOT": str(cannbench),
        "GOAL_PLUS_AKG_ASCENDC_SKILLS_ROOT": str(akg_skills),
        "GOAL_PLUS_CANNBOT_SKILLS_ROOT": str(cannbot),
    }
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=int(os.environ.get("ST_ASCENDC_NPU_TIMEOUT", "10800")),
        check=False,
    )
    output_log = st_pi_run_root / "pi-output.log"
    safe_stdout = _redact_output(result.stdout, st_pi_run_root)
    safe_stderr = _redact_output(result.stderr, st_pi_run_root)
    output_log.write_text(
        f"--- stdout ---\n{safe_stdout}\n--- stderr ---\n{safe_stderr}\n",
        encoding="utf-8",
    )
    assert result.returncode == 0, safe_stderr[-4000:] or safe_stdout[-4000:]

    goal_files = sorted((search_root / "goal-plus").glob("gp_*/goal.json"))
    assert len(goal_files) == 1, output_log
    goal = json.loads(goal_files[0].read_text(encoding="utf-8"))
    assert goal["status"] == "complete", output_log
    linked = goal.get("linked_search") or {}
    assert linked.get("run_id"), goal

    runtime = FileSearchRuntime(search_root)
    run = runtime._load_run(linked["run_id"])
    frozen = runtime._load_frozen_spec(run.frozen_spec_id)
    assert run.state == "promoted"
    assert run.candidates_total == 2
    assert run.candidates_evaluated == 2
    assert run.selected_candidate_id == linked["selected_candidate_id"]
    assert frozen.spec.budget.max_candidates == 2
    assert frozen.spec.budget.max_parallel == 2
    assert frozen.spec.strategy.worker_host == "pi-rpc"
    assert frozen.spec.strategy.worker_mode == "agent-session-pool"
    assert frozen.spec.promotion_verifiers

    plans = runtime._load_plans(run.run_id)
    assert len(plans) == 1
    assert plans[0].planned_k == 2
    assert plans[0].started_candidate_ids == ["c001", "c002"]

    records = runtime._load_candidate_records(run.run_id)
    assert [record.candidate_id for record in records] == ["c001", "c002"]
    required_evidence = {"passed_case_ids", "cases_sha256", "artifact_hash"}
    passing_records = [
        record
        for record in records
        if record.score_report is not None and record.score_report.process_passed
    ]
    assert passing_records
    for record in records:
        assert record.status == "evaluated"
        assert record.score_report is not None
        assert record.score_report.aggregate_score is not None
        assert math.isfinite(record.score_report.aggregate_score)
        if not record.score_report.process_passed:
            assert record.iterations
            continue
        metric_keys = set().union(
            *(
                _all_keys(verifier.metrics)
                for verifier in record.score_report.verifier_results
            )
        )
        assert required_evidence <= metric_keys

    selected = next(
        record for record in records if record.candidate_id == run.selected_candidate_id
    )
    assert selected.promotion_report is not None
    assert selected.promotion_report.promotion_passed is True
    assert selected.promotion_evidence is not None
    assert selected.promotion_evidence.passed is True
    assert selected.promotion_evidence.artifact_hash == run.selected_artifact_hash
    assert selected.promotion_evidence.git_head == run.selected_git_head

    sessions = runtime._load_agent_sessions(run.run_id)
    assert len(sessions) == 2
    assert {session.host for session in sessions} == {"pi-rpc"}
    assert {session.candidate_id for session in sessions} == {"c001", "c002"}
    for session in sessions:
        metadata = session.host_handle.metadata
        assert metadata.get("runner_failed") is not True
        assert metadata.get("continuation") == "state_redispatch"
        assert Path(metadata["event_log"]).is_file()

    source = Path(run.source_path)
    for relative in (
        "_task/operator_request.json",
        "_task/reference_manifest.json",
        "_task/target_platform.json",
        "_task/search_policy.json",
        "_task/baseline.json",
        "_task/verifier_readiness.json",
        "_oracle/cases.jsonl",
        "_oracle/tolerances.json",
        "_skills/manifest.json",
    ):
        assert source.joinpath(relative).is_file(), relative

    knowledge = json.loads(
        source.joinpath("_skills/manifest.json").read_text(encoding="utf-8")
    )
    knowledge_sources = {item["name"]: item for item in knowledge["sources"]}
    assert knowledge_sources["akg"]["commit"] == AKG_COMMIT
    assert knowledge_sources["akg"]["role"] == "primary"
    assert knowledge_sources["cannbot"]["commit"] == CANNBOT_COMMIT
    assert knowledge_sources["cannbot"]["role"] == "supplement"

    reference_text = source.joinpath("_task/reference_manifest.json").read_text(
        encoding="utf-8"
    )
    assert _git_head(cannbench) == CANNBENCH_COMMIT
    assert CANNBENCH_COMMIT in reference_text
    assert "tasks/level1/gelu" in reference_text
    assert "golden" in reference_text.lower()
    assert "cases" in reference_text.lower()
    assert "tolerance" in reference_text.lower()

    report_path = runtime._run_dir(run.run_id) / "report.md"
    patch_path = (
        runtime._run_dir(run.run_id)
        / "promotion"
        / f"{run.selected_candidate_id}.patch"
    )
    assert report_path.is_file()
    assert patch_path.is_file()
    assert Path(linked["report_path"]).resolve() == report_path.resolve()
    assert Path(linked["promotion_artifact_path"]).resolve() == patch_path.resolve()

    session_files = sorted(session_dir.glob("*.jsonl"))
    assert session_files
    entries = _read_jsonl(session_files[-1])
    batch_calls = _tool_calls(entries, "pi_search_run_batch")
    assert len(batch_calls) == 1
    batch_args = batch_calls[0].get("arguments") or {}
    assert batch_args["candidate_ids"] == ["c001", "c002"]
    assert batch_args["max_parallel"] == 2
