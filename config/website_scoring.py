"""Website screening: dimension weights, weighted average, evidence caps, verdict bands."""

from __future__ import annotations

import re
from typing import Any, Literal

ScreeningMode = Literal["deck", "website"]

WEBSITE_WEIGHTS: dict[str, float] = {
    "problem_clarity": 1.0,
    "product_clarity": 1.1,
    "target_customer_clarity": 1.0,
    "urgency_and_budget_signal": 1.3,
    "differentiation": 1.3,
    "traction_evidence": 1.5,
    "customer_proof": 1.4,
    "business_model_clarity": 1.0,
    "founder_or_team_signal": 0.9,
    "distribution_signal": 1.2,
    "market_potential": 1.1,
    "technical_depth_or_defensibility": 1.2,
}

_WEBSITE_WEIGHT_SUM = sum(WEBSITE_WEIGHTS.values())


def calculate_website_weighted_score(scores: dict[str, int]) -> float:
    total = sum(scores.get(k, 0) * w for k, w in WEBSITE_WEIGHTS.items())
    return round(total / _WEBSITE_WEIGHT_SUM, 2)


WebsiteVerdict = Literal[
    "REJECT_AUTO",
    "REJECT_OR_LOW_PRIORITY",
    "NEEDS_DECK",
    "NEEDS_FOUNDER_CALL",
    "PASS_TO_HITL",
    "STRONG_SIGNAL",
    "GOOD_COMPANY_NOT_OBVIOUS_VC",
]


def _external_conf_not_high(conf: str) -> bool:
    return (conf or "").lower() != "high"


def resolve_blended_website_verdict(
    *,
    website_verdict: WebsiteVerdict,
    website_score: float,
    website_llm_confidence: str,
    final_score: float,
    external_score: float,
    external_confidence: str,
    n_sources: int,
    provider_unavailable_warning: str | None,
) -> tuple[WebsiteVerdict, str]:
    """
    When the site signal is solid (≥6.5) but the numeric blend lands in REJECT_AUTO because
    public-market diligence is thin, low-confidence, or harshly scored, do not auto-veto:
    defer to the website verdict (NEEDS_DECK / NEEDS_FOUNDER_CALL / …).

    This mirrors the deck pipeline idea: marketing + sparse web research ≠ full information
    advantage of a confidential deck, so a pessimistic external block should not always kill.
    """
    conf_ext = external_confidence or website_llm_confidence
    naive = resolve_website_verdict(
        gate1_fail=False,
        website_score=final_score,
        confidence=conf_ext,
    )
    if website_verdict in ("REJECT_AUTO", "REJECT_OR_LOW_PRIORITY"):
        return naive, ""
    if naive != "REJECT_AUTO":
        return naive, ""

    thin_public = bool(provider_unavailable_warning) or n_sources < 4
    harsh_or_uncertain_external = external_score < 5.0 and _external_conf_not_high(conf_ext)
    if (
        website_score >= 6.5
        and final_score < 5.5
        and (thin_public or harsh_or_uncertain_external)
    ):
        note = (
            f"Blended verdict defers to website signal: public external score "
            f"({external_score:.2f}, conf={conf_ext}, sources={n_sources}) is not treated as "
            f"sufficient to auto-reject vs website {website_score:.2f}."
        )
        return website_verdict, note
    return naive, ""


def resolve_website_verdict(
    *,
    gate1_fail: bool,
    website_score: float,
    confidence: str,
) -> WebsiteVerdict:
    if gate1_fail:
        return "REJECT_AUTO"
    c = (confidence or "").lower()
    hi = c == "high"
    if website_score < 5.5:
        return "REJECT_AUTO"
    if website_score >= 8.3 and hi:
        return "STRONG_SIGNAL"
    if website_score >= 7.5:
        return "PASS_TO_HITL"
    if website_score >= 6.5:
        return "NEEDS_FOUNDER_CALL"
    if website_score >= 5.5:
        return "NEEDS_DECK"
    return "REJECT_AUTO"


_LANDING_AI_PAT = re.compile(
    r"\b(ai|gpt|llm|copilot|agents?|productivity|workflow|supercharge|10x)\b",
    re.I,
)


def _non_empty(s: str | None) -> bool:
    if not s or not str(s).strip():
        return False
    low = str(s).strip().lower()
    if low in ("unknown", "n/a", "none", "not found", "missing"):
        return False
    return True


def apply_website_evidence_caps(
    raw_score: float,
    *,
    facts: dict[str, Any],
    extraction_quality_score: int,
    combined_markdown: str,
    num_pages_fetched_ok: int,
) -> tuple[float, list[str]]:
    """
    Apply weaker-evidence caps. Returns (capped_score, reasons).
    If signals show a rich site (ICP + pricing + customers + product), skip restrictive caps.
    """
    reasons: list[str] = []
    pricing = _non_empty(facts.get("pricing_signals"))
    customers = _non_empty(facts.get("customer_proof"))
    cases = _non_empty(facts.get("logos_or_case_studies"))
    team = _non_empty(facts.get("team_signals"))
    icp = _non_empty(facts.get("target_customer"))
    product = _non_empty(facts.get("product_description"))
    traction = _non_empty(facts.get("traction_signals"))

    rich = pricing and customers and icp and product
    cap: float | None = None

    if not rich:
        if not pricing and not customers and not cases and not team:
            cap = _min_cap(cap, 5.5)
            reasons.append("cap:no_pricing_customers_cases_team")

    if not customers and not traction:
        cap = _min_cap(cap, 6.0)
        reasons.append("cap:no_customer_proof_and_no_traction")

    md = combined_markdown or ""
    short_site = num_pages_fetched_ok <= 2 and len(md) < 3500
    if short_site and _LANDING_AI_PAT.search(md) and not rich:
        cap = _min_cap(cap, 5.0)
        reasons.append("cap:thin_landing_vague_ai")

    if extraction_quality_score < 5:
        cap = _min_cap(cap, 5.5)
        reasons.append("cap:low_extraction_quality")

    if cap is None:
        return raw_score, []

    out = round(min(raw_score, cap), 2)
    if out < raw_score:
        return out, reasons
    return raw_score, []


def _min_cap(current: float | None, val: float) -> float:
    if current is None:
        return val
    return min(current, val)
