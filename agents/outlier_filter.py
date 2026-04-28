"""Fund-return style outlier dimensions + composite outlier_score."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from agents.schemas_website import WebsiteFactsOutput, WebsiteScoresOutput
from agents.schemas_website_vc import (
    CompetitiveIntelligenceOutput,
    DistributionEngineOutput,
    OutlierFilterOutput,
    OutlierVerdict,
    RetentionModelOutput,
    RightToWinOutput,
    UnitEconomicsOutput,
)
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL

OUTLIER_WEIGHTS: dict[str, float] = {
    "market_size_outlier": 1.3,
    "category_leadership_potential": 1.5,
    "scalability": 1.2,
    "margin_profile": 0.9,
    "distribution_asymmetry": 1.5,
    "defensibility": 1.5,
    "fund_return_potential": 1.7,
}
_WSUM = sum(OUTLIER_WEIGHTS.values())


class OutlierLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market_size_outlier: float = 5.0
    market_size_reasoning: str = ""
    category_leadership_potential: float = 5.0
    category_leadership_reasoning: str = ""
    scalability: float = 5.0
    scalability_reasoning: str = ""
    margin_profile: float = 5.0
    margin_profile_reasoning: str = ""
    distribution_asymmetry: float = 5.0
    distribution_asymmetry_reasoning: str = ""
    defensibility: float = 5.0
    defensibility_reasoning: str = ""
    fund_return_potential: float = 5.0
    fund_return_reasoning: str = ""
    reasoning: str = ""
    must_validate_next: list[str] = Field(default_factory=list)


def _clamp(x: float) -> float:
    return max(0.0, min(10.0, float(x)))


def compile_outlier_filter_output(o: OutlierLLM) -> OutlierFilterOutput:
    """Deterministic outlier score + verdict from LLM-filled dimensions (no extra LLM call)."""
    vals = {
        "market_size_outlier": _clamp(o.market_size_outlier),
        "category_leadership_potential": _clamp(o.category_leadership_potential),
        "scalability": _clamp(o.scalability),
        "margin_profile": _clamp(o.margin_profile),
        "distribution_asymmetry": _clamp(o.distribution_asymmetry),
        "defensibility": _clamp(o.defensibility),
        "fund_return_potential": _clamp(o.fund_return_potential),
    }
    outlier_score = round(
        sum(vals[k] * OUTLIER_WEIGHTS[k] for k in OUTLIER_WEIGHTS) / _WSUM,
        2,
    )

    verdict: OutlierVerdict = "INTERESTING_BUT_NOT_OUTLIER_YET"
    # Website-only signal is structurally weak — relax thresholds vs deck-grade scoring.
    # Killing portfolio companies (e.g. Gralio at 5.76) for "outlier_gate_failed"
    # was the bug. Only kill on truly bad composite, never on single dim < 5.
    if outlier_score >= 8.0:
        verdict = "OUTLIER_POTENTIAL"
    elif outlier_score < 4.0:
        verdict = "NOT_VC_INVESTABLE"

    # No more single-dimension hard-kills on website-only signal.
    # Those rules belonged to deck-grade scoring with verified metrics.

    kills: list[str] = []
    if verdict == "NOT_VC_INVESTABLE":
        kills.append("outlier_gate_failed")

    # Build why_not_higher with reasoning-attached lines — NEVER bare numbers.
    def _line(label: str, val: float, reason: str) -> str:
        r = (reason or "").strip() or "no reasoning provided by model"
        return f"{label} = {val:.1f}/10 — {r}"

    why = [
        _line("Market size outlier",        vals["market_size_outlier"],          o.market_size_reasoning),
        _line("Category leadership potential", vals["category_leadership_potential"], o.category_leadership_reasoning),
        _line("Scalability",                vals["scalability"],                  o.scalability_reasoning),
        _line("Margin profile",             vals["margin_profile"],               o.margin_profile_reasoning),
        _line("Distribution asymmetry",     vals["distribution_asymmetry"],       o.distribution_asymmetry_reasoning),
        _line("Defensibility",              vals["defensibility"],                o.defensibility_reasoning),
        _line("Fund-return potential",      vals["fund_return_potential"],        o.fund_return_reasoning),
        f"Composite outlier_score = {outlier_score:.2f}/10 (weighted 7-dim) — {(o.reasoning or '').strip()[:400]}",
    ]

    return OutlierFilterOutput(
        market_size_outlier=vals["market_size_outlier"],
        category_leadership_potential=vals["category_leadership_potential"],
        scalability=vals["scalability"],
        margin_profile=vals["margin_profile"],
        distribution_asymmetry=vals["distribution_asymmetry"],
        defensibility=vals["defensibility"],
        fund_return_potential=vals["fund_return_potential"],
        outlier_score=outlier_score,
        outlier_verdict=verdict,
        reasoning=o.reasoning,
        must_validate_next=o.must_validate_next or [],
        kill_flags=kills,
        why_not_higher=why,
    )


def run_outlier_filter(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    scores: WebsiteScoresOutput,
    competitive: CompetitiveIntelligenceOutput,
    distribution: DistributionEngineOutput,
    unit_econ: UnitEconomicsOutput,
    retention: RetentionModelOutput,
    rtw: RightToWinOutput,
    model: str = OPENAI_MODEL,
) -> OutlierFilterOutput:
    dmap = {
        "traction": scores.traction_evidence.score,
        "market": scores.market_potential.score,
        "differentiation": scores.differentiation.score,
        "technical": scores.technical_depth_or_defensibility.score,
        "urgency": scores.urgency_and_budget_signal.score,
        "distribution_dim": scores.distribution_signal.score,
    }
    ctx = json.dumps(
        {
            "dims": dmap,
            "saturation": competitive.market_saturation_score,
            "parity": competitive.feature_parity_score,
            "positioning": competitive.relative_positioning,
            "distribution_score": distribution.distribution_score,
            "economic": unit_econ.economic_viability_score,
            "retention": retention.retention_structural_score,
            "rtw": rtw.right_to_win_score,
            "one_liner": (facts.one_liner or "")[:400],
        },
        ensure_ascii=False,
    )

    o = json_llm(
        client,
        system=(
            "VC outlier analyst. JSON only with floats 0-10: market_size_outlier, category_leadership_potential, "
            "scalability, margin_profile, distribution_asymmetry, defensibility, fund_return_potential, "
            "reasoning, must_validate_next[] short strings. High fund_return only if credible path to 50-100x from seed context."
        ),
        user=f"CONTEXT:\n{ctx}",
        model=model,
        max_tokens=1000,
        response_model=OutlierLLM,
    )

    return compile_outlier_filter_output(o)
