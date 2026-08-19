import pytest

from duration import parse_duration


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" \t1MS2s\n3M 4h\r\n", 14_582_001),
        ("500ms 500MS1s", 2_000),
        ("999999999999999999999h", 999999999999999999999 * 3_600_000),
    ],
)
def test_valid_edge_cases(value: str, expected: int) -> None:
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["   ", "\t\n", "0ms", "00s", "1m 0s"])
def test_empty_or_non_positive_tokens_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize(
    "value",
    [
        "+1s",
        "1s-2ms",
        "1.0s",
        "1s .5m",
        "1 ms",
        "1\tms",
        "1s,2m",
        "1s+2m",
        "1s/2m",
    ],
)
def test_malformed_tokens_and_separators_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize(
    "value",
    ["about 1s", "1s remaining", "1s!", "ms", "1", "1d", "1sec", "1\u017f"],
)
def test_unmatched_or_unknown_text_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)
