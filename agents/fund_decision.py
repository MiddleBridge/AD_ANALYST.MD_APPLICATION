from __future__ import annotations

from dataclasses import dataclass, field

from agents.fund_domain import FundGeoAssessment, FundMandateFit, InvestmentInterest


@dataclass
class Blockers:
    has_hard_fail: bool = False
    reasons: list[str] = field(default_factory=list)


def apply_fund_geo_rule(geo_assessment: FundGeoAssessment) -> str:
    if geo_assessment.status == "confirmed_cee":
        return "PASS"
    if geo_assessment.status in ("possible_cee_founder", "possible_cee_diaspora", "possible_cee_operations", "possible_cee"):
        return "UNCERTAIN"
    if geo_assessment.status in ("no_cee_signal", "unknown"):
        return "UNCERTAIN"
    return "UNCERTAIN"


def classify_stage(stage_text: str, funding_rounds: list[str] | None = None, headcount: int | None = None) -> str:
    st = (stage_text or "").strip().lower()
    fr = " ".join(funding_rounds or []).lower()
    blob = f"{st} {fr}".strip()
    if any(x in blob for x in ("series b", "series c", "series d")):
        return "series-b+"
    if "series a" in blob:
        return "series-a"
    if "seed extension" in blob or "seed-extension" in blob or "extension" in blob:
        return "seed-extension"
    if "seed" in blob:
        return "seed"
    if "pre-seed" in blob or "preseed" in blob or "angel" in blob:
        return "pre-seed"
    if "late seed" in blob:
        return "late-seed"
    if "early" in blob:
        return "seed"
    if headcount and headcount > 100:
        return "series-a-ready"
    return "unknown"


def investment_interest_from_scores(
    *,
    product_clarity: int,
    team_signal: int,
    market_potential: int,
    traction_signal: int,
    distribution_signal: int,
    defensibility_signal: int,
    regulatory_risk: int,
) -> InvestmentInterest:
    avg = (
        product_clarity
        + team_signal
        + market_potential
        + traction_signal
        + distribution_signal
        + defensibility_signal
    ) / 6.0
    if avg >= 7.5:
        overall = "HIGH"
    elif avg >= 6.5:
        overall = "MEDIUM_HIGH"
    elif avg >= 5.0:
        overall = "MEDIUM"
    else:
        overall = "LOW"
    return InvestmentInterest(
        product_clarity=product_clarity,
        team_signal=team_signal,
        market_potential=market_potential,
        traction_signal=traction_signal,
        distribution_signal=distribution_signal,
        defensibility_signal=defensibility_signal,
        regulatory_risk=regulatory_risk,
        overall=overall,
    )


def fund_verdict(
    mandate_fit: str,
    investment_interest: str,
    confidence: float,
    blockers: Blockers,
) -> str:
    if blockers.has_hard_fail:
        if confidence >= 0.75:
            return "REJECT"
        return "MANUAL_REVIEW"
    if mandate_fit == "PASS" and investment_interest in ("HIGH", "MEDIUM_HIGH"):
        return "REVIEW_DECK_OR_TAKE_CALL"
    if mandate_fit == "PASS" and investment_interest == "MEDIUM":
        return "REQUEST_DECK"
    if mandate_fit == "UNCERTAIN":
        return "REQUEST_DECK_AND_VERIFY"
    if mandate_fit == "FAIL":
        return "REJECT_OR_ARCHIVE"
    return "REJECT_OR_ARCHIVE"


def map_verdict_to_action(verdict: str) -> str:
    return {
        "REVIEW_DECK_OR_TAKE_CALL": "PASS_TO_PARTNER",
        "REQUEST_DECK": "ASK_FOR_MORE_INFO",
        "REQUEST_DECK_AND_VERIFY": "ASK_FOR_MORE_INFO",
        "MANUAL_REVIEW": "ASK_FOR_MORE_INFO",
        "REJECT": "STOP",
        "REJECT_OR_ARCHIVE": "STOP",
    }.get(verdict, "ASK_FOR_MORE_INFO")


def build_fund_mandate_fit(
    *,
    geo_decision: str,
    stage_decision: str,
    sector_decision: str,
    ticket_decision: str = "UNKNOWN",
    software_decision: str = "PASS",
) -> FundMandateFit:
    vals = [geo_decision, stage_decision, sector_decision, ticket_decision, software_decision]
    if "FAIL" in vals:
        overall = "FAIL"
    elif "UNCERTAIN" in vals:
        overall = "UNCERTAIN"
    else:
        overall = "PASS"
    return FundMandateFit(
        geography=geo_decision,
        stage=stage_decision,
        sector=sector_decision,
        ticket_size=ticket_decision,
        software_component=software_decision,
        overall=overall,
    )

