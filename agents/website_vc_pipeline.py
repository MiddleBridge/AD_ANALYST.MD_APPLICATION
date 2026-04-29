"""Orchestrate VC modules → quality_score, vc_score, caps, final verdict, WebsiteVCFinalOutput."""

from __future__ import annotations

import re
from typing import Optional

from openai import OpenAI

from agents.competitive_intelligence import (
    fetch_category_osint_bundle,
    finalize_competitive_intelligence,
)
from agents.schemas_website import WebsiteFactsOutput, WebsiteScoresOutput
from agents.schemas_website_vc import (
    OutlierVerdict,
    ValidationQuestion,
    WebsiteFinalVerdict,
    WebsiteVCFinalOutput,
)
from agents.unit_economics import run_unit_economics
from agents.website_vc_facts_digest import build_vc_facts_digest
from agents.website_vc_pack import assemble_vc_sub_outputs, run_vc_pack_llm
from config.llm_cost import OPENAI_MODEL

QUALITY_KEYS = (
    "problem_clarity",
    "product_clarity",
    "target_customer_clarity",
    "customer_proof",
    "business_model_clarity",
    "traction_evidence",
)

VC_WEIGHTS: dict[str, float] = {
    "traction_evidence": 1.3,
    "urgency_and_budget_signal": 1.1,
    "market_potential": 1.2,
    "competitive_position_score": 1.6,
    "market_timing_score": 1.2,
    "distribution_score": 1.5,
    "economic_viability_score": 1.5,
    "retention_structural_score": 1.4,
    "right_to_win_score": 1.7,
    "outlier_score": 2.0,
}
_VC_SUM = sum(VC_WEIGHTS.values())


def calculate_quality_score(scores: WebsiteScoresOutput) -> float:
    total = sum(int(getattr(scores, k).score) for k in QUALITY_KEYS)
    return round(total / len(QUALITY_KEYS), 2)


def calculate_vc_score(
    *,
    scores: WebsiteScoresOutput,
    competitive_position_score: float,
    market_timing_score: float,
    distribution_score: float,
    economic_viability_score: float,
    retention_structural_score: float,
    right_to_win_score: float,
    outlier_score: float,
) -> float:
    parts = {
        "traction_evidence": int(scores.traction_evidence.score),
        "urgency_and_budget_signal": int(scores.urgency_and_budget_signal.score),
        "market_potential": int(scores.market_potential.score),
        "competitive_position_score": competitive_position_score,
        "market_timing_score": market_timing_score,
        "distribution_score": distribution_score,
        "economic_viability_score": economic_viability_score,
        "retention_structural_score": retention_structural_score,
        "right_to_win_score": right_to_win_score,
        "outlier_score": outlier_score,
    }
    total = sum(parts[k] * VC_WEIGHTS[k] for k in VC_WEIGHTS)
    return round(total / _VC_SUM, 2)


def _min_cap(cur: Optional[float], val: float) -> float:
    if cur is None:
        return val
    return min(cur, val)


def apply_vc_investment_caps(
    vc: float,
    *,
    ci,
    dist,
    ret,
    rtw,
    ue,
) -> tuple[float, list[str]]:
    cap: Optional[float] = None
    reasons: list[str] = []
    if "crowded_market_no_clear_edge" in (ci.kill_flags or []) or (
        float(ci.market_saturation_score) >= 7.0 and not (ci.differentiation_summary or "").strip()
    ):
        cap = _min_cap(cap, 6.5)
        reasons.append("vc_cap:crowded_market_no_clear_edge<=6.5")
    if "low_ticket_b2c_no_obvious_distribution" in (dist.kill_flags or []):
        cap = _min_cap(cap, 6.2)
        reasons.append("vc_cap:low_ticket_b2c_no_organic<=6.2")
    if "single_use_product_high_churn" in (ret.kill_flags or []) and "no_expansion_path" in (
        ret.kill_flags or []
    ):
        cap = _min_cap(cap, 6.3)
        reasons.append("vc_cap:single_use_no_expansion<=6.3")
    if "ai_as_only_differentiation" in (rtw.kill_flags or []) and "no_proprietary_edge" in (rtw.kill_flags or []):
        cap = _min_cap(cap, 6.5)
        reasons.append("vc_cap:ai_only_no_proprietary<=6.5")
    if "paid_ads" in (dist.primary_channels or []) and ue.monthly_price_estimate is not None:
        if ue.monthly_price_estimate < 20:
            cap = _min_cap(cap, 6.5)
            reasons.append("vc_cap:low_price_paid_channel<=6.5")
    if float(ci.market_saturation_score) >= 7.0 and float(dist.distribution_score) < 5.0:
        cap = _min_cap(cap, 6.0)
        reasons.append("vc_cap:no_distribution_evidence_crowded<=6.0")
    if cap is None:
        return vc, reasons
    out = round(min(vc, cap), 2)
    return out, reasons


