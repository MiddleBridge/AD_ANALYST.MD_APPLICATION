"""Deterministic external score, final score, hard caps, kill flags."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

ScreeningMode = Literal["deck", "website"]

from agents.schemas_gate25 import (
    ExternalMarketCheckResult,
    FinalInvestmentDecision,
    FinalReco,
    FinalVerdict,
    KillFlag,
)
from storage.models import Gate1Result, Gate2Result, ScoredDimension

EXTERNAL_WEIGHT_SUM = 11.0
EXTERNAL_WEIGHTS: dict[str, float] = {
    "right_to_win_score": 1.5,
    "competitive_position_score": 1.4,
    "distribution_feasibility_score": 1.4,
    "market_saturation_score": 1.2,
    "incumbent_risk_score": 1.2,
    "cac_viability_score": 1.2,
    "switching_trigger_score": 1.1,
    "trend_validity_score": 1.0,
    "regulatory_platform_risk_score": 1.0,
}


def compute_external_weighted_score(scores: dict[str, int]) -> float:
    total = sum(scores[k] * EXTERNAL_WEIGHTS[k] for k in EXTERNAL_WEIGHTS)
    return round(total / EXTERNAL_WEIGHT_SUM, 2)


def compute_risk_penalty(kill_flags: list[KillFlag]) -> float:
    p = 0.0
    for f in kill_flags:
        if f.severity == "fatal":
            continue
        if f.severity == "warning":
            p += 0.25
        elif f.severity == "major":
            p += 0.7
    return min(1.5, round(p, 2))


def _facts_blob(facts: dict[str, Any]) -> str:
    parts = [
        str(facts.get("what_they_do", "")),
        str(facts.get("traction", "")),
        str(facts.get("customers", "")),
        str(facts.get("market", "")),
        str(facts.get("pricing", "")),
    ]
    return " ".join(parts).lower()


def _vanity_traction(facts: dict[str, Any], internal_traction_score: int) -> bool:
    if internal_traction_score < 6:
        return False
    b = _facts_blob(facts)
    vanity = bool(
        re.search(
            r"\b(waitlist|loi|letter of intent|pilot(s)?|sign[\s-]?ups?|pre[- ]launch)\b",
            b,
        )
    )
    revenue = bool(
        re.search(
            r"\b(mrr|arr|revenue|paying customer|paid user|\$[0-9]|€[0-9]|k mrr)\b",
            b,
        )
    )
    return vanity and not revenue


def _is_marketplace(facts: dict[str, Any]) -> bool:
    b = _facts_blob(facts)
    return "marketplace" in b or "two-sided" in b or "two sided" in b


def _category_ai(facts: dict[str, Any], gate1_sector: str, one_liner: str) -> bool:
    s = f"{gate1_sector} {one_liner} {facts.get('what_they_do','')}".lower()
    return bool(re.search(r"\bai\b|artificial intelligence|llm|machine learning", s))


def _b2b_enterprise(facts: dict[str, Any]) -> bool:
    b = _facts_blob(facts) + " " + str(facts.get("business_model", "")).lower()
    return "enterprise" in b or "b2b" in b


def _paid_acquisition_core(facts: dict[str, Any]) -> bool:
    b = _facts_blob(facts)
    return bool(
        re.search(r"\b(plg|self-serve|paid ads|performance marketing|google ads|meta ads)\b", b)
        or ("b2b" in b and "sales" in b)
    )


def apply_hard_cap_rules(
    *,
    gate2: Gate2Result,
    facts: dict[str, Any],
    gate1: Gate1Result,
    ext: dict[str, int],
) -> tuple[Optional[float], list[KillFlag]]:
    """
    Returns (hard_cap ceiling applied to final score, or None if no cap; deterministic kill flags).
    """
    cap = 10.0
    flags: list[KillFlag] = []

    ms = ext["market_saturation_score"]
    df = ext["distribution_feasibility_score"]
    ir = ext["incumbent_risk_score"]
    rw = ext["right_to_win_score"]
    st = ext["switching_trigger_score"]
    cv = ext["cac_viability_score"]
    reg = ext["regulatory_platform_risk_score"]
    tv = ext["trend_validity_score"]
    cp = ext["competitive_position_score"]

    bm = gate2.business_model.score if gate2.business_model else 0
    tr = gate2.traction.score if gate2.traction else 0
    moat = gate2.moat_path.score if gate2.moat_path else 0
    fmf = gate2.founder_market_fit.score if gate2.founder_market_fit else 0

    if ms <= 3 and df <= 5:
        cap = min(cap, 5.5)
        flags.append(
            KillFlag(
                code="no_credible_wedge_in_saturated_market",
                severity="major",
                description="Niska saturacja rynku + słaba dystrybucja — brak wiarygodnego wedge.",
                evidence=f"market_saturation={ms}, distribution_feasibility={df}",
            )
        )

    if ir <= 3 and rw <= 5:
        cap = min(cap, 6.0)
        flags.append(
            KillFlag(
                code="incumbent_can_bundle_or_copy",
                severity="major",
                description="Wysokie ryzyko incumbent copy/bundle przy słabym right-to-win.",
                evidence=f"incumbent_risk={ir}, right_to_win={rw}",
            )
        )

    if st <= 3:
        cap = min(cap, 6.0)
        flags.append(
            KillFlag(
                code="no_clear_switching_trigger",
                severity="major",
                description="Brak pilnego triggera przełączenia z obecnego rozwiązania.",
                evidence=f"switching_trigger={st}",
            )
        )

    cac_fatal = cv <= 3 and bm <= 6 and _paid_acquisition_core(facts)
    if cv <= 3 and bm <= 6:
        sev = "fatal" if cac_fatal else "major"
        cap = min(cap, 5.0)
        flags.append(
            KillFlag(
                code="cac_likely_structurally_broken",
                severity=sev,
                description="CAC / ekonomia pozyskania wygląda na strukturalnie złą względem modelu.",
                evidence=f"cac_viability={cv}, internal business_model={bm}",
            )
        )

    if reg <= 3:
        cap = min(cap, 5.5)
        flags.append(
            KillFlag(
                code="regulatory_blocker_unresolved",
                severity="major",
                description="Wysokie ryzyko regulacyjne / platformowe.",
                evidence=f"regulatory_platform_risk={reg}",
            )
        )

    if _vanity_traction(facts, tr):
        cap = min(cap, 6.0)
        flags.append(
            KillFlag(
                code="vanity_traction_only",
                severity="major",
                description="Traction w decku wygląda na vanity (waitlist/LOI/pilot) bez twardych sygnałów przychodu.",
                evidence="internal traction vs facts",
            )
        )

    if rw <= 4 and tv <= 6:
        cap = min(cap, 6.0)
        flags.append(
            KillFlag(
                code="no_proprietary_insight",
                severity="major",
                description="Słaby right-to-win i przeciętny trend — brak własnego insightu.",
                evidence=f"right_to_win={rw}, trend_validity={tv}",
            )
        )

    if (
        _category_ai(facts, gate1.detected_sector, gate2.company_one_liner or "")
        and moat <= 5
        and rw <= 5
    ):
        cap = min(cap, 5.5)
        flags.append(
            KillFlag(
                code="ai_wrapper_no_workflow_ownership",
                severity="major",
                description="Wzorzec AI wrapper: słaby moat i right-to-win.",
                evidence=f"moat_path={moat}, right_to_win={rw}",
            )
        )

    if _is_marketplace(facts) and df <= 5 and st <= 5:
        cap = min(cap, 5.5)
        flags.append(
            KillFlag(
                code="marketplace_without_liquidity_wedge",
                severity="major",
                description="Marketplace bez wiarygodnego wedge płynności / dystrybucji.",
                evidence=f"distribution={df}, switching={st}",
            )
        )

    if _b2b_enterprise(facts) and df <= 5 and fmf <= 6:
        cap = min(cap, 6.0)
        flags.append(
            KillFlag(
                code="long_sales_cycle_no_sales_edge",
                severity="major",
                description="B2B/enterprise z słabą dystrybucją i bez mocnego founder-market fit na sprzedaż.",
                evidence=f"distribution={df}, founder_market_fit={fmf}",
            )
        )

    if cap >= 10.0:
        return None, flags
    return cap, flags


def merge_kill_flags(
    deterministic: list[KillFlag],
    from_llm: list[KillFlag],
) -> list[KillFlag]:
    seen: set[str] = set()
    out: list[KillFlag] = []
    for f in deterministic + from_llm:
        key = f"{f.code}:{f.severity}"
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def compute_final_score_before_cap(
    internal_score: float,
    external_score: float,
    risk_penalty: float,
    *,
    internal_weight: Optional[float] = None,
    external_weight: Optional[float] = None,
    screening_mode: ScreeningMode = "deck",
) -> float:
    if internal_weight is None or external_weight is None:
        if screening_mode == "website":
            internal_weight, external_weight = 0.45, 0.55
        else:
            internal_weight, external_weight = 0.60, 0.40
    return round(
        internal_weight * internal_score + external_weight * external_score - risk_penalty,
        2,
    )


def gate2_proxy_from_website_dimensions(
    *,
    dim_scores: dict[str, int],
    company_one_liner: str,
    website_overall_score: float,
) -> Gate2Result:
    """Map website dimensions to Gate2Result fields used by apply_hard_cap_rules."""
    dist = dim_scores.get("distribution_signal", dim_scores.get("distribution", 5))
    return Gate2Result(
        passes=True,
        overall_score=website_overall_score,
        recommendation="MAYBE",
        company_one_liner=company_one_liner or "",
        business_model=ScoredDimension(dim_scores.get("business_model_clarity", 5), ""),
        traction=ScoredDimension(dim_scores.get("traction_evidence", 5), ""),
        moat_path=ScoredDimension(dim_scores.get("technical_depth_or_defensibility", 5), ""),
        founder_market_fit=ScoredDimension(dim_scores.get("founder_or_team_signal", 5), ""),
        distribution=ScoredDimension(int(dist) if dist is not None else 5, ""),
    )


def apply_hard_cap_to_final(final_before: float, hard_cap: float) -> float:
    return round(min(final_before, hard_cap), 2)


def build_final_investment_decision(
    *,
    gate1: Gate1Result,
    gate2: Gate2Result,
    external: ExternalMarketCheckResult,
    final_score: float,
    gate2_threshold: float,
    final_threshold: float,
    override_fatal: bool,
) -> FinalInvestmentDecision:
    internal = gate2.overall_score
    pass_internal = internal >= gate2_threshold
    fatal = any(f.severity == "fatal" for f in external.kill_flags)
    has_fatal = fatal and not override_fatal
    pass_final = final_score >= final_threshold

    strengths = list(gate2.top_strengths or [])[:3]
    risks = [f.description for f in external.kill_flags[:5]]
    questions = list(external.open_questions or [])[:8]

    if gate1.verdict == "FAIL_CONFIDENT":
        return FinalInvestmentDecision(
            gate1_verdict=gate1.verdict,
            internal_score=internal,
            external_score=external.external_score,
            final_score=final_score,
            pass_internal_threshold=False,
            pass_final_threshold=False,
            has_fatal_kill_flag=False,
            hard_cap_applied=external.hard_cap,
            final_verdict="REJECT_AUTO",
            recommendation="STRONG_NO",
            rationale="Gate 1 FAIL_CONFIDENT.",
            top_strengths=strengths,
            top_risks=risks,
            questions_for_founder=questions,
        )

    if has_fatal:
        return FinalInvestmentDecision(
            gate1_verdict=gate1.verdict,
            internal_score=internal,
            external_score=external.external_score,
            final_score=final_score,
            pass_internal_threshold=pass_internal,
            pass_final_threshold=False,
            has_fatal_kill_flag=True,
            hard_cap_applied=external.hard_cap,
            final_verdict="REJECT_AUTO",
            recommendation="STRONG_NO",
            rationale="Fatal kill flag on external check — auto reject.",
            top_strengths=strengths,
            top_risks=risks,
            questions_for_founder=questions,
        )

    if not pass_internal:
        return FinalInvestmentDecision(
            gate1_verdict=gate1.verdict,
            internal_score=internal,
            external_score=external.external_score,
            final_score=final_score,
            pass_internal_threshold=False,
            pass_final_threshold=pass_final,
            has_fatal_kill_flag=False,
            hard_cap_applied=external.hard_cap,
            final_verdict="REJECT_AUTO",
            recommendation="WEAK_NO",
            rationale="Internal deck score below threshold.",
            top_strengths=strengths,
            top_risks=risks,
            questions_for_founder=questions,
        )

    n_sources = len(getattr(external, "sources", None) or [])
    low_external_reliability = bool(external.provider_unavailable_warning) or (
        external.external_confidence == "low" and n_sources == 0
    )
    if (
        internal >= 7.5
        and low_external_reliability
        and pass_final
        and pass_internal
        and not has_fatal
    ):
        pw = external.provider_unavailable_warning or "low external confidence"
        return FinalInvestmentDecision(
            gate1_verdict=gate1.verdict,
            internal_score=internal,
            external_score=external.external_score,
            final_score=final_score,
            pass_internal_threshold=True,
            pass_final_threshold=True,
            has_fatal_kill_flag=False,
            hard_cap_applied=external.hard_cap,
            final_verdict="NEEDS_MANUAL_REVIEW",
            recommendation="MAYBE",
            rationale=f"Strong deck-implied score but external evidence weak — partner review: {pw}",
            top_strengths=strengths,
            top_risks=risks,
            questions_for_founder=questions,
        )

    if pass_internal and pass_final and not has_fatal:
        reco: FinalReco = "YES"
        if internal >= 8 and final_score >= 7.5:
            reco = "STRONG_YES"
        elif final_score < final_threshold + 0.3:
            reco = "MAYBE"
        return FinalInvestmentDecision(
            gate1_verdict=gate1.verdict,
            internal_score=internal,
            external_score=external.external_score,
            final_score=final_score,
            pass_internal_threshold=True,
            pass_final_threshold=True,
            has_fatal_kill_flag=False,
            hard_cap_applied=external.hard_cap,
            final_verdict="PASS_TO_HITL",
            recommendation=reco,
            rationale="Internal and final thresholds passed; no fatal kill flags.",
            top_strengths=strengths,
            top_risks=risks,
            questions_for_founder=questions,
        )

    return FinalInvestmentDecision(
        gate1_verdict=gate1.verdict,
        internal_score=internal,
        external_score=external.external_score,
        final_score=final_score,
        pass_internal_threshold=pass_internal,
        pass_final_threshold=pass_final,
        has_fatal_kill_flag=False,
        hard_cap_applied=external.hard_cap,
        final_verdict="REJECT_AUTO",
        recommendation="WEAK_NO",
        rationale="Final score below threshold or combined risk too high.",
        top_strengths=strengths,
        top_risks=risks,
        questions_for_founder=questions,
    )


def cap_external_when_provider_down(raw_external: float) -> float:
    return min(6.0, raw_external)
