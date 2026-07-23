from __future__ import annotations

from typing import Any


CODEX_PRICING_CATALOG = "pi-ai@0.80.6/openai-codex"
CODEX_PRICING_AS_OF = "2026-07-23"
_TOKENS_PER_MILLION = 1_000_000

# API-equivalent USD rates per million tokens, mirrored from the local Pi
# openai-codex model catalog named above. Keep the catalog identifier and date
# in sync when these values change.
_CODEX_MODEL_RATES: dict[str, dict[str, Any]] = {
    "gpt-5.3-codex-spark": {
        "input": 1.75,
        "output": 14.0,
        "cache_read": 0.175,
        "cache_write": 0.0,
    },
    "gpt-5.4": {
        "input": 2.5,
        "output": 15.0,
        "cache_read": 0.25,
        "cache_write": 0.0,
        "tiers": [
            {
                "input_tokens_above": 272_000,
                "input": 5.0,
                "output": 22.5,
                "cache_read": 0.5,
                "cache_write": 0.0,
            }
        ],
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "output": 4.5,
        "cache_read": 0.075,
        "cache_write": 0.0,
    },
    "gpt-5.5": {
        "input": 5.0,
        "output": 30.0,
        "cache_read": 0.5,
        "cache_write": 0.0,
        "tiers": [
            {
                "input_tokens_above": 272_000,
                "input": 10.0,
                "output": 45.0,
                "cache_read": 1.0,
                "cache_write": 0.0,
            }
        ],
    },
    "gpt-5.6-luna": {
        "input": 1.0,
        "output": 6.0,
        "cache_read": 0.1,
        "cache_write": 1.25,
        "tiers": [
            {
                "input_tokens_above": 272_000,
                "input": 2.0,
                "output": 9.0,
                "cache_read": 0.2,
                "cache_write": 2.5,
            }
        ],
    },
    "gpt-5.6-sol": {
        "input": 5.0,
        "output": 30.0,
        "cache_read": 0.5,
        "cache_write": 6.25,
        "tiers": [
            {
                "input_tokens_above": 272_000,
                "input": 10.0,
                "output": 45.0,
                "cache_read": 1.0,
                "cache_write": 12.5,
            }
        ],
    },
    "gpt-5.6-terra": {
        "input": 2.5,
        "output": 15.0,
        "cache_read": 0.25,
        "cache_write": 3.125,
        "tiers": [
            {
                "input_tokens_above": 272_000,
                "input": 5.0,
                "output": 22.5,
                "cache_read": 0.5,
                "cache_write": 6.25,
            }
        ],
    },
}


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _model_id(value: str | None) -> str | None:
    if not value:
        return None
    for prefix in ("openai-codex/", "openai/"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _service_tier_multiplier(model: str, service_tier: str | None) -> float:
    if service_tier == "flex":
        return 0.5
    if service_tier == "priority":
        return 2.5 if model == "gpt-5.5" else 2.0
    return 1.0


def estimate_codex_request_cost(
    usage: Any,
    *,
    model: str | None,
    service_tier: str | None,
) -> dict[str, Any] | None:
    """Estimate one Codex model response with Pi-compatible pricing semantics."""
    usage = usage if isinstance(usage, dict) else {}
    model_id = _model_id(model)
    catalog_rates = _CODEX_MODEL_RATES.get(model_id or "")
    input_tokens = _number(usage.get("input_tokens"))
    output_tokens = _number(usage.get("output_tokens"))
    if catalog_rates is None or input_tokens is None or output_tokens is None:
        return None

    cached_input_tokens = _number(usage.get("cached_input_tokens")) or 0
    cache_write_tokens = _number(usage.get("cache_write_input_tokens")) or 0
    non_cached_input_tokens = max(
        0,
        input_tokens - cached_input_tokens - cache_write_tokens,
    )
    input_volume = (
        non_cached_input_tokens + cached_input_tokens + cache_write_tokens
    )

    rates = catalog_rates
    matched_threshold: int | None = None
    for tier in catalog_rates.get("tiers", []):
        threshold = int(tier["input_tokens_above"])
        if input_volume > threshold and (
            matched_threshold is None or threshold > matched_threshold
        ):
            rates = tier
            matched_threshold = threshold

    multiplier = _service_tier_multiplier(model_id or "", service_tier)
    components = {
        "input": rates["input"] * non_cached_input_tokens / _TOKENS_PER_MILLION,
        "output": rates["output"] * output_tokens / _TOKENS_PER_MILLION,
        "cache_read": (
            rates["cache_read"] * cached_input_tokens / _TOKENS_PER_MILLION
        ),
        "cache_write": (
            rates["cache_write"] * cache_write_tokens / _TOKENS_PER_MILLION
        ),
    }
    components = {
        key: float(value) * multiplier for key, value in components.items()
    }
    return {
        "cost_usd": sum(components.values()),
        "model": model_id,
        "service_tier": service_tier or "default",
        "service_tier_multiplier": multiplier,
        "input_tokens": non_cached_input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "matched_input_tokens_above": matched_threshold,
        "components_usd": components,
    }
