from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_prediction(path: Path) -> str:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("prediction file must contain a JSON object")
    return str(parsed.get("answer", ""))


def read_gold(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("gold file must contain a JSON object")
    return parsed


def parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--prediction", required=True)
    arg_parser.add_argument("--gold-file", required=True)
    return arg_parser


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0
