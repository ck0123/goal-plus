from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from .helpers.opencode_runner import OpenCodeRunner, find_opencode


ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).parent / "prompts"


def _run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _opencode_available() -> bool:
    return find_opencode() is not None


def _mcp_runtime_connected() -> tuple[bool, str]:
    """Verify search-runtime MCP server shows up as connected in `opencode mcp list`."""
    if not _opencode_available():
        return False, "opencode binary not on PATH"
    proc = _run(["opencode", "mcp", "list"], timeout=20)
    if proc is None:
        return False, "opencode mcp list timed out or failed to launch"
    if proc.returncode != 0:
        return False, f"opencode mcp list exited {proc.returncode}: {proc.stderr.strip()[:200]}"
    if not re.search(r"search-runtime.*connected", proc.stdout):
        return False, (
            "search-runtime MCP server not connected. "
            "Check opencode.json in project root, or run: "
            "opencode mcp add search-runtime --command 'agentic-any-search-mcp --root .search'"
        )
    return True, ""


def _mcp_server_binary_available() -> tuple[bool, str]:
    """Verify `agentic-any-search-mcp` (the MCP server entry point) is on PATH."""
    path = shutil.which("agentic-any-search-mcp")
    if path is None:
        return False, (
            "agentic-any-search-mcp binary not on PATH. "
            "Install with: pip install -e . (from project root)"
        )
    return True, ""


def _model_available(model: str) -> tuple[bool, str]:
    """Verify the configured model appears in `opencode models` output.

    Only called when the user explicitly sets ST_OPENCODE_MODEL. When unset,
    we don't pass -m to opencode and skip this check entirely — opencode picks
    its own default model.
    """
    if not _opencode_available():
        return False, "opencode not on PATH, cannot list models"
    proc = _run(["opencode", "models"], timeout=20)
    if proc is None:
        return False, "opencode models timed out"
    if proc.returncode != 0:
        return False, f"opencode models exited {proc.returncode}: {proc.stderr.strip()[:200]}"
    if model not in proc.stdout:
        return False, (
            f"ST_OPENCODE_MODEL='{model}' not in `opencode models` output. "
            "Unset ST_OPENCODE_MODEL to let opencode pick its default, or pick a listed model."
        )
    return True, ""


def _fixtures_present() -> tuple[bool, str]:
    """Verify the ST-local fixture specs and evaluators exist.

    ST fixtures live under tests/st/fixtures/<scenario>/ and are independent
    of examples/ and tests/fixtures/ — those are referenced by unit tests and
    example docs, not by ST.
    """
    missing = []
    for scenario in [
        "circle_packing",
        "k_module_problem",
        "signal_processing",
        "swe_bench_20212",
    ]:
        for fname in ("spec.json", "evaluator.py", "initial_program.py", "config.yaml"):
            rel = f"tests/st/fixtures/{scenario}/{fname}"
            if not (ROOT / rel).exists():
                missing.append(rel)
    if missing:
        return False, f"missing ST fixture files under {ROOT}: {missing}"
    return True, ""


def _st_selected(config: pytest.Config) -> bool:
    return "st" in (config.getoption("-m") or "")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "st: system test that drives a real `opencode run` (slow, opt-in via `-m st`)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Pre-flight checks for ST tests. Skip with a concrete reason if any check fails."""
    if not any("st" in [m.name for m in item.iter_markers()] for item in items):
        return

    selected = _st_selected(config)

    # Run all checks up front so we can report ALL failures, not just the first
    checks: list[tuple[bool, str]] = []

    if not selected:
        # No need to run expensive checks if user didn't select ST
        for item in items:
            if "st" in [m.name for m in item.iter_markers()]:
                item.add_marker(pytest.mark.skip(
                    reason="ST tests not selected (use `-m st` to run)"
                ))
        return

    opencode_ok = _opencode_available()
    checks.append((opencode_ok, "opencode binary not on PATH"))

    if opencode_ok:
        mcp_ok, mcp_msg = _mcp_runtime_connected()
        checks.append((mcp_ok, mcp_msg or "search-runtime MCP connected"))

        binary_ok, binary_msg = _mcp_server_binary_available()
        checks.append((binary_ok, binary_msg or "agentic-any-search-mcp on PATH"))

        # Only verify model when user explicitly set ST_OPENCODE_MODEL; otherwise
        # let opencode pick its own default and skip the check.
        model = os.environ.get("ST_OPENCODE_MODEL")
        if model:
            model_ok, model_msg = _model_available(model)
            checks.append((model_ok, model_msg or f"model {model} available"))

    fixtures_ok, fixtures_msg = _fixtures_present()
    checks.append((fixtures_ok, fixtures_msg or "fixtures/specs present"))

    failed = [msg for ok, msg in checks if not ok]
    if failed:
        reason = "ST pre-flight check failed: " + " | ".join(failed)
        for item in items:
            if "st" in [m.name for m in item.iter_markers()]:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture()
def st_project_root(request: pytest.FixtureRequest) -> Path:
    """Temporary project root for an ST run, under <repo>/.tmp/st-runs/.

    Uses the project-local .tmp/ instead of the system tmp_path, because some
    environments restrict /tmp access or have small tmp partitions. Each
    scenario gets its own subdirectory keyed by the test node id so parallel
    runs don't collide.
    """
    base = ROOT / ".tmp" / "st-runs"
    base.mkdir(parents=True, exist_ok=True)
    # Sanitize node id -> dir name (replace :: with _, strip param brackets)
    node = request.node.name.replace("::", "_").replace("[", "_").replace("]", "")
    project_root = base / node
    project_root.mkdir(parents=True, exist_ok=True)

    opencode_json = ROOT / "opencode.json"
    if opencode_json.exists():
        target = project_root / "opencode.json"
        if target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to(opencode_json)

    return project_root


@pytest.fixture()
def st_log_dir(request: pytest.FixtureRequest) -> Path:
    base = ROOT / ".tmp" / "st-logs"
    base.mkdir(parents=True, exist_ok=True)
    node = request.node.name.replace("::", "_").replace("[", "_").replace("]", "")
    log_dir = base / node
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


@pytest.fixture()
def opencode_runner(st_project_root: Path, st_log_dir: Path) -> OpenCodeRunner:
    return OpenCodeRunner(
        project_root=st_project_root,
        log_dir=st_log_dir,
        default_timeout=int(os.environ.get("ST_OPENCODE_TIMEOUT", "1800")),
    )


def load_prompt(scenario: str) -> str:
    """Load a prompt template and render {{PROJECT_ROOT}} with the absolute repo path."""
    path = PROMPTS_DIR / f"{scenario}.md"
    assert path.exists(), f"prompt file missing: {path}"
    text = path.read_text(encoding="utf-8")
    return text.replace("{{PROJECT_ROOT}}", str(ROOT))
