from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


HostKind = Literal["opencode", "codex", "claude-code", "pi-rpc"]


@dataclass(frozen=True)
class StHost:
    kind: HostKind
    marker: str
    display_name: str
    binary: str
    model_env: str
    default_model: str | None = None


HOSTS: dict[HostKind, StHost] = {
    "opencode": StHost(
        kind="opencode",
        marker="st_opencode",
        display_name="OpenCode",
        binary="opencode",
        model_env="ST_OPENCODE_MODEL",
    ),
    "codex": StHost(
        kind="codex",
        marker="st_codex",
        display_name="Codex",
        binary="codex",
        model_env="ST_CODEX_MODEL",
        default_model="gpt-5.3-codex-spark",
    ),
    "claude-code": StHost(
        kind="claude-code",
        marker="st_claude",
        display_name="Claude Code",
        binary="claude",
        model_env="ST_CLAUDE_MODEL",
    ),
    "pi-rpc": StHost(
        kind="pi-rpc",
        marker="st_pi_rpc",
        display_name="Pi RPC",
        binary="pi",
        model_env="ST_PI_MODEL",
    ),
}

HOST_BY_MARKER = {host.marker: host for host in HOSTS.values()}
DEFAULT_ST_HOST: HostKind = "opencode"
ST_ACTIVE_ENV = "AGENTIC_ANY_SEARCH_ST_ACTIVE"


def st_host_from_marker_names(marker_names: list[str] | set[str]) -> HostKind:
    host_markers = [name for name in marker_names if name in HOST_BY_MARKER]
    if len(host_markers) > 1:
        raise ValueError(f"multiple ST host markers are not allowed: {host_markers}")
    if host_markers:
        return HOST_BY_MARKER[host_markers[0]].kind
    return DEFAULT_ST_HOST


def st_model_for_host(host: HostKind) -> str | None:
    config = HOSTS[host]
    return os.environ.get(config.model_env) or config.default_model


def _replace_path(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _link_if_present(project_root: Path, source_root: Path, name: str) -> None:
    source = source_root / name
    if not source.exists():
        return
    target = project_root / name
    _replace_path(target)
    if target.exists():
        return
    target.symlink_to(source, target_is_directory=source.is_dir())


def link_host_assets(project_root: Path, source_root: Path) -> None:
    """Expose project-local host configs inside an isolated ST workdir."""
    _replace_path(project_root / ".agents")
    for name in (
        "opencode.json",
        ".codex",
        ".mcp.json",
        ".claude",
        ".pi",
    ):
        _link_if_present(project_root, source_root, name)
