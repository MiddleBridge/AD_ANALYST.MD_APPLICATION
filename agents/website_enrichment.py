"""Deterministic post-crawl enrichment for website screening.

Why this module exists
----------------------
LLM-based facts extraction occasionally misses high-signal proper nouns that are
clearly present in the crawled markdown (founder names on About pages, the legal
entity / address in the footer, plausible business-model phrases, integration
logos, etc.). When that happens, the scoring layer punishes the company with a
1/10 founder_or_team_signal even though the website does state who the founders
are.

This module runs *after* the crawl and *before* scoring. It scans the combined
markdown deterministically and returns enrichment hints. The screener then uses
those hints to backfill empty/"unknown" facts, so the rest of the pipeline (LLM
scoring, evidence ledger, kill-flags) sees a more honest input.

Everything here is best-effort and intentionally conservative: we only fill a
field if we have very strong textual evidence for it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

from agents.schemas_website import WebsiteFactsOutput

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Roles we consider "founder/leadership" for the founders heuristic.
_LEADER_ROLE_PATTERN = (
    r"(?:Co[\u00ad\-\s]?Founder|Founder|CEO|Chief\s+Executive\s+Officer|"
    r"CTO|Chief\s+Technology\s+Officer|"
    r"COO|Chief\s+Operating\s+Officer|"
    r"CPO|Chief\s+Product\s+Officer|"
    r"CFO|Chief\s+Financial\s+Officer|"
    r"CMO|Chief\s+Marketing\s+Officer|"
    r"President|Managing\s+Director|Founding\s+Engineer)"
)

# Reasonable Latin-script personal name (2-3 capitalised words). Allows
# Polish/European diacritics in lowercase letters. We require at least 2 chars
# *after* the leading capital (so "Co", "Mr", "Sr" – frequent role prefixes –
# do not get sucked into the captured name) and limit to 3 words to avoid
# pulling in adjacent capitalised company names from the previous sentence.
_NAME_WORD = r"[A-Z][a-zA-Z\u00C0-\u017F\u0370-\u03FF'\-]{2,}"
_NAME_PATTERN = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{1,2}}"

# Tokens that look like a personal name but are actually role prefixes,
# initials, salutations, or company-tail words ("Inc", "Corp", "Labs").
_BAD_NAME_TOKENS = {
    "co", "sr", "jr", "mr", "mrs", "ms", "dr", "mx", "vp", "ceo", "cto", "coo",
    "cmo", "cfo", "cpo", "founder", "founding", "head", "chief", "vice",
    "president", "director", "manager", "engineer", "officer", "staff",
    "inc", "corp", "corporation", "ltd", "llc", "labs", "studio", "group",
    "company", "team", "lab", "agency",
}

# Two-letter US state codes (handles "Dover, Delaware 19901" and "Austin, TX")
_US_STATE_NAMES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|"
    "Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|"
    "Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|"
    "Nevada|New\\s+Hampshire|New\\s+Jersey|New\\s+Mexico|New\\s+York|North\\s+Carolina|"
    "North\\s+Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode\\s+Island|South\\s+Carolina|"
    "South\\s+Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West\\s+Virginia|"
    "Wisconsin|Wyoming"
)

# Country mentions that frequently appear in footers.
_COUNTRY_NAMES = (
    "Poland|Polska|United\\s+States|USA|United\\s+Kingdom|UK|Germany|France|Spain|"
    "Italy|Netherlands|Belgium|Sweden|Norway|Denmark|Finland|Ireland|Switzerland|"
    "Austria|Portugal|Czech\\s+Republic|Czechia|Slovakia|Hungary|Romania|Bulgaria|"
    "Greece|Estonia|Latvia|Lithuania|Croatia|Slovenia|Ukraine|Canada|Australia|Israel|"
    "Singapore|India|Japan|Brazil|Mexico|Argentina"
)

_BUSINESS_MODEL_HINTS = [
    (re.compile(r"\b(per\s+seat|per\s+user|per\s+month|per\s+year|/month|/yr|/user)\b", re.I), "subscription / per-seat"),
    (re.compile(r"\bsubscription[s]?\b", re.I), "subscription"),
    (re.compile(r"\b(SaaS|usage[\-\s]based|metered)\b", re.I), "usage-based / SaaS"),
    (re.compile(r"\b(one[\-\s]time\s+fee|setup\s+fee|implementation\s+fee)\b", re.I), "implementation/setup fee"),
    (re.compile(r"\b(retainer|monthly\s+retainer|managed\s+service)\b", re.I), "retainer / managed service"),
    (re.compile(r"\b(diagnostic[s]?|blueprint[s]?|audit)\s+as\s+a\s+(?:paid\s+)?service\b", re.I), "paid diagnostics / blueprints"),
    (re.compile(r"\b(book\s+a\s+demo|contact\s+sales|talk\s+to\s+sales|schedule\s+a\s+call)\b", re.I), "sales-led pricing (contact sales)"),
    (re.compile(r"\b(free\s+trial|free\s+plan|freemium)\b", re.I), "freemium / free trial"),
]

_INTEGRATION_KEYWORDS = [
    "Zapier", "n8n", "Make.com", "Make ", "Slack", "Notion", "Salesforce",
    "HubSpot", "Stripe", "Intercom", "Zendesk", "Microsoft Teams", "Microsoft 365",
    "Google Workspace", "Gmail", "Outlook", "Jira", "Asana", "Linear",
    "Webflow", "Shopify", "Airtable", "BigQuery", "Snowflake", "Databricks",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Twilio", "SendGrid",
    "OpenAI", "Anthropic", "Azure", "AWS", "Google Cloud", "GCP", "Vertex AI",
]

_COMPLIANCE_KEYWORDS = [
    "SOC2", "SOC 2", "ISO 27001", "ISO27001", "GDPR", "HIPAA", "PCI-DSS", "PCI DSS",
    "CCPA", "DPA", "Data Processing Agreement",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class WebsiteEnrichmentHints:
    """Heuristic hints derived from raw markdown to backfill missing facts."""

    founders: str = ""
    founders_evidence: List[str] = field(default_factory=list)

    geography: str = ""
    geography_evidence: List[str] = field(default_factory=list)
    geography_confidence: str = "low"

    business_model: str = ""
    business_model_evidence: List[str] = field(default_factory=list)

    integrations: List[str] = field(default_factory=list)
    compliance: List[str] = field(default_factory=list)

    notes: List[str] = field(default_factory=list)

    def as_summary_lines(self) -> List[str]:
        out: List[str] = []
        if self.founders:
            out.append(f"founders: {self.founders}")
        if self.geography:
            out.append(f"geography: {self.geography}")
        if self.business_model:
            out.append(f"business_model: {self.business_model}")
        if self.integrations:
            out.append("integrations: " + ", ".join(self.integrations[:8]))
        if self.compliance:
            out.append("compliance: " + ", ".join(self.compliance[:8]))
        return out


# ---------------------------------------------------------------------------
# Founder / team extraction
# ---------------------------------------------------------------------------


def _extract_founders(markdown: str) -> tuple[str, List[str]]:
    """Extract founders using page context + GLiNER labels."""
    if not markdown:
        return "", []
    found: list[tuple[str, str, str]] = []
    seen_names: set[str] = set()

    source_re = re.compile(r"^## Source:\s*(https?://\S+)\s*$", flags=re.MULTILINE)
    hits = list(source_re.finditer(markdown))
    pages: list[tuple[str, str]] = []
    if not hits:
        pages = [("", markdown)]
    else:
        for i, m in enumerate(hits):
            start = m.end()
            end = hits[i + 1].start() if i + 1 < len(hits) else len(markdown)
            pages.append((m.group(1).strip(), markdown[start:end].strip()))

    @lru_cache(maxsize=1)
    def _get_gliner():
        # Default OFF: downloading HF models during screening is slow and brittle.
        # Enable explicitly when you want name extraction to be stronger.
        if (os.getenv("WEBSITE_ENRICH_USE_GLINER", "0") or "").strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return None
        try:
            from gliner import GLiNER

            return GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
        except Exception:
            return None

    def _push(name: str, role: str, evidence: str) -> None:
        n = re.sub(r"\s+", " ", name).strip()
        r = re.sub(r"\s+", " ", role).strip()
        if not n or not r:
            return
        # Reject very generic words sometimes mis-capitalised in markdown headings.
        bad_tokens = {
            "About", "Team", "Company", "Leadership", "Our", "We", "Why", "What",
            "Privacy", "Policy", "Terms", "Service", "Customer", "Customers",
            "Contact", "Pricing", "Login", "Sign", "Book", "Demo", "Get",
            "Read", "More", "Case", "Study", "Studies", "Press", "Release",
            "Solution", "Solutions", "Industry", "Blog", "Article", "Source",
        }
        tokens = n.split()
        if any(tok in bad_tokens for tok in tokens):
            return
        # Reject names whose any word is a known role prefix / company-tail
        # (e.g. "Tymon Terlikiewicz Co" — leftover "Co" from "Co-Founder").
        if any(tok.lower() in _BAD_NAME_TOKENS for tok in tokens):
            return
        # Personal names usually have at most 3 components on a website card.
        if not (2 <= len(tokens) <= 3):
            return
        if n.lower() in seen_names:
            return
        seen_names.add(n.lower())
        found.append((n, r, evidence))

    model = _get_gliner()
    labels = [
        "founder_at_this_company",
        "customer_ceo_quoted",
        "investor",
        "advisor",
    ]
    role_rx = re.compile(_LEADER_ROLE_PATTERN, flags=re.I)

    for page_url, page_md in pages:
        page_low = page_url.lower()
        if "/customers" in page_low or "/case-studies" in page_low or "/testimonials" in page_low:
            continue
        if page_url and not any(k in page_low for k in ("/about", "/team", "/company", "/founder", "/leadership", "/people")):
            continue
        if not page_md.strip():
            continue

        if model is not None:
            chunks = [page_md[i : i + 2500] for i in range(0, len(page_md), 2500)]
            for chunk in chunks:
                try:
                    ents = model.predict_entities(chunk, labels)
                except Exception:
                    ents = []
                for ent in ents:
                    if str(ent.get("label", "")).strip() != "founder_at_this_company":
                        continue
                    if float(ent.get("score", 0.0) or 0.0) < 0.7:
                        continue
                    name = str(ent.get("text", "")).strip()
                    if not re.fullmatch(_NAME_PATTERN, name):
                        continue
                    # Try to find a nearby role mention so output stays useful.
                    idx = chunk.find(name)
                    context = chunk[max(0, idx - 140) : idx + len(name) + 160] if idx >= 0 else chunk
                    role_hit = role_rx.search(context)
                    role = role_hit.group(0) if role_hit else "Founder"
                    _push(name, role, f"{name} ({role})")
        else:
            # Safe fallback if GLiNER model is unavailable at runtime.
            pat = re.compile(
                rf"({_NAME_PATTERN})\s*[\u2014\-\u2013,:]\s*({_LEADER_ROLE_PATTERN})\b",
                flags=re.UNICODE,
            )
            for m in pat.finditer(page_md):
                _push(m.group(1), m.group(2), m.group(0))

    if not found:
        return "", []

    # Cap at 6 founders/leaders to keep facts.founders short.
    found = found[:6]
    summary = "; ".join(f"{n} \u2014 {r}" for (n, r, _ev) in found)
    evidence = [ev for (_n, _r, ev) in found]
    return summary, evidence


# ---------------------------------------------------------------------------
# Geography extraction
# ---------------------------------------------------------------------------


def _extract_location(markdown: str) -> tuple[str, List[str], str]:
    if not markdown:
        return "unknown", [], "low"

    evidence: list[str] = []
    components: list[str] = []

    # 1) Prefer structured-data hints injected by crawler (JSON-LD).
    # Example line: "- schema_address: 123 Main St, Belgrade, Serbia"
    for ln in (markdown or "").splitlines():
        low = ln.lower().strip()
        if "schema_address:" in low:
            raw = ln.split("schema_address:", 1)[1].strip(" -:\t")
            if raw:
                # Try to pick a "city, country" shape from the blob.
                pat_country = re.compile(
                    rf"([A-Z][\w\-\.\u00C0-\u017F]+(?:\s+[A-Z][\w\-\.\u00C0-\u017F]+)*)"
                    rf"\s*,\s*({_COUNTRY_NAMES})\b",
                    flags=re.UNICODE,
                )
                m = pat_country.search(raw)
                if m:
                    city = m.group(1).strip()
                    country = m.group(2).strip()
                    return f"{city}, {country}", [ln.strip()[:420]], "high"
                # Fallback: if it contains a country name at least, keep it as low-confidence.
                m2 = re.search(rf"\b({_COUNTRY_NAMES})\b", raw, flags=re.UNICODE)
                if m2:
                    return m2.group(1).strip(), [ln.strip()[:420]], "medium"

    context_keywords = (
        "headquartered in",
        "hq:",
        "based in",
        "legal entity",
        "incorporated in",
        "registered office",
        "registered address",
        "company address",
        "address:",
    )
    windows: list[str] = []
    md_low = markdown.lower()
    for kw in context_keywords:
        start = 0
        while True:
            idx = md_low.find(kw, start)
            if idx < 0:
                break
            windows.append(markdown[max(0, idx - 120) : idx + 220].replace("\n", " "))
            start = idx + len(kw)

    if not windows:
        # 2) Address blocks without explicit keywords (common in footers/terms).
        # Try to find short lines that look like "City, Country" or "City, ST, USA".
        lines = [re.sub(r"\s+", " ", x).strip() for x in markdown.splitlines() if x.strip()]
        candidate_lines: list[str] = []
        for x in lines:
            if len(x) < 8 or len(x) > 180:
                continue
            if any(tok in x.lower() for tok in ("privacy", "terms", "cookies", "copyright", "all rights reserved")):
                # keep footer-y lines; these often include entity/address
                candidate_lines.append(x)
            elif "address" in x.lower() or "registered" in x.lower():
                candidate_lines.append(x)
        windows = candidate_lines[:20]
        if not windows:
            return "unknown", [], "low"

    pat_us = re.compile(
        rf"([A-Z][\w\-\.\u00C0-\u017F]+(?:\s+[A-Z][\w\-\.\u00C0-\u017F]+)*)"
        rf"\s*,\s*(?:({_US_STATE_NAMES})|([A-Z]{{2}}))\b",
        flags=re.UNICODE,
    )
    pat_country = re.compile(
        rf"([A-Z][\w\-\.\u00C0-\u017F]+(?:\s+[A-Z][\w\-\.\u00C0-\u017F]+)*)"
        rf"\s*,\s*({_COUNTRY_NAMES})\b",
        flags=re.UNICODE,
    )
    for window in windows:
        m = pat_us.search(window)
        if m:
            city = m.group(1).strip()
            state = (m.group(2) or m.group(3) or "").strip()
            if state and city.lower() not in {"about", "team", "contact", "company"}:
                components.append(f"{city}, {state}, USA")
                evidence.append(window.strip())
                return components[0], evidence[:3], "high"
        m = pat_country.search(window)
        if m:
            city = m.group(1).strip()
            country = m.group(2).strip()
            if city.lower() not in {"about", "team", "contact", "company"}:
                components.append(f"{city}, {country}")
                evidence.append(window.strip())
                return components[0], evidence[:3], "high"

    # 3) Country-only fallback in keyword windows (legal pages sometimes say only "United States").
    for window in windows:
        m = re.search(rf"\b({_COUNTRY_NAMES})\b", window, flags=re.UNICODE)
        if m:
            c = m.group(1).strip()
            evidence.append(window.strip())
            return c, evidence[:3], "medium"

    return "unknown", [], "low"


def _extract_geography(markdown: str) -> tuple[str, List[str]]:
    location, evidence, _ = _extract_location(markdown)
    return location, evidence


# ---------------------------------------------------------------------------
# Business model heuristic
# ---------------------------------------------------------------------------


def _extract_business_model(markdown: str) -> tuple[str, List[str]]:
    if not markdown:
        return "", []
    hits: list[str] = []
    evidence: list[str] = []
    for rx, label in _BUSINESS_MODEL_HINTS:
        m = rx.search(markdown)
        if m:
            hits.append(label)
            evidence.append(m.group(0))
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique_hits = []
    for h in hits:
        if h.lower() in seen:
            continue
        seen.add(h.lower())
        unique_hits.append(h)
    return (" / ".join(unique_hits[:3]) if unique_hits else ""), evidence[:3]


# ---------------------------------------------------------------------------
# Integrations & compliance
# ---------------------------------------------------------------------------


def _extract_keywords(markdown: str, vocabulary: list[str]) -> List[str]:
    if not markdown:
        return []
    md_low = markdown.lower()
    found: list[str] = []
    seen: set[str] = set()
    for kw in vocabulary:
        if kw.lower() in md_low and kw.lower() not in seen:
            seen.add(kw.lower())
            found.append(kw)
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enrich_from_markdown(combined_markdown: str) -> WebsiteEnrichmentHints:
    """Run all heuristics and return a single hints object."""
    md = combined_markdown or ""
    hints = WebsiteEnrichmentHints()

    hints.founders, hints.founders_evidence = _extract_founders(md)
    hints.geography, hints.geography_evidence, hints.geography_confidence = _extract_location(md)
    hints.business_model, hints.business_model_evidence = _extract_business_model(md)
    hints.integrations = _extract_keywords(md, _INTEGRATION_KEYWORDS)
    hints.compliance = _extract_keywords(md, _COMPLIANCE_KEYWORDS)
    return hints


_UNKNOWN_VALUES = {"", "unknown", "n/a", "none", "not stated", "not available", "not provided"}


def _is_blank(value: Optional[str]) -> bool:
    if value is None:
        return True
    return value.strip().lower() in _UNKNOWN_VALUES


def merge_enrichment_into_facts(
    facts: WebsiteFactsOutput, hints: WebsiteEnrichmentHints
) -> tuple[WebsiteFactsOutput, List[str]]:
    """Backfill `facts` only where the LLM left fields blank/unknown.

    Returns the (possibly mutated) facts object plus a list of human-readable
    notes describing what we filled, so the run can show this in the terminal
    and store it in the evidence ledger.
    """
    notes: list[str] = []

    if hints.founders and _is_blank(facts.founders):
        facts.founders = hints.founders
        notes.append(f"enrichment: filled founders → {facts.founders[:140]}")

    if hints.founders and _is_blank(facts.team):
        facts.team = hints.founders
        notes.append(f"enrichment: filled team from founders → {facts.team[:140]}")

    if hints.geography and hints.geography.lower() != "unknown" and _is_blank(facts.geography):
        facts.geography = hints.geography
        notes.append(f"enrichment: filled geography → {facts.geography}")

    # Business-model: only fill if LLM has nothing useful
    if hints.business_model and _is_blank(facts.pricing_signals):
        facts.pricing_signals = hints.business_model
        notes.append(f"enrichment: filled pricing_signals → {facts.pricing_signals}")

    # Integrations: union (preserve LLM output, append missing ones)
    if hints.integrations:
        existing = (facts.integrations or "").strip()
        existing_low = existing.lower()
        added = [k for k in hints.integrations if k.lower() not in existing_low]
        if added:
            facts.integrations = (existing + (", " if existing else "") + ", ".join(added)).strip(", ")
            notes.append(f"enrichment: added integrations → {', '.join(added[:6])}")

    if hints.compliance:
        existing = (facts.security_compliance_signals or "").strip()
        existing_low = existing.lower()
        added = [k for k in hints.compliance if k.lower() not in existing_low]
        if added:
            facts.security_compliance_signals = (
                existing + ("; " if existing else "") + ", ".join(added)
            ).strip("; ")
            notes.append(f"enrichment: added compliance → {', '.join(added[:6])}")

    # Recompute "unclear_or_missing_data" so stale "unknowns" are dropped.
    stale_keywords = (
        ("founder", facts.founders, "founders and team information"),
        ("team", facts.team, "team information"),
        ("geograph", facts.geography, "geography or HQ location"),
        ("revenue stream", facts.pricing_signals, "monetization / business model"),
        ("business model", facts.pricing_signals, "business model"),
    )
    if facts.unclear_or_missing_data:
        cleaned_lines: list[str] = []
        for line in facts.unclear_or_missing_data.splitlines():
            ln = line.lower()
            keep = True
            for needle, value, _label in stale_keywords:
                if needle in ln and not _is_blank(value):
                    keep = False
                    break
            if keep:
                cleaned_lines.append(line)
        new_missing = "\n".join(cleaned_lines).strip()
        if new_missing != (facts.unclear_or_missing_data or "").strip():
            facts.unclear_or_missing_data = new_missing
            notes.append("enrichment: cleared stale 'unknown' lines after backfill")

    return facts, notes


__all__ = [
    "WebsiteEnrichmentHints",
    "enrich_from_markdown",
    "merge_enrichment_into_facts",
]
