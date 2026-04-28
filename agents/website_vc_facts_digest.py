"""Compact facts text for VC prompts — one pass instead of repeated full JSON."""

from __future__ import annotations

from agents.schemas_website import WebsiteFactsOutput

_FACT_FIELDS = (
    ("company_name", "Company"),
    ("one_liner", "One-liner"),
    ("sector", "Sector"),
    ("geography", "Geography"),
    ("target_customer", "ICP / buyer"),
    ("product_description", "Product"),
    ("use_cases", "Use cases"),
    ("pricing_signals", "Pricing"),
    ("customer_proof", "Customers"),
    ("logos_or_case_studies", "Logos / cases"),
    ("traction_signals", "Traction"),
    ("team_signals", "Team"),
    ("technical_depth", "Technical depth"),
    ("market_claims", "Market claims"),
    ("security_compliance_signals", "Security / compliance"),
    ("hiring_signals", "Hiring"),
    ("blog_content_velocity", "Content velocity"),
    ("inferred_signals", "Inferred (weak)"),
    ("unclear_or_missing_data", "Missing / unclear"),
)


def build_vc_facts_digest(facts: WebsiteFactsOutput, *, max_chars: int = 4200) -> str:
    lines: list[str] = []
    for attr, label in _FACT_FIELDS:
        val = (getattr(facts, attr, None) or "").strip()
        if not val or val.lower() in ("unknown", "n/a", "none"):
            continue
        chunk = val.replace("\n", " ").strip()
        if len(chunk) > 520:
            chunk = chunk[:517] + "..."
        lines.append(f"- {label}: {chunk}")
    text = "\n".join(lines) if lines else "(no non-empty fact fields)"
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(digest truncated)"
    return text