def _has_revenue_or_hard_traction(facts: WebsiteFactsOutput) -> bool:
    blob = (
        (facts.traction_signals or "")
        + " "
        + (facts.customer_proof or "")
        + " "
        + (facts.pricing_signals or "")
    ).lower()
    return bool(
        re.search(r"\b(mrr|arr|revenue|paying customer|paid user|\$[0-9]{2,}|€[0-9]{2,})\b", blob)
    )


def resolve_vc_final_verdict(
    *,
    gate1_fail: bool,
    outlier_verdict: OutlierVerdict,
    vc_score: float,
    quality_score: float,
    facts: WebsiteFactsOutput,
) -> WebsiteFinalVerdict:
    if gate1_fail:
        return "REJECT_AUTO"
    if quality_score <= 1.5 and vc_score < 4.5:
        return "REJECT_AUTO"
    if outlier_verdict == "NOT_VC_INVESTABLE" and vc_score < 7.5:
        return "REJECT_OR_LOW_PRIORITY"
    v = vc_score
    q = quality_score
    verdict: WebsiteFinalVerdict
    if v >= 8.3 and outlier_verdict == "OUTLIER_POTENTIAL":
        verdict = "STRONG_SIGNAL"
    elif v >= 7.5:
        verdict = "PASS_TO_HITL"
    elif v >= 6.5:
        verdict = "NEEDS_FOUNDER_CALL"
    elif q >= 7.5 and v < 6.5:
        verdict = "GOOD_COMPANY_NOT_OBVIOUS_VC"
    elif v >= 5.0:
        verdict = "NEEDS_DECK"
    else:
        verdict = "REJECT_AUTO"

    if verdict == "STRONG_SIGNAL" and not _has_revenue_or_hard_traction(facts):
        verdict = "PASS_TO_HITL"
    return verdict


def _validation_questions(
    facts: WebsiteFactsOutput,
    ue,
    dist,
    ret,
    outlier,
) -> list[ValidationQuestion]:
    out: list[ValidationQuestion] = []
    for s in outlier.must_validate_next[:6]:
        out.append(
            ValidationQuestion(
                topic="Follow-up",
                question=s,
                why_it_matters="Reduces uncertainty on VC-scale outcome.",
            )
        )
    if not _has_revenue_or_hard_traction(facts):
        out.append(
            ValidationQuestion(
                topic="Revenue quality",
                question="What is current MRR/ARR and MoM growth?",
                why_it_matters="Public site rarely proves paid conversion at scale.",
            )
        )
    if "no_expansion_path" in (ret.kill_flags or []):
        out.append(
            ValidationQuestion(
                topic="Retention",
                question="What is cohort retention after primary use-case completes?",
                why_it_matters="Structural churn caps VC returns for episodic products.",
            )
        )
    if dist.likely_cac_pressure in ("high", "medium"):
        out.append(
            ValidationQuestion(
                topic="Distribution",
                question="What % of users from organic vs paid channels? Blended CAC and payback?",
                why_it_matters="Low-ticket B2C fails if economics are paid-driven without LTV proof.",
            )
        )
    return out[:12]


def _next_step_vc(v: WebsiteFinalVerdict) -> str:
    return {
        "REJECT_AUTO": "Pass — not a fit from public website + VC layer.",
        "REJECT_OR_LOW_PRIORITY": "Low priority vs fund mandate; optional deck request only if strategic.",
        "NEEDS_DECK": "Request pitch deck and light data room; validate VC-scale economics.",
        "NEEDS_FOUNDER_CALL": "Founder call + deck; validate ICP, CAC, retention, and right-to-win.",
        "PASS_TO_HITL": "Route to partner HITL with VC diligence summary.",
        "STRONG_SIGNAL": "Fast-track partner review; confirm non-public metrics and moat on call.",
        "GOOD_COMPANY_NOT_OBVIOUS_VC": "Good operator clarity; not yet fund-return shaped — nurture or pass politely.",
    }.get(v, "Review manually.")


