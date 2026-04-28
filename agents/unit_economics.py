"""Pricing parse, LTV/CAC proxies, economic_viability_score — no real financials required."""

from __future__ import annotations

import re
from typing import Optional, Tuple

from agents.schemas_website import WebsiteFactsOutput
from agents.schemas_website_vc import DistributionEngineOutput, RetentionModelOutput, UnitEconomicsOutput


def _parse_money_period(text: str) -> Tuple[Optional[float], str, str]:
    """Return (amount, period token, currency hint) best-effort."""
    t = (text or "").lower().replace(",", ".")
    m = re.search(
        r"([\d.]+)\s*([$€£]|usd|eur|gbp)?\s*/\s*(week|weekly|wk|month|monthly|mo|year|yr|annual|annually)",
        t,
        re.I,
    )
    if not m:
        m2 = re.search(r"([$€£])\s*([\d.]+)\s*/\s*(week|month|year)", t, re.I)
        if m2:
            cur = m2.group(1)
            amt = float(m2.group(2))
            per = m2.group(3).lower()
            return amt, per, cur
        return None, "", ""

    amt = float(m.group(1))
    per = m.group(3).lower()
    cur = (m.group(2) or "$").lower()
    return amt, per, cur


def convert_to_monthly(amount: float, period: str) -> float:
    p = period.lower()
    if p.startswith("week") or p == "wk":
        return amount * (365.0 / 12.0 / 7.0)
    if p.startswith("year") or p.startswith("annual"):
        return amount / 12.0
    return amount


def estimate_gross_margin(category: str) -> float:
    c = (category or "").lower()
    if "marketplace" in c:
        return 0.55
    if "services" in c or "agency" in c:
        return 0.45
    return 0.75


def run_unit_economics(
    *,
    facts: WebsiteFactsOutput,
    distribution: DistributionEngineOutput,
    retention: RetentionModelOutput,
    category: str,
) -> UnitEconomicsOutput:
    pr = facts.pricing_signals or facts.one_liner or ""
    amt, period, _cur = _parse_money_period(pr)
    monthly: Optional[float] = None
    if amt and period:
        monthly = round(convert_to_monthly(amt, period), 2)

    lifetime = retention.expected_lifetime_months
    if lifetime is None:
        blob = (facts.product_description or "").lower()
        if any(x in blob for x in ("exam", "certification", "prep", "test")):
            lifetime = 2.5
        else:
            lifetime = 8.0

    gm = estimate_gross_margin(category)
    ltv: Optional[float] = None
    if monthly is not None:
        ltv = round(monthly * float(lifetime) * gm, 2)

    cac_level = "medium"
    if distribution.likely_cac_pressure == "high":
        cac_level = "high"
    elif distribution.likely_cac_pressure == "low":
        cac_level = "low"

    cac_map = {"low": 10.0, "medium": 35.0, "high": 75.0, "unclear": 35.0}
    cac_proxy = cac_map.get(cac_level, 35.0)
    if distribution.primary_channels and "paid_ads" in distribution.primary_channels:
        cac_proxy = max(cac_proxy, 55.0)

    ltv_cac: Optional[float] = None
    if ltv is not None and cac_proxy > 0:
        ltv_cac = round(ltv / cac_proxy, 2)

    if ltv_cac is None:
        score = 5.5
    elif ltv_cac >= 5:
        score = 9.0
    elif ltv_cac >= 3:
        score = 7.5
    elif ltv_cac >= 1.5:
        score = 6.0
    elif ltv_cac >= 1.0:
        score = 4.0
    else:
        score = 2.5

    assumptions = [
        f"gross_margin_assumption={gm}",
        f"expected_lifetime_months={lifetime}",
        f"cac_proxy_usd≈{cac_proxy} from channel pressure {distribution.likely_cac_pressure}",
    ]
    missing = list(distribution.missing_data or [])
    if monthly is None:
        missing.append("explicit pricing for LTV math")

    kills: list[str] = []
    if ltv_cac is not None and ltv_cac < 1.0:
        kills.append("unit_economics_likely_broken")
    if monthly is not None and monthly < 20 and cac_level == "high":
        kills.append("low_price_paid_acquisition_risk")

    why = []
    if ltv_cac is not None:
        why.append(f"LTV/CAC proxy ≈ {ltv_cac} (LTV≈{ltv}, CAC≈{cac_proxy}) caps economic headroom.")
    else:
        why.append("No parseable pricing — economic score anchored at 5.5.")

    return UnitEconomicsOutput(
        pricing_model="subscription" if "sub" in pr.lower() or "month" in pr.lower() else "unknown",
        monthly_price_estimate=monthly,
        expected_lifetime_months=lifetime,
        gross_margin_assumption=gm,
        ltv_proxy=ltv,
        cac_proxy=cac_proxy,
        ltv_cac_proxy=ltv_cac,
        economic_viability_score=score,
        assumptions=assumptions,
        missing_data=missing,
        kill_flags=kills,
        why_not_higher=why,
    )
