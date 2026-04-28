"""Category framing + competitor context; optional Tavily when TAVILY_API_KEY is set — otherwise name-only fallback from the category LLM."""

from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from openai import OpenAI

from agents.external_research import NullExternalResearchProvider, get_research_provider
from agents.schemas_website import WebsiteFactsOutput
from agents.schemas_website_vc import (
    CategoryIntelLLM,
    CompetitiveIntelligenceOutput,
    Competitor,
    FeatureParityLLM,
    RelativePositioning,
)
from agents.website_vc_llm import json_llm
from config.llm_cost import OPENAI_MODEL, TOK_WEBSITE_SCORES_OUT


def _normalize_incumbent_names(names: list[str], *, limit: int = 24) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        s = (raw or "").strip()
        if len(s) < 2:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _matched_incumbents(major_incumbents: list[str], names_blob: str) -> list[str]:
    blob = names_blob.lower()
    matched: list[str] = []
    for brand in major_incumbents:
        b = brand.strip()
        if len(b) < 2:
            continue
        if b.lower() in blob:
            matched.append(b)
    return matched


def compute_market_saturation(
    competitors: list[Competitor],
    *,
    category: str,
    markdown_lower: str,
    major_incumbents: list[str],
    has_live_search_snippets: bool = True,
) -> float:
    """0–10, higher = more crowded.

    Long ``alternatives``/``major_incumbents`` lists from the category LLM (no Tavily) are not
    treated like 10+ independent SERP hits — that used to peg saturation at 10.0 and flood kill flags.
    """
    saturation = 2.0
    n = len(competitors)
    if not has_live_search_snippets:
        n = min(n, 5)
    if n >= 10:
        saturation += 3.0
    elif n >= 6:
        saturation += 2.0
    elif n >= 3:
        saturation += 1.0

    names_blob = " ".join(c.name for c in competitors).lower()
    if major_incumbents:
        # Synthetic name lists often contain the majors themselves — do not match on that alone.
        ref = names_blob if has_live_search_snippets else markdown_lower
        if _matched_incumbents(major_incumbents, ref):
            saturation += 2.0

    if has_live_search_snippets:
        positions = [c.positioning.strip().lower() for c in competitors if c.positioning.strip()]
        if len(positions) >= 4:
            uniq = len(set(positions))
            if uniq <= max(1, len(positions) // 3):
                saturation += 2.0

    cat = (category or "").lower()
    md = markdown_lower
    if "app store" in md or "download on the" in md or "ios" in md or "android" in md:
        saturation += 1.0
    if "b2c" in cat or "consumer" in cat or "student" in cat:
        saturation += 0.5
    if any(k in cat for k in ("process mining", "process intelligence", "workflow automation", "process automation")):
        saturation += 1.5

    if not has_live_search_snippets:
        saturation = min(saturation, 7.5)

    return max(0.0, min(10.0, round(saturation, 2)))


def _name_only_competitors_from_category(cat_llm: CategoryIntelLLM) -> list[Competitor]:
    """When no web search API — use category LLM names only (no URLs; not crawled evidence)."""
    seen: set[str] = set()
    out: list[Competitor] = []
    for raw in list(cat_llm.alternatives or []) + list(cat_llm.major_incumbents or []):
        name = (raw or "").strip()[:80]
        if len(name) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Competitor(
                name=name,
                url="",
                positioning="From category framing only; no paid web search run.",
                pricing=None,
                target_customer=None,
                evidence_url="",
                source_type="category_llm",
            )
        )
        if len(out) >= 14:
            break
    return out


def _sources_to_competitors(sources: list[Any], query: str) -> list[Competitor]:
    out: list[Competitor] = []
    for s in sources:
        url = getattr(s, "url", None) or ""
        title = (getattr(s, "title", "") or "").strip()
        snip = (getattr(s, "snippet", None) or "")[:400]
        if not url and not title:
            continue
        host = ""
        try:
            if url:
                host = urlparse(url).netloc or ""
        except Exception:
            host = ""
        name = title.split("|")[0].split("-")[0].strip()[:80] or (host or "Unknown")
        out.append(
            Competitor(
                name=name,
                url=url or "",
                positioning=snip[:200],
                pricing=None,
                target_customer=None,
                evidence_url=url or "",
                source_type="search_snippet",
            )
        )
    return out


