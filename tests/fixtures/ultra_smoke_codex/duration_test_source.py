import pytest

from duration import parse_duration


def test_single_and_compound_durations() -> None:
    assert parse_duration("250ms") == 250
    assert parse_duration("1h 30m") == 5_400_000
    assert parse_duration("2m5s") == 125_000


@pytest.mark.parametrize("value", ["", "12", "-1s", "1.5s", "4fortnights"])
def test_invalid_durations(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)
