from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


OPTIONS = {
    "loader": {"csv_reader", "json_reader", "parquet_reader", "sqlite_reader", "api_reader"},
    "preprocess": {"normalize", "dedupe", "tokenize", "filter_nulls", "bucketize"},
    "algorithm": {"quicksort", "mergesort", "heapsort", "radixsort", "timsort"},
    "formatter": {"json", "xml", "csv", "protobuf", "yaml"},
}

HIDDEN_TARGET = {
    "loader": "csv_reader",
    "preprocess": "normalize",
    "algorithm": "quicksort",
    "formatter": "json",
}


def _load_program(program_path: str):
    path = Path(program_path).resolve()
    spec = importlib.util.spec_from_file_location("candidate_program", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load program from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(program_path: str) -> dict[str, Any]:
    try:
        program = _load_program(program_path)
        if not hasattr(program, "configure_pipeline"):
            return {
                "combined_score": 0.0,
                "valid": False,
                "error": "missing configure_pipeline",
            }

        config = program.configure_pipeline()
        if not isinstance(config, dict):
            return {
                "combined_score": 0.0,
                "valid": False,
                "error": "configure_pipeline must return dict",
            }

        for key, choices in OPTIONS.items():
            if key not in config:
                return {
                    "combined_score": 0.0,
                    "valid": False,
                    "error": f"missing key: {key}",
                }
            if config[key] not in choices:
                return {
                    "combined_score": 0.0,
                    "valid": False,
                    "error": f"invalid option for {key}: {config[key]}",
                }

        correct = sum(1 for key, value in HIDDEN_TARGET.items() if config[key] == value)
        return {
            "combined_score": correct / len(HIDDEN_TARGET),
            "correct_modules": correct,
            "total_modules": len(HIDDEN_TARGET),
            "valid": True,
        }
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "valid": False,
            "error": str(exc),
        }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "initial_program.py"
    print(json.dumps(evaluate(target), sort_keys=True))

