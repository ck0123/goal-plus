from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
from pathlib import Path

from goal_plus.models import WorkspaceBackend
from goal_plus.paths import DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT


IGNORED_NAMES = {
    ".git",
    DEFAULT_RUNTIME_ROOT,
    LEGACY_RUNTIME_ROOT,
    ".tmp",
    ".pytest_cache",
    "__pycache__",
}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
GENERATED_SOURCE_DIRECTORIES = {"build", "dist", "CMakeFiles"}
GENERATED_SOURCE_SUFFIXES = {".o", ".obj", ".so", ".whl"}
GENERATED_SOURCE_FILES = {
    "CMakeCache.txt",
    "cmake_install.cmake",
    "compile_commands.json",
}


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


def list_source_files(root: Path) -> list[Path]:
    """List source inputs without carrying untracked build products forward.

    Ordinary ignored files remain valid source inputs. Generated-looking files
    are retained only when the source repository deliberately tracks them.
    """
    if root.is_file():
        return [root]

    try:
        tracked = _git_list_paths(
            root,
            ["git", "ls-files", "--cached", "-z", "--", "."],
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        tracked = set()

    paths: list[Path] = []
    for path in list_files(root):
        rel_path = path.relative_to(root)
        rel_value = rel_path.as_posix()
        if (
            rel_value in tracked
            or not _looks_like_generated_source_artifact(rel_path)
        ):
            paths.append(path)
    return sorted(paths)


def _looks_like_generated_source_artifact(path: Path) -> bool:
    if any(
        part in GENERATED_SOURCE_DIRECTORIES or part.endswith(".egg-info")
        for part in path.parts[:-1]
    ):
        return True
    return (
        path.name in GENERATED_SOURCE_FILES
        or path.suffix in GENERATED_SOURCE_SUFFIXES
    )


def _git_list_paths(
    cwd: Path,
    command: list[str],
) -> set[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    }


def copy_source_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / source.name)
        return

    included_files = {
        path.relative_to(source).as_posix() for path in list_source_files(source)
    }
    included_directories = {
        parent.as_posix()
        for rel_path in included_files
        for parent in Path(rel_path).parents
        if parent != Path(".")
    }

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        rel_directory = Path(directory).relative_to(source)
        for name in names:
            rel_path = (rel_directory / name).as_posix()
            path = Path(directory) / name
            if (
                name in IGNORED_NAMES
                or Path(name).suffix in IGNORED_SUFFIXES
                or (
                    path.is_dir()
                    and not path.is_symlink()
                    and rel_path not in included_directories
                )
                or (
                    (not path.is_dir() or path.is_symlink())
                    and rel_path not in included_files
                )
            ):
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
            ["git", "add", "-f", "--", *files],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=goal-plus",
                "-c",
                "user.email=goal-plus@example.invalid",
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
    if not _is_expected_worktree(repository, workspace, branch, revision):
        _git_run(repository, "worktree", "prune")
        branch_ref = f"refs/heads/{branch}"
        if _git_succeeds(repository, "show-ref", "--verify", "--quiet", branch_ref):
            branch_revision = _git_output(repository, "rev-parse", branch_ref)
            if branch_revision != revision:
                raise RuntimeError(
                    f"candidate branch {branch} exists at {branch_revision}, "
                    f"expected {revision}"
                )
            _git_run(
                repository,
                "worktree",
                "add",
                "-q",
                str(workspace),
                branch,
            )
        else:
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
    baseline_branch = _branch_name(run_id, "baseline")
    if repository.exists():
        if not (repository / ".git").exists():
            shutil.rmtree(repository)
        else:
            try:
                _git_output(repository, "rev-parse", baseline_branch)
                return repository
            except RuntimeError:
                try:
                    _git_output(repository, "rev-parse", "HEAD")
                except RuntimeError as exc:
                    if isinstance(exc.__cause__, FileNotFoundError):
                        raise
                    shutil.rmtree(repository)
                else:
                    _git_run(repository, "branch", "-M", baseline_branch)
                    return repository

    staging = run_dir / "workspace-repository.init"
    copy_source_tree(source, staging)
    baseline_revision = initialize_workspace_git_baseline(staging)
    if baseline_revision is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("git_worktree backend requires a non-empty source and Git")
    _git_run(staging, "branch", "-M", baseline_branch)
    staging.replace(repository)
    return repository


def _is_expected_worktree(
    repository: Path,
    workspace: Path,
    branch: str,
    revision: str,
) -> bool:
    if not workspace.exists():
        return False
    if not any(workspace.iterdir()):
        workspace.rmdir()
        return False
    try:
        current_branch = _git_output(workspace, "branch", "--show-current")
        current_revision = _git_output(workspace, "rev-parse", "HEAD")
        common_dir = _resolve_git_path(
            workspace, _git_output(workspace, "rev-parse", "--git-common-dir")
        )
        repository_git_dir = _resolve_git_path(
            repository, _git_output(repository, "rev-parse", "--git-common-dir")
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"candidate workspace already exists but is not a valid worktree: {workspace}"
        ) from exc
    if (
        current_branch != branch
        or current_revision != revision
        or common_dir != repository_git_dir
    ):
        raise RuntimeError(
            f"candidate workspace {workspace} does not match expected branch/base"
        )
    return True


def _resolve_git_path(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (workspace / path).resolve()


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


def _git_succeeds(repository: Path, *args: str) -> bool:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        ).returncode == 0
    except FileNotFoundError:
        return False
