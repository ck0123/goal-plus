from __future__ import annotations

import json
from pathlib import Path

import pytest

from goal_plus.evidence_annotator import (
    EvidenceAnnotationResult,
    ToolViewOutput,
    drain_evidence_annotations,
)
from goal_plus.models import SearchSpec
from goal_plus.monitor import goal_plus_monitor_snapshot
from goal_plus.runtime import FileSearchRuntime
from goal_plus.shared_dir import SharedDirManager
from tests._runtime_helpers import make_project, spec_for


ROOT = Path(__file__).resolve().parents[1]


def _shared_run(
    tmp_path: Path,
    *,
    enabled: bool = True,
    max_files: int = 64,
    max_bytes: int = 2 * 1024 * 1024,
    max_tools: int = 16,
    max_path_entries: int = 512,
    max_depth: int = 8,
) -> tuple[FileSearchRuntime, str, list[tuple[str, str, Path]]]:
    project = make_project(tmp_path)
    (project / "evaluator.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "value = Path('initial_program.py').read_text().split('=', 1)[1].strip()\n"
        "print(json.dumps({'combined_score': float(value)}))\n",
        encoding="utf-8",
    )
    data = spec_for(project, max_parallel=2).model_dump(mode="json")
    data["workspace"] = {"backend": "git_worktree"}
    data["shared_dir"] = {
        "enabled": enabled,
        "max_tools_per_iteration": max_tools,
        "max_files_per_iteration": max_files,
        "max_path_entries_per_iteration": max_path_entries,
        "max_depth": max_depth,
        "max_bytes_per_iteration": max_bytes,
    }
    runtime = FileSearchRuntime(tmp_path / ".gp")
    frozen = runtime.freeze_spec(
        SearchSpec.model_validate(data),
        [project / "evaluator.py"],
    )
    run_id = runtime.create_run(frozen.frozen_spec_id)
    plan = runtime.plan_next(run_id, requested_k=2)
    tasks = runtime.start_batch(run_id, plan.plan_id)
    sessions = []
    for task in tasks:
        session = runtime.start_agent_session(run_id, task.candidate_id)
        sessions.append((task.candidate_id, session.agent_session_id, task.workspace))
    return runtime, run_id, sessions


def _write_tool(share_out: Path, name: str = "score-helper") -> None:
    tool = share_out / name
    tool.mkdir(parents=True)
    (tool / "manifest.json").write_text(
        json.dumps(
            {
                "name": "score-helper",
                "summary": "Parse the toy score from a source file.",
                "entrypoint": "helper.py:read_score",
            }
        ),
        encoding="utf-8",
    )
    (tool / "helper.py").write_text(
        "def read_score(text):\n    return float(text.split('=', 1)[1])\n",
        encoding="utf-8",
    )


