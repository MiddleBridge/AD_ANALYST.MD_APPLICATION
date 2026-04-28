"""VC-grade website screening models (competition, timing, distribution, economics, outlier)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.schemas_website import WebsiteScoresOutput

TrendEvidenceItem = dict[str, Any]


class Competitor(BaseModel):
    name: str = ""
    url: str = ""
    positioning: str = ""
    pricing: Optional[str] = None
    target_customer: Optional[str] = None
    evidence_url: str = ""
    source_type: Literal[
        "website",
        "app_store",
        "review",
        "article",
        "search_snippet",
        "category_llm",
    ] = "search_snippet"


class CategoryIntelLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = ""
    subcategories: list[str] = Field(default_factory=list)
    buyer: str = ""
    alternatives: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    major_incumbents: list[str] = Field(
        default_factory=list,
        description="Category-specific dominant vendors/platforms (names only), not a fixed global list.",
    )

    @field_validator("buyer", mode="before")
    @classmethod
    def coerce_buyer_to_str(cls, v):
        # LLM sometimes returns buyer as a list; normalize to compact string.
        if v is None:
            return ""
        if isinstance(v, list):
            parts = [str(x).strip() for x in v if str(x).strip()]
            return ", ".join(parts[:6])
        if isinstance(v, dict):
            vals = [str(x).strip() for x in v.values() if str(x).strip()]
            return ", ".join(vals[:6])
        return str(v).strip()


class FeatureParityLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    feature_parity_score: float = 5.0
    feature_parity_reasoning: str = ""  # why this score, citing concrete competitor names + what's similar/different
    has_clear_unique_angle: bool = False
    unique_angle: str = ""
    is_unique_or_table_stakes: str = ""
    strongest_competitor: str = ""
    why_competitor_may_win: str = ""


RelativePositioning = Literal[
    "category_leader_candidate",
    "credible_niche_challenger",
    "undifferentiated_clone",
    "unclear",
]


class CompetitiveIntelligenceOutput(BaseModel):
    category: str = ""
    subcategories: list[str] = Field(default_factory=list)
    category_major_incumbents: list[str] = Field(
        default_factory=list,
        description="Incumbents the model associated with this category (from category step).",
    )
    matched_major_incumbents: list[str] = Field(
        default_factory=list,
        description="Subset of category_major_incumbents whose names appeared in OSINT competitor titles/names.",
    )
    competitors: list[Competitor] = Field(default_factory=list)
    market_saturation_score: float = Field(ge=0, le=10, default=5.0)
    feature_parity_score: float = Field(ge=0, le=10, default=5.0)
    relative_positioning: RelativePositioning = "unclear"
    strongest_competitors: list[str] = Field(default_factory=list)
    differentiation_summary: str = ""
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


class TrendAnalysisOutput(BaseModel):
    trend_direction: Literal["up", "flat", "down", "unclear"] = "unclear"
    trend_velocity: Literal["fast", "medium", "slow", "unclear"] = "unclear"
    demand_drivers: list[str] = Field(default_factory=list)
    headwinds: list[str] = Field(default_factory=list)
    funding_activity: Literal["hot", "active", "cold", "unclear"] = "unclear"
    timing_score: float = Field(ge=1, le=10, default=5.5)
    evidence: list[TrendEvidenceItem] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


class ChannelAssessment(BaseModel):
    channel: str = ""
    evidence_strength: Literal["none", "weak", "medium", "strong"] = "none"
    scalability: float = Field(ge=0, le=10, default=5.0)
    likely_competition: float = Field(ge=0, le=10, default=5.0)
    cac_risk: float = Field(ge=0, le=10, default=5.0)
    notes: str = ""


class DistributionEngineOutput(BaseModel):
    primary_channels: list[str] = Field(default_factory=list)
    channel_assessments: list[ChannelAssessment] = Field(default_factory=list)
    distribution_score: float = Field(ge=1, le=10, default=5.0)
    distribution_risk_score: float = Field(ge=0, le=10, default=5.0)
    likely_cac_pressure: Literal["low", "medium", "high", "unclear"] = "unclear"
    evidence: list[TrendEvidenceItem] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


class UnitEconomicsOutput(BaseModel):
    pricing_model: str = ""
    monthly_price_estimate: Optional[float] = None
    expected_lifetime_months: Optional[float] = None
    gross_margin_assumption: float = 0.75
    ltv_proxy: Optional[float] = None
    cac_proxy: Optional[float] = None
    ltv_cac_proxy: Optional[float] = None
    economic_viability_score: float = Field(ge=1, le=10, default=5.5)
    assumptions: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


class RetentionModelOutput(BaseModel):
    use_case_type: str = "unclear"
    natural_churn_risk: Literal["low", "medium", "high", "unclear"] = "unclear"
    expected_lifetime_months: Optional[float] = None
    repeat_usage_potential: float = Field(ge=0, le=10, default=5.0)
    expansion_paths: list[str] = Field(default_factory=list)
    retention_evidence: list[str] = Field(default_factory=list)
    retention_structural_score: float = Field(ge=1, le=10, default=5.0)
    missing_data: list[str] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


class AdvantageItem(BaseModel):
    type: str = ""
    claim: str = ""
    evidence: str = ""
    evidence_strength: Literal["none", "weak", "medium", "strong"] = "weak"


class RightToWinOutput(BaseModel):
    advantage_types: list[AdvantageItem] = Field(default_factory=list)
    strongest_advantage: Optional[str] = None
    right_to_win_score: float = Field(ge=1, le=10, default=5.0)
    evidence_strength: Literal["none", "weak", "medium", "strong"] = "weak"
    missing_data: list[str] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


OutlierVerdict = Literal[
    "OUTLIER_POTENTIAL",
    "INTERESTING_BUT_NOT_OUTLIER_YET",
    "NOT_VC_INVESTABLE",
]


class OutlierFilterOutput(BaseModel):
    market_size_outlier: float = Field(ge=0, le=10, default=5.0)
    category_leadership_potential: float = Field(ge=0, le=10, default=5.0)
    scalability: float = Field(ge=0, le=10, default=5.0)
    margin_profile: float = Field(ge=0, le=10, default=5.0)
    distribution_asymmetry: float = Field(ge=0, le=10, default=5.0)
    defensibility: float = Field(ge=0, le=10, default=5.0)
    fund_return_potential: float = Field(ge=0, le=10, default=5.0)
    outlier_score: float = Field(ge=0, le=10, default=5.0)
    outlier_verdict: OutlierVerdict = "INTERESTING_BUT_NOT_OUTLIER_YET"
    reasoning: str = ""
    must_validate_next: list[str] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)


class ValidationQuestion(BaseModel):
    topic: str = ""
    question: str = ""
    why_it_matters: str = ""


WebsiteFinalVerdict = Literal[
    "STRONG_SIGNAL",
    "PASS_TO_HITL",
    "NEEDS_FOUNDER_CALL",
    "NEEDS_DECK",
    "GOOD_COMPANY_NOT_OBVIOUS_VC",
    "REJECT_OR_LOW_PRIORITY",
    "REJECT_AUTO",
]


class WebsiteVCFinalOutput(BaseModel):
    company_name: str = ""
    website_url: str = ""
    gate1_verdict: str = ""
    quality_score: float = 0.0
    vc_score: float = 0.0
    raw_website_score: float = 0.0
    capped_website_score: float = 0.0
    final_verdict: WebsiteFinalVerdict = "REJECT_AUTO"
    existing_12_scores: Optional[WebsiteScoresOutput] = None
    competitive_intelligence: CompetitiveIntelligenceOutput = Field(
        default_factory=CompetitiveIntelligenceOutput
    )
    trend_analysis: TrendAnalysisOutput = Field(default_factory=TrendAnalysisOutput)
    distribution_engine: DistributionEngineOutput = Field(default_factory=DistributionEngineOutput)
    unit_economics: UnitEconomicsOutput = Field(default_factory=UnitEconomicsOutput)
    retention_model: RetentionModelOutput = Field(default_factory=RetentionModelOutput)
    right_to_win: RightToWinOutput = Field(default_factory=RightToWinOutput)
    outlier_filter: OutlierFilterOutput = Field(default_factory=OutlierFilterOutput)
    top_strengths: list[str] = Field(default_factory=list)
    top_concerns: list[str] = Field(default_factory=list)
    kill_flags: list[str] = Field(default_factory=list)
    cap_reasons: list[str] = Field(default_factory=list)
    why_not_higher: list[str] = Field(default_factory=list)
    must_validate_next: list[ValidationQuestion] = Field(default_factory=list)
    recommended_next_step: str = ""
    vc_cap_reasons: list[str] = Field(default_factory=list)

    @field_validator("top_strengths", "top_concerns", mode="before")
    @classmethod
    def cap_three(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [str(x) for x in v[:3]]


class VCPackTrend(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trend_direction: str = "unclear"
    trend_velocity: str = "unclear"
    demand_drivers: list[str] = Field(default_factory=list)
    headwinds: list[str] = Field(default_factory=list)
    funding_activity: str = "unclear"
    timing_score: float = 5.5
    timing_reasoning: str = ""  # why this timing score — cite the specific tailwind/inflection driving it


class VCPackDistribution(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_channels: list[str] = Field(default_factory=list)
    dist_evidence: list[str] = Field(default_factory=list)
    dist_missing: list[str] = Field(default_factory=list)
    distribution_score: float = 5.0
    distribution_score_reasoning: str = ""  # why this distribution score — name the channel(s) seen + why scalable/not
    distribution_risk_score: float = 5.0
    distribution_risk_reasoning: str = ""  # why this risk level — CAC, channel concentration, sales cycle
    likely_cac_pressure: str = "unclear"


class VCPackRetention(BaseModel):
    model_config = ConfigDict(extra="ignore")

    use_case_type: str = "unclear"
    natural_churn_risk: str = "unclear"
    expected_lifetime_months: float = 4.0
    expansion_paths: list[str] = Field(default_factory=list)
    has_expansion_path: bool = False
    evidence_strength: str = "weak"
    retention_structural_score: float = 5.5
    retention_reasoning: str = ""  # why this retention score — cite use case frequency + expansion path


class VCPackRTW(BaseModel):
    model_config = ConfigDict(extra="ignore")

    advantage_types: list[dict] = Field(default_factory=list)
    strongest_advantage: str = ""
    right_to_win_score: float = 5.0
    right_to_win_reasoning: str = ""  # why this RTW score — cite the SPECIFIC moat (founder-market-fit, data, IP, network)
    evidence_strength: str = "weak"
    rtw_missing: list[str] = Field(default_factory=list)


class VCPackOutlier(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market_size_outlier: float = 5.0
    market_size_reasoning: str = ""  # why this market-size score — TAM proxy, buyer count, ACV envelope
    category_leadership_potential: float = 5.0
    category_leadership_reasoning: str = ""  # why — credible #1 path, brand pull, network effects
    scalability: float = 5.0
    scalability_reasoning: str = ""  # why — software margins, automation, deployment cost
    margin_profile: float = 5.0
    margin_profile_reasoning: str = ""  # why — software vs services mix, gross margin proxy
    distribution_asymmetry: float = 5.0
    distribution_asymmetry_reasoning: str = ""  # why — unfair channel access, founder network, viral loop
    defensibility: float = 5.0
    defensibility_reasoning: str = ""  # why — moat type (data, switching cost, ecosystem, regulatory)
    fund_return_potential: float = 5.0
    fund_return_reasoning: str = ""  # why — credible 50-100x path from seed-stage entry
    reasoning: str = ""  # rolled-up summary
    must_validate_next: list[str] = Field(default_factory=list)


class WebsiteVCPackLLM(BaseModel):
    """One JSON response replacing separate trend / distribution / retention / rtw / outlier / parity LLM calls."""

    model_config = ConfigDict(extra="ignore")

    feature_parity: FeatureParityLLM = Field(default_factory=FeatureParityLLM)
    trend: VCPackTrend = Field(default_factory=VCPackTrend)
    distribution: VCPackDistribution = Field(default_factory=VCPackDistribution)
    retention: VCPackRetention = Field(default_factory=VCPackRetention)
    right_to_win: VCPackRTW = Field(default_factory=VCPackRTW)
    outlier: VCPackOutlier = Field(default_factory=VCPackOutlier)
