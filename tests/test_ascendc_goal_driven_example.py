from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "ascendc-direct-search"
MATERIALIZER = EXAMPLE / "materialize_knowledge.py"
PINNED_AKG_COMMIT = "a2c1a23fd371e234b7e767247e8c4753462ecdca"
PINNED_CANNBOT_COMMIT = "d5ddcacc6e51eeaa8b52fa446c3b768c6813602e"


def _load_materializer():
    spec = importlib.util.spec_from_file_location(
        "ascendc_knowledge_materializer",
        MATERIALIZER,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _commit_fixture(repository: Path) -> str:
    _write(
        repository / ".fixture",
        "fixture\n",
    )
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=goal-plus-test",
        "-c",
        "user.email=goal-plus-test@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return _git(repository, "rev-parse", "HEAD")


def _fake_knowledge_repositories(
    tmp_path: Path,
) -> tuple[Path, str, Path, str]:
    akg = tmp_path / "akg"
    akg.mkdir()
    _git(akg, "init", "-q")
    _write(akg / "LICENSE", "Apache License 2.0\n")
    _write(
        akg / "skills" / "ascendc" / "fundamentals" / "demo" / "SKILL.md",
        """---
name: source-guide
---
# Pinned Agent Plugin Guide

Use `/ascendc-docs-search` skill and run `pip install bad-package`.
Use ascendc-docs-search 技能 for another lookup.
rm -rf $HOME/global-cache

[Selected detail](references/detail.md)
[Singular reference](reference/perf.md)
[Not bundled](missing.md)
Read `references/detail.md` and `missing-guide.md`.

## Agent 使用指南

调试计数器必须达到 7 次并形成总结。

### 检查清单

- [ ] 尝试次数 < 7
""",
    )
    _write(
        akg
        / "skills"
        / "ascendc"
        / "fundamentals"
        / "demo"
        / "references"
        / "detail.md",
        "# Detail\n\nPinned implementation facts.\n",
    )
    _write(
        akg
        / "skills"
        / "ascendc"
        / "fundamentals"
        / "demo"
        / "reference"
        / "perf.md",
        "# Performance\n\nPinned performance facts.\n",
    )
    _write(
        akg
        / "skills"
        / "ascendc"
        / "fundamentals"
        / "demo"
        / "scripts"
        / "install.md",
        "# This executable workflow must not be selected.\n",
    )
    akg_commit = _commit_fixture(akg)

    cannbot = tmp_path / "cannbot-skills"
    cannbot.mkdir()
    _git(cannbot, "init", "-q")
    _write(
        cannbot / "LICENSE",
        "CANN Open Software License Agreement Version 2.0\n",
    )
    _write(
        cannbot / "docs" / "matmul.md",
        "# Matmul supplement\n\nPinned matmul facts.\n",
    )
    cannbot_commit = _commit_fixture(cannbot)
    return akg, akg_commit, cannbot, cannbot_commit


def _selection(path: Path, akg_commit: str, cannbot_commit: str) -> Path:
    value = {
        "schema_version": 2,
        "name": "test-knowledge",
        "sources": [
            {
                "name": "akg",
                "role": "primary",
                "kind": "skill_tree",
                "repository": (
                    "https://declared:secret@example.invalid/akg.git"
                    "?token=hidden#fragment"
                ),
                "commit": akg_commit,
                "license": {"name": "Apache License 2.0", "path": "LICENSE"},
                "include_root": "skills/ascendc",
                "output_root": "akg",
            },
            {
                "name": "cannbot",
                "role": "supplement",
                "kind": "files",
                "repository": (
                    "https://declared:secret@example.invalid/cannbot-skills.git"
                    "?token=hidden#fragment"
                ),
                "commit": cannbot_commit,
                "license": {
                    "name": "CANN Open Software License Agreement Version 2.0",
                    "path": "LICENSE",
                },
                "output_root": "cannbot",
                "files": [
                    {
                        "category": "matmul_api",
                        "source": "docs/matmul.md",
                        "output": "api/matmul.md",
                        "transform": "sanitize_markdown",
                    }
                ],
            },
        ],
    }
    _write(path, json.dumps(value))
    return path


def test_materializer_exports_pinned_sanitized_git_objects(tmp_path: Path) -> None:
    module = _load_materializer()
    akg, akg_commit, cannbot, cannbot_commit = _fake_knowledge_repositories(
        tmp_path
    )
    selection = _selection(
        tmp_path / "selection.json", akg_commit, cannbot_commit
    )
    _git(
        akg,
        "remote",
        "add",
        "origin",
        "https://observed:secret@example.invalid/akg.git"
        "?token=hidden#fragment",
    )
    _git(
        cannbot,
        "remote",
        "add",
        "origin",
        "https://observed:secret@example.invalid/cannbot-skills.git"
        "?token=hidden#fragment",
    )

    # Export must read the selected commit, never this dirty working-tree file.
    _write(
        akg
        / "skills"
        / "ascendc"
        / "fundamentals"
        / "demo"
        / "references"
        / "detail.md",
        "# DIRTY WORKTREE CONTENT\n",
    )
    _write(cannbot / "docs" / "matmul.md", "# DIRTY CANNBOT CONTENT\n")
    output = tmp_path / "workspace" / "_skills"
    assert (
        module.main(
            [
                "--akg-root",
                str(akg / "skills" / "ascendc"),
                "--cannbot-skills-root",
                str(cannbot),
                "--selection",
                str(selection),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )

    detail = (
        output
        / "files"
        / "akg"
        / "fundamentals"
        / "demo"
        / "references"
        / "detail.md"
    ).read_text(encoding="utf-8")
    entry = (
        output
        / "files"
        / "akg"
        / "fundamentals"
        / "demo"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    perf = (
        output
        / "files"
        / "akg"
        / "fundamentals"
        / "demo"
        / "reference"
        / "perf.md"
    ).read_text(encoding="utf-8")
    matmul = (output / "files" / "cannbot" / "api" / "matmul.md").read_text(
        encoding="utf-8"
    )
    assert "Pinned implementation facts" in detail
    assert "Pinned performance facts" in perf
    assert "Pinned matmul facts" in matmul
    assert "DIRTY WORKTREE" not in detail
    assert "DIRTY CANNBOT" not in matmul
    for forbidden in (
        "Agent",
        "Plugin",
        "pip install",
        "rm -",
        "/ascendc-",
        "ascendc-docs-search",
        "技能",
        "调试计数器",
        "形成总结",
        "检查清单",
    ):
        assert forbidden not in entry
    assert "[Selected detail](references/detail.md)" in entry
    assert "[Singular reference](reference/perf.md)" in entry
    assert "[references/detail.md](references/detail.md)" in entry
    assert "missing.md" not in entry
    assert "missing-guide.md" not in entry

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["sources"] == [
        {
            "commit": akg_commit,
            "export_method": "git_object_database",
            "files": 3,
            "kind": "skill_tree",
            "name": "akg",
            "observed_origin": "https://example.invalid/akg.git",
            "repository": "https://example.invalid/akg.git",
            "requested_ref": akg_commit,
            "role": "primary",
        },
        {
            "commit": cannbot_commit,
            "export_method": "git_object_database",
            "files": 1,
            "kind": "files",
            "name": "cannbot",
            "observed_origin": "https://example.invalid/cannbot-skills.git",
            "repository": "https://example.invalid/cannbot-skills.git",
            "requested_ref": cannbot_commit,
            "role": "supplement",
        },
    ]
    assert all("checkout_path" not in source for source in manifest["sources"])
    assert manifest["selection"]["sha256"] == hashlib.sha256(
        selection.read_bytes()
    ).hexdigest()
    assert manifest["materializer"] == {
        "name": "materialize_knowledge.py",
        "sha256": hashlib.sha256(MATERIALIZER.read_bytes()).hexdigest(),
    }
    assert len(manifest["files"]) == 4
    first = manifest["files"][0]
    assert first["source_git_blob_sha1"] == _git(
        akg,
        "rev-parse",
        f"{akg_commit}:skills/ascendc/fundamentals/demo/SKILL.md",
    )
    assert first["rendered_sha256"] == hashlib.sha256(
        entry.encode("utf-8")
    ).hexdigest()
    assert first["derivation_audit"]["removed_orchestration_lines"]
    assert (output / "LICENSE").read_text(encoding="utf-8").startswith("Apache")
    assert (output / "licenses" / "akg.txt").is_file()
    assert (output / "licenses" / "cannbot.txt").is_file()
    assert {license_["source_name"] for license_ in manifest["licenses"]} == {
        "akg",
        "cannbot",
    }
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert akg_commit in readme
    assert cannbot_commit in readme
    assert "_skills/files/akg/fundamentals/demo/SKILL.md" in readme
    assert "_skills/files/cannbot/api/matmul.md" in readme

    with pytest.raises(ValueError, match="knowledge ref must be a full commit id"):
        module.main(
            [
                "--akg-root",
                str(akg),
                "--cannbot-skills-root",
                str(cannbot),
                "--source-ref",
                "akg=HEAD",
                "--selection",
                str(selection),
                "--output-dir",
                str(tmp_path / "other-workspace" / "_skills"),
            ]
        )


def test_materializer_selection_rejects_workflow_content(tmp_path: Path) -> None:
    module = _load_materializer()
    selection = {
        "schema_version": 2,
        "name": "unsafe",
        "sources": [
            {
                "name": "cannbot",
                "role": "supplement",
                "kind": "files",
                "repository": "https://example.invalid/cannbot-skills.git",
                "commit": "a" * 40,
                "license": {"name": "test", "path": "LICENSE"},
                "output_root": "cannbot",
                "files": [
                    {
                        "category": "api",
                        "source": "ops/example/scripts/install.md",
                        "output": "api/install.md",
                        "transform": "sanitize_markdown",
                    }
                ],
            }
        ],
    }
    path = tmp_path / "selection.json"
    _write(path, json.dumps(selection))

    with pytest.raises(ValueError, match="executable/workflow content"):
        module._load_selection(path)


def test_ascendc_example_has_only_goal_driven_assets() -> None:
    for legacy_path in (
        "prepare_workspace.py",
        "skill_profile.json",
        "verifier",
        "template/run.sh",
    ):
        assert not (EXAMPLE / legacy_path).exists(), legacy_path

    assert MATERIALIZER.is_file()
    assert (EXAMPLE / "knowledge.sources.json").is_file()
    assert not (EXAMPLE / "knowledge").exists()

    selection = json.loads(
        (EXAMPLE / "knowledge.sources.json").read_text(encoding="utf-8")
    )
    template_source = json.loads(
        (EXAMPLE / "template" / "SOURCE.json").read_text(encoding="utf-8")
    )
    assert selection["schema_version"] == 2
    sources = {source["name"]: source for source in selection["sources"]}
    assert set(sources) == {"akg", "cannbot"}
    assert sources["akg"]["commit"] == PINNED_AKG_COMMIT
    assert sources["akg"]["role"] == "primary"
    assert sources["akg"]["kind"] == "skill_tree"
    assert sources["akg"]["include_root"].endswith(
        "op/resources/skills/ascendc"
    )
    assert sources["cannbot"]["commit"] == PINNED_CANNBOT_COMMIT
    assert sources["cannbot"]["role"] == "supplement"
    assert sources["cannbot"]["kind"] == "files"
    assert template_source["source_commit"] == PINNED_CANNBOT_COMMIT
    supplements = sources["cannbot"]["files"]
    assert len({entry["source"] for entry in supplements}) == len(supplements)
    assert len({entry["output"] for entry in supplements}) == len(supplements)
    assert {entry["category"] for entry in supplements} == {
        "architecture",
        "attention",
        "conversion",
        "cube_vector",
        "matmul_api",
        "matmul_performance",
        "matmul_tiling",
        "simt",
        "sort",
    }
    selected_paths = {entry["source"] for entry in supplements}
    for required in (
        "ops/ascendc-api-best-practices/references/api-matmul.md",
        "ops/ascendc-tiling-design/references/matmul/patterns.md",
        "ops/ascendc-direct-invoke-template/references/matmul_fusion_guide.md",
        "ops/ascendc-blaze-best-practice/SKILL.md",
        "ops/ascendc-simt-best-practices/SKILL.md",
        "ops/ascendc-simt-tiling-design/references/guide.md",
        "ops/ascendc-tiling-design/references/flashattention/design.md",
        "ops/ascendc-tiling-design/references/sort/patterns.md",
    ):
        assert required in selected_paths
    forbidden_parts = {"agents", "plugins", "hooks", "scripts", ".github"}
    for entry in supplements:
        assert not forbidden_parts.intersection(
            part.lower() for part in Path(entry["source"]).parts
        )
        assert entry["transform"] == "sanitize_markdown"


def test_ascendc_request_schema_closes_direct_invoke_v1_contract() -> None:
    schema = json.loads((EXAMPLE / "request.schema.json").read_text(encoding="utf-8"))

    assert {"case_policy", "ranking_policy"}.issubset(schema["required"])
    operator = schema["properties"]["operator"]
    assert operator["properties"]["invocation_mode"]["const"] == "direct_invoke"
    inputs = schema["properties"]["inputs"]
    assert inputs["prefixItems"] == [{"$ref": "#/$defs/primary_tensor"}]
    primary = schema["$defs"]["primary_tensor"]["allOf"][1]
    assert primary["properties"]["kind"]["const"] == "tensor"
    assert primary["properties"]["optional"]["const"] is False
    assert {"optional", "dtypes", "shape"}.issubset(primary["required"])
    outputs = schema["properties"]["outputs"]
    assert outputs["minItems"] == outputs["maxItems"] == 1
    case_policy = schema["properties"]["case_policy"]
    assert case_policy["additionalProperties"] is False
    assert case_policy["properties"]["correctness_requirement"]["const"] == "all_cases_pass"
    ranking_policy = schema["properties"]["ranking_policy"]
    assert ranking_policy["properties"]["source"]["enum"] == [
        "user",
        "reference",
        "default",
    ]
    reference_roles = schema["$defs"]["reference"]["properties"]["roles"]["items"]["enum"]
    assert "scoring" in reference_roles


def test_all_goal_plus_hosts_route_ascendc_to_dynamic_spec_discovery() -> None:
    skill_paths = (
        ".codex/skills/goal-plus/SKILL.md",
        ".opencode/skills/goal-plus/SKILL.md",
        ".claude/skills/goal-plus/SKILL.md",
        ".pi/skills/goal-plus/SKILL.md",
    )
    for relative in skill_paths:
        text = (ROOT / relative).read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        assert 'scenario="ascendc_direct_invoke"' in text
        assert "examples/ascendc-direct-search/SPEC_DISCOVERY.md" in text
        assert "materialize_knowledge.py" in text
        assert "knowledge.sources.json" in text
        assert "exact pinned Git commits" in normalized
        assert "AKG" in text
        assert "Never require the user to run a task preparer" in normalized
        assert "Direct Invoke only" in normalized
        assert "GOAL_PLUS_VERIFIER_TMPDIR" in text
        assert "do not invoke an external ascendc" in normalized.casefold()
        assert "_task/operator_request.json" in text
        assert "examples/ascendc-direct-search/request.schema.json" in text
        assert "JSON Schema validator" in normalized
        assert "validation failure blocks freezing" in normalized


def test_ascendc_spec_discovery_requires_generated_verifier_evidence() -> None:
    text = (EXAMPLE / "SPEC_DISCOVERY.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    for required in (
        "passed_case_ids",
        "cases_sha256",
        "exact built candidate artifact",
        "ordinary finite values beyond tolerance",
        "first schema argument is a non-optional Tensor",
        "materialize_knowledge.py",
        "Git objects rather than checkout working trees",
        "immutable selected commit",
        "$GOAL_PLUS_VERIFIER_TMPDIR/workspace",
        "complete structured failure report to stdout",
        "goal_plus.verifier_support.isolated_verifier_workspace",
        "GOAL_PLUS_VERIFIER_DIAGNOSTICS_DIR",
        '"resource_lock": "ascend-npu:<device-id>"',
        "one acceptance case policy shared by Search and Promotion",
        "Search and Promotion performance use the same case IDs",
        "executable scoring contract of the selected `scoring` reference",
        "rank valid Candidates by minimum measured time",
        "Promotion may collect additional non-gating diagnostics",
        "minimally correct Seed that builds and passes every shared correctness case",
        "same frozen scorer and comparison inputs to the Baseline and every Candidate",
        "`IsolatedVerifierWorkspace` representation itself",
        "isolated.workspace",
        "isolated.temp_root",
        "required_paths=required",
        "scripts/build_wheel.sh",
        "launch the evaluator from `temp_root`",
        "Do not propagate that inherited value to an evaluator",
        "A neutral `cwd` alone is insufficient",
        "goal_plus.verifier_support.sanitized_evaluator_environment",
        "evaluator_env = sanitized_evaluator_environment",
        "python_paths=(pinned_cannbench_src,)",
        "official report `overall_score`, maximized",
        "SearchSpec metric is `cannbench_score`",
        "parse_cannbench_report",
        "do not add `--no-perf`",
        "do not rank with `precision_margin`",
        "use a standards-based JSON Schema validator",
        "manual field checklist or JSON parse alone is not validation",
        "Immediately before `search_freeze_spec`, validate the final `_task/operator_request.json` again",
        "delivery format, knowledge location, verifier entrypoint",
        "resolved `implementation` references relevant to the task",
    ):
        assert required in normalized

    for public_asset in (
        EXAMPLE / "SPEC_DISCOVERY.md",
        EXAMPLE / "README.md",
        ROOT / "tests" / "st_pi" / "ascendc_cannbench_gelu.md",
    ):
        assert "/home/" not in public_asset.read_text(encoding="utf-8")

    combined_docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            EXAMPLE / "README.md",
            ROOT / "docs" / "ascendc-direct-invoke-development-plan.md",
            ROOT / "docs" / "ascendc-direct-invoke-development-plan-zh.md",
        )
    )
    for retired_name in (
        "prepare_workspace.py",
        "skill_profile.json",
        "run_search_eval.sh",
        "run_full_acceptance.sh",
    ):
        assert retired_name not in combined_docs


def test_ascendc_npu_smoke_uses_real_goal_plus_with_two_candidates() -> None:
    prompt = (ROOT / "tests/st_pi/ascendc_cannbench_gelu.md").read_text(
        encoding="utf-8"
    )
    normalized_prompt = " ".join(prompt.split())
    for required in (
        "CANNBench Level1 GELU",
        "{{CANNBENCH_ROOT}}/tasks/level1/gelu",
        "AscendC Direct Invoke",
        '"max_candidates": 2',
        '"max_parallel": 2',
        '"worker_host": "pi-rpc"',
        "pi_search_run_batch",
        "禁止调用 OpenCode",
        "passed_case_ids",
        "cases_sha256",
        "artifact_hash",
        "Promotion Verifier",
        "{{NPU_ENV_SETUP}}",
        "$GOAL_PLUS_VERIFIER_TMPDIR/workspace",
        "resource_lock",
        "Search Precision 必须覆盖 GELU 的全部 官方 Case",
        "Search 与 Promotion 的 Oracle、Case、Tolerance、Metric 和拒绝条件必须相同",
        "Baseline、两个 Candidate、Search 和 Promotion 必须使用同一份 冻结评分输入",
    ):
        assert required in normalized_prompt

    script = (ROOT / "scripts/run_ascendc_cannbench_e2e.sh").read_text(
        encoding="utf-8"
    )
    for required in (
        "GOAL_PLUS_NPU_CONDA_SH",
        "GOAL_PLUS_NPU_ENV_SH",
        "GOAL_PLUS_RUN_ASCENDC_NPU_ST=1",
        'python -m pip install -e ".[dev]"',
        "command -v pi",
        'st_npu and st_pi',
    ):
        assert required in script

    st_test = (ROOT / "tests/st_pi/test_ascendc_npu.py").read_text(
        encoding="utf-8"
    )
    assert 'f"/goal-plus {prompt}"' in st_test
    assert 'frozen.spec.strategy.worker_host == "pi-rpc"' in st_test
    assert '_tool_calls(entries, "pi_search_run_batch")' in st_test
    assert "run.state == \"promoted\"" in st_test
    assert "selected.promotion_evidence.passed is True" in st_test
