"""Unit tests: Gate 2.5 scoring, caps, kill flags."""

from __future__ import annotations

from agents.final_scoring import (
    apply_hard_cap_rules,
    apply_hard_cap_to_final,
    build_final_investment_decision,
    cap_external_when_provider_down,
    compute_external_weighted_score,
    compute_final_score_before_cap,
    compute_risk_penalty,
)
from agents.schemas_gate25 import ExternalMarketCheckResult, ExternalSource, KillFlag
from storage.models import Gate1Result, Gate2Result, ScoredDimension


def _gate2(**kwargs) -> Gate2Result:
    g = Gate2Result(
        passes=True,
        overall_score=6.5,
        recommendation="MAYBE",
        business_model=ScoredDimension(6, ""),
        traction=ScoredDimension(6, ""),
        moat_path=ScoredDimension(5, ""),
        founder_market_fit=ScoredDimension(6, ""),
        company_one_liner="AI SaaS",
    )
    for k, v in kwargs.items():
        setattr(g, k, v)
    return g


def _gate1() -> Gate1Result:
    return Gate1Result(
        verdict="PASS",
        geography_match=True,
        stage_match=True,
        sector_match=True,
        detected_sector="AI",
    )


def test_external_weighted_score():
    scores = {
        "market_saturation_score": 6,
        "competitive_position_score": 6,
        "incumbent_risk_score": 6,
        "distribution_feasibility_score": 6,
        "cac_viability_score": 6,
        "switching_trigger_score": 6,
        "trend_validity_score": 6,
        "regulatory_platform_risk_score": 6,
        "right_to_win_score": 6,
    }
    assert compute_external_weighted_score(scores) == 6.0


def test_final_score_blend():
    f = compute_final_score_before_cap(8.0, 4.0, 0.0)
    assert f == round(0.6 * 8.0 + 0.4 * 4.0, 2)


def test_website_final_score_weights_more_external():
    f = compute_final_score_before_cap(6.0, 8.0, 0.0, screening_mode="website")
    assert f == round(0.45 * 6.0 + 0.55 * 8.0, 2)


def test_hard_cap_applied():
    assert apply_hard_cap_to_final(7.5, 6.0) == 6.0
    assert apply_hard_cap_to_final(5.0, 10.0) == 5.0


def test_cap_external_when_provider_down():
    assert cap_external_when_provider_down(8.0) == 6.0
    assert cap_external_when_provider_down(5.0) == 5.0


def test_fatal_kill_blocks_hitl():
    g1 = _gate1()
    g2 = _gate2(overall_score=8.0)
    ext = ExternalMarketCheckResult(
        market_saturation_score=5,
        competitive_position_score=5,
        incumbent_risk_score=5,
        distribution_feasibility_score=5,
        cac_viability_score=5,
        switching_trigger_score=5,
        trend_validity_score=5,
        regulatory_platform_risk_score=5,
        right_to_win_score=5,
        external_score=5.0,
        kill_flags=[
            KillFlag(code="x", severity="fatal", description="test fatal"),
        ],
    )
    d = build_final_investment_decision(
        gate1=g1,
        gate2=g2,
        external=ext,
        final_score=7.0,
        gate2_threshold=6.0,
        final_threshold=6.3,
        override_fatal=False,
    )
    assert d.has_fatal_kill_flag
    assert d.final_verdict == "REJECT_AUTO"


def test_override_fatal_allows_pass():
    g1 = _gate1()
    g2 = _gate2(overall_score=8.0)
    ext = ExternalMarketCheckResult(
        market_saturation_score=8,
        competitive_position_score=8,
        incumbent_risk_score=8,
        distribution_feasibility_score=8,
        cac_viability_score=8,
        switching_trigger_score=8,
        trend_validity_score=8,
        regulatory_platform_risk_score=8,
        right_to_win_score=8,
        external_score=8.0,
        external_confidence="high",
        sources=[ExternalSource(title="ref", url="https://news.ycombinator.com/item?id=1")],
        kill_flags=[
            KillFlag(code="x", severity="fatal", description="test"),
        ],
    )
    d = build_final_investment_decision(
        gate1=g1,
        gate2=g2,
        external=ext,
        final_score=7.5,
        gate2_threshold=6.0,
        final_threshold=6.3,
        override_fatal=True,
    )
    assert not d.has_fatal_kill_flag
    assert d.final_verdict == "PASS_TO_HITL"


