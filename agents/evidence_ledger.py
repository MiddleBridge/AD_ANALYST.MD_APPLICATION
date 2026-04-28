"""Evidence ledger — separates deck claims, site text, external search, and inference."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


SourceType = Literal["deck", "website", "external_search", "llm_inference"]


class LedgerEvidenceItem(BaseModel):
    """One atomic claim with provenance; dimensions reference these by index or id string."""

    id: str = Field(default="", description="Optional stable id, e.g. e1, e2")
    source_type: SourceType = "deck"
    claim: str = ""
    source_url: Optional[str] = None
    confidence: Literal["low", "medium", "high"] = "medium"
    used_for_dimensions: list[str] = Field(default_factory=list)


class EvidenceLedger(BaseModel):
    items: list[LedgerEvidenceItem] = Field(default_factory=list)
