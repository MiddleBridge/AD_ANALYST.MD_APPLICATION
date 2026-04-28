"""Why this company vs alternatives — evidence-separated advantages."""

from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from agents.schemas_website import WebsiteFactsOutput
from agents.schemas_website_vc import (
    AdvantageItem,
    CompetitiveIntelligenceOutput,
    DistributionEngineOutput,
    RetentionModelOutput,
    RightToWinOutput,
)
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL


class RTWLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    advantage_types: list[dict] = Field(default_factory=list)
    strongest_advantage: str = ""
    evidence_strength: str = "weak"
    right_to_win_reasoning: str = ""  # why this RTW score, citing the specific moat


def _score_from_llm(
    r: RTWLLM,
    *,
    crowded: bool,
    ai_only: bool,
) -> float:
    score = 5.0
    for a in r.advantage_types or []:
        t = str(a.get("type", "")).lower()
        ev = str(a.get("evidence_strength", "weak")).lower()
        bump = 0.4
        if ev == "strong":
            bump = 1.2
        elif ev == "medium":
            bump = 0.8
        if "data" in t or "proprietary" in t:
            score += 1.5 * (bump / 1.2)
        if "distribution" in t:
            score += 1.5 * (bump / 1.2)
        if "founder" in t or "domain" in t:
            score += 1.0 * (bump / 1.2)
        if "wedge" in t or "segment" in t:
            score += 0.8 * (bump / 1.2)
    if crowded and score < 6.5:
        score -= 2.0
    if ai_only:
        score -= 1.5
    return max(1.0, min(10.0, round(score, 2)))


def compile_right_to_win_output(
    r: RTWLLM,
    *,
    facts: WebsiteFactsOutput,
    competitive: CompetitiveIntelligenceOutput,
) -> RightToWinOutput:
    items: list[AdvantageItem] = []
    for a in r.advantage_types or []:
        if not isinstance(a, dict):
            continue
        evs = str(a.get("evidence_strength", "weak")).lower()
        if evs not in ("none", "weak", "medium", "strong"):
            evs = "weak"
        items.append(
            AdvantageItem(
                type=str(a.get("type", "")),
                claim=str(a.get("claim", "")),
                evidence=str(a.get("evidence", "")),
                evidence_strength=evs,  # type: ignore[arg-type]
            )
        )

    crowded = competitive.market_saturation_score >= 7 and not competitive.differentiation_summary
    blob = (facts.product_description or "") + (facts.one_liner or "")
    ai_only = "ai" in blob.lower() and competitive.feature_parity_score >= 6.5

    score = _score_from_llm(r, crowded=crowded, ai_only=ai_only)
    ev = r.evidence_strength if r.evidence_strength in ("none", "weak", "medium", "strong") else "weak"  # type: ignore[assignment]

    kills: list[str] = []
    if score <= 3.5:
        kills.append("no_right_to_win")
    if ai_only:
        kills.append("ai_as_only_differentiation")
    if not any("data" in i.type.lower() for i in items) and competitive.feature_parity_score >= 7:
        kills.append("no_proprietary_edge")
    if crowded and score <= 4.5:
        kills.append("execution_only_in_crowded_market")

    # Build a single reasoning-attached why_not_higher line per metric.
    adv_summary = "; ".join(
        [
            f"{i.type or 'unspecified'} ({i.evidence_strength})"
            for i in items[:3]
        ]
    ) or "no advantage types extracted"
    llm_reason = (r.right_to_win_reasoning or "").strip()
    rtw_line = (
        f"Right-to-win = {score:.1f}/10 — strongest advantage: {r.strongest_advantage or 'unspecified'}; "
        f"advantages seen: {adv_summary}; overall evidence_strength={ev}."
    )
    if llm_reason:
        rtw_line += f" Why: {llm_reason}"
    sat_line = (
        f"Market saturation context = {competitive.market_saturation_score:.1f}/10 — "
        f"{'crowded; RTW is harder to defend' if crowded else 'manageable; RTW does not need extreme moat to win'}."
    )
    why = [rtw_line, sat_line]
    return RightToWinOutput(
        advantage_types=items,
        strongest_advantage=r.strongest_advantage or None,
        right_to_win_score=score,
        evidence_strength=ev,  # type: ignore[arg-type]
        missing_data=[],
        kill_flags=kills,
        why_not_higher=why,
    )


def run_right_to_win(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    competitive: CompetitiveIntelligenceOutput,
    distribution: DistributionEngineOutput,
    retention: RetentionModelOutput,
    model: str = OPENAI_MODEL,
) -> RightToWinOutput:
    fj = json.dumps(facts.model_dump(), ensure_ascii=False)[:9000]
    ci = json.dumps(competitive.model_dump(), ensure_ascii=False)[:6000]
    r = json_llm(
        client,
        system=(
            "JSON: advantage_types array of {type, claim, evidence, evidence_strength none|weak|medium|strong}, "
            "strongest_advantage string, evidence_strength overall weak|medium|strong. "
            "Separate founder claims vs on-site evidence."
        ),
        user=f"FACTS:\n{fj}\n\nCOMPETITIVE:\n{ci}",
        model=model,
        max_tokens=900,
        response_model=RTWLLM,
    )
    return compile_right_to_win_output(r, facts=facts, competitive=competitive)
