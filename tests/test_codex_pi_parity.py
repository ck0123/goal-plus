from pathlib import Path
import re

import pytest

from goal_plus.agent_hosts import get_agent_host_adapter


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.codex, pytest.mark.pi]


def test_fast_host_markers_are_registered() -> None:
    pytest_config = (ROOT / "pytest.ini").read_text(encoding="utf-8")

    assert "    codex: Codex-specific unit, integration, asset, or parity test" in pytest_config
    assert "    pi: Pi-specific unit, integration, asset, or parity test" in pytest_config


@pytest.mark.parametrize(
    ("relative_path", "marker"),
    [
        ("tests/test_codex_assets.py", "codex"),
        ("tests/test_pi_assets.py", "pi"),
        ("tests/test_pi_worker.py", "pi"),
        ("tests/test_pi_driver.py", "pi"),
        ("tests/test_pi_pool.py", "pi"),
        ("tests/test_pi_tool.py", "pi"),
    ],
)
def test_host_specific_test_modules_declare_fast_marker(
    relative_path: str,
    marker: str,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert f"pytestmark = pytest.mark.{marker}" in source


@pytest.mark.parametrize("host", ["codex", "pi_rpc"])
def test_mixed_adapter_tests_mark_host_specific_cases(host: str) -> None:
    source = (ROOT / "tests" / "test_agent_hosts.py").read_text(encoding="utf-8")
    host_marker = "codex" if host == "codex" else "pi"
    host_tests = re.findall(rf"def (test_{host}[^\(]+)", source)

    assert host_tests
    for test_name in host_tests:
        definition = f"def {test_name}"
        prefix = source[: source.index(definition)]
        assert prefix.rstrip().endswith(f"@pytest.mark.{host_marker}"), test_name


def test_codex_and_pi_publish_native_parity_capabilities() -> None:
    codex = get_agent_host_adapter("codex").capabilities
    pi = get_agent_host_adapter("pi-rpc").capabilities

    assert codex.supports_soft_closeout is True
    assert codex.supports_model_override is True
    assert codex.supports_reasoning_effort is True
    assert codex.supports_service_tier is True
    assert codex.supports_usage_metadata is False
    assert codex.supports_process_kill is False
    assert codex.pool.launch_mode == "async"
    assert codex.pool.wait_mode == "wait_any"
    assert codex.pool.continuation_mode == "same_worker"
    assert codex.pool.recovery_mode == "host_resident"

    assert pi.supports_soft_closeout is True
    assert pi.supports_model_override is True
    assert pi.supports_reasoning_effort is True
    assert pi.supports_service_tier is False
    assert pi.supports_usage_metadata is True
    assert pi.supports_process_kill is True
    assert pi.pool.launch_mode == "async"
    assert pi.pool.wait_mode == "wait_any"
    assert pi.pool.continuation_mode == "state_redispatch"
    assert pi.pool.recovery_mode == "supervisor_persisted"


@pytest.mark.parametrize(
    "relative_path",
    [
        "tests/test_runtime_unit.py",
        "tests/test_models.py",
        "tests/test_goal_plus.py",
        "tests/test_model_optimize_torch_cpu_target.py",
        "tests/test_st_host_harness.py",
    ],
)
def test_mixed_modules_mark_named_codex_and_pi_fast_tests(relative_path: str) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    host_tests = re.findall(r"def (test_[^\(]*(?:codex|pi_)[^\(]*)", source)

    assert host_tests
    for test_name in host_tests:
        marker = "codex" if "codex" in test_name else "pi"
        definition = f"def {test_name}"
        prefix = source[: source.index(definition)]
        assert prefix.rstrip().endswith(f"@pytest.mark.{marker}"), (
            relative_path,
            test_name,
        )


def test_test_readme_documents_fast_markers_and_terra_st_model() -> None:
    text = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    assert "pytest -m codex" in text
    assert "pytest -m pi" in text
    assert "gpt-5.6-terra" in text


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/codex.md",
        "tests/README.md",
        "tests/st/conftest.py",
        "tests/st/hosts.py",
        "tests/st/helpers/codex_runner.py",
        "tests/st/prompts/codex_redispatch.md",
        "tests/st/prompts/codex_rolling_followup.md",
    ],
)
def test_codex_terra_path_uses_the_cli_model_slug(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8").lower()

    assert ("openai." + "gpt-5.6-terra") not in text
