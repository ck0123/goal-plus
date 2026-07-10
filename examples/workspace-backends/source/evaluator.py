from __future__ import annotations

import json
from pathlib import Path


def evaluate(program_path: str | Path = "initial_program.py") -> dict[str, float | bool]:
    namespace: dict[str, object] = {}
    path = Path(program_path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    value = namespace.get("VALUE")
    valid = isinstance(value, int | float)
    return {
        "combined_score": float(value) if valid else 0.0,
        "valid": valid,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), sort_keys=True))
