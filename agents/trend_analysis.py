"""Market timing / momentum heuristics + LLM narrative."""

from __future__ import annotations

import os
import json
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from agents.external_research import NullExternalResearchProvider, get_research_provider
from agents.schemas_website import WebsiteFactsOutput
from agents.schemas_website_vc import CompetitiveIntelligenceOutput, TrendAnalysisOutput, VCPackTrend
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL


class TrendLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trend_direction: str = "unclear"
    trend_velocity: str = "unclear"
    demand_drivers: list[str] = Field(default_factory=list)
    headwinds: list[str] = Field(default_factory=list)
    funding_activity: str = "unclear"


def _compute_timing_score(
    t: TrendLLM,
    *,
    market_saturation: float,
    ai_buzz_only: bool,
) -> float:
    score = 5.5
    d = (t.trend_direction or "").lower()
    v = (t.trend_velocity or "").lower()
    f = (t.funding_activity or "").lower()

    if d == "up" and v == "fast":
        score = 8.5
    elif d == "up":
        score = 7.2
    elif d == "flat" and f == "cold":
        score = 5.0
    elif d == "down":
        score = 3.2

    if market_saturation >= 8.0:
        score -= 1.0
    if ai_buzz_only:
        score -= 1.0
    return max(1.0, min(10.0, round(score, 2)))


def run_trend_analysis(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    competitive: CompetitiveIntelligenceOutput,
    model: str = OPENAI_MODEL,
) -> TrendAnalysisOutput:
    cat = competitive.category or facts.sector or "software"
    subs = competitive.subcategories or []
    blob = "\n".join(
        [
            facts.market_claims or "",
            facts.product_description or "",
            facts.target_customer or "",
        ]
    )[:8000]

    use_search = os.getenv("WEBSITE_VC_WEB_SEARCH", "0").strip().lower() in ("1", "true", "yes", "on")
    provider, search_live = get_research_provider() if use_search else (NullExternalResearchProvider(), False)
    evidence: list[dict[str, Any]] = []
    queries = [
        f"{cat} market growth 2026",
        f"{cat} venture funding startups",
        f"{subs[0] if subs else cat} demand growth",
    ]
    for q in queries[:3]:
        try:
            for s in provider.search(q, max_results=2):
                evidence.append(
                    {
                        "query": q,
                        "title": getattr(s, "title", ""),
                        "url": getattr(s, "url", ""),
                        "snippet": (getattr(s, "snippet", None) or "")[:500],
                    }
                )
        except Exception:
            pass

    ev_text = json.dumps(evidence[:6], ensure_ascii=False)[:6000]
    ctx_note = (
        "Note: no live web-search snippets (OpenAI-only / no Tavily). Infer cautiously from facts; "
        "do not invent statistics."
        if not search_live
        else "Use facts + snippets; do not invent statistics."
    )
    t = json_llm(
        client,
        system=(
            "VC timing analyst. JSON only (TrendLLM): trend_direction up|flat|down|unclear, "
            "trend_velocity fast|medium|slow|unclear, demand_drivers[], headwinds[], "
            "funding_activity hot|active|cold|unclear. "
            + ctx_note
        ),
        user=f"Category: {cat}\nSubcategories: {subs}\n\nFACTS:\n{blob}\n\nSNIPPETS:\n{ev_text}",
        model=model,
        max_tokens=800,
        response_model=TrendLLM,
    )

    ai_buzz = "ai" in blob.lower() and "data" not in blob.lower() and "workflow" not in blob.lower()
    timing = _compute_timing_score(
        t,
        market_saturation=float(competitive.market_saturation_score),
        ai_buzz_only=ai_buzz,
    )

    kills: list[str] = []
    if t.trend_direction == "unclear" and t.funding_activity == "cold":
        kills.append("no_clear_why_now")
    if competitive.market_saturation_score >= 8 and t.trend_velocity != "fast":
        kills.append("mature_market_without_timing_shift")
    if "trillion" in blob.lower() or "billion" in blob.lower():
        kills.append("trend_claim_not_supported")
    if t.funding_activity == "cold":
        kills.append("funding_market_cold")

    why = [
        f"Inferred timing_score={timing} from direction={t.trend_direction}, velocity={t.trend_velocity}, funding={t.funding_activity}.",
    ]

    td = t.trend_direction if t.trend_direction in ("up", "flat", "down", "unclear") else "unclear"
    tv = t.trend_velocity if t.trend_velocity in ("fast", "medium", "slow", "unclear") else "unclear"
    ff = t.funding_activity if t.funding_activity in ("hot", "active", "cold", "unclear") else "unclear"
    return TrendAnalysisOutput(
        trend_direction=td,  # type: ignore[arg-type]
        trend_velocity=tv,  # type: ignore[arg-type]
        demand_drivers=t.demand_drivers or [],
        headwinds=t.headwinds or [],
        funding_activity=ff,  # type: ignore[arg-type]
        timing_score=timing,
        evidence=evidence,
        kill_flags=kills,
        why_not_higher=why,
    )


