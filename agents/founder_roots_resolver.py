"""Optional web snippets pass for CEE founder / diaspora roots.

Sites often emphasize US HQ while omitting European origin. Fund mandates CEE *or*
diaspora — this module runs **country-agnostic** search queries, then scores snippets
against a shared CEE lexicon (aligned with ``config.criteria.CRITERIA['geographies']``).

Costs: Tavily when ``get_research_provider()`` is live.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

from agents.external_research import get_research_provider
from agents.schemas_gate25 import ExternalSource
from config.criteria import CRITERIA

# --- Structured CEE: country label, demonyms / adjectives, major cities (ASCII + local) ---
# Keep in sync with PRD / CRITERIA geographies; used only for *matching* snippets, not query bias.
_CEE_GEO_SIGNALS: list[tuple[str, list[str], list[str]]] = [
    ("Poland", ["Polish", "Polska"], ["Warsaw", "Warszawa", "Kraków", "Krakow", "Wrocław", "Wroclaw", "Gdańsk", "Gdansk", "Poznań", "Poznan", "Łódź", "Lodz", "Katowice", "Lublin"]),
    ("Lithuania", ["Lithuanian"], ["Vilnius", "Kaunas", "Klaipėda", "Klaipeda"]),
    ("Latvia", ["Latvian"], ["Riga", "Liepāja", "Liepaja", "Daugavpils"]),
    ("Estonia", ["Estonian"], ["Tallinn", "Tartu"]),
    ("Czech Republic", ["Czech", "Czechia"], ["Prague", "Praha", "Brno", "Ostrava"]),
    ("Slovakia", ["Slovak"], ["Bratislava", "Košice", "Kosice"]),
    ("Hungary", ["Hungarian", "Magyar"], ["Budapest", "Debrecen", "Szeged"]),
    ("Romania", ["Romanian"], ["Bucharest", "Bucuresti", "Cluj", "Timișoara", "Timisoara", "Iași", "Iasi"]),
    ("Bulgaria", ["Bulgarian"], ["Sofia", "Plovdiv", "Varna"]),
    ("Croatia", ["Croatian"], ["Zagreb", "Split", "Rijeka"]),
    ("Slovenia", ["Slovenian"], ["Ljubljana", "Maribor"]),
    ("Serbia", ["Serbian"], ["Belgrade", "Beograd", "Novi Sad", "Niš", "Nis"]),
    ("Ukraine", ["Ukrainian"], ["Kyiv", "Kiev", "Lviv", "Kharkiv", "Odesa", "Odessa", "Dnipro"]),
    ("Bosnia and Herzegovina", ["Bosnian", "Herzegovinian"], ["Sarajevo", "Banja Luka", "Mostar"]),
    ("Montenegro", ["Montenegrin"], ["Podgorica", "Cetinje"]),
    ("North Macedonia", ["Macedonian"], ["Skopje", "Bitola"]),
    ("Kosovo", ["Kosovar", "Kosovan"], ["Pristina", "Priština", "Prizren"]),
    ("Albania", ["Albanian"], ["Tirana", "Durrës", "Durres"]),
    ("Moldova", ["Moldovan"], ["Chișinău", "Chisinau", "Bălți", "Balti"]),
]


def _norm_token(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


@lru_cache(maxsize=1)
def _cee_lexicon_longest_first() -> tuple[str, ...]:
    """Lowercase phrases for substring match; longest first to prefer multi-word hits."""
    out: set[str] = set()

    for country, demonyms, cities in _CEE_GEO_SIGNALS:
        out.add(_norm_token(country))
        for d in demonyms:
            nd = _norm_token(d)
            if len(nd) >= 3:
                out.add(nd)
        for c in cities:
            nc = _norm_token(c)
            if len(nc) >= 3:
                out.add(nc)

    for g in CRITERIA.get("geographies") or []:
        gl = _norm_token(str(g))
        if not gl or "diaspora" in gl:
            continue
        out.add(gl)
        for part in re.split(r"[\s,;/]+", gl):
            p = _norm_token(part)
            if len(p) >= 3:
                out.add(p)

    # Regional / process phrases (English snippets)
    for phrase in (
        "central and eastern europe",
        "central europe",
        "eastern europe",
        "cee ",
        "cee,",
        "cee/",
        "emerging europe",
        "eu eastern",
        "balkan",
        "balkans",
        "western balkans",
        "visegrad",
        "v4 ",
        "yugoslav",
        "ex-yu",
        "ex yu",
        "diaspora",
        "founders from",
        "roots in",
        "based in zagreb",
        "based in warsaw",
        "university of zagreb",
        "university of warsaw",
    ):
        out.add(phrase.strip())

    # Drop ultra-short noisy tokens
    cleaned = {x for x in out if len(x) >= 3}
    return tuple(sorted(cleaned, key=len, reverse=True))


def roots_osint_enabled() -> bool:
    v = (os.getenv("FOUNDER_ROOTS_OSINT", "auto") or "").strip().lower()
    return v not in ("0", "false", "no", "off", "none", "disabled")


def roots_osint_mode() -> str:
    """``always`` = run whenever provider is live; ``auto`` = only US-looking HQ + no local CEE signal."""
    v = (os.getenv("FOUNDER_ROOTS_OSINT", "auto") or "").strip().lower()
    if v in ("1", "true", "yes", "on", "always"):
        return "always"
    return "auto"


def _domain_host(url_or_domain: str) -> str:
    d = (url_or_domain or "").strip().lower().replace("https://", "").replace("http://", "")
    d = d[4:] if d.startswith("www.") else d
    return d.split("/")[0].strip()


def _cee_in_blob(text: str) -> tuple[bool, str]:
    low = (text or "").lower()
    for t in _cee_lexicon_longest_first():
        if t in low:
            return True, t
    return False, ""


def _snippet_blob(sources: list[ExternalSource]) -> str:
    parts: list[str] = []
    for s in sources:
        parts.append(f"{s.title or ''} {s.snippet or ''}")
    return " ".join(parts)


def _extract_founder_names_from_snippets(sources: list[ExternalSource]) -> list[str]:
    """Best-effort founder-name extraction from search snippets/titles."""
    out: list[str] = []
    seen: set[str] = set()
    for s in sources:
        text = f"{s.title or ''}. {s.snippet or ''}"
        low = text.lower()
        if not any(k in low for k in ("founded by", "co-founded by", "founder", "co-founder", "established by")):
            continue
        for m in re.finditer(r"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,2})\b", text):
            nm = m.group(1).strip()
            nml = nm.lower()
            if not is_valid_person_name(nm, ""):
                continue
            if any(bad in nml for bad in ("linkedin", "europe", "riskseal", "fieldy", "pythagora")):
                continue
            if len(nm) < 5 or nml in seen:
                continue
            seen.add(nml)
            out.append(nm)
            if len(out) >= 6:
                return out
    return out


def is_valid_person_name(name: str, company_name: str) -> bool:
    nm = _norm_token(name)
    cn = _norm_token(company_name)
    if not nm:
        return False
    if cn and nm == cn:
        return False
    if any(token in nm for token in (" ltd", " inc", " sp. z o.o", " limited", " llc", " gmbh", " plc")):
        return False
    if len(nm.split()) < 2:
        return False
    return True


def _neutral_queries(company_name: str, host: str) -> list[str]:
    """No assumed country — let snippets carry geography; we classify with the CEE lexicon."""
    cn = (company_name or "").strip()
    h = (host or "").strip()
    base = [
        f"{cn} founders background nationality",
        f"{cn} founding team founders origin",
        f"{cn} {h} CEO founder team",
    ]
    return [q.strip() for q in base if q.strip()]


@dataclass
class FounderRootsResult:
    cee_signal: bool
    summary: str
    n_sources: int
    matched_signal: str = ""
    founder_names: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.founder_names is None:
            self.founder_names = []


def resolve_founder_roots_cee(
    *,
    company_name: str,
    website_url: str,
    max_results_per_query: int = 6,
    max_queries: int | None = None,
) -> FounderRootsResult:
    if not roots_osint_enabled():
        return FounderRootsResult(False, "", 0, "", [])
    provider, live = get_research_provider()
    if not live:
        return FounderRootsResult(False, "founder_roots_osint: search provider unavailable", 0, "", [])

    cn = (company_name or "").strip()
    host = _domain_host(website_url)
    if not cn:
        return FounderRootsResult(False, "", 0, "", [])

    mq = max_queries
    if mq is None:
        mq = int(os.getenv("FOUNDER_ROOTS_MAX_QUERIES", "3") or "3")
    mq = max(1, min(mq, 5))

    queries = _neutral_queries(cn, host)
    merged: list[ExternalSource] = []
    seen: set[str] = set()
    calls = 0
    for q in queries:
        if calls >= mq:
            break
        calls += 1
        for s in provider.search(q, max_results=max_results_per_query):
            u = (s.url or "").strip()
            if u and u not in seen:
                seen.add(u)
                merged.append(s)

    blob = _snippet_blob(merged)
    ok, token = _cee_in_blob(blob)
    founder_names = _extract_founder_names_from_snippets(merged)
    if not ok:
        return FounderRootsResult(
            False,
            f"founder_roots_osint: no CEE lexicon hit in snippets (n={len(merged)})",
            len(merged),
            "",
            founder_names,
        )

    # Domains that are not valid external sources for nationality evidence —
    # they're VC portfolio pages that only exist for Fund's own companies.
    _SKIP_DOMAINS = ("example.vc", "example.vc")

    def _is_valid_source(url: str) -> bool:
        u = (url or "").lower()
        return bool(u) and not any(d in u for d in _SKIP_DOMAINS)

    # Prefer authoritative external sources (Crunchbase, LinkedIn, news, etc.)
    _PREFERRED = ("crunchbase.com", "linkedin.com", "techcrunch.com", "forbes.com",
                  "bloomberg.com", "eu-startups.com", "dealroom.co", "tracxn.com")

    def _source_rank(url: str) -> int:
        u = (url or "").lower()
        for i, domain in enumerate(_PREFERRED):
            if domain in u:
                return i
        return len(_PREFERRED)

    quote = ""
    src_url = ""
    low_tok = token.lower()
    candidates = [
        s for s in merged
        if low_tok in f"{s.title or ''} {s.snippet or ''}".lower()
        and _is_valid_source(s.url or "")
    ]
    candidates.sort(key=lambda s: _source_rank(s.url or ""))
    if candidates:
        best = candidates[0]
        quote = (best.snippet or best.title or "").strip()
        src_url = (best.url or "").strip()
        if len(quote) > 280:
            quote = quote[:277].rstrip() + "…"
    summary = (
        f"cee_founder_roots_osint: snippet match for CEE/diaspora signal (“{token}”)"
        f"{(' — ' + quote) if quote else ''}"
        f"{(' [' + src_url + ']') if src_url else ''}"
    )
    return FounderRootsResult(True, summary, len(merged), token, founder_names)


def cee_lexicon_preview(*, limit: int = 40) -> list[str]:
    """Debug: first N lexicon entries (longest-first)."""
    return list(_cee_lexicon_longest_first()[:limit])
