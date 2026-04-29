from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolvedField:
    value: Any
    confidence: float
    status: str
    sources: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing_reason: str | None = None


@dataclass
class CEESignal:
    signal_type: str
    value: str
    confidence: float
    source: str
    explanation: str


@dataclass
class FundGeoAssessment:
    status: str  # confirmed_cee | possible_cee | no_cee_signal | unknown
    strongest_signal: str | None
    all_signals: list[CEESignal] = field(default_factory=list)
    confidence: float = 0.0
    decision: str = "UNCERTAIN"  # PASS | UNCERTAIN | FAIL


@dataclass
class FundMandateFit:
    geography: str
    stage: str
    sector: str
    ticket_size: str
    software_component: str
    overall: str


@dataclass
class InvestmentInterest:
    product_clarity: int = 0
    team_signal: int = 0
    market_potential: int = 0
    traction_signal: int = 0
    distribution_signal: int = 0
    defensibility_signal: int = 0
    regulatory_risk: int = 0
    overall: str = "LOW"  # HIGH | MEDIUM_HIGH | MEDIUM | LOW


@dataclass
class SectorAssessment:
    primary_sector: str
    secondary_sectors: list[str] = field(default_factory=list)
    fund_sector_fit: str = "UNCERTAIN"  # PASS | UNCERTAIN | FAIL
    why_fit: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


@dataclass
class ProductAssessment:
    clarity_score: int = 0
    problem_severity_score: int = 0
    ten_x_claim_score: int = 0
    proof_score: int = 0
    missing_proof: list[str] = field(default_factory=list)

