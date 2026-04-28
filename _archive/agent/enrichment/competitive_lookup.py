from __future__ import annotations

from agents.external_research import get_research_provider


def lookup_competitors(company_name: str, category_hint: str = "", min_items: int = 3) -> list[str]:
    provider, _ = get_research_provider()
    query = f"{company_name} alternatives competitors {category_hint}".strip()
    results = provider.search(query, max_results=10)
    names: list[str] = []
    seen = set()
    for r in results:
        raw = (r.title or "").split("|")[0].split("-")[0].strip()
        if raw and raw.lower() not in seen and company_name.lower() not in raw.lower():
            seen.add(raw.lower())
            names.append(raw[:80])
    # No hardcoded competitors: if live research cannot find enough, return what we truly found.
    # Quality gates will force re-enrichment or mark unknowns explicitly.
    return names[: max(min_items, 3)] if len(names) >= min_items else names

