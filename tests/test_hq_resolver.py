from agents.hq_resolver import resolve_hq_country
from tools.website_to_markdown import WebsiteMarkdownResult, WebsitePageRecord


def test_hq_resolver_middlebridge_official_footer_no_search(monkeypatch):
    footer_html = (
        "<footer>© 2025 Middle Bridge sp. z o.o. All rights reserved. "
        "Middle Bridge is a limited liability company registered in Lodz, Poland under KRS number 0001176237."
        "</footer>"
    )
    page = WebsitePageRecord(
        url="https://www.middlebridge.pl/",
        title="Middle Bridge",
        meta_description="",
        raw_html=footer_html,
        markdown="Middle Bridge",
        text_length=12,
        fetch_ok=True,
        status_code=200,
    )
    fake_md = WebsiteMarkdownResult(
        root_url="https://www.middlebridge.pl/",
        pages=[page],
        combined_markdown="Middle Bridge",
        fetch_warnings=[],
        extraction_quality_score=8,
    )

    def _fake_fetch(url: str, max_pages: int = 12, timeout_seconds: float = 20.0):
        return fake_md

    monkeypatch.setattr("agents.hq_resolver.fetch_website_markdown", _fake_fetch)
    out = resolve_hq_country(domain="middlebridge.pl", company_name="middlebridge.pl", strict_domain_match=True)
    assert out.status in {"LEGAL_WEBSITE_ONLY", "LEGAL_VERIFIED"}
    assert out.legal_registered_office.get("country") == "Poland"
    assert out.legal_registered_office.get("city") == "Lodz"
    assert out.final_geo_for_vc_screening.get("country") == "Poland"
    assert out.final_geo_for_vc_screening.get("basis") == "legal_registered_office"
    assert out.search_calls == 0
    assert out.llm_tokens_used == 0
