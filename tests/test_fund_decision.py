from agents.fund_decision import (
    Blockers,
    FundGeoAssessment,
    apply_fund_geo_rule,
    build_fund_mandate_fit,
    classify_stage,
    fund_verdict,
)


def test_no_cee_signal_cannot_be_pass():
    geo = FundGeoAssessment(
        status="no_cee_signal",
        strongest_signal=None,
        confidence=0.2,
        decision="UNCERTAIN",
    )
    geo_decision = apply_fund_geo_rule(geo)
    mandate = build_fund_mandate_fit(
        geo_decision=geo_decision,
        stage_decision="PASS",
        sector_decision="PASS",
        ticket_decision="UNKNOWN",
        software_decision="PASS",
    )
    assert mandate.overall != "PASS"
    assert mandate.overall == "UNCERTAIN"


def test_hq_outside_cee_with_diaspora_signal_is_not_auto_fail():
    geo = FundGeoAssessment(
        status="possible_cee_diaspora",
        strongest_signal="founder_origin: Poland",
        confidence=0.7,
        decision="UNCERTAIN",
    )
    assert apply_fund_geo_rule(geo) in ("PASS", "UNCERTAIN")


def test_stage_classification_series_b_plus_fails_stage():
    s = classify_stage("Series B growth")
    assert s == "series-b+"


def test_verdict_mapping_uncertain_becomes_request_deck_and_verify():
    verdict = fund_verdict(
        mandate_fit="UNCERTAIN",
        investment_interest="MEDIUM_HIGH",
        confidence=0.6,
        blockers=Blockers(has_hard_fail=False),
    )
    assert verdict == "REQUEST_DECK_AND_VERIFY"