def test_process_verifier_publishes_share_out_into_global_evidence(
    tmp_path: Path,
) -> None:
    runtime, run_id, candidates = _shared_run(tmp_path)
    producer, peer = candidates
    producer_context = runtime.get_agent_context(producer[1])
    share_out = Path(producer_context["candidate_task"]["share_out_dir"])
    shared_dir = Path(producer_context["candidate_task"]["shared_dir"])

    assert share_out == producer[2] / ".tmp" / "share-out"
    assert shared_dir == runtime._run_dir(run_id) / "shared"
    assert (shared_dir / "index.json").is_file()
    assert "manifest.json" in " ".join(
        producer_context["candidate_task"]["instructions"]
    )
    assert "shared_tools[*].tool_view" in " ".join(
        producer_context["candidate_task"]["instructions"]
    )

    _write_tool(share_out)
    (producer[2] / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Raise the score and export a reusable parser",
    )
    assert report.process_passed is True
    assert report.shared_tool_staged_entries == ["score-helper"]
    assert report.shared_tool_staged_file_count == 2
    assert report.shared_tool_publish_status == "published"
    assert report.shared_tool_consumed_entries == ["score-helper"]
    assert report.shared_tool_deduplicated_entries == []
    assert list(share_out.iterdir()) == []

    evidence = runtime.get_global_evidence(peer[1])
    assert len(evidence) == 1
    [tool] = evidence[0]["shared_tools"]
    assert tool["candidate_id"] == producer[0]
    assert tool["iteration"] == 1
    assert tool["source_commit"] == evidence[0]["commit"]
    assert tool["name"] == "score-helper"
    assert tool["entrypoint"] == "helper.py:read_score"
    assert tool["files"] == ["helper.py", "manifest.json"]
    snapshot = Path(tool["read_only_path"])
    assert snapshot.is_relative_to(shared_dir)
    assert (snapshot / "helper.py").is_file()

    index = json.loads((shared_dir / "index.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == 1
    assert [item["tool_id"] for item in index["tools"]] == [tool["tool_id"]]
    iteration = runtime.list_iterations(run_id, producer[0])[0]
    assert iteration["shared_tools"][0]["snapshot_hash"] == tool["snapshot_hash"]
    assert iteration["shared_tool_errors"] == []
    assert iteration["shared_tool_staged_entries"] == ["score-helper"]
    assert iteration["shared_tool_staged_file_count"] == 2
    assert iteration["shared_tool_publish_status"] == "published"
    assert iteration["changed_files"] == ["initial_program.py"]
    monitor = goal_plus_monitor_snapshot(runtime.root_dir, run_id=run_id)
    candidate_monitor = monitor["candidates"][producer[0]]
    assert candidate_monitor["shared_tools_published_total"] == 1
    assert candidate_monitor["shared_tool_staged_file_count_last"] == 2
    assert candidate_monitor["shared_tool_publish_status_last"] == "published"
    assert candidate_monitor["shared_tool_publish_status_counts"] == {
        "published": 1
    }


def test_view_agent_publishes_bound_tool_view_into_global_evidence(
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(share_out)
    (producer[2] / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Publish a parser for peer adoption",
    )
    before = runtime.get_global_evidence(peer[1])
    [published] = before[0]["shared_tools"]
    assert published["tool_view"] is None

    class ToolViewAnnotator:
        def annotate(self, context):
            [tool_input] = context["published_tools"]
            assert tool_input["tool_id"] == published["tool_id"]
            assert tool_input["snapshot_hash"] == published["snapshot_hash"]
            assert tool_input["source_commit"] == before[0]["commit"]
            assert tool_input["manifest"]["entrypoint"] == "helper.py:read_score"
            assert not any(
                item.get("path") == "manifest.json" and "text" in item
                for item in tool_input["snapshot_excerpts"]
            )
            assert any(
                item.get("path") == "helper.py" and "read_score" in item.get("text", "")
                for item in tool_input["snapshot_excerpts"]
            )
            return EvidenceAnnotationResult(
                description="发布了一个解析候选分数的辅助工具。",
                usage={"input_tokens": 10, "output_tokens": 5},
                tool_views=[
                    ToolViewOutput(
                        tool_id=tool_input["tool_id"],
                        summary="从候选源码文本中解析数值分数。",
                        capabilities=["解析等号右侧的浮点数"],
                        when_to_use="复用相同的文本分数格式时。",
                        entrypoint="hallucinated.py:wrong_entrypoint",
                        inputs=["包含 VALUE=<number> 的文本"],
                        outputs=["浮点数"],
                        dependencies=["Python 标准库"],
                        adoption_steps=["复制 helper.py 到 allowed_files", "重新运行 verifier"],
                        limitations=["只支持包含等号的文本"],
                    )
                ],
            )

    assert drain_evidence_annotations(
        runtime.root_dir,
        run_id,
        annotator=ToolViewAnnotator(),
    ) == 1
    after = runtime.get_global_evidence(peer[1])
    tool_view = after[0]["shared_tools"][0]["tool_view"]
    assert tool_view["tool_id"] == published["tool_id"]
    assert tool_view["snapshot_hash"] == published["snapshot_hash"]
    assert tool_view["source_commit"] == before[0]["commit"]
    assert tool_view["entrypoint"] == "helper.py:read_score"
    assert tool_view["capabilities"] == ["解析等号右侧的浮点数"]
    assert "不代表工具已被独立验证" in tool_view["evidence_scope"]


def test_view_agent_rejects_tool_identity_mismatch(tmp_path: Path) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(share_out)
    runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Publish a parser with immutable identity",
    )

    class WrongIdentityAnnotator:
        def annotate(self, _context):
            return EvidenceAnnotationResult(
                description="尝试描述一个错误工具身份。",
                usage={"input_tokens": 3},
                tool_views=[
                    ToolViewOutput(
                        tool_id="invented-tool-id",
                        summary="错误身份。",
                        capabilities=[],
                        when_to_use="不适用。",
                        entrypoint=None,
                        inputs=[],
                        outputs=[],
                        dependencies=[],
                        adoption_steps=[],
                        limitations=["身份不匹配"],
                    )
                ],
            )

    assert drain_evidence_annotations(
        runtime.root_dir,
        run_id,
        annotator=WrongIdentityAnnotator(),
    ) == 0
    annotation_task = runtime._load_evidence_annotation_task(
        run_id, producer[0], 1
    )
    assert annotation_task is not None
    assert annotation_task.state == "retry_wait"
    assert annotation_task.usage == {"input_tokens": 3}
    assert "identities do not match" in (annotation_task.last_error or "")
    assert runtime.get_global_evidence(peer[1])[0]["shared_tools"][0][
        "tool_view"
    ] is None


def test_view_agent_rejects_tampered_tool_snapshot(tmp_path: Path) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(share_out)
    runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Publish a hash-bound parser snapshot",
    )
    published = runtime.get_global_evidence(peer[1])[0]["shared_tools"][0]
    helper = Path(published["read_only_path"]) / "helper.py"
    helper.chmod(0o666)
    helper.write_text("def read_score(_text):\n    return 999.0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot integrity mismatch"):
        runtime._evidence_annotation_context(run_id, producer[0], 1)


def test_valid_non_improving_iteration_can_still_share_an_tool(tmp_path: Path) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    context = runtime.get_agent_context(producer[1])
    (producer[2] / "initial_program.py").write_text("VALUE = 2\n", encoding="utf-8")
    first = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Establish the candidate best before sharing",
    )
    assert first.disposition == "keep"

    _write_tool(Path(context["candidate_task"]["share_out_dir"]))
    (producer[2] / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")
    second = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Export a reusable parser from a valid lower-scoring attempt",
    )
    assert second.disposition == "discard"

    evidence = runtime.get_global_evidence(peer[1])
    assert evidence[-1]["disposition"] == "discard"
    assert len(evidence[-1]["shared_tools"]) == 1
    assert (producer[2] / "initial_program.py").read_text(encoding="utf-8") == (
        "VALUE = 2\n"
    )


