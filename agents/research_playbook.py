"""Concrete web-style research query templates per startup (rubric support)."""

from __future__ import annotations

def build_research_queries(
    company_name: str,
    category: str,
    website_url: str | None = None,
) -> dict[str, list[str]]:
    c = (company_name or "startup").strip()
    cat = (category or "SaaS").strip()
    site = (website_url or "").strip()
    site_q = f" site:{site}" if site else ""
    return {
        "company_identity": [
            f"{c} founders",
            f"{c} linkedin",
            f"{c} funding",
            f"{c} crunchbase",
        ],
        "product_pricing": [
            f"{c} pricing{site_q}",
            f"{c} customers",
            f"{c} case study",
            f"{c} demo",
        ],
        "competition": [
            f"{cat} competitors",
            f"{cat} startups",
            f"{c} alternatives",
            f"{cat} G2",
        ],
        "market": [
            f"{cat} market size",
            f"{cat} growth rate",
            f"{cat} analyst report",
            f"{cat} budget owner",
        ],
        "traction": [
            f"{c} revenue",
            f"{c} customers",
            f"{c} hiring",
            f"{c} jobs",
            f"{c} employees linkedin",
        ],
    }


def playbook_json_for_prompt(company_name: str, category: str, website_url: str | None = None) -> str:
    import json

    return json.dumps(
        build_research_queries(company_name, category, website_url),
        ensure_ascii=False,
        indent=2,
    )
