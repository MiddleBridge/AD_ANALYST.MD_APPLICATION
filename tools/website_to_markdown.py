"""Fetch and crawl a startup website -> structured markdown for LLM screening."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import extruct
import httpx
import trafilatura
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

DEFAULT_MAX_PAGES = 10
DEFAULT_TIMEOUT = 20.0
PROBE_PATHS: list[str] = [
    "/",
    "/about",
    "/about-us",
    "/company",
    "/team",
    "/founders",
    "/leadership",
    "/contact",
    "/careers",
    "/career",
    "/jobs",
    "/job",
    "/customers",
    "/case-studies",
    "/pricing",
    "/privacy",
    "/privacy-policy",
    "/terms",
    "/terms-of-service",
    "/terms-and-conditions",
]


@dataclass
class WebsitePageRecord:
    url: str
    title: str
    meta_description: str
    raw_html: str
    markdown: str
    text_length: int
    fetch_ok: bool = True
    status_code: Optional[int] = None
    error: Optional[str] = None


@dataclass
class WebsiteMarkdownResult:
    root_url: str
    pages: list[WebsitePageRecord]
    combined_markdown: str
    fetch_warnings: list[str] = field(default_factory=list)
    extraction_quality_score: int = 5


def normalize_root_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("Empty URL")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    parsed = urlparse(u)
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


_BROWSER_UAS: list[str] = [
    # Recent macOS Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Recent Chrome on Linux (Tavily/Cloudflare-friendly)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.91 Safari/537.36",
    # Internal UA (kept for transparency / observability)
    "InovoWebsiteScreening/1.0",
]


_COMMON_TLD_SWAPS = {
    "pl": ["com", "io", "co", "ai", "eu"],
    "com": ["pl", "io", "co"],
    "io": ["com", "co", "ai"],
    "co": ["com", "io"],
    "ai": ["com", "io"],
    "eu": ["com", "io"],
}


def _origin_variants(root_url: str, *, include_tld_swaps: bool = False) -> list[str]:
    """Return candidate origins (https/http x www/non-www) preserving original first.

    When include_tld_swaps=True, also append a small set of plausible TLD swaps
    (e.g. flyingbisons.pl -> flyingbisons.com) for the case where DNS for the
    user-supplied TLD does not resolve at all.
    """
    parsed = urlparse(root_url)
    host = (parsed.netloc or "").lower()
    if not host:
        return [root_url]
    bare = host[4:] if host.startswith("www.") else host
    www = host if host.startswith("www.") else f"www.{host}"
    schemes = ["https", "http"]
    hosts: list[str] = []
    for h in [host, bare, www]:
        if h and h not in hosts:
            hosts.append(h)
    if include_tld_swaps:
        parts = bare.split(".")
        if len(parts) >= 2:
            tld = parts[-1]
            stem = ".".join(parts[:-1])
            swaps = _COMMON_TLD_SWAPS.get(tld, [])
            for new_tld in swaps:
                cand_bare = f"{stem}.{new_tld}"
                cand_www = f"www.{cand_bare}"
                for h in (cand_bare, cand_www):
                    if h not in hosts:
                        hosts.append(h)
    out: list[str] = []
    seen: set[str] = set()
    for s in schemes:
        for h in hosts:
            v = f"{s}://{h}"
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _resolve_reachable_origin(root_url: str) -> tuple[str, list[str]]:
    """Probe origin variants with HEAD/GET; return first reachable origin + warnings.

    We use this so that single-shot crawl of `https://flyingbisons.pl` does not
    silently return zero content when the site only resolves on `www.` or `https`.
    Two passes:
      1) host-as-given x scheme/www variants
      2) common TLD swaps (e.g. .pl -> .com) when no variant resolved
    """
    warnings: list[str] = []
    parsed = urlparse(root_url)
    host = (parsed.netloc or "").lower()
    base_origin = f"{parsed.scheme}://{host}"

    def _probe(origin: str) -> Optional[str]:
        for ua in _BROWSER_UAS[:2]:
            try:
                r = httpx.get(
                    origin + "/",
                    timeout=8.0,
                    follow_redirects=True,
                    headers={
                        "User-Agent": ua,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en;q=0.9, pl;q=0.8",
                    },
                )
                if r.status_code < 400 and (r.text or "").strip():
                    final_host = (urlparse(str(r.url)).netloc or host).lower()
                    return f"{urlparse(str(r.url)).scheme}://{final_host}"
            except Exception as exc:
                warnings.append(f"origin_probe_failed {origin}: {exc.__class__.__name__}")
                continue
        return None

    for origin in _origin_variants(root_url):
        final_origin = _probe(origin)
        if final_origin:
            if final_origin != base_origin:
                warnings.append(f"origin_resolved: {origin} -> {final_origin}")
            return final_origin, warnings

    # Pass 2: TLD swaps. Only used when nothing resolved in pass 1, to avoid
    # accidentally pointing the resolver at a different company on a different TLD.
    swap_candidates = [
        v for v in _origin_variants(root_url, include_tld_swaps=True) if v not in _origin_variants(root_url)
    ]
    for origin in swap_candidates:
        final_origin = _probe(origin)
        if final_origin:
            warnings.append(f"origin_tld_swap: {root_url} -> {final_origin}")
            return final_origin, warnings

    return base_origin, warnings


def _same_site(url: str, root: str) -> bool:
    def _norm(host: str) -> str:
        h = (host or "").lower().strip()
        return h[4:] if h.startswith("www.") else h

    try:
        return _norm(urlparse(url).netloc) == _norm(urlparse(root).netloc)
    except Exception:
        return False


def _candidate_urls(root_url: str, max_pages: int) -> list[str]:
    parsed = urlparse(root_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    urls: list[str] = []
    seen: set[str] = set()
    for path in PROBE_PATHS:
        full = origin if path == "/" else urljoin(origin, path)
        if full not in seen:
            seen.add(full)
            urls.append(full)
        if len(urls) >= max_pages:
            break
    return urls


def _extract_meta_description(html: str) -> str:
    hit = re.search(
        r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]+content=[\"']([^\"']+)[\"']",
        html,
        flags=re.I,
    )
    return (hit.group(1) if hit else "").strip()


def _extract_title(html: str) -> str:
    hit = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", hit.group(1)).strip() if hit else ""


def _schema_to_markdown(structured_data: dict[str, Any]) -> str:
    if not structured_data:
        return ""
    try:
        blob = json.dumps(structured_data, ensure_ascii=False)
    except Exception:
        return ""
    lines: list[str] = []
    for m in re.finditer(r'"(?:name|legalName)"\s*:\s*"([^"]{2,120})"', blob):
        text = m.group(1).strip()
        if text.lower() not in {"logo", "home", "about"}:
            lines.append(f"- schema_name: {text}")
        if len(lines) >= 10:
            break

    # Address signals (Organization / LocalBusiness / PostalAddress in JSON-LD).
    # Keep this as compact markdown hints — deterministic enrichment reads these.
    addr_bits: list[str] = []
    for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
        for m in re.finditer(rf'"{key}"\s*:\s*"([^"]{{2,140}})"', blob):
            val = m.group(1).strip()
            if val and val.lower() not in {"unknown", "n/a", "none"}:
                addr_bits.append(val)
                if len(addr_bits) >= 6:
                    break
        if len(addr_bits) >= 6:
            break
    if addr_bits:
        # De-dupe while preserving order (avoid repeating country twice).
        seen: set[str] = set()
        uniq: list[str] = []
        for x in addr_bits:
            k = x.strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            uniq.append(x.strip())
        lines.append(f"- schema_address: {', '.join(uniq[:6])}")

    # sameAs links can include LinkedIn; keep only a tiny hint.
    sameas = re.findall(r'"sameAs"\s*:\s*\[\s*([^\]]+)\]', blob)
    if sameas:
        raw = sameas[0]
        urls = re.findall(r'"(https?://[^"]{6,300})"', raw)
        li = [u for u in urls if "linkedin.com/company" in u.lower()]
        if li:
            lines.append(f"- schema_linkedin: {li[0]}")
    return "## Structured data\n" + "\n".join(lines) if lines else ""


def _dedupe_repeated_lines(markdown: str) -> str:
    raw_lines = markdown.splitlines()
    norm = [ln.strip() for ln in raw_lines]
    counts = Counter(
        ln
        for ln in norm
        if ln and len(ln) <= 120 and not ln.startswith("## Source:") and not ln.startswith("#")
    )
    drop = {ln for ln, c in counts.items() if c >= 3}
    if not drop:
        return markdown
    kept = [ln for ln in raw_lines if ln.strip() not in drop]
    return "\n".join(kept).strip() or markdown


def _quality_heuristic(pages: list[WebsitePageRecord], combined_len: int) -> int:
    if not pages:
        return 1
    ok = sum(1 for p in pages if p.fetch_ok and p.text_length > 100)
    score = 4 + (1 if ok >= 1 else 0) + (2 if ok >= 3 else 0)
    if combined_len > 3000:
        score += 1
    if combined_len > 12000:
        score += 1
    if len(pages) >= 5:
        score += 1
    return max(1, min(10, score))


async def _crawl_pages(root_url: str, max_pages: int) -> tuple[list[WebsitePageRecord], list[str]]:
    pages: list[WebsitePageRecord] = []
    warnings: list[str] = []
    # Keep Crawl4AI from hanging for 30-60s per page on flaky/blocked sites.
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=15_000,  # ms
        wait_for_timeout=5_000,
        remove_consent_popups=True,
        magic=True,
        verbose=False,
        max_retries=0,
    )
    consecutive_failures = 0
    async with AsyncWebCrawler() as crawler:
        for u in _candidate_urls(root_url, max_pages=max_pages):
            try:
                result = await crawler.arun(url=u, config=run_cfg)
            except Exception as exc:
                fallback = _fallback_http_extract(u)
                if fallback is not None:
                    pages.append(fallback)
                    warnings.append(f"{u}: crawl4ai_failed_fallback_used")
                    consecutive_failures = 0
                else:
                    pages.append(WebsitePageRecord(url=u, title="", meta_description="", raw_html="", markdown="", text_length=0, fetch_ok=False, error=str(exc)))
                    warnings.append(f"{u}: {exc}")
                    consecutive_failures += 1
                if consecutive_failures >= 3:
                    warnings.append("crawl_stopped_early: too many consecutive failures")
                    break
                continue

            final_url = (getattr(result, "redirected_url", None) or getattr(result, "url", None) or u).strip()
            if not _same_site(final_url, root_url):
                continue
            html = (getattr(result, "cleaned_html", None) or getattr(result, "html", None) or "").strip()
            status_code = getattr(result, "status_code", None)
            if not getattr(result, "success", False) or not html:
                fallback = _fallback_http_extract(final_url)
                if fallback is not None:
                    pages.append(fallback)
                    warnings.append(f"{final_url}: crawl4ai_unreadable_fallback_used")
                    consecutive_failures = 0
                else:
                    pages.append(
                        WebsitePageRecord(
                            url=final_url,
                            title="",
                            meta_description="",
                            raw_html="",
                            markdown="",
                            text_length=0,
                            fetch_ok=False,
                            status_code=status_code,
                            error=(getattr(result, "error_message", None) or "empty_html"),
                        )
                    )
                    warnings.append(f"{final_url}: crawl failed")
                    consecutive_failures += 1
                if consecutive_failures >= 3:
                    warnings.append("crawl_stopped_early: too many consecutive failures")
                    break
                continue

            text = trafilatura.extract(
                html,
                url=final_url,
                include_links=True,
                include_tables=True,
                include_formatting=True,
                favor_precision=True,
            ) or ""
            schema_md = ""
            with contextlib.suppress(Exception):
                schema_md = _schema_to_markdown(extruct.extract(html, base_url=final_url))
            markdown = text.strip()
            if schema_md:
                markdown = (markdown + "\n\n" + schema_md).strip()
            pages.append(
                WebsitePageRecord(
                    url=final_url,
                    title=_extract_title(html),
                    meta_description=_extract_meta_description(html),
                    raw_html=html[:500_000],
                    markdown=markdown,
                    text_length=len(markdown),
                    fetch_ok=True,
                    status_code=status_code,
                )
            )
            consecutive_failures = 0
    return pages, warnings


def _fallback_http_extract(url: str) -> Optional[WebsitePageRecord]:
    """Direct httpx GET with multiple browser UAs and full redirect follow."""
    r = None
    for ua in _BROWSER_UAS:
        try:
            r = httpx.get(
                url,
                timeout=12.0,
                follow_redirects=True,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en;q=0.9, pl;q=0.8",
                },
            )
            if r.status_code < 400 and (r.text or "").strip():
                break
            r = None
        except Exception:
            r = None
            continue
    if r is None:
        return None
    try:
        if r.status_code >= 400 or not (r.text or "").strip():
            return None
        html = r.text
        text = trafilatura.extract(html, url=str(r.url), include_links=True, include_tables=True, favor_precision=True) or ""
        if not text.strip():
            return None
        schema_md = ""
        with contextlib.suppress(Exception):
            schema_md = _schema_to_markdown(extruct.extract(html, base_url=str(r.url)))
        markdown = text.strip()
        if schema_md:
            markdown = (markdown + "\n\n" + schema_md).strip()
        return WebsitePageRecord(
            url=str(r.url),
            title=_extract_title(html),
            meta_description=_extract_meta_description(html),
            raw_html=html[:500_000],
            markdown=markdown,
            text_length=len(markdown),
            fetch_ok=True,
            status_code=r.status_code,
        )
    except Exception:
        return None


def fetch_website_markdown(
    url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> WebsiteMarkdownResult:
    root_url = normalize_root_url(url)
    # Probe origin variants (https/http x www/non-www) before kicking off Crawl4AI.
    # This avoids "INSUFFICIENT_EVIDENCE / 0 search_calls" outcomes for sites that
    # only resolve on www. or http://, e.g. flyingbisons.pl -> https://www.flyingbisons.com.
    resolved_origin, origin_warnings = _resolve_reachable_origin(root_url)
    if resolved_origin and resolved_origin not in (root_url, root_url.rstrip("/")):
        try:
            root_url = normalize_root_url(resolved_origin)
        except Exception:
            pass
    try:
        pages, warnings = asyncio.run(_crawl_pages(root_url, max_pages=max_pages))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            pages, warnings = loop.run_until_complete(_crawl_pages(root_url, max_pages=max_pages))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    warnings = list(origin_warnings) + list(warnings or [])

    # If Crawl4AI still produced nothing usable, retry probing each remaining
    # origin variant via plain httpx -- this rescues sites that block headless
    # browsers but answer fine to direct GETs with browser UAs.
    if not any(p.fetch_ok and (p.markdown or "").strip() for p in (pages or [])):
        rescued: list[WebsitePageRecord] = []
        for origin in _origin_variants(root_url):
            for path in PROBE_PATHS:
                full = origin if path == "/" else origin + path
                rec = _fallback_http_extract(full)
                if rec is not None:
                    rescued.append(rec)
                    if len(rescued) >= max_pages:
                        break
            if rescued:
                break
        if rescued:
            warnings.append(f"crawl_rescued_via_httpx_origin: {urlparse(rescued[0].url).scheme}://{urlparse(rescued[0].url).netloc}")
            pages = list(pages or []) + rescued

    _ = timeout_seconds
    ok_pages = [p for p in pages if p.fetch_ok and p.markdown.strip()]
    combined = "\n\n---\n\n".join(f"## Source: {p.url}\n\n{p.markdown}" for p in ok_pages)
    combined = _dedupe_repeated_lines(combined)
    if not combined.strip():
        warnings.append("No readable text extracted from crawled pages.")
    return WebsiteMarkdownResult(
        root_url=root_url,
        pages=pages,
        combined_markdown=combined or "(empty)",
        fetch_warnings=warnings,
        extraction_quality_score=_quality_heuristic(pages, len(combined)),
    )
