"""Post-LLM sanity checks — flag inconsistent scoring vs extracted text."""

from __future__ import annotations

import re
from typing import Any


def _lower(s: str) -> str:
    return (s or "").lower()


def run_post_llm_quality_checks(
    *,
    facts: dict[str, Any],
    scores: dict[str, int],
    traction_summary: str,
) -> list[str]:
    flags: list[str] = []
    t = _lower(traction_summary)
    combined = " ".join(
        _lower(str(facts.get(k, "")))
        for k in ("traction", "customers", "what_they_do", "market")
    )

    # High traction score but explicit "no revenue" / idea stage language
    no_rev = bool(
        re.search(r"\bno revenue\b", t)
        or re.search(r"\bno mrr\b", t)
        or "pre-revenue" in t
        or "idea stage" in combined
    )
    if no_rev and scores.get("traction", 0) >= 8:
        flags.append("High traction score despite no/low revenue signals in summary")

    if scores.get("traction", 0) >= 7 and not traction_summary.strip():
        flags.append("High traction score but empty traction summary")

    founders = facts.get("founders") or []
    if scores.get("founder_market_fit", 0) >= 7 and (not founders or len(founders) == 0):
        flags.append("High founder-market-fit score but no founder data extracted")

    if scores.get("problem", 0) >= 8:
        if "customer" not in combined and "pilot" not in combined and "budget" not in combined:
            flags.append("High problem score with weak customer/budget evidence in facts")

    if scores.get("business_model", 0) >= 7:
        pricing = _lower(str(facts.get("pricing", "")))
        if not pricing.strip() or pricing in ("unknown", "n/a", "not specified"):
            flags.append("High business model score but no pricing evidence in facts")

    return flags
