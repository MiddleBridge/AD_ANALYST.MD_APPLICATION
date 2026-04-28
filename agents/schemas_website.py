"""Pydantic models for website screening (LLM tools + assessment output)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, conint, field_validator

from agents.schemas import Confidence, EvidenceItem

Gate1WebsiteVerdict = Literal["PASS", "FAIL_CONFIDENT", "UNCERTAIN_NEED_MORE_CONTEXT"]
WebsiteAssessmentVerdict = Literal[
    "REJECT_AUTO",
    "REJECT_OR_LOW_PRIORITY",
    "NEEDS_DECK",
    "NEEDS_FOUNDER_CALL",
    "PASS_TO_HITL",
    "STRONG_SIGNAL",
    "GOOD_COMPANY_NOT_OBVIOUS_VC",
]


class WebsiteFactsOutput(BaseModel):
    company_name: str = ""
    one_liner: str = ""
    founded_year: str = ""
    founders: str = ""
    team: str = ""
    target_customer: str = ""
    sector: str = ""
    geography: str = ""
    product_description: str = ""
    use_cases: str = ""
    pricing_signals: str = ""
    customer_proof: str = ""
    logos_or_case_studies: str = ""
    traction_signals: str = ""
    team_signals: str = ""
    technical_depth: str = ""
    integrations: str = ""
    security_compliance_signals: str = ""
    hiring_signals: str = ""
    blog_content_velocity: str = ""
    market_claims: str = ""
    funding_round: str = ""
    funding_amount: str = ""
    funding_date: str = ""
    valuation: str = ""
    inferred_signals: str = ""
    unclear_or_missing_data: str = ""


class WebsiteGate1Output(BaseModel):
    verdict: Gate1WebsiteVerdict
    geography_match: bool = False
    stage_guess: str = ""
    sector_match: bool = False
    company_name: str = ""
    rejection_reason: str = ""
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"


class WebsiteDimensionScore(BaseModel):
    score: conint(ge=1, le=10)  # type: ignore[valid-type]
    reasoning: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class WebsiteScoresOutput(BaseModel):
    problem_clarity: WebsiteDimensionScore
    product_clarity: WebsiteDimensionScore
    target_customer_clarity: WebsiteDimensionScore
    urgency_and_budget_signal: WebsiteDimensionScore
    differentiation: WebsiteDimensionScore
    traction_evidence: WebsiteDimensionScore
    customer_proof: WebsiteDimensionScore
    business_model_clarity: WebsiteDimensionScore
    founder_or_team_signal: WebsiteDimensionScore
    distribution_signal: WebsiteDimensionScore
    market_potential: WebsiteDimensionScore
    technical_depth_or_defensibility: WebsiteDimensionScore
    confidence: Confidence = "medium"
    missing_critical_data: list[str] = Field(default_factory=list)
    should_ask_founder: list[str] = Field(default_factory=list)
    suggested_kill_flags: list[str] = Field(default_factory=list)


class EvidenceTableRow(BaseModel):
    aspect: str
    finding: str
    kind: Literal["fact_on_site", "inferred", "missing", "needs_follow_up"] = "fact_on_site"
    source_hint: str = ""


class WebsiteInvestmentAssessment(BaseModel):
    website_score: float
    """Primary investability signal: VC-layer score (capped) when VC pipeline ran; else legacy."""
    quality_score: float = 0.0
    vc_score: float = 0.0
    confidence: str = "medium"
    verdict: WebsiteAssessmentVerdict
    top_strengths: list[str] = Field(default_factory=list)
    top_concerns: list[str] = Field(default_factory=list)
    missing_critical_data: list[str] = Field(default_factory=list)
    founder_questions: list[str] = Field(default_factory=list)
    evidence_table: list[EvidenceTableRow] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    recommended_next_step: str = ""
    cap_applied: bool = False
    cap_reasons: list[str] = Field(default_factory=list)
    raw_website_score: float = 0.0
    external_score: Optional[float] = None
    final_score: Optional[float] = None
    blended_verdict: Optional[WebsiteAssessmentVerdict] = None
    gate1_verdict: str = ""
    company_name: str = ""
    website_url: str = ""
    why_not_higher: list[str] = Field(default_factory=list)
    vc_analysis: Optional[Any] = Field(
        default=None,
        description="WebsiteVCFinalOutput (Pydantic) when VC pipeline ran.",
    )

    @field_validator("top_strengths", "top_concerns", mode="before")
    @classmethod
    def cap_three(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [str(x) for x in v[:3]]
