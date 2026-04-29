from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from datetime import datetime


class ScreeningDepth(str, Enum):
    INITIAL = "INITIAL"
    ENRICHED = "ENRICHED"
    DEEP_DIVE = "DEEP_DIVE"


class AuthRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ScreeningDecision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    NEEDS_MORE_INFO = "NEEDS_MORE_INFO"
    PASS_TO_CALL = "PASS_TO_CALL"
    WATCHLIST = "WATCHLIST"
    REJECT = "REJECT"
    STOP = "STOP"
    ASK_FOR_MORE_INFO = "ASK_FOR_MORE_INFO"
    RUN_ENRICHED_SCREEN = "RUN_ENRICHED_SCREEN"
    PASS_TO_PARTNER = "PASS_TO_PARTNER"
    TEST_CASE_ONLY = "TEST_CASE_ONLY"


@dataclass
class EmailData:
    message_id: str
    sender_email: str
    sender_name: str
    subject: str
    body: str
    date: str
    has_pdf: bool
    pdf_filename: Optional[str] = None
    pdf_bytes: Optional[bytes] = None
    attachment_id: Optional[str] = None
    thread_id: Optional[str] = None
    # Pipeline source: inbound email+PDF vs optional website-only flows
    source_type: str = "email_pdf"  # email_pdf | website
    website_url: Optional[str] = None


@dataclass
class Gate1Result:
    verdict: str  # PASS | FAIL_CONFIDENT | UNCERTAIN_READ_DECK
    geography_match: bool
    stage_match: bool
    sector_match: bool
    company_name: str = ""
    company_one_liner: str = ""
    detected_stage: str = ""
    detected_geography: str = ""
    detected_sector: str = ""
    rejection_reason: str = ""
    flags: list = field(default_factory=list)
    confidence: str = "MEDIUM"

    @property
    def passes_to_gate2(self) -> bool:
        return self.verdict != "FAIL_CONFIDENT"


@dataclass
class ScoredDimension:
    score: int
    reasoning: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    dim_confidence: str = "medium"  # low|medium|high per dimension (optional)
    evidence_used: list[str] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    comparisons_made: list[str] = field(default_factory=list)
    why_not_higher: str = ""
    why_not_lower: str = ""
    evidence_ledger_item_ids: list[str] = field(default_factory=list)


@dataclass
class Gate2Result:
    passes: bool
    overall_score: float
    recommendation: str

    @property
    def internal_deck_score(self) -> float:
        """Deck-implied weighted score (Gate 2B); alias for overall_score."""
        return self.overall_score

    # Company metadata
    company_name: str = ""
    company_one_liner: str = ""
    what_they_do: str = ""
    founded_year: str = ""
    founders: list = field(default_factory=list)
    business_model_description: str = ""
    fundraising_ask: str = ""
    use_of_funds: str = ""
    current_traction_summary: str = ""

    timing: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    problem: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    wedge: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    founder_market_fit: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    product_love: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    execution_speed: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    market: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    moat_path: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    traction: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    business_model: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))
    distribution: ScoredDimension = field(default_factory=lambda: ScoredDimension(0, ""))

    solution_love_flags: list = field(default_factory=list)
    slow_execution_flags: list = field(default_factory=list)

    executive_summary: str = ""
    venture_scale_assessment: str = ""
    top_strengths: list = field(default_factory=list)
    top_concerns: list = field(default_factory=list)
    comparable_portfolio_company: str = ""
    recommendation_rationale: str = ""

    gate2_confidence: str = "medium"
    missing_critical_data: list = field(default_factory=list)
    should_ask_founder: list = field(default_factory=list)
    quality_flags: list = field(default_factory=list)
    scoring_audit: list[str] = field(default_factory=list)
    evidence_ledger: list[dict[str, Any]] = field(default_factory=list)
    # Staged-screening fields
    screening_depth: str = ScreeningDepth.INITIAL.value
    fund_fit_decision: str = ""
    deck_evidence_decision: str = ""
    generic_vc_interest: str = ""
    final_action: str = ""
    auth_risk: str = AuthRisk.MEDIUM.value
    deck_evidence_score: float = 0.0
    external_opportunity_score: Optional[float] = None
    fund_fit_score: float = 0.0
    debug_override_used: bool = False
    continued_because_debug_override: bool = False
    test_case: bool = False


@dataclass
class ScoreBundle:
    deck_evidence_score: float
    external_opportunity_score: Optional[float]
    fund_fit_score: float


@dataclass
class Brief:
    company_name: str
    sender_name: str
    sender_email: str
    date_received: str
    overall_score: float
    recommendation: str

    one_liner: str
    what_they_do: str
    founded_year: str
    founders: list
    business_model_description: str
    fundraising_ask: str
    use_of_funds: str
    current_traction_summary: str

    geography: str
    stage: str
    sector: str

    scorecard: dict

    solution_love_flags: list
    slow_execution_flags: list

    strengths: list
    concerns: list
    executive_summary: str
    venture_scale_assessment: str
    comparable: str
    email_body_preview: str

    gate2_confidence: str = "medium"
    missing_critical_data: list = field(default_factory=list)
    should_ask_founder: list = field(default_factory=list)
    quality_flags: list = field(default_factory=list)

    # Gate 2.5: overall_score in brief = final composite when external enabled
    internal_deck_score: float = 0.0
    final_composite_score: Optional[float] = None
    external_check_enabled: bool = False
    external_market: Any = None
    final_investment_decision: Any = None
    how_scores_formed: list[str] = field(default_factory=list)


@dataclass
class HITLDecision:
    approved: bool
    notes: str = ""
    rejection_reason: str = ""
    rejection_kind: str = ""  # wrong_geo | wrong_stage | ...
    decided_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class DealRecord:
    message_id: str
    created_at: str
    updated_at: str
    sender_email: str
    sender_name: str
    subject: str
    company_name: str = ""
    status: str = "NEW"
    gate1_status: str = ""
    gate1_rejection_reason: str = ""
    gate2_status: str = ""
    gate2_score: float = 0.0
    hitl_decision: str = ""
    hitl_notes: str = ""