def _compute_competitive_position_score(
    *,
    market_saturation_score: float,
    feature_parity_score: float,
    relative_positioning: RelativePositioning,
) -> float:
    s = 10.0
    s -= market_saturation_score * 0.35
    s -= feature_parity_score * 0.45
    if relative_positioning == "category_leader_candidate":
        s += 1.0
    if relative_positioning == "undifferentiated_clone":
        s -= 1.5
    return max(1.0, min(10.0, round(s, 2)))


def _infer_positioning(
    *,
    saturation: float,
    parity: float,
    has_unique: bool,
) -> RelativePositioning:
    if parity >= 7.5 and saturation >= 6.5:
        return "undifferentiated_clone"
    if has_unique and saturation <= 6.0 and parity <= 5.5:
        return "category_leader_candidate"
    if has_unique or parity <= 5.0:
        return "credible_niche_challenger"
    if saturation >= 7.0 and not has_unique:
        return "undifferentiated_clone"
    return "unclear"


@dataclass
class CategoryOsintBundle:
    cat: CategoryIntelLLM
    competitors: list[Competitor]
    saturation: float
    matched_incumbents: list[str]
    major_names: list[str]
    comp_blob: str
    md_lower: str
    blob: str


def fetch_category_osint_bundle(
    client: OpenAI,
    *,
    facts: WebsiteFactsOutput,
    combined_markdown: str,
    website_url: str,
    model: str = OPENAI_MODEL,
) -> CategoryOsintBundle:
    """One LLM call (category) + optional web search; parity in VC pack.

    Without ``TAVILY_API_KEY`` (or with ``EXTERNAL_WEB_SEARCH=0``) no paid search runs —
    competitor context comes from category LLM ``alternatives`` / ``major_incumbents`` only.
    """
    md_lower = (combined_markdown or "").lower()
    blob = "\n".join(
        [
            facts.company_name or "",
            facts.one_liner or "",
            facts.product_description or "",
            facts.target_customer or "",
            facts.market_claims or "",
            facts.pricing_signals or "",
        ]
    )[:12000]

    cat_llm = json_llm(
        client,
        system=(
            "You are a VC competitive analyst. Output strict JSON matching CategoryIntelLLM: "
            "category, subcategories[], buyer, alternatives[], search_queries[] (5–8 concrete web queries), "
            "major_incumbents[] (5–15 dominant vendors or platforms in THIS category only — "
            "regionally or globally strong names relevant to the facts; never a generic unrelated list). "
            "Also force major_incumbents to include 5-10 major competitors/incumbents that a buyer would realistically shortlist "
            "using broad category knowledge (not only current OSINT snippets). "
            "alternatives[]: concrete competitor product names a buyer would compare (strings). "
            "No prose outside JSON."
        ),
        user=f"Website root: {website_url}\n\nFACTS:\n{blob}",
        model=model,
        max_tokens=min(1200, TOK_WEBSITE_SCORES_OUT),
        response_model=CategoryIntelLLM,
    )

    major_names = _normalize_incumbent_names(cat_llm.major_incumbents)

    use_search = os.getenv("WEBSITE_VC_WEB_SEARCH", "0").strip().lower() in ("1", "true", "yes", "on")
    provider, live = get_research_provider() if use_search else (NullExternalResearchProvider(), False)
    competitors: list[Competitor] = []
    seen_urls: set[str] = set()
    queries = (cat_llm.search_queries or [])[:8]
    if not queries:
        queries = [
            f"{cat_llm.category} competitors",
            f"{facts.company_name} alternatives",
        ]

    for q in queries:
        if len(competitors) >= 14:
            break
        try:
            batch = provider.search(q, max_results=3)
        except Exception:
            batch = []
        for c in _sources_to_competitors(batch, q):
            key = (c.url or c.name).lower()
            if key and key not in seen_urls:
                seen_urls.add(key)
                competitors.append(c)

    if not live or not competitors:
        competitors = _name_only_competitors_from_category(cat_llm)

    has_live_snippets = any(
        c.source_type == "search_snippet" and (c.url or "").strip() for c in competitors
    )
    search_names_blob = " ".join(
        c.name for c in competitors if c.source_type == "search_snippet" and (c.url or "").strip()
    ).lower()
    alt_blob = " ".join(cat_llm.alternatives or []).lower()
    text_for_major_match = f"{md_lower} {blob.lower()} {alt_blob} {search_names_blob}".strip()
    matched_incumbents = _matched_incumbents(major_names, text_for_major_match)

    saturation = compute_market_saturation(
        competitors,
        category=cat_llm.category,
        markdown_lower=md_lower,
        major_incumbents=major_names,
        has_live_search_snippets=has_live_snippets,
    )

    comp_blob = json.dumps([c.model_dump() for c in competitors[:12]], ensure_ascii=False)
    return CategoryOsintBundle(
        cat=cat_llm,
        competitors=competitors,
        saturation=saturation,
        matched_incumbents=matched_incumbents,
        major_names=major_names,
        comp_blob=comp_blob,
        md_lower=md_lower,
        blob=blob,
    )


