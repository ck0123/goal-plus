from __future__ import annotations

import pytest

from goal_plus.codex_pricing import estimate_codex_request_cost


pytestmark = pytest.mark.codex


def test_codex_cost_matches_pi_long_context_and_priority_semantics() -> None:
    estimate = estimate_codex_request_cost(
        {
            "input_tokens": 300_000,
            "cached_input_tokens": 200_000,
            "cache_write_input_tokens": 10_000,
            "output_tokens": 1_000,
        },
        model="openai-codex/gpt-5.6-sol",
        service_tier="priority",
    )

    assert estimate is not None
    assert estimate["matched_input_tokens_above"] == 272_000
    assert estimate["service_tier_multiplier"] == 2.0
    assert estimate["input_tokens"] == 90_000
    assert estimate["cost_usd"] == pytest.approx(2.54)
    assert estimate["components_usd"] == pytest.approx(
        {
            "input": 1.8,
            "output": 0.09,
            "cache_read": 0.4,
            "cache_write": 0.25,
        }
    )


def test_codex_cost_stays_unavailable_for_unknown_model() -> None:
    assert (
        estimate_codex_request_cost(
            {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "output_tokens": 10,
            },
            model="gpt-future",
            service_tier=None,
        )
        is None
    )
