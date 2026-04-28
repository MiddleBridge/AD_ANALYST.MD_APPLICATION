"""Apply rubric-aligned hard caps to deck dimension scores using extracted facts + checks."""

from __future__ import annotations

from typing import Any
import re

from agents.competition_density import competition_density_from_facts, apply_competition_caps_to_scores
from agents.market_reality import apply_market_cap_to_score, market_reality_from_facts
from agents.schemas import DimensionScore, Gate2ScoreOutput


def _dim_copy(d: DimensionScore, new_score: int) -> DimensionScore:
    return d.model_copy(update={"score": new_score})


def apply_deck_rubric_caps(
    parsed: Gate2ScoreOutput,
    facts: dict[str, Any],
) -> tuple[Gate2ScoreOutput, list[str]]:
    """
    Mutates integer scores downward when facts + rubric checks warrant caps.
    Returns (updated Gate2ScoreOutput, human-readable reasons).
    """
    reasons: list[str] = []
    mr = market_reality_from_facts(facts)
    cd = competition_density_from_facts(facts, int(parsed.moat_path.score))
    blob = " ".join(
        str(
            facts.get(k, "")
        )
        for k in ("traction", "pricing", "market", "what_they_do", "fundraising_ask", "customers")
    ).lower()
    founders = facts.get("founders") or []

    m_score = int(parsed.market.score)
    new_m, n1 = apply_market_cap_to_score(m_score, mr)
    if n1:
        reasons.extend(n1)

    moat_s = int(parsed.moat_path.score)
    new_m2, new_moat, n2 = apply_competition_caps_to_scores(
        market_score=new_m,
        moat_path_score=moat_s,
        check=cd,
    )
    reasons.extend(n2)

    # Hard caps for sparse evidence discipline
    traction_s = int(parsed.traction.score)
    biz_s = int(parsed.business_model.score)
    team_s = int(parsed.founder_market_fit.score)
    metrics_tokens = ("revenue", "gmv", "retention", "active users", "mau", "wau", "rides", "usage", "fulfillment")
    metric_hits = [t for t in metrics_tokens if t in blob]
    has_numeric_metric = bool(re.search(r"\b\d[\d,\.]*\b", blob)) and bool(metric_hits)
    has_metrics = has_numeric_metric
    if not has_metrics and traction_s > 4:
        traction_s = 4
        reasons.append("cap_traction<=4: no concrete revenue/usage/retention/customer metrics")
    pricing_unknown = "pricing= unknown" in blob or "pricing unknown" in blob or "pricing: unknown" in blob
    monetization_missing = ("price" not in blob and "pricing" not in blob and "monetization" not in blob) or pricing_unknown
    if monetization_missing and biz_s > 5:
        biz_s = 5
        reasons.append("cap_business_model<=5: no pricing/monetization data")
    has_founder_names = False
    if isinstance(founders, list) and founders:
        for f in founders:
            if isinstance(f, dict) and str(f.get("name") or "").strip() and str(f.get("name")).strip().lower() != "unknown":
                has_founder_names = True
                break
            if isinstance(f, str) and f.strip().lower() != "unknown":
                has_founder_names = True
                break
    if (not has_founder_names) and team_s > 6:
        team_s = 6
        reasons.append("cap_team<=6: founder names/background missing")
    if ("tam" in blob and "sam" in blob and "som" in blob and "x" in blob) and new_m2 > 6:
        new_m2 = 6
        reasons.append("cap_market<=6: placeholder market sizing")
    if int(new_moat) > 6 and ("patent" not in blob and "network effect" not in blob and "data moat" not in blob):
        new_moat = 6
        reasons.append("cap_moat<=6: defensibility unclear")

    out = parsed.model_copy(
        update={
            "market": _dim_copy(parsed.market, new_m2),
            "moat_path": _dim_copy(parsed.moat_path, new_moat),
            "traction": _dim_copy(parsed.traction, traction_s),
            "business_model": _dim_copy(parsed.business_model, biz_s),
            "founder_market_fit": _dim_copy(parsed.founder_market_fit, team_s),
        }
    )
    return out, reasons
