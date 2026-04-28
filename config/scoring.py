"""Deterministic weighted overall score — LLM returns per-dimension scores only."""

from __future__ import annotations

from typing import Optional

WEIGHTS: dict[str, float] = {
    "timing": 1.2,
    "problem": 1.0,
    "wedge": 1.0,
    "founder_market_fit": 1.3,
    "product_love": 1.0,
    "execution_speed": 1.0,
    "market": 1.0,
    "moat_path": 1.0,
    "traction": 1.5,
    "business_model": 1.0,
    "distribution": 1.5,
}

_WEIGHT_SUM = sum(WEIGHTS.values())


def calculate_overall_score(scores: dict[str, int]) -> float:
    """Weighted mean over WEIGHTS keys. Missing keys treated as 0."""
    total = sum(scores.get(k, 0) * w for k, w in WEIGHTS.items())
    return round(total / _WEIGHT_SUM, 2)


def apply_outlier_adjustment(overall: float, dim_scores: dict[str, int]) -> tuple[float, Optional[str]]:
    """
    Avoid passing deals that are uniformly mediocre on market+traction+distribution,
    or reward clear outlier pattern (strong market + strong traction or distribution).
    """
    m = int(dim_scores.get("market", 0))
    t = int(dim_scores.get("traction", 0))
    d = int(dim_scores.get("distribution", 0))
    if m < 7 and t < 7 and d < 7:
        capped = min(overall, 6.0)
        if capped + 1e-6 < overall:
            return round(capped, 2), "outlier_gate:all_market_traction_distribution_below_7_capped_at_6"
    if m >= 8 and (t >= 7 or d >= 7):
        boosted = min(10.0, overall + 0.5)
        if boosted > overall + 1e-6:
            return round(boosted, 2), "outlier_gate:strong_market_plus_traction_or_distribution_boost_0_5"
    return overall, None
