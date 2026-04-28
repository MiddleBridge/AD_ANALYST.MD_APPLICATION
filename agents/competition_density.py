"""Heuristic competition / saturation check from extracted facts."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


class CompetitionDensityCheck(BaseModel):
    direct_competitors: list[str] = Field(default_factory=list)
    funded_competitors_count: int | None = None
    incumbent_risk: Literal["low", "medium", "high"] = "medium"
    saturation_level: Literal["low", "medium", "high"] = "medium"
    company_edge: str | None = None
    cap_if_saturated: float | None = None


_COMPETITOR_WORDS = re.compile(
    r"\b(competitor|alternative|vs\.?|compared to|unlike|differentiator|moat|wedge|only platform)\b",
    re.I,
)


def competition_density_from_facts(facts: dict[str, Any], moat_path_score: int) -> CompetitionDensityCheck:
    blob = " ".join(str(facts.get(k, "")) for k in ("market", "what_they_do", "traction", "facts")).lower()
    names: list[str] = []
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:competitor|alternative)", facts.get("market", "") + facts.get("what_they_do", "")):
        names.append(m.group(1))

    sat: Literal["low", "medium", "high"] = "medium"
    if "crowded" in blob or "many vendors" in blob or "commoditized" in blob:
        sat = "high"
    elif _COMPETITOR_WORDS.search(blob):
        sat = "medium"
    else:
        sat = "low"

    edge = None
    if re.search(r"\b(wedge|differentiator|only|first|defensible|proprietary|data network)\b", blob):
        edge = "differentiation language present in facts"

    inc: Literal["low", "medium", "high"] = "high" if "incumbent" in blob or "microsoft" in blob or "salesforce" in blob else "medium"

    cap: float | None = None
    if sat == "high" and not edge:
        cap = 6.0
    if inc == "high" and moat_path_score < 6:
        # Weaker moat under incumbent pressure — ceiling for moat dimension (see rubric).
        cap = min(cap or 10.0, 6.5)

    return CompetitionDensityCheck(
        direct_competitors=names[:8],
        funded_competitors_count=None,
        incumbent_risk=inc,
        saturation_level=sat,
        company_edge=edge,
        cap_if_saturated=cap,
    )


def apply_competition_caps_to_scores(
    *,
    market_score: int,
    moat_path_score: int,
    check: CompetitionDensityCheck,
) -> tuple[int, int, list[str]]:
    """Saturation without edge caps market; incumbent + weak moat caps moat_path (integer scores)."""
    notes: list[str] = []
    m, mo = market_score, moat_path_score

    if check.saturation_level == "high" and not check.company_edge and m > 6:
        m = 6
        notes.append("competition_cap:market<=6_high_saturation_no_edge")

    if check.incumbent_risk == "high" and moat_path_score < 6 and mo > 6:
        mo = 6  # rubric ceiling 6.5 → int deck scores top out at 6
        notes.append("competition_cap:moat<=6_high_incumbent_weak_moat")

    return m, mo, notes
