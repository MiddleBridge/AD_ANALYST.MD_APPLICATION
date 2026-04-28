"""Pydantic validation for LLM tool outputs (with retry on failure)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, conint, field_validator, model_validator

from agents.evidence_ledger import LedgerEvidenceItem

Confidence = Literal["low", "medium", "high"]
Gate1Verdict = Literal["PASS", "FAIL_CONFIDENT", "UNCERTAIN_READ_DECK"]
Reco = Literal["STRONG_YES", "YES", "MAYBE", "NO", "STRONG_NO"]
AnalysisMode = Literal["ANALYZED_EMAIL_ONLY", "ANALYZED_WITH_DECK"]


class EvidenceItem(BaseModel):
    quote: str = Field(default="", description="Verbatim or near-verbatim from deck/facts")
    source: str = Field(default="", description="Slide/section identifier if known")
    confidence: Confidence = "medium"


class DimensionScore(BaseModel):
    score: conint(ge=1, le=10)  # type: ignore[valid-type]
    reasoning: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    dimension_confidence: Confidence = "medium"
    evidence_used: list[str] = Field(default_factory=list)
    queries_run: list[str] = Field(default_factory=list)
    comparisons_made: list[str] = Field(default_factory=list)
    why_not_higher: str = ""
    why_not_lower: str = ""
    evidence_ledger_item_ids: list[str] = Field(
        default_factory=list,
        description="Ids of evidence_ledger entries this dimension relied on (e.g. e1, e2)",
    )


class Gate1AssessmentParsed(BaseModel):
    verdict: Gate1Verdict
    geography_match: bool
    stage_match: bool
    sector_match: bool
    company_name: str = ""
    company_one_liner: str = ""
    detected_stage: str = ""
    detected_geography: str = ""
    detected_sector: str = ""
    rejection_reason: str = ""
    flags: list[str] = Field(default_factory=list)
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"


class ExtractedFactItem(BaseModel):
    key: str
    value: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class Gate2ExtractOutput(BaseModel):
    company_name: Optional[str] = None
    company_one_liner: Optional[str] = None
    what_they_do: str = ""
    founded_year: str = ""
    founders: list[dict] = Field(default_factory=list)
    geography: str = ""
    stage: str = ""
    traction: str = ""
    fundraising_ask: str = ""
    use_of_funds: str = ""
    customers: str = ""
    pricing: str = ""
    market: str = ""
    quotes: list[EvidenceItem] = Field(default_factory=list)
    facts: list[ExtractedFactItem] = Field(default_factory=list)


_DIM_KEYS = (
    "timing",
    "problem",
    "wedge",
    "founder_market_fit",
    "product_love",
    "execution_speed",
    "market",
    "moat_path",
    "traction",
    "business_model",
    "distribution",
)


class Gate2ScoreOutput(BaseModel):
    timing: DimensionScore
    problem: DimensionScore
    wedge: DimensionScore
    founder_market_fit: DimensionScore
    product_love: DimensionScore
    execution_speed: DimensionScore
    market: DimensionScore
    moat_path: DimensionScore
    traction: DimensionScore
    business_model: DimensionScore
    distribution: DimensionScore
    confidence: Confidence = "medium"
    missing_critical_data: list[str] = Field(default_factory=list)
    should_ask_founder: list[str] = Field(default_factory=list)
    solution_love_flags: list[str] = Field(default_factory=list)
    slow_execution_flags: list[str] = Field(default_factory=list)
    evidence_ledger: list[LedgerEvidenceItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _backfill_rubric_and_distribution(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "distribution" not in data:
            data["distribution"] = {
                "score": 5,
                "reasoning": "Distribution dimension was not in the model output; defaulted pending rescoring.",
                "evidence": [],
                "missing_data": ["distribution"],
                "dimension_confidence": "low",
                "evidence_used": [],
                "queries_run": [],
                "comparisons_made": [],
                "why_not_higher": "No distribution analysis in this payload.",
                "why_not_lower": "Default mid score only.",
                "evidence_ledger_item_ids": [],
            }
        if "evidence_ledger" not in data:
            data["evidence_ledger"] = []
        for key in _DIM_KEYS:
            d = data.get(key)
            if not isinstance(d, dict):
                continue
            d.setdefault("dimension_confidence", "medium")
            d.setdefault("evidence_used", [])
            d.setdefault("queries_run", [])
            d.setdefault("comparisons_made", [])
            d.setdefault("why_not_higher", "Legacy or partial output — rerun scoring for rubric detail.")
            d.setdefault("why_not_lower", "Legacy or partial output — rerun scoring for rubric detail.")
            d.setdefault("evidence_ledger_item_ids", [])
        return data


class Gate2BriefOutput(BaseModel):
    executive_summary: str
    venture_scale_assessment: str
    top_strengths: list[str] = Field(default_factory=list)
    top_concerns: list[str] = Field(default_factory=list)
    comparable_portfolio_company: str = ""
    recommendation: Reco
    recommendation_rationale: str

    @field_validator(
        "executive_summary",
        "venture_scale_assessment",
        "recommendation_rationale",
        "comparable_portfolio_company",
        mode="before",
    )
    @classmethod
    def _coerce_optional_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return ""
        return str(v)

    @field_validator("top_strengths", "top_concerns", mode="before")
    @classmethod
    def _coerce_optional_list(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if x is not None]

    @model_validator(mode="after")
    def cap_lists(self):
        self.top_strengths = (self.top_strengths or [])[:3]
        self.top_concerns = (self.top_concerns or [])[:3]
        return self
