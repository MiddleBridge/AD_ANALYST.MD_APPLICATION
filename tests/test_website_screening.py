"""Unit tests: website crawl limits, extraction, caps, weights, verdicts."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from agents.schemas_website import (
    WebsiteDimensionScore,
    WebsiteFactsOutput,
    WebsiteGate1Output,
    WebsiteScoresOutput,
)
from agents.website_quality import (
    deterministic_website_kill_flags,
    filter_kill_flags_against_dimensions,
)
from agents.competitive_intelligence import compute_market_saturation
from agents.schemas_website_vc import Competitor
from agents.website_screener import _mandate_blocks_before_scoring, website_dimension_int_scores
from agents.schemas_website_vc import (
    CompetitiveIntelligenceOutput,
    DistributionEngineOutput,
    RetentionModelOutput,
    RightToWinOutput,
    UnitEconomicsOutput,
)
from agents.website_vc_pipeline import apply_vc_investment_caps, calculate_quality_score, calculate_vc_score
from config.website_scoring import (
    apply_website_evidence_caps,
    calculate_website_weighted_score,
    resolve_blended_website_verdict,
    resolve_website_verdict,
)
from tools.website_to_markdown import fetch_website_markdown, normalize_root_url


def test_normalize_root_url_adds_scheme():
    u = normalize_root_url("example.com")
    assert u.startswith("https://example.com")


def test_weighted_score_all_sevens():
    keys = [
        "problem_clarity",
        "product_clarity",
        "target_customer_clarity",
        "urgency_and_budget_signal",
        "differentiation",
        "traction_evidence",
        "customer_proof",
        "business_model_clarity",
        "founder_or_team_signal",
        "distribution_signal",
        "market_potential",
        "technical_depth_or_defensibility",
    ]
    scores = {k: 7 for k in keys}
    assert calculate_website_weighted_score(scores) == 7.0


def test_cap_no_pricing_customers_team():
    raw = 8.0
    facts = {
        "pricing_signals": "",
        "customer_proof": "",
        "logos_or_case_studies": "",
        "team_signals": "",
        "target_customer": "SMB",
        "product_description": "A product",
    }
    capped, reasons = apply_website_evidence_caps(
        raw,
        facts=facts,
        extraction_quality_score=8,
        combined_markdown="long " * 500,
        num_pages_fetched_ok=5,
    )
    assert capped <= 5.5
    assert reasons


def test_cap_no_traction_and_no_customer():
    raw = 8.0
    facts = {
        "pricing_signals": "Enterprise tiers",
        "customer_proof": "",
        "logos_or_case_studies": "",
        "team_signals": "Team page",
        "traction_signals": "",
        "target_customer": "Enterprise",
        "product_description": "Analytics",
    }
    capped, reasons = apply_website_evidence_caps(
        raw,
        facts=facts,
        extraction_quality_score=8,
        combined_markdown="long " * 500,
        num_pages_fetched_ok=5,
    )
    assert capped <= 6.0
    assert any("traction" in r for r in reasons)


def test_cap_thin_landing_ai():
    raw = 7.5
    facts = {
        "pricing_signals": "",
        "customer_proof": "",
        "logos_or_case_studies": "",
        "team_signals": "",
        "target_customer": "",
        "product_description": "",
    }
    capped, _ = apply_website_evidence_caps(
        raw,
        facts=facts,
        extraction_quality_score=6,
        combined_markdown="We supercharge your AI productivity workflow with agents.",
        num_pages_fetched_ok=1,
    )
    assert capped <= 5.0


def test_cap_low_extraction_quality():
    raw = 9.0
    facts = {
        "pricing_signals": "x",
        "customer_proof": "y",
        "target_customer": "z",
        "product_description": "w",
    }
    capped, reasons = apply_website_evidence_caps(
        raw,
        facts=facts,
        extraction_quality_score=4,
        combined_markdown="x" * 4000,
        num_pages_fetched_ok=8,
    )
    assert capped <= 5.5
    assert any("extraction" in r for r in reasons)


def test_verdict_bands():
    assert resolve_website_verdict(gate1_fail=True, website_score=8.0, confidence="high") == "REJECT_AUTO"
    assert resolve_website_verdict(gate1_fail=False, website_score=5.0, confidence="high") == "REJECT_AUTO"
    assert resolve_website_verdict(gate1_fail=False, website_score=5.6, confidence="low") == "NEEDS_DECK"
    assert resolve_website_verdict(gate1_fail=False, website_score=6.6, confidence="low") == "NEEDS_FOUNDER_CALL"
    assert resolve_website_verdict(gate1_fail=False, website_score=7.6, confidence="low") == "PASS_TO_HITL"
    assert resolve_website_verdict(gate1_fail=False, website_score=8.5, confidence="high") == "STRONG_SIGNAL"


def test_website_dimension_int_scores():
    z = lambda n: WebsiteDimensionScore(score=n, reasoning="")
    s = WebsiteScoresOutput(
        problem_clarity=z(8),
        product_clarity=z(7),
        target_customer_clarity=z(6),
        urgency_and_budget_signal=z(5),
        differentiation=z(4),
        traction_evidence=z(3),
        customer_proof=z(8),
        business_model_clarity=z(7),
        founder_or_team_signal=z(6),
        distribution_signal=z(5),
        market_potential=z(4),
        technical_depth_or_defensibility=z(9),
    )
    m = website_dimension_int_scores(s)
    assert m["technical_depth_or_defensibility"] == 9


class _MockResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _MockHttpxClient:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls += 1
        html = """<html><head><title>Co</title>
        <meta name="description" content="We do things" />
        </head><body>
        <h1>Hello</h1>
        <p>Paragraph one.</p>
        <a href="/about">About</a>
        <a href="/pricing">Pricing</a>
        <a href="https://other.com/x">Ext</a>
        </body></html>"""
        return _MockResp(200, html)


def test_crawl_respects_max_pages():
    with patch("tools.website_to_markdown.httpx.Client", _MockHttpxClient):
        r = fetch_website_markdown("https://test.example", max_pages=3, timeout_seconds=5.0)
    assert len(r.pages) <= 3
    assert r.root_url.startswith("https://test.example")


def test_blended_defers_when_external_harsh_not_high():
    """Portfolio-style case: solid website, pessimistic non-high external → no auto-reject."""
    v, note = resolve_blended_website_verdict(
        website_verdict="NEEDS_FOUNDER_CALL",
        website_score=6.65,
        website_llm_confidence="medium",
        final_score=3.64,
        external_score=3.91,
        external_confidence="medium",
        n_sources=10,
        provider_unavailable_warning=None,
    )
    assert v == "NEEDS_FOUNDER_CALL"
    assert "defers" in note.lower()


def test_blended_no_defer_when_external_high_confidence():
    v, note = resolve_blended_website_verdict(
        website_verdict="NEEDS_FOUNDER_CALL",
        website_score=6.65,
        website_llm_confidence="medium",
        final_score=3.64,
        external_score=3.91,
        external_confidence="high",
        n_sources=10,
        provider_unavailable_warning=None,
    )
    assert v == "REJECT_AUTO"
    assert note == ""


def test_blended_defers_when_few_sources():
    v, note = resolve_blended_website_verdict(
        website_verdict="PASS_TO_HITL",
        website_score=7.6,
        website_llm_confidence="high",
        final_score=4.0,
        external_score=5.5,
        external_confidence="high",
        n_sources=2,
        provider_unavailable_warning=None,
    )
    assert v == "PASS_TO_HITL"
    assert note


def test_filter_drops_llm_kill_flags_when_dimensions_contradict():
    dims = {
        "problem_clarity": 8,
        "product_clarity": 7,
        "target_customer_clarity": 8,
        "traction_evidence": 8,
        "customer_proof": 5,
        "business_model_clarity": 6,
        "differentiation": 6,
        "technical_depth_or_defensibility": 3,
        "urgency_and_budget_signal": 6,
        "distribution_signal": 4,
        "market_potential": 7,
        "founder_or_team_signal": 3,
    }
    facts = {
        "traction_signals": "600,000+ students; 93% pass rate",
        "target_customer": "Students preparing for professional exams",
        "product_description": "Online exam prep platform",
        "pricing_signals": "",
    }
    flags = [
        "no_customer_evidence",
        "no_product_specificity",
        "no_clear_icp",
        "no_business_model_signal",
        "no_right_to_win",
    ]
    kept = filter_kill_flags_against_dimensions(flags, dim_scores=dims, facts=facts)
    assert kept == []


def test_deterministic_skips_customer_flag_when_traction_strong():
    facts = {
        "customer_proof": "",
        "logos_or_case_studies": "",
        "traction_signals": "Over 500k learners; pass rate 90%",
        "product_description": "Exam prep SaaS",
        "pricing_signals": "Free and premium plans",
        "target_customer": "Certification candidates",
    }
    dims = {k: 6 for k in [
        "problem_clarity", "product_clarity", "target_customer_clarity",
        "urgency_and_budget_signal", "differentiation", "traction_evidence",
        "customer_proof", "business_model_clarity", "founder_or_team_signal",
        "distribution_signal", "market_potential", "technical_depth_or_defensibility",
    ]}
    dims["traction_evidence"] = 8
    flags = deterministic_website_kill_flags(facts=facts, dim_scores=dims, combined_markdown="x")
    assert "no_customer_evidence" not in flags


def test_quality_and_vc_score_helpers():
    z = lambda n: WebsiteDimensionScore(score=n, reasoning="")
    s = WebsiteScoresOutput(
        problem_clarity=z(8),
        product_clarity=z(8),
        target_customer_clarity=z(7),
        urgency_and_budget_signal=z(6),
        differentiation=z(5),
        traction_evidence=z(7),
        customer_proof=z(8),
        business_model_clarity=z(7),
        founder_or_team_signal=z(6),
        distribution_signal=z(5),
        market_potential=z(7),
        technical_depth_or_defensibility=z(6),
    )
    q = calculate_quality_score(s)
    assert 7.0 <= q <= 8.0
    vc = calculate_vc_score(
        scores=s,
        competitive_position_score=7.0,
        market_timing_score=6.0,
        distribution_score=6.0,
        economic_viability_score=6.0,
        retention_structural_score=6.0,
        right_to_win_score=6.0,
        outlier_score=6.0,
    )
    assert 6.0 <= vc <= 8.0


def test_apply_vc_investment_caps_crowded():
    ci = CompetitiveIntelligenceOutput(
        kill_flags=["crowded_market_no_clear_edge"],
        market_saturation_score=8.0,
        differentiation_summary="",
    )
    dist = DistributionEngineOutput(distribution_score=4.0, primary_channels=["paid_ads"])
    ret = RetentionModelOutput()
    rtw = RightToWinOutput()
    ue = UnitEconomicsOutput(monthly_price_estimate=9.0)
    capped, reasons = apply_vc_investment_caps(8.5, ci=ci, dist=dist, ret=ret, rtw=rtw, ue=ue)
    assert capped <= 6.5
    assert reasons


def test_mandate_uncertain_geo_false_medium_does_not_block():
    g1 = WebsiteGate1Output(
        verdict="UNCERTAIN_NEED_MORE_CONTEXT",
        geography_match=False,
        sector_match=True,
        confidence="MEDIUM",
    )
    facts = WebsiteFactsOutput()
    blocked, kills = _mandate_blocks_before_scoring(g1, facts=facts, website_url="https://example.com")
    assert not blocked
    assert kills == []


def test_mandate_fail_confident_blocks():
    g1 = WebsiteGate1Output(
        verdict="FAIL_CONFIDENT",
        geography_match=True,
        sector_match=True,
        confidence="HIGH",
    )
    facts = WebsiteFactsOutput()
    blocked, kills = _mandate_blocks_before_scoring(g1, facts=facts, website_url="https://example.com")
    assert blocked
    assert "gate1_fail_confident" in kills


def test_mandate_high_confidence_geo_false_blocks():
    g1 = WebsiteGate1Output(
        verdict="PASS",
        geography_match=False,
        sector_match=True,
        confidence="HIGH",
    )
    facts = WebsiteFactsOutput()
    blocked, kills = _mandate_blocks_before_scoring(g1, facts=facts, website_url="https://example.com")
    assert blocked
    assert "mandate_fail_geography" in kills


def test_compute_market_saturation_synthetic_list_not_pegged_to_ten():
    """Category LLM can return many names; without Tavily that must not imply max SERP crowding."""
    comps = [
        Competitor(name=f"Alt{i}", url="", source_type="category_llm", positioning="x") for i in range(14)
    ]
    s = compute_market_saturation(
        comps,
        category="consumer education",
        markdown_lower="",
        major_incumbents=["Salesforce", "HubSpot"],
        has_live_search_snippets=False,
    )
    assert s <= 7.5


def test_mandate_strict_geo_env_blocks_uncertain():
    g1 = WebsiteGate1Output(
        verdict="UNCERTAIN_NEED_MORE_CONTEXT",
        geography_match=False,
        sector_match=True,
        confidence="LOW",
    )
    facts = WebsiteFactsOutput()
    with patch.dict("os.environ", {"WEBSITE_MANDATE_STRICT_GEOGRAPHY": "1"}):
        blocked, kills = _mandate_blocks_before_scoring(
            g1, facts=facts, website_url="https://example.com"
        )
    assert blocked
    assert "mandate_fail_geography" in kills


def test_mandate_pl_domain_does_not_add_geo_kill_on_sector_fail():
    """Consulting on .pl: sector may fail but CEE ccTLD must not falsely add mandate_fail_geography."""
    g1 = WebsiteGate1Output(
        verdict="FAIL_CONFIDENT",
        geography_match=False,
        sector_match=False,
        confidence="HIGH",
    )
    facts = WebsiteFactsOutput(geography="")
    blocked, kills = _mandate_blocks_before_scoring(
        g1, facts=facts, website_url="https://middlebridge.pl/"
    )
    assert blocked
    assert "gate1_fail_confident" in kills
    assert "mandate_fail_geography" not in kills
    assert "mandate_fail_sector" in kills


def test_unreachable_emits_warnings():
    class FailClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kwargs):
            raise httpx.ConnectError("nope", request=None)

    with patch("tools.website_to_markdown.httpx.Client", FailClient):
        r = fetch_website_markdown("https://unreachable.invalid", max_pages=2, timeout_seconds=1.0)
    assert r.fetch_warnings
    assert any("ConnectError" in w or "nope" in w for w in r.fetch_warnings)


# ----- Deterministic enrichment (post-crawl) regression tests --------------


def test_enrichment_extracts_founders_geography_business_model():
    """Golden fixture modelled on gralio.ai: ensure heuristics fill the LLM gaps."""
    from agents.website_enrichment import enrich_from_markdown, merge_enrichment_into_facts

    md = (
        "## Source: https://www.gralio.ai/about\n\n"
        "# About us\n\n"
        "Gralio was founded by entrepreneurs who saw teams drown in repetitive screen work.\n\n"
        "## Experts behind Gralio\n\n"
        "Michal Kaczor — CEO at Gralio.\n"
        "Tymon Terlikiewicz, Co-Founder at Gralio.\n\n"
        "We integrate with Zapier, n8n and Slack. SOC2 compliant.\n\n"
        "---\n\n"
        "## Source: https://www.gralio.ai/\n\n"
        "Footer: Gralio Inc., 8 The Green Suite #14256, Dover, Delaware 19901, USA.\n"
        "Pricing: We run diagnostics and deliver automation blueprints as a paid service.\n"
        "Subscription tiers available, contact sales.\n"
    )

    hints = enrich_from_markdown(md)
    assert "Michal Kaczor" in hints.founders
    assert "Tymon Terlikiewicz" in hints.founders
    assert "Co-Founder" in hints.founders or "CEO" in hints.founders
    assert "Delaware" in hints.geography or "USA" in hints.geography
    assert hints.business_model
    assert "Zapier" in hints.integrations
    assert "SOC2" in hints.compliance

    facts = WebsiteFactsOutput(
        company_name="Gralio",
        founders="unknown",
        team="",
        geography="unknown",
        pricing_signals="",
        unclear_or_missing_data="- Founders and team information is not provided.\n- Geography unclear.",
    )
    facts, notes = merge_enrichment_into_facts(facts, hints)
    assert "Michal Kaczor" in facts.founders
    assert "unknown" not in facts.founders.lower()
    assert "Delaware" in facts.geography or "USA" in facts.geography
    # Stale "Founders ... is not provided" line must be cleared after backfill.
    assert "Founders and team information is not provided" not in (
        facts.unclear_or_missing_data or ""
    )
    assert any("filled founders" in n for n in notes)


def test_enrichment_does_not_capture_customer_quotes():
    """A customer quote attribution must not become a founder."""
    from agents.website_enrichment import enrich_from_markdown

    md = (
        '"It was sobering to see that 77% of my operations could be automated."\n'
        "— Marek Jakun, COO at Sylvan Inc.\n"
    )
    hints = enrich_from_markdown(md)
    assert hints.founders == ""


def test_scoring_floor_keeps_founder_signal_above_one_when_facts_present():
    """If founders are detected on the site, the LLM cannot keep founder_signal=1."""
    from agents.website_screener import _apply_website_scoring_floors

    z = lambda n, why="": WebsiteDimensionScore(score=n, reasoning=why)
    scores = WebsiteScoresOutput(
        problem_clarity=z(7),
        product_clarity=z(8),
        target_customer_clarity=z(7),
        urgency_and_budget_signal=z(5),
        differentiation=z(6),
        traction_evidence=z(6),
        customer_proof=z(6),
        business_model_clarity=z(2, "no pricing"),
        founder_or_team_signal=z(1, "no team listed"),
        distribution_signal=z(4),
        market_potential=z(7),
        technical_depth_or_defensibility=z(5),
    )
    facts = WebsiteFactsOutput(
        founders="Michal Kaczor — CEO; Tymon Terlikiewicz — Co-Founder",
        team="Michal Kaczor — CEO; Tymon Terlikiewicz — Co-Founder",
        pricing_signals="subscription / paid diagnostics",
    )
    _apply_website_scoring_floors(scores, facts)
    assert scores.founder_or_team_signal.score >= 4
    assert scores.business_model_clarity.score >= 4
    assert "Floor applied" in scores.founder_or_team_signal.reasoning


def test_kill_flag_no_founder_dropped_when_founders_present():
    """The `no_founder_or_team_signal` kill must be dropped when facts.founders is set."""
    flags = filter_kill_flags_against_dimensions(
        ["no_founder_or_team_signal"],
        dim_scores={"founder_or_team_signal": 4},
        facts={"founders": "Michal Kaczor — CEO", "team": ""},
    )
    assert "no_founder_or_team_signal" not in flags
