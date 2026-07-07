#!/usr/bin/env python3
"""Compatibility wrapper for the Goal Plus host hook.

Host configs should prefer `agentic-any-search-mcp --goal-plus-host-hook` so
the hook uses the same installed Python environment as the MCP server. This
script remains useful for local development and direct unit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _add_local_src_to_path() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))


def main() -> int:
    _add_local_src_to_path()
    from agentic_any_search_mcp.goal_plus_stop_hook import main as hook_main

    return hook_main()


if __name__ == "__main__":
    raise SystemExit(main())