def test_failed_process_verifier_does_not_publish_tools(tmp_path: Path) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    context = runtime.get_agent_context(producer[1])
    _write_tool(Path(context["candidate_task"]["share_out_dir"]))
    (producer[2] / "initial_program.py").write_text(
        "VALUE = not-a-number\n", encoding="utf-8"
    )

    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Try an invalid score while an tool is staged",
    )
    assert report.process_passed is False
    evidence = runtime.get_global_evidence(peer[1])
    assert evidence[0]["shared_tools"] == []
    assert json.loads(
        (runtime._run_dir(run_id) / "shared" / "index.json").read_text(
            encoding="utf-8"
        )
    )["tools"] == []
    [iteration] = runtime.list_iterations(run_id, producer[0])
    assert iteration["shared_tool_staged_entries"] == ["score-helper"]
    assert iteration["shared_tool_publish_status"] == "skipped_failed_verifier"


def test_parent_fallback_records_staging_without_publishing(tmp_path: Path) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    context = runtime.get_agent_context(producer[1])
    _write_tool(Path(context["candidate_task"]["share_out_dir"]))
    report = runtime.run_verifier(run_id, producer[0])

    assert report.process_passed is True
    [iteration] = runtime.list_iterations(run_id, producer[0])
    assert iteration["agent_session_id"] is None
    assert iteration["shared_tool_staged_entries"] == ["score-helper"]
    assert iteration["shared_tool_publish_status"] == (
        "skipped_unattributed_verifier"
    )
    assert runtime.get_global_evidence(peer[1]) == []
    assert json.loads(
        (runtime._run_dir(run_id) / "shared" / "index.json").read_text(
            encoding="utf-8"
        )
    )["tools"] == []
    assert not list(
        (runtime._run_dir(run_id) / "shared" / "tools").rglob("helper.py")
    )


def test_shared_tool_limits_are_advisory_to_valid_verifier_evidence(
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path, max_files=1)
    context = runtime.get_agent_context(producer[1])
    share_out = Path(context["candidate_task"]["share_out_dir"])
    (share_out / "one.py").write_text("ONE = 1\n", encoding="utf-8")
    (share_out / "two.py").write_text("TWO = 2\n", encoding="utf-8")
    (producer[2] / "initial_program.py").write_text("VALUE = 1\n", encoding="utf-8")

    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Publish only tools within the configured bound",
    )
    assert report.process_passed is True
    [evidence] = runtime.get_global_evidence(peer[1])
    assert len(evidence["shared_tools"]) == 1


