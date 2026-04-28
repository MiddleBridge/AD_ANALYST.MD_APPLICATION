"""Distribution model detection + channel-market fit heuristics."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from agents.schemas_website import WebsiteFactsOutput
from agents.schemas_website_vc import (
    ChannelAssessment,
    CompetitiveIntelligenceOutput,
    DistributionEngineOutput,
)
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL


class DistLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_channels: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def _low_ticket_consumer(facts: WebsiteFactsOutput) -> bool:
    tc = (facts.target_customer or "").lower()
    pr = (facts.pricing_signals or "").lower()
    consumer = any(x in tc for x in ("consumer", "student", "prosumer", "b2c", "learner"))
    low = bool(re.search(r"\b(\$|€|£)\s*[0-9]{1,2}\s*/\s*(mo|month|wk|week)\b", pr)) or bool(
        re.search(r"\b([0-9]{1,2})\s*(usd|eur)?\s*/\s*month\b", pr)
    )
    sub = "subscription" in pr or "month" in pr or "week" in pr
    return consumer and (low or sub)


def _has_organic(channels: list[str], facts: WebsiteFactsOutput) -> bool:
    ch = " ".join(channels).lower()
    blob = (
        (facts.traction_signals or "")
        + (facts.blog_content_velocity or "")
        + (facts.customer_proof or "")
    ).lower()
    if any(x in ch for x in ("seo", "product_led_growth", "plg", "viral", "community")):
        return True
    if "organic" in blob or "seo" in blob or "referral" in blob:
        return True
    return False


def _channel_assessments(channels: list[str], facts: WebsiteFactsOutput) -> list[ChannelAssessment]:
    out: list[ChannelAssessment] = []
    blob = ((facts.product_description or "") + (facts.traction_signals or "")).lower()
    for ch in channels[:8]:
        ev = "none"
        if ch and ch.replace("_", " ") in blob:
            ev = "medium"
        elif ch in ("unknown",):
            ev = "none"
        else:
            ev = "weak"
        paid = ch in ("paid_ads",)
        scal = 7.0 if ch in ("product_led_growth", "seo", "viral", "community") else (4.0 if paid else 5.0)
        comp = 8.0 if paid else 5.0
        cac = 8.0 if paid else 4.0
        out.append(
            ChannelAssessment(
                channel=ch,
                evidence_strength=ev,  # type: ignore[arg-type]
                scalability=scal,
                likely_competition=comp,
                cac_risk=cac,
                notes="",
            )
        )
    return out


def _distribution_score_from_assessments(assessments: list[ChannelAssessment]) -> float:
    if not assessments:
        return 4.5
    best = max(
        (
            a.scalability * 0.5
            + (8.0 if a.evidence_strength == "strong" else 6.0 if a.evidence_strength == "medium" else 3.0)
            * 0.5
            - a.cac_risk * 0.15
        )
        for a in assessments
    )
    return max(1.0, min(10.0, round(best, 2)))


def assemble_distribution_from_pack(
    *,
    primary_channels: list[str],
    dist_evidence: list[str],
    dist_missing: list[str],
    distribution_score: float,
    distribution_risk_score: float,
    likely_cac_pressure: str,
    facts: WebsiteFactsOutput,
    competitive: CompetitiveIntelligenceOutput,
    distribution_score_reasoning: str = "",
    distribution_risk_reasoning: str = "",
) -> DistributionEngineOutput:
    """Build distribution output from batched VC pack (no extra LLM)."""
    channels = primary_channels or ["unknown"]
    assessments = _channel_assessments(channels, facts)
    ds = max(1.0, min(10.0, float(distribution_score)))
    risk = max(0.0, min(10.0, float(distribution_risk_score)))
    if competitive.market_saturation_score >= 7:
        risk = min(10.0, risk + 0.5)

    cac_p: Any = likely_cac_pressure if likely_cac_pressure in ("low", "medium", "high", "unclear") else "unclear"
    if "paid_ads" in channels and competitive.market_saturation_score >= 6:
        cac_p = "high"
    elif _has_organic(channels, facts):
        cac_p = "low"

    kills: list[str] = []
    if _low_ticket_consumer(facts) and not _has_organic(channels, facts):
        kills.append("low_ticket_b2c_no_obvious_distribution")

    ev_short = "; ".join((dist_evidence or [])[:3]) or "no on-site evidence"
    score_line = f"Distribution = {ds:.1f}/10 — primary channels: {', '.join(channels)}; evidence: {ev_short}."
    if distribution_score_reasoning.strip():
        score_line += f" Why: {distribution_score_reasoning.strip()}"
    risk_line = (
        f"Distribution risk = {risk:.1f}/10 — CAC pressure {cac_p}, saturation {competitive.market_saturation_score:.1f}/10."
    )
    if distribution_risk_reasoning.strip():
        risk_line += f" Why: {distribution_risk_reasoning.strip()}"
    why = [score_line, risk_line]
    if kills:
        why.append("Distribution kill: low-ticket motion without obvious organic/PLG/viral proof.")

    return DistributionEngineOutput(
        primary_channels=channels,
        channel_assessments=assessments,
        distribution_score=ds,
        distribution_risk_score=risk,
        likely_cac_pressure=cac_p,  # type: ignore[arg-type]
        evidence=[{"kind": "vc_pack", "lines": (dist_evidence or [])[:12]}],
        missing_data=dist_missing or [],
        kill_flags=kills,
        why_not_higher=why,
    )


def run_distribution_engine(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    competitive: CompetitiveIntelligenceOutput,
    model: str = OPENAI_MODEL,
) -> DistributionEngineOutput:
    fj = json.dumps(facts.model_dump(), ensure_ascii=False)[:10000]
    d = json_llm(
        client,
        system=(
            "Classify GTM. JSON only: primary_channels[] from "
            "[SEO,paid_ads,app_store_search,product_led_growth,viral,sales_led,partnerships,community,marketplace,unknown], "
            "evidence[] strings from facts, missing[] gaps."
        ),
        user=f"FACTS JSON:\n{fj}\n\nCategory: {competitive.category}",
        model=model,
        max_tokens=700,
        response_model=DistLLM,
    )
    channels = d.primary_channels or ["unknown"]
    assessments = _channel_assessments(channels, facts)
    dist_score = _distribution_score_from_assessments(assessments)
    risk = 5.0
    if competitive.market_saturation_score >= 7:
        risk += 1.5
    if "paid_ads" in channels and _low_ticket_consumer(facts):
        risk += 2.0
    risk = max(0.0, min(10.0, round(risk, 2)))

    cac_p: Any = "medium"
    if "paid_ads" in channels and competitive.market_saturation_score >= 6:
        cac_p = "high"
    elif _has_organic(channels, facts):
        cac_p = "low"

    kills: list[str] = []
    if _low_ticket_consumer(facts) and not _has_organic(channels, facts):
        kills.append("low_ticket_b2c_no_obvious_distribution")

    why = [
        f"Primary channels: {', '.join(channels)}.",
        f"Saturation context: {competitive.market_saturation_score}/10.",
    ]
    if kills:
        why.append("Distribution risk: low-ticket motion without obvious organic/PLG/viral proof.")

    return DistributionEngineOutput(
        primary_channels=channels,
        channel_assessments=assessments,
        distribution_score=dist_score,
        distribution_risk_score=risk,
        likely_cac_pressure=cac_p,  # type: ignore[arg-type]
        evidence=[{"kind": "llm", "lines": d.evidence[:8]}],
        missing_data=d.missing or [],
        kill_flags=kills,
        why_not_higher=why,
    )