def test_ai_wrapper_hard_cap():
    g1 = _gate1()
    g2 = _gate2(moat_path=ScoredDimension(4, ""), company_one_liner="AI tool")
    facts = {"what_they_do": "We use LLM for workflow"}
    ext = {
        "market_saturation_score": 5,
        "competitive_position_score": 5,
        "incumbent_risk_score": 5,
        "distribution_feasibility_score": 5,
        "cac_viability_score": 5,
        "switching_trigger_score": 5,
        "trend_validity_score": 5,
        "regulatory_platform_risk_score": 5,
        "right_to_win_score": 4,
    }
    cap, flags = apply_hard_cap_rules(gate2=g2, facts=facts, gate1=g1, ext=ext)
    assert cap is not None and cap <= 5.5
    assert any(f.code == "ai_wrapper_no_workflow_ownership" for f in flags)


def test_marketplace_liquidity_cap():
    g1 = _gate1()
    g2 = _gate2()
    facts = {"what_they_do": "two-sided marketplace for X"}
    ext = {
        "market_saturation_score": 5,
        "competitive_position_score": 5,
        "incumbent_risk_score": 5,
        "distribution_feasibility_score": 4,
        "cac_viability_score": 5,
        "switching_trigger_score": 4,
        "trend_validity_score": 5,
        "regulatory_platform_risk_score": 5,
        "right_to_win_score": 5,
    }
    cap, flags = apply_hard_cap_rules(gate2=g2, facts=facts, gate1=g1, ext=ext)
    assert cap is not None and cap <= 5.5
    assert any(f.code == "marketplace_without_liquidity_wedge" for f in flags)


def test_vanity_traction_cap():
    g1 = _gate1()
    g2 = _gate2(traction=ScoredDimension(7, ""))
    facts = {"traction": "waitlist and LOIs, pilots starting soon"}
    ext = {
        "market_saturation_score": 5,
        "competitive_position_score": 5,
        "incumbent_risk_score": 5,
        "distribution_feasibility_score": 5,
        "cac_viability_score": 5,
        "switching_trigger_score": 5,
        "trend_validity_score": 5,
        "regulatory_platform_risk_score": 5,
        "right_to_win_score": 5,
    }
    cap, flags = apply_hard_cap_rules(gate2=g2, facts=facts, gate1=g1, ext=ext)
    assert any(f.code == "vanity_traction_only" for f in flags)


def test_pass_to_hitl_requires_both_thresholds():
    g1 = _gate1()
    g2 = _gate2(overall_score=8.0)
    ext = ExternalMarketCheckResult(
        market_saturation_score=8,
        competitive_position_score=8,
        incumbent_risk_score=8,
        distribution_feasibility_score=8,
        cac_viability_score=8,
        switching_trigger_score=8,
        trend_validity_score=8,
        regulatory_platform_risk_score=8,
        right_to_win_score=8,
        external_score=8.0,
        external_confidence="high",
        sources=[ExternalSource(title="ref", url="https://news.ycombinator.com/item?id=1")],
        kill_flags=[],
    )
    d = build_final_investment_decision(
        gate1=g1,
        gate2=g2,
        external=ext,
        final_score=7.0,
        gate2_threshold=6.0,
        final_threshold=6.3,
        override_fatal=False,
    )
    assert d.final_verdict == "PASS_TO_HITL"

    d2 = build_final_investment_decision(
        gate1=g1,
        gate2=g2,
        external=ext,
        final_score=5.0,
        gate2_threshold=6.0,
        final_threshold=6.3,
        override_fatal=False,
    )
    assert d2.final_verdict == "REJECT_AUTO"


def test_great_deck_terrible_market_composite():
    """A. internal 8, external 4 → final ~6.4 before penalty."""
    f = compute_final_score_before_cap(8.0, 4.0, 0.0)
    assert f < 7.0


def test_risk_penalty_sum_capped():
    flags = [
        KillFlag(code="a", severity="warning", description=""),
        KillFlag(code="b", severity="major", description=""),
    ]
    p = compute_risk_penalty(flags)
    assert p <= 1.5