def test_passing_settlement_consumes_staging_and_only_publishes_deltas(
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    context = runtime.get_agent_context(producer[1])
    share_out = Path(context["candidate_task"]["share_out_dir"])

    _write_tool(share_out)
    first = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Publish the first helper version",
    )
    assert first.shared_tool_publish_status == "published"
    assert list(share_out.iterdir()) == []

    _write_tool(share_out)
    unchanged = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Restage an unchanged helper",
    )
    assert unchanged.shared_tool_publish_status == "consumed_unchanged"
    assert unchanged.shared_tool_consumed_entries == ["score-helper"]
    assert unchanged.shared_tool_deduplicated_entries == ["score-helper"]
    assert list(share_out.iterdir()) == []

    _write_tool(share_out)
    (share_out / "score-helper" / "helper.py").write_text(
        "def read_score(text):\n    return float(text.rsplit('=', 1)[1])\n",
        encoding="utf-8",
    )
    changed = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Publish a modified helper version",
    )
    assert changed.shared_tool_publish_status == "published"

    evidence = runtime.get_global_evidence(peer[1])
    assert [len(item["shared_tools"]) for item in evidence] == [1, 0, 1]
    index = json.loads(
        (runtime._run_dir(run_id) / "shared" / "index.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(index["tools"]) == 2
    assert len({item["snapshot_hash"] for item in index["tools"]}) == 2


def test_identical_content_from_peers_reuses_one_physical_snapshot(
    tmp_path: Path,
) -> None:
    runtime, run_id, [first, second] = _shared_run(tmp_path)
    first_share = Path(
        runtime.get_agent_context(first[1])["candidate_task"]["share_out_dir"]
    )
    second_share = Path(
        runtime.get_agent_context(second[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(first_share)
    _write_tool(second_share)

    first_report = runtime.run_verifier(
        run_id,
        first[0],
        agent_session_id=first[1],
        hypothesis="Publish a reusable helper",
    )
    second_report = runtime.run_verifier(
        run_id,
        second[0],
        agent_session_id=second[1],
        hypothesis="Publish identical helper content from another lane",
    )
    assert first_report.shared_tool_publish_status == "published"
    assert second_report.shared_tool_publish_status == "published"

    iterations = [
        runtime.list_iterations(run_id, candidate_id)[0]
        for candidate_id in (first[0], second[0])
    ]
    paths = {
        item["shared_tools"][0]["read_only_path"] for item in iterations
    }
    assert len(paths) == 1


def test_depth_and_top_level_limits_stop_scanning_and_leave_staging_recoverable(
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, _peer] = _shared_run(
        tmp_path,
        max_tools=1,
        max_depth=1,
    )
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    (share_out / "one.py").write_text("ONE = 1\n", encoding="utf-8")
    (share_out / "two.py").write_text("TWO = 2\n", encoding="utf-8")
    over_tools = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Exercise the top-level tool bound",
    )
    assert over_tools.process_passed is True
    assert over_tools.shared_tool_publish_status == "snapshot_rejected"
    assert sorted(path.name for path in share_out.iterdir()) == ["one.py", "two.py"]
    assert "top-level tools" in over_tools.shared_tool_errors[0]

    (share_out / "one.py").unlink()
    (share_out / "two.py").unlink()
    nested = share_out / "nested" / "level-one" / "level-two"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("DEEP = 1\n", encoding="utf-8")
    over_depth = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Exercise the recursive depth bound",
    )
    assert over_depth.process_passed is True
    assert over_depth.shared_tool_publish_status == "snapshot_rejected"
    assert (nested / "deep.py").is_file()
    assert "maximum depth 1" in over_depth.shared_tool_errors[0]


def test_path_entry_limit_stops_recursive_scan_and_restores_staging(
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, _peer] = _shared_run(
        tmp_path,
        max_path_entries=2,
    )
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(share_out)

    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Exercise the filesystem entry traversal bound",
    )
    assert report.process_passed is True
    assert report.shared_tool_publish_status == "snapshot_rejected"
    assert "exceeds 2 filesystem entries" in report.shared_tool_errors[0]
    assert (share_out / "score-helper" / "helper.py").is_file()


def test_index_failure_restores_claimed_staging_without_publishing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, peer] = _shared_run(tmp_path)
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(share_out)

    def fail_index_update(self, tools):
        raise OSError("simulated index replace failure")

    monkeypatch.setattr(SharedDirManager, "_append_index", fail_index_update)
    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Keep staged source recoverable if index publication fails",
    )
    assert report.process_passed is True
    assert report.shared_tool_publish_status == "snapshot_error"
    assert (share_out / "score-helper" / "helper.py").is_file()
    assert runtime.get_global_evidence(peer[1])[0]["shared_tools"] == []
    assert json.loads(
        (runtime._run_dir(run_id) / "shared" / "index.json").read_text(
            encoding="utf-8"
        )
    )["tools"] == []


