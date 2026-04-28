from __future__ import annotations

from agents.external_research import get_research_provider

from agent.models import SourceItem


def lookup_founders(company_name: str, limit: int = 5) -> list[SourceItem]:
    provider, _ = get_research_provider()
    queries = [
        f"{company_name} founders linkedin",
        f"{company_name} founder",
        f"{company_name} team linkedin",
    ]
    out: list[SourceItem] = []
    seen = set()
    for q in queries:
        for s in provider.search(q, max_results=limit):
            if s.url and s.url not in seen:
                seen.add(s.url)
                out.append(
                    SourceItem(
                        url=s.url,
                        title=s.title or "",
                        snippet=s.snippet or "",
                        source_type="founder_lookup",
                    )
                )
    return out[:limit]

