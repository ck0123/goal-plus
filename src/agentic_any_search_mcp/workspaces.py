from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
from pathlib import Path

from agentic_any_search_mcp.models import WorkspaceBackend
from agentic_any_search_mcp.paths import DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT


IGNORED_NAMES = {
    ".git",
    DEFAULT_RUNTIME_ROOT,
    LEGACY_RUNTIME_ROOT,
    ".tmp",
    ".pytest_cache",
    "__pycache__",
}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


@dataclass(frozen=True)
class WorkspaceMaterialization:
    backend: WorkspaceBackend
    workspace: Path
    branch: str | None
    base_revision: str | None


def should_ignore(path: Path) -> bool:
    if any(part in IGNORED_NAMES for part in path.parts):
        return True
    return path.suffix in IGNORED_SUFFIXES


def list_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and not should_ignore(path.relative_to(root)):
            files.append(path)
    return sorted(files)


def copy_source_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / source.name)
        return

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in IGNORED_NAMES or Path(name).suffix in IGNORED_SUFFIXES:
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, ignore=ignore)


def initialize_workspace_git_baseline(workspace: Path) -> str | None:
    try:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    files = [path.relative_to(workspace).as_posix() for path in list_files(workspace)]
    if not files:
        return None

    try:
        subprocess.run(
            ["git", "add", "--", *files],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=agentic-any-search",
                "-c",
                "user.email=agentic-any-search@example.invalid",
                "commit",
                "-q",
                "--no-verify",
                "-m",
                "search candidate baseline",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    return _git_output(workspace, "rev-parse", "HEAD")


def materialize_candidate_workspace(
    *,
    backend: WorkspaceBackend,
    run_dir: Path,
    source: Path,
    workspace: Path,
    run_id: str,
    candidate_id: str,
    base_workspace: Path | None = None,
    base_revision: str | None = None,
) -> WorkspaceMaterialization:
    if backend == "copy":
        copy_source_tree(base_workspace or source, workspace)
        revision = initialize_workspace_git_baseline(workspace)
        (workspace / ".tmp").mkdir(parents=True, exist_ok=True)
        return WorkspaceMaterialization(
            backend="copy",
            workspace=workspace,
            branch=None,
            base_revision=revision,
        )

    repository = _ensure_worktree_repository(run_dir, source, run_id)
    baseline_branch = _branch_name(run_id, "baseline")
    revision = base_revision or _git_output(
        repository, "rev-parse", baseline_branch
    )
    branch = _branch_name(run_id, candidate_id)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _git_run(
        repository,
        "worktree",
        "add",
        "-q",
        "-b",
        branch,
        str(workspace),
        revision,
    )
    (workspace / ".tmp").mkdir(parents=True, exist_ok=True)
    return WorkspaceMaterialization(
        backend="git_worktree",
        workspace=workspace,
        branch=branch,
        base_revision=revision,
    )


def _ensure_worktree_repository(run_dir: Path, source: Path, run_id: str) -> Path:
    repository = run_dir / "workspace-repository"
    if repository.exists():
        _git_output(repository, "rev-parse", "--show-toplevel")
        return repository

    copy_source_tree(source, repository)
    baseline_revision = initialize_workspace_git_baseline(repository)
    if baseline_revision is None:
        raise RuntimeError("git_worktree backend requires a non-empty source and Git")
    _git_run(repository, "branch", "-M", _branch_name(run_id, "baseline"))
    return repository


def _branch_name(run_id: str, candidate_id: str) -> str:
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip("-")
    safe_candidate_id = re.sub(
        r"[^A-Za-z0-9._-]+", "-", candidate_id
    ).strip("-")
    if not safe_run_id or not safe_candidate_id:
        raise ValueError("run_id and candidate_id must contain a Git-safe character")
    return f"gp/{safe_run_id}/{safe_candidate_id}"


def _git_run(repository: Path, *args: str) -> None:
    try:
        subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(
            f"git workspace command failed in {repository}: git {' '.join(args)}: "
            f"{detail.strip()}"
        ) from exc


def _git_output(repository: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(
            f"git workspace command failed in {repository}: git {' '.join(args)}: "
            f"{detail.strip()}"
        ) from exc
