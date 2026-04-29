"""Deterministic funding stage resolver (no LLM).

Goal:
- Extract stage (Pre-seed/Seed/Series A/B/...) from *explicit* web snippets
  (press / investor portfolio / databases).
- Evidence-backed only (quote + URL). Unknown is better than wrong.

This is optional because it may use a paid web-search provider (Tavily).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from agents.external_research import get_research_provider
from agents.schemas_gate25 import ExternalSource


StageStatus = str  # VERIFIED | LIKELY | CONFLICTING | INSUFFICIENT_EVIDENCE


@dataclass
class StageEvidence:
    source_url: str
    source_type: str
    raw_quote: str
    extracted_stage: str
    weight: float


@dataclass
class StageResolution:
    status: StageStatus
    stage: str = ""
    confidence: float = 0.0
    summary: str = ""
    llm_tokens_used: int = 0
    search_calls: int = 0
    tavily_credits_estimated: int = 0
    evidence: list[StageEvidence] = None  # type: ignore[assignment]
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = []
        if self.warnings is None:
            self.warnings = []


_STAGE_CANON = {
    "preseed": "Pre-seed",
    "pre-seed": "Pre-seed",
    "pre seed": "Pre-seed",
    "seed": "Seed",
    "series a": "Series A",
    "series b": "Series B",
    "series c": "Series C",
    "series d": "Series D",
    "series": "Series",  # ambiguous; avoid unless clarified by letter
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _source_weight(url: str) -> tuple[float, str]:
    u = (url or "").lower()
    if "example.vc" in u:
        return 0.90, "investor_portfolio"
    if "therecursive.com" in u or "vestbee.com" in u or "ain.ua" in u:
        return 0.75, "reputable_press"
    if "crunchbase.com" in u or "dealroom.co" in u or "tracxn.com" in u:
        return 0.80, "startup_database"
    return 0.55, "web"


def _extract_stage_from_text(text: str) -> tuple[str, str]:
    """
    Returns (stage, quote). Quote must include the explicit stage marker.
    """
    s = _norm(text)
    if not s:
        return "", ""

    # Prefer explicit "Stage: Seed"
    m = re.search(r"\bStage\s*:\s*(Pre[- ]?Seed|Seed|Series\s+[A-D])\b", s, flags=re.I)
    if m:
        raw = _norm(m.group(1)).lower()
        stage = _STAGE_CANON.get(raw, _norm(m.group(1)))
        return stage, f"Stage: {stage}"

    # Funding round phrasing: "secured a €1.5M Seed round"
    m2 = re.search(r"\b(Pre[- ]?Seed|Seed|Series\s+[A-D])\s+round\b", s, flags=re.I)
    if m2:
        raw = _norm(m2.group(1)).lower()
        stage = _STAGE_CANON.get(raw, _norm(m2.group(1)))
        return stage, f"{stage} round"

    # Common press phrasing: "raises ... seed funding" / "seed investment"
    m3 = re.search(
        r"\b(Pre[- ]?Seed|Seed|Series\s+[A-D])\s+(funding|investment|financing)\b",
        s,
        flags=re.I,
    )
    if m3:
        raw = _norm(m3.group(1)).lower()
        stage = _STAGE_CANON.get(raw, _norm(m3.group(1)))
        noun = _norm(m3.group(2)).lower()
        return stage, f"{stage} {noun}"

    # "raises €1.5M seed" style (avoid matching "seed" as a verb by requiring money/round context)
    if re.search(r"\brais(?:e|es|ed|ing)\b", s, flags=re.I) and re.search(
        r"(€|\$|£|\bmn\b|\bm\b|\bmillion\b)", s, flags=re.I
    ):
        m4 = re.search(r"\b(Pre[- ]?Seed|Seed|Series\s+[A-D])\b", s, flags=re.I)
        if m4:
            raw = _norm(m4.group(1)).lower()
            stage = _STAGE_CANON.get(raw, _norm(m4.group(1)))
            return stage, f"{stage} (raises)"

    # Avoid claiming "Series" without letter (too ambiguous)
    return "", ""


def enabled() -> bool:
    v = (os.getenv("STAGE_RESOLVER", "auto") or "").strip().lower()
    if v in ("0", "false", "no", "off", "none", "disabled"):
        return False
    return True


def resolve_stage(
    *,
    company_name: str,
    domain: str = "",
    max_results: int = 6,
) -> StageResolution:
    provider, live = get_research_provider()
    if not live:
        return StageResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="Web-search provider unavailable; stage resolution skipped.",
            warnings=["provider_unavailable"],
        )

    # Multiple queries: stage is often mentioned in press/investor pages, not on the official site.
    # Keep this deterministic and cheap (a few search calls).
    queries = [
        f"{company_name} seed round",
        f"{company_name} seed funding",
        f"{company_name} raises seed",
        f"{company_name} stage seed",
        f"{company_name} Fund portfolio seed",
    ]
    if domain:
        queries.insert(1, f"{company_name} seed round {domain}")

    max_search_calls = int(os.getenv("STAGE_RESOLVER_MAX_SEARCH_CALLS", "3") or "3")
    search_calls = 0
    sources: list[ExternalSource] = []
    seen: set[str] = set()
    ev: list[StageEvidence] = []
    for q in queries:
        if search_calls >= max_search_calls:
            break
        search_calls += 1
        for s in provider.search(q, max_results=max_results):
            u = (s.url or "").strip()
            if u and u in seen:
                continue
            if u:
                seen.add(u)
            sources.append(s)
            if s.url and s.snippet:
                stage, quote = _extract_stage_from_text(s.snippet)
                if stage and quote:
                    w, stype = _source_weight(s.url)
                    ev.append(
                        StageEvidence(
                            source_url=s.url,
                            source_type=stype,
                            raw_quote=quote,
                            extracted_stage=stage,
                            weight=w,
                        )
                    )
        # Early stop: once we have 2 strong, same-stage signals.
        strong = [e for e in ev if e.weight >= 0.75]
        strong_stages = {e.extracted_stage for e in strong}
        if len(strong) >= 2 and len(strong_stages) == 1:
            break

    if not sources:
        return StageResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="No web sources returned for stage query.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            warnings=["no_sources"],
        )

    if not ev:
        return StageResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="No explicit stage evidence found in snippets.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            warnings=["no_explicit_stage_quote"],
        )

    # Detect conflicts among strong sources.
    strong = [e for e in ev if e.weight >= 0.75]
    strong_stages = {e.extracted_stage for e in strong}
    if len(strong_stages) >= 2:
        return StageResolution(
            status="CONFLICTING",
            confidence=0.3,
            summary="Conflicting stages across strong sources; manual review required.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            evidence=ev[:6],
            warnings=["conflicting_stage"],
        )

    top = sorted(ev, key=lambda x: x.weight, reverse=True)[0]
    # VERIFIED when we have at least one strong source (>=0.75)
    verified = top.weight >= 0.75
    return StageResolution(
        status="VERIFIED" if verified else "LIKELY",
        stage=top.extracted_stage,
        confidence=0.85 if verified else 0.65,
        summary=f"Stage resolved from snippet evidence: {top.extracted_stage}.",
        llm_tokens_used=0,
        search_calls=search_calls,
        tavily_credits_estimated=search_calls,
        evidence=sorted(ev, key=lambda x: x.weight, reverse=True)[:3],
        warnings=[],
    )

