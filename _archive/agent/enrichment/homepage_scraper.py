from __future__ import annotations

from tools.website_to_markdown import fetch_website_markdown


TARGET_PATH_HINTS = ("homepage", "about", "careers", "pricing", "blog")


def scrape_core_pages(url: str, max_pages: int = 10) -> dict[str, str]:
    """Return best URL match for key website sections."""
    md = fetch_website_markdown(url, max_pages=max_pages, timeout_seconds=20.0)
    out: dict[str, str] = {k: "" for k in TARGET_PATH_HINTS}
    for p in md.pages:
        u = (p.url or "").lower()
        if not out["homepage"] and p.url == md.root_url:
            out["homepage"] = p.url
        for key in ("about", "careers", "pricing", "blog"):
            if (f"/{key}" in u or u.rstrip("/").endswith(key)) and not out[key]:
                out[key] = p.url
    if not out["homepage"]:
        out["homepage"] = md.root_url
    return out

