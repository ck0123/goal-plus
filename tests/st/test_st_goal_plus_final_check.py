from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.st.helpers.codex_runner import CodexRunner


pytestmark = [pytest.mark.st, pytest.mark.st_codex]


def test_codex_goal_plus_required_final_checker_smoke(
    st_project_root: Path,
    st_log_dir: Path,
) -> None:
    delivery = st_project_root / "delivery.txt"
    delivery.write_text("goal-plus-final-check-smoke\n", encoding="utf-8")
    runner = CodexRunner(
        project_root=st_project_root,
        log_dir=st_log_dir,
        default_timeout=600,
    )
    raw_goal = (
        "Read delivery.txt and prove that its complete trimmed content is exactly "
        "goal-plus-final-check-smoke. Do not edit any file. Finish through the required "
        "independent Codex final checker."
    )
    result = runner.run_streaming(
        (
            "Create one Goal Plus record with goal_plus_create using this raw goal, "
            "source_path='.', and policy.final_check.mode='required':\n"
            f"{raw_goal}\n\n"
            "Record Goal Mode triage, inspect the file read-only, then call "
            "goal_plus_prepare_final_check(checker_host='codex'). Launch the exact returned "
            "foreground reviewer payload, wait for it, and do not submit the verdict on its "
            "behalf. Finish only after goal_plus_status reports complete."
        ),
        scenario="codex_goal_plus_final_check",
        timeout=600,
    )

    print(f"\n[codex_goal_plus_final_check] log: {result.log_path}")
    assert not result.timed_out, result.log_path
    goal_files = sorted((st_project_root / ".gp" / "goal-plus").glob("gp_*/goal.json"))
    assert len(goal_files) == 1, result.log_path
    record = json.loads(goal_files[0].read_text(encoding="utf-8"))
    assert record["status"] == "complete", result.log_path
    assert record["policy"]["final_check"]["mode"] == "required"
    assert record["goal_revision"] == 1
    assert record["final_checks"][-1]["checker_host"] == "codex"
    assert record["final_checks"][-1]["status"] == "passed"
