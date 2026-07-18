from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.example
EXAMPLE_DIR = ROOT / "examples" / "model-optimize"
TARGET = EXAMPLE_DIR / "torch-cpu-target"


def _run_json_suite(scripts: list[Path]) -> dict[str, dict]:
    parents = {script.parent for script in scripts}
    assert len(parents) == 1
    driver = """
import contextlib
import io
import json
import runpy
import sys

results = {}
for script in sys.argv[1:]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runpy.run_path(script, run_name="__main__")
    results[script] = json.loads(output.getvalue().strip().splitlines()[-1])
print(json.dumps(results, sort_keys=True))
"""
    env = {
        **os.environ,
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "MAX_JOBS": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-c", driver, *[script.name for script in scripts]],
        cwd=parents.pop(),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_model_optimize_no_longer_uses_static_templates() -> None:
    assert not (EXAMPLE_DIR / "templates").exists()
    assert not (EXAMPLE_DIR / "prompts").exists()
    assert not (EXAMPLE_DIR / "skills").exists()


def test_torch_cpu_target_runs_on_one_cpu_thread() -> None:
    results = _run_json_suite(
        [TARGET / "verify.py", TARGET / "benchmark.py", TARGET / "profile.py"]
    )
    verify = results["verify.py"]
    benchmark = results["benchmark.py"]
    profile = results["profile.py"]

    assert verify["valid"] is True
    assert verify["torch_num_threads"] == 1
    assert benchmark["valid"] is True
    assert benchmark["torch_num_threads"] == 1
    assert benchmark["tokens_per_second"] > 0
    assert profile["valid"] is True
    assert any(item["id"] == "fuse_vector_tail" for item in profile["opportunities"])
    assert not any(
        item["id"] == "remove_redundant_projection"
        for item in profile["opportunities"]
    )


def test_cpp_reference_fused_op_is_present_and_documented() -> None:
    cpp = TARGET / "cpp_reference" / "fused_vector_tail.cpp"
    runner = TARGET / "cpp_reference" / "run_reference.py"

    assert cpp.is_file()
    assert runner.is_file()
    text = cpp.read_text(encoding="utf-8")
    assert "fused_vector_tail" in text
    assert "torch::Tensor" in text
    assert "TORCH_LIBRARY" in text


@pytest.mark.pi
def test_pi_goal_skill_and_user_prompt_are_minimal_goal_inputs() -> None:
    skill_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".pi" / "skills").glob("*/SKILL.md")
    )
    prompt = (EXAMPLE_DIR / "pi-goal-prompt.md").read_text(encoding="utf-8")

    assert skill_files == [".pi/skills/goal-plus/SKILL.md"]
    assert "single CPU core" in prompt
    assert "C++ CPU operator" in prompt
    assert "cpp_reference/fused_vector_tail.cpp" in prompt
    assert "/goal-plus" in prompt
    assert "examples/model-optimize/torch-cpu-target" in prompt
    assert "tokens_per_second" in prompt
