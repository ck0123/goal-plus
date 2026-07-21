#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "runner.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, file=sys.stderr, end="")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        return completed.returncode

    match = re.search(
        r"^score_cycles=(\d+(?:\.\d+)?)$",
        completed.stdout,
        re.MULTILINE,
    )
    if match is None:
        match = re.search(
            r"^Score:\s*(\d+(?:\.\d+)?)$",
            completed.stdout,
            re.MULTILINE,
        )
    if match is None:
        print("could not parse VLIW cycle score", file=sys.stderr)
        return 2
    print(json.dumps({"cycles": float(match.group(1))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
