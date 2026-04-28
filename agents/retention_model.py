"""Use-case type, churn risk, expansion paths — structural retention."""

from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from agents.schemas_website import WebsiteFactsOutput
from agents.schemas_website_vc import RetentionModelOutput
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL


class RetentionLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    use_case_type: str = "unclear"
    natural_churn_risk: str = "unclear"
    expected_lifetime_months: float = 4.0
    expansion_paths: list[str] = Field(default_factory=list)
    has_expansion_path: bool = False
    evidence_strength: str = "weak"
    retention_evidence: list[str] = Field(default_factory=list)
    retention_reasoning: str = ""


def _structural_score(r: RetentionLLM, facts: WebsiteFactsOutput) -> float:
    score = 5.5
    u = (r.use_case_type or "").lower()
    if "daily" in u:
        score = 8.5
    elif "weekly" in u:
        score = 7.5
    elif "one_time" in u or "one-time" in u:
        score = 3.5
    elif "occasional" in u:
        score = 4.5
    elif "compliance" in u or "recurring" in u:
        score = 8.0

    if r.has_expansion_path:
        score += 1.5
    if r.natural_churn_risk == "high":
        score -= 1.0
    if r.natural_churn_risk == "low":
        score += 0.5
    if "subscription" in (facts.pricing_signals or "").lower() and ("one_time" in u or "one-time" in u):
        score -= 1.0
    if "b2b" in u:
        score += 1.0
    return max(1.0, min(10.0, round(score, 2)))


def compile_retention_model_output(r: RetentionLLM, *, facts: WebsiteFactsOutput) -> RetentionModelOutput:
    churn = r.natural_churn_risk if r.natural_churn_risk in ("low", "medium", "high", "unclear") else "unclear"  # type: ignore[assignment]
    score = _structural_score(r, facts)
    kills: list[str] = []
    if churn == "high" and not r.has_expansion_path:
        kills.append("single_use_product_high_churn")
    if "subscription" in (facts.pricing_signals or "").lower() and "one_time" in r.use_case_type.lower():
        kills.append("subscription_on_one_time_need")
    if not r.expansion_paths:
        kills.append("no_expansion_path")
    retention_evidence = [x.strip() for x in (r.retention_evidence or []) if str(x).strip()]
    if score > 7.0 and not retention_evidence:
        score = min(score, 6.0)
        kills.append("missing_retention_metrics")

    expansion = ", ".join((r.expansion_paths or [])[:3]) or "no path stated"
    line = (
        f"Retention = {score:.1f}/10 — use-case={r.use_case_type or 'unclear'}, churn_risk={churn}, "
        f"expansion: {expansion}, expected_lifetime≈{float(r.expected_lifetime_months):.0f} months."
    )
    if retention_evidence:
        line += f" Evidence: {'; '.join(retention_evidence[:2])}."
    if (r.retention_reasoning or "").strip():
        line += f" Why: {r.retention_reasoning.strip()}"
    why = [line]
    return RetentionModelOutput(
        use_case_type=r.use_case_type,
        natural_churn_risk=churn,  # type: ignore[arg-type]
        expected_lifetime_months=float(r.expected_lifetime_months),
        repeat_usage_potential=min(10.0, score),
        expansion_paths=r.expansion_paths or [],
        retention_evidence=retention_evidence,
        retention_structural_score=score,
        missing_data=[],
        kill_flags=kills,
        why_not_higher=why,
    )


def run_retention_model(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    category: str,
    model: str = OPENAI_MODEL,
) -> RetentionModelOutput:
    fj = json.dumps(facts.model_dump(), ensure_ascii=False)[:10000]
    r = json_llm(
        client,
        system=(
            "JSON only: use_case_type one of "
            "daily_workflow|weekly_workflow|occasional_need|one_time_event|career_lifecycle|compliance_recurring|unclear, "
            "natural_churn_risk low|medium|high|unclear, expected_lifetime_months float, "
            "expansion_paths[], has_expansion_path bool, evidence_strength weak|medium|strong, "
            "retention_evidence[] with concrete proof snippets (cohort data, churn, NPS trend, or explicit factual rationale)."
        ),
        user=f"Category: {category}\n\nFACTS:\n{fj}",
        model=model,
        max_tokens=800,
        response_model=RetentionLLM,
    )
    return compile_retention_model_output(r, facts=facts)
