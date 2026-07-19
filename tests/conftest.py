"""Shared pytest configuration for the goal-plus test suite.

Mirrors the procedural opt-in skip pattern established by
``tests/st/conftest.py``: tests carrying ``integration`` or ``example``
markers are skipped unless the corresponding marker name appears in the
``-m`` selection expression. Unsupported OpenCode and Claude tests likewise
require their explicit host marker. This keeps the default ``pytest``
invocation focused on maintained fast tests.
"""

from __future__ import annotations

import pytest

_OPT_IN_MARKERS = ("integration", "example")
_UNSUPPORTED_HOST_MARKERS = ("opencode", "claude")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip opt-in marker tests unless their marker is named in ``-m``."""
    for item in items:
        nodeid = item.nodeid.lower()
        if "opencode" in nodeid:
            item.add_marker(pytest.mark.opencode)
        if "claude" in nodeid:
            item.add_marker(pytest.mark.claude)

    markexpr = config.getoption("-m") or ""
    for name in _UNSUPPORTED_HOST_MARKERS:
        if name in markexpr:
            continue
        for item in items:
            if any(m.name == name for m in item.iter_markers()):
                item.add_marker(
                    pytest.mark.skip(
                        reason=(
                            f"unsupported {name} tests not selected "
                            f"(use `-m {name}` to run)"
                        )
                    )
                )

    for name in _OPT_IN_MARKERS:
        if name in markexpr:
            continue
        for item in items:
            if any(m.name == name for m in item.iter_markers()):
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"{name} tests not selected (use `-m {name}` to run)"
                    )
                )