def finalize_competitive_intelligence(
    bundle: CategoryOsintBundle,
    fp: FeatureParityLLM,
) -> tuple[CompetitiveIntelligenceOutput, float]:
    cat_llm = bundle.cat
    saturation = bundle.saturation
    matched_incumbents = bundle.matched_incumbents
    major_names = bundle.major_names
    competitors = bundle.competitors
    blob = bundle.blob

    rel = _infer_positioning(
        saturation=saturation,
        parity=float(fp.feature_parity_score),
        has_unique=bool(fp.has_clear_unique_angle),
    )

    kill: list[str] = []
    if saturation >= 7.5 and not fp.has_clear_unique_angle:
        kill.append("crowded_market_no_clear_edge")
    if matched_incumbents and saturation >= 6:
        kill.append("strong_incumbents_with_brand_trust")
    if fp.feature_parity_score >= 7.5:
        kill.append("feature_parity_clone")
    if re.search(r"\b(app|mobile|consumer)\b", (cat_llm.category + blob).lower()) and fp.feature_parity_score >= 6:
        kill.append("commodity_app_category")

    # Reasoning-attached lines — never bare numbers.
    sat_band = "low" if saturation <= 3 else "moderate" if saturation <= 6 else "high"
    n_majors = len(major_names)
    n_matched = len(matched_incumbents)
    sat_line = (
        f"Market saturation = {saturation:.1f}/10 ({sat_band}) — "
        f"category '{cat_llm.category or 'unspecified'}', {n_majors} known major incumbents, "
        f"{n_matched} of them matched in OSINT snippets. "
        f"{'More incumbents → harder to break out.' if saturation >= 6 else 'Open enough for a strong wedge to win.'}"
    )
    fp_reason = (fp.feature_parity_reasoning or "").strip()
    fp_label = fp.is_unique_or_table_stakes or ("table_stakes" if fp.feature_parity_score >= 7 else "differentiated" if fp.feature_parity_score <= 4 else "mixed")
    fp_line = (
        f"Feature parity = {float(fp.feature_parity_score):.1f}/10 (high = clone risk) — "
        f"strongest competitor: {fp.strongest_competitor or 'unknown'}; classification: {fp_label}; "
        f"unique angle: {fp.unique_angle or 'not stated'}."
    )
    if fp_reason:
        fp_line += f" Why: {fp_reason}"
    why_not = [sat_line, fp_line]
    if kill:
        why_not.append("Kill-style risks fired: " + ", ".join(kill))

    out = CompetitiveIntelligenceOutput(
        category=cat_llm.category,
        subcategories=cat_llm.subcategories or [],
        category_major_incumbents=major_names,
        matched_major_incumbents=matched_incumbents,
        competitors=competitors[:20],
        market_saturation_score=saturation,
        feature_parity_score=float(fp.feature_parity_score),
        relative_positioning=rel,
        strongest_competitors=[c.name for c in competitors[:5] if c.name],
        differentiation_summary=fp.unique_angle or fp.why_competitor_may_win or "",
        kill_flags=kill,
        why_not_higher=why_not,
    )
    pos = _compute_competitive_position_score(
        market_saturation_score=saturation,
        feature_parity_score=float(fp.feature_parity_score),
        relative_positioning=rel,
    )
    return out, pos