def test_failed_verifier_uses_only_cheap_staging_inventory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, _peer] = _shared_run(tmp_path)
    share_out = Path(
        runtime.get_agent_context(producer[1])["candidate_task"]["share_out_dir"]
    )
    _write_tool(share_out)
    (producer[2] / "initial_program.py").write_text(
        "VALUE = not-a-number\n", encoding="utf-8"
    )

    def fail_if_scanned(*args, **kwargs):
        raise AssertionError("recursive staging scan should not run")

    monkeypatch.setattr(SharedDirManager, "_tool_files", fail_if_scanned)
    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Fail validity before recursive shared-tool scanning",
    )
    assert report.process_passed is False
    assert report.shared_tool_publish_status == "skipped_failed_verifier"
    assert report.shared_tool_staged_entries == ["score-helper"]
    assert report.shared_tool_staged_file_count == 0
    assert (share_out / "score-helper" / "helper.py").is_file()


def test_staging_inspection_failure_is_advisory_to_verifier_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime, run_id, [producer, _peer] = _shared_run(tmp_path)

    def fail_inspection(self, share_out_dir, **kwargs):
        raise OSError("diagnostic read failed")

    monkeypatch.setattr(SharedDirManager, "inspect_staging", fail_inspection)

    report = runtime.run_verifier(
        run_id,
        producer[0],
        agent_session_id=producer[1],
        hypothesis="Settle valid verifier evidence despite diagnostic failure",
    )

    assert report.process_passed is True
    assert report.shared_tool_publish_status == "snapshot_error"
    assert report.shared_tool_errors is not None
    assert "diagnostic read failed" in report.shared_tool_errors[0]
    [iteration] = runtime.list_iterations(run_id, producer[0])
    assert iteration["shared_tool_publish_status"] == "snapshot_error"
    assert runtime.status(run_id).state != "failed"


def test_shared_dir_is_disabled_by_default(tmp_path: Path) -> None:
    runtime, run_id, [candidate, _peer] = _shared_run(tmp_path, enabled=False)
    context = runtime.get_agent_context(candidate[1])
    task = context["candidate_task"]
    assert task["share_out_dir"] is None
    assert task["shared_dir"] is None
    assert not (runtime._run_dir(run_id) / "shared").exists()
    assert "manifest.json" not in " ".join(task["instructions"])


def test_torch_cpu_shared_dir_validation_files_cover_publication_and_adoption() -> None:
    target = ROOT / "examples" / "model-optimize" / "torch-cpu-target"
    treatment = json.loads(
        (target / "shared-dir-treatment-search-spec.json").read_text(
            encoding="utf-8"
        )
    )
    proposals = json.loads(
        (target / "shared-dir-proposals.json").read_text(encoding="utf-8")
    )
    treatment_spec = SearchSpec.model_validate(treatment)
    assert treatment_spec.shared_dir.enabled is True
    assert treatment_spec.strategy.name == "agent_guided"
    assert treatment_spec.budget.max_parallel == 2
    assert treatment_spec.strategy.worker_budget is not None
    assert treatment_spec.strategy.worker_budget.min_verifier_runs == 1
    assert [item.role for item in treatment_spec.process_verifiers] == [
        "validity_gate",
        "ranking_signal",
    ]
    assert len(proposals) == 2
    assert [item["metadata"]["shared_dir_role"] for item in proposals] == [
        "publisher",
        "consumer",
    ]
    experiment = (target / "shared-dir-experiment.md").read_text(encoding="utf-8")
    assert "shared_tool_publish_status" in experiment
    assert "producer staging" in experiment
    assert "adopter" in experiment
    assert "Tool View" in experiment
