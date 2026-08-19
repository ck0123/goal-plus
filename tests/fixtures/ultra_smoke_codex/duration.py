"""Duration parser produced by the Goal Plus Ultra Codex smoke run."""

import re


_TOKEN = re.compile(r"([0-9]+)(ms|s|m|h)", re.IGNORECASE | re.ASCII)
_MILLISECONDS_PER_UNIT = {
    "ms": 1,
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
}


def parse_duration(value: str) -> int:
    """Convert a compact duration string to milliseconds."""
    if not isinstance(value, str):
        raise ValueError("duration must be a string")

    position = 0
    total = 0
    token_count = 0

    while position < len(value):
        while position < len(value) and value[position].isspace():
            position += 1

        if position == len(value):
            break

        match = _TOKEN.match(value, position)
        if match is None:
            raise ValueError(f"invalid duration at position {position}")

        amount = int(match.group(1))
        if amount == 0:
            raise ValueError("duration values must be positive")

        unit = match.group(2).lower()
        total += amount * _MILLISECONDS_PER_UNIT[unit]
        token_count += 1
        position = match.end()

    if token_count == 0:
        raise ValueError("duration must contain at least one token")

    return total