def assemble_trend_from_vcpack(
    pt: VCPackTrend,
    *,
    digest_text: str,
    market_saturation: float,
) -> TrendAnalysisOutput:
    """Build TrendAnalysisOutput from batched VC pack (no extra LLM / Tavily)."""
    t = TrendLLM(
        trend_direction=pt.trend_direction,
        trend_velocity=pt.trend_velocity,
        demand_drivers=pt.demand_drivers or [],
        headwinds=pt.headwinds or [],
        funding_activity=pt.funding_activity,
    )
    blob = (digest_text or "").lower()
    ai_buzz = "ai" in blob and "data" not in blob and "workflow" not in blob
    # Prefer LLM-provided timing score (with reasoning) when given; else infer from heuristic.
    if pt.timing_score and pt.timing_score > 0:
        timing = max(1.0, min(10.0, round(float(pt.timing_score), 2)))
    else:
        timing = _compute_timing_score(t, market_saturation=float(market_saturation), ai_buzz_only=ai_buzz)

    kills: list[str] = []
    if t.trend_direction == "unclear" and t.funding_activity == "cold":
        kills.append("no_clear_why_now")
    if market_saturation >= 8 and t.trend_velocity != "fast":
        kills.append("mature_market_without_timing_shift")
    if "trillion" in blob or "billion" in blob:
        kills.append("trend_claim_not_supported")
    if t.funding_activity == "cold":
        kills.append("funding_market_cold")

    td = t.trend_direction if t.trend_direction in ("up", "flat", "down", "unclear") else "unclear"
    tv = t.trend_velocity if t.trend_velocity in ("fast", "medium", "slow", "unclear") else "unclear"
    ff = t.funding_activity if t.funding_activity in ("hot", "active", "cold", "unclear") else "unclear"
    drivers = ", ".join((pt.demand_drivers or [])[:3]) or "no driver listed"
    headw = ", ".join((pt.headwinds or [])[:3]) or "none surfaced"
    llm_reason = (pt.timing_reasoning or "").strip()
    if llm_reason:
        why_line = (
            f"Timing = {timing:.1f}/10 — direction={td}, velocity={tv}, funding={ff}; "
            f"drivers: {drivers}; headwinds: {headw}. Why: {llm_reason}"
        )
    else:
        why_line = (
            f"Timing = {timing:.1f}/10 — direction={td}, velocity={tv}, funding={ff}; "
            f"drivers: {drivers}; headwinds: {headw}. (No model rationale given — timing inferred from heuristic.)"
        )
    why = [why_line]
    return TrendAnalysisOutput(
        trend_direction=td,  # type: ignore[arg-type]
        trend_velocity=tv,  # type: ignore[arg-type]
        demand_drivers=t.demand_drivers or [],
        headwinds=t.headwinds or [],
        funding_activity=ff,  # type: ignore[arg-type]
        timing_score=timing,
        evidence=[],
        kill_flags=kills,
        why_not_higher=why,
    )
