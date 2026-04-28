"""Pydantic models for Gate 2.5 External Market Check."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, conint, field_validator

Severity = Literal["warning", "major", "fatal"]
SourceType = Literal["web", "company_site", "news", "database", "review_site", "app_store", "other"]
QueryPurpose = Literal[
    "competition",
    "market_size",
    "trend",
    "regulation",
    "pricing",
    "funding",
    "customer_pain",
    "alternatives",
    "other",
]
ExternalConfidence = Literal["low", "medium", "high"]
FinalVerdict = Literal["REJECT_AUTO", "PASS_TO_HITL", "NEEDS_MANUAL_REVIEW"]
FinalReco = Literal["STRONG_YES", "YES", "MAYBE", "WEAK_NO", "STRONG_NO"]


class ExternalSource(BaseModel):
    title: str = ""
    url: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    snippet: Optional[str] = None
    source_type: SourceType = "web"

    @field_validator("url", mode="before")
    @classmethod
    def no_placeholder_url(cls, v):
        if v is None or v == "":
            return None
        s = str(v).strip().lower()
        if "example.com" in s or s.startswith("http://localhost"):
            return None
        return str(v).strip()


class ResearchQueryItem(BaseModel):
    query: str
    purpose: str = "other"
    priority: conint(ge=1, le=5) = 3  # type: ignore[valid-type]


class ResearchQueryPlan(BaseModel):
    queries: list[ResearchQueryItem] = Field(default_factory=list)


class KillFlag(BaseModel):
    code: str
    severity: Severity
    description: str
    evidence: Optional[str] = None


class ExternalDimensionLLM(BaseModel):
    score: conint(ge=1, le=10)  # type: ignore[valid-type]
    reasoning: str = ""
    missing_data: list[str] = Field(default_factory=list)
    source_indices: list[int] = Field(default_factory=list)


class ExternalMarketLLMAssessment(BaseModel):
    """Raw LLM output — external_score computed in code."""

    market_saturation: ExternalDimensionLLM
    competitive_position: ExternalDimensionLLM
    incumbent_risk: ExternalDimensionLLM
    distribution_feasibility: ExternalDimensionLLM
    cac_viability: ExternalDimensionLLM
    switching_trigger: ExternalDimensionLLM
    trend_validity: ExternalDimensionLLM
    regulatory_platform_risk: ExternalDimensionLLM
    right_to_win: ExternalDimensionLLM
    external_confidence: ExternalConfidence = "medium"
    market_summary: str = ""
    competition_summary: str = ""
    right_to_win_summary: str = ""
    open_questions: list[str] = Field(default_factory=list)
    suggested_kill_flags: list[KillFlag] = Field(default_factory=list)


class ExternalMarketCheckResult(BaseModel):
    company_name: str = ""
    category: Optional[str] = None
    geography: Optional[str] = None
    target_customer: Optional[str] = None

    market_saturation_score: int
    competitive_position_score: int
    incumbent_risk_score: int
    distribution_feasibility_score: int
    cac_viability_score: int
    switching_trigger_score: int
    trend_validity_score: int
    regulatory_platform_risk_score: int
    right_to_win_score: int

    external_score: float
    external_confidence: ExternalConfidence = "low"
    kill_flags: list[KillFlag] = Field(default_factory=list)
    risk_penalty: float = 0.0
    hard_cap: Optional[float] = None
    market_summary: str = ""
    competition_summary: str = ""
    right_to_win_summary: str = ""
    open_questions: list[str] = Field(default_factory=list)
    sources: list[ExternalSource] = Field(default_factory=list)
    provider_unavailable_warning: Optional[str] = None


class FinalInvestmentDecision(BaseModel):
    gate1_verdict: str = ""
    internal_score: float
    external_score: float
    final_score: float
    pass_internal_threshold: bool
    pass_final_threshold: bool
    has_fatal_kill_flag: bool
    hard_cap_applied: Optional[float] = None
    final_verdict: FinalVerdict
    recommendation: FinalReco
    rationale: str
    top_strengths: list[str] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    questions_for_founder: list[str] = Field(default_factory=list)
