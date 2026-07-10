from __future__ import annotations

import subprocess
from pathlib import Path

from agentic_any_search_mcp.workspaces import materialize_candidate_workspace


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "program.py").write_text("VALUE = 0\n", encoding="utf-8")
    return source


def git_output(workspace: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=workspace, text=True
    ).strip()


def git_commit(workspace: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            message,
        ],
        cwd=workspace,
        check=True,
    )
    return git_output(workspace, "rev-parse", "HEAD")


def common_git_dir(workspace: Path) -> Path:
    raw = Path(git_output(workspace, "rev-parse", "--git-common-dir"))
    return raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()


def test_copy_backend_creates_independent_git_workspaces(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    run_dir = tmp_path / "run"
    first_path = run_dir / "workspace" / "c001"
    second_path = run_dir / "workspace" / "c002"

    first = materialize_candidate_workspace(
        backend="copy",
        run_dir=run_dir,
        source=source,
        workspace=first_path,
        run_id="run_test",
        candidate_id="c001",
    )
    second = materialize_candidate_workspace(
        backend="copy",
        run_dir=run_dir,
        source=source,
        workspace=second_path,
        run_id="run_test",
        candidate_id="c002",
    )

    assert first.backend == "copy"
    assert first.branch is None
    assert first.base_revision is not None
    assert second.backend == "copy"
    assert (first_path / "program.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert common_git_dir(first_path) != common_git_dir(second_path)


def test_git_worktree_backend_shares_objects_but_keeps_branches_independent(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path)
    run_dir = tmp_path / "run"
    first_path = run_dir / "workspace" / "c001"
    second_path = run_dir / "workspace" / "c002"

    first = materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=first_path,
        run_id="run_test",
        candidate_id="c001",
    )
    second = materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=second_path,
        run_id="run_test",
        candidate_id="c002",
    )

    assert first.branch == "gp/run_test/c001"
    assert second.branch == "gp/run_test/c002"
    assert common_git_dir(first_path) == common_git_dir(second_path)
    assert git_output(first_path, "branch", "--show-current") == first.branch
    assert git_output(second_path, "branch", "--show-current") == second.branch

    (first_path / "program.py").write_text("VALUE = 1\n", encoding="utf-8")
    first_head = git_commit(first_path, "candidate one")
    (second_path / "program.py").write_text("VALUE = 2\n", encoding="utf-8")
    second_head = git_commit(second_path, "candidate two")

    assert first_head != second_head
    assert (first_path / "program.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (second_path / "program.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_git_worktree_baseline_tracks_files_ignored_by_source_gitignore(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path)
    (source / ".gitignore").write_text("generated.txt\n", encoding="utf-8")
    (source / "generated.txt").write_text("snapshot me\n", encoding="utf-8")
    workspace = tmp_path / "run" / "workspace" / "c001"

    materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=tmp_path / "run",
        source=source,
        workspace=workspace,
        run_id="run_test",
        candidate_id="c001",
    )

    assert (workspace / "generated.txt").read_text(encoding="utf-8") == "snapshot me\n"
    tracked = git_output(workspace, "ls-files", "generated.txt")
    assert tracked == "generated.txt"


def test_git_worktree_materialization_is_idempotent_after_workspace_creation(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path)
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace" / "c001"

    first = materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=workspace,
        run_id="run_test",
        candidate_id="c001",
    )
    second = materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=workspace,
        run_id="run_test",
        candidate_id="c001",
    )

    assert second == first
    assert git_output(workspace, "branch", "--show-current") == first.branch


def test_git_worktree_recovers_incomplete_run_repository(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    run_dir = tmp_path / "run"
    repository = run_dir / "workspace-repository"
    repository.mkdir(parents=True)
    (repository / "partial.txt").write_text("incomplete\n", encoding="utf-8")
    workspace = run_dir / "workspace" / "c001"

    materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=workspace,
        run_id="run_test",
        candidate_id="c001",
    )

    assert (workspace / "program.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not (workspace / "partial.txt").exists()


def test_git_worktree_child_starts_from_explicit_parent_revision(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path)
    run_dir = tmp_path / "run"
    parent_path = run_dir / "workspace" / "c001"
    child_path = run_dir / "workspace" / "c003"

    parent = materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=parent_path,
        run_id="run_test",
        candidate_id="c001",
    )
    (parent_path / "program.py").write_text("VALUE = 7\n", encoding="utf-8")
    parent_revision = git_commit(parent_path, "parent winner")

    child = materialize_candidate_workspace(
        backend="git_worktree",
        run_dir=run_dir,
        source=source,
        workspace=child_path,
        run_id="run_test",
        candidate_id="c003",
        base_revision=parent_revision,
    )

    assert parent.base_revision != parent_revision
    assert child.base_revision == parent_revision
    assert child.branch == "gp/run_test/c003"
    assert git_output(child_path, "rev-parse", "HEAD") == parent_revision
    assert (child_path / "program.py").read_text(encoding="utf-8") == "VALUE = 7\n"
    assert common_git_dir(parent_path) == common_git_dir(child_path)
