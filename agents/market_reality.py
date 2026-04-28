"""Heuristic market reality check from extracted facts (caps align with SCREENING_RUBRIC.md)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


class MarketRealityCheck(BaseModel):
    icp: str | None = None
    buyer: str | None = None
    estimated_customer_count: str | None = None
    estimated_acv: str | None = None
    revenue_ceiling_logic: str = ""
    can_reach_100m_arr: bool | None = None
    confidence: Literal["low", "medium", "high"] = "low"
    cap_if_weak: float | None = None


_ICP_PAT = re.compile(
    r"\b(enterprise|smb|b2b|b2c|consumer|developer|student|healthcare|finance|"
    r"buyer|procurement|icp|persona|team lead|cto|cfo|clinic|hospital|bank)\b",
    re.I,
)
_BUYER_PAT = re.compile(r"\b(budget|economic buyer|champion|procurement|department head)\b", re.I)
_ARR_PAT = re.compile(r"\b(\$|€|£|usd|arr|mrr|acv|per seat|/month|pricing tier|€\d)\b", re.I)


def market_reality_from_facts(facts: dict[str, Any]) -> MarketRealityCheck:
    blob = " ".join(
        str(facts.get(k, ""))
        for k in ("customers", "market", "what_they_do", "pricing", "traction")
    )
    has_icp = bool(_ICP_PAT.search(blob))
    icp_val = "identified in materials" if has_icp else None
    buyer_val = "signals present" if _BUYER_PAT.search(blob) else None
    has_price = bool(_ARR_PAT.search(str(facts.get("pricing", "")))) or bool(_ARR_PAT.search(blob))

    cap: float | None = None
    bits: list[str] = []

    if not has_icp:
        cap = 5.5
        bits.append("no_icp_signal")
    if buyer_val is None:
        cap = min(cap or 10.0, 6.0)
        bits.append("no_budget_owner_signal")

    can_100m: bool | None = None
    if has_icp and has_price:
        can_100m = None
    elif not has_icp:
        can_100m = False
        cap = min(cap or 10.0, 6.0)
        bits.append("no_credible_100m_path")
    else:
        can_100m = None

    conf: Literal["low", "medium", "high"] = (
        "high" if (has_icp and buyer_val and has_price) else ("medium" if has_icp else "low")
    )

    return MarketRealityCheck(
        icp=icp_val,
        buyer=buyer_val,
        estimated_customer_count=None,
        estimated_acv=str(facts.get("pricing", ""))[:200] if facts.get("pricing") else None,
        revenue_ceiling_logic="; ".join(bits) if bits else "No rubric caps from market-reality heuristics.",
        can_reach_100m_arr=can_100m,
        confidence=conf,
        cap_if_weak=cap,
    )


def apply_market_cap_to_score(market_score: int, check: MarketRealityCheck) -> tuple[int, list[str]]:
    if check.cap_if_weak is None:
        return market_score, []
    capped = min(float(market_score), check.cap_if_weak)
    if capped < market_score:
        return int(capped), [f"market_rubric_cap<={check.cap_if_weak}"]
    return market_score, []
