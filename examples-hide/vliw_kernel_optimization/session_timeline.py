#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def short(value: object, limit: int = 500) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def message_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a compact, observable timeline from a Codex session JSONL."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--messages", action="store_true", help="include assistant messages")
    args = parser.parse_args()

    with args.session.expanduser().open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("type") != "response_item":
                continue
            timestamp = str(record.get("timestamp") or "-")
            payload = record.get("payload") or {}
            kind = payload.get("type")

            if kind == "function_call":
                name = payload.get("name") or "tool"
                raw_arguments = payload.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                except (TypeError, json.JSONDecodeError):
                    arguments = raw_arguments
                detail = arguments.get("cmd") if isinstance(arguments, dict) else arguments
                if not detail:
                    detail = arguments
                print(f"{timestamp}  {name}: {short(detail)}")
            elif kind == "custom_tool_call":
                name = payload.get("name") or "tool"
                tool_input = payload.get("input") or ""
                files = re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", str(tool_input), re.M)
                detail = ", ".join(files) if files else short(tool_input)
                print(f"{timestamp}  {name}: {detail}")
            elif args.messages and kind == "message" and payload.get("role") == "assistant":
                text = message_text(payload)
                if text:
                    print(f"{timestamp}  assistant: {short(text)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
