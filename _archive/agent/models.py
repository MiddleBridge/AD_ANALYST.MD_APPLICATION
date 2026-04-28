from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


Depth = Literal["INITIAL", "DEEP", "REJECTED"]
RouteAction = Literal["REJECT", "WATCH_3M", "WATCH_6M", "PASS_TO_ASSOCIATE", "PASS_TO_PARTNER"]


@dataclass
class SourceItem:
    url: str
    title: str = ""
    snippet: str = ""
    source_type: str = "web"


@dataclass
class EnrichmentBundle:
    company_name: str
    url: str
    crawled_pages: dict[str, str] = field(default_factory=dict)
    search_results: list[SourceItem] = field(default_factory=list)
    founder_sources: list[SourceItem] = field(default_factory=list)
    funding_sources: list[SourceItem] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def unique_urls(self) -> list[str]:
        urls = []
        seen = set()
        for u in self.crawled_pages.values():
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        for group in (self.search_results, self.founder_sources, self.funding_sources):
            for s in group:
                if s.url and s.url not in seen:
                    seen.add(s.url)
                    urls.append(s.url)
        return urls


@dataclass
class RunContext:
    screened_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    depth: Depth = "INITIAL"
    retry_count: int = 0

