from __future__ import annotations

from typing import Any

from agent.config import InovoThesisConfig


def compute_weighted(scoring: dict[str, Any]) -> float:
    keys = [
        "team",
        "market",
        "product_wedge",
        "traction",
        "defensibility",
        "timing",
        "capital_efficiency",
    ]
    vals = [float(scoring.get(k, 0) or 0) for k in keys]
    return round(sum(vals) / len(vals), 2)


def _contains_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)


def apply_fund_fit(crm: dict[str, Any], thesis: InovoThesisConfig) -> dict[str, Any]:
    basics = crm.get("basics") or {}
    product = crm.get("product") or {}
    stage = str(basics.get("stage", "unknown")).lower()
    hq_country = str(basics.get("hq_country", "unknown")).lower()
    geo_markets = [str(x).lower() for x in (basics.get("geo_markets") or [])]
    text = f"{product.get('category','')} {basics.get('one_liner','')}".lower()

    stage_match = stage in thesis.allowed_stages
    geo_match = hq_country in thesis.allowed_hq_countries or any(g in thesis.allowed_geo_markets for g in geo_markets)

    thesis_match = False
    for keywords in thesis.sector_keywords.values():
        if _contains_any(text, keywords):
            thesis_match = True
            break

    fit_score = 0
    fit_score += 4 if stage_match else 0
    fit_score += 3 if geo_match else 0
    fit_score += 3 if thesis_match else 0

    out = crm.get("fund_fit") or {}
    out["inovo_fit_score"] = max(1, min(10, fit_score))
    out["stage_match"] = stage_match
    out["geo_match"] = geo_match
    out["thesis_match"] = thesis_match
    out["inovo_fit_rationale"] = f"stage={stage_match}, geo={geo_match}, thesis={thesis_match}"
    crm["fund_fit"] = out
    return crm

