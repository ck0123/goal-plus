from __future__ import annotations

from typing import Any


DEFAULT_TIERS = [
    "AnySearchAgentFlash",
    "AnySearchAgent",
    "AnySearchAgentDeep",
    "AnySearchAgentExtraDeep",
]


class AdaptEvolveStrategy:
    """Evolutionary planner with adaptive OpenCode worker-tier allocation.

    The original AdaptEvolve signal is token confidence. This runtime does not
    own model logits, so the plugin uses MCP-observable confidence proxies:
    verified score, process pass/fail, failure classes, and whether the run has
    any usable parent candidate.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        tiers = self.config.get("tiers") or DEFAULT_TIERS
        if not isinstance(tiers, list) or not tiers:
            raise ValueError("AdaptEvolveStrategy config.tiers must be a non-empty list")
        self.tiers = [str(tier) for tier in tiers]

    def plan_next(self, payload: dict[str, Any]) -> dict[str, Any]:
        planned_k = int(payload["planned_k"])
        history = payload.get("history", {})
        candidates = list(history.get("candidates", []))
        scored = [candidate for candidate in candidates if candidate.get("score") is not None]
        selected_tier, confidence = self._select_worker_tier(history, scored)
        worker_policy = self._worker_policy(selected_tier, confidence["reason"])

        if not scored:
            work_orders = [
                self._bootstrap_work_order(slot, selected_tier, confidence)
                for slot in range(1, planned_k + 1)
            ]
            return {
                "requires_agent_proposals": False,
                "official_history": history,
                "derivation_policy": {
                    "base_workspace_source": "source",
                    "must_derive_from": [],
                    "may_reference": [],
                },
                "worker_policy": worker_policy,
                "work_orders": work_orders,
                "strategy_trace": {
                    "selection_rule": "adaptevolve bootstrap",
                    "selected_worker_agent_type": selected_tier,
                    "confidence_signal": confidence,
                    "reason": "No scored parent exists yet, so AdaptEvolve starts cheaply from source.",
                },
            }

        parent = self._best_candidate(history, scored)
        parent_id = str(parent["candidate_id"])
        inspiration_ids = [
            str(candidate["candidate_id"])
            for candidate in self._ranked_candidates(history, scored)
            if candidate.get("candidate_id") != parent_id
        ][: max(0, int(self.config.get("max_inspirations", 3)))]
        work_orders = [
            self._mutation_work_order(
                slot,
                parent_id,
                inspiration_ids,
                selected_tier,
                confidence,
                parent,
            )
            for slot in range(1, planned_k + 1)
        ]

        return {
            "requires_agent_proposals": False,
            "official_history": history,
            "derivation_policy": {
                "base_workspace_source": f"candidate:{parent_id}",
                "must_derive_from": [parent_id],
                "may_reference": inspiration_ids,
            },
            "worker_policy": worker_policy,
            "work_orders": work_orders,
            "strategy_trace": {
                "selection_rule": "adaptevolve mutate best parent",
                "parent_candidate_id": parent_id,
                "inspiration_candidate_ids": inspiration_ids,
                "selected_worker_agent_type": selected_tier,
                "confidence_signal": confidence,
                "reason": (
                    "AdaptEvolve reuses evolve-mode parent selection while routing the "
                    "next worker tier from MCP-observable confidence proxies."
                ),
            },
        }

    def _select_worker_tier(
        self,
        history: dict[str, Any],
        scored: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        if not scored:
            return self.tiers[0], {
                "phase": "bootstrap",
                "best_score": None,
                "failure_count": 0,
                "reason": "no scored candidates",
            }

        ranked = self._ranked_candidates(history, scored)
        best = ranked[0]
        best_score = float(best["score"])
        failure_count = sum(1 for candidate in scored if self._has_failure(candidate))
        low_score_threshold = float(self.config.get("low_score_threshold", 0.2))
        high_score_threshold = float(self.config.get("high_score_threshold", 0.8))

        if failure_count >= int(self.config.get("extra_deep_failure_count", 2)):
            return self._named_or_last("AnySearchAgentExtraDeep"), {
                "phase": "repeated_failure",
                "best_score": best_score,
                "failure_count": failure_count,
                "reason": "multiple failed verified candidates",
            }
        if self._is_low_confidence(best, best_score, low_score_threshold):
            return self._named_or_index("AnySearchAgentDeep", 1), {
                "phase": "low_confidence",
                "best_score": best_score,
                "failure_count": failure_count,
                "threshold": low_score_threshold,
                "reason": "best verified score is below low-score threshold",
            }
        if self._is_high_confidence(best, best_score, high_score_threshold):
            return self.tiers[0], {
                "phase": "high_confidence",
                "best_score": best_score,
                "failure_count": failure_count,
                "threshold": high_score_threshold,
                "reason": "best verified score is above high-score threshold",
            }
        return self._named_or_index("AnySearchAgent", 1), {
            "phase": "medium_confidence",
            "best_score": best_score,
            "failure_count": failure_count,
            "reason": "score is usable but not yet high-confidence",
        }

    def _best_candidate(
        self,
        history: dict[str, Any],
        scored: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._ranked_candidates(history, scored)[0]

    def _ranked_candidates(
        self,
        history: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        maximize = history.get("metric_direction", "maximize") == "maximize"
        return sorted(
            candidates,
            key=lambda candidate: float(candidate["score"]),
            reverse=maximize,
        )

    def _has_failure(self, candidate: dict[str, Any]) -> bool:
        if candidate.get("process_passed") is False:
            return True
        return bool(candidate.get("failure_classes"))

    def _is_low_confidence(
        self,
        candidate: dict[str, Any],
        score: float,
        threshold: float,
    ) -> bool:
        if self._has_failure(candidate):
            return True
        return score < threshold

    def _is_high_confidence(
        self,
        candidate: dict[str, Any],
        score: float,
        threshold: float,
    ) -> bool:
        return not self._has_failure(candidate) and score >= threshold

    def _named_or_index(self, preferred: str, fallback_index: int) -> str:
        if preferred in self.tiers:
            return preferred
        return self.tiers[min(fallback_index, len(self.tiers) - 1)]

    def _named_or_last(self, preferred: str) -> str:
        if preferred in self.tiers:
            return preferred
        return self.tiers[-1]

    def _worker_policy(self, selected_tier: str, reason: str) -> dict[str, Any]:
        return {
            "mode": "agent-session-pool",
            "worker_agent_type": selected_tier,
            "subagent_type": selected_tier,
            "requires_agent_session": True,
            "direct_edit_allowed": False,
            "dynamic_allocation": "adaptevolve",
            "allocation_reason": reason,
        }

    def _bootstrap_work_order(
        self,
        slot: int,
        selected_tier: str,
        confidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "slot": slot,
            "intent": (
                "AdaptEvolve bootstrap: start from source with a cheap worker and "
                "produce one verified candidate."
            ),
            "hypothesis": f"AdaptEvolve bootstrap slot {slot}",
            "instructions": [
                f"AdaptEvolve selected worker tier: {selected_tier}.",
                "Use the first pass to expose verifier feedback, not to exhaustively search.",
            ],
            "metadata": self._metadata(selected_tier, confidence),
        }

    def _mutation_work_order(
        self,
        slot: int,
        parent_id: str,
        inspiration_ids: list[str],
        selected_tier: str,
        confidence: dict[str, Any],
        parent: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "slot": slot,
            "base_candidate_id": parent_id,
            "parent_candidate_ids": [parent_id],
            "inspiration_candidate_ids": inspiration_ids,
            "intent": (
                f"AdaptEvolve mutation from `{parent_id}` with worker tier "
                f"`{selected_tier}`; target the weakest verified signal first."
            ),
            "hypothesis": f"AdaptEvolve mutation from {parent_id} slot {slot}",
            "instructions": [
                f"AdaptEvolve selected worker tier: {selected_tier}.",
                f"Start from parent {parent_id} with score {parent.get('score')}.",
                "Preserve any improvement from the parent; mutate one concrete mechanism.",
            ],
            "must_derive_from": [parent_id],
            "metadata": self._metadata(selected_tier, confidence),
        }

    def _metadata(self, selected_tier: str, confidence: dict[str, Any]) -> dict[str, Any]:
        return {
            "strategy": "adaptevolve",
            "selected_worker_agent_type": selected_tier,
            "adaptevolve_confidence_signal": confidence,
        }

