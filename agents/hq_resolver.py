"""Deterministic HQ resolver (no LLM).

Why:
- Website crawls often omit HQ even when it's easily discoverable via LinkedIn snippets.
- We want evidence-backed HQ (quote + URL), not guesses.
- This module performs *cheap* web-search snippet parsing (e.g. Tavily) and returns:
  VERIFIED / LIKELY / CONFLICTING / INSUFFICIENT_EVIDENCE.

Important:
- Unknown is better than wrong.
- No quote -> no claim.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from agents.legal_signal_extractor import LegalTextInput, extract_legal_signals
from agents.external_research import get_research_provider
from agents.website_enrichment import enrich_from_markdown
from agents.schemas_gate25 import ExternalSource
from tools.legal_blocks import extract_legal_blocks_from_html
from tools.website_to_markdown import fetch_website_markdown


HQStatus = str  # VERIFIED | LIKELY | CONFLICTING | INSUFFICIENT_EVIDENCE


@dataclass
class HQEvidence:
    source_url: str
    source_type: str
    raw_quote: str
    id: str = ""
    claim_type: str = "unknown"
    extracted_city: str = ""
    extracted_country: str = ""
    address: str = ""
    weight: float = 0.0
    confidence: float = 0.0
    is_operating_signal: bool = True
    is_legal_signal: bool = False


@dataclass
class HQResolution:
    status: HQStatus
    hq_city: str = ""
    hq_country: str = ""
    confidence: float = 0.0
    summary: str = ""
    legal_registered_office: dict = None  # type: ignore[assignment]
    operating_hq: dict = None  # type: ignore[assignment]
    market_focus: dict = None  # type: ignore[assignment]
    legal_entity: dict = None  # type: ignore[assignment]
    final_geo_for_vc_screening: dict = None  # type: ignore[assignment]
    llm_tokens_used: int = 0
    search_calls: int = 0
    tavily_credits_estimated: int = 0
    evidence: list[HQEvidence] = None  # type: ignore[assignment]
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = []
        if self.warnings is None:
            self.warnings = []
        if self.legal_registered_office is None:
            self.legal_registered_office = {
                "country": None,
                "city": None,
                "address": None,
                "confidence": None,
                "evidence_ids": [],
            }
        if self.operating_hq is None:
            self.operating_hq = {
                "country": None,
                "city": None,
                "confidence": None,
                "evidence_ids": [],
            }
        if self.market_focus is None:
            self.market_focus = {"countries": [], "regions": [], "evidence_ids": []}
        if self.legal_entity is None:
            self.legal_entity = {"name": None, "legal_form": None, "registry_ids": []}
        if self.final_geo_for_vc_screening is None:
            self.final_geo_for_vc_screening = {
                "country": self.hq_country or None,
                "city": self.hq_city or None,
                "basis": None,
                "confidence": self.confidence if self.confidence else None,
            }


_CITY_TO_COUNTRY = {
    # CEE / common
    "belgrade": "Serbia",
    "novi sad": "Serbia",
    "warsaw": "Poland",
    "warszawa": "Poland",
    "krakow": "Poland",
    "kraków": "Poland",
    "wrocław": "Poland",
    "wroclaw": "Poland",
    "poznań": "Poland",
    "poznan": "Poland",
    "gdańsk": "Poland",
    "gdansk": "Poland",
    "łódź": "Poland",
    "lodz": "Poland",
    "katowice": "Poland",
    "prague": "Czech Republic",
    "praha": "Czech Republic",
    "brno": "Czech Republic",
    "bratislava": "Slovakia",
    "budapest": "Hungary",
    "bucharest": "Romania",
    "bucurești": "Romania",
    "cluj-napoca": "Romania",
    "sofia": "Bulgaria",
    "zagreb": "Croatia",
    "ljubljana": "Slovenia",
    "vilnius": "Lithuania",
    "riga": "Latvia",
    "tallinn": "Estonia",
    "kyiv": "Ukraine",
    "kiev": "Ukraine",
    # Western Europe (less common but worth catching)
    "berlin": "Germany",
    "munich": "Germany",
    "münchen": "Germany",
    "amsterdam": "Netherlands",
    "rotterdam": "Netherlands",
    "paris": "France",
    "london": "United Kingdom",
    "madrid": "Spain",
    "barcelona": "Spain",
    "lisbon": "Portugal",
    "lisboa": "Portugal",
    "dublin": "Ireland",
    "stockholm": "Sweden",
    "helsinki": "Finland",
    "copenhagen": "Denmark",
    "københavn": "Denmark",
    "oslo": "Norway",
    "vienna": "Austria",
    "wien": "Austria",
    "zurich": "Switzerland",
    "zürich": "Switzerland",
    # US common for parsing examples
    "austin": "United States",
    "san francisco": "United States",
    "new york": "United States",
    "boston": "United States",
    "delaware": "United States",
}

_COUNTRY_ADJECTIVE = {
    "serbian": "Serbia",
    "polish": "Poland",
    "romanian": "Romania",
    "bulgarian": "Bulgaria",
    "hungarian": "Hungary",
    "czech": "Czech Republic",
    "slovak": "Slovakia",
    "croatian": "Croatia",
    "slovenian": "Slovenia",
    "ukrainian": "Ukraine",
    "lithuanian": "Lithuania",
    "latvian": "Latvia",
    "estonian": "Estonia",
}


_US_STATE_ABBR = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_operating_hq_from_markdown(
    pages: list, *, domain_hint: str
) -> tuple[str, str, list[HQEvidence]]:
    """Best-effort operating-HQ extraction from already-crawled markdown.

    Strategy:
      1. Scan /contact, /about and footer-like pages first (higher weight).
      2. Look for known city names from `_CITY_TO_COUNTRY`.
      3. Look for Polish postal pattern (XX-XXX) plus known city as a strong signal.
      4. Look for "based in <City>", "headquartered in <City>", "office in <City>".

    Returns (city, country, evidence_list). City is normalized capitalised.
    """
    if not pages:
        return "", "", []

    def _page_score(url: str) -> float:
        u = (url or "").lower()
        if any(x in u for x in ("/contact", "/about", "/imprint", "/legal", "/privacy", "/terms", "/career", "/jobs")):
            return 1.0
        if u.endswith("/"):
            return 0.7
        return 0.5

    aliases = {  # normalize alternative spellings to canonical English label
        "warszawa": "Warsaw",
        "warsaw": "Warsaw",
        "kraków": "Krakow",
        "krakow": "Krakow",
        "wrocław": "Wrocław",
        "wroclaw": "Wrocław",
        "poznań": "Poznań",
        "poznan": "Poznań",
        "gdańsk": "Gdańsk",
        "gdansk": "Gdańsk",
        "łódź": "Łódź",
        "lodz": "Łódź",
    }

    cities = sorted(_CITY_TO_COUNTRY.keys(), key=len, reverse=True)
    pattern_city = re.compile(
        r"\b(" + "|".join(re.escape(c) for c in cities) + r")\b",
        flags=re.IGNORECASE,
    )
    pattern_polish_postal = re.compile(r"\b(\d{2}-\d{3})\s+([A-ZŻŹĆĄŚĘŁÓŃ][\wŻŹĆĄŚĘŁÓŃżźćąśęłóń\-]{2,40})", re.UNICODE)
    pattern_based_in = re.compile(
        r"\b(?:based\s+in|headquartered\s+in|HQ\s+in|office\s+in|located\s+in)\s+([A-Za-zŻŹĆĄŚĘŁÓŃżźćąśęłóń' \-]{2,40})",
        re.IGNORECASE,
    )

    score_by_country: dict[str, float] = {}
    city_votes: dict[str, dict[str, float]] = {}  # country -> {city: score}
    evidence: list[HQEvidence] = []

    for p in pages:
        text = (getattr(p, "markdown", "") or "")
        if not text or not getattr(p, "fetch_ok", False):
            continue
        url = getattr(p, "url", "") or ""
        page_w = _page_score(url)

        # 1) explicit "based in / headquartered in / office in"
        for m in pattern_based_in.finditer(text):
            cand = _norm(m.group(1)).strip(" .,;:")
            cand_low = cand.lower()
            if cand_low in _CITY_TO_COUNTRY:
                country = _CITY_TO_COUNTRY[cand_low]
                canon = aliases.get(cand_low, cand.title())
                w = 0.85 * page_w
                score_by_country[country] = score_by_country.get(country, 0.0) + w
                city_votes.setdefault(country, {})[canon] = city_votes.get(country, {}).get(canon, 0.0) + w
                evidence.append(
                    HQEvidence(
                        source_url=url,
                        source_type="official_website_text",
                        raw_quote=_norm(m.group(0))[:160],
                        extracted_city=canon,
                        extracted_country=country,
                        weight=round(w, 3),
                        is_operating_signal=True,
                        is_legal_signal=False,
                    )
                )

        # 2) Polish postal + city
        for m in pattern_polish_postal.finditer(text):
            cand = _norm(m.group(2))
            cand_low = cand.lower()
            if cand_low in _CITY_TO_COUNTRY and _CITY_TO_COUNTRY[cand_low] == "Poland":
                country = "Poland"
                canon = aliases.get(cand_low, cand.title())
                w = 0.95 * page_w
                score_by_country[country] = score_by_country.get(country, 0.0) + w
                city_votes.setdefault(country, {})[canon] = city_votes.get(country, {}).get(canon, 0.0) + w
                evidence.append(
                    HQEvidence(
                        source_url=url,
                        source_type="official_website_text",
                        raw_quote=_norm(m.group(0))[:160],
                        extracted_city=canon,
                        extracted_country=country,
                        weight=round(w, 3),
                        is_operating_signal=True,
                        is_legal_signal=False,
                    )
                )

        # 3) plain city mentions on contact/footer pages
        if page_w >= 0.7:
            seen_cities_on_page: set[str] = set()
            for m in pattern_city.finditer(text):
                cand_low = m.group(1).lower()
                if cand_low in seen_cities_on_page:
                    continue
                seen_cities_on_page.add(cand_low)
                country = _CITY_TO_COUNTRY.get(cand_low)
                if not country:
                    continue
                canon = aliases.get(cand_low, m.group(1).title())
                w = 0.55 * page_w
                score_by_country[country] = score_by_country.get(country, 0.0) + w
                city_votes.setdefault(country, {})[canon] = city_votes.get(country, {}).get(canon, 0.0) + w
                # Quote the immediate context for the evidence.
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 40)
                evidence.append(
                    HQEvidence(
                        source_url=url,
                        source_type="official_website_text",
                        raw_quote=_norm(text[start:end]).strip()[:160],
                        extracted_city=canon,
                        extracted_country=country,
                        weight=round(w, 3),
                        is_operating_signal=True,
                        is_legal_signal=False,
                    )
                )

    if not score_by_country:
        return "", "", []

    top_country = max(score_by_country.items(), key=lambda x: x[1])[0]
    top_city = ""
    if top_country in city_votes and city_votes[top_country]:
        top_city = max(city_votes[top_country].items(), key=lambda x: x[1])[0]
    # Keep only evidence for the chosen country, ordered by weight.
    keep = [e for e in evidence if (e.extracted_country == top_country)]
    keep = sorted(keep, key=lambda e: e.weight, reverse=True)[:8]
    _ = domain_hint  # kept for parity / future site-bound filtering
    return top_city, top_country, keep


def _domain_hint(domain: str) -> str:
    d = (domain or "").strip().lower()
    d = d.replace("https://", "").replace("http://", "")
    d = d[4:] if d.startswith("www.") else d
    d = d.strip("/").split("/")[0]
    return d


def _looks_like_domain(s: str) -> bool:
    t = (s or "").strip().lower()
    return "." in t and " " not in t and len(t) <= 120


def _is_relevant_source(
    src: ExternalSource, *, domain_hint: str, strict_domain_match: bool, company_name: str
) -> bool:
    """Reject unrelated entities when input is ambiguous (domain-only)."""
    url = (src.url or "").strip().lower()
    title = (src.title or "").strip().lower()
    snip = (src.snippet or "").strip().lower()
    if not url:
        return False

    # Always accept official-domain pages.
    if domain_hint and domain_hint in url:
        return True

    # In strict mode, require a domain mention in snippet/title too.
    if strict_domain_match:
        return bool(domain_hint) and (domain_hint in snip or domain_hint in title)

    # Non-strict: accept sources that mention the company name (press/db).
    cn = (company_name or "").strip().lower()
    if cn and cn not in {domain_hint, domain_hint.replace(".", " ")}:
        if cn in title or cn in snip:
            return True

    # LinkedIn can be relevant if snippet contains the official website domain.
    if "linkedin" in url and domain_hint and domain_hint in snip:
        return True

    return False


def _extract_hq_from_snippet(snippet: str) -> tuple[str, str, str]:
    """
    Returns (city, country, quote) based on a snippet containing "Headquarters:".
    Quote is the smallest useful substring to keep as evidence.
    """
    s = _norm(snippet)
    if not s:
        return "", "", ""

    # Example: "Headquarters: Belgrade. Type: Company."
    # Snippets often pack multiple fields separated by "·" and sentences; we must cut tightly.
    m = re.search(r"\b(?:Headquarters|HQ location|HQ)\b\s*[:\-]?\s*(.+)$", s, flags=re.I)
    if not m:
        return "", "", ""
    loc = _norm(m.group(1))
    # Crunchbase-style phrasing: "Where is X's headquarters? X is located in Dover, Delaware, United States."
    if re.search(r"\bis located in\b", loc, flags=re.I):
        loc = re.split(r"\bis located in\b", loc, maxsplit=1, flags=re.I)[1].strip()
    # Cut at common follow-up fields present in SERP snippets.
    loc = re.split(
        r"\s+\b(?:Type|Industry|Company size|Website|Followers?|Founded|Specialties)\s*:\s*",
        loc,
        maxsplit=1,
        flags=re.I,
    )[0]
    # Cut at typical separators.
    loc = re.split(r"\s*[·\|]\s*", loc, maxsplit=1)[0]
    # Cut at end of first sentence in most cases.
    # (We avoid overfitting; this is only for snippet parsing.)
    loc = loc.split(". ")[0]
    loc = loc.strip().strip(".").strip()
    if not loc:
        return "", "", ""

    # Keep the quote minimal but informative.
    quote = f"Headquarters: {loc}"

    # City, ST -> USA
    m_us = re.match(r"^([A-Za-z][A-Za-z \-']{1,60})\s*,\s*([A-Z]{2})$", loc)
    if m_us and m_us.group(2).upper() in _US_STATE_ABBR:
        return _norm(m_us.group(1)), "United States", quote

    # City, Country (rare in snippets but handle)
    m_cc = re.match(r"^(.+?)\s*,\s*([A-Za-z][A-Za-z \-']{2,60})$", loc)
    if m_cc:
        city = _norm(m_cc.group(1))
        country = _norm(m_cc.group(2))
        return city, country, quote

    # City-only: map if unambiguous in our map
    city = _norm(loc)
    country = _CITY_TO_COUNTRY.get(city.lower(), "")
    return city, country, quote


def _extract_based_in_from_snippet(snippet: str) -> tuple[str, str, str]:
    """Extract 'Belgrade-based' or 'Warsaw-based' style signals."""
    s = _norm(snippet)
    if not s:
        return "", "", ""
    m = re.search(r"\b([A-Z][A-Za-z \-']{2,60})-based\b", s)
    if not m:
        return "", "", ""
    city = _norm(m.group(1))
    country = _CITY_TO_COUNTRY.get(city.lower(), "")
    # Avoid false positives like "quiz-based" where the token isn't a place.
    if not country:
        return "", "", ""
    return city, country, f"{city}-based"


def _extract_country_label_from_snippet(snippet: str) -> tuple[str, str]:
    """Extract 'Serbian startup' style country-only labels."""
    s = _norm(snippet).lower()
    if not s:
        return "", ""
    m = re.search(r"\b(" + "|".join(re.escape(k) for k in _COUNTRY_ADJECTIVE.keys()) + r")\s+startup\b", s)
    if not m:
        return "", ""
    adj = m.group(1).strip().lower()
    return _COUNTRY_ADJECTIVE.get(adj, ""), f"{adj} startup"


def _is_legal_context(snippet: str) -> bool:
    s = (snippet or "").lower()
    return any(k in s for k in ("incorporated", "registered office", "registered in", "inc.", "llc", "ltd", "delaware"))


def _weight_for_source(url: str) -> tuple[float, str]:
    u = (url or "").lower()
    if "linkedin.com/company" in u or "linkedin." in u and "/company" in u:
        return 0.90, "linkedin_company"
    if "prepia.com" in u and any(p in u for p in ("/jobs", "/careers", "/current-jobs", "/job")):
        return 0.85, "official_website_jobs"
    if "example.vc" in u:
        return 0.85, "investor_portfolio"
    if any(x in u for x in ("therecursive.com", "ain.ua", "balkanengineer.com", "nin.rs", "vestbee.com")):
        return 0.75, "reputable_press"
    if any(x in u for x in ("crunchbase.com", "dealroom.co", "preqin.com", "tracxn.com")):
        return 0.70, "startup_database"
    return 0.45, "weak_directory"


def _pick_linkedin_company_sources(
    sources: list[ExternalSource], *, domain: str, company_name: str
) -> list[ExternalSource]:
    d = _domain_hint(domain)
    cn = (company_name or "").strip().lower()
    out: list[ExternalSource] = []
    for s in sources:
        url = (s.url or "").lower()
        snip = (s.snippet or "").lower()
        title = (s.title or "").lower()
        if "linkedin." not in url and "linkedin" not in title and "linkedin" not in snip:
            continue
        if "/company" not in url:
            continue
        # Prefer cases where snippet includes the official site domain (like your Google example).
        if d and d in snip:
            out.append(s)
            continue
        # Otherwise allow if company name appears in title.
        if cn and cn in title:
            out.append(s)
            continue
    return out[:8]


def _pick_candidate_sources(
    sources: list[ExternalSource], *, domain: str, company_name: str
) -> list[ExternalSource]:
    """Keep only likely-relevant sources (official domain, LinkedIn, press, databases)."""
    d = _domain_hint(domain)
    cn = (company_name or "").strip().lower()
    out: list[ExternalSource] = []
    for s in sources:
        url = (s.url or "").strip()
        if not url:
            continue
        u = url.lower()
        sn = (s.snippet or "").lower()
        tl = (s.title or "").lower()
        # keep official site pages
        if d and d in u:
            out.append(s)
            continue
        # keep linkedin
        if "linkedin" in u:
            out.append(s)
            continue
        # keep common press/db if company mentioned
        if cn and (cn in sn or cn in tl):
            out.append(s)
            continue
    return out[:12]


def _safe_domain(s: str) -> str:
    d = _domain_hint(s) or "domain"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", d)


def _dump_debug(debug_hq: bool, domain: str, payloads: dict[str, object]) -> None:
    if not debug_hq:
        return
    base = Path("artifacts") / "hq_debug" / _safe_domain(domain)
    base.mkdir(parents=True, exist_ok=True)
    for name, obj in payloads.items():
        path = base / name
        if name.endswith(".md"):
            path.write_text(str(obj or ""), encoding="utf-8")
        else:
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_hq_country(
    *,
    domain: str,
    company_name: str,
    max_results: int = 10,
    strict_domain_match: bool = False,
    debug_hq: bool = False,
) -> HQResolution:
    """
    Deterministic HQ resolver using web-search snippets (e.g. Tavily).
    No LLM calls. May incur search-provider cost if enabled.
    """
    # Phase 1: official website first (deterministic, no LLM/Tavily).
    md = None
    raw_pages_dbg: list[dict[str, object]] = []
    legal_blocks_dbg: list[dict[str, object]] = []
    legal_signals_dbg: dict[str, object] = {}
    try:
        md = fetch_website_markdown(domain, max_pages=12, timeout_seconds=20.0)
        raw_pages_dbg = [
            {
                "url": p.url,
                "status_code": p.status_code,
                "html_length": len(p.raw_html or ""),
                "text_length": p.text_length,
                "has_footer_selector": ("<footer" in (p.raw_html or "").lower()),
                "first_500_chars_text": _norm((p.markdown or "")[:500]),
            }
            for p in (md.pages or [])
        ]
        legal_inputs: list[LegalTextInput] = []
        for p in (md.pages or []):
            blocks = extract_legal_blocks_from_html(p.raw_html or "", p.url)
            for b in blocks:
                legal_blocks_dbg.append(b.to_dict())
                legal_inputs.append(
                    LegalTextInput(text=b.text, source_url=b.source_url, source_type=f"official_website_{b.selector}")
                )
            if (p.markdown or "").strip():
                legal_inputs.append(
                    LegalTextInput(text=p.markdown, source_url=p.url, source_type="official_website_markdown")
                )
        if (md.combined_markdown or "").strip():
            legal_inputs.append(
                LegalTextInput(text=md.combined_markdown, source_url=md.root_url, source_type="official_website_markdown")
            )
        legal_signals = extract_legal_signals(legal_inputs)
        legal_signals_dbg = legal_signals.to_dict()

        if legal_signals.registered_country or legal_signals.registry_ids:
            evid: list[HQEvidence] = []
            for e in (legal_signals.evidence or []):
                evid.append(
                    HQEvidence(
                        source_url=e.source_url,
                        source_type=e.source_type,
                        raw_quote=e.raw_quote,
                        id=e.id,
                        claim_type=e.claim_type,
                        extracted_city=e.city or "",
                        extracted_country=e.country or "",
                        address=e.address or "",
                        weight=float(e.weight or 0.0),
                        confidence=float(e.confidence or 0.0),
                        is_operating_signal=e.claim_type in {"operating_location"},
                        is_legal_signal=e.claim_type in {"legal_registration", "registered_address", "legal_form", "legal_entity_name"},
                    )
                )

            country = legal_signals.registered_country or ""
            city = legal_signals.registered_city or ""
            conf = 0.95 if country else 0.75
            out = HQResolution(
                status="LEGAL_WEBSITE_ONLY",
                hq_city=city,
                hq_country=country,
                confidence=conf,
                summary=(
                    f"Legal registered office resolved from official website legal text: "
                    f"{(city + ', ') if city else ''}{country or 'unknown country'}."
                ),
                llm_tokens_used=0,
                search_calls=0,
                tavily_credits_estimated=0,
                evidence=evid[:10],
                warnings=list(legal_signals.warnings or []),
            )
            out.legal_registered_office = {
                "country": country or None,
                "city": city or None,
                "address": legal_signals.registered_address,
                "confidence": conf,
                "evidence_ids": [x.id for x in evid if x.is_legal_signal],
            }
            out.operating_hq = {"country": None, "city": None, "confidence": None, "evidence_ids": []}
            out.legal_entity = {
                "name": legal_signals.legal_entity_name,
                "legal_form": legal_signals.legal_form,
                "registry_ids": [x.to_dict() for x in (legal_signals.registry_ids or [])],
            }
            out.final_geo_for_vc_screening = {
                "country": country or None,
                "city": city or None,
                "basis": "legal_registered_office",
                "confidence": conf,
            }
            _dump_debug(
                debug_hq,
                domain,
                {
                    "raw_pages.json": raw_pages_dbg,
                    "markdown.md": md.combined_markdown or "",
                    "legal_blocks.json": legal_blocks_dbg,
                    "legal_signals.json": legal_signals_dbg,
                    "evidence.json": [asdict(x) for x in out.evidence],
                    "final_decision.json": asdict(out),
                },
            )
            return out
    except Exception:
        pass

    # Phase 1b: operating-HQ from already-crawled markdown (city map / postal / "based in").
    # This is the crucial fallback for sites that don't publish a Polish legal block
    # in the footer (e.g. flyingbisons.com just says "WeWork office in Warsaw").
    op_city, op_country, op_evidence = "", "", []
    if md and (md.combined_markdown or "").strip():
        op_city, op_country, op_evidence = _extract_operating_hq_from_markdown(
            list(md.pages or []), domain_hint=_domain_hint(domain)
        )
    if op_country:
        # We have at least operating-HQ from the official site -> return without Tavily.
        n_independent = len({(e.source_url, e.extracted_city) for e in op_evidence})
        agg = sum(float(e.weight) for e in op_evidence)
        confidence = min(0.92, 0.55 + 0.10 * n_independent + 0.05 * (1 if agg >= 1.5 else 0))
        out = HQResolution(
            status="LIKELY",
            hq_city=op_city,
            hq_country=op_country,
            confidence=round(confidence, 2),
            summary=(
                f"Operating HQ inferred from official website text: "
                f"{(op_city + ', ') if op_city else ''}{op_country} "
                f"(no_legal_block_in_footer; sources={n_independent})."
            ),
            llm_tokens_used=0,
            search_calls=0,
            tavily_credits_estimated=0,
            evidence=op_evidence,
            warnings=["operating_only_no_legal_block"],
        )
        out.operating_hq = {
            "country": op_country,
            "city": op_city or None,
            "confidence": out.confidence,
            "evidence_ids": [],
        }
        out.legal_registered_office = {
            "country": None,
            "city": None,
            "address": None,
            "confidence": None,
            "evidence_ids": [],
        }
        out.final_geo_for_vc_screening = {
            "country": op_country,
            "city": op_city or None,
            "basis": "operating_hq_official_website",
            "confidence": out.confidence,
        }
        _dump_debug(
            debug_hq,
            domain,
            {
                "raw_pages.json": raw_pages_dbg,
                "markdown.md": md.combined_markdown if md else "",
                "legal_blocks.json": legal_blocks_dbg,
                "legal_signals.json": legal_signals_dbg,
                "evidence.json": [asdict(x) for x in out.evidence],
                "final_decision.json": asdict(out),
            },
        )
        return out

    # Cost guard: in strict mode (domain-only input) we cap Tavily credits.
    # Decision rules:
    #   * crawl OK but no legal evidence -> skip Tavily (cheap path; most
    #     official sites *will* yield a footer claim, so further snippets are
    #     unlikely to add a domain-correct legal claim).
    #   * crawl FAILED (no readable content at all) -> we are blind. Run a
    #     small, tightly-budgeted Tavily fallback so we still return a HQ
    #     instead of "0 search_calls / INSUFFICIENT_EVIDENCE". User explicitly
    #     wants HQ to "always" be found when the data exists.
    strict_fallback_budget = 0
    if strict_domain_match:
        no_content = (
            (not bool(md))
            or (not (md.combined_markdown or "").strip())
            or all((len(p.raw_html or "") == 0 and int(p.text_length or 0) == 0) for p in (md.pages or []))
        )
        if not no_content:
            msg = "Official website crawl did not expose explicit HQ evidence; Tavily fallback skipped (strict domain mode)."
            out = HQResolution(
                status="INSUFFICIENT_EVIDENCE",
                confidence=0.0,
                summary=msg,
                llm_tokens_used=0,
                search_calls=0,
                tavily_credits_estimated=0,
                warnings=["strict_mode_no_search_fallback"],
            )
            _dump_debug(
                debug_hq,
                domain,
                {
                    "raw_pages.json": raw_pages_dbg,
                    "markdown.md": (md.combined_markdown if md else ""),
                    "legal_blocks.json": legal_blocks_dbg,
                    "legal_signals.json": legal_signals_dbg,
                    "evidence.json": [],
                    "final_decision.json": asdict(out),
                },
            )
            return out
        # Crawl truly failed -> allow a TINY snippet fallback (max 2 calls).
        strict_fallback_budget = int(os.getenv("HQ_RESOLVER_STRICT_FALLBACK_CALLS", "2") or "2")

    provider, live = get_research_provider()
    if not live:
        out = HQResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="Official site had no parseable HQ and web-search provider unavailable.",
            llm_tokens_used=0,
            search_calls=0,
            tavily_credits_estimated=0,
            warnings=["provider_unavailable"],
        )
        _dump_debug(
            debug_hq,
            domain,
            {
                "raw_pages.json": raw_pages_dbg,
                "markdown.md": (md.combined_markdown if md else ""),
                "legal_blocks.json": legal_blocks_dbg,
                "legal_signals.json": legal_signals_dbg,
                "evidence.json": [],
                "final_decision.json": asdict(out),
            },
        )
        return out

    # Run a small set of queries: LinkedIn HQ field is not always present in snippets.
    # In strict (domain-only) mode after a failed crawl we ground every query in
    # the domain itself, so Tavily cannot drift to look-alike companies.
    d_hint = _domain_hint(domain)
    if strict_domain_match and strict_fallback_budget > 0:
        queries = [
            f"site:linkedin.com/company {d_hint}",
            f"\"{d_hint}\" headquarters",
            f"{company_name or d_hint} headquarters {d_hint}",
        ]
    else:
        queries = [
            f"{company_name} headquarters",
            f"{company_name} headquarters {domain}",
            f"{company_name} LinkedIn headquarters {domain}",
            f"{company_name} Belgrade Serbia headquarters",
        ]
    if strict_domain_match and strict_fallback_budget > 0:
        max_search_calls = max(1, strict_fallback_budget)
    else:
        max_search_calls = int(os.getenv("HQ_RESOLVER_MAX_SEARCH_CALLS", "3") or "3")
    search_calls = 0
    all_sources: list[ExternalSource] = []
    seen: set[str] = set()
    ev: list[HQEvidence] = []
    domain_hint = _domain_hint(domain)
    for q in queries:
        if search_calls >= max_search_calls:
            break
        search_calls += 1
        for s in provider.search(q.strip(), max_results=max_results):
            u = (s.url or "").strip()
            if u and u in seen:
                continue
            if u:
                seen.add(u)
            all_sources.append(s)
            if s.url and s.snippet:
                if not _is_relevant_source(
                    s,
                    domain_hint=domain_hint,
                    strict_domain_match=strict_domain_match,
                    company_name=company_name,
                ):
                    continue
                w, st = _weight_for_source(s.url)
                snippet = s.snippet or ""
                city, country, quote = _extract_hq_from_snippet(snippet)
                if quote and city:
                    ev.append(
                        HQEvidence(
                            source_url=s.url,
                            source_type=st,
                            raw_quote=quote,
                            extracted_city=city,
                            extracted_country=country,
                            weight=w,
                            is_operating_signal=not _is_legal_context(snippet),
                            is_legal_signal=_is_legal_context(snippet),
                        )
                    )
                    continue
                city2, country2, quote2 = _extract_based_in_from_snippet(snippet)
                if quote2 and city2:
                    ev.append(
                        HQEvidence(
                            source_url=s.url,
                            source_type=st,
                            raw_quote=quote2,
                            extracted_city=city2,
                            extracted_country=country2,
                            weight=min(w, 0.80),
                            is_operating_signal=True,
                            is_legal_signal=False,
                        )
                    )
                    continue
                ctry, quote3 = _extract_country_label_from_snippet(snippet)
                if ctry and quote3:
                    ev.append(
                        HQEvidence(
                            source_url=s.url,
                            source_type=st,
                            raw_quote=quote3,
                            extracted_city="",
                            extracted_country=ctry,
                            weight=min(w, 0.75),
                            is_operating_signal=True,
                            is_legal_signal=False,
                        )
                    )
        # Early stop: strong operating evidence already found.
        op = [x for x in ev if x.is_operating_signal and (x.extracted_country or "").strip()]
        if len(op) >= 2:
            top = sorted(op, key=lambda x: x.weight, reverse=True)[:2]
            if top[0].extracted_country == top[1].extracted_country:
                break
    cands = _pick_candidate_sources(all_sources, domain=domain, company_name=company_name)
    if not cands:
        return HQResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="No relevant sources returned for HQ resolution queries.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            warnings=["no_sources"],
        )

    if not ev:
        for s in cands:
            if not s.url or not s.snippet:
                continue
            if not _is_relevant_source(
                s,
                domain_hint=domain_hint,
                strict_domain_match=strict_domain_match,
                company_name=company_name,
            ):
                continue
            w, st = _weight_for_source(s.url)
            snippet = s.snippet or ""

            # 1) Explicit HQ fields
            city, country, quote = _extract_hq_from_snippet(snippet)
            if quote and city:
                ev.append(
                    HQEvidence(
                        source_url=s.url,
                        source_type=st,
                        raw_quote=quote,
                        extracted_city=city,
                        extracted_country=country,
                        weight=w,
                        is_operating_signal=not _is_legal_context(snippet),
                        is_legal_signal=_is_legal_context(snippet),
                    )
                )
                continue

            # 2) "Belgrade-based" style operating evidence
            city2, country2, quote2 = _extract_based_in_from_snippet(snippet)
            if quote2 and city2:
                ev.append(
                    HQEvidence(
                        source_url=s.url,
                        source_type=st,
                        raw_quote=quote2,
                        extracted_city=city2,
                        extracted_country=country2,
                        weight=min(w, 0.80),
                        is_operating_signal=True,
                        is_legal_signal=False,
                    )
                )
                continue

            # 3) Country label only ("Serbian startup")
            ctry, quote3 = _extract_country_label_from_snippet(snippet)
            if ctry and quote3:
                ev.append(
                    HQEvidence(
                        source_url=s.url,
                        source_type=st,
                        raw_quote=quote3,
                        extracted_city="",
                        extracted_country=ctry,
                        weight=min(w, 0.75),
                        is_operating_signal=True,
                        is_legal_signal=False,
                    )
                )

    if not ev:
        return HQResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="Sources found, but none contained explicit location evidence in snippets.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            warnings=["no_location_evidence_in_snippets"],
        )

    # Score operating vs legal separately
    op_scores: dict[str, float] = {}
    legal_scores: dict[str, float] = {}
    for e in ev:
        c = (e.extracted_country or "").strip()
        if not c:
            continue
        if e.is_legal_signal and not e.is_operating_signal:
            legal_scores[c] = legal_scores.get(c, 0.0) + float(e.weight)
        else:
            op_scores[c] = op_scores.get(c, 0.0) + float(e.weight)

    if not op_scores:
        # We may have city-only evidence, but no safe country mapping.
        return HQResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.0,
            summary="No safe operating-HQ country could be resolved from snippet evidence.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            evidence=sorted(ev, key=lambda x: x.weight, reverse=True)[:5],
            warnings=["no_operating_country"],
        )

    if not op_scores and legal_scores:
        # Only legal/incorporation-like evidence
        top_legal = max(legal_scores.items(), key=lambda x: x[1])[0]
        return HQResolution(
            status="INSUFFICIENT_EVIDENCE",
            confidence=0.35,
            summary=f"Only legal/incorporation-like location evidence found (e.g. {top_legal}); operating HQ unknown.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            evidence=ev[:5],
            warnings=["legal_only"],
        )

    # Conflict: two strong operating countries disagree
    strong_ops = sorted(op_scores.items(), key=lambda x: x[1], reverse=True)
    if len(strong_ops) >= 2 and strong_ops[0][1] >= 0.75 and strong_ops[1][1] >= 0.75 and strong_ops[0][0] != strong_ops[1][0]:
        return HQResolution(
            status="CONFLICTING",
            confidence=0.3,
            summary="Conflicting operating-HQ countries in snippet evidence; manual review required.",
            llm_tokens_used=0,
            search_calls=search_calls,
            tavily_credits_estimated=search_calls,
            evidence=ev[:6],
            warnings=["conflicting_operating_countries"],
        )

    # Pick top operating country and best city if present
    top_country = strong_ops[0][0] if strong_ops else ""
    city_votes: dict[str, float] = {}
    for e in ev:
        if e.extracted_city and (e.extracted_country or top_country) == top_country:
            city_votes[e.extracted_city] = city_votes.get(e.extracted_city, 0.0) + float(e.weight)
    top_city = max(city_votes.items(), key=lambda x: x[1])[0] if city_votes else ""

    score = float(op_scores.get(top_country, 0.0))
    n_independent = len({(e.source_type, e.source_url) for e in ev if (e.extracted_country or "") == top_country})
    verified = score >= 1.50 and n_independent >= 2
    conf = min(0.99, score / 2.50)
    if n_independent <= 1:
        conf = max(0.35, conf - 0.20)
    status = "VERIFIED" if verified else "LIKELY"
    summary = (
        f"Operating HQ inferred from snippet evidence: {top_city + ', ' if top_city else ''}{top_country} "
        f"(score={score:.2f}, sources={n_independent})."
    )
    out = HQResolution(
        status=status,
        hq_city=top_city,
        hq_country=top_country,
        confidence=round(conf, 2),
        summary=summary,
        llm_tokens_used=0,
        search_calls=search_calls,
        tavily_credits_estimated=search_calls,
        evidence=sorted(ev, key=lambda x: x.weight, reverse=True)[:5],
        warnings=[],
    )
    out.operating_hq = {
        "country": top_country or None,
        "city": top_city or None,
        "confidence": out.confidence,
        "evidence_ids": [x.id for x in out.evidence if x.is_operating_signal],
    }
    out.legal_registered_office = {
        "country": None,
        "city": None,
        "address": None,
        "confidence": None,
        "evidence_ids": [x.id for x in out.evidence if x.is_legal_signal],
    }
    out.final_geo_for_vc_screening = {
        "country": top_country or None,
        "city": top_city or None,
        "basis": "operating_hq",
        "confidence": out.confidence,
    }
    _dump_debug(
        debug_hq,
        domain,
        {
            "raw_pages.json": raw_pages_dbg,
            "markdown.md": (md.combined_markdown if md else ""),
            "legal_blocks.json": legal_blocks_dbg,
            "legal_signals.json": legal_signals_dbg,
            "evidence.json": [asdict(x) for x in out.evidence],
            "final_decision.json": asdict(out),
        },
    )
    return out


def enabled() -> bool:
    return (os.getenv("HQ_RESOLVER", "0") or "").strip().lower() in ("1", "true", "yes", "on")