def build_website_vc_final(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    scores: WebsiteScoresOutput,
    combined_markdown: str,
    website_url: str,
    raw_twelve_dim_score: float,
    capped_twelve_dim_score: float,
    gate1_verdict: str,
    gate1_fail: bool,
    top_strengths: list[str],
    top_concerns: list[str],
    kill_flags_base: list[str],
    cap_reasons_twelve: list[str],
    model: str = OPENAI_MODEL,
) -> WebsiteVCFinalOutput:
    if gate1_fail:
        return WebsiteVCFinalOutput(
            company_name=facts.company_name or "",
            website_url=website_url,
            gate1_verdict=gate1_verdict,
            quality_score=0.0,
            vc_score=0.0,
            raw_website_score=raw_twelve_dim_score,
            capped_website_score=capped_twelve_dim_score,
            final_verdict="REJECT_AUTO",
            existing_12_scores=scores,
            top_strengths=[],
            top_concerns=top_concerns,
            kill_flags=list(kill_flags_base),
            cap_reasons=list(cap_reasons_twelve),
            recommended_next_step=_next_step_vc("REJECT_AUTO"),
        )

    digest = build_vc_facts_digest(facts)
    bundle = fetch_category_osint_bundle(
        client,
        facts=facts,
        combined_markdown=combined_markdown,
        website_url=website_url,
        model=model,
    )
    category = bundle.cat.category or facts.sector or "software"
    pack = run_vc_pack_llm(
        client,
        digest=digest,
        bundle=bundle,
        scores=scores,
        market_saturation=bundle.saturation,
        category=category,
        model=model,
    )
    ci, comp_pos = finalize_competitive_intelligence(bundle, pack.feature_parity)
    trend, dist, ret, rtw, out = assemble_vc_sub_outputs(
        pack,
        digest=digest,
        facts=facts,
        scores=scores,
        ci=ci,
    )
    ue = run_unit_economics(
        facts=facts,
        distribution=dist,
        retention=ret,
        category=ci.category or facts.sector or "",
    )

    quality = calculate_quality_score(scores)
    vc_raw = calculate_vc_score(
        scores=scores,
        competitive_position_score=comp_pos,
        market_timing_score=float(trend.timing_score),
        distribution_score=float(dist.distribution_score),
        economic_viability_score=float(ue.economic_viability_score),
        retention_structural_score=float(ret.retention_structural_score),
        right_to_win_score=float(rtw.right_to_win_score),
        outlier_score=float(out.outlier_score),
    )
    vc_capped, vc_cap_reasons = apply_vc_investment_caps(vc_raw, ci=ci, dist=dist, ret=ret, rtw=rtw, ue=ue)

    verdict = resolve_vc_final_verdict(
        gate1_fail=False,
        outlier_verdict=out.outlier_verdict,
        vc_score=vc_capped,
        quality_score=quality,
        facts=facts,
    )

    kills = list(kill_flags_base)
    for bucket in (ci, trend, dist, ue, ret, rtw, out):
        for k in getattr(bucket, "kill_flags", None) or []:
            if k and k not in kills:
                kills.append(k)

    why = []
    for part in (ci, trend, dist, ue, ret, rtw, out):
        why.extend(getattr(part, "why_not_higher", None) or [])
    why = why[:25]

    must = _validation_questions(facts, ue, dist, ret, out)

    all_cap_reasons = list(cap_reasons_twelve) + list(vc_cap_reasons)

    return WebsiteVCFinalOutput(
        company_name=facts.company_name or "",
        website_url=website_url,
        gate1_verdict=gate1_verdict,
        quality_score=quality,
        vc_score=vc_capped,
        raw_website_score=raw_twelve_dim_score,
        capped_website_score=capped_twelve_dim_score,
        final_verdict=verdict,
        existing_12_scores=scores,
        competitive_intelligence=ci,
        trend_analysis=trend,
        distribution_engine=dist,
        unit_economics=ue,
        retention_model=ret,
        right_to_win=rtw,
        outlier_filter=out,
        top_strengths=top_strengths[:3],
        top_concerns=top_concerns[:3],
        kill_flags=kills[:40],
        cap_reasons=all_cap_reasons,
        why_not_higher=why,
        must_validate_next=must,
        recommended_next_step=_next_step_vc(verdict),
        vc_cap_reasons=vc_cap_reasons,
    )
